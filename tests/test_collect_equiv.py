"""동등성 대조 도구 — 차이를 **놓치지 않는가**(같으면 0, 다르면 그 칸·날짜를 짚는다)."""

from __future__ import annotations

import copy

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
    con.execute("INSERT INTO stocks VALUES ('000810','삼성화재','거래소','보험','')")
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
