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


def _job(name: str) -> str:
    body = WF.split("\njobs:\n", 1)[1]
    m = re.search(rf"^  {name}:\n(.*?)(?=^  \S|\Z)", body, re.M | re.S)
    assert m, name
    return m.group(1)


def test_pypi_publishes_after_the_release_with_oidc_only_in_its_own_job():
    """PyPI 는 Release 가 지난 뒤에만, 토큰 없이 신뢰 게시로 — id-token 권한은 pypi 잡에만 있다."""
    pypi = _job("pypi")
    assert "needs: release" in pypi and "environment: pypi" in pypi
    assert "id-token: write" in pypi and "uv publish --trusted-publishing always" in pypi
    top = "\n".join(ln for ln in WF.split("\njobs:\n", 1)[0].splitlines() if not ln.startswith("#"))
    assert "id-token" not in top and "id-token" not in _job("release")
    assert "secrets." not in WF, "토큰을 두지 않는다 — 신뢰 게시만"


def test_pypi_readme_has_no_relative_links():
    """PyPI 는 상대 경로를 풀지 못한다 — 설명에 실리는 README 는 링크·그림이 전부 절대 주소여야 한다."""
    from tests.hatch_stub import load_hatch_build

    mod = load_hatch_build()
    out = mod.absolutize((REPO / "README.md").read_text("utf-8"))
    rel = re.findall(r'(?:\]\(|\bsrc="|\bsrcset=")(?![a-z][a-z0-9+.-]*:|#)([^)"\s]+)', out)
    assert rel == [], rel
    assert f"]({mod.RAW_URL}/docs/images/map-demo.gif)" in out
    assert f"]({mod.REPO_URL}/blob/main/docs/guide.md)" in out


def test_sector_flow_command_is_the_terminal_screen():
    """`uvx sector-flow --demo` 한 줄이 sw-flow 와 같은 화면을 띄운다."""
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text("utf-8"))["project"]["scripts"]
    assert scripts["sector-flow"] == scripts["sw-flow"]
