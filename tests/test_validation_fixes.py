"""2026-11-08 지표 검증이 찾은 결함 — 시총·수익률·G·k·확정 표시.

결과 문서: ``docs/validation/2026-11-08-results.md``.
"""
from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pandas as pd
import pytest

from sectorflow.storage import connect, market_cap_asof_bulk, rights_pending
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

def test_status_chip_says_rebuilt_for_reconstructed_reports():
    """지난 기준일을 나중 코드·확정 데이터로 다시 만든 리포트(KR_QUANT_ASOF)는 확정과 함께 재구성이라 말한다."""
    assert status_chip({"finalized": True, "final": True, "rebuilt": True}).startswith("마감·확정·재구성")
    assert "재구성" not in status_chip({"finalized": True, "final": True})
    # 수치표 페이지(TUI 가 읽는 것)까지 키가 건너가야 칩이 안다 — 재구성이 아닌 날은 키를 싣지 않는다.
    P = _payload(0)
    assert "rebuilt" not in SN.build(P)
    P["rebuilt"] = True
    assert SN.build(P)["rebuilt"] is True


def test_rebuilt_day_outside_the_finalize_window_is_marked_and_keeps_the_final_return_label():
    """창 밖 재구성(finalize_unverified): 칩은 '확정기록없음', 수익률 라벨은 확정과 같은 ~20:00(두 달 지난 마감 값)."""
    from sectorflow.tui import flow_view as FV
    d = {"finalized": True, "rebuilt": True, "finalize_unverified": True}
    assert status_chip(d).startswith("마감·확정기록없음·재구성")
    assert FV.ret_label(d) == FV.RET_LABEL and FV.ret_label(d, short=True) == FV.RET_LABEL_SHORT
    assert FV.ret_label({"final": True}) == FV.RET_LABEL
    assert FV.ret_label({"finalized": True}) == FV.RET_LABEL_PROV
    assert FV.ret_label({"finalized": True}, short=True) == FV.RET_LABEL_PROV_SHORT
    ctx = FV._ctx_lines(dict(d, today={}), [], 200)[0]
    assert FV.RET_LABEL in ctx and "수집 시각" not in ctx, ctx
    assert "수집 시각" in FV._ctx_lines({"finalized": True, "today": {}}, [], 200)[0], "잠정인데 ~20:00 이라 적었다"
    P = _payload(0)
    assert "finalize_unverified" not in SN.build(P)
    P["finalize_unverified"] = True
    assert SN.build(P)["finalize_unverified"] is True


def test_status_chip_says_final_only_for_rebuilt_reports():
    assert status_chip({"finalized": True}).startswith("마감·잠정")
    assert status_chip({"finalized": True, "final": True}).startswith("마감·확정")


def test_numbers_carry_the_final_flag():
    P = _payload(0)
    assert SN.build(P)["final"] is False
    P["final"] = True
    assert SN.build(P)["final"] is True


# ── 권리락 표지: 값은 원 종가 × 그 시점 상장주식수 그대로, 신주가 안 들어온 동안 표지만 ────────────────

@pytest.fixture
def rights_con(tmp_path):
    c = connect(str(tmp_path / "r.db"))
    #            코드, 권리락일, upd_rt, 스냅샷들(날짜, 주식수)
    cases = [("PEND01", "2026-10-26", -49.75, [("2026-10-20", 1000), ("2026-11-03", 1000)]),   # 안 따라왔다
             ("DONE01", "2026-10-05", -50.0, [("2026-09-29", 1000), ("2026-10-27", 2000)]),    # 따라왔다
             ("SMAL01", "2026-10-26", -3.15, [("2026-10-20", 1000)]),                          # 소폭 — 소음
             ("OLD001", "2026-07-01", -50.0, [("2026-06-30", 1000), ("2026-11-03", 1000)]),    # 100일 넘게 안 따라옴 — 끈다
             ("LATE01", "2026-11-05", -50.0, [("2026-10-20", 1000)]),                          # 기준일 뒤
             ("NOBS01", "2026-10-26", -50.0, [("2026-11-03", 2000)]),                          # 직전 주식수가 없어 판단 불가
             ("NOSN01", "2026-10-29", -33.0, [("2026-10-27", 500)])]                           # 권리락 뒤 스냅샷이 아직 없다
    for code, d, rt, snaps in cases:
        c.execute("INSERT INTO corporate_actions VALUES (?,?,'256',?)", (code, d, rt))
        c.executemany("INSERT INTO shares_outstanding_history VALUES (?,?,?)", [(code, sd, v) for sd, v in snaps])
    c.commit()
    return c, [x[0] for x in cases]


def test_rights_pending_marks_only_what_has_not_caught_up(rights_con):
    con, codes = rights_con
    assert rights_pending(con, codes, "2026-11-03") == {
        "PEND01": {"date": "2026-10-26", "rt": -49.75}, "NOSN01": {"date": "2026-10-29", "rt": -33.0}}


def test_rights_pending_does_not_read_past_the_report_day(rights_con):
    """지난 기준일 재구성 — 그날엔 아직 따라오지 않았던 종목은 그날의 표지다(뒤의 스냅샷을 안 본다)."""
    con, codes = rights_con
    assert "DONE01" in rights_pending(con, codes, "2026-10-20")
    assert "PEND01" not in rights_pending(con, codes, "2026-10-20")


def test_rights_are_not_counted_into_the_market_cap(rights_con):
    """표지는 값을 바꾸지 않는다 — 상장주식수는 신주상장일에 바뀐다(storage.SHARE_EVENTS 주석)."""
    con, _ = rights_con
    con.execute("INSERT INTO supply_demand(code,date,close,source) VALUES ('PEND01','2026-11-03',5000,'kiwoom')")
    q = pd.DataFrame({"code": ["PEND01"], "date": ["2026-11-03"]})
    assert market_cap_asof_bulk(con, q).iloc[0] == 5000 * 1000


def test_one_year_percentile_really_looks_back_260_days():
    """리포트 구간이 260일보다 짧은 합성 DB 로는 되돌아보기 길이가 안 드러난다 — 앞쪽 큰 값이 260일 안이면 순위에 든다."""
    ser = [1000.0] * 100 + [float(i % 7) for i in range(200)]       # 300일: 앞 100일이 크다
    win = 5
    span = ser[-260:]                                               # 뒤 260일 = 큰 값 60일 + 작은 값 200일
    sums = [sum(span[i:i + win]) for i in range(len(span) - win + 1)]
    below = sum(1 for x in sums if x < sums[-1])
    ties = sum(1 for x in sums if x == sums[-1])
    assert SN.rolling_pct(ser, win) == pytest.approx(100.0 * (below + (ties - 1) / 2.0) / (len(sums) - 1))
    assert SN.rolling_pct(ser, win) < 80, "큰 값 60일이 분포에 안 들었다"


# ── 시총/지수 비: 주식수 쪽 점프 ─────────────────────────────────────────────────

def _ratio_warns(caps: dict, tmp_path) -> tuple[list, list]:
    tmp_path.mkdir(exist_ok=True)
    con = connect(str(tmp_path / "ratio.db"))
    dates = sorted(caps)
    con.executemany("INSERT INTO sector_index(code,name,date,close) VALUES ('101','코스닥',?,100.0)", [(d,) for d in dates])
    VR.WARNS.clear()
    VR.FAILS.clear()
    VR._f_cap_index_ratio({"dates": dates, "markets": ["코스닥"]}, con.cursor(), "?",
                          {("코스닥", d): v for d, v in caps.items()})
    return list(VR.WARNS), list(VR.FAILS)


def test_cap_to_index_jump_on_the_report_day_warns_but_does_not_fail(tmp_path):
    """2026-01-02 모양 — 낡은 주식수가 일괄 갱신돼 코스닥 비가 하루에 +10.5%. 값이 틀린 건 아니라 실패가 아니다."""
    w, f = _ratio_warns({"2026-01-01": 100.0, "2026-01-02": 110.5}, tmp_path)
    assert w == [f"F/{VR.VF_CAP_INDEX_MARK}"] and not f


def test_old_cap_to_index_jump_in_the_window_does_not_warn_every_day(tmp_path):
    """01-02 의 점프는 1년 동안 창 안에 있다 — 기준일만 경고한다."""
    w, _ = _ratio_warns({"2026-01-01": 100.0, "2026-01-02": 110.5, "2026-01-05": 111.0}, tmp_path)
    assert w == []


def test_cap_to_index_threshold_sits_above_the_largest_normal_day(tmp_path):
    """주간 스냅샷 뒤 평소 최대 2.47%(통합 종가 ↔ 정규장 지수) — 조용해야 한다. 07-09 의 3.52% 는 울려야 한다."""
    assert _ratio_warns({"2026-10-19": 100.0, "2026-10-20": 97.53}, tmp_path)[0] == []
    assert _ratio_warns({"2026-07-08": 100.0, "2026-07-09": 103.52}, tmp_path / "x")[0]
