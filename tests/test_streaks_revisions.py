"""연속 순매매(``today.streaks``)·잠정→확정 수정 폭의 상위 3 유지율과 아카이브 시드, 그리고 검증기 T 층이
확정 리포트의 잠정값을 **옆 폴더의 잠정 리포트에서** 다시 세는지(2026-11-08 리뷰 3차 D1-M1·D1-m1·D3-M1).

합성 페이로드(``test_payload_today._payload``)와 임시 리포트 루트로 돈다. DB·네트워크 불요(웹 대조는 node 필요).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
node = pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")


def _mod(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sf = _mod("sector_flow", REPO / "scripts" / "sector_flow.py")
vr = _mod("verify_report", REPO / "scripts" / "verify_report.py")
sn = _mod("sector_numbers", REPO / "scripts" / "sector_numbers.py")
seed = _mod("seed_revisions", REPO / "scripts" / "seed_revisions.py")
PT = _mod("payload_today", REPO / "tests" / "test_payload_today.py")


def _thick(Q: dict) -> dict:
    """합성 섹터는 4종목이라 전부 얇다 — 종목 수를 12로 올리고 거래소 보험만 얇게 둔 뒤 연속을 다시 센다."""
    for m in Q["n_by_sector"]:
        for s in Q["n_by_sector"][m]:
            Q["n_by_sector"][m][s] = 12
    Q["n_by_sector"]["거래소"]["보험"] = 4
    Q["today"]["streaks"] = sf.streak_block(Q)
    return Q


@pytest.fixture(scope="module")
def P() -> dict:
    return _thick(PT._payload())


def _fails(P: dict, folder=None) -> list[str]:
    vr.FAILS.clear()
    vr.layer_t(P, None if folder is None else str(folder))
    return list(vr.FAILS)


# ── 연속 순매매 ─────────────────────────────────────────────────────────────

def test_streak_counts_same_sign_days_and_a_near_zero_day_breaks_it():
    """≈0 날(목록 Σ|값| 의 1% 미만)은 사는 날도 파는 날도 아니다 — 연속을 끊는다. 백분위는 날마다의 연속 길이 분포."""
    g = [100.0] * 7
    f = sf.streak_fact([5.0, -3.0, -4.0, -0.5, -2.0, -6.0, -7.0], g)
    assert f == {"sign": -1, "days": 3, "sum": -15.0, "pct": 100.0}
    # 길이 분포 [1,1,2,0,1,2] 에서 2 는 아래 4개·동률 2개(자기 포함) → midrank 100·(4 + 0.5)/5
    f2 = sf.streak_fact([5.0, -3.0, -4.0, -0.5, -2.0, -6.0], g[:6])
    assert f2["days"] == 2 and f2["pct"] == pytest.approx(90.0)
    assert sf.streak_fact([5.0, 5.0, 0.2], [100.0] * 3) == {"sign": 0, "days": 0, "sum": 0.0, "pct": None}
    assert sf.streak_fact([5.0, 5.0], [0.0, 0.0])["days"] == 0, "목록이 비면(Σ|값|=0) 부호가 없다"


def _tiny() -> dict:
    """손으로 짠 한 시장 페이로드 — 섹터 A·B(두꺼움), C(얇음), (미분류)."""
    n = 6
    secs = ["A", "B", "C", "(미분류)"]
    inst = {"A": [1, 1, -9, 5, 5, 5], "B": [-1, 9, -9, 5, 5, 6], "C": [3, 3, 3, 3, 3, 3], "(미분류)": [2] * n}
    flows = {"거래소": {s: {"inst": [float(x) for x in inst[s]], "forgn": [0.0] * n, "indiv": [0.0] * n,
                           "etc": [0.0] * n, "tv": [1.0] * n} for s in secs}}
    detail = {"거래소": {s: {"invtrt": [0.0] * n, "penfnd_etc": [0.0] * n} for s in secs}}
    return {"dates": [f"2026-11-0{i + 1}" for i in range(n)], "markets": ["거래소"], "sectors": secs,
            "n_by_sector": {"거래소": {"A": 12, "B": 12, "C": 3, "(미분류)": 30}},
            "flows": flows, "detail": detail}


def test_streak_block_skips_thin_and_unclassified_sectors_and_breaks_ties_by_amount():
    S = sf.streak_block(_tiny())
    assert set(S["sectors"]["거래소"]) == {"A", "B"}, "얇은 섹터·(미분류)는 섹터 연속에서 뺀다"
    # A·B 모두 3일 연속 순매수 — 누적 |금액| 이 큰 B(16) 가 A(15) 보다 앞선다.
    assert S["sectors"]["거래소"]["A"]["inst"]["days"] == S["sectors"]["거래소"]["B"]["inst"]["days"] == 3
    assert S["longest"]["거래소"]["inst"] == "B"
    assert S["longest"]["거래소"]["forgn"] is None, "연속이 없으면 최장도 없다"
    # 시장 합계는 그 시장 모든 섹터(얇은 섹터·미분류 포함)의 합이다: [5, 15, −13, 15, 15, 16].
    assert S["market"]["거래소"]["inst"] == {"sign": 1, "days": 3, "sum": 46.0, "pct": 100.0}


def _streak_days(Q):
    Q["today"]["streaks"]["market"]["거래소"]["forgn"]["days"] += 1


def _streak_sector_pct(Q):
    row = next(iter(Q["today"]["streaks"]["sectors"]["코스닥"].values()))
    row["inst"]["pct"] = (row["inst"]["pct"] or 0.0) + 7.0


def _streak_thin_kept(Q):
    Q["today"]["streaks"]["sectors"]["거래소"]["보험"] = {"inst": {"sign": 1, "days": 1, "sum": 1.0, "pct": 50.0}}


def _streak_longest(Q):
    L = Q["today"]["streaks"]["longest"]["전체"]
    secs = sorted(Q["today"]["streaks"]["sectors"]["전체"])
    L["inst"] = next(s for s in secs if s != L["inst"])


def _streak_missing(Q):
    del Q["today"]["streaks"]


@pytest.mark.parametrize("corrupt, want", [
    (_streak_days, "T/연속 순매매 — 시장 합계의 연속 일수·누적·1년 백분위 재계산"),
    (_streak_sector_pct, "T/연속 순매매 — 섹터(얇은 섹터·미분류 제외)의 연속 재계산"),
    (_streak_thin_kept, "T/연속 순매매 — 섹터(얇은 섹터·미분류 제외)의 연속 재계산"),
    (_streak_longest, "T/연속 순매매 — 시장·주체별 최장 섹터 재계산"),
    (_streak_missing, "T/연속 순매매 블록(today.streaks)이 있는가"),
])
def test_the_verifier_recounts_the_streaks(P, corrupt, want):
    assert _fails(P) == []
    bad = copy.deepcopy(P)
    corrupt(bad)
    assert want in _fails(bad)


# ── 상위 3 유지율 ───────────────────────────────────────────────────────────

def _top_cells():
    prov = [100, 90, 80, 70, 60, 50, 40, 30]
    fin = [100, 90, 65, 70, 60, 50, 40, 30]          # 셋째가 넷째 아래로 — 상위 3 중 2개만 남는다
    cells = [["거래소", f"S{i}", "inst", float(p), float(f)] for i, (p, f) in enumerate(zip(prov, fin))]
    cells += [["코스닥", f"K{i}", "inst", float(i), float(i)] for i in range(3)]   # 3섹터 목록 — 유지율에서 뺀다
    return cells


def test_revision_top3_counts_sectors_kept_in_the_top_three(tmp_path):
    assert sf.revision_top3(_top_cells()) == (4, 6), "거래소·전체 두 목록에서 3칸 중 2칸씩"
    PT._final_folder(tmp_path, "2026-11-04", _top_cells())
    R = sf.revision_stats({"dates": ["2026-11-04", "2026-11-05"]}, str(tmp_path))
    assert R["top3_keep_pct"] == pytest.approx(66.7) and R["top3_lists"] == 2
    # 투신+연기금은 두 칸의 합 — 한쪽이 없으면 그 섹터는 그 목록에서 빠진다.
    inv = [["거래소", f"S{i}", p, float(10 - i), float(10 - i)] for i in range(7) for p in ("invtrt", "penfnd_etc")]
    assert sf.revision_top3(inv + [["거래소", "S9", "invtrt", 99.0, 99.0]]) == (6, 6)


# ── 종목(기관상위) 수정 ───────────────────────────────────────────────────────

def _stk_payload(vals: dict, extra: "dict | None" = None) -> dict:
    """names 만 있는 최소 페이로드 — vals: code → (시장, 섹터, 기관, 투신, 연기금, 외국인)."""
    names = {code: {"name": "N" + code, "sector": s, "market": m,
                    "win": {"1": {"inst": i, "invtrt": iv, "penfnd_etc": pf, "forgn": fg}}}
             for code, (m, s, i, iv, pf, fg) in vals.items()}
    return {"names": names | (extra or {})}


def _stk_pair():
    """한 섹터(거래소/반도체) — 잠정 기관상위 {a1, a2}(a2 가 80% 를 처음 넘긴 행), 확정에서 a6 이 처음 잡힌다."""
    prov = _stk_payload({
        "a1": ("거래소", "반도체", 100.0, 10.0, 0.0, 20.0),
        "a2": ("거래소", "반도체", 50.0, 0.1, 0.0, -5.0),
        "a3": ("거래소", "반도체", 30.0, 0.0, 0.0, 0.0),
        "a4": ("거래소", "반도체", 5.0, 0.0, 0.0, 0.0),
        "a5": ("거래소", "반도체", 1.0, 0.0, 0.0, 0.0),
    }, extra={"a6": {"name": "Na6", "sector": "반도체", "market": "거래소", "win": {}}})   # 1일 칸 없음
    fin = _stk_payload({
        "a1": ("거래소", "반도체", 100.0, 10.2, 0.0, 20.0),    # Δ 0.2 — 문턱(0.5억) 안
        "a2": ("거래소", "반도체", 62.3, 0.1, 0.0, 5.0),       # 기관 Δ 12.3, 외국인 부호 뒤집힘
        "a3": ("거래소", "반도체", 30.0, 0.0, 0.0, 0.0),
        "a4": ("거래소", "반도체", 5.0, 0.0, 0.0, 0.0),
        "a5": ("거래소", "반도체", 1.0, 0.0, 0.0, 0.0),
        "a6": ("거래소", "반도체", 90.0, 0.0, 0.0, 0.0),       # 수집 뒤 처음 잡힘 — 잠정 쪽이 None
    })
    return prov, fin


def test_stock_diff_lists_driver_rows_beyond_the_threshold(monkeypatch):
    """기관상위(|기관| 누적 80% — 넘긴 행 포함)의 합집합만 보고, 문턱을 넘는 행만 max|Δ| 순으로 상한까지 싣는다."""
    prov, fin = _stk_pair()
    S = sf.stock_diff(fin, prov)
    assert S["n_stocks"] == 3, "잠정 {a1,a2} ∪ 확정 {a1,a6,a2} — a3 이하는 80% 밖"
    assert [(r[2], r[0], r[1]) for r in S["rows"]] == [("a6", "거래소", "반도체"), ("a2", "거래소", "반도체")], \
        "한쪽 값이 없는 행이 맨 앞, a1(Δ 0.2)은 문턱 안"
    assert S["rows"][0][4]["inst"] == [None, 90.0] and S["rows"][1][4]["inst"] == [50.0, 62.3]
    assert S["n_rows"] == 2 == len(S["rows"]) and S["dropped_sectors"] == 0
    monkeypatch.setattr(sf, "STOCK_DIFF_CAP", 1)
    S = sf.stock_diff(fin, prov)
    assert len(S["rows"]) == 1 and S["n_rows"] == 2, "상한은 rows 만 자르고 요약은 전체에서 센다"
    # 옛 형식(1일 종목 칸이 아예 없다) 게시본 대비 — 모든 기관상위를 "수정"이라 적는 것은 거짓이다.
    legacy = {"names": {c: dict(r, win={}) for c, r in prov["names"].items()}}
    assert sf.stock_diff(fin, legacy) is None


def test_stock_diff_summary_gates_small_cells_and_counts_flips():
    """요약: 분모 게이트(그 (시장, 섹터, 주체) 기관상위 Σ|확정| 의 2%)를 넘는 칸의 중앙값·부호 뒤집힘."""
    prov, fin = _stk_pair()
    S = sf.stock_diff(fin, prov)
    # 기관 [0, 19.7] · 투신 a1 [2.0](a2 0.1 은 게이트 밖) · 외국인 [0, 200(뒤집힘)] · 연기금 0 값 없음 → 5칸.
    assert S["n_cells"] == 5 and S["flips"] == 1
    assert S["rel_median_pct"] == pytest.approx(2.0, abs=0.051)


def test_stock_top3_keeps_and_skips_thin_or_reshuffled_lists():
    """(시장, 섹터, 주체) 상위 3 종목 유지 — 6종목 미만 목록과 종목 구성이 갈린 (시장, 섹터)는 안 센다."""
    def sector(s, codes, inst):
        return {c: ("거래소", s, v, 10.0 - i, 20.0 - i, 30.0 - i) for i, (c, v) in enumerate(zip(codes, inst))}

    b = [f"b{i}" for i in range(6)]
    prov = _stk_payload(sector("금융", b, [60.0, 50.0, 40.0, 30.0, 20.0, 10.0])
                        | sector("화학", [f"c{i}" for i in range(5)], [9.0, 8.0, 7.0, 6.0, 5.0]))
    fin = _stk_payload(sector("금융", b, [60.0, 50.0, 25.0, 30.0, 20.0, 10.0])    # b3 이 b4 아래로 — 기관만 2/3
                       | sector("화학", [f"c{i}" for i in range(5)], [9.0, 8.0, 7.0, 6.0, 5.0]))
    S = sf.stock_diff(fin, prov)
    assert (S["top3_kept"], S["top3_lists"]) == (2 + 3 * 3, 4), "화학은 5종목 — 목록에서 뺀다"
    # 확정에만 있는 코드가 끼면 그 (시장, 섹터)는 통째로 수정 폭이 아니다(편입·분류 변경).
    fin2 = json.loads(json.dumps(fin))
    fin2["names"]["b9"] = {"name": "Nb9", "sector": "금융", "market": "거래소",
                           "win": {"1": {"inst": 999.0, "invtrt": 0.0, "penfnd_etc": 0.0, "forgn": 0.0}}}
    S2 = sf.stock_diff(fin2, prov)
    assert S2["dropped_sectors"] == 1 and S2["top3_lists"] == 0 and S2["n_rows"] == 0
    assert not any(r[1] == "금융" for r in S2["rows"])


def test_revision_stats_pools_stock_summaries_across_days(tmp_path):
    """종목 집계: 칸·뒤집힘·유지는 날들의 합, 중앙값은 일별 중앙값의 중앙값 — 요약이 없는 날은 안 센다."""
    root = _revision_root(tmp_path)
    R = _rev_payload(root)["revision_stats"]["stocks"]
    assert R["n_dates"] == 2, "11-04 확정 기록에는 종목 요약이 없다 — 시드 두 날(11-02·11-03)만"
    assert R["n_cells"] == 16 and R["flips"] == 1
    assert R["rel_median_pct"] == pytest.approx(3.0, abs=0.051)
    assert R["top3_keep_pct"] == pytest.approx(88.9, abs=0.051) and R["top3_lists"] == 3
    # 종목 요약을 남긴 기록이 하나도 없으면 비워 둔다(0 이 아니라 모름).
    none = tmp_path / "none"
    PT._final_folder(none, "2026-11-04", [["거래소", "S0", "inst", 1.0, 1.0]])
    assert sf.revision_stats({"dates": ["2026-11-05"]}, str(none))["stocks"] is None


# ── 시드 ────────────────────────────────────────────────────────────────────

def _seed_file(root: Path, date: str, cells, label=sf.REV_SEED_LABEL, built=None, stocks=None):
    d = root / sf.REV_SEED_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{date}.json").write_text(json.dumps({"base_final": False, "base_built": built or f"{date} 18:10",
                                                "cells_1d": cells, "stocks": stocks,
                                                "seed": {"label": label}}), "utf-8")


#: 시드 두 날의 종목(기관상위) 일별 요약 — 집계: 날 2, 칸 16, 뒤집힘 1, 중앙 (4+2)/2=3.0, 유지 8/(3×3)=88.9%.
_STK_A = {"n_cells": 10, "rel_median_pct": 4.0, "flips": 1, "top3_kept": 5, "top3_lists": 2}
_STK_B = {"n_cells": 6, "rel_median_pct": 2.0, "flips": 0, "top3_kept": 3, "top3_lists": 1}


def _revision_root(tmp_path: Path) -> Path:
    big = [["거래소", f"S{i}", "inst", 100.0, 100.0] for i in range(8)]
    moved = big[:7] + [["거래소", "S7", "inst", -50.0, 100.0]]
    PT._final_folder(tmp_path, "2026-11-04", big)                  # 진짜 확정 기록 — 시드보다 먼저(종목 요약 없음)
    _seed_file(tmp_path, "2026-11-04", moved)
    (tmp_path / "2026-11-03").mkdir()                              # 재구성 확정(기록 없음) + 시드
    (tmp_path / "2026-11-03" / "FINAL.txt").write_text("재구성")
    _seed_file(tmp_path, "2026-11-03", moved, stocks=_STK_A)
    _seed_file(tmp_path, "2026-11-02", _top_cells(), stocks=_STK_B)   # 폴더 없이 시드만
    _seed_file(tmp_path, "2026-10-31", moved, label="다른 것")     # 표지가 다르다 — 안 읽는다
    _seed_file(tmp_path, "2026-10-30", moved, built="2026-10-31 07:40")   # 다음날 아침 것 — 안 읽는다
    return tmp_path


def _rev_payload(root: Path) -> dict:
    P = {"dates": ["2026-11-04", "2026-11-05"]}
    P["revision_stats"] = sf.revision_stats(P, str(root))
    return P


def test_revision_stats_reads_the_seed_only_where_the_folder_has_no_record(tmp_path):
    root = _revision_root(tmp_path)
    R = _rev_payload(root)["revision_stats"]
    assert R["dates"] == ["2026-11-02", "2026-11-03", "2026-11-04"]
    assert R["seed"] == {"label": sf.REV_SEED_LABEL, "dates": ["2026-11-02", "2026-11-03"]}
    assert R["flips"] == 1, "11-04 는 확정 기록(뒤집힘 0)을, 11-03 은 시드(뒤집힘 1)를 읽는다"
    assert R["skipped"]["2026-10-31"] == "시드 표지가 없다"
    assert "기준일 당일" in R["skipped"]["2026-10-30"]


def _rev_bump_flips(R):
    R["flips"] += 1


def _rev_bump_keep(R):
    R["top3_keep_pct"] = (R["top3_keep_pct"] or 0.0) + 5.0


def _rev_drop_seed(R):
    R["seed"] = None


def _rev_stk_cells(R):
    R["stocks"]["n_cells"] += 1


def _rev_stk_median(R):
    R["stocks"]["rel_median_pct"] = (R["stocks"]["rel_median_pct"] or 0.0) + 1.0


@pytest.mark.parametrize("corrupt, want", [
    (_rev_bump_flips, "T/잠정→확정 수정 폭 — 날짜·칸 수·중앙값·부호 뒤집힘을 옆 폴더의 확정 기록·시드에서 다시 센 것과 같은가"),
    (_rev_bump_keep, "T/잠정→확정 수정 폭 — 상위 3 유지율 재계산"),
    (_rev_drop_seed, "T/잠정→확정 수정 폭 — 시드로 센 날짜와 출처 표지"),
    (_rev_stk_cells, "T/잠정→확정 수정 폭 — 종목(기관상위) 일별 요약의 집계 재계산"),
    (_rev_stk_median, "T/잠정→확정 수정 폭 — 종목(기관상위) 일별 요약의 집계 재계산"),
])
def test_the_verifier_recounts_revision_stats_from_the_sibling_folders(tmp_path, corrupt, want):
    root = _revision_root(tmp_path)
    P = _rev_payload(root)
    folder = str(root / "2026-11-05.building.7")
    vr.FAILS.clear()
    vr._vt_revision_check(P, folder)
    assert vr.FAILS == []
    corrupt(P["revision_stats"])
    vr.FAILS.clear()
    vr._vt_revision_check(P, folder)
    assert want in vr.FAILS


# ── 검증기 T 층 — 확정 리포트의 잠정값을 옆 폴더의 잠정 리포트에서 다시 센다 ─────────────

def _final_build(tmp_path: Path):
    _prev, new = PT._prev_and_cur()
    asof = new["dates"][-1]
    old = json.loads(json.dumps(new))
    old["flows"]["코스닥"]["금융"]["inst"][-1] -= 12.34
    ser = old["flows"]["거래소"]["반도체"]["inst"]
    ser[-1] = max(ser) + 50.0                       # 잠정에서는 1년 최고 — 확정에서 극단 목록을 나간다
    # 종목 층 — |기관| 최대 종목(자기 섹터의 기관상위)의 잠정값을 틀어 기관상위 행 하나를 만든다.
    w1 = {c: r["win"]["1"] for c, r in old["names"].items() if (r.get("win") or {}).get("1")}
    code = max(w1, key=lambda c: abs(w1[c]["inst"]))
    old["names"][code]["win"]["1"]["inst"] = round(w1[code]["inst"] - 12.3, 1)
    old.pop("final", None)
    PT._write(tmp_path, asof, old)
    fd, why = sf.final_diff_block(new, str(tmp_path))
    assert why is None and fd["n_changed"] >= 2 and fd["extremes_change"]
    assert fd["stocks"]["n_rows"] >= 1 and fd["stocks"]["rows"][0][2] == code
    new.update(final=True, final_diff=fd, final_diff_reason=why)
    build = tmp_path / f"{asof}.building.3"
    build.mkdir()
    (build / "final_diff.json").write_text(json.dumps(fd, ensure_ascii=False), "utf-8")
    return new, build


def _fd_old(P, b):
    P["final_diff"]["changed"][0]["old"] += 1.0


def _fd_cells(P, b):
    P["final_diff"]["cells_1d"][0][3] = (P["final_diff"]["cells_1d"][0][3] or 0.0) + 5.0


def _fd_ext(P, b):
    P["final_diff"]["extremes_change"] = {}


def _fd_built(P, b):
    P["final_diff"]["base_built"] = "2026-01-01 00:00"


def _fd_json(P, b):
    (b / "final_diff.json").write_text(json.dumps({"cells_1d": []}), "utf-8")


def _fd_stk_row(P, b):
    P["final_diff"]["stocks"]["rows"][0][4]["inst"][1] = (P["final_diff"]["stocks"]["rows"][0][4]["inst"][1] or 0) + 1.0


def _fd_stk_missing(P, b):
    del P["final_diff"]["stocks"]


def _fd_stk_kept(P, b):
    P["final_diff"]["stocks"]["top3_kept"] += 1


def _fd_none(P, b):
    P["final_diff"], P["final_diff_reason"] = None, "어쩌다"


def _fd_sibling_gone(P, b):
    os.remove(b.parent / P["dates"][-1] / "payload.json")


@pytest.mark.parametrize("corrupt, want", [
    (_fd_old, "T/잠정 → 확정 — 잠정값·차이·칸 수를 옆 잠정 리포트에서 다시 센 것과 같은가"),
    (_fd_cells, "T/잠정 → 확정 — 잠정값·차이·칸 수를 옆 잠정 리포트에서 다시 센 것과 같은가"),
    (_fd_ext, "T/잠정 → 확정 — %ile 극단 목록의 들고 남 재계산"),
    (_fd_stk_row, "T/잠정 → 확정 — 종목(기관상위) 행·요약을 옆 잠정 리포트에서 다시 센 것과 같은가"),
    (_fd_stk_missing, "T/잠정 → 확정 — 종목(기관상위) 행·요약을 옆 잠정 리포트에서 다시 센 것과 같은가"),
    (_fd_stk_kept, "T/잠정 → 확정 — 종목(기관상위) 행·요약을 옆 잠정 리포트에서 다시 센 것과 같은가"),
    (_fd_built, "T/잠정 → 확정 — 견준 리포트의 지위·생성 시각 = 옆 폴더의 것"),
    (_fd_json, "T/final_diff.json(다음 리포트들의 수정 폭 원천) = 페이로드의 final_diff"),
    (_fd_none, "T/잠정 → 확정 — 바꿔 끼울 잠정 리포트가 옆에 있는데 비교가 비지 않았는가(덮어쓰기 지문이 같을 때)"),
    (_fd_sibling_gone, "T/잠정 → 확정 — 견준 잠정 리포트가 옆 폴더에 있고 지문이 같은가"),
])
def test_the_verifier_recounts_final_diff_from_the_provisional_report_before_the_swap(tmp_path, corrupt, want):
    P, build = _final_build(tmp_path)
    assert _fails(P, build) == []
    corrupt(P, build)
    assert want in _fails(P, build)


def test_the_stock_layer_is_empty_against_a_legacy_provisional_without_one_day_cells(tmp_path):
    """옆 잠정 리포트가 옛 형식(1일 종목 칸 없음)이면 종목 층은 None 이어야 하고, 검증기가 그걸 지킨다."""
    P, build = _final_build(tmp_path)
    sib = tmp_path / P["dates"][-1] / "payload.json"
    old = json.loads(sib.read_text("utf-8"))
    for r in old["names"].values():
        (r.get("win") or {}).pop("1", None)
    sib.write_text(json.dumps(old, ensure_ascii=False), "utf-8")
    P["final_diff"], P["final_diff_reason"] = sf.final_diff_block(P, str(tmp_path))
    assert P["final_diff"]["stocks"] is None
    (build / "final_diff.json").write_text(json.dumps(P["final_diff"], ensure_ascii=False), "utf-8")
    assert _fails(P, build) == []
    P["final_diff"]["stocks"] = {"n_rows": 0}
    assert "T/잠정 → 확정 — 종목(기관상위) 층은 1일 칸이 없는 옛 형식 게시본 대비에서 비어 있는가" in _fails(P, build)


def test_after_the_swap_the_provisional_recount_is_skipped_not_failed(tmp_path, capsys):
    """바꿔 끼운 뒤(``verify_merge --report-dir <루트>/<기준일>``)에는 옆 폴더가 자기 자신이다 — 확정을 잠정으로 읽지 않는다."""
    P, build = _final_build(tmp_path)
    dest = tmp_path / P["dates"][-1]
    shutil.rmtree(dest)
    build.rename(dest)
    (dest / "payload.json").write_text(json.dumps(P, ensure_ascii=False), "utf-8")
    assert _fails(P, dest) == []
    assert "바꿔 끼운 뒤다" in capsys.readouterr().out


# ── 시드 스크립트 ───────────────────────────────────────────────────────────

def _kst_mtime(path: Path, stamp: str) -> None:
    t = datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=sf.KST).timestamp()
    os.utime(path, (t, t))


def _archive(tmp_path: Path):
    _prev, fin = PT._prev_and_cur()
    asof = fin["dates"][-1]
    prov = json.loads(json.dumps(fin))
    prov["flows"]["코스닥"]["금융"]["inst"][-1] -= 12.34
    # 종목 층 — 코스닥 |기관| 최대 종목(자기 섹터의 기관상위)의 잠정값을 튼다.
    k2 = max((c for c, r in prov["names"].items() if r["market"] == "코스닥" and (r.get("win") or {}).get("1")),
             key=lambda c: abs(prov["names"][c]["win"]["1"]["inst"]))
    prov["names"][k2]["win"]["1"]["inst"] = round(prov["names"][k2]["win"]["1"]["inst"] - 23.4, 1)
    # 그 사이 분류가 바뀐 종목 하나 — 거래소 금융 → 화학. 두 칸(× 6주체)은 수정 폭이 아니라 버린다.
    code = next(c for c, r in prov["names"].items() if r["market"] == "거래소" and r["sector"] == "금융")
    prov["names"][code] = dict(prov["names"][code], sector="화학")
    arch, final = tmp_path / "archive", tmp_path / "reports"
    PT._write(arch, asof, prov)
    _kst_mtime(arch / asof / "payload.json", f"{asof} 18:10")
    fin["final"] = True
    PT._write(final, asof, fin, "FINAL.txt")
    return arch, final, asof, prov


def test_seed_script_writes_final_diff_cells_from_the_evening_archive(tmp_path):
    arch, final, asof, _prov = _archive(tmp_path)
    out = tmp_path / "scratch" / sf.REV_SEED_DIR
    assert seed.main(["--archive", str(arch), "--final-root", str(final), "--out", str(out),
                      "--since", "2000-01-01"]) == 0
    doc = json.loads((out / f"{asof}.json").read_text("utf-8"))
    assert doc["seed"]["label"] == sf.REV_SEED_LABEL and doc["base_built"] == f"{asof} 18:10"
    assert doc["seed"]["dropped_membership"] == 2 * len(sf.FINAL_DIFF_ACTORS)
    cells = {(m, s, a): (o, n) for m, s, a, o, n in doc["cells_1d"]}
    assert ("거래소", "금융", "inst") not in cells and ("거래소", "화학", "inst") not in cells
    o, n = cells[("코스닥", "금융", "inst")]
    assert n - o == pytest.approx(12.34, abs=0.011)
    # 종목(기관상위) 층도 시드에 싣는다 — 분류가 바뀐 (시장, 섹터) 둘은 종목 층에서도 통째로 뺀다.
    S = doc["stocks"]
    assert S["dropped_sectors"] == 2 and not any((r[0], r[1]) in {("거래소", "금융"), ("거래소", "화학")}
                                                 for r in S["rows"])
    assert any(r[4]["inst"][1] - r[4]["inst"][0] == pytest.approx(23.4, abs=0.011)
               for r in S["rows"] if None not in r[4]["inst"]), S["rows"][:3]
    # 적용하면 다음 리포트의 수정 폭이 그 시드를 읽고 출처를 적는다.
    shutil.copytree(out, final / sf.REV_SEED_DIR)
    R = sf.revision_stats({"dates": [asof, "2099-01-01"]}, str(final))
    assert R["seed"] == {"label": sf.REV_SEED_LABEL, "dates": [asof]} and R["n_dates"] == 1


def test_seed_script_skips_next_morning_incomplete_and_pre_regime_archives(tmp_path):
    arch, final, asof, prov = _archive(tmp_path)
    folder = str(arch / asof)
    assert seed.seed_one(asof, folder, str(final), "2000-01-01")[0] is not None
    assert "2099" in seed.seed_one(asof, folder, str(final), "2099-01-01")[1]
    _kst_mtime(arch / asof / "payload.json", f"{asof} 23:59")
    assert seed.seed_one(asof, folder, str(final), "2000-01-01")[0] is not None, "그날 밤까지는 그날 게시본이다"
    nxt = (datetime.strptime(asof, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    for stamp in (f"{asof} 12:00", f"{nxt} 07:40"):          # 장중에 만든 것·다음날 아침에 다시 만든 것
        _kst_mtime(arch / asof / "payload.json", stamp)
        assert "저녁 게시본이 아니다" in seed.seed_one(asof, folder, str(final), "2000-01-01")[1]
    # 수집 미완 — 코스닥 수익률 가중치가 확정의 절반
    for s in prov["retw"]["코스닥"]:
        prov["retw"]["코스닥"][s][-1] = (prov["retw"]["코스닥"][s][-1] or 0.0) / 2
    one = tmp_path / "one"
    PT._write(one, "x", prov)
    _kst_mtime(one / "x" / "payload.json", f"{asof} 18:10")
    doc, why = seed.seed_one(None, str(one / "x"), str(final), "2000-01-01")
    assert doc is None and "수집 미완" in why and "코스닥" in why
    (final / asof / "VERIFY_FAILED.txt").write_text("x")
    _kst_mtime(arch / asof / "payload.json", f"{asof} 18:10")
    assert "자기 검증" in seed.seed_one(asof, folder, str(final), "2000-01-01")[1]


# ── 화면 — TUI 첫 화면과 웹 '오늘' 패널 ─────────────────────────────────────

def _numbers(P: dict) -> dict:
    Q = copy.deepcopy(P)
    Q["revision_stats"] = {"n_dates": 20, "rel_median_pct": 1.44, "flips": 3, "n_cells": 1800,
                           "top3_keep_pct": 93.46, "top3_lists": 200,
                           "stocks": {"n_dates": 18, "n_cells": 900, "rel_median_pct": 6.52, "flips": 40,
                                      "top3_keep_pct": 84.6, "top3_lists": 600},
                           "seed": {"label": sf.REV_SEED_LABEL, "dates": [f"2026-10-{d:02d}" for d in range(1, 18)]}}
    return Q, sn.build(Q)


def test_first_screen_shows_streaks_and_the_revision_line_with_top3_and_seed(P):
    from sectorflow.tui.flow_view import State, banner_lines, revision_tiers, streak_items, streak_prefix
    _Q, D = _numbers(P)
    text = "\n".join(banner_lines(State(D), 400))
    items = streak_items(D, "전체")
    assert streak_prefix("전체") + items[0] in text, text
    S = D["today"]["streaks"]
    f = S["market"]["전체"]["forgn"]
    side = "순매수" if f["sign"] > 0 else "순매도"
    assert any(i.startswith(f"외국인 {f['days']}일 연속 {side} 누적 ") for i in items) or not f["days"]
    assert any(i.startswith(f"기관 최장 {S['longest']['전체']['inst']} ") for i in items)
    assert "상위3 유지 93%" in text and f"{sf.REV_SEED_LABEL} 17일" in text, text
    assert ", 기관상위 중앙 7%·종목 상위3 유지 85%" in text, text
    assert all("시드" in t for t in revision_tiers(D)), "좁은 화면에서도 시드 출처는 남는다"
    assert "기관상위" not in revision_tiers(D)[1], "좁은 화면에서는 종목 층부터 줄인다"
    assert "연속 순매매" not in "\n".join(banner_lines(State(dict(D, today={**D["today"], "streaks": None})), 400))


def test_big_amounts_switch_to_jo_at_ten_thousand_eok():
    from sectorflow.tui.flow_view import fmt_big
    assert fmt_big(-41234.0) == "-4.1조" and fmt_big(9999.4) == "+9,999억" and fmt_big(-9999.6) == "-1.0조"
    assert fmt_big(None) == "—"


def _w_page(tmp: Path, P: dict, D: dict, patch=None) -> list[str]:
    tpl = (REPO / "scripts" / "templates" / "sector_numbers.html").read_text("utf-8")
    if patch:
        assert patch[0] in tpl
        tpl = tpl.replace(*patch)
    (tmp / "numbers.html").write_text(tpl.replace("__DATA__", json.dumps(D, ensure_ascii=False)), "utf-8")
    vt = (REPO / "scripts" / "templates" / "sector_flow.html").read_text("utf-8")
    (tmp / "viewer.html").write_text(vt.replace("__DATA__", sf.script_json(P)), "utf-8")
    vr.FAILS.clear()
    vr.layer_w(D, P, str(tmp))
    return [f for f in vr.FAILS if f.startswith("W/")]


@node
def test_web_today_panel_says_the_same_streaks_and_revision_line_as_the_terminal(P, tmp_path):
    Q, D = _numbers(P)
    assert _w_page(tmp_path, Q, D) == []


@node
@pytest.mark.parametrize("patch", [
    ('(f.sign>0?"순매수":"순매도")', '(f.sign>0?"매수":"매도")'),
    ('", 상위3 유지 "+RHU(RS.top3_keep_pct)+"%"', '""'),
])
def test_the_web_layer_catches_a_streak_or_revision_line_that_drifts_from_the_terminal(P, tmp_path, patch):
    Q, D = _numbers(P)
    assert "W/'오늘' 패널 연속 순매매·잠정 수정 폭 = TUI 첫 화면 글자" in _w_page(tmp_path, Q, D, patch)


def test_seed_with_a_missing_archive_says_so_instead_of_a_traceback(tmp_path, capsys):
    """디스크 소실 뒤에는 아카이브가 없다 — 복구 절차의 한 단계가 Traceback 으로 끝나면 안 된다."""
    spec = importlib.util.spec_from_file_location("seed_revisions_cli", REPO / "scripts" / "seed_revisions.py")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    rc = seed.main(["--archive", str(tmp_path / "없다"), "--final-root", str(tmp_path), "--out", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert rc == 1 and "아카이브 폴더가 없다" in out and "시드 0일" in out
