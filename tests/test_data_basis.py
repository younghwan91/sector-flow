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
