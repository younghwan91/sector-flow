"""증권사 어댑터 구조(``sectorflow.collect.brokers``) — 키움을 첫 어댑터로 옮겨도 동작·호출이 바이트째 같은가.

지키는 것: 등록부 이름 · 어댑터마다 능력(주체·거래소·검증 여부·약관 한 줄) · 키움 어댑터로 옮기기 **전에** 기록한 가짜 전송
기록(요청 URL·헤더·본문 순서 그대로)과 sqlite 내용이 옮긴 뒤와 같다(``tests/fixtures/brokers/kiwoom_golden.json``).

기록은 옮기기 전 코드(a0d69e9)에서 ``SECTORFLOW_WRITE_GOLDEN=1`` 로 한 번 썼다. 다시 쓰는 것은 동작을 바꾸기로 정했을 때뿐이다 —
다시 쓰면 이 검사는 그 커밋의 동작을 새 기준으로 삼는다.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from sectorflow.collect import kiwoom as K
from sectorflow.collect import run as R
from tests import test_collect_backfill as TB
from tests import test_collect_daily as TD
from tests import test_collect_universe as TU

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "brokers" / "kiwoom_golden.json"


class Recorder:
    """가짜 서버 앞에 서서 요청을 순서대로 적는다."""

    def __init__(self, inner):
        self.inner, self.log = inner, []

    def __call__(self, url, headers, body, timeout):
        self.log.append([url, dict(sorted(headers.items())), body])
        return self.inner(url, headers, body, timeout)


def _patch(monkeypatch, rec: Recorder, now: str) -> None:
    def client(args, only=None):
        return K.KiwoomRest("k", "s", transport=rec, rate=None, max_calls=args.max_calls,
                            sleep=lambda s: None, only=only)
    monkeypatch.setattr(R, "_client", client)
    monkeypatch.setattr(R, "_now_kst", lambda: TD.kst(now))
    monkeypatch.setattr(R, "_today", lambda: TD.kst(now).strftime("%Y%m%d"))


#: 기록에서 빼는 칸 — 벽시계 시각이라 실행마다 다르다. 그리고 기록 뒤에 더한 열(그 열의 검사는 따로 있다):
#: ``stocks.sector_source``(Task 56, ``test_sector_source_*``).
_CLOCK = {"collect_runs": ("started_at", "finished_at"), "collect_done": ("done_at",), "stocks": ("sector_source",)}


def _dump(db: Path) -> dict:
    con = sqlite3.connect(db)
    out = {}
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                            "ORDER BY name"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        keep = [c for c in cols if c not in _CLOCK.get(t, ())]
        rows = con.execute(f"SELECT {','.join(keep)} FROM {t}").fetchall()
        out[t] = {"cols": keep, "rows": sorted([list(r) for r in rows], key=json.dumps)}
    con.close()
    return out


def _scenarios(tmp_path: Path, monkeypatch) -> dict:
    """기존 가짜 서버 넷으로 명령 다섯을 돌린 기록 — 요청 순서와 끝난 sqlite."""
    out: dict = {}

    def run(name, fake, now, argv, db, seed=None):
        rec = Recorder(fake)
        _patch(monkeypatch, rec, now)
        if seed is not None:
            seed(db)
        rc = R.main([argv[0], "--db", str(db), *argv[1:]])
        out[name] = {"rc": rc, "requests": rec.log, "db": _dump(db)}

    with monkeypatch.context() as m:
        m.setattr(TD, "TRUTH", TD.CAL.trading_days("2026-01-02", "2026-12-30")[0])
        run("backfill", TB.FakeKiwoom(), "2026-11-07 10:00", ["backfill", "--sectors", "운송/창고", "--days", "30"],
            tmp_path / "a.db")
        run("backfill-map", TD.FakeServer(), "2026-11-20 21:00", ["backfill", "--map-only", "--sectors", "운송/창고"],
            tmp_path / "b.db")
        run("daily", TD.FakeServer(), "2026-11-20 21:00", ["daily"], tmp_path / "c.db", seed=TD._seed)
        run("daily-map", TD.FakeServer(), "2026-11-20 21:00", ["daily", "--map-only"], tmp_path / "d.db",
            seed=TD._seed)
    rec = Recorder(TU.FakeList())
    monkeypatch.setattr(R, "_client", lambda args: K.KiwoomRest("k", "s", transport=rec, rate=None,
                                                                 max_calls=args.max_calls, sleep=lambda s: None))
    db = tmp_path / "e.db"
    out["universe"] = {"rc": R.main(["universe", "--db", str(db)]), "requests": rec.log, "db": _dump(db)}
    return json.loads(json.dumps(out, ensure_ascii=False))


def test_kiwoom_adapter_byte_identical(tmp_path, monkeypatch):
    got = _scenarios(tmp_path, monkeypatch)
    if os.environ.get("SECTORFLOW_WRITE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(got, ensure_ascii=False, indent=0, sort_keys=True) + "\n", "utf-8")
        pytest.skip("기록을 새로 썼다")
    want = json.loads(GOLDEN.read_text("utf-8"))
    assert sorted(got) == sorted(want)
    for name in want:
        assert got[name]["rc"] == want[name]["rc"] == 0, name
        assert got[name]["requests"] == want[name]["requests"], f"{name}: 요청 순서·헤더·본문이 바뀌었다"
        assert got[name]["db"] == want[name]["db"], f"{name}: sqlite 내용이 바뀌었다"
    assert sum(len(v["requests"]) for v in want.values()) > 40, "기록이 거의 비었다 — 시나리오가 헛돈다"


# ── 등록부·능력 ────────────────────────────────────────────────────────────
#: 계획(Phase 6b)의 어댑터 이름 — 등록부는 이 안에서만 는다.
PLANNED = ("kiwoom", "kis", "ls", "toss", "db", "nh")


def test_broker_registry_names(monkeypatch):
    from sectorflow.collect.brokers import BROKERS, CAPABILITIES, DEFAULT, Broker, Settings
    assert list(BROKERS)[0] == DEFAULT == "kiwoom"
    assert set(BROKERS) <= set(PLANNED) and list(BROKERS) == [n for n in PLANNED if n in BROKERS]
    assert set(CAPABILITIES) == set(BROKERS)
    monkeypatch.setenv("KIWOOM_APP_KEY", "k")
    monkeypatch.setenv("KIWOOM_APP_SECRET", "s")
    for name, make in BROKERS.items():
        b = make(Settings(transport=lambda *a: (500, {}, {})))
        assert isinstance(b, Broker) and b.name == name and b.capability is CAPABILITIES[name], name
        assert b.total_calls == 0, "만들기만 해서는 부르지 않는다"


def test_kiwoom_is_the_full_verified_krx_reference():
    from sectorflow.collect.brokers import CAPABILITIES
    from sectorflow.storage import INVESTOR_COLUMNS
    cap = CAPABILITIES["kiwoom"]
    assert cap.actors == frozenset(INVESTOR_COLUMNS) and cap.missing == ()
    assert (cap.venue, cap.verified) == ("KRX", True)


def test_capability_rejects_unknown_actors_venues_and_multiline_notes():
    from sectorflow.collect.brokers.base import Capability
    ok = dict(actors=frozenset({"individual"}), venue="KRX", verified=False, terms_note="한 줄")
    assert Capability(**ok).missing[0] == "foreign_"
    for bad in ({"actors": frozenset({"etc_fnnc"})}, {"venue": "NXT"}, {"terms_note": "두\n줄"}, {"terms_note": " "}):
        with pytest.raises(ValueError):
            Capability(**{**ok, **bad})


def test_sd_row_leaves_missing_actors_unknown_not_zero():
    from sectorflow.collect.brokers.base import SD_FIELDS, sd_row, sd_tuple
    r = sd_row("000120", "2026-11-20", close=1000, flu_rt=None, acc_trde_qty=None,
               actors={"individual": -3, "institution": 0}, source="x")
    assert r["institution"] == 0 and r["foreign_"] is None and r["etc_corp"] is None
    assert sd_tuple(r) == tuple(r[k] for k in SD_FIELDS) and len(SD_FIELDS) == 17
    with pytest.raises(ValueError):
        sd_row("000120", "2026-11-20", close=1, flu_rt=None, acc_trde_qty=None, actors={"etc_fnnc": 1}, source="x")


def test_other_brokers_only_collect_for_the_map():
    import argparse
    R._require_map_only(argparse.Namespace(broker="kiwoom"), False)
    R._require_map_only(argparse.Namespace(broker="kis"), True)
    with pytest.raises(SystemExit, match="--map-only"):
        R._require_map_only(argparse.Namespace(broker="kis"), False)
