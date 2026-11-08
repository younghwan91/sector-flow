"""웹 흐름 뷰어(`viewer.html`)가 TUI·수치표와 **같은 문턱·같은 표지**로 말하는가.

페이지 스크립트를 node 로 실제로 돌린다(하네스는 ``test_report_pages`` 것을 쓴다).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "src"))

from test_report_pages import _page, _viewer_payload, node  # noqa: E402

from sectorflow.tui.flow_view import B_MEAS, B_NEG, B_NONE, BADGES, THIN_MIN  # noqa: E402

HTML = REPO / "scripts" / "templates" / "sector_flow.html"


#: 작은 가짜 DOM — 요소 트리·클래스·style·scrollLeft 를 **남긴다**. `test_report_pages` 의 하네스는 글자만
#: 모아서 "회색인가·척도에서 빠졌나·오른쪽 끝으로 밀었나" 를 볼 수 없었다. 단추는 `[[seg id, key], …]` 순서로 누른다.
DOM_HARNESS = r"""
const fs=require("fs"), vm=require("vm");
const src=fs.readFileSync(process.argv[2],"utf8");
const actions=JSON.parse(fs.readFileSync(process.argv[3],"utf8"));
class Text{constructor(t){this.textContent=String(t);}}
class El{
  constructor(tag){this.tag=tag;this.children=[];this.style={};this.attrs={};this.dataset={};
    this.className="";this._t="";this.ls={};this.scrollLeft=0;this.clientWidth=0;}
  get scrollWidth(){return 1234;}
  get textContent(){return this._t+this.children.map(c=>c.textContent).join("");}
  set textContent(v){this._t=String(v);this.children=[];}
  get firstChild(){ if(!(this.children[0] instanceof Text)) this.children.unshift(new Text("")); return this.children[0]; }
  get classList(){const e=this; return {add(c){e.className+=" "+c;}};}
  append(...xs){xs.forEach(x=>this.children.push(typeof x==="string"?new Text(x):x));}
  replaceChildren(...xs){this.children=[];this._t="";this.append(...xs);}
  setAttribute(k,v){this.attrs[k]=String(v);} getAttribute(k){return this.attrs[k];}
  addEventListener(ev,fn){(this.ls[ev]??=[]).push(fn);}
}
const byId=new Map();
const document={
  getElementById(id){ if(!byId.has(id)) byId.set(id,new El("div")); return byId.get(id); },
  createElement(t){return new El(t);}, createElementNS(_n,t){return new El(t);},
  createTextNode(t){return new Text(t);},
};
function Option(t,v){const e=new El("option"); e.textContent=t; e.value=String(v); return e;}
const ctx={document,Option,console,Math,Date,JSON,Set,Map,Object,Array,Number,String,innerWidth:1400,innerHeight:900};
vm.createContext(ctx);
vm.runInContext(src.match(/<script>([\s\S]*)<\/script>/)[1],ctx);
for(const [id,k] of actions){
  const b=document.getElementById(id).children.find(x=>x.dataset&&x.dataset.k===String(k));
  if(!b) throw new Error("단추 없음 "+id+" "+k);
  (b.ls.click||[]).forEach(fn=>fn({}));
}
const ser=e=>e instanceof Text?{t:e.textContent}:{tag:e.tag,cls:e.className,text:e._t,bg:e.style.background||"",
  attrs:e.attrs,scrollLeft:e.scrollLeft,scrollWidth:e.scrollWidth,kids:e.children.map(ser)};
const out={}; for(const id of ["hm","rows","tbl","line","rk-title","ev-buy-amt","ev-buy-trust","rangeinfo"])
  out[id]=ser(document.getElementById(id));
console.log(JSON.stringify(out));
"""


def _render(p: dict, tmp_path: Path, actions=()) -> dict:
    """페이지를 가짜 DOM 으로 그리고, 단추를 누른 뒤의 주요 칸(히트맵·랭킹·표·추이)을 트리로 돌려준다."""
    import subprocess

    page = tmp_path / "viewer.html"
    page.write_text(_html().replace("__DATA__", json.dumps(p, ensure_ascii=False)), "utf-8")
    (tmp_path / "h.js").write_text(DOM_HARNESS, "utf-8")
    (tmp_path / "a.json").write_text(json.dumps(list(actions)), "utf-8")
    r = subprocess.run(["node", str(tmp_path / "h.js"), str(page), str(tmp_path / "a.json")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout)


def _walk(n: dict):
    yield n
    for k in n.get("kids", []):
        yield from _walk(k)


def _text(n: dict) -> str:
    return n["t"] if "t" in n else n["text"] + "".join(_text(k) for k in n["kids"])


def _heat_labels(out: dict) -> list[str]:
    return [_text(n) for n in _walk(out["hm"]) if "hmlab" in n.get("cls", "") and _text(n)]


def _rank_rows(out: dict) -> list[tuple[str, bool]]:
    rows = [n for n in out["rows"]["kids"] if "row" in n.get("cls", "").split()]
    return [(_text(next(k for k in _walk(r) if k.get("cls") == "name")), "thin" in r["cls"].split())
            for r in rows]


def _html() -> str:
    return HTML.read_text("utf-8")


def _with_sectors(p: dict, counts: dict[str, int], scale: dict[str, float]) -> dict:
    """섹터마다 종목 ``counts[s]`` 개(거래소, 5·20·60·120일 값 있음)와 흐름 ``scale[s]`` 를 싣는다."""
    n = len(p["dates"])
    actors = ("inst", "forgn", "indiv", "etc", "tv")
    p["sectors"] = list(counts)
    p["n_by_sector"] = {"거래소": dict(counts), "코스닥": {}}
    for m in p["markets"]:
        p["flows"][m] = {s: {a: [scale[s]] * n for a in actors} for s in counts}
        p["cap"][m] = {s: 1000.0 for s in counts}
        p["detail"][m] = {s: {"invtrt": [1.0] * n} for s in counts}
        p["ret"][m] = {s: [0.1] * n for s in counts}
        p["retw"][m] = {s: [1.0] * n for s in counts}
    win = {"inst": 5.0, "forgn": -5.0, "indiv": 0.0, "etc": 0.0, "tv": 50.0,
           "invtrt": 1.0, "penfnd_etc": 1.0, "ret": 2.0}
    p["names"] = {f"{s}{i:03d}": {"name": f"{s}종목{i}", "sector": s, "market": "거래소", "cap": 100.0,
                                  "win": {w: dict(win) for w in ("5", "20", "60", "120")}}
                  for s, k in counts.items() for i in range(k)}
    p["n_names"] = len(p["names"])
    return p


def test_viewer_shares_the_thin_threshold_and_badge_set_with_the_tui():
    """뷰어의 얇은 섹터 문턱·증거 표지 다섯은 TUI(`flow_view`) 것과 같다. 범례가 다섯을 다 적는다.

    회귀: 범례가 [측정]·[앞날 증거 없음] 둘만 설명해서 종목 투신·연기금의 [음의 순위 정보·쓸 수 없음] 이
    뷰어에 없었다. 주입: 범례에서 표지 하나를 빼면 실패한다.
    """
    html = _html()
    m = re.search(r"const THIN_MIN=(\d+);", html)
    assert m and int(m.group(1)) == THIN_MIN, "뷰어 얇은 섹터 문턱이 TUI 와 다르다"
    leg = re.search(r'<p class="evleg">(.*?)</p>', html, re.S)
    assert leg, "증거 표지 범례가 없다"
    for b in BADGES:
        assert f"<b>{b}</b>" in leg.group(1), f"범례에 {b} 가 없다"
    names = json.loads(re.search(r"const EV_NAME=(\{.*?\});", html).group(1))
    assert names["trust"][0] == B_NEG and names["trust"][1] == [20]


def test_viewer_does_not_say_the_numbers_page_dropped_x():
    """'미반응' 설명이 사실과 맞다 — 수치표는 x 를 내리지 않았다(표지를 달고 탐색 쪽에 둔다)."""
    html = _html()
    assert "수치표에서도 같은 이유로 내렸다" not in html
    assert "[≈섹터 20일 반전: x]" in html


@node
def test_viewer_sinks_thin_sectors_and_greys_them(tmp_path):
    """얇은 섹터(`~`, 10종목 미만)는 값이 가장 커도 **맨 아래**이고 `~` 를 단다 — TUI·수치표와 같다.

    회귀: 1일 가속 2등이 부동산(3종목), 20일 14등이 출판/매체복제(2종목)였다.
    주입: `order` 정렬에서 `thin(a)-thin(b)` 를 빼면 실패한다.
    """
    p = _with_sectors(_viewer_payload(), {"얇은": 3, "두꺼운": 30, "보통": 12},
                      {"얇은": 500.0, "두꺼운": 5.0, "보통": 1.0})
    texts = _page("sector_flow.html", p, tmp_path)
    # 첫 렌더의 히트맵 이름표(맨 처음 그려지는 섹터 이름 목록)로 순서를 본다.
    labs = [t for t in texts if t in ("얇은~", "두꺼운", "보통")][:3]
    assert labs == ["두꺼운", "보통", "얇은~"], labs
    assert "~" in texts, "랭킹 줄 이름 옆에 ~ 가 없다"


@node
def test_viewer_has_no_accel_on_one_day(tmp_path):
    """1일 구간에는 가속이 없다 — TUI 1일 창처럼 `—` 이고 임펄스(금액) 순이다.

    페이로드를 하루로 줄여 모든 렌더가 1일이 되게 한다(하네스가 '가속' 단추도 누른다).
    주입: `M()` 이 1일에도 `metric` 을 돌려주게 되돌리면 실패한다.
    """
    p = _viewer_payload()
    p["dates"] = p["dates"][-1:]
    for tbl in ("flows", "detail"):
        for m in p[tbl].values():
            for sec in m.values():
                for k in sec:
                    sec[k] = sec[k][-1:]
    for tbl in ("ret", "retw"):
        p[tbl] = {mk: {s: v[-1:] for s, v in d.items()} for mk, d in p[tbl].items()}
    texts = _page("sector_flow.html", p, tmp_path)
    assert not any("가속(%p) 큰 순" in t for t in texts), "1일인데 가속 순으로 줄세웠다"
    assert any("임펄스(금액) 큰 순" in t for t in texts)
    assert any("1일은 가속(파생값)을 내지 않는다" in t for t in texts)


@node
def test_viewer_badges_are_scoped_to_the_tested_windows(tmp_path):
    """표지는 **검정한 창·주체**에만 붙는다.

    * 20일(첫 화면): 종목 투신·연기금 = [음의 순위 정보·쓸 수 없음], 기관 = [앞날 증거 없음].
    * 5일(단추): 같은 자리가 [측정] + "앞날 검정은 20일만" 이다 — 5일은 검정하지 않았다.
    주입: `evName` 이 창과 무관하게 표지를 돌려주게 하면 실패한다.
    """
    texts = _page("sector_flow.html", _viewer_payload(), tmp_path)
    assert " " + B_NEG in texts, "20일 종목 투신·연기금 표지가 없다"
    assert " " + B_NONE in texts
    assert f" {B_MEAS} · 앞날 검정은 20일만" in texts, "검정 안 한 창에 범위 표기가 없다"
    assert any(t.endswith(f"{B_MEAS} · 앞날 검정은 20·60일만") for t in texts), "기관 5일 범위 표기가 없다"



# ── 가짜 DOM 으로 그린 결과를 본다 ─────────────────────────────────────────────

def _three(tmp_path_unused=None) -> dict:
    """아홉(9종목, 흐름 최대)·열(10종목)·서른(30종목). 문턱 바로 아래·위가 같이 있다."""
    return _with_sectors(_viewer_payload(), {"아홉": 9, "열": 10, "서른": 30},
                         {"아홉": 500.0, "열": 5.0, "서른": 1.0})


def _one_day(p: dict) -> dict:
    p["dates"] = p["dates"][-1:]
    for tbl in ("flows", "detail"):
        for m in p[tbl].values():
            for sec in m.values():
                for k in sec:
                    sec[k] = sec[k][-1:]
    for tbl in ("ret", "retw"):
        p[tbl] = {mk: {s: v[-1:] for s, v in d.items()} for mk, d in p[tbl].items()}
    return p


@node
def test_thin_threshold_is_below_ten_not_at_ten(tmp_path):
    """9종목은 얇고(`~`, 맨 아래) 10종목은 아니다 — `THIN_MIN` 과 같은 `<` 다.

    주입: `nOf(sec)<THIN_MIN` 을 `<=` 로 바꾸면 10종목 섹터까지 `~` 가 붙어 실패한다.
    """
    out = _render(_three(), tmp_path)
    assert _heat_labels(out) == ["열", "서른", "아홉~"], _heat_labels(out)
    assert _rank_rows(out) == [("열", False), ("서른", False), ("아홉~", True)], _rank_rows(out)


@node
def test_heat_scale_and_context_lines_leave_thin_sectors_out(tmp_path):
    """얇은 섹터는 히트맵 농도의 최대와 누적 추이 회색 선에서 빠진다.

    아홉(얇음)의 흐름이 열의 100배라, 농도 최대를 아홉으로 재면 열이 거의 투명해진다(96% → 13%).
    주입: 농도 최대에 얇은 섹터를 넣거나, 회색 선에 얇은 섹터를 다시 그리면 실패한다.
    """
    out = _render(_three(), tmp_path)
    pct = [float(m.group(1)) for n in _walk(out["hm"]) if "hmc" in n.get("cls", "") and "thin" not in n["cls"]
           for m in [re.search(r"\) ([\d.]+)%", n["bg"])] if m]
    assert pct and max(pct) > 90, f"얇지 않은 섹터 농도 최대 {max(pct, default=None)}%"
    ctx = [n for n in _walk(out["line"]) if n.get("attrs", {}).get("class") == "ctxln"]
    assert len(ctx) == 1, f"회색 선 {len(ctx)}개 — 강조(열)를 빼면 얇지 않은 섹터는 서른 하나뿐이다"


@node
def test_whole_range_counts_names_from_n_by_sector(tmp_path):
    """종목 값이 없는 구간(전체·직접 지정)은 `n_by_sector` 로 센다 — 거기서도 얇은 섹터는 `~`·맨 아래다.

    주입: `n_by_sector` 로 떨어지는 길을 끊으면(아무것도 안 얇다) 실패한다.
    """
    out = _render(_three(), tmp_path, [["seg-preset", 0]])
    assert _heat_labels(out)[-1] == "아홉~", _heat_labels(out)


@node
def test_thin_count_follows_the_selected_market(tmp_path):
    """반반(거래소 6 + 코스닥 6)은 전체에서 12종목이라 얇지 않고, 거래소만 보면 6종목이라 얇다.

    주입: `nOf` 의 시장 거르기를 빼면 거래소 화면에서도 12로 세어 실패한다.
    """
    p = _with_sectors(_viewer_payload(), {"반반": 12, "서른": 30}, {"반반": 5.0, "서른": 1.0})
    for i, r in enumerate(x for x in p["names"].values() if x["sector"] == "반반"):
        if i % 2:
            r["market"] = "코스닥"
    p["n_by_sector"] = {"거래소": {"반반": 6, "서른": 30}, "코스닥": {"반반": 6}}
    assert "반반" in _heat_labels(_render(p, tmp_path))
    assert "반반~" in _heat_labels(_render(p, tmp_path, [["seg-market", "거래소"]]))


@node
def test_one_day_table_has_no_accel_and_no_badge_on_it(tmp_path):
    """1일 표의 기관 가속 열은 값이 `—` 이고 머리에 표지도 없다 — 없는 값에 증거 지위를 달지 않는다.

    주입: 1일에도 가속을 계산하거나, 1일 가속 머리에 표지를 달면 실패한다.
    """
    out = _render(_one_day(_viewer_payload()), tmp_path)
    thead, tbody = out["tbl"]["kids"]
    heads = [_text(th) for th in thead["kids"][0]["kids"]]
    assert heads[5] == "기관 가속 %p", heads[5]
    cells = [_text(tr["kids"][5]) for tr in tbody["kids"]]
    assert cells and all(c == "—" for c in cells), cells


@node
def test_badges_follow_the_tested_metric_and_market(tmp_path):
    """검정은 `전체` 시장에서, 외국인은 ÷ 섹터 시총(가속)만 했다.

    * 전체·20일 표: 기관 금액 [앞날 증거 없음](Q3d 기관 임펄스 20일), 외국인 금액 [측정].
    * 거래소 화면: 같은 기관 가속 20일이 "[측정] · 전체 시장만 검정".
    주입: 시장을 안 보거나, 금액과 가속을 같은 범위로 보면 실패한다.
    """
    out = _render(_viewer_payload(), tmp_path)
    heads = [_text(th) for th in out["tbl"]["kids"][0]["kids"][0]["kids"]]
    assert heads[1] == "기관" + B_NONE and heads[2] == "외국인" + B_MEAS, heads
    assert _text(out["rk-title"]).endswith(B_NONE), _text(out["rk-title"])
    out = _render(_viewer_payload(), tmp_path, [["seg-market", "거래소"]])
    assert _text(out["rk-title"]).endswith("[측정] · 전체 시장만 검정"), _text(out["rk-title"])


@node
def test_heatmap_opens_at_the_latest_column(tmp_path):
    """히트맵은 오른쪽 끝(최근)에서 열린다 — 폰(390px)에서 가장 옛날부터 보여 오늘이 화면 밖이었다.

    주입: `box.scrollLeft=box.scrollWidth;` 를 지우면 실패한다.
    """
    hm = _render(_three(), tmp_path)["hm"]
    assert hm["scrollLeft"] == hm["scrollWidth"] > 0, (hm["scrollLeft"], hm["scrollWidth"])
