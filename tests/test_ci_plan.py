"""CI 잡 건너뛰기 판정(scripts/ci_plan.py) — 건너뛴 잡이 배포 관문의 보호를 줄이지 않는가.

``pull-all --ci-gate`` 는 머리 커밋의 체크만 본다. 빨간 코드 커밋 위에 문서 커밋을 얹어 전체 변이를 건너뛰면 관문이
뚫린다 — 진짜 git 레포로 그 순서를 재현해 본다.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ci_plan_t", REPO / "scripts" / "ci_plan.py")
CP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CP)

GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _git(wt: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=wt, env=GIT_ENV, capture_output=True, text=True, check=True).stdout.strip()


def _commit(wt: Path, path: str, text: str = "x\n") -> str:
    f = wt / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, "utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", path)
    return _git(wt, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    wt = tmp_path / "r"
    wt.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    _git(wt, "config", "user.email", "t@example.com")
    _git(wt, "config", "user.name", "t")
    _commit(wt, "src/a.py")
    monkeypatch.chdir(wt)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    return wt


def _kind(event: str, base: str = "main", verified: str = "") -> str:
    return CP.plan(event, CP.changed_since(event, base, verified))["kind"]


@pytest.mark.parametrize("path,doc", [
    ("README.md", True), ("CLAUDE.md", True), ("docs/formulas.md", True), ("docs/validation/x.csv", True),
    ("LICENSE", True), ("src/a.py", False), ("scripts/templates/x.html", False), (".github/workflows/ci.yml", False),
    ("pyproject.toml", False), ("uv.lock", False), ("tests/fixtures/a.md", False), ("research/note.md", False),
])
def test_only_root_markdown_and_docs_count_as_documents(path, doc):
    assert CP.is_doc(path) is doc


def test_docs_commit_on_top_of_a_red_code_commit_still_runs_everything(repo):
    """관문을 뚫는 순서 — 초록 A → 빨간 코드 B → 문서 C. C 는 A 와 견주므로 전체다. 직전과 견주면 문서로 보여 뚫린다."""
    a = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "src/b.py")
    _commit(repo, "README.md")
    assert _kind("push", verified=a) == "code"


def test_docs_commit_on_top_of_a_verified_tree_skips(repo):
    a = _commit(repo, "src/b.py")
    _commit(repo, "README.md")
    _commit(repo, "docs/x.md")
    out = CP.plan("push", CP.changed_since("push", "main", a))
    assert out["kind"] == "docs" and json.loads(out["python"]) == CP.PY_DOCS


def test_unknown_base_runs_everything(repo):
    """모르면 전체 — 초록 기록 없음·gh 실패(빈 값)·없는 커밋·갈라진 초록(강제 푸시)·재실행(변경 없음)·다른 이벤트."""
    _commit(repo, "README.md")
    assert _kind("push", verified="") == "code"
    assert _kind("push", verified="0" * 40) == "code"
    head = _git(repo, "rev-parse", "HEAD")
    assert _kind("push", verified=head) == "code"                # 같은 커밋 재실행 — 비었다고 문서가 아니다
    assert _kind("workflow_dispatch", verified=head) == "code"
    _git(repo, "checkout", "-q", "-b", "side", "HEAD~1")
    side = _commit(repo, "src/side.py")
    _git(repo, "checkout", "-q", "main")
    assert _kind("push", verified=side) == "code"                # 갈라진 초록과는 코드 트리가 달라 전체다
    assert CP.plan("push", None)["kind"] == "code"


def test_moving_code_into_docs_is_not_a_docs_change(repo):
    a = _git(repo, "rev-parse", "HEAD")
    (repo / "docs").mkdir()
    _git(repo, "mv", "src/a.py", "docs/a.py")
    _git(repo, "commit", "-q", "-m", "move")
    assert CP._changed(a) == {"src/a.py", "docs/a.py"}
    assert _kind("push", verified=a) == "code"


def test_pull_request_compares_to_merge_base_and_picks_python(repo):
    _git(repo, "checkout", "-q", "-b", "pr")
    _commit(repo, "docs/x.md")
    assert _kind("pull_request") == "docs"
    assert json.loads(CP.plan("pull_request", {"docs/x.md"})["python"]) == CP.PY_DOCS
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "src/moved_on.py")                             # 기준이 앞서가도 PR 이 안 건드린 파일은 안 센다
    _git(repo, "checkout", "-q", "pr")
    assert _kind("pull_request") == "docs"
    _commit(repo, "src/c.py")
    assert _kind("pull_request") == "code"
    assert json.loads(CP.plan("pull_request", {"src/c.py"})["python"]) == CP.PY_PR
    assert json.loads(CP.plan("push", {"src/c.py"})["python"]) == CP.PY_ALL


def test_cli_prints_github_output_lines(repo, capsys):
    a = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "README.md")
    assert CP.main(["--event", "push", "--verified", a]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "kind=docs" and json.loads(lines[1].removeprefix("python=")) == ["3.13"]


def test_python_lists_match_the_workflow_matrix_axis():
    """PR·문서의 판은 push 의 부분집합이고, 3.11(하한)은 PR 에서 빠지지 않는다 — 3.11 전용 실패가 실제로 있었다."""
    assert set(CP.PY_PR) <= set(CP.PY_ALL) and set(CP.PY_DOCS) <= set(CP.PY_ALL)
    assert "3.11" in CP.PY_PR
    requires = (REPO / "pyproject.toml").read_text("utf-8")
    assert f'requires-python = ">={CP.PY_ALL[0]}"' in requires
