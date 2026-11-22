"""릴리스(``.github/workflows/release.yml``) — 태그는 main 의 커밋·pyproject 판과 같아야 하고, 본문은 CHANGELOG 의 그 판 절이며,
붙이는 것은 빌드 산출물과 데모 GIF 뿐이다(실데이터가 나갈 길이 없다)."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WF = (REPO / ".github" / "workflows" / "release.yml").read_text("utf-8")
CHANGELOG = (REPO / "CHANGELOG.md").read_text("utf-8")
VERSION = tomllib.loads((REPO / "pyproject.toml").read_text("utf-8"))["project"]["version"]


def test_release_runs_only_on_version_tags():
    on = WF.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert re.search(r'tags: \["v\*"\]', on) and "branches" not in on and "pull_request" not in on


def test_release_checks_the_tag_is_on_main_and_matches_the_version():
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in WF
    assert '[ "$GITHUB_REF_NAME" = "$want" ]' in WF


def test_release_attaches_only_build_outputs_and_the_demo_gif():
    cmd = next(ln for ln in WF.splitlines() if "gh release create" in ln)
    files = cmd.split('"$GITHUB_REF_NAME"', 1)[1].split("--", 1)[0].split()
    assert files == ["dist/*", "docs/images/map-demo.gif"], files


def test_changelog_has_a_section_for_the_current_version():
    assert re.search(rf"^## \[{re.escape(VERSION)}\]", CHANGELOG, re.M), VERSION
    from sectorflow.wording import banned_hits
    assert banned_hits(CHANGELOG) == []
