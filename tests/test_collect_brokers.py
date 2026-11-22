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


#: 기록에서 빼는 칸 — 벽시계 시각이라 실행마다 다르다.
_CLOCK = {"collect_runs": ("started_at", "finished_at"), "collect_done": ("done_at",)}


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
        run("backfill", TB.FakeKiwoom(), "2026-11-06 21:00", ["backfill", "--sectors", "운송/창고", "--days", "30"],
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
