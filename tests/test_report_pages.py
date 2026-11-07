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
