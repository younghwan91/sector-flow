"""'오늘 한 장' — 페이로드의 시장 일별 배열·같은 방향 순위·더 큰 날·같은 길이 횟수·최근 20일, 그리고 웹 두 장·TUI 첫 화면·
다이제스트가 **같은 요약 문장**을 쓰는가. DB·네트워크 불요(웹 그리기는 node).

값은 검증기 T 층이 다른 길로 다시 세고(``_vt_rarity_check``·``_vt_streak``), 그려진 글자는 W 층이 ``sectorflow.summary`` 와 맞춘다
(``_w_oneday``). 여기서는 손으로 짠 배열의 답, 검증기가 틀린 값을 잡는지, 세 출구가 같은 문장인지를 본다.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
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


WR = _mod("web_render_for_oneday", REPO / "tests" / "test_web_render.py")
SF, VR, PT = WR.SF, WR.VR, WR.PT


@pytest.fixture(scope="module")
def PD():
    """종목 수를 키워(10 이상) 평소 범위 밖 칸·부호 바뀐 섹터가 실제로 세어지는 페이로드."""
    P = PT._payload()
    P["today"]["krx_index"] = {}
    for m in P["n_by_sector"]:
        for s in P["n_by_sector"][m]:
            P["n_by_sector"][m][s] = 12
    P["today"]["streaks"] = SF.streak_block(P)
    P["today"]["day_change"] = SF.day_change_block(P)
    P["today"]["concentration"] = SF.concentration_block(P)
    return P, WR.SN.build(P)


def _t_fails(P: dict) -> list[str]:
    VR.FAILS.clear()
    VR.layer_t(P, None)
    return [f for f in VR.FAILS if "오늘 한 장" in f or "연속 순매매" in f]


# ── 페이로드 ────────────────────────────────────────────────────────────────

def test_rarity_counts_same_side_bigger_days_and_the_nearest_one():
    """순매수면 오늘보다 많이 산 날만, 순매도면 많이 판 날만 센다. 더 큰 날이 없으면 거리는 없다(순위 1)."""
    ser = [9.0, -8.0, 3.0, -2.0, 7.0, 1.0, 5.0]
    assert SF.day_rarity(ser) == {"rank": 3, "n": 7, "last_more": 2}, "5 보다 큰 날은 9·7 — 가까운 7 은 이틀 전"
    assert SF.day_rarity(ser[:-1] + [-5.0]) == {"rank": 2, "n": 7, "last_more": 5}, "−5 보다 많이 판 날은 −8 하나"
    assert SF.day_rarity(ser[:-1] + [10.0]) == {"rank": 1, "n": 7, "last_more": None}
    assert SF.day_rarity(ser[:-1] + [0.0])["rank"] is None, "0 은 '0 또는 미보고' — 순위를 내지 않는다"
    assert SF.day_rarity([0.0] * 6 + [4.0])["rank"] is None, "백분위가 없으면(분모 게이트) 순위도 없다"


def test_streak_times_counts_same_side_runs_that_reached_today_length():
    """오늘 길이에 닿은 **같은 방향** 연속만 센다 — 반대 방향의 같은 길이는 세지 않는다."""
    g = [100.0] * 9
    f = SF.streak_fact([5.0, 6.0, -3.0, -4.0, 2.0, 0.1, -2.0, -6.0, -7.0], g)
    assert (f["days"], f["sign"]) == (3, -1)
    assert f["times"] == 1, "순매수 2일·순매도 2일은 3에 못 닿았다 — 오늘 하나"
    f2 = SF.streak_fact([5.0, 6.0, -3.0, -4.0, 2.0, 0.1, -2.0, -6.0], g[:8])
    assert (f2["days"], f2["times"]) == (2, 2), "순매도 2일에 닿은 것은 셋째·넷째 날과 오늘 — 앞의 순매수 2일은 아니다"


def test_payload_carries_the_market_series_ranks_and_recent_days(PD):
    P, _D = PD
    T = P["today"]
    lb = T["lookback"]
    for m, M in T["market"].items():
        for a in SF.STRIP_ACTORS:
            assert len(M["series"][a]) == lb and M["series"][a][-1] == M["net"][a]["v"], (m, a)
            assert M["net"][a] | SF.day_rarity(M["series"][a]) == M["net"][a], (m, a)
    R = T["recent"]
    assert R["days"] == 20 and R["dates"] == P["dates"][-20:]
    assert R["sectors"]["전체"]["반도체"]["inst"] == SF._series(P, P["markets"], "반도체", "inst")[-20:]
    assert _t_fails(P) == []


@pytest.mark.parametrize("break_it", [
    lambda T: T["market"]["전체"]["net"]["inst"].update(rank=T["market"]["전체"]["net"]["inst"]["rank"] + 1),
    lambda T: T["market"]["거래소"]["net"]["forgn"].update(last_more=(T["market"]["거래소"]["net"]["forgn"]["last_more"] or 0) + 1),
    # 오늘 값과의 대소는 그대로 두고 옛날 하루만 바꾼다 — 순위·%ile 은 그대로라 배열 대조만 잡을 수 있다.
    lambda T: T["market"]["코스닥"]["series"]["indiv"].__setitem__(0, T["market"]["코스닥"]["series"]["indiv"][0] + (
        50.0 if T["market"]["코스닥"]["series"]["indiv"][0] > T["market"]["코스닥"]["series"]["indiv"][-1] else -50.0)),
    lambda T: T["streaks"]["market"]["전체"]["indiv"].update(times=(T["streaks"]["market"]["전체"]["indiv"]["times"] or 0) + 1),
    lambda T: T["recent"]["sectors"]["전체"]["금융"]["invpen"].__setitem__(-1, 999.0),
])
def test_verifier_catches_a_wrong_rank_distance_series_count_or_recent_day(PD, break_it):
    P = copy.deepcopy(PD[0])
    break_it(P["today"])
    assert _t_fails(P), "틀린 값을 검증기 T 층이 못 잡았다"


# ── 요약 문장 ──────────────────────────────────────────────────────────────

def _tiny() -> dict:
    net = {"inst": {"v": 1140.0, "pct": 44.0, "rank": 146, "n": 260, "last_more": 3},
           "forgn": {"v": -6374.0, "pct": 47.0, "rank": 123, "n": 260, "last_more": 5},
           "indiv": {"v": -16.0, "pct": 49.0}, "etc": {"v": 5279.0, "pct": 84.0}}
    secs = {"증권": {"inst": {"v": 626.0, "pct": 94.5}, "invpen": {"v": 626.0, "pct": 100.0}},
            "전기/가스": {"forgn": {"v": -525.0, "pct": 2.0}, "indiv": {"v": 885.0, "pct": 98.0}},
            "부동산": {"inst": {"v": 9.0, "pct": 100.0}}}
    return {"n_by_sector": {"거래소": {"증권": 20, "전기/가스": 12, "부동산": 3}},
            "today": {"lookback": 260, "sectors": {"거래소": secs}, "market": {"거래소": {"net": net}},
                      "streaks": {"lookback": 260, "market": {"거래소": {
                          "inst": {"sign": 1, "days": 1, "sum": 1140.0, "times": 60},
                          "invpen": {"sign": 1, "days": 4, "sum": 8828.0, "times": 15}}}},
                      "day_change": {"prev_date": "2026-11-12", "flips": {"거래소": {"inst": [
                          {"sector": "운송장비/부품", "prev": -1498.0, "now": 824.0}]}}}}}


def test_summary_sentences_say_who_how_much_what_is_unusual_and_what_changed():
    from sectorflow import summary as S
    assert S.summary_texts(_tiny(), "거래소", ["거래소"]) == [
        "외국인 -6,374억 순매도 — 오늘 가장 큰 주체. 1년 %ile 47 · 평소 범위 안 · 260일 중 123번째로 큰 순매도 · 더 많이 판 날은 5거래일 전",
        "투신+연기금 4일 연속 순매수 (누적 +8,828억) — 260일 중 같은 방향 4일 이상 연속 15번(오늘 포함)",
        "평소 범위(1년 %ile 5~95) 밖 4칸/4칸: 증권 투신+연기금 +626억(100) · 전기/가스 외국인 -525억(2) · "
        "전기/가스 개인 +885억(98) 외 1",
        "어제(11-12)와 기관 부호가 바뀐 섹터 1곳: 운송장비/부품 -1,498→+824",
    ]
    assert S.band(94.5) == "평소 범위 밖(위)", "보이는 정수 95 는 범위 밖 — 화면에 95 로 적힌 칸을 '안' 이라 하지 않는다"
    assert S.band(94.4) == S.band(5.5) == "평소 범위 안" and S.band(5.4) == "평소 범위 밖(아래)"


def test_web_pages_carry_the_same_not_said_line_and_trend_badge():
    from sectorflow import summary as S
    from sectorflow.tui.flow_view import EVIDENCE
    tpl = REPO / "scripts" / "templates"
    for page, name in (("sector_numbers.html", "NOT_SAID"), ("sector_flow.html", "VS_NOT_SAID")):
        m = re.search(rf'const {name}="([^"]*)";', (tpl / page).read_text("utf-8"))
        assert m and m.group(1) == S.NOT_SAID, page
    assert EVIDENCE["trend"] == "[앞날 증거 없음]"


# ── 세 출구가 같은 문장 ─────────────────────────────────────────────────────

def test_terminal_first_screen_leads_with_the_summary_sentences(PD):
    from sectorflow import summary as S
    from sectorflow.tui import flow_view as FV
    _P, D = PD
    st = FV.State(D)
    text = " ".join(ln.strip() for ln in FV.banner_lines(st, 400))
    want = S.summary_texts(D, "전체", st.markets[1:])
    assert want and len(want) >= 3
    assert "한눈에" in FV.banner_lines(st, 400)[2], "요약이 첫 화면 맨 위(제목·자료 범위 다음)에 없다"
    for t in want:
        assert "· " + t in text, t


def test_digest_leads_with_the_same_summary_sentences(PD):
    from sectorflow import summary as S
    dg = _mod("digest_for_oneday", REPO / "scripts" / "digest.py")
    P, _D = PD
    F, text, _pages = dg.build(P)
    want = S.summary_texts(P, "전체", P["markets"])
    assert F["summary"] == want
    assert text.index("한눈에") < text.index("시장 순매수"), "요약이 맨 위에 없다"
    for t in want:
        assert f"- {t}" in text


@node
def test_web_layer_is_green_on_the_one_day_page(PD, tmp_path):
    P, D = PD
    assert WR._w_fails(P, D, WR._write(tmp_path, P, D)) == []


@node
@pytest.mark.parametrize("patch,which", [
    (('+(b?" · "+b:"")+(r?" · "+r:"")]); }', '+(r?" · "+r:"")]); }'), "요약 문장"),
    (('if(isExt(f.pct)){ cell.textContent=PS(f.pct);', 'if(f.pct!=null){ cell.textContent=PS(f.pct);'), "섹터×주체 칸"),
    (('const b=band(f.pct), l1=BIG(f.v)+" · 1년 %ile "+PS(f.pct)', 'const b=band(f.pct), l1=A(f.v)+" · 1년 %ile "+PS(f.pct)'), "시장 4주체"),
    (('" · 누적 "+BIG(f.sum)+(f.days>=2', '" · 누적 "+A(f.sum)+(f.days>=2'), "연속 막대"),
])
def test_web_layer_catches_a_one_day_page_that_says_something_else(PD, tmp_path, patch, which):
    P, D = PD
    fails = WR._w_fails(P, D, WR._write(tmp_path, P, D, numbers_patch=patch))
    assert any(which in f for f in fails), fails


@node
def test_web_layer_catches_a_viewer_summary_that_counts_thin_sectors(PD, tmp_path):
    """얇은 섹터(10종목 미만)뿐인 리포트 — 멀쩡한 뷰어는 초록, 얇은 섹터를 세는 뷰어는 빨강."""
    P, D = PD
    Q = copy.deepcopy(P)
    for m in Q["n_by_sector"]:
        for s in Q["n_by_sector"][m]:
            Q["n_by_sector"][m][s] = 4
    QD = WR.SN.build(Q)
    assert WR._w_fails(Q, QD, WR._write(tmp_path, Q, QD)) == []
    patch = ('if(s==="(미분류)"||mem(s)<10) return;', 'if(s==="(미분류)") return;')
    fails = WR._w_fails(Q, QD, WR._write(tmp_path, Q, QD, viewer_patch=patch))
    assert any("뷰어 '오늘 한눈에'" in f for f in fails), fails


@node
def test_watch_line_lives_only_in_the_browser(PD, tmp_path):
    """관심 섹터는 localStorage 에만 — 비면 요약에 줄이 없고, 고르면 그 브라우저의 요약에만 줄이 생긴다."""
    P, D = PD
    page = WR._write(tmp_path, P, D) / "numbers.html"
    probe = r"""
const ul=()=>document.getElementById("a-sum").children.find(c=>c.tagName==="UL");
const keys=()=>ul().children.map(li=>li.dataset.key);
const out={empty:keys()};
const store={}; globalThis.localStorage={getItem:k=>store[k]||null,setItem:(k,v)=>{store[k]=v;}};
toggleWatch("금융"); out.after=keys(); out.saved=store["sf-watch"];
out.line=ul().children.find(li=>li.dataset.key==="watch").textContent;
__out(out);
"""
    out = VR._w_run(VR._w_node(), str(page), probe)
    assert "watch" not in out["empty"]
    assert out["after"][-1] == "watch" and json.loads(out["saved"]) == ["금융"]
    assert out["line"].startswith("관심 1곳 중 기관 순매도") and "금융" in out["line"]
    assert "watch" not in json.dumps(D, ensure_ascii=False), "관심 섹터가 리포트 데이터에 들어갔다"
