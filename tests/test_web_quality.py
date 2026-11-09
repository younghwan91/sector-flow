"""웹·카드의 읽기·조작 품질 — 대비, 얇은 섹터 표지, 키보드, 용어, 정직 문구(2026-11-09 4회차 리뷰)."""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
TPL = REPO / "scripts" / "templates"
node = pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")


def _mod(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


WR = _mod("web_render_for_quality", REPO / "tests" / "test_web_render.py")
VR = WR.VR


def _lum(h: str) -> float:
    h = h.lstrip("#")
    r = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4      # noqa: E731
    return .2126 * f(r[0]) + .7152 * f(r[1]) + .0722 * f(r[2])


def _contrast(a: str, b: str) -> float:
    x, y = sorted((_lum(a), _lum(b)), reverse=True)
    return (x + .05) / (y + .05)


def _themes(css: str) -> list[dict]:
    """``--이름:#색`` 묶음 — :root 블록마다 하나(밝은·어두운)."""
    out = []
    for block in re.findall(r":root[^{]*\{([^}]*--ink[^}]*)\}", css):
        out.append(dict(re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{6})", block)))
    return out


@pytest.mark.parametrize("page", ["sector_numbers.html", "sector_flow.html"])
def test_small_text_colours_reach_aa_on_every_surface(page):
    themes = _themes((TPL / page).read_text("utf-8"))
    assert len(themes) >= 2, "밝은·어두운 테마를 못 찾았다"
    for t in themes:
        for fg in ("ink-2", "ink-3"):
            for bg in ("ground", "card", "sunk"):
                c = _contrast(t[fg], t[bg])
                assert c >= 4.5, f"{page} {fg} on {bg}: {c:.2f}:1"


def test_card_small_text_colours_reach_aa():
    t = _themes((TPL / "digest_card.html").read_text("utf-8"))[0]
    for fg in ("ink-2", "ink-3"):
        assert _contrast(t[fg], t["surface"]) >= 4.5, fg


def test_card_disclaimer_is_readable_on_a_phone():
    """1080 폭 카드는 폰(390)에서 0.36배로 줄어든다 — 18px 면책은 6.5px 였다."""
    css = (TPL / "digest_card.html").read_text("utf-8")
    px = float(re.search(r"footer \{[^}]*font-size:\s*(\d+)px", css).group(1))
    assert px * 390 / 1080 >= 9, px


def test_thin_rows_are_not_told_apart_by_colour_alone():
    css = (TPL / "sector_numbers.html").read_text("utf-8")
    assert not re.search(r"tr\.thin[^{]*\{[^}]*opacity", css), "얇은 섹터 행의 투명도가 대비를 다시 깎는다"


VISIBLE_STRIP = re.compile(r"/\*.*?\*/|<!--.*?-->|^\s*//[^\n]*$|^\s*\*[^\n]*$", re.S | re.M)


def _visible(path: Path) -> str:
    """주석을 지운 글 — 화면에 안 나가는 설명은 검사하지 않는다."""
    return VISIBLE_STRIP.sub(" ", path.read_text("utf-8"))


@pytest.mark.parametrize("page,words", [
    ("sector_numbers.html", ("시가총액", "백분위", "분위 ", "오늘 금액 최대", "랭킹", "기간")),
    ("sector_flow.html", ("시가총액", "백분위", "랭킹", "기간")),
    ("digest_card.html", ("시가총액", "백분위", "분위", "오늘 금액 최대")),
])
def test_web_pages_use_one_word_for_one_thing(page, words):
    text = _visible(TPL / page)
    for w in words:
        assert w not in text, f"{page} 에 '{w}' — 용어를 하나로(시총·구간·1년%ile·최대 순매매 주체)"


def test_digest_uses_one_word_for_one_thing():
    src = (REPO / "scripts" / "digest.py").read_text("utf-8")
    for w in ('"오늘 금액', "1년 분위", "오늘 금액이 가장 큰"):
        assert w not in src, w


def _help_text() -> str:
    from sectorflow.tui import flow_view as FV
    return "\n".join(d for _n, d in FV.HELP) + "\n".join(FV.HINT_DESC.values())


#: 측정하지 않은 해석·권유로 읽히는 말 — 계산만 적는다(CLAUDE.md 화면·리포트 규칙).
UNMEASURED = ("방향성이 약", "방향성이 있는", "살 수 있는", "좋아서", "3등을", "해소", "구간 누적 랭킹", "매매 판단", "위험선호)이 지배")


@pytest.mark.parametrize("where", ["help", "ledger", "numbers", "viewer", "numbers_py"])
def test_screens_do_not_claim_what_was_not_measured(where):
    if where == "help":
        text = _help_text()
    elif where == "ledger":
        from sectorflow.tui import ledger_view as LV
        text = "\n".join(LV.LIMITS) if hasattr(LV, "LIMITS") else (REPO / "src/sectorflow/tui/ledger_view.py").read_text("utf-8")
    elif where == "numbers_py":
        text = (REPO / "scripts/sector_numbers.py").read_text("utf-8").split('"""')[1]
    else:
        text = _visible(TPL / {"numbers": "sector_numbers.html", "viewer": "sector_flow.html"}[where])
    for w in UNMEASURED:
        assert w not in text, f"{where}: '{w}'"


def test_kosdaq_r2_claim_is_gone_because_nothing_checks_it():
    assert "R² 0.00" not in _help_text()


@pytest.fixture(scope="module")
def PD():
    P = WR.PT._payload()
    P["today"]["approx_gap_warn"] = 5.0
    P["today"]["krx_index"] = {}
    return P, WR.SN.build(P)


def _run(page: Path, probe: str) -> dict:
    return VR._w_run(VR._w_node(), str(page), probe)


_VIEWER_KEYS = r"""
const hm=document.getElementById("hm").children[0];
const cells=hm.children.filter(c=>(c.className||"").split(" ")[0]==="hmc");
const stop=()=>cells.filter(c=>c.tabIndex===0);
const key=(c,k)=>c.listeners.keydown[0]({key:k,preventDefault(){}});
const out={n:cells.length, stops:stop().length, label:stop()[0].attrs["aria-label"], role:hm.attrs.role};
const a=stop()[0]; a.listeners.focus[0]({}); out.tip=document.getElementById("tip").textContent;
key(a,"ArrowLeft"); out.afterLeft=stop().length; out.moved=stop()[0]!==a;
const b=stop()[0]; out.sameRow=b.dataset.r===a.dataset.r; out.colStep=+a.dataset.c-(+b.dataset.c);
key(b,"Home"); out.home=stop()[0].dataset.c;
const before=i0; const c=stop()[0]; key(c,"ArrowRight"); const d=stop()[0]; key(d,"Enter");
out.pickedAfterEnter=picked; out.i0Changed=i0!==before;
out.svgLabel=(document.getElementById("line").children[0].attrs||{})["aria-label"]||null;
__out(out);
"""


@node
def test_viewer_heatmap_is_one_tab_stop_with_arrows_and_enter(PD, tmp_path):
    P, D = PD
    folder = WR._write(tmp_path, P, D)
    o = _run(folder / "viewer.html", _VIEWER_KEYS)
    assert o["n"] >= 50, o
    assert o["stops"] == 1, f"칸마다 탭 정지면 그 아래로 못 간다: {o['stops']}"
    assert o["label"] and "~" in o["label"] and "기관" in o["label"], o["label"]
    assert o["tip"], "포커스한 칸의 값이 안 보인다"
    assert o["afterLeft"] == 1 and o["moved"] and o["sameRow"] and o["colStep"] == 1, o
    assert o["home"] == "0", o
    assert o["pickedAfterEnter"] and o["i0Changed"], "Enter 가 클릭과 같은 일을 안 한다"
    assert o["svgLabel"] and "누적 추이" in o["svgLabel"], "svg 에 이름이 없다"


_NUMBERS_SORT = r"""
const th=document.getElementById("tbl").children[0].children[0].children;
const btn=th.find(t=>t.dataset.k==="ret").children.find(c=>c.tagName==="BUTTON");
btn.listeners.click[0]({});
const th2=document.getElementById("tbl").children[0].children[0].children;
const drv=th2.find(t=>t.dataset.k==="drv");
const row=document.getElementById("tbl").children[1].children;
const cell=(r,k)=>r.children.find(c=>c.dataset.k===k);
__out({sorted:th2.find(t=>t.dataset.k==="ret").attrs["aria-sort"], drvBtn:drv.children.some(c=>c.tagName==="BUTTON"),
  noThClick:!(th2.find(t=>t.dataset.k==="ret").listeners.click),
  titles:row.map(r=>cell(r,"drv")).filter(c=>c.children.length>1).map(c=>[c.textContent,c.title]).slice(0,3),
  nullClass:cls(null), negClass:cls(-1), posClass:cls(0)});
"""


@node
def test_numbers_sort_headers_are_buttons_and_cut_cells_have_titles(PD, tmp_path):
    P, D = PD
    o = _run(WR._write(tmp_path, P, D) / "numbers.html", _NUMBERS_SORT)
    assert o["sorted"] == "descending"
    assert o["noThClick"] and not o["drvBtn"], o
    assert o["titles"], "잘릴 수 있는 칸을 못 찾았다"
    assert all(text == title for text, title in o["titles"]), o["titles"]
    assert o["nullClass"] == "dim" and o["negClass"] == "neg" and o["posClass"] == "pos"


_THIN_ROW = r"""
const row=document.getElementById("tbl").children[1].children.map(r=>[r.dataset.sector,r.className,r.children[0].textContent]);
__out({row});
"""


@node
def test_numbers_thin_sector_carries_a_tilde(PD, tmp_path):
    P, D = PD
    D2 = json.loads(json.dumps(D))
    for B in D2["blocks"].values():
        B["rows"][0]["thin"] = True
    o = _run(WR._write(tmp_path, P, D2) / "numbers.html", _THIN_ROW)
    thin = [r for r in o["row"] if r[1] == "thin"]
    assert thin and all(text == sec + "~" for sec, _c, text in thin), thin


def test_viewer_value_cell_does_not_wrap():
    css = (TPL / "sector_flow.html").read_text("utf-8")
    assert re.search(r"\.row \.val\{[^}]*white-space:nowrap", css)


def test_viewer_mobile_title_does_not_promise_the_hidden_driver_line():
    page = (TPL / "sector_flow.html").read_text("utf-8")
    assert '<span class="wideonly"> · 이름 아래 = 그 섹터의' in page
    assert re.search(r"@media \(max-width:900px\)\{[^\n]*\.wideonly\{display:none\}", page)


@pytest.fixture(scope="module")
def PD_thick():
    """종목 수를 키워 부호 줄·집중 줄이 실제로 그려지는 페이로드."""
    P = WR.PT._payload()
    P["today"]["approx_gap_warn"] = 5.0
    P["today"]["krx_index"] = {}
    for m in P["n_by_sector"]:
        for s in P["n_by_sector"][m]:
            P["n_by_sector"][m][s] = 12
    P["today"]["day_change"] = WR.SF.day_change_block(P)
    P["today"]["concentration"] = WR.SF.concentration_block(P)
    return P, WR.SN.build(P)


_TODAY = r"""
win=20; mkt="전체"; render();
__out(document.getElementById("t-grid").children.map(c=>({id:c.id||null, text:c.textContent})));
"""


@node
def test_web_today_panel_tags_extremes_like_the_terminal(PD_thick, tmp_path):
    from sectorflow.tui import flow_view as FV
    P, D = PD_thick
    cards = {c["id"]: c["text"] for c in _run(WR._write(tmp_path, P, D) / "numbers.html", _TODAY) if c["id"]}
    tags = []
    for a, _ko in FV.banner_actors(D, "전체"):
        for side in FV.today_extremes(D, "전체", a):
            tags += side
    assert tags, "극단 칸이 없으면 이 검사가 헛돈다"
    assert all("·" in t for t in tags), tags
    for t in tags:
        assert t in cards["t-ext"], t


@node
def test_web_today_panel_answers_what_changed_first(PD_thick, tmp_path):
    """카드 순서 — 시장 → 달라진 것 → 맥락 → 순위·극단(TUI 첫 화면과 같다). 점프 링크 대상이 다 있다."""
    P, D = PD_thick
    html = WR._write(tmp_path, P, D) / "numbers.html"
    ids = [c["id"] for c in _run(html, _TODAY) if c["id"]]
    assert ids.index("t-net") < ids.index("t-day") < ids.index("t-change") < ids.index("t-ret") < ids.index("t-rank"), ids
    page = html.read_text("utf-8")
    links = re.findall(r'href="#([\w-]+)"', re.search(r'<nav class="jump".*?</nav>', page, re.S).group(0))
    assert links and all(x in ids or f'id="{x}"' in page for x in links), links


@node
def test_web_layer_compares_new_today_lines_with_the_terminal(PD_thick, tmp_path):
    P, D = PD_thick
    assert WR._w_fails(P, D, WR._write(tmp_path, P, D)) == []
    cards = {c["id"] for c in _run(tmp_path / "numbers.html", _TODAY) if c["id"]}
    assert {"t-day", "t-conc"} <= cards, cards
