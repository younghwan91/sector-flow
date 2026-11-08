"""브라우저로 여는 리포트 — 페이지 스크립트를 node 로 실제로 돌린다.

2026-08-28 페이로드가 종목 일별 배열을 빼면서 ``viewer.html`` 이 ``TypeError`` 로 절반만 그려졌는데,
VERIFY 는 뷰어를 안 봐서 두 달 넘게 "실패 0건" 이었다. DOM 은 빈 껍데기로 대신하고, 페이지가 **이
페이로드 모양으로 끝까지 도는가**와 무엇을 적었는가만 본다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
node = pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")

HARNESS = r"""
const fs=require("fs"), vm=require("vm");
const src=fs.readFileSync(process.argv[2],"utf8");
const texts=[], clicks=[];
function mk(){
  const store={};
  const f=function(){return mk();};
  return new Proxy(f,{
    get(_t,k){
      if(k===Symbol.toPrimitive) return ()=>0;
      if(k===Symbol.iterator) return function*(){};
      if(k==="addEventListener") return (ev,fn)=>{if(ev==="click")clicks.push(fn);};
      if(k==="textContent"||k==="value") return store[k]??"";
      if(k==="style"||k==="classList"||k==="dataset"||k==="firstChild"){return store[k]??=mk();}
      if(k==="length") return 0;
      if(k in store) return store[k];
      return mk();
    },
    set(_t,k,v){store[k]=v; if(k==="textContent"&&v!=null)texts.push(String(v)); return true;},
    apply(){return mk();}, construct(){return mk();},
  });
}
const document=mk();
const ctx={document,console,Math,Date,JSON,Set,Object,Array,Number,String,
  Option:function(){return mk();},innerWidth:1400,innerHeight:900,
  createTextNode:null};
document.createTextNode=(s)=>{texts.push(String(s)); return mk();};
Object.defineProperty(ctx,"document",{value:new Proxy(document,{get(t,k){
  if(k==="createTextNode") return (s)=>{texts.push(String(s)); return mk();};
  return t[k];}})});
vm.createContext(ctx);
const m=src.match(/<script>([\s\S]*)<\/script>/);
vm.runInContext(m[1],ctx);
// 단추를 전부 한 번씩 눌러 본다(시장·주체·기준·기간). 누르다 생긴 처리기는 안 누른다.
for(const fn of clicks.slice()) fn({});
console.log(JSON.stringify(texts));
"""


def _page(tpl: str, data: dict, tmp_path: Path) -> list[str]:
    html = (REPO / "scripts" / "templates" / tpl).read_text("utf-8")
    page = tmp_path / tpl
    page.write_text(html.replace("__DATA__", json.dumps(data, ensure_ascii=False)), "utf-8")
    h = tmp_path / "harness.js"
    h.write_text(HARNESS, "utf-8")
    p = subprocess.run(["node", str(h), str(page)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr[-1500:]
    return json.loads(p.stdout)


def _viewer_payload(asof: str = "2026-11-02") -> dict:
    n = 130
    dates = [f"2026-01-{i:03d}" for i in range(n - 1)] + [asof]
    actors = ("inst", "forgn", "indiv", "etc", "tv")
    flows = {m: {"섹터A": {a: [1.0] * n for a in actors}} for m in ("거래소", "코스닥")}
    win = {"inst": 5.0, "forgn": -5.0, "indiv": 0.0, "etc": 0.0, "tv": 50.0,
           "invtrt": 1.0, "penfnd_etc": 1.0, "ret": 2.0}
    names = {"000001": {"name": "담은종목", "sector": "섹터A", "market": "거래소", "cap": 100.0,
                        "win": {w: dict(win) for w in ("5", "20", "60", "120")}},
             "000002": {"name": "던진종목", "sector": "섹터A", "market": "거래소", "cap": 100.0,
                        "win": {w: dict(win, inst=-5.0) for w in ("5", "20", "60", "120")}}}
    return {"dates": dates, "sectors": ["섹터A"], "n_by_sector": {"거래소": {"섹터A": 2}, "코스닥": {}},
            "markets": ["거래소", "코스닥"], "flows": flows,
            "detail": {m: {"섹터A": {"invtrt": [1.0] * n}} for m in ("거래소", "코스닥")},
            "ret": {m: {"섹터A": [0.1] * n} for m in ("거래소", "코스닥")},
            "retw": {m: {"섹터A": [1.0] * n} for m in ("거래소", "코스닥")},
            "iret": {m: {} for m in ("거래소", "코스닥")},
            "cap": {m: {"섹터A": 1000.0} for m in ("거래소", "코스닥")}, "cap_by_month": {},
            "names": names, "n_names": 2, "finalized": True,
            "detail_labels": {"invtrt": "투신"}}


@node
def test_viewer_renders_stock_lists_from_the_payload_shape(tmp_path):
    texts = _page("sector_flow.html", _viewer_payload(), tmp_path)
    assert "담은종목" in texts and "던진종목" in texts


@node
def test_viewer_says_why_the_stock_list_is_empty_outside_presets(tmp_path):
    texts = _page("sector_flow.html", _viewer_payload(), tmp_path)
    assert any("5·20·60·120일 기간에만" in t for t in texts), "1일·전체 단추를 눌렀는데 설명이 없다"


@node
def test_viewer_marks_a_stale_report(tmp_path):
    assert any("일 지난 리포트" in t for t in _page("sector_flow.html", _viewer_payload("2026-01-05"), tmp_path))


def test_viewer_does_not_claim_krx_index_returns():
    """수익률은 자체 바구니다(`retOf`). 설명만 옛 계산을 말하고 있었다."""
    html = (REPO / "scripts" / "templates" / "sector_flow.html").read_text("utf-8")
    assert "KRX 업종지수 실측" not in html and "KRX지수" not in html


def _numbers_data(combined: bool = True, asof: str = "2026-11-02") -> dict:
    row = {"sector": "섹터A", "n_all": 20, "thin": False, "G": 0.5, "G_pass": True,
           "per": {"20": 0.5}, "pass_n": 1, "seen": 1}
    blocks = {f"{w}|{m}": {"from": "2026-10-02", "to": asof, "k": 1.0, "b": 0.0, "t": 2.0, "rows": []}
              for w in (20, 60) for m in ("전체", "코스닥")}
    return {"asof": asof, "dates": ["2025-10-10", asof], "finalized": True, "blocks": blocks,
            "combined": {"전체": {"windows": [20, 60], "rows": [row]}} if combined else {}}


@node
def test_numbers_pass_count_is_over_all_windows(tmp_path):
    assert "1/2" in _page("sector_numbers.html", _numbers_data(), tmp_path)


@node
def test_numbers_explains_an_empty_combined_table_and_staleness(tmp_path):
    texts = _page("sector_numbers.html", _numbers_data(combined=False, asof="2026-01-05"), tmp_path)
    assert any("종합이 없다" in t for t in texts)
    assert any("일 지난 리포트" in t for t in texts)


@pytest.mark.parametrize("tpl", ["sector_flow.html", "sector_numbers.html"])
def test_pages_declare_encoding_and_do_not_block_on_font_servers(tpl):
    """HTTP 로 서빙하면 한글이 깨지고, 폰트 서버가 응답하지 않는 망에서는 표가 안 그려졌다."""
    html = (REPO / "scripts" / "templates" / tpl).read_text("utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert '<meta charset="utf-8">' in html[:200]
    for link in re.findall(r'<link rel="stylesheet"[^>]*>', html):
        assert 'media="print"' in link, link


def test_numbers_template_passes_the_static_checks_of_the_verifier(monkeypatch):
    """verify_report 는 템플릿 소스에서 셀 수를 센다 — 리포트를 만들어 봐야 나오는 실패를 여기서 잡는다."""
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import verify_report as vr
    finally:
        sys.path.pop(0)
    html = (REPO / "scripts" / "templates" / "sector_numbers.html").read_text("utf-8")
    fails: list[str] = []
    monkeypatch.setattr(vr, "FAILS", fails)
    vr.layer_c(_numbers_data(), html)
    assert not [f for f in fails if "종합표" in f or "본표" in f], fails


def test_script_json_cannot_close_the_script_tag():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        from sector_flow import script_json
    finally:
        sys.path.pop(0)
    text = script_json({"name": "</script><b>"})
    assert "</script>" not in text
    assert json.loads(text) == {"name": "</script><b>"}


def test_db_check_without_a_db_is_a_failure_not_a_pass(tmp_path):
    """건너뛰고 '전부 통과' 를 내면 DB 대조를 한 것처럼 읽힌다."""
    (tmp_path / "numbers.html").write_text('const D = {"blocks": {}};\n', "utf-8")
    (tmp_path / "payload.json").write_text('{"dates": ["2026-11-02"], "sectors": []}', "utf-8")
    env = {k: v for k, v in os.environ.items() if k != "KR_QUANT_DB"}
    p = subprocess.run([sys.executable, str(REPO / "scripts" / "verify_report.py"),
                        "--dir", str(tmp_path), "--db-check"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode != 0
    assert "[FAIL] F · --db-check 인데 DB 접속정보가 있는가" in p.stdout


@node
def test_numbers_drops_a_sector_with_no_stocks_in_the_market(tmp_path):
    """그 시장에 종목이 없는 섹터가 `+0.00` 으로 찍혀 조용한 섹터로 읽혔다(화면 `absent_row` 와 같은 기준)."""
    d = _numbers_data()
    live = {"sector": "있는섹터", "n_all": 5, "cap": 10.0, "thin": True, "G": None, "inst": 1.0, "accel": 0.1,
            "top": {"buy": [], "sell": []}}
    for b in d["blocks"].values():
        b["rows"] = [live, {"sector": "없는섹터", "n_all": 0, "cap": 0.0, "thin": True, "G": None,
                            "inst": 0.0, "accel": 0.0}]
    d["combined"]["전체"]["rows"].append({"sector": "없는섹터", "n_all": 0, "G": None, "per": {}, "pass_n": 0})
    texts = _page("sector_numbers.html", d, tmp_path)
    assert "있는섹터" in texts, "픽스처가 행을 하나도 안 그린다 — 이 검사가 헛돈다"
    assert "없는섹터" not in texts


@node
def test_viewer_drops_a_sector_with_no_stocks_in_the_market(tmp_path):
    d = _viewer_payload()
    n = len(d["dates"])
    d["sectors"].append("없는섹터")
    for m in d["markets"]:
        d["flows"][m]["없는섹터"] = {a: [0.0] * n for a in ("inst", "forgn", "indiv", "etc", "tv")}
    texts = _page("sector_flow.html", d, tmp_path)
    assert "섹터A" in texts
    assert "없는섹터" not in texts


# ── 웹 두 장도 TUI 와 같은 말을 한다 ─────────────────────────────────────────

def _ev(tpl: str) -> dict:
    html = (REPO / "scripts" / "templates" / tpl).read_text("utf-8")
    m = re.search(r"const EV=(\{.*?\});", html)
    assert m, f"{tpl} 에 증거 표(const EV)가 없다"
    return json.loads(m.group(1))


@pytest.mark.parametrize("tpl", ["sector_numbers.html", "sector_flow.html"])
def test_web_evidence_badges_match_the_tui(tpl):
    """웹의 증거 표지는 TUI(`flow_view.EVIDENCE`)와 **같은 값**이어야 한다.

    주입: 한쪽에서 `accel` 을 `[측정]` 으로 바꾸면 실패한다 — 두 화면이 같은 열에 다른 지위를 단다.
    """
    sys.path.insert(0, str(REPO / "src"))
    from sectorflow.tui.flow_view import BADGES, EVIDENCE

    ev = _ev(tpl)
    assert ev, "증거 표가 비었다"
    for k, badge in ev.items():
        assert badge in BADGES, f"{tpl}: 모르는 표지 {badge!r}"
        assert EVIDENCE.get(k) == badge, f"{tpl}: {k} 표지가 TUI({EVIDENCE.get(k)})와 다르다({badge})"


def _const(tpl: str, name: str):
    html = (REPO / "scripts" / "templates" / tpl).read_text("utf-8")
    m = re.search(r"const " + name + r"=(\{.*?\});", html)
    return json.loads(m.group(1)) if m else None


def test_web_evidence_scope_is_the_tuis():
    """검정 범위 표(`EV_TESTED`·`EV_TESTED_SCORE`)와 범위 말 — 수치표가 TUI 와 같은 표·같은 글자를 쓴다(리뷰 M2)."""
    sys.path.insert(0, str(REPO / "src"))
    from sectorflow.tui import flow_view as FV
    want = {a: {k: list(w) for k, w in v.items()} for a, v in FV.EV_TESTED.items()}
    assert _const("sector_numbers.html", "EV_TESTED") == want
    assert _const("sector_numbers.html", "EV_TESTED_SCORE") == {k: list(v) for k, v in FV.EV_TESTED_SCORE.items()}
    html = (REPO / "scripts" / "templates" / "sector_numbers.html").read_text("utf-8")
    for text in (FV.MARKET_NOTE, FV.EV_TRUST_NOTE, "앞날 검정은 "):
        assert text in html, text


def test_viewer_evidence_scope_is_the_same_table():
    """뷰어(sector_flow.html)도 같은 `EV_TESTED` 와 같은 범위 말을 쓴다 — 세 화면이 같은 열에 같은 범위를 말한다.

    주입: 한 화면의 표나 범위 말을 바꾸면 실패한다.
    """
    sys.path.insert(0, str(REPO / "src"))
    from sectorflow.tui import flow_view as FV
    got = _const("sector_flow.html", "EV_TESTED")
    assert got is not None, "뷰어에 EV_TESTED 가 없다"
    assert got == {a: {k: list(w) for k, w in v.items()} for a, v in FV.EV_TESTED.items()}
    html = (REPO / "scripts" / "templates" / "sector_flow.html").read_text("utf-8")
    assert f'"[측정] · {FV.MARKET_NOTE}"' in html and '"[측정] · 앞날 검정은 "' in html


def test_numbers_page_defaults_to_flow_and_hides_g_behind_the_exploration_label():
    """수치표의 기본 정렬은 가속이고, G 와 종합 G 는 탐색 표지가 달린 접힌 칸 안에 있다.

    주입: `sortKey = "G"` 로 되돌리거나 탐색 칸을 `open` 으로 두면 실패한다.
    """
    html = (REPO / "scripts" / "templates" / "sector_numbers.html").read_text("utf-8")
    assert re.search(r'let sortKey = "accel"', html), "기본 정렬이 가속이 아니다"
    m = re.search(r'<details class="explore" id="explore"(.*?)>(.*?)</details>', html, re.S)
    assert m and "open" not in m.group(1), "탐색 칸이 없거나 처음부터 열려 있다"
    assert "탐색 — 검정 결과 앞날 증거 없음(G 판정 불가)" in m.group(2)
    assert 'id="ctbl"' in m.group(2), "종합 G 표가 탐색 칸 밖에 있다"
    assert html.index('id="tbl"') < html.index('id="ctbl"'), "종합 G 표가 수급 표보다 위에 있다"


@pytest.mark.parametrize("tpl", ["sector_numbers.html", "sector_flow.html"])
def test_pages_carry_no_stale_or_timing_language(tpl):
    """낡은 상관값·타이밍 은유·"미반응" 지표가 다시 들어오지 않는다."""
    html = (REPO / "scripts" / "templates" / tpl).read_text("utf-8")
    for bad in ("0.94~1.00", "막 풀리기 시작", "정렬되는 순간", "60일 이상에서 보라", "미반응순",
                "아직 안 간 섹터", '"resid"', "검증된 적이 없다", "해석·추천 없음 — 숫자만", "가속도 %p",
                "비중%p", "던진 종목"):
        assert bad not in html, f"{tpl} 에 {bad!r} 가 남았다"


@pytest.mark.parametrize("tpl,other", [("sector_numbers.html", "viewer.html"),
                                       ("sector_flow.html", "numbers.html")])
def test_pages_link_each_other_and_the_index(tpl, other):
    html = (REPO / "scripts" / "templates" / tpl).read_text("utf-8")
    assert f'href="{other}"' in html and 'href="../"' in html, f"{tpl} 에 다른 화면 링크가 없다"


@node
def test_pages_lead_with_the_report_day_and_spell_out_the_chip(tmp_path):
    """제목 아래 첫 줄은 **기준일**이고, 칩의 뜻이 title 이 아니라 본문 글로 있다."""
    for tpl, data in (("sector_numbers.html", _numbers_data()), ("sector_flow.html", _viewer_payload())):
        texts = _page(tpl, data, tmp_path)
        assert "기준일 2026-11-02" in texts, f"{tpl}: 기준일이 안 보인다"
        assert any(t.startswith("잠정:") for t in texts), f"{tpl}: 칩 설명 글이 없다"


@node
def test_viewer_driver_names_follow_the_tui_rule(tmp_path):
    """뷰어 섹터 이름 아래 종목 = TUI 견인주(`State._leads`)와 같은 규칙 — **금액** 순.

    회귀: 뷰어가 시총 대비(%p)로 줄세워 시총 10억짜리 스팩이 섹터 1위로 떴다(금융: 'KB제34호스팩'
    vs 화면 'SK +5,426억'). 주입: `drivers` 를 `a` 기준 정렬로 되돌리면 실패한다.
    """
    sys.path.insert(0, str(REPO / "src"))
    from sectorflow.tui.flow_view import State

    p = _viewer_payload()
    win = {"forgn": 0.0, "indiv": 0.0, "etc": 0.0, "tv": 50.0, "invtrt": 0.0, "penfnd_etc": 0.0,
           "ret": 0.0}
    p["names"] = {
        "000010": {"name": "큰종목", "sector": "섹터A", "market": "거래소", "cap": 1_000_000.0,
                   "win": {w: dict(win, inst=500.0) for w in ("5", "20", "60", "120")}},
        "000020": {"name": "스팩", "sector": "섹터A", "market": "거래소", "cap": 10.0,
                   "win": {w: dict(win, inst=5.0) for w in ("5", "20", "60", "120")}},
        "000030": {"name": "중간종목", "sector": "섹터A", "market": "거래소", "cap": 5_000.0,
                   "win": {w: dict(win, inst=50.0) for w in ("5", "20", "60", "120")}},
    }
    texts = _page("sector_flow.html", p, tmp_path)
    tui = State({"asof": "2026-11-02", "names": p["names"],
                 "blocks": {"20|전체": {"rows": [{"sector": "섹터A", "inst": 555.0}]},
                            "20|거래소": {"rows": []}}})
    want = " · ".join(t["name"] for t in tui._leads()["섹터A"]["buy"][:2])
    assert want == "큰종목 · 중간종목", want
    assert want in texts, f"뷰어 이름 줄이 TUI 와 다르다: {[t for t in texts if '·' in t][:5]}"


# ── 오늘 — 수치표 맨 위 패널은 TUI 첫 화면(`banner_lines`)과 같은 사실을 적는다 ──


def _today_numbers(final: bool = False) -> dict:
    sys.path.insert(0, str(REPO / "tests"))
    from test_tui_flow_view import _today_block

    D = _numbers_data()
    for w in (5, 20):
        for m in ("전체", "거래소", "코스닥"):
            D["blocks"][f"{w}|{m}"] = {"from": "2026-10-02", "to": "2026-11-02", "k": 1.0, "b": 0.0,
                                       "t": 2.0, "rows": []}
    D["blocks"]["20|전체"]["rows"] = [{"sector": "가", "inst": 300.0, "n_all": 20, "cap": 1.0,
                                       "top": {"buy": [], "sell": []}}]
    D["blocks"]["5|전체"]["rows"] = [{"sector": "가", "inst": -50.0, "n_all": 20, "cap": 1.0,
                                      "top": {"buy": [], "sell": []}}]
    D["today"] = _today_block(["전기/전자", "건설", "부동산", "IT 서비스"])
    D["change"] = {"prev_date": "2026-10-30", "window": 20, "top_n": 5, "top": {"전체": {
        "inst": {"entered": ["건설"], "exited": ["보험"]}, "invpen": {"entered": [], "exited": []}}}}
    if final:
        D["final"] = True
        D["final_diff"] = {"base_final": False, "base_built": "x", "tol": 0.05, "n_cells": 300,
                           "n_changed": 1, "market_totals": [],
                           "changed": [{"market": "거래소", "sector": "건설", "actor": "inst", "win": 1,
                                        "old": 10.0, "new": 12.5, "diff": 2.5}]}
    return D


@node
def test_numbers_today_panel_says_what_the_terminal_banner_says(tmp_path):
    """수치표 '오늘' 패널 = TUI 첫 화면의 사실 — 시장 4주체(+%ile)·상위/하위·극단·직전 대비·반전·수익률 라벨.

    주입: 패널의 KRX 칸에 자체 바구니 값을 넣거나 하위를 상위로 바꾸면 실패한다.
    """
    texts = _page("sector_numbers.html", _today_numbers(), tmp_path)
    joined = "\n".join(texts)
    for want in ("기관 상위", "투신+연기금 하위", "기관 ≥95", "기관 ≤5", "들어옴 건설", "나감 보험",
                 "변동 없음", "(97)", "+123", "−33", "−6.02%", "+4.00%", "−4.37%", "+5.50%",
                 "전일 20:00→당일 수집 시각", "정규장", "가 +350→−50", "(잠정) 기관 ≥95"):
        assert want in joined, f"오늘 패널에 {want!r} 가 없다"
    # 상위·하위 순서 — TUI 와 같은 금액 순
    i = texts.index("기관 상위")
    assert texts[i + 1].startswith("전기/전자"), texts[i:i + 6]
    j = texts.index("기관 하위")
    assert texts[j + 1].startswith("IT 서비스"), texts[j:j + 6]


@node
def test_numbers_today_panel_lists_the_provisional_to_final_changes(tmp_path):
    texts = "\n".join(_page("sector_numbers.html", _today_numbers(final=True), tmp_path))
    assert "잠정→확정 변경: 1칸 / 300칸" in texts
    assert "전일 20:00→당일 20:00" in texts and "(잠정) 기관" not in texts
    assert "거래소/건설 기관 1일 +10.00 → +12.50 (+2.50)" in texts


@node
def test_numbers_today_panel_says_when_the_report_has_no_today(tmp_path):
    assert any("'오늘' 블록이 없다" in t for t in _page("sector_numbers.html", _numbers_data(), tmp_path))


@node
def test_numbers_gap_chip_only_above_threshold(tmp_path):
    D = _today_numbers()
    assert not any("금액 근사 오차 큼" in t for t in _page("sector_numbers.html", D, tmp_path))
    D["today"]["market"]["코스닥"]["approx_gap"]["pct"] = 6.1
    assert any("금액 근사 오차 큼(코스닥 6.1%)" in t for t in _page("sector_numbers.html", D, tmp_path))


@node
def test_viewer_one_day_preset_lists_stocks_with_trust_and_pension(tmp_path):
    """뷰어 1일 구간에서도 종목이 나온다(payload names[code].win["1"]), 종목마다 투신·연기금 금액을 단다.

    페이로드를 하루짜리로 줄여 첫 화면이 곧 1일 구간이 되게 한다(하네스가 단추를 누르는 순서와 무관하게).
    주입: 종목 줄에서 투신·연기금을 빼면 실패한다.
    """
    p = _viewer_payload()
    p["dates"] = p["dates"][-1:]
    for tbl in ("flows", "detail", "ret", "retw"):
        for m in p[tbl].values():
            for sec in m.values():
                if isinstance(sec, dict):
                    for k in sec:
                        sec[k] = sec[k][-1:]
                else:
                    pass
    for m in ("ret", "retw"):
        p[m] = {mk: {s: v[-1:] for s, v in d.items()} for mk, d in p[m].items()}
    for r in p["names"].values():
        r["win"] = {"1": dict(r["win"]["5"], invtrt=7.0, penfnd_etc=-3.0)}
    texts = _page("sector_flow.html", p, tmp_path)
    assert "1거래일 · 2종목" in texts and "담은종목" in texts
    assert "투신 " in texts and "연기금 " in texts and "+7" in texts and "−3" in texts


@node
def test_numbers_today_panel_adds_the_dominant_actor_revision_size_and_base_status(tmp_path):
    """리뷰 2차 — 최대 순매매 주체(외국인 −28,336억)·잠정 수정 폭·직전 리포트의 지위(잠정·검증 실패)·20일 누적 기준."""
    D = _today_numbers()
    for m in D["today"]["market"].values():
        m["net"] = dict(m["net"], forgn={"v": -28336.0, "pct": 17.0})
    D["revision_stats"] = {"n_dates": 4, "rel_median_pct": 7.5, "flips": 2, "n_cells": 200}
    D["change"]["prev_verify_failed"] = True
    joined = "\n".join(_page("sector_numbers.html", D, tmp_path))
    for want in ("외국인(최대 순매매 주체) 상위", "외국인(최대 순매매 주체) 하위", "(잠정) 외국인(최대 순매매 주체) ≥95",
                 "최근 4일 잠정→확정: 섹터 칸 중앙 8%, 부호 뒤집힘 2회",
                 "직전 리포트(2026-10-30·잠정·검증 실패) 대비 — 20일 누적 기준"):
        assert want in joined, f"오늘 패널에 {want!r} 가 없다"


def test_numbers_page_states_no_dated_numbers_in_its_notes():
    """리뷰 11 — 템플릿에 특정일 숫자(11-06 −4.37% vs −6.02%, "2%p 넘게")를 박지 않는다."""
    html = (REPO / "scripts" / "templates" / "sector_numbers.html").read_text("utf-8")
    for bad in ("11-06", "−4.37", "2%p 넘게", "미반응"):
        assert bad not in html, bad


@node
def test_numbers_today_panel_counts_the_unclassified_members(tmp_path):
    """(미분류)가 남아 있으면 오늘 패널이 그 수를 적는다 — 섹터가 아니라 마스터 결함이라는 말과 함께."""
    D = _today_numbers()
    D["n_by_sector"] = {"거래소": {"(미분류)": 3}, "코스닥": {"(미분류)": 2}}
    assert any("(미분류) 5종목" in t for t in _page("sector_numbers.html", D, tmp_path))
    D["n_by_sector"] = {"거래소": {"금융": 9}}
    assert not any("(미분류)" in t and "종목" in t for t in _page("sector_numbers.html", D, tmp_path))

@pytest.mark.parametrize("tpl", ["sector_flow.html", "sector_numbers.html"])
def test_rebuilt_report_says_so_in_the_page_chip(tpl, tmp_path):
    """재구성 리포트(KR_QUANT_ASOF)를 페이지가 '다음날 아침 확정치를 확인한 뒤 다시 만든 리포트' 로만 말하면 안 된다."""
    make = _viewer_payload if tpl == "sector_flow.html" else _numbers_data
    plain = _page(tpl, dict(make(), final=True), tmp_path)
    rebuilt = _page(tpl, dict(make(), final=True, rebuilt=True), tmp_path)
    assert "마감 · 확정 · 재구성" in rebuilt, "재구성 리포트의 칩 글자가 재구성을 안 말한다"
    assert any("그날 저녁 게시본이 아니다" in t for t in rebuilt)
    assert not any("재구성" in t for t in plain)
