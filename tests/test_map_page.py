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
