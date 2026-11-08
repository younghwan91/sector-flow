"""수치표 producer(``sector_numbers``)와 검증기(``verify_report``)의 2026-11-08 리뷰 결함.

* ẍ 의 세 조각 길이가 달랐다(20일 → 6·6·8 인데 Δ=6 으로 나눴다).
* 시총이 없는 칸의 가속도가 0.0 이었다(결측이어야).
* 종합 `*` 가 "판정된 모든 창 통과" 가 아니라 "한 창이라도 통과" 였다(seen 이 pass_n 과 늘 같았다).
* 검증기의 G 순위가 동률을 위치 순서로 갈랐다(producer 는 중간순위).
* 문서 주장 어긋남이 수치 실패와 한 목록이었다.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _mod(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SN = _mod("sector_numbers", REPO / "scripts" / "sector_numbers.py")
VR = _mod("verify_report", REPO / "scripts" / "verify_report.py")
VF = _mod("validation_fixes", REPO / "tests" / "test_validation_fixes.py")


# ── ẍ 조각 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n", range(9, 31))
def test_release_pieces_have_equal_length_and_end_today(monkeypatch, n):
    """세 조각이 같은 길이 Δ=n//3 이고 마지막 조각이 구간 끝에서 끝난다(오래된 n%3 일을 버린다)."""
    seen = []

    def fake_agg(_P, _m, _s, a, b):
        seen.append((a, b))
        return {"inst": 1.0, "cap_idx": 100.0, "ret": 0.0}

    monkeypatch.setattr(SN, "agg", fake_agg)
    i0 = 50
    i1 = i0 + n - 1
    _xs, cut = SN.release({}, ["거래소"], "S", i0, i1)
    assert cut == n // 3
    assert [b - a + 1 for a, b in seen] == [cut] * 3, seen
    assert seen[-1][1] == i1 and seen[0][0] == i1 - 3 * cut + 1
    assert all(seen[j][1] + 1 == seen[j + 1][0] for j in range(2)), "조각이 붙어 있어야"


def test_accel_is_missing_not_zero_when_the_cap_is_missing():
    P = VF._payload(0)
    P["cap"]["거래소"]["S0"] = 0.0
    g = SN.agg(P, ["거래소"], "S0", 0, 19)
    assert g["accel"] is None and g["inst"] != 0.0
    D = SN.build(P)                      # 결측 가속도가 적합·화면 계산을 터뜨리지 않는다
    assert next(r for r in D["blocks"]["20|거래소"]["rows"] if r["sector"] == "S0")["accel"] is None


# ── 종합 `*` ──────────────────────────────────────────────────────────────

def _judged(r) -> bool:
    return (not r["thin"] and r.get("a_idx") is not None and r.get("x") is not None
            and r.get("xddot") is not None)


def test_combined_star_means_passed_every_judged_window():
    """`*` 는 판정된 모든 창에서 통과해야 붙는다. 한 창만 통과한 줄에는 안 붙는다."""
    partial = full = 0
    for seed in range(12):
        D = SN.build(VF._payload(seed))
        for mk, C in D["combined"].items():
            for r in C["rows"]:
                blocks = [D["blocks"][f"{w}|{mk}"] for w in C["windows"]]
                rows = [next(q for q in B["rows"] if q["sector"] == r["sector"]) for B in blocks]
                seen = sum(_judged(q) for q in rows)
                passed = sum(q["G"] is not None for q in rows)
                assert (r["seen"], r["pass_n"]) == (seen, passed), r
                assert r["G_pass"] == (seen > 0 and passed == seen), r
                partial += 0 < passed < seen
                full += r["G_pass"]
    assert partial and full, "두 경우가 다 나와야 이 검사가 뜻이 있다"


def test_verifier_flags_a_combined_star_on_a_partial_passer():
    D = SN.build(VF._payload(0))
    r = next(r for C in D["combined"].values() for r in C["rows"] if 0 < r["pass_n"] < r["seen"])
    r["G_pass"] = True
    VR.FAILS.clear()
    VR.layer_a2(D)
    assert any("종합 *" in f for f in VR.FAILS), VR.FAILS


# ── 검증기: G 순위의 동률 ─────────────────────────────────────────────────────

def _row(sector, a, x, xdd, G):
    return {"sector": sector, "thin": False, "a_idx": a, "x": x, "xddot": xdd, "G": G,
            "G_pass": True, "U": 0.5 * x * x, "pct1y": {}, "spark": {}}


def test_verifier_ranks_ties_by_midrank_like_the_producer():
    """a 가 같은 두 섹터는 a 순위를 나눠 가진다(0.25·0.25). 위치 순위로 다시 매기면 0·0.5 가 되어
    producer 의 맞는 G 를 틀렸다고 한다."""
    # a: 1,1,2 → 중간순위 0.25,0.25,1 · x: 1,2,3 → 0,0.5,1 · ẍ: 3,2,1 → 1,0.5,0
    rows = [_row("A", 1.0, 1.0, 3.0, (0.25 + 0 + 1) / 3), _row("B", 1.0, 2.0, 2.0, (0.25 + 0.5 + 0.5) / 3),
            _row("C", 2.0, 3.0, 1.0, (1 + 1 + 0) / 3)]
    D = {"blocks": {"20|전체": {"k": 1.0, "rows": rows}}, "combined": {}}
    VR.FAILS.clear()
    VR.layer_a2(D)
    assert not [f for f in VR.FAILS if "rank" in f], VR.FAILS


# ── 검증기: 문서 주장 ≠ 실패 ─────────────────────────────────────────────────

def test_a_doc_claim_mismatch_is_not_a_failure_and_lands_in_claims_txt(tmp_path):
    VR.FAILS.clear()
    VR.CLAIMS.clear()
    VR.claim("G", "도움말 범위", False, "관측 47% ≥ 널 49%")
    VR.claim("G", "다른 주장", True)
    assert VR.FAILS == []
    VR.write_claims(str(tmp_path))
    txt = (tmp_path / "CLAIMS.txt").read_text("utf-8")
    assert "G/도움말 범위" in txt and "47%" in txt and "다른 주장" not in txt
    VR.CLAIMS.clear()
    VR.write_claims(str(tmp_path))       # 다시 만든 폴더에 옛 CLAIMS 가 남지 않는다
    assert not (tmp_path / "CLAIMS.txt").exists()
