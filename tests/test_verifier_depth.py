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


# ── 유니버스 밖 거래대금 — 분모는 KRX 종합이다 ──────────────────────────────

def _universe_db(kospi_whole=2898.0, kospi_in=2794.0):
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.executescript("""
      CREATE TABLE stocks(code TEXT, market TEXT);
      CREATE TABLE daily_bars(code TEXT, date TEXT, trade_value REAL);
      CREATE TABLE sector_index(code TEXT, date TEXT, trade_value REAL);
    """)
    con.executemany("INSERT INTO stocks VALUES (?,?)", [("A", "거래소"), ("B", "코스닥")])
    con.executemany("INSERT INTO daily_bars VALUES (?,?,?)", [("A", "2026-11-06", kospi_in), ("B", "2026-11-06", 507.0)])
    con.executemany("INSERT INTO sector_index VALUES (?,?,?)",
                    [("001", "2026-11-06", kospi_whole), ("101", "2026-11-06", 507.0)])
    return con


def test_universe_gap_is_measured_against_the_krx_whole_market(capsys):
    """우선주·리츠는 DB 어느 표에도 없다 — 유니버스 안에서 세면(옛 0.130%) 안 보인다. 11-06 거래소 28.98조 − 27.94조 = 3.6%."""
    con = _universe_db()
    VR.CLAIMS.clear()
    VR._f_universe_gap(con.cursor(), "?", "2026-11-06", "2026-11-06")
    assert VR.CLAIMS == []
    out = capsys.readouterr().out
    assert "거래소 3.59%" in out and "코스닥 0.00%" in out


def test_universe_gap_claim_trips_when_the_document_goes_stale():
    con = _universe_db(kospi_in=2000.0)                     # 31% — "3.6%" 라 적은 글이 낡았다
    VR.CLAIMS.clear()
    VR._f_universe_gap(con.cursor(), "?", "2026-11-06", "2026-11-06")
    assert len(VR.CLAIMS) == 1 and "유니버스 밖" in VR.CLAIMS[0] and "거래소 30.99%" in VR.CLAIMS[0]
    VR.CLAIMS.clear()


def test_universe_gap_skips_when_the_index_has_no_trade_value(capsys):
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE sector_index(code TEXT, date TEXT, close REAL)")
    VR.CLAIMS.clear()
    VR._f_universe_gap(con.cursor(), "?", "2026-11-06", "2026-11-06")
    assert VR.CLAIMS == [] and "SKIP 유니버스 밖" in capsys.readouterr().out


# ── T 층 종목(주도주) 재계산 — 손으로 센 값과 맞댄다. 끝값 대조는 양쪽이 같으면 초록이라 정의가 움직여도 안 보인다.

def _stk(new: dict, old: dict) -> tuple[dict, dict]:
    def doc(rows):
        return {"names": {c: {"market": "거래소", "sector": "보험", "name": c,
                              "win": {"1": dict(zip(VR.VT_STK_ACTORS, r))}} for c, r in rows.items()}}
    return doc(new), doc(old)


def test_stock_layer_recount_matches_hand_numbers():
    """기관 |값| 50·30·15·5 → 누적 80% 에 **딱** 닿는 둘째 종목에서 멈춘다(주도주 A·B).

    수정 폭 = |신−구|/|신|: 칸 여섯 개가 10·10·10·10·100·200 %(B 외국인은 부호가 뒤집혔고, 0 → 값인 B 연기금은 뒤집힘이
    아니다) → 중앙값 10. 게이트(그 칸 합의 2%)에 **정확히** 걸린 칸이 둘(A 외국인 2 = 100×2%, B 투신은 합이 자기뿐) 들어가고,
    모자란 칸 하나(A 연기금 0.1 < 2.002)는 빠진다. 상위 3 유지 = 주체 4개 × 6종목: 기관·투신·연기금은 3개씩, 외국인은 {B,A,C} 대 {A,C,D} 로 2개 → 11/4."""
    new = {"A": (50.0, 0.0, 0.1, 2.0), "B": (30.0, 0.01, 100.0, 98.0), "C": (15.0, 0.0, 0.0, 0.0),
           "D": (5.0, 0.0, 0.0, 0.0), "E": (0.0, 0.0, 0.0, 0.0), "F": (0.0, 0.0, 0.0, 0.0)}
    old = {"A": (55.0, 0.0, 0.1, 1.8), "B": (33.0, 0.009, 0.0, -98.0), "C": (15.0, 0.0, 0.0, 0.0),
           "D": (5.0, 0.0, 0.0, 0.0), "E": (0.0, 0.0, 0.0, 0.0), "F": (0.0, 0.0, 0.0, 0.0)}
    P, prov = _stk(new, old)
    S = VR._vt_stock_diff(P, prov)
    assert S["n_stocks"] == 2, "주도주 — 누적 80% 를 처음 넘긴(또는 닿은) 행까지"
    assert S["n_cells"] == 6 and S["flips"] == 1
    assert S["rel_median_pct"] == pytest.approx(10.0)
    assert (S["top3_kept"], S["top3_lists"]) == (11, 4), "6종목은 문턱(VT_STK_MIN) 안이다"
    # 합계의 79.6% 에서는 아직 안 닿았다 — 문턱의 분모(100)가 움직이면 여기서 일찍 멈춘다.
    near = {"A": (49.6, 0, 0, 0), "B": (30.0, 0, 0, 0), "C": (15.4, 0, 0, 0), "D": (5.0, 0, 0, 0)}
    assert VR._vt_stk_drivers(VR._vt_win1(_stk(near, near)[0])) == {"A", "B", "C"}


def test_viewer_expect_treats_a_sector_missing_from_one_market_as_zero():
    """한 시장에만 있는 섹터의 '전체' 합 = 있는 시장의 합 — 없는 칸을 0 아닌 값으로 메우면 뷰어 대조가 틀린 값을 기대한다."""
    P = {"dates": ["2026-11-05", "2026-11-06"], "markets": ["거래소", "코스닥"], "sectors": ["음식료"],
         "flows": {"거래소": {"음식료": {k: [1.0, 2.0] for k in ("inst", "forgn", "indiv", "etc", "tv")}}, "코스닥": {}}}
    got = VR._w_viewer_expect(P, 2, "전체")
    assert got["tot"]["inst"] == 3.0 and got["sec"]["음식료"]["inst"] == 3.0


def test_stock_revision_rollup_counts_zero_kept_as_zero():
    """유지 0개인 날은 0 이다 — 결측처럼 1 로 메우면 유지율이 부푼다."""
    VR.FAILS.clear()
    found = [("2026-11-05", {"stocks": {"n_cells": 2, "flips": 0, "rel_median_pct": 1.0,
                                        "top3_kept": 0, "top3_lists": 4}})]
    R = {"stocks": {"n_dates": 1, "n_cells": 2, "flips": 0, "rel_median_pct": 1.0,
                    "top3_lists": 4, "top3_keep_pct": 0.0}}
    VR._vt_stock_rev_check(R, found)
    assert VR.FAILS == []
    # 칸 수·뒤집힘이 비어도 0 이다.
    VR.FAILS.clear()
    found = [("2026-11-05", {"stocks": {"n_cells": None, "flips": None, "rel_median_pct": None,
                                        "top3_kept": 2, "top3_lists": 1}})]
    R = {"stocks": {"n_dates": 1, "n_cells": 0, "flips": 0, "rel_median_pct": None,
                    "top3_lists": 1, "top3_keep_pct": 200.0 / 3}}
    VR._vt_stock_rev_check(R, found)
    assert VR.FAILS == []
