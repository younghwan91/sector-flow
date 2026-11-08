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


def test_viewer_scrolls_the_heatmap_to_today_and_draws_the_line_in_pixels():
    """폰(390px)에서 히트맵이 가장 옛날부터 보여 오늘이 화면 밖이었다 — 오른쪽 끝으로 민다.
    누적 추이는 실제 픽셀 폭으로 그린다(1000 폭 viewBox 를 줄이면 눈금이 4px 가 된다).

    주입: `box.scrollLeft=box.scrollWidth;` 를 지우면 실패한다.
    """
    html = _html()
    heat = html[html.index("function renderHeat"):html.index("/* ---------- cumulative line")]
    assert "box.scrollLeft=box.scrollWidth;" in heat
    line = html[html.index("function renderLine"):html.index("/* ---------- ranking")]
    assert "Number(box.clientWidth)" in line
