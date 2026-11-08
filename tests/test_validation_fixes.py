"""2026-11-08 지표 검증이 찾은 결함 — 시총·수익률·G·k·확정 표시.

결과 문서: ``docs/validation/2026-11-08-results.md``.
"""
from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pandas as pd
import pytest

from sectorflow.storage import connect, market_cap_asof_bulk
from sectorflow.tui.flow_view import status_chip

REPO = Path(__file__).resolve().parent.parent


def _mod(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SN = _mod("sector_numbers", "sector_numbers.py")
SF = _mod("sector_flow", "sector_flow.py")
VR = _mod("verify_report", "verify_report.py")


# ── 시총: 원 종가 × 주식수(분할·병합·감자 뒤 다시 센다) ─────────────────────────

@pytest.fixture
def con(tmp_path):
    c = connect(str(tmp_path / "t.db"))
    c.executemany("INSERT INTO supply_demand(code,date,close,source) VALUES (?,?,?,'kiwoom')",
                  [("078130", "2026-05-19", 436), ("078130", "2026-06-30", 4005),
                   ("078130", "2026-07-10", 4000)])
    # daily_bars 는 수정주가 — 병합 뒤라 병합 전 날도 10배다. 시총에 쓰면 안 된다.
    c.executemany("INSERT INTO daily_bars(code,date,close) VALUES (?,?,?)",
                  [("078130", "2026-05-19", 4360), ("078130", "2026-06-30", 4005),
                   ("078130", "2026-07-10", 4000), ("000001", "2026-06-30", 999)])
    c.execute("INSERT INTO daily_bars_raw(code,date,close) VALUES ('000001','2026-06-30',500)")
    c.executemany("INSERT INTO shares_outstanding_history VALUES (?,?,?)",
                  [("078130", "2025-12-31", 1_127_405_871), ("078130", "2026-07-09", 112_741_000),
                   ("000001", "2025-12-31", 1_000)])
    c.execute("INSERT INTO corporate_actions VALUES ('078130','2026-05-20','16',900.0)")
    c.commit()
    return c


def _cap(con, code, day):
    return float(market_cap_asof_bulk(con, pd.DataFrame({"code": [code], "date": [day]})).iloc[0])


def test_cap_uses_raw_price_and_recounts_shares_after_a_merge(con):
    """078130 10:1 병합(2026-05-20). 병합 전 주식수 × 병합 후 가격이 6월 시총을 10배로 만들었다."""
    before, june, july = (_cap(con, "078130", d) for d in ("2026-05-19", "2026-06-30", "2026-07-10"))
    assert before == pytest.approx(436 * 1_127_405_871)
    assert june == pytest.approx(4005 * 112_740_587.1, rel=1e-6)
    assert july == pytest.approx(4000 * 112_741_000)
    assert max(before, june, july) / min(before, june, july) < 1.2


def test_cap_falls_back_to_raw_bars_not_adjusted_bars(con):
    assert _cap(con, "000001", "2026-06-30") == pytest.approx(500 * 1_000)


# ── 수익률: 조정 종가 비(flu_rt 가 아니다) ──────────────────────────────────────

def test_returns_come_from_adjusted_closes_and_stay_missing_when_absent(tmp_path):
    c = connect(str(tmp_path / "r.db"))
    days = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    c.executemany("INSERT INTO supply_demand(code,date,close,flu_rt,source) VALUES (?,?,?,?,'kiwoom')",
                  [("A", d, 100, 9999, ) for d in days])
    c.executemany("INSERT INTO daily_bars_adjusted VALUES (?,?,?)",
                  [("A", "2026-11-02", 100.0), ("A", "2026-11-03", 110.0), ("A", "2026-11-04", 99.0)])
    c.commit()
    keys = pd.DataFrame({"code": ["A"] * 3, "date": days[1:]})
    r = SF.adjusted_returns(c, keys, days[1:]).tolist()
    assert r[0] == pytest.approx(10.0) and r[1] == pytest.approx(-10.0)
    assert r[2] != r[2], "조정 종가가 없는 날은 0 이 아니라 결측이어야 한다"


# ── G·k (sector_numbers.build) ─────────────────────────────────────────────

def _payload(seed=0, n=130):
    rnd = random.Random(seed)
    thin = ("T",)
    sectors = [f"S{i}" for i in range(10)] + list(thin)
    dates = [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]
    m = "거래소"
    acts = ("inst", "forgn", "indiv", "etc", "tv")
    flows = {m: {s: {a: [rnd.gauss(0, 10) for _ in range(n)] for a in acts} for s in sectors}}
    # 얇은 섹터는 극단값 — 적합에 넣으면 k 가 크게 움직인다.
    flows[m]["T"]["inst"] = [400.0] * n
    ret = {m: {s: [rnd.gauss(0, 1) for _ in range(n)] for s in sectors}}
    ret[m]["T"] = [-3.0] * n
    names, k = {}, 0
    for s in sectors:
        for _ in range(3 if s in thin else 12):
            k += 1
            names[f"{k:06d}"] = {"name": f"n{k}", "sector": s, "market": m, "cap": 100.0,
                                 "win": {w: {"inst": 1.0, "forgn": 0.0, "indiv": 0.0, "etc": 0.0,
                                             "tv": 1.0, "invtrt": 0.0, "penfnd_etc": 0.0, "ret": 0.0}
                                         for w in ("5", "20", "60", "120")}}
    return {"dates": dates, "sectors": sectors, "markets": [m], "flows": flows,
            "detail": {m: {s: {"invtrt": [0.0] * n, "penfnd_etc": [0.0] * n} for s in sectors}},
            "ret": ret, "retw": {m: {s: [100.0] * n for s in sectors}},
            "iret": {m: {s: None for s in sectors}}, "cap": {m: {s: 1000.0 for s in sectors}},
            "cap_by_month": {m: {s: {d[:7]: 1000.0 for d in dates} for s in sectors}},
            "names": names, "n_names": k, "finalized": True,
            "n_by_sector": {m: {s: (3 if s in thin else 12) for s in sectors}},
            "detail_labels": {"invtrt": "투신", "penfnd_etc": "연기금"}}


def _passers(B):
    return [r for r in B["rows"] if not r["thin"] and r.get("xddot") is not None
            and r.get("a_idx") is not None and r["a_idx"] > 0 and r["x"] > 0 and r["xddot"] > 0]


def test_a_lone_passer_keeps_its_mark():
    """세 관문을 다 통과한 섹터가 하나뿐이면 G 를 안 줘서 ● 가 사라졌다(10-15 60|전체)."""
    lone = []
    for seed in range(20):
        D = SN.build(_payload(seed))
        lone += [B for B in D["blocks"].values() if len(_passers(B)) == 1]
    assert lone, "단독 통과 블록이 하나도 안 나왔다 — 이 검사가 헛돈다"
    for B in lone:
        r = _passers(B)[0]
        assert r["G"] == 0.5 and r["G_pass"] is True


def test_every_passer_has_a_score():
    for seed in range(5):
        for B in SN.build(_payload(seed))["blocks"].values():
            assert {r["sector"] for r in _passers(B)} == {r["sector"] for r in B["rows"] if r["G"] is not None}


def test_thin_sectors_do_not_enter_the_k_fit():
    """얇은 섹터 하나로 k 의 부호가 바뀐 날이 있었다(10-15 20|전체 −0.59 → +14.42)."""
    D = SN.build(_payload(1))
    for key, B in D["blocks"].items():
        fit = [r for r in B["rows"] if not r["thin"] and r.get("a_idx") is not None]
        k, _b, _t, _r2 = VR.ols([r["a_idx"] for r in fit], [r["ret"] for r in fit])
        assert B["k"] == pytest.approx(round(k, 3), abs=1e-3), key


def test_verifier_flags_a_passer_without_a_score():
    D = SN.build(_payload(0))
    B = next(B for B in D["blocks"].values() if _passers(B))
    _passers(B)[0]["G"] = None
    VR.FAILS.clear()
    VR.layer_a(D)
    assert any("관문" in f for f in VR.FAILS), VR.FAILS


# ── 확정 표시 ───────────────────────────────────────────────────────────────

def test_status_chip_says_final_only_for_rebuilt_reports():
    assert status_chip({"finalized": True}).startswith("마감·잠정")
    assert status_chip({"finalized": True, "final": True}).startswith("마감·확정")


def test_numbers_carry_the_final_flag():
    P = _payload(0)
    assert SN.build(P)["final"] is False
    P["final"] = True
    assert SN.build(P)["final"] is True
