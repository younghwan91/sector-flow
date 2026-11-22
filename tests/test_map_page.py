"""수급 지도 페이지(``scripts/sector_map.py`` + ``templates/sector_map.html``) — 데이터가 한 줄로 심기고, 표지가 채워지고,
글이 금칙어·지도용 피할 말을 통과하는가. DB·네트워크·브라우저 없음."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from sectorflow.wording import map_hits, rendered_page_hits

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sector_map", REPO / "scripts" / "sector_map.py")
SM = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SM)


def _payload():
    f = lambda **k: {a: list(v) for a, v in k.items()}  # noqa: E731
    return {"dates": ["2026-01-05", "2026-01-06"], "sectors": ["가", "나"], "markets": ["거래소"],
            "flows": {"거래소": {"가": f(indiv=[-10, -20], forgn=[6, 12], inst=[4, 8], etc=[0, 0]),
                                "나": f(indiv=[3, 5], forgn=[-1, -2], inst=[-2, -3], etc=[0, 0])}}}


def _html():
    data = SM.build_map_data(_payload())
    return data, SM.render(data, Path(SM.TEMPLATE).read_text("utf-8"))


def test_map_html_embeds_data_once():
    data, html = _html()
    lines = [ln for ln in html.splitlines() if ln.startswith("const D = ")]
    assert len(lines) == 1, "데이터 줄은 하나 — rendered_page_hits 가 이 줄을 빼고 글만 본다"
    assert json.loads(lines[0][len("const D = "):].rstrip(";")) == data


def test_map_html_slots_filled_and_no_remote_scripts():
    _data, html = _html()
    assert not re.search(r"__[A-Z_]+__", html), "빈 자리표시자가 남았다"
    assert "<script src=" not in html, "외부 스크립트 없음"


def test_map_page_wording():
    _data, html = _html()
    assert rendered_page_hits(html) == []
    text = re.sub(r"^const D = .*$", "", html, flags=re.M)
    assert map_hits(text) == [], "지도 글에 유입·유출·몰림 같은 말"


def test_time_flow_view_keeps_its_reason_and_draws_straight_lines():
    """두 번째 보기 '시간 흐름' — 화면 아래에 섹터와 섹터를 잇지 않는 이유가 있고, 값 사이를 곡선으로 메우지 않는다(직선 L 만)."""
    _data, html = _html()
    assert 'data-v="stream"' in html or '["stream", "시간 흐름"]' in html
    why = re.search(r'<p class="why">(.*?)</p>', html, re.S)
    assert why and "섹터와 섹터를 잇는 선은 긋지 않는다" in why.group(1) and "꼬리표가 없고" in why.group(1)
    js = html[html.index("두 번째 보기: 시간 흐름"):]
    line = re.search(r"const line = pts => (.*?);\n", js).group(1)
    assert '"L"' in line and "C" not in line.replace("toFixed", ""), "곡선 보간은 없는 값을 만든다"
    assert map_hits(why.group(1)) == []
