"""검증기 W 층 — 웹 페이지를 node 로 **그려** 칸의 글자를 페이로드·TUI 와 맞춘다(2026-11-08 리뷰 2차 M1).

합성 페이로드(``test_payload_today._payload``)로 numbers.html·viewer.html 을 만들고 ``layer_w`` 를 돌린다.
멀쩡한 페이지는 초록이고, 템플릿이나 TUI 의 반올림·열을 틀리게 바꾸면 빨개진다. DB·네트워크 불요(node 는 필요).
"""
from __future__ import annotations

import functools
import importlib.util
import json
import math
import operator
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


def _write(tmp: Path, P: dict, D: dict, numbers_patch=None, viewer_patch=None) -> Path:
    tpl = (REPO / "scripts" / "templates" / "sector_numbers.html").read_text("utf-8")
    if numbers_patch:
        old, new = numbers_patch
        assert old in tpl
        tpl = tpl.replace(old, new)
    (tmp / "numbers.html").write_text(tpl.replace("__DATA__", json.dumps(D, ensure_ascii=False)), "utf-8")
    vt = (REPO / "scripts" / "templates" / "sector_flow.html").read_text("utf-8")
    if viewer_patch:
        old, new = viewer_patch
        assert old in vt
        vt = vt.replace(old, new)
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


@node
def test_today_sector_items_carry_the_same_day_return_on_web_and_terminal(PD, tmp_path):
    """'오늘' 상위·하위 섹터 옆 그날 수익률(리뷰 3차 D3-m1) — 웹 패널과 TUI 첫 화면이 **같은 글자**인가.

    반올림이 갈리는 값(1.005 — 이진수로 1.00499…, 그냥 toFixed(2) 면 1.00)을 섞는다. 종목 꼬리표를 뗀 항목을
    시장·주체·상위/하위마다 맞춘다. 주입: 한쪽만 반올림을 바꾸거나 수익률을 빼면 실패한다.
    """
    import copy
    import re

    from sectorflow.tui import flow_view as FV
    P, D = copy.deepcopy(PD[0]), copy.deepcopy(PD[1])
    sec = max(D["today"]["sectors"]["거래소"], key=lambda s: (D["today"]["sectors"]["거래소"][s]["inst"] or {}).get("v") or 0)
    for T in (D["today"], P["today"]):
        T["sectors"]["거래소"][sec]["ret"] = 1.005
    out = VR._w_run(VR._w_node(), str(_write(tmp_path, P, D) / "numbers.html"), VR._W_PROBE_NUMBERS)
    strip = re.compile(r"\[[^\]]*\]")
    n = 0
    for m, cards in out["today"].items():
        rank = next(c for c in cards if c["id"] == "t-rank")
        st = FV.State(D)
        st.mi = st.markets.index(m)
        tui = FV.banner_lines(st, 400)
        for (a, ko), side in ((x, sd) for x in FV.banner_actors(D, m) for sd in ("top", "bottom")):
            web = [strip.sub("", i) for ln in rank["lines"] if ln.get("actor") == a and ln.get("side") == side
                   for i in ln["items"]]
            pre = f"   {ko} {'상위' if side == 'top' else '하위'}: "
            line = next(ln for ln in tui if ln.startswith(pre))
            term = [t.replace("-", "−") for t in strip.sub("", line[len(pre):]).rstrip().split(" · ")]
            assert web == term, (m, a, side, web, term)
            assert all(re.search(r" (?:[+−]\d+\.\d{2}%|—)$", t) for t in web), web
            n += len(web)
    assert n > 0
    assert any(f"{sec} " in t and t.endswith(" +1.01%") for t in
               (strip.sub("", i) for c in out["today"]["거래소"] if c["id"] == "t-rank" for ln in c["lines"]
                for i in ln["items"])), "1.005 가 +1.01% 로 올라가지 않았다"


@pytest.fixture(scope="module")
def PH():
    """반올림이 갈리는 흐름 — 마지막 날 기관 971.4999999999999억(JS `toLocaleString` 971, RHU 972)."""
    import copy
    P = copy.deepcopy(PT._payload())
    m = P["markets"][0]
    sec = next(iter(P["flows"][m]))
    P["flows"][m][sec]["inst"][-1] = 971.4999999999999
    P["flows"][m][sec]["forgn"][-1] = -14810.5
    return P, SN.build(P)


@node
def test_web_layer_reads_the_whole_viewer_and_is_green_when_rounding_is_one_rule(PH, tmp_path, capsys):
    """뷰어 전체 표·KPI 카드·랭킹 막대·종목 표지까지 그려 대조한다 — 멀쩡하면 초록(3차 리뷰 D5-M1)."""
    P, D = PH
    out = _w_fails(P, D, _write(tmp_path, P, D))
    assert out == [], out
    printed = capsys.readouterr().out
    for name in ("뷰어 전체 표 = 페이로드", "뷰어 KPI 카드", "뷰어 랭킹 막대", "뷰어 전체 표 금액", "종목 표지 TUI = 뷰어"):
        assert f"[OK  ] W · {name}" in printed, name


@node
def test_web_layer_catches_viewer_amounts_rounded_by_tolocalestring(PH, tmp_path):
    """뷰어 금액이 수치표·TUI 와 다른 반올림(`toLocaleString`)이면 빨갛다 — 금속 +971 vs +972, 금융 −14,810 vs −14,811."""
    P, D = PH
    patch = ('const amt=n=>sgn(n)+Math.abs(RHU(n)).toLocaleString', 'const amt=n=>sgn(n)+Math.abs(n).toLocaleString')
    fails = _w_fails(P, D, _write(tmp_path, P, D, viewer_patch=patch))
    assert "W/뷰어 전체 표 = 페이로드(표시 반올림, 창×시장)" in fails, fails
    assert "W/뷰어 랭킹 막대 값 = 페이로드(창×시장×주체)" in fails, fails
    assert "W/뷰어 전체 표 금액 = 수치표·TUI 블록 글자" in fails, fails


@node
def test_web_layer_catches_viewer_kpi_cards_and_bars_drawing_the_wrong_value(PD, tmp_path):
    P, D = PD
    patch = ('D.sectors.forEach(s=>t+=sum(flow(s,k),i0,i1));', 'D.sectors.forEach(s=>t+=sum(flow(s,"inst"),i0,i1));')
    assert "W/뷰어 KPI 카드 = 페이로드(창×시장×주체)" in _w_fails(P, D, _write(tmp_path, P, D, viewer_patch=patch))
    patch = ('val.append(el("span","num "+cls(v),fmt(v)));', 'val.append(el("span","num "+cls(v),amt(v)));')
    assert "W/뷰어 랭킹 막대 값 = 페이로드(창×시장×주체)" in _w_fails(P, D, _write(tmp_path, P, D, viewer_patch=patch))


@node
def test_web_layer_catches_stock_badges_that_differ_between_tui_and_viewer(PD, tmp_path, monkeypatch):
    """종목 층 표지 — 뷰어가 창을 안 보거나, TUI 드릴다운 헤더가 표지를 박아 두면 빨갛다(3차 리뷰 D2-M1)."""
    from sectorflow.tui import flow_view as FV
    P, D = PD
    patch = ("  return (!e[0].length||e[0].includes(nDays()))?e[1]:scopeNote(e[0]); }", "  return e[1]; }")
    name = "W/종목 표지 TUI = 뷰어(창별·주체별) · 섹터 표 표지 = evidence_at"
    assert name in _w_fails(P, D, _write(tmp_path, P, D, viewer_patch=patch))
    monkeypatch.setattr(FV, "name_head", lambda h, st: h + "[음]" if h in FV.NAME_EV_COLS else h)
    assert name in _w_fails(P, D, _write(tmp_path, P, D))


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


def test_external_truth_skips_a_rebuilt_day_whose_universe_has_since_lost_names(capsys):
    """재구성은 지금의 stocks 마스터로 바구니를 만든다 — 그 뒤 폐지된 종목이 그날 지수에 있으면 같은 것을 맞대는
    대조가 아니다(2026-09-15 재구성의 09-14: 폐지 예정 코스닥 종목 −14~−34%, 바구니가 지수보다 0.115%p 높았다)."""
    def run(rebuilt: bool):
        con, P, meta, px = _ctx_db(idx_t1=100.5)                     # 바구니 +1.0% vs 지수 +0.5% — 갈린다
        con.executemany("INSERT INTO regular_close VALUES (?,?,?,?)",
                        [("Z", "2026-11-04", 100.0, "identity"), ("Z", "2026-11-05", 60.0, "identity")])
        P["rebuilt"] = rebuilt
        VR.FAILS.clear()
        VR._f_today_context(P, con.cursor(), "?", meta, px, lambda c, d, p: None if p is None else p * 100.0)
        return [f for f in VR.FAILS if "외부 참값" in f]
    assert run(rebuilt=True) == []
    assert "그 뒤 폐지" in capsys.readouterr().out
    assert run(rebuilt=False), "평소 리포트에서는 마스터 밖 종목이 있어도 갈림을 실패로 본다"


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

# ── 외부 참값 연속 SKIP — 옆 기준일 폴더의 VERIFY.txt 를 거슬러 세고 3번째부터 WARN(실패 아님) ──────────

_SKIP_TXT = "  [OK  ] F · KRX 지수\n       SKIP 정규장 종가 대조 — 2026-11-0x regular_close 준비 안 됨({'pending': 9})\n"
_RAN_TXT = "  [OK  ] F · 외부 참값: 2026-11-0x 자체 바구니(정규장 종가) ≈ KRX 지수 ±0.1%p   거래소 바구니 +1.000%\n"
_PROV_TXT = "       (잠정 리포트 — 정규장 종가 대조는 확정 모드에서만)\n"


def _skip_root(tmp_path, past: dict) -> str:
    """기준일 → VERIFY.txt 내용(None 이면 폴더만). 검증 중인 폴더(``2026-11-06.building.1``) 경로를 돌려준다."""
    for day, text in past.items():
        (tmp_path / day).mkdir()
        if text is not None:
            (tmp_path / day / "VERIFY.txt").write_text(text, "utf-8")
    (tmp_path / "2026-11-06.building.1").mkdir()
    (tmp_path / "2026-11-09").mkdir()                       # 뒤 기준일은 세지 않는다
    (tmp_path / "2026-11-09" / "VERIFY.txt").write_text(_SKIP_TXT, "utf-8")
    return str(tmp_path / "2026-11-06.building.1")


def _skip_run(monkeypatch, folder):
    monkeypatch.setattr(VR, "REPORT_DIR", folder)
    VR.WARNS.clear()
    return _ctx_fails(rc_src="pending")


def test_external_truth_skip_streak_counts_back_through_sibling_reports(tmp_path):
    """연속 SKIP 수 — 잠정·리포트 없는 날은 건너뛰고(세지도 끊지도 않는다), 대조를 한 날에서 멈춘다.

    주입: 대조한 날에서 멈추지 않거나, 뒤 기준일까지 세면 실패한다.
    """
    folder = _skip_root(tmp_path, {"2026-10-30": _SKIP_TXT, "2026-11-02": _RAN_TXT, "2026-11-03": _SKIP_TXT,
                                   "2026-11-04": _PROV_TXT, "2026-11-05": _SKIP_TXT, "2026-10-29": None})
    assert VR.external_skip_streak(folder, "2026-11-06") == 2
    assert VR.external_skip_streak(None, "2026-11-06") == 0
    assert VR.external_skip_streak(str(tmp_path / "nope" / "2026-11-06"), "2026-11-06") == 0


def test_third_consecutive_external_truth_skip_warns_but_does_not_fail(tmp_path, monkeypatch, capsys):
    """확정 리포트가 외부 참값 대조를 3번 연달아 건너뛰면 [WARN] — VERIFY_FAILED 는 아니다(리뷰 3차 D1-m3).

    주입: 경고를 안 내거나, 경고를 실패(FAILS)로 올리거나, 두 번째에서 내면 실패한다.
    """
    (tmp_path / "a").mkdir()
    two = _skip_root(tmp_path / "a", {"2026-11-05": _SKIP_TXT, "2026-11-04": _RAN_TXT})
    assert _skip_run(monkeypatch, two) == [] and VR.WARNS == []
    out = capsys.readouterr().out
    assert "외부 참값 연속 SKIP 2회" in out and "[WARN]" not in out, out
    (tmp_path / "b").mkdir()
    three = _skip_root(tmp_path / "b", {"2026-11-05": _SKIP_TXT, "2026-11-04": _PROV_TXT, "2026-11-03": _SKIP_TXT})
    assert _skip_run(monkeypatch, three) == [], "연속 SKIP 은 실패가 아니다"
    out = capsys.readouterr().out
    assert VR.WARNS and "[WARN] F · 외부 참값 연속 SKIP 3회" in out, out

# ── 2026-08-27~11-03 재구성 48일 중 21일이 W 층에서만 빨갰다 — 비교기 결함 셋과 표시 결함 하나 ─────────────

def _rekey(P: dict, order: list) -> dict:
    """``names`` 를 숫자 꼴 코드로 다시 단다 — 파이썬은 ``order`` 순서로, JS 는 코드 오름차순(= 그 **역순**)으로 돈다."""
    P2 = json.loads(json.dumps(P))
    old = P2["names"]
    P2["names"] = {str(900000 - i): old[c] for i, c in enumerate(order)}
    return P2


def test_minus_normalizer_touches_signs_not_hyphens_in_names():
    """TUI 의 `-90` 만 웹의 `−90` 으로 — 이름 속 하이픈(S-Oil·와이지-원)은 그대로다."""
    assert VR._w_minus("[에이피알 -90·S-Oil -73]") == "[에이피알 −90·S-Oil −73]"
    assert VR._w_minus("[성광벤드 -16·와이지-원 -1]") == "[성광벤드 −16·와이지-원 −1]"
    assert VR._w_minus("-0.25") == "−0.25"
    assert VR._w_minus("화학 -1,234(3)") == "화학 −1,234(3)"
    assert VR._w_minus("[인제니아테… -3·CJ ENM +2]") == "[인제니아테… −3·CJ ENM +2]"


@node
def test_web_layer_today_tags_with_hyphenated_names_match_the_tui(PD, tmp_path):
    """종목명에 하이픈·공백·괄호가 있어도 '오늘' 패널 = TUI 첫 화면이다.

    비교기가 TUI 꼬리표를 통째로 ``replace("-", "−")`` 해 `S-Oil` 이 `S−Oil` 이 되고, 같은 글자를 불일치로 냈다
    (2026-10-21 화학 `에이피알 −90·S-Oil −73` vs `[에이피알 -90·S-Oil -73]` 등 13일).
    """
    P, _ = PD
    P2 = json.loads(json.dumps(P))
    bases = ["S-Oil", "와이지-원", "CJ ENM", "인제니아테라퓨틱스(Reg.S)"]
    for i, nm in enumerate(P2["names"].values()):
        nm["name"] = f"{bases[i % len(bases)]}{i}"
    D2 = SN.build(P2)
    folder = _write(tmp_path, P2, D2)
    assert "S-Oil" in (folder / "numbers.html").read_text("utf-8")
    assert not [f for f in _w_fails(P2, D2, folder) if "'오늘' 패널" in f]


@node
def test_web_layer_one_day_list_tie_across_the_sixth_place(PD, tmp_path):
    """동률이 6번째 자리를 걸치면 뷰어는 잘린 6개 **밖**의 종목을 고를 수 있다 — 그것도 불일치가 아니다.

    2026-10-30 유통: −0.5억 셋(에스에이엠티·신세계인터내셔날·KG모빌리티플랫폼)이 5~7번째에 걸쳐, 뷰어가
    파이썬의 6개에 없는 에스에이엠티를 그렸다. 짝을 잘린 6개 안에서만 찾던 비교기가 빨갰다.
    """
    P, _ = PD
    P2 = json.loads(json.dumps(P))
    S1 = P2["today"]["sectors"]["전체"]
    sec = max(S1, key=lambda s: abs((S1[s].get("inst") or {}).get("v") or 0))
    mine = [c for c, n in P2["names"].items() if n.get("sector") == sec and (n.get("win") or {}).get("1")]
    assert len(mine) >= 7, "픽스처에 7종목이 없다"
    # 7종목 모두 순매수, 6·7번째가 같은 금액 — 둘 중 파이썬은 앞의 것(0 으로 시작하는 코드), JS 는 숫자 꼴 코드를 고른다.
    for i, c in enumerate(mine[:7]):
        P2["names"][c]["win"]["1"].update(inst=[30.0, 25.0, 20.0, 15.0, 10.0, 5.5, 5.5][i],
                                          invtrt=0.1 * i, penfnd_etc=1.0 + i)
    for c in mine[7:]:
        P2["names"][c]["win"]["1"].update(inst=-1.0)
    assert mine[5].startswith("0")
    P2["names"]["175330"] = P2["names"].pop(mine[6])
    D2 = SN.build(P2)
    assert not [f for f in _w_fails(P2, D2, _write(tmp_path, P2, D2)) if "1일 종목 목록" in f]


@node
def test_trust_sum_that_cancels_to_zero_is_plus_zero_on_web_tui_and_verifier(PD, tmp_path):
    """상쇄돼 0 인 투신+연기금 합이 더하는 순서에 따라 ±2.7e-17 이 돼 웹 '+0'·TUI '−0' 으로 갈렸다.

    2026-09-01 종이/목재·09-09 금융 재구성. JS 는 숫자 꼴 코드를 오름차순으로 돌아 파이썬과 순서가 반대다.
    섹터 둘: 하나는 파이썬 순서에서 음의 잔차, 다른 하나는 JS 순서에서 음의 잔차 — 어느 한쪽이 잔차를 안 지우면 빨갛다.
    """
    from sectorflow.tui import flow_view as FV
    P, _ = PD
    by_sec: dict = {}
    for c, n in P["names"].items():
        if n["market"] == "거래소":
            by_sec.setdefault(n["sector"], []).append(c)
    secs = sorted(s for s, cs in by_sec.items() if len(cs) == 4)[:2]
    assert len(secs) == 2
    seqs = {secs[0]: [0.1, -0.5, 0.3, 0.1], secs[1]: [0.1, 0.3, -0.5, 0.1]}   # 파이썬 −·JS + / 파이썬 +·JS −
    order = [c for s in secs for c in by_sec[s]] + [c for c in P["names"] if c not in
                                                    {x for s in secs for x in by_sec[s]}]
    P2 = _rekey(P, order)
    for c, n in P2["names"].items():
        if n["sector"] in secs:
            n["win"]["5"].update(invtrt=0.0, penfnd_etc=0.0)
    for s in secs:
        for c, v in zip([c for c, n in P2["names"].items() if n["sector"] == s and n["market"] == "거래소"], seqs[s]):
            P2["names"][c]["win"]["5"]["invtrt"] = v
    # 픽스처가 잔차를 정말 만드는가 — 파이썬 순서 합의 부호가 두 섹터에서 반대다.
    # (내장 sum 은 3.12 부터 보정 합이라 잔차가 안 남는다 — TUI·웹처럼 하나씩 더한다.)
    raw = [functools.reduce(operator.add, [P2["names"][c]["win"]["5"]["invtrt"] for c in P2["names"]
                                           if P2["names"][c]["sector"] == s and P2["names"][c]["market"] == "거래소"], 0.0)
           for s in secs]
    assert raw[0] < 0 < raw[1] and max(map(abs, raw)) < 1e-15
    D2 = SN.build(P2)
    t = FV._sector_trusted(D2, ["거래소"], "5")
    assert all(t[s] == 0.0 and math.copysign(1, t[s]) > 0 for s in secs)
    fails = _w_fails(P2, D2, _write(tmp_path, P2, D2))
    assert not [f for f in fails if "본표 칸" in f], fails


@node
def test_viewer_rounds_like_the_tui(tmp_path):
    """뷰어 금액·%p 가 TUI `rhu` 와 같은 반올림이다 — toFixed(2) 는 1.005 를 1.00 으로(이진 값) 그려 TUI 1.01 과 갈렸다."""
    from sectorflow.tui import flow_view as FV
    P = PT._payload()
    page = tmp_path / "viewer.html"
    vt = (REPO / "scripts" / "templates" / "sector_flow.html").read_text("utf-8")
    page.write_text(vt.replace("__DATA__", SF.script_json(P)), "utf-8")
    vals = [1.005, 2.675, -1.005, 0.5, -0.5, 2.4999999999, -0.3, 0.0, 12.5, -96.5]
    got = VR._w_run(shutil.which("node"), str(page), f"__out({{a:{json.dumps(vals)}.map(amt), p:{json.dumps(vals)}.map(pp)}});")
    assert got["a"] == [VR._w_minus(FV.fmt_amt(v)) for v in vals]
    assert got["p"] == [VR._w_minus(FV.fmt_pct(v)) for v in vals]


# ── #32 의 ±0 부류, 뷰어 창 합 경로(2026-09-02·09-28 재구성) — 상쇄된 창 합의 잔차 부호 ──────────────

#: 네 double 의 정확한 합은 0 — 왼쪽부터 더할 때만(뷰어 JS, 파이썬 3.11 sum) −2.8e-17 이 남는다(3.12+ sum 은 보정 합이라 0.0).
_CANCEL_JS_NEG = [0.1, -0.5, 0.3, 0.1]
#: 세 double 의 정확한 합 자체가 음수(십진 오차) — 어느 쪽으로 더해도 잔차가 남는다. 블록(zsum 전)·검증기(rhu 전)가 '−0' 이 되는 쪽.
_CANCEL_BOTH_NEG = [-0.1, -0.2, 0.3]


def _flows_patch(P: dict, mkt: str, pats: dict) -> dict:
    """``flows[mkt][sec][actor]`` 의 마지막 20일 머리에 패턴을 심는다(나머지 0) — 20·60·120일 합이 그 패턴이 된다."""
    P2 = json.loads(json.dumps(P))
    nd = len(P2["dates"])
    for (sec, actor), pat in pats.items():
        arr = [0.0] * nd
        arr[nd - 20:nd - 20 + len(pat)] = pat
        P2["flows"][mkt][sec][actor] = arr
    return P2


@node
def test_viewer_window_sum_that_cancels_is_plus_zero_everywhere(PD, tmp_path):
    """상쇄돼 0 인 20일 합이 뷰어 표·막대 '+0' = 블록 '+0' = 검증기 '+0' 이다.

    2026-09-02·09-28 재구성: 종이/목재 20일 합이 뷰어(JS, 왼쪽부터)에서 −2.8e-17, 블록·검증기(파이썬)에서
    0.0 또는 −2.8e-17 로 남아 '−0' vs '+0' 셋이 서로 갈렸다. 금융은 JS 쪽만, 반도체는 양쪽 다 음의 잔차 —
    뷰어 `Z`·블록 `zsum`·검증기 `_w_rhu` 중 **어느 하나**를 빼도 빨개진다.
    """
    P, _ = PD
    P2 = _flows_patch(P, "코스닥", {("금융", "etc"): _CANCEL_JS_NEG, ("반도체", "etc"): _CANCEL_BOTH_NEG})
    a = P2["flows"]["코스닥"]["금융"]["etc"]
    # 전제는 파이썬 판에 안 묶는 math.fsum(정확한 합)으로 적는다 — sum() 은 3.12 에서 보정 합으로 바뀌었다.
    assert functools.reduce(operator.add, a, 0.0) < 0 and math.fsum(a) == 0.0, "픽스처가 JS 쪽 잔차를 안 만든다"
    b = P2["flows"]["코스닥"]["반도체"]["etc"]
    assert functools.reduce(operator.add, b, 0.0) < 0 and math.fsum(b) < 0, "픽스처가 양쪽 음의 잔차를 안 만든다"
    D2 = SN.build(P2)
    for w in ("20", "60", "120"):
        blk = {r["sector"]: r for r in D2["blocks"][f"{w}|코스닥"]["rows"]}
        assert blk["금융"]["etc"] == 0.0 and math.copysign(1, blk["금융"]["etc"]) > 0
        assert blk["반도체"]["etc"] == 0.0 and math.copysign(1, blk["반도체"]["etc"]) > 0
    fails = [f for f in _w_fails(P2, D2, _write(tmp_path, P2, D2)) if "뷰어" in f]
    assert fails == [], fails


@node
def test_viewer_market_total_that_cancels_is_plus_zero(PD, tmp_path):
    """섹터 합(각각은 0 아님)이 시장 전체에서 상쇄될 때 — KPI 카드도 '+0' 이다.

    sum() 이 잔차를 지워도 KPI 는 섹터 27개를 **다시 누적**하므로 제 잔차를 만든다(음수 둘을 먼저 더하면 −5.6e-17).
    검증기의 시장 합(`out["tot"]`)도 같은 자리에서 지운다 — 한쪽만 지우면 '+0' vs '−0' 으로 빨갛다.
    """
    P, _ = PD
    secs = [s for s in P["sectors"] if s in P["flows"]["코스닥"]]
    # D.sectors 순회 순서대로 음·음·양 — 각 섹터 합은 −0.1·−0.2·+0.3(0 아님), 시장 합만 0.
    pats = {(secs[0], "etc"): [-0.1], (secs[1], "etc"): [-0.2], (secs[2], "etc"): [0.3]}
    for s in secs[3:]:
        pats[(s, "etc")] = [0.0]
    P2 = _flows_patch(P, "코스닥", pats)
    tots = [sum(P2["flows"]["코스닥"][s]["etc"]) for s in secs[:3]]
    acc = functools.reduce(operator.add, tots, 0.0)
    assert acc != 0.0 and acc < 0, "픽스처가 시장 합의 음 잔차를 안 만든다"
    D2 = SN.build(P2)
    fails = [f for f in _w_fails(P2, D2, _write(tmp_path, P2, D2)) if "뷰어" in f]
    assert fails == [], fails
