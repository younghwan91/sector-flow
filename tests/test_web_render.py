"""검증기 W 층 — 웹 페이지를 node 로 **그려** 칸의 글자를 페이로드·TUI 와 맞춘다(2026-11-08 리뷰 2차 M1).

합성 페이로드(``test_payload_today._payload``)로 numbers.html·viewer.html 을 만들고 ``layer_w`` 를 돌린다.
멀쩡한 페이지는 초록이고, 템플릿이나 TUI 의 반올림·열을 틀리게 바꾸면 빨개진다. DB·네트워크 불요(node 는 필요).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
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


SN = _mod("sector_numbers", REPO / "scripts" / "sector_numbers.py")
SF = _mod("sector_flow", REPO / "scripts" / "sector_flow.py")
VR = _mod("verify_report", REPO / "scripts" / "verify_report.py")
PT = _mod("payload_today", REPO / "tests" / "test_payload_today.py")


@pytest.fixture(scope="module")
def PD():
    P = PT._payload()
    P["today"]["approx_gap_warn"] = 5.0
    P["today"]["krx_index"] = {"거래소": {"code": "001", "name": "코스피", "ret": {"1": -1.25, "20": 3.5}}}
    # 반올림이 갈리는 칸 — 96.5(파이썬 .0f 는 96, JS 는 97)와 12.5억.
    sec = next(iter(P["today"]["sectors"]["거래소"]))
    P["today"]["sectors"]["거래소"][sec]["inst"] = {"v": 12.5, "pct": 96.5}
    P["revision_stats"] = {"n_dates": 3, "rel_median_pct": 9.5, "flips": 1, "n_cells": 120}
    return P, SN.build(P)


def _write(tmp: Path, P: dict, D: dict, numbers_patch=None) -> Path:
    tpl = (REPO / "scripts" / "templates" / "sector_numbers.html").read_text("utf-8")
    if numbers_patch:
        old, new = numbers_patch
        assert old in tpl
        tpl = tpl.replace(old, new)
    (tmp / "numbers.html").write_text(tpl.replace("__DATA__", json.dumps(D, ensure_ascii=False)), "utf-8")
    vt = (REPO / "scripts" / "templates" / "sector_flow.html").read_text("utf-8")
    (tmp / "viewer.html").write_text(vt.replace("__DATA__", SF.script_json(P)), "utf-8")
    return tmp


def _w_fails(P, D, folder) -> list[str]:
    VR.FAILS.clear()
    VR.layer_w(D, P, str(folder))
    return [f for f in VR.FAILS if f.startswith("W/")]


@node
def test_web_layer_is_green_on_a_fresh_report(PD, tmp_path):
    P, D = PD
    assert _w_fails(P, D, _write(tmp_path, P, D)) == []
    assert VR.CHECKS > 0


@node
def test_web_layer_catches_a_table_cell_rounded_another_way(PD, tmp_path):
    P, D = PD
    patch = ('const PI=p=>p==null?"—":String(RHU(p));', 'const PI=p=>p==null?"—":String(Math.floor(p));')
    fails = _w_fails(P, D, _write(tmp_path, P, D, patch))
    assert "W/본표 칸 = 페이로드 값(표시 반올림)" in fails, fails


@node
def test_web_layer_catches_a_today_panel_that_drops_the_stocks(PD, tmp_path):
    P, D = PD
    patch = ('const stockTag=(arr,side)=>!arr?[]:', 'const stockTag=(arr,side)=>true?[]:')
    fails = _w_fails(P, D, _write(tmp_path, P, D, patch))
    assert any("'오늘' 패널 = 페이로드" in f for f in fails), fails


@node
def test_web_layer_catches_the_tui_rounding_half_to_even(PD, tmp_path, monkeypatch):
    """리뷰 M6 — TUI 가 96.5 를 96 으로(파이썬 은행가 반올림) 그리면 웹 97 과 갈린다."""
    from sectorflow.tui import flow_view as FV
    P, D = PD
    monkeypatch.setattr(FV, "pct_int", lambda p: int(f"{p:.0f}"))
    fails = _w_fails(P, D, _write(tmp_path, P, D))
    assert "W/본표 칸 = TUI 가 같은 칸에 그리는 글자" in fails, fails
    assert "W/'오늘' 패널 = TUI 첫 화면(주체·상위/하위·%ile 극단 글자)" in fails, fails


@node
def test_web_layer_catches_a_missing_one_day_tab(PD, tmp_path):
    P, D = PD
    patch = ("const WINS = (T&&T.sectors ? [1] : []).concat(BLOCK_WINS);", "const WINS = BLOCK_WINS;")
    fails = _w_fails(P, D, _write(tmp_path, P, D, patch))
    assert "W/1일 탭이 있는가(오늘 블록이 있는 리포트)" in fails, fails


@node
def test_web_layer_catches_an_evidence_badge_outside_the_tested_scope(PD, tmp_path):
    P, D = PD
    patch = ('  return m===EV_MARKET?base:MARKET_NOTE;', '  return base;')
    fails = _w_fails(P, D, _write(tmp_path, P, D, patch))
    assert "W/본표 증거 표지 = TUI 의 표지(창·주체 범위)" in fails, fails


def test_without_node_the_layer_fails_rather_than_passing(PD, tmp_path, monkeypatch):
    """node 가 없으면 **실패**다 — 그려 보지 않고 초록을 내면 웹을 본 것처럼 읽힌다."""
    P, D = PD
    monkeypatch.setattr(VR, "_w_node", lambda: None)
    assert _w_fails(P, D, tmp_path) == ["W/환경 — node 가 있는가(웹 페이지를 그려 본다)"]


# ── F 층의 '오늘' 맥락 — KRX 지수·근사 오차 재계산과 외부 참값(정규장 종가 ≈ KRX 지수) ──────────

def _ctx_db(rc_src="identity", idx_t1=101.0):
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.executescript("""
      CREATE TABLE sector_index(code, date, close); CREATE TABLE daily_bars(code, date, volume, trade_value, source);
      CREATE TABLE supply_demand(code, date, close, individual, foreign_, institution, etc_corp, source);
      CREATE TABLE regular_close(code, date, close, src); CREATE TABLE stocks(code, market, sector);""")
    d0, d1, d2 = "2026-11-04", "2026-11-05", "2026-11-06"
    con.executemany("INSERT INTO sector_index VALUES (?,?,?)",
                    [("001", d0, 100.0), ("001", d1, idx_t1), ("001", d2, 99.0)])
    # 두 종목: A 시총 가중 3/4 +2%, B 1/4 −2% → 바구니 +1.0%
    con.executemany("INSERT INTO regular_close VALUES (?,?,?,?)",
                    [("A", d0, 100.0, rc_src), ("A", d1, 102.0, rc_src), ("B", d0, 100.0, rc_src),
                     ("B", d1, 98.0, rc_src), ("A", d2, None, "pending"), ("B", d2, None, "pending")])
    P = {"dates": [d0, d1, d2], "markets": ["거래소"], "final": True,
         "today": {"krx_index": {"거래소": {"ret": {"1": -1.98, "20": None}}},
                   "market": {"전체": {"approx_gap": None}, "거래소": {"approx_gap": None}}}}
    P["today"]["krx_index"]["거래소"]["ret"]["1"] = round((99.0 / idx_t1 - 1) * 100, 3)
    meta = {"A": ("거래소", "S"), "B": ("거래소", "S")}
    px = {("A", d0): 3.0, ("B", d0): 1.0}
    return con, P, meta, px


def _ctx_fails(rc_src="identity", idx_t1=101.0):
    con, P, meta, px = _ctx_db(rc_src, idx_t1)
    VR.FAILS.clear()
    VR._f_today_context(P, con.cursor(), "?", meta, px, lambda c, d, p: None if p is None else p * 100.0)
    return [f for f in VR.FAILS if f.startswith("F/")]


def test_external_truth_regular_close_basket_matches_the_krx_index(capsys):
    assert _ctx_fails() == []
    assert "바구니 +1.000% vs 지수 +1.000%" in capsys.readouterr().out


def test_external_truth_fails_when_the_basket_and_the_index_disagree():
    fails = _ctx_fails(idx_t1=100.5)
    assert any("외부 참값" in f for f in fails), fails


def test_external_truth_skips_with_a_reason_when_regular_close_is_not_ready(capsys):
    assert _ctx_fails(rc_src="pending") == []
    assert "SKIP 정규장 종가 대조" in capsys.readouterr().out


def test_krx_index_return_is_recomputed_from_the_db():
    con, P, meta, px = _ctx_db()
    P["today"]["krx_index"]["거래소"]["ret"]["1"] += 0.5
    VR.FAILS.clear()
    VR._f_today_context(P, con.cursor(), "?", meta, px, lambda c, d, p: None if p is None else p * 100.0)
    assert any("KRX 지수" in f for f in VR.FAILS), VR.FAILS


@node
def test_web_layer_is_green_on_a_rebuilt_day_outside_the_finalize_window(PD, tmp_path):
    """창 밖 재구성(finalize_unverified)은 마감 값이다 — '(잠정) 표지가 없다' 로 빨개지면 안 된다(2026-09-15 재구성)."""
    P, D = PD
    P2 = dict(P, rebuilt=True, finalize_unverified=True)
    D2 = dict(D, rebuilt=True, finalize_unverified=True)
    assert _w_fails(P2, D2, _write(tmp_path, P2, D2)) == []


@node
def test_web_layer_one_day_list_does_not_care_how_ties_are_ordered(PD, tmp_path):
    """같은 1일 금액의 두 종목은 뷰어(JS)와 검증기(파이썬)가 다른 순서로 놓는다 — 그걸 불일치로 보면 안 된다.

    JS 객체는 0 으로 시작하지 않는 숫자 꼴 키(175330)를 **맨 앞에 숫자 순**으로 돌고, 파이썬 dict 는 넣은 순서로 돈다.
    동률이면 정렬이 그 순서를 남겨 둘이 갈린다(2026-09-15 금융: LG 003550·JB금융지주 175330 둘 다 +37.9억).
    """
    P, D = PD
    P2 = json.loads(json.dumps(P))
    sec = max(P2["today"]["sectors"]["전체"], key=lambda s: abs((P2["today"]["sectors"]["전체"][s].get("inst") or {}).get("v") or 0))
    mine = sorted(c for c, n in P2["names"].items() if n.get("sector") == sec and (n.get("win") or {}).get("1"))
    assert len(mine) >= 2, "픽스처가 동률을 만들 종목이 없다"
    lo, hi = mine[0], mine[1]
    for i, c in enumerate((lo, hi)):
        P2["names"][c]["win"]["1"].update(inst=37.9, invtrt=2.2 + i, penfnd_etc=11.9 + i)   # 같은 금액, 다른 세부
    # hi 를 0 으로 시작하지 않는 코드로 바꿔 **뒤에** 넣는다 — 파이썬은 lo·175330, JS 는 175330·lo 순으로 돈다.
    moved = P2["names"].pop(hi)
    P2["names"]["175330"] = moved
    D2 = SN.build(P2)
    assert not [f for f in _w_fails(P2, D2, _write(tmp_path, P2, D2)) if "1일 종목 목록" in f]
