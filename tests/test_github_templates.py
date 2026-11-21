"""이슈·PR 템플릿이 공개 레포의 입구를 지키는가 — 실데이터를 붙이지 말고 데모로 재현, 투자 질문은 받지 않는다.

템플릿은 사람이 처음 쓰는 글이라 잘 낡는다. 체크가 required 에서 풀리거나 빈 이슈가 다시 열리면 실데이터가 이슈로 들어온다.
YAML 파서 없이(의존성을 늘리지 않으려고) 글자로 본다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sectorflow import wording as W

REPO = Path(__file__).resolve().parent.parent
GH = REPO / ".github"
FORMS = sorted((GH / "ISSUE_TEMPLATE").glob("*.yml"))


def _checks(text: str) -> list[tuple[str, bool]]:
    """(체크 문구, required) — `- label:` 다음 줄의 `required: true`."""
    return [(m[0], m[1] == "true") for m in re.findall(r"- label: (.+)\n\s+required: (\w+)", text)]


@pytest.mark.parametrize("name", ["bug.yml", "feature.yml"])
def test_issue_forms_require_the_no_real_data_check(name):
    text = (GH / "ISSUE_TEMPLATE" / name).read_text("utf-8")
    checks = _checks(text)
    assert any("실데이터" in lab and req for lab, req in checks), f"{name}: 실데이터를 붙이지 않았다는 체크가 required 가 아니다"
    assert "투자 질문" in text or "고르기·예측" in text, name


def test_bug_form_points_at_the_demo():
    text = (GH / "ISSUE_TEMPLATE/bug.yml").read_text("utf-8")
    assert "--demo" in text and "sector-flow-demo" in text


def test_blank_issues_are_off():
    """빈 이슈가 열리면 위 체크를 건너뛴다."""
    assert re.search(r"^blank_issues_enabled: false$", (GH / "ISSUE_TEMPLATE/config.yml").read_text("utf-8"), re.M)


def test_pr_template_asks_about_real_data():
    text = (GH / "pull_request_template.md").read_text("utf-8")
    assert re.search(r"^- \[ \] 실데이터.*없다", text, re.M)


@pytest.mark.parametrize("path", [*FORMS, GH / "pull_request_template.md"], ids=lambda p: p.name)
def test_templates_use_no_banned_word(path):
    assert W.page_hits(path.read_text("utf-8")) == [], path.name
