"""변경 줄 커버리지 관문(`scripts/diff_coverage.py`)이 **바뀐 줄만** 세고, 측정 안 된 새 파일을 빼먹지 않는가."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("diff_coverage_t", REPO / "scripts" / "diff_coverage.py")
DC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(DC)

GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _git(wt: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=wt, env=GIT_ENV, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """src/a.py 를 가진 기준 커밋 위에 a.py 두 줄 수정 + 새 파일 b.py(아무도 안 돌린다) + 문서 수정."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\ny = 2\n\n\ndef f():\n    return 3\n", "utf-8")
    (tmp_path / "README.md").write_text("a\n", "utf-8")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "wt")
    (tmp_path / "src" / "a.py").write_text(
        "x = 1\ny = 20\n\n\ndef f():\n    return 3\n\n\ndef g():\n    return 4\n", "utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "b.py").write_text('"""새 스크립트."""\nimport os\n\nprint(os.sep)\n', "utf-8")
    (tmp_path / "README.md").write_text("b\n", "utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "change")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_changed_lines_are_only_the_added_python_lines(repo):
    got = DC.changed_lines("main", repo)
    assert got == {"src/a.py": {2, 7, 8, 9, 10}, "scripts/b.py": {1, 2, 3, 4}}


def test_unmeasured_new_file_counts_as_uncovered_and_fails_the_gate(repo):
    cov = {"files": {"src/a.py": {"executed_lines": [1, 2, 5, 9], "missing_lines": [6, 10]}}}
    rows = DC.measure(DC.changed_lines("main", repo), cov, repo)
    by = {r["path"]: r for r in rows}
    assert by["src/a.py"]["lines"] == {2, 9, 10} and by["src/a.py"]["miss"] == [10]
    assert not by["scripts/b.py"]["measured"] and by["scripts/b.py"]["miss"] == [1, 2, 4]
    ok, text = DC.report(rows, 80)
    assert not ok and "2/6 = 33.3%" in text and "측정 안 됨" in text and "10" in text


def test_main_passes_when_changed_lines_are_run(repo, capsys):
    cov = {"files": {"src/a.py": {"executed_lines": [1, 2, 5, 9, 10], "missing_lines": [6]},
                     "scripts/b.py": {"executed_lines": [1, 2, 4], "missing_lines": []}}}
    (repo / "cov.json").write_text(json.dumps(cov), "utf-8")
    assert DC.main(["--base", "main", "--json", "cov.json", "--min", "80"]) == 0
    assert "6/6 = 100.0%" in capsys.readouterr().out


def test_nothing_changed_passes():
    assert DC.report([], 80)[0]


def test_ranges():
    assert DC.ranges([1, 2, 3, 5, 7, 8]) == "1-3,5,7-8"
    assert DC.ranges([]) == ""
