"""자료 범위 표지(KRX 단독, NXT 제외)가 TUI 도움말 둘과 웹 템플릿 둘에 **같은 글**로 실리는가.

표지 글은 ``sectorflow.basis.DATA_BASIS`` 한 곳에만 있다. 화면 하나만 고치고 다른 화면을 잊으면
같은 리포트를 두 도구가 다른 범위라고 말하게 된다 — 그 부류를 막는다. 렌더된 페이지(자리표시가 실제로
갈렸는지)는 ``test_pipeline_e2e.py::test_rendered_pages_carry_the_data_basis`` 가 본다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sectorflow.basis import BASIS_SLOT, DATA_BASIS

TEMPLATES = Path(__file__).resolve().parents[1] / "scripts" / "templates"


def test_basis_label_is_on_the_flow_help_screen_unclipped_at_width_80():
    """주입: ``flow_view.HELP`` 에서 ``BASIS_HELP`` 를 빼면 실패한다."""
    from sectorflow.tui.flow_view import help_lines

    lines, _ = help_lines(80, 0, 10**6)
    assert any(DATA_BASIS in ln for ln in lines), "sw-flow 도움말에 자료 범위 표지가 없다(또는 폭 80 에서 잘렸다)"


def test_basis_label_is_on_the_ledger_help_screen_unclipped_at_width_80():
    """주입: ``ledger_view.LEDGER_HELP`` 에서 ``BASIS_HELP`` 를 빼면 실패한다."""
    from sectorflow.tui.flow_view import help_lines
    from sectorflow.tui.ledger_view import HELP_LABEL_W, help_total, ledger_help

    entries = ledger_help()
    lines, total = help_lines(80, 0, 10**6, entries, label_w=HELP_LABEL_W)
    assert any(DATA_BASIS in ln for ln in lines), "sw-ledger 도움말에 자료 범위 표지가 없다(또는 폭 80 에서 잘렸다)"
    assert total == help_total(), "스크롤 하한이 실제 도움말 줄 수와 어긋난다"


@pytest.mark.parametrize("tpl", ["sector_flow.html", "sector_numbers.html"])
def test_web_templates_carry_the_basis_slot(tpl):
    """주입: 템플릿에서 자리표시를 지우면 실패한다. 글을 템플릿에 직접 적어도 실패한다(두 벌이 된다)."""
    html = (TEMPLATES / tpl).read_text("utf-8")
    assert html.count(BASIS_SLOT) == 1, f"{tpl} 에 자료 범위 자리표시가 한 번 있어야 한다"
    assert DATA_BASIS not in html, f"{tpl} 이 표지 글을 직접 적었다 — DATA_BASIS 한 곳에서 끼운다"


def _tui_states():
    """첫 화면 두 꼴 — '오늘' 블록이 있는 리포트와 없는 옛 리포트."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_tui_flow_view import _today_block

    from sectorflow.tui.flow_view import State

    base = {"asof": "2026-11-06", "sectors": ["전기/전자"], "markets": ["전체", "거래소", "코스닥"],
            "blocks": {f"{w}|{m}": {"rows": []} for w in ("5", "20") for m in ("전체", "거래소", "코스닥")}}
    return [State(dict(base, today=_today_block(["전기/전자", "건설"]))), State(base)]


@pytest.mark.parametrize("width", [80, 60])
def test_first_screen_states_the_data_basis_right_under_the_title(width):
    """도움말 끝까지 안 내려가도 첫 화면 둘째 줄에 자료 범위가 보인다(리뷰 3차 D1-m5) — 옛 리포트에서도, 폭 60 에서도.

    주입: 첫 화면에서 자료 범위 줄을 빼면 실패한다.
    """
    from sectorflow.basis import DATA_BASIS_SHORT
    from sectorflow.tui.flow_view import banner_lines, banner_view

    for st in _tui_states():
        assert DATA_BASIS_SHORT in banner_lines(st, width)[1], banner_lines(st, width)[:3]
        assert DATA_BASIS_SHORT in banner_view(st, width, 24)[1]          # 80x24 에서 스크롤 없이 보인다


def test_numbers_today_panel_header_carries_the_short_basis_chip():
    """웹 맨 위 '오늘 한 장' 머리(제목·시장 단추 줄)에 짧은 자료 범위 칩 — 자리표시로, 글을 직접 적지 않고.
    예전 '오늘' 패널은 접힌 세부 숫자가 됐다 — 칩은 접지 않은 맨 위에 있어야 펼치지 않고도 보인다.

    주입: 칩을 빼거나 맨 위 밖(notes·접힌 세부)으로 옮기면 실패한다.
    """
    from sectorflow.basis import BASIS_SHORT_SLOT, DATA_BASIS_SHORT

    html = (TEMPLATES / "sector_numbers.html").read_text("utf-8")
    assert html.count(BASIS_SHORT_SLOT) == 1 and DATA_BASIS_SHORT not in html
    head = html[html.index('<section class="oneday"'):html.index('id="a-sum"')]
    assert 'class="ahead"' in head and BASIS_SHORT_SLOT in head, "칩이 '오늘 한 장' 머리에 없다"
