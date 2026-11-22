"""동등성 대조 도구 — 차이를 **놓치지 않는가**(같으면 0, 다르면 그 칸·날짜를 짚는다)."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from sectorflow.collect import equiv as E
from sectorflow.collect import write


def _payload():
    dates = ["2026-11-04", "2026-11-05", "2026-11-06"]
    z = lambda: [0.0, 0.0, 0.0]  # noqa: E731
    cell = {k: [10.0, -20.0, 30.0] for k in E.FLOW_KEYS}
    det = {k: [1.0, 2.0, 3.0] for k in E.DETAIL_KEYS}
    return {"dates": dates, "markets": ["거래소"], "sectors": ["보험", "증권"],
            "n_by_sector": {"거래소": {"보험": 3, "증권": 2}},
            "flows": {"거래소": {"보험": copy.deepcopy(cell), "증권": copy.deepcopy(cell)}},
            "detail": {"거래소": {"보험": copy.deepcopy(det), "증권": copy.deepcopy(det)}},
            "ret": {"거래소": {"보험": [0.0, 1.0, -1.0], "증권": z()}},
            "retw": {"거래소": {"보험": z(), "증권": z()}},
            "iret": {"거래소": {"보험": [None, 1.0, 2.0], "증권": None}},
            "names": {"000810": {"win": {"5": {"inst": 1.0}}}}}


def test_identical_payloads_have_no_differences():
    P = _payload()
    rows = E.flows_table(P, copy.deepcopy(P))
    assert rows and all(r["max_abs"] == 0 and r["days_diff"] == 0 and r["l1_rel"] == 0 for r in rows)
    assert E.leaf_diff(P, copy.deepcopy(P)) == []


def test_flow_difference_is_located_by_sector_actor_and_day():
    A, B = _payload(), _payload()
    A["flows"]["거래소"]["증권"]["inst"][1] = -19.0
    rows = {(r["sector"], r["actor"]): r for r in E.flows_table(A, B)}
    r = rows[("증권", "inst")]
    assert r["max_abs"] == 1.0 and r["max_abs_date"] == "2026-11-05" and r["days_diff"] == 1
    assert r["l1_rel"] == pytest.approx(1 / 60)
    assert rows[("보험", "inst")]["max_abs"] == 0


def test_ret_table_reports_daily_and_cumulative_gap():
    A, B = _payload(), _payload()
    A["ret"]["거래소"]["보험"][2] = -0.5
    r = {x["sector"]: x for x in E.ret_table(A, B, "ret")}["보험"]
    assert r["max_abs_pp"] == 0.5 and r["days_diff"] == 1
    assert r["cum_diff_pp"]["1"] == pytest.approx(0.5)
    one_sided = E.ret_table({**A, "iret": {"거래소": {"보험": None, "증권": [0, 0, 0]}}}, B, "iret")
    assert {x["sector"]: x.get("only") for x in one_sided} == {"보험": "b", "증권": "a"}


def test_leaf_summary_folds_codes_and_indices():
    A, B = _payload(), _payload()
    A["names"]["000810"]["win"]["5"]["inst"] = 1.5
    A["flows"]["거래소"]["보험"]["tv"] = [10.0]           # 길이가 다르다
    groups = {g["group"]: g for g in E.summarize_leaves(E.leaf_diff(A, B))}
    assert groups["/names/<code>/win/5/inst"]["max_abs"] == 0.5
    assert groups["/flows/거래소/보험/tv"]["kind"] == "len"


def test_restrict_keeps_only_the_slice_sectors():
    P = _payload()
    R = E.restrict(P, ["증권"])
    assert R["sectors"] == ["증권"] and list(R["flows"]["거래소"]) == ["증권"]


def _db(path, close, inst):
    con = write.open_db(path)
    con.execute("INSERT INTO stocks (code, name, market, sector, kind) VALUES ('000810','삼성화재','거래소','보험','')")
    con.execute("INSERT INTO supply_demand(code,date,close,institution,source) VALUES (?,?,?,?,?)",
                ("000810", "2026-11-06", close, inst, "kiwoom"))
    con.execute("INSERT INTO daily_bars_adjusted(code, date, close) VALUES ('000810','2026-11-06',?)",
                (float(close),))
    con.commit()
    con.close()
    return str(path)


def test_rowdiff_counts_mismatching_cells(tmp_path):
    a = _db(tmp_path / "a.db", 400000, 100)
    b = _db(tmp_path / "b.db", 400000, 120)
    rep = E.rowdiff(a, b)
    sd = rep["supply_demand"]
    assert sd["both"] == 1 and list(sd["cols"]) == ["institution"]
    assert sd["cols"]["institution"]["worst"] == ["000810", "2026-11-06", 100, 120]
    assert rep["daily_bars_adjusted"]["cols"] == {}


def test_slice_refuses_to_overwrite_without_force(tmp_path):
    out = tmp_path / "krq.db"
    out.write_bytes(b"keep me")
    with pytest.raises(SystemExit, match="--force"):
        E.main(["slice", "--dsn", "postgresql://u@127.0.0.1:1/x", "--sectors", "보험",
                "--start", "2026-01-01", "--end", "2026-11-06", "--out", str(out)])
    assert out.read_bytes() == b"keep me"


def _long(n=30):
    dates = [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]
    one = lambda v: [v] * n  # noqa: E731
    return {"dates": dates, "markets": ["거래소", "코스닥"], "sectors": ["보험"],
            "ret": {"거래소": {"보험": one(0.5)}, "코스닥": {"보험": one(-0.5)}},
            "retw": {"거래소": {"보험": one(3.0)}, "코스닥": {"보험": one(1.0)}}}


def test_any_end_window_finds_a_gap_that_the_reference_day_window_misses():
    """오차가 구간 앞에 몰려 있으면 기준일에 끝나는 창은 깨끗하다 — Phase 1 이 처음 놓친 부류."""
    A, B = _long(), _long()
    A["ret"]["코스닥"]["보험"][3] = 1.5                     # 4번째 날 하루만 2%p 다르다
    res = E.ret_any_end(A, B, windows=(1, 5, 20))
    assert res["1"]["max_pp"] == pytest.approx(2.0) and res["1"]["end"] == B["dates"][3]
    assert res["1"]["market"] == "코스닥" and res["1"]["n"] == 3 * 30     # 거래소·코스닥·전체
    assert res["5"]["end"] in B["dates"][3:8] and res["5"]["max_pp"] > 1.9
    tail = {r["market"]: r["cum_diff_pp"]["5"] for r in E.ret_table(A, B, "ret")}
    assert tail == {"거래소": 0.0, "코스닥": 0.0}            # 기준일 끝 5일 창은 0 이다
    late = E.ret_any_end(A, B, windows=(1, 5), first=B["dates"][4])
    assert late["1"]["max_pp"] == 0 and late["5"]["max_pp"] == 0 and late["5"]["n"] == 3 * 22
    early = E.ret_any_end(A, B, windows=(1,), last=B["dates"][3])
    assert early["1"]["max_pp"] == pytest.approx(2.0) and early["1"]["n"] == 3 * 4


def test_all_markets_cell_is_cap_weighted_like_the_numbers_page():
    A, B = _long(), _long()
    A["retw"]["거래소"]["보험"] = [1.0] * 30                # 가중치만 바뀌어도 전체 칸 수익률이 달라진다
    res = E.ret_any_end(A, B, windows=(1,))
    assert res["1"]["market"] == "전체"
    # B 전체 = (0.5·3 − 0.5·1)/4 = 0.25,  A 전체 = 0
    assert res["1"]["max_pp"] == pytest.approx(0.25)
    assert E.ret_any_end(A, B, windows=(1,), first="2099-01-01")["1"]["n"] == 0
    B["iret"] = A["iret"] = {"거래소": {"보험": [0.1] * 30}, "코스닥": {"보험": None}}
    assert E.ret_any_end(A, B, key="iret", windows=(1,))["1"]["n"] == 30   # 업종지수엔 전체 칸이 없다


def test_any_end_windows_cover_every_preset_the_viewer_offers():
    """뷰어가 고를 수 있는 구간(프리셋·260일 기본)을 대조 창이 다 덮는다 — 빠진 길이의 오차는 아무도 안 본다."""
    html = (Path(E.__file__).resolve().parents[3] / "scripts/templates/sector_flow.html").read_text("utf-8")
    presets = {int(n) for n in re.findall(r"\[(\d+),\"", re.search(r"const PRESETS=\[(.*?)\];", html).group(1))}
    assert presets - {0} and (presets - {0}) | {260} <= set(E.ANY_END_WINDOWS)


@pytest.mark.parametrize("side", ["A", "B"])
def test_any_end_treats_a_missing_day_as_flat_on_either_side(side):
    """결측 날은 0% 다 — 한쪽이 결측이고 다른 쪽이 0% 면 차이가 없다."""
    A, B = _long(), _long()
    for P in (A, B):
        P["ret"]["거래소"]["보험"] = [0.0] * len(P["dates"])
        P["ret"]["코스닥"]["보험"] = [0.0] * len(P["dates"])
    (A if side == "A" else B)["ret"]["거래소"]["보험"][3] = None
    assert E.ret_any_end(A, B, windows=(1,))["1"]["max_pp"] == 0
