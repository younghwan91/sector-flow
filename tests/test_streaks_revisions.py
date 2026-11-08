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


# ── 시드 ────────────────────────────────────────────────────────────────────

def _seed_file(root: Path, date: str, cells, label=sf.REV_SEED_LABEL, built=None):
    d = root / sf.REV_SEED_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{date}.json").write_text(json.dumps({"base_final": False, "base_built": built or f"{date} 18:10",
                                                "cells_1d": cells, "seed": {"label": label}}), "utf-8")


def _revision_root(tmp_path: Path) -> Path:
    big = [["거래소", f"S{i}", "inst", 100.0, 100.0] for i in range(8)]
    moved = big[:7] + [["거래소", "S7", "inst", -50.0, 100.0]]
    PT._final_folder(tmp_path, "2026-11-04", big)                  # 진짜 확정 기록 — 시드보다 먼저
    _seed_file(tmp_path, "2026-11-04", moved)
    (tmp_path / "2026-11-03").mkdir()                              # 재구성 확정(기록 없음) + 시드
    (tmp_path / "2026-11-03" / "FINAL.txt").write_text("재구성")
    _seed_file(tmp_path, "2026-11-03", moved)
    _seed_file(tmp_path, "2026-11-02", _top_cells())               # 폴더 없이 시드만
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


@pytest.mark.parametrize("corrupt, want", [
    (_rev_bump_flips, "T/잠정→확정 수정 폭 — 날짜·칸 수·중앙값·부호 뒤집힘을 옆 폴더의 확정 기록·시드에서 다시 센 것과 같은가"),
    (_rev_bump_keep, "T/잠정→확정 수정 폭 — 상위 3 유지율 재계산"),
    (_rev_drop_seed, "T/잠정→확정 수정 폭 — 시드로 센 날짜와 출처 표지"),
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
    old.pop("final", None)
    PT._write(tmp_path, asof, old)
    fd, why = sf.final_diff_block(new, str(tmp_path))
    assert why is None and fd["n_changed"] >= 2 and fd["extremes_change"]
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


def _fd_none(P, b):
    P["final_diff"], P["final_diff_reason"] = None, "어쩌다"


def _fd_sibling_gone(P, b):
    os.remove(b.parent / P["dates"][-1] / "payload.json")


@pytest.mark.parametrize("corrupt, want", [
    (_fd_old, "T/잠정 → 확정 — 잠정값·차이·칸 수를 옆 잠정 리포트에서 다시 센 것과 같은가"),
    (_fd_cells, "T/잠정 → 확정 — 잠정값·차이·칸 수를 옆 잠정 리포트에서 다시 센 것과 같은가"),
    (_fd_ext, "T/잠정 → 확정 — %ile 극단 목록의 들고 남 재계산"),
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
    assert Q["final_diff"] is None and Q["final_diff_reason"].startswith(sf.REFINAL_WHY)
    assert _fails(Q, build) == []


def test_retry_says_no_comparison_when_values_moved_after_the_first_final(tmp_path):
    """첫 확정 뒤 값이 또 바뀌면 그 기록의 확정값이 이 리포트와 다르다 — 잇지 않고 이유를 적는다."""
    P, Q, build = _published_first_final(tmp_path)
    Q["flows"]["코스닥"]["금융"]["inst"][-1] = round(Q["flows"]["코스닥"]["금융"]["inst"][-1] + 0.01, 2)
    _rebuild(Q, tmp_path, build)
    assert Q["final_diff"] is None and "값이 또 바뀌어" in Q["final_diff_reason"]
    assert vr._vt_nothing_to_inherit(Q, P)
    # 극단 목록만 움직인 경우도 같다 — 머리 칸(1·5일)이 같아도 그 기록의 들고 남이 낡는다.
    (tmp_path / "x").mkdir()
    P2, Q2, build2 = _published_first_final(tmp_path / "x")
    ser = Q2["flows"]["거래소"]["반도체"]["inst"]
    ser[:-5] = [max(ser) + 100.0 + i for i in range(len(ser) - 5)]    # 5일 창 밖 — 오늘 값의 1년 순위만 바뀐다
    assert sf._head_cells(Q2) == sf._head_cells(P2) and sf.extremes_change(Q2, P2)
    _rebuild(Q2, tmp_path / "x", build2)
    assert Q2["final_diff"] is None and "값이 또 바뀌어" in Q2["final_diff_reason"]
    assert vr._vt_nothing_to_inherit(Q2, P2)


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
    (_rr_zero, "T/확정 재시도 — 잠정 대비가 첫 확정의 기록 그대로인가(확정끼리 견주지 않았는가)"),
    (_rr_cells, "T/확정 재시도 — 잠정 대비가 첫 확정의 기록 그대로인가(확정끼리 견주지 않았는가)"),
    (_rr_count, "T/확정 재시도 — 잠정 대비가 첫 확정의 기록 그대로인가(확정끼리 견주지 않았는가)"),
    (_rr_first_time, "T/확정 재시도 — 첫 확정 시각이 옆 폴더의 것인가"),
    (_rr_values, "T/확정 재시도 — 이어받은 칸의 값이 첫 확정과 같은가"),
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
    assert all("시드" in t for t in revision_tiers(D)), "좁은 화면에서도 시드 출처는 남는다"
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
