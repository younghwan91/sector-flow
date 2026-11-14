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
    Q["today"]["day_change"] = sf.day_change_block(Q)
    Q["today"]["concentration"] = sf.concentration_block(Q)
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
    assert f == {"sign": -1, "days": 3, "sum": -15.0, "pct": 100.0, "times": 1}
    # 길이 분포 [1,1,2,0,1,2] 에서 2 는 아래 4개·동률 2개(자기 포함) → midrank 100·(4 + 0.5)/5
    f2 = sf.streak_fact([5.0, -3.0, -4.0, -0.5, -2.0, -6.0], g[:6])
    assert f2["days"] == 2 and f2["pct"] == pytest.approx(90.0)
    assert f2["times"] == 2, "같은 방향(순매도) 연속이 길이 2 에 닿은 것은 셋째 날과 오늘 — 두 번"
    assert sf.streak_fact([5.0, 5.0, 0.2], [100.0] * 3) == {"sign": 0, "days": 0, "sum": 0.0, "pct": None, "times": None}
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
    assert S["market"]["거래소"]["inst"] == {"sign": 1, "days": 3, "sum": 46.0, "pct": 100.0, "times": 1}


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


# ── 문턱 경계 — 생산자와 검증기가 같은 규칙인가 ─────────────────────────────

def test_a_change_of_exactly_the_tolerance_is_rounding_for_producer_and_verifier(tmp_path):
    """7.86 − 7.81 = 0.05000000000000071. 날값으로 재면 0.05 칸이 실리고 반올림한 ``diff`` 를 보는 검증기가 거부했다."""
    assert abs(7.86 - 7.81) > sf.FINAL_DIFF_TOL and round(7.86 - 7.81, 2) == sf.FINAL_DIFF_TOL
    _prev, new = PT._prev_and_cur()
    asof = new["dates"][-1]
    old = json.loads(json.dumps(new))
    moved, kept, raw_over, raw_under = set(), set(), 0, 0
    steps = (0.05, -0.05, 0.06, -0.06, 0.04, 0.0)
    i = 0
    for m in old["markets"]:
        for s in old["sectors"]:
            if not sf._present(old, [m], s):
                continue
            for src, a in (("flows", "inst"), ("flows", "forgn"), ("detail", "invtrt")):
                d = steps[i % len(steps)]
                i += 1
                ser = old[src][m][s][a]
                ser[-1] = round(new[src][m][s][a][-1] - d, 2)
                raw = abs(new[src][m][s][a][-1] - ser[-1])
                if round(raw, 2) == 0.05:
                    raw_over += raw > 0.05
                    raw_under += raw <= 0.05
                (moved if abs(d) > 0.05 else kept).add((m, s, a))
    assert raw_over and raw_under, "이 입력은 부동소수 경계의 양쪽을 다 건드려야 한다"
    PT._write(tmp_path, asof, old)
    fd, why = sf.final_diff_block(new, str(tmp_path))
    assert why is None
    assert {(r["market"], r["sector"], r["actor"]) for r in fd["changed"]} == moved
    assert all(abs(r["diff"]) > sf.FINAL_DIFF_TOL for r in fd["changed"])
    assert not kept & moved
    new.update(final=True, final_diff=fd, final_diff_reason=why)
    assert _fails(new, tmp_path / f"{asof}.building.3") == []


# ── 확정 위에 다시 만들 때 — 첫 확정의 잠정 대비를 잇는다 ────────────────────

def _published_first_final(tmp_path: Path, stamp: str = "08:21"):
    """첫 확정을 게시한 뒤의 루트 — 잠정 폴더가 확정으로 바뀌어 있다. (첫 확정 P, 재시도 페이로드 Q, 빌드 폴더)."""
    P, build = _final_build(tmp_path)
    asof = P["dates"][-1]
    dest = tmp_path / asof
    _kst_mtime(dest / "payload.json", f"{asof} 18:12")
    P["final_diff"], _ = sf.final_diff_block(P, str(tmp_path))
    assert P["final_diff"]["base_built"] == f"{asof} 18:12"
    (dest / "payload.json").write_text(json.dumps(P, ensure_ascii=False), "utf-8")
    (dest / "FINAL.txt").write_text("확정\n", "utf-8")
    _kst_mtime(dest / "payload.json", f"{asof} {stamp}")
    Q = json.loads(json.dumps(P))
    for k in ("final", "final_diff", "final_diff_reason"):
        Q.pop(k, None)
    return P, Q, build


def _rebuild(Q: dict, root: Path, build: Path) -> dict:
    Q["final"] = True
    Q["final_diff"], Q["final_diff_reason"] = sf.final_diff_block(Q, str(root))
    fj = build / "final_diff.json"
    fj.unlink(missing_ok=True)
    if Q["final_diff"] is not None:
        fj.write_text(json.dumps(Q["final_diff"], ensure_ascii=False), "utf-8")
    return Q


def test_retry_over_a_final_report_inherits_the_first_finals_record(tmp_path):
    """10:20 재시도(검증 실패)·강제 재생성 — 확정끼리 견줘 '0칸' 이라 적지 않고 첫 확정의 기록을 잇는다."""
    P, Q, build = _published_first_final(tmp_path)
    asof = P["dates"][-1]
    first = P["final_diff"]
    assert first["n_changed"] >= 2
    _rebuild(Q, tmp_path, build)
    F = Q["final_diff"]
    assert Q["final_diff_reason"] is None and F["base_final"] is False
    assert F["n_changed"] == first["n_changed"] and F["changed"] == first["changed"]
    assert F["base_built"] == f"{asof} 18:12" and F["inherited_from"] == f"{asof} 08:21"
    assert F["cells_1d"] == first["cells_1d"]
    assert _fails(Q, build) == []
    # 수정 폭은 그날을 잠정 대비 그대로 센다 — 건너뛰지 않는다.
    R = sf.revision_stats(Q, str(tmp_path))
    assert asof in R["dates"] and asof not in R["skipped"]
    # 사람이 읽는 줄이 이어받았다고 적는다.
    assert f"첫 확정({asof} 08:21 KST)의 기록을 이어받았다" in sf.final_diff_text(Q)
    # 한 번 더 — 첫 확정 시각이 그대로 남는다.
    dest = tmp_path / asof
    (dest / "payload.json").write_text(json.dumps(Q, ensure_ascii=False), "utf-8")
    _kst_mtime(dest / "payload.json", f"{asof} 10:21")
    Q2 = _rebuild(json.loads(json.dumps(Q)), tmp_path, build)
    assert Q2["final_diff"] == F
    assert _fails(Q2, build) == []


def test_retry_says_no_comparison_when_the_first_final_left_no_record(tmp_path):
    P, Q, build = _published_first_final(tmp_path)
    asof = P["dates"][-1]
    dest = tmp_path / asof
    P["final_diff"], P["final_diff_reason"] = None, "재구성 리포트다"
    (dest / "payload.json").write_text(json.dumps(P, ensure_ascii=False), "utf-8")
    _rebuild(Q, tmp_path, build)
    assert Q["final_diff"] is None
    assert Q["final_diff_reason"].startswith(sf.REFINAL_WHY) and "첫 확정: 재구성 리포트다" in Q["final_diff_reason"]
    assert "비교 없음" in sf.final_diff_text(Q) and "0칸" not in sf.final_diff_text(Q)
    assert _fails(Q, build) == []
    assert not (build / "final_diff.json").exists()
    # 재시도가 거듭돼도 이유가 겹겹이 붙지 않는다.
    (dest / "payload.json").write_text(json.dumps(Q, ensure_ascii=False), "utf-8")
    why = Q["final_diff_reason"]
    Q2 = _rebuild(json.loads(json.dumps(Q)), tmp_path, build)
    assert Q2["final_diff_reason"] == why


def test_retry_does_not_inherit_a_final_to_final_record(tmp_path):
    """옛 코드가 확정끼리 견준 기록(``base_final`` 참)은 잠정 대비가 아니다 — 이으면 '0칸' 이 되살아난다."""
    P, Q, build = _published_first_final(tmp_path)
    dest = tmp_path / P["dates"][-1]
    P["final_diff"].update(base_final=True, changed=[], n_changed=0)
    (dest / "payload.json").write_text(json.dumps(P, ensure_ascii=False), "utf-8")
    _rebuild(Q, tmp_path, build)
    assert Q["final_diff"] is None and "이어받을 잠정 대비 기록도 없다" in Q["final_diff_reason"]
    assert _fails(Q, build) == []


def _fd_fails(P: dict, folder) -> list[str]:
    """잠정 대비 쪽 실패만 — 값을 손으로 옮긴 입력은 ``today`` 블록(다른 층의 재계산)과 일부러 갈린다."""
    return [f for f in _fails(P, folder) if f.startswith(("T/잠정 → 확정", "T/확정 재시도", "T/final_diff.json", "T/확정 리포트"))]


def test_retry_keeps_the_provisional_column_when_values_moved_after_the_first_final(tmp_path):
    """첫 확정 뒤 값이 또 바뀌면 잠정 열만 이어 1일 칸을 다시 센다 — 폴더를 바꿔 끼우면 잠정 열은 이 기록에만 남는다."""
    P, Q, build = _published_first_final(tmp_path)
    asof = P["dates"][-1]
    first = P["final_diff"]
    ser = Q["flows"]["코스닥"]["금융"]["inst"]
    ser[-1] = round(ser[-1] + 1.0, 2)
    _rebuild(Q, tmp_path, build)
    F = Q["final_diff"]
    assert Q["final_diff_reason"] is None and F["rebased"] is True and F["windows"] == [1]
    assert F["base_final"] is False and F["base_built"] == first["base_built"] and F["inherited_from"] == f"{asof} 08:21"
    assert F["n_cells"] == len(F["cells_1d"]) and all(r["win"] == 1 for r in F["changed"])
    was = {tuple(c[:3]): c for c in first["cells_1d"]}
    for c in F["cells_1d"]:
        assert c[3] == was[tuple(c[:3])][3], "잠정 열은 첫 확정의 것 그대로다"
    now = {tuple(c[:3]): c[4] for c in F["cells_1d"]}
    assert now[("코스닥", "금융", "inst")] == ser[-1] != was[("코스닥", "금융", "inst")][4]
    assert F["extremes_change"] == first["extremes_change"], "극단 목록은 안 움직였다 — 잠정 대비 들고 남도 그대로다"
    assert _fd_fails(Q, build) == []
    # 수정 폭에 남는다.
    R = sf.revision_stats(Q, str(tmp_path))
    assert asof in R["dates"] and asof not in R["skipped"]
    assert "1일 칸만 다시 셌다" in sf.final_diff_text(Q) and "순매수[억] 1일," in sf.final_diff_text(Q)
    # 한 번 더 — 값이 그대로면 그대로 잇고, 또 바뀌면 같은 잠정 열로 다시 센다.
    dest = tmp_path / asof
    (dest / "payload.json").write_text(json.dumps(Q, ensure_ascii=False), "utf-8")
    Q2 = _rebuild(json.loads(json.dumps(Q)), tmp_path, build)
    assert Q2["final_diff"] == F and _fd_fails(Q2, build) == []
    Q3 = json.loads(json.dumps(Q))
    Q3["flows"]["코스닥"]["금융"]["inst"][-1] += 1.0
    Q3 = _rebuild(Q3, tmp_path, build)
    assert Q3["final_diff"]["rebased"] and _fd_fails(Q3, build) == []
    assert {tuple(c[:3]): c[3] for c in Q3["final_diff"]["cells_1d"]} == {k: v[3] for k, v in was.items()}


def test_retry_rebases_the_extreme_moves_on_the_provisional_lists(tmp_path):
    """극단 목록만 움직인 경우 — 머리 칸(1·5일)이 같아도 들고 남은 잠정 목록에 다시 견준다."""
    P, Q, build = _published_first_final(tmp_path)
    ser = Q["flows"]["거래소"]["반도체"]["inst"]
    was_out = (P["final_diff"]["extremes_change"].get("거래소") or {}).get("inst", {}).get("hi_out")
    assert was_out == ["반도체"], "입력: 잠정에서는 1년 최고였다가 확정에서 나간 섹터"
    ser[:-5] = [min(ser) - 100.0 - i for i in range(len(ser) - 5)]    # 5일 창 밖 — 오늘 값의 1년 순위만 바뀐다(다시 최상단)
    assert sf._head_cells(Q) == sf._head_cells(P)
    _rebuild(Q, tmp_path, build)
    F = Q["final_diff"]
    assert F["rebased"] is True
    assert "반도체" not in ((F["extremes_change"].get("거래소") or {}).get("inst") or {}).get("hi_out", []), \
        "잠정에서도 지금도 ≥95 다 — 나간 것이 아니다"
    assert _fd_fails(Q, build) == []


def _rr_rebased_cells(Q, b):
    Q["final_diff"]["cells_1d"][0][3] = (Q["final_diff"]["cells_1d"][0][3] or 0.0) + 5.0


def _rr_rebased_ext(Q, b):
    Q["final_diff"]["extremes_change"] = {"거래소": {"inst": {"hi_in": ["없는 섹터"]}}}


def _rr_rebased_base(Q, b):
    Q["final_diff"]["base_built"] = "2026-01-01 00:00"


def _rr_rebased_windows(Q, b):
    Q["final_diff"]["windows"] = [1, 5]


def _rr_rebased_flag(Q, b):
    Q["final_diff"].pop("rebased")


def _rr_rebased_stock_layer(Q, b):
    """낡은 종목 층을 들고 간 재시도 — 이 입력의 종목 칸은 그대로라 행은 페이로드와 맞는다. 머리 검사만 잡을 수 있다."""
    Q["final_diff"]["stocks"] = json.loads((b.parent / Q["dates"][-1] / "payload.json").read_text("utf-8"))["final_diff"]["stocks"]


@pytest.mark.parametrize("corrupt, want", [
    (_rr_rebased_cells, "T/" + vr.VT_CHECK_RETRY_RECORD),
    (_rr_rebased_ext, "T/" + vr.VT_CHECK_RETRY_RECORD),
    (_rr_rebased_base, "T/" + vr.VT_CHECK_RETRY_RECORD),
    (_rr_rebased_windows, "T/" + vr.VT_CHECK_RETRY_RECORD),
    (_rr_rebased_flag, "T/" + vr.VT_CHECK_RETRY_KIND),
    (_rr_rebased_stock_layer, "T/" + vr.VT_CHECK_RETRY_RECORD),
])
def test_the_verifier_recounts_a_rebased_retry_from_the_first_finals_column(tmp_path, corrupt, want):
    _P, Q, build = _published_first_final(tmp_path)
    Q["flows"]["코스닥"]["금융"]["inst"][-1] += 1.0
    _rebuild(Q, tmp_path, build)
    assert _fd_fails(Q, build) == []
    corrupt(Q, build)
    assert want in _fails(Q, build)


# ── 재시도의 종목(기관상위) 층 — 잠정 칸은 첫 확정 때 사라지고 문턱·상한을 넘은 행에만 남는다 ──────

def _a_driver_row(P: dict) -> str:
    return P["final_diff"]["stocks"]["rows"][0][2]


def test_retry_with_the_same_values_keeps_the_stock_layer_and_its_day(tmp_path):
    P, Q, build = _published_first_final(tmp_path)
    first = P["final_diff"]["stocks"]
    assert first and first["n_rows"] >= 1
    _rebuild(Q, tmp_path, build)
    assert Q["final_diff"]["stocks"] == first
    assert _fails(Q, build) == []
    R = sf.revision_stats(Q, str(tmp_path))["stocks"]
    assert R["n_dates"] == 1 and R["n_cells"] == first["n_cells"]


def test_retry_with_moved_values_drops_the_stock_layer_it_can_no_longer_recount(tmp_path):
    """확정 열이 낡고 잠정 열은 일부 행에만 남아 다시 못 센다 — 섹터 칸은 잠정 열로 다시 세되 종목 층은 비운다(거짓 수정 폭 없음)."""
    P, Q, build = _published_first_final(tmp_path)
    asof = P["dates"][-1]
    assert P["final_diff"]["stocks"]
    Q["flows"]["코스닥"]["금융"]["inst"][-1] += 1.0
    _rebuild(Q, tmp_path, build)
    F = Q["final_diff"]
    assert F["rebased"] is True and F["stocks"] is None
    assert _fd_fails(Q, build) == []
    R = sf.revision_stats(Q, str(tmp_path))
    assert asof in R["dates"] and R["n_dates"] == 1, "섹터 층의 그날은 남는다"
    assert R["stocks"] is None, "종목 층엔 그날을 세지 않는다"
    # 재시도가 거듭돼도 되살아나지 않는다 — 값이 그대로든 또 바뀌든.
    dest = tmp_path / asof
    (dest / "payload.json").write_text(json.dumps(Q, ensure_ascii=False), "utf-8")
    Q2 = _rebuild(json.loads(json.dumps(Q)), tmp_path, build)
    assert Q2["final_diff"] == F and _fd_fails(Q2, build) == []
    Q3 = json.loads(json.dumps(Q))
    Q3["flows"]["코스닥"]["금융"]["inst"][-1] += 1.0
    Q3 = _rebuild(Q3, tmp_path, build)
    assert Q3["final_diff"]["rebased"] and Q3["final_diff"]["stocks"] is None and _fd_fails(Q3, build) == []


def _sm_cell(Q, code):
    Q["names"][code]["win"]["1"]["inst"] = round(Q["names"][code]["win"]["1"]["inst"] + 5.0, 1)


def _sm_other_actor(Q, code):
    Q["names"][code]["win"]["1"]["forgn"] = round((Q["names"][code]["win"]["1"].get("forgn") or 0.0) + 5.0, 1)


def _sm_cell_gone(Q, code):
    Q["names"][code]["win"].pop("1")


def _sm_new_member(Q, code):
    """그날 칸 없는 새 종목 — 구성이 갈리면 그 (시장, 섹터)는 stock_diff 가 통째로 뺀다."""
    r = Q["names"][code]
    Q["names"]["999999"] = {"market": r["market"], "sector": r["sector"], "name": "새 종목", "win": {}}


@pytest.mark.parametrize("move", [_sm_cell, _sm_other_actor, _sm_cell_gone, _sm_new_member])
def test_retry_does_not_inherit_a_stock_layer_when_only_the_stocks_moved(tmp_path, move):
    """섹터 칸·극단이 그대로여도 종목 칸이 움직였으면 기록의 확정 열이 낡았다 — 그대로 잇는 것은 거짓이다."""
    P, Q, build = _published_first_final(tmp_path)
    move(Q, _a_driver_row(P))
    assert sf._head_cells(Q) == sf._head_cells(P) and not sf.extremes_change(Q, P), "입력: 섹터 칸은 그대로"
    _rebuild(Q, tmp_path, build)
    F = Q["final_diff"]
    assert F["rebased"] is True and F["stocks"] is None and F["windows"] == [1]
    assert _fd_fails(Q, build) == []


def test_the_verifier_rejects_a_stock_layer_inherited_over_moved_stocks(tmp_path):
    """생산자가 규칙을 어겨 그대로 이어도(섹터 칸은 같다) 검증기가 따로 잡는다."""
    P, Q, build = _published_first_final(tmp_path)
    _rebuild(Q, tmp_path, build)
    assert _fd_fails(Q, build) == []
    # 행에 없는 종목 칸만 움직인다 — 기록의 행은 페이로드와 맞아 자기모순 검사로는 안 보인다.
    in_rows = {r[2] for r in P["final_diff"]["stocks"]["rows"]}
    other = next(c for c, r in Q["names"].items() if c not in in_rows and (r.get("win") or {}).get("1"))
    Q["names"][other]["win"]["1"]["inst"] = round(Q["names"][other]["win"]["1"]["inst"] + 5.0, 1)
    assert "T/" + vr.VT_CHECK_RETRY_RECORD in _fails(Q, build)


def test_retry_over_a_record_without_a_stock_layer_is_not_rebased_for_moved_stocks(tmp_path):
    """옛 코드의 첫 확정(종목 층 자체가 없다) — 말한 적 없는 층이 움직였다고 섹터 기록(5일 칸)을 버리지 않는다."""
    P, Q, build = _published_first_final(tmp_path)
    code = _a_driver_row(P)
    del P["final_diff"]["stocks"]
    dest = tmp_path / P["dates"][-1]
    (dest / "payload.json").write_text(json.dumps(P, ensure_ascii=False), "utf-8")
    _sm_cell(Q, code)
    _rebuild(Q, tmp_path, build)
    F = Q["final_diff"]
    assert "rebased" not in F and "stocks" not in F and F["windows"] == list(sf.FINAL_DIFF_WINDOWS)
    assert _fd_fails(Q, build) == []


def test_a_first_final_that_failed_its_stock_recount_is_not_inherited(tmp_path):
    """종목 재계산만 걸린 첫 확정 — 섹터 칸이 멀쩡해도 잇지 않는다(틀린 종목 층을 세탁하지 않는다)."""
    name = "잠정 → 확정 — 종목(기관상위) 행·요약을 옆 잠정 리포트에서 다시 센 것과 같은가"
    text = OLD_CODE_FAILED.format(n=0).replace("[FAIL] T · 잠정 → 확정 — 확정값·차이", "[OK  ] T · 잠정 → 확정 — 확정값·차이")
    text += f"  [FAIL] T · {name}   1건  x\n"
    P, Q, build = _first_with_marker(tmp_path, text)
    _rebuild(Q, tmp_path, build)
    assert Q["final_diff"] is None and name in Q["final_diff_reason"]
    assert _fails(Q, build) == []


# ── 검증에 걸린 첫 확정 — 거짓 실패는 잇고, 진짜 실패는 잇지 않는다 ─────────────
#
# 아래 줄은 고치기 전 코드(a2f31f3)가 실제 11-06 사본에 남긴 VERIFY_FAILED.txt 그대로다(건수만 {n}).
OLD_CODE_FAILED = """  [OK  ] T · 확정 리포트에 잠정 대비(final_diff 또는 이유)가 있는가
  [FAIL] T · 잠정 → 확정 — 확정값·차이가 페이로드에서 다시 나오는가   {n}건  거래소/오락/문화/invtrt/1 diff 0.05; 거래소/오락/문화/invtrt/5 diff 0.05
  [OK  ] T · 잠정 → 확정 — 견준 잠정 리포트가 옆 폴더에 있고 지문이 같은가   지문 859e24f0433c→859e24f0433c
  [OK  ] T · 잠정 → 확정 — 잠정값·차이·칸 수를 옆 잠정 리포트에서 다시 센 것과 같은가   0건\x20\x20
  [OK  ] T · 잠정 → 확정 — %ile 극단 목록의 들고 남 재계산   {{'코스닥': {{'indiv': {{'hi_in': [], 'hi_out': [], 'lo_in': ['IT 서비스'], 'lo_out': []}}}}}} vs {{'코스닥': {{'indiv': {{'hi_in': [], 'hi_out': [], 'lo_in': ['IT 서비스'], 'lo_out': []}}}}}}
  [OK  ] T · 잠정 → 확정 — 견준 리포트의 지위·생성 시각 = 옆 폴더의 것   False/2026-11-06 18:12 vs False/2026-11-06 18:12
  [OK  ] T · final_diff.json(다음 리포트들의 수정 폭 원천) = 페이로드의 final_diff   /x/final_diff.json

검사 179건 · 실패 1건 · 문서 주장 어긋남 0건
실패 목록: T/잠정 → 확정 — 확정값·차이가 페이로드에서 다시 나오는가
"""


def _with_old_edge_rows(P: dict, dest: Path, n: int = 2) -> None:
    """옛 규칙이 실은 경계 칸 — 1일 칸 하나의 1·5일 값이 문턱만큼 움직였다."""
    fd = P["final_diff"]
    cell = next(c for c in fd["cells_1d"] if c[:3] == ["거래소", "화학", "inst"])
    ser = P["flows"]["거래소"]["화학"]["inst"]
    assert n in (1, 2)
    cell[3] = round(cell[4] - 0.05, 2)
    for w in (1, 5)[:n]:
        v = round(sum(ser[-w:]), 2)
        fd["changed"].append({"market": "거래소", "sector": "화학", "actor": "inst", "win": w,
                              "old": round(v - 0.05, 2), "new": v, "diff": 0.05})
    fd["n_changed"] += n
    for t in fd["market_totals"]:
        if (t["market"], t["actor"]) == ("거래소", "inst") and t["old"] is not None:
            t["old"] = round(t["old"] - 0.05, 2)
            t["diff"] = round(t["new"] - t["old"], 2)
    (dest / "payload.json").write_text(json.dumps(P, ensure_ascii=False), "utf-8")


def _first_with_marker(tmp_path, text: str, edges: int = 0):
    P, Q, build = _published_first_final(tmp_path)
    dest = tmp_path / P["dates"][-1]
    if edges:
        _with_old_edge_rows(P, dest, edges)
    (dest / "VERIFY_FAILED.txt").write_text(text, "utf-8")
    _kst_mtime(dest / "payload.json", f"{P['dates'][-1]} 08:21")
    return P, Q, build


def test_the_old_codes_edge_failure_is_still_inherited(tmp_path):
    """11-10 08:20 이 옛 코드로 돌 수 있다 — 그 VERIFY_FAILED 는 값이 틀렸다는 뜻이 아니다. 경계 칸을 걸러 잇는다."""
    P, Q, build = _first_with_marker(tmp_path, OLD_CODE_FAILED.format(n=2), edges=2)
    want = P["final_diff"]["n_changed"] - 2
    _rebuild(Q, tmp_path, build)
    assert Q["final_diff_reason"] is None, Q["final_diff_reason"]
    assert Q["final_diff"]["n_changed"] == want and "rebased" not in Q["final_diff"]
    assert not [r for r in Q["final_diff"]["changed"] if abs(r["diff"]) <= 0.05]
    assert _fails(Q, build) == []


@pytest.mark.parametrize("label, text, edges", [
    # 표지만 있고 증거가 없다 — 손으로 만든 파일·검증이 도중에 죽은 파일.
    ("빈 파일", "", 0),
    ("검사 줄 없음", "Traceback (most recent call last):\n", 0),
    # 진짜 잠정과 다시 세어 틀렸다.
    ("재계산 실패", OLD_CODE_FAILED.format(n=2).replace(
        "[OK  ] T · 잠정 → 확정 — 잠정값·차이·칸 수", "[FAIL] T · 잠정 → 확정 — 잠정값·차이·칸 수"), 2),
    ("극단 재계산 실패", OLD_CODE_FAILED.format(n=2).replace(
        "[OK  ] T · 잠정 → 확정 — %ile", "[FAIL] T · 잠정 → 확정 — %ile"), 2),
    ("지위·시각 실패", OLD_CODE_FAILED.format(n=2).replace(
        "[OK  ] T · 잠정 → 확정 — 견준 리포트", "[FAIL] T · 잠정 → 확정 — 견준 리포트"), 2),
    # 경계 칸 말고 다른 칸도 걸렸다 — 건수가 경계 칸 수보다 많다.
    ("경계 칸보다 많이 걸림", OLD_CODE_FAILED.format(n=3), 2),
    ("경계 칸보다 적게 걸림", OLD_CODE_FAILED.format(n=1), 2),
    # 경계 칸이 없는데 그 검사가 걸렸다 — 거짓 실패일 수 없다.
    ("경계 칸 없음", OLD_CODE_FAILED.format(n=2), 0),
    ("건수 0", OLD_CODE_FAILED.format(n=0), 0),
    ("final_diff.json 실패", OLD_CODE_FAILED.format(n=2).replace(
        "[OK  ] T · final_diff.json", "[FAIL] T · final_diff.json"), 2),
])
def test_a_first_final_that_failed_its_recount_is_not_inherited(tmp_path, label, text, edges):
    """재시도가 틀린 기록을 세탁하면 안 된다 — 검증 실패를 지우고(rc=0) 수정 폭까지 오염시킨다."""
    P, Q, build = _first_with_marker(tmp_path, text, edges)
    assert sf._failed_record_why(str(tmp_path / P["dates"][-1]), P["final_diff"]), label
    assert vr._vt_failed_record_why(str(tmp_path / P["dates"][-1]), P["final_diff"]), label
    _rebuild(Q, tmp_path, build)
    assert Q["final_diff"] is None and Q["final_diff_reason"].startswith(sf.REFINAL_WHY)
    assert "검증에 걸려" in Q["final_diff_reason"] and "비교 없음" in sf.final_diff_text(Q)
    assert not (build / "final_diff.json").exists()
    assert _fails(Q, build) == [], "비교 없음이 맞는 자리다 — 검증기가 비었다고 거부하면 재시도가 영영 걸린다"
    assert P["dates"][-1] not in sf.revision_stats(Q, str(tmp_path))["dates"]


def test_the_verifier_rejects_a_retry_that_inherited_a_failed_record(tmp_path):
    """생산자가 규칙을 어겨 이어도 검증기가 따로 잡는다."""
    P, Q, build = _published_first_final(tmp_path)
    _rebuild(Q, tmp_path, build)
    assert _fails(Q, build) == []
    (tmp_path / P["dates"][-1] / "VERIFY_FAILED.txt").write_text("", "utf-8")
    assert "T/" + vr.VT_CHECK_RETRY_TRUST in _fails(Q, build)


def test_an_unrelated_failure_does_not_block_the_inheritance(tmp_path):
    """다른 층의 실패(문서·연속 순매매 등)는 잠정 대비 기록과 상관없다 — 재계산 OK 줄이 있으면 잇는다."""
    text = OLD_CODE_FAILED.format(n=0).replace("[FAIL] T · 잠정 → 확정 — 확정값·차이", "[OK  ] T · 잠정 → 확정 — 확정값·차이")
    text += "  [FAIL] T · 연속 순매매 — 섹터(얇은 섹터·미분류 제외)의 연속 재계산   1건  x\n"
    P, Q, build = _first_with_marker(tmp_path, text)
    _rebuild(Q, tmp_path, build)
    assert Q["final_diff"]["n_changed"] == P["final_diff"]["n_changed"] and _fails(Q, build) == []


def test_a_retry_of_an_inherited_record_may_show_the_retry_evidence(tmp_path):
    """이어받은 재시도가 다른 이유로 걸렸으면 재계산 증거는 '확정 재시도' 줄이다(첫 확정 줄은 이미 없다)."""
    P, Q, build = _published_first_final(tmp_path)
    _rebuild(Q, tmp_path, build)
    dest = tmp_path / P["dates"][-1]
    (dest / "payload.json").write_text(json.dumps(Q, ensure_ascii=False), "utf-8")
    text = ("  [OK  ] T · 확정 리포트에 잠정 대비(final_diff 또는 이유)가 있는가\n"
            f"  [OK  ] T · {sf.FD_CHECKS_RETRY[0]}   0건  \n"
            f"  [OK  ] T · {sf.FD_CHECKS_RETRY[1]}   2026-06-26 08:21 vs 2026-06-26 08:21\n"
            "  [FAIL] F · 어떤 다른 층\n")
    (dest / "VERIFY_FAILED.txt").write_text(text, "utf-8")
    Q2 = _rebuild(json.loads(json.dumps(Q)), tmp_path, build)
    assert Q2["final_diff"] == Q["final_diff"] and _fails(Q2, build) == []


def test_producer_and_verifier_read_the_names_the_verifier_really_prints(tmp_path, capsys):
    """검사 이름을 바꾸면 규칙이 조용히 '증거 없음' 으로 돈다 — 실제 출력으로 묶는다."""
    assert sf.FD_CHECK_VALUES == vr.VT_CHECK_VALUES
    assert set(sf.FD_CHECKS_RECOUNT) == {vr.VT_CHECK_RECOUNT, vr.VT_CHECK_EXT, vr.VT_CHECK_BASE}
    assert set(sf.FD_CHECKS_RETRY) == {vr.VT_CHECK_RETRY_RECORD, vr.VT_CHECK_RETRY_FIRST}
    P, build = _final_build(tmp_path)
    assert _fails(P, build) == []
    out = capsys.readouterr().out                    # 첫 확정의 실제 검증 출력
    marker = build / "VERIFY_FAILED.txt"
    marker.write_text(out, "utf-8")
    assert sf._failed_record_why(str(build), P["final_diff"]) is None
    assert vr._vt_failed_record_why(str(build), P["final_diff"]) is None
    marker.unlink()
    (tmp_path / "r").mkdir()
    _P, Q, b2 = _published_first_final(tmp_path / "r")
    _rebuild(Q, tmp_path / "r", b2)
    capsys.readouterr()
    assert _fails(Q, b2) == []
    (b2 / "VERIFY_FAILED.txt").write_text(capsys.readouterr().out, "utf-8")
    assert sf._failed_record_why(str(b2), Q["final_diff"]) is None
    assert vr._vt_failed_record_why(str(b2), Q["final_diff"]) is None


# ── 기록의 자체 일관성 — 잠정이 없어도 보이는 어긋남 ─────────────────────────

def _sc_cell_old(F):
    next(c for c in F["cells_1d"] if c[3] is not None)[3] += 5000.0


def _sc_cell_new(F):
    F["cells_1d"][0][4] += 1.0


def _sc_cell_drop(F):
    F["cells_1d"].pop()


def _sc_changed_old(F):
    next(r for r in F["changed"] if r["win"] == 1)["old"] += 1.0


def _sc_changed_drop(F):
    F["changed"].remove(next(r for r in F["changed"] if r["win"] == 1))
    F["n_changed"] -= 1


def _sc_totals_old(F):
    F["market_totals"][0]["old"] += 777.0


def _sc_totals_old_5d(F):
    next(t for t in F["market_totals"] if t["win"] == 5)["old"] += 777.0


def _sc_totals_diff(F):
    F["market_totals"][0]["diff"] += 1.0


def _sc_ext_in(F):
    F["extremes_change"] = {"거래소": {"inst": {"hi_in": ["금융"]}}}


def _sc_ext_lo(F):
    F["extremes_change"] = {"거래소": {"inst": {"lo_in": ["금융"]}}}


def _sc_n_cells(F):
    F["n_cells"] += 1


def _sc_totals_old_and_diff(F):
    """합계의 잠정과 차이를 함께 고쳐 차이 검산을 통과시킨다 — 칸에서 다시 세는 쪽만 잡는다."""
    t = F["market_totals"][0]
    t["old"] += 777.0
    t["diff"] -= 777.0


def _sc_changed_old_and_diff(F):
    r = next(r for r in F["changed"] if r["win"] == 1)
    r["old"] -= 300.0
    r["diff"] += 300.0


def _sc_changed_new_5d(F):
    r = next(r for r in F["changed"] if r["win"] == 5)
    r["new"] += 300.0
    r["diff"] += 300.0


def _sc_changed_diff_5d(F):
    next(r for r in F["changed"] if r["win"] == 5)["diff"] += 1.0


def _sc_stock_new(F):
    """종목 행의 확정 열이 그 기록을 낸 페이로드와 다르다 — 잠정 열이 틀린 것은 잠정 없이 안 보이지만 이건 보인다."""
    F["stocks"]["rows"][0][4]["inst"][1] += 300.0


def _sc_stock_rows_dropped(F):
    """행을 지웠는데 전체 행 수는 그대로다."""
    F["stocks"]["rows"].pop()


SELF_BREAKS = [_sc_cell_old, _sc_cell_new, _sc_cell_drop, _sc_changed_old, _sc_changed_drop, _sc_totals_old,
               _sc_totals_old_5d, _sc_totals_diff, _sc_ext_in, _sc_ext_lo, _sc_n_cells,
               _sc_totals_old_and_diff, _sc_changed_old_and_diff, _sc_changed_new_5d, _sc_changed_diff_5d,
               _sc_stock_new, _sc_stock_rows_dropped]


@pytest.mark.parametrize("corrupt", SELF_BREAKS)
def test_a_record_that_contradicts_itself_is_not_inherited(tmp_path, corrupt):
    """VERIFY_FAILED 가 없어도(강제 재생성) 어긋난 기록은 잇지 않고, 검증기도 따로 본다."""
    P, Q, build = _published_first_final(tmp_path)
    assert sf._record_mismatch(P, P["final_diff"]) is None and vr._vt_fd_self(P, P["final_diff"]) == []
    corrupt(P["final_diff"])
    assert sf._record_mismatch(P, P["final_diff"]), "생산자"
    assert vr._vt_fd_self(P, P["final_diff"]), "검증기"
    dest = tmp_path / P["dates"][-1]
    (dest / "payload.json").write_text(json.dumps(P, ensure_ascii=False), "utf-8")
    _rebuild(Q, tmp_path, build)
    assert Q["final_diff"] is None and "기록이 어긋나" in Q["final_diff_reason"]
    assert _fails(Q, build) == []


def test_the_review_tamper_is_not_laundered(tmp_path):
    """리뷰 재현 — 잠정값 +5000·가짜 들고 남·합계 변조 + 표지 뒤 재시도(rc=0, 수정 폭 오염)."""
    P, Q, build = _published_first_final(tmp_path)
    F = P["final_diff"]
    _sc_cell_old(F)
    _sc_ext_in(F)
    _sc_totals_old(F)
    dest = tmp_path / P["dates"][-1]
    (dest / "payload.json").write_text(json.dumps(P, ensure_ascii=False), "utf-8")
    for marker in (None, "", OLD_CODE_FAILED.format(n=2)):       # 옛 코드의 실패 글을 그대로 가져다 둬도
        if marker is not None:
            (dest / "VERIFY_FAILED.txt").write_text(marker, "utf-8")
        R = _rebuild(json.loads(json.dumps(Q)), tmp_path, build)
        assert R["final_diff"] is None and R["final_diff_reason"].startswith(sf.REFINAL_WHY)
        assert _fails(R, build) == []


@pytest.mark.parametrize("corrupt", [_sc_cell_old, _sc_totals_old_and_diff, _sc_ext_in])
def test_the_verifier_reports_a_self_contradicting_record_on_a_first_final(tmp_path, corrupt):
    P, build = _final_build(tmp_path)
    corrupt(P["final_diff"])
    (build / "final_diff.json").write_text(json.dumps(P["final_diff"], ensure_ascii=False), "utf-8")
    assert "T/잠정 → 확정 — changed·cells_1d·시장 합계·극단 들고 남이 서로·페이로드와 맞는가" in _fails(P, build)


def test_every_real_first_final_passes_its_own_consistency_check(tmp_path):
    """새 검사가 정상 기록을 거부하면 무인 확정이 멈춘다."""
    P, build = _final_build(tmp_path)
    assert sf._record_mismatch(P, P["final_diff"]) is None
    assert vr._vt_fd_self(P, P["final_diff"]) == []


# ── %ile 극단 경계 — 들고 남이 화면 목록과 같은 규칙인가 ───────────────────────

def _pct_payload(values: list[float]) -> dict:
    n = len(values)
    return {"dates": [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)], "markets": ["거래소"], "sectors": ["화학"],
            "n_by_sector": {"거래소": {"화학": 5}},
            "flows": {"거래소": {"화학": {"inst": values, "forgn": [1.0] * n, "indiv": [1.0] * n, "etc": [1.0] * n}}},
            "detail": {"거래소": {"화학": {"invtrt": [1.0] * n, "penfnd_etc": [1.0] * n}}}}


@pytest.mark.parametrize("rank, want_hi, want_lo", [
    (189.0, True, False),      # 189/199 = 94.97 — 화면 95.0
    (188.0, False, False),     # 188/199 = 94.47
    (10.0, False, True),       # 10/199 = 5.03 — 화면 5.0
    (11.0, False, False),      # 11/199 = 5.53
])
def test_extreme_lists_round_like_the_screen(rank, want_hi, want_lo):
    """화면은 반올림한 pct 로 ≥95 를 고른다(94.97 → 95.0). 날값으로 재면 들고 남이 화면 목록과 갈린다(28일 57칸)."""
    values = [float(i) for i in range(200) if i != int(rank)] + [rank]
    P = _pct_payload(values)
    raw = sf.day_pct(sf._series(P, ["거래소"], "화학", "inst"))
    assert raw == pytest.approx(100.0 * rank / 199)
    hi, lo = sf._extremes(P, ["거래소"], "inst")
    vhi, vlo = vr._vt_extremes(P, ["거래소"], "inst")
    assert (("화학" in hi), ("화학" in lo)) == (want_hi, want_lo)
    assert (hi, lo) == (vhi, vlo)
    from sectorflow.tui import flow_view as FV
    d = {"today": {"sectors": {"거래소": {"화학": {"inst": sf._fact(P["flows"]["거래소"]["화학"]["inst"])}}}}}
    shi, slo = FV.today_extremes(d, "거래소", "inst")
    assert (bool(shi), bool(slo)) == (want_hi, want_lo), "화면 목록과 같아야 한다"


def test_retry_drops_boundary_rows_left_by_the_old_rule(tmp_path):
    """고치기 전 코드가 쓴 첫 확정(문턱과 같은 칸 포함) 위의 재시도가 영영 검증에 걸리면 안 된다."""
    P, Q, build = _published_first_final(tmp_path)
    dest = tmp_path / P["dates"][-1]
    want = P["final_diff"]["n_changed"]
    ser = P["flows"]["거래소"]["화학"]["inst"]
    P["final_diff"]["changed"].append({"market": "거래소", "sector": "화학", "actor": "inst", "win": 1,
                                       "old": round(ser[-1] - 0.05, 2), "new": ser[-1], "diff": 0.05})
    P["final_diff"]["n_changed"] += 1
    (dest / "payload.json").write_text(json.dumps(P, ensure_ascii=False), "utf-8")
    _rebuild(Q, tmp_path, build)
    assert Q["final_diff"]["n_changed"] == want
    assert _fails(Q, build) == []


def _rr_zero(Q, b):
    """고치기 전 동작 — 확정끼리 견줘 0칸."""
    Q["final_diff"].update(base_final=True, changed=[], n_changed=0)
    Q["final_diff"].pop("inherited_from")


def _rr_cells(Q, b):
    Q["final_diff"]["cells_1d"][0][3] = (Q["final_diff"]["cells_1d"][0][3] or 0.0) + 5.0


def _rr_count(Q, b):
    Q["final_diff"]["changed"].pop()
    Q["final_diff"]["n_changed"] -= 1


def _rr_first_time(Q, b):
    Q["final_diff"]["inherited_from"] = "2026-01-01 00:00"


def _rr_values(Q, b):
    Q["flows"]["코스닥"]["금융"]["inst"][-1] = round(Q["flows"]["코스닥"]["금융"]["inst"][-1] + 0.01, 2)


def _rr_json(Q, b):
    (b / "final_diff.json").write_text(json.dumps({"cells_1d": []}), "utf-8")


def _rr_none(Q, b):
    Q["final_diff"], Q["final_diff_reason"] = None, "어쩌다"
    (b / "final_diff.json").unlink()


@pytest.mark.parametrize("corrupt, want", [
    (_rr_zero, "T/" + vr.VT_CHECK_RETRY_RECORD),
    (_rr_cells, "T/" + vr.VT_CHECK_RETRY_RECORD),
    (_rr_count, "T/" + vr.VT_CHECK_RETRY_RECORD),
    (_rr_first_time, "T/" + vr.VT_CHECK_RETRY_FIRST),
    (_rr_values, "T/" + vr.VT_CHECK_RETRY_KIND),
    (_rr_json, "T/final_diff.json(다음 리포트들의 수정 폭 원천) = 페이로드의 final_diff"),
    (_rr_none, "T/잠정 → 확정 — 바꿔 끼울 잠정 리포트가 옆에 있는데 비교가 비지 않았는가(덮어쓰기 지문이 같을 때)"),
])
def test_the_verifier_checks_a_retry_against_the_first_final_next_door(tmp_path, corrupt, want):
    _P, Q, build = _published_first_final(tmp_path)
    _rebuild(Q, tmp_path, build)
    assert _fails(Q, build) == []
    corrupt(Q, build)
    assert want in _fails(Q, build)


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


def test_stock_drivers_stop_at_exactly_the_cut_of_the_sum():
    """주도주 정의를 손으로 센 값과 — 누적이 80% 에 **닿으면** 멈추고(50+30), 79.6% 면 한 종목 더 간다."""
    def w1(**inst):
        return {c: ("거래소", "보험", c, {"inst": v}) for c, v in inst.items()}
    assert sf._stock_drivers(w1(A=50.0, B=30.0, C=15.0, D=5.0)) == {"A", "B"}
    assert sf._stock_drivers(w1(A=49.6, B=30.0, C=15.4, D=5.0)) == {"A", "B", "C"}
    assert sf._stock_drivers(w1(A=0.0, B=0.0)) == set()


def test_stock_top3_ranks_a_missing_value_last():
    """1일 값이 없는 종목은 순위 맨 뒤(0) — 값을 메우면 없는 종목이 상위 3 에 든다."""
    def w1(**inst):
        return {c: ("거래소", "보험", c, dict.fromkeys(sf.STOCK_DIFF_ACTORS, 0.0) | {"inst": v}) for c, v in inst.items()}
    new = w1(A=None, B=0.5, C=0.4, D=0.3, E=-0.1, F=-0.2)
    old = w1(A=0.1, B=0.5, C=0.4, D=0.3, E=-0.1, F=-0.2)
    kept, lists = sf._stock_top3(new, old, set(), 3)
    # inst: {B,C,D} 대 {B,C,D} = 3. 나머지 세 주체는 전부 0 → 코드순 {A,B,C} 끼리 = 3.
    assert (kept, lists) == (3 * len(sf.STOCK_DIFF_ACTORS), len(sf.STOCK_DIFF_ACTORS))


def test_stock_diff_counts_a_cell_exactly_at_the_gate_and_no_flip_from_zero():
    """분모 게이트 경계와 뒤집힘의 정의를 손으로 — 합의 2% 에 **딱** 걸린 칸은 들어가고, 0 → 값은 뒤집힘이 아니다."""
    fin = _stk_payload({"A": ("거래소", "보험", 50.0, 0.0, 0.0, 2.0), "B": ("거래소", "보험", 30.0, 0.0, 5.0, 98.0),
                        "C": ("거래소", "보험", 15.0, 0.0, 0.0, 0.0), "D": ("거래소", "보험", 5.0, 0.0, 0.0, 0.0)})
    prov = _stk_payload({"A": ("거래소", "보험", 50.0, 0.0, 0.0, 2.0), "B": ("거래소", "보험", 30.0, 0.0, 0.0, 98.0),
                         "C": ("거래소", "보험", 15.0, 0.0, 0.0, 0.0), "D": ("거래소", "보험", 5.0, 0.0, 0.0, 0.0)})
    S = sf.stock_diff(fin, prov)
    assert S["n_stocks"] == 2 and S["flips"] == 0
    assert S["n_cells"] == 5, "기관 2 + 연기금 B + 외국인 A(100×2% 에 딱)·B"


def test_revision_rollup_counts_empty_stock_summaries_as_zero(tmp_path):
    _seed_file(tmp_path, "2026-11-02", _top_cells(), stocks={"n_cells": None, "flips": None, "rel_median_pct": None,
                                                             "top3_kept": None, "top3_lists": None})
    R = sf.revision_stats({"dates": ["2026-11-02", "2026-11-03"]}, str(tmp_path))["stocks"]
    assert (R["n_cells"], R["flips"], R["top3_lists"]) == (0, 0, 0) and R["top3_keep_pct"] is None
