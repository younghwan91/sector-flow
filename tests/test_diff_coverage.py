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


def test_missing_coverage_json_fails(repo, capsys):
    """재지 않고 넘어가면 관문이 없는 것과 같다."""
    assert DC.main(["--base", "main", "--json", "nope.json"]) == 2
    assert "커버리지 결과가 없다" in capsys.readouterr().out


def test_a_new_file_below_its_floor_fails_even_when_the_total_passes(repo):
    rows = [{"path": "src/a.py", "lines": set(range(1, 21)), "hit": set(range(1, 21)), "miss": [],
             "measured": True},
            {"path": "scripts/b.py", "lines": {1, 2, 3}, "hit": set(), "miss": [1, 2, 3],
             "measured": False}]
    ok, text = DC.report(rows, 80, {"scripts/b.py"}, 50)
    assert not ok and "20/23" in text and "새 파일 scripts/b.py" in text
    assert DC.report(rows, 80, set(), 50)[0]                 # 새 파일이 아니면 전체 몫만 본다
    assert DC.new_files("main", repo) == {"scripts/b.py"}


def test_non_ascii_paths_are_matched(repo):
    (repo / "scripts" / "한글.py").write_text("x = 1\n", "utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "korean")
    assert DC.changed_lines("main", repo)["scripts/한글.py"] == {1}
    assert "scripts/한글.py" in DC.new_files("main", repo)
    assert DC._unquote('"scripts/\\355\\225\\234.py"') == "scripts/한.py"


def _touch(repo: Path, rel: str, text: str = "x\n") -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text, "utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"touch {rel}")


def test_changed_shell_and_template_need_a_mutation(repo, capsys):
    """줄을 잴 수 없는 파일은 변이로 본다 — 없으면 실패하고 파일 이름을 말한다. 지운 파일은 묻지 않는다."""
    cov = {"files": {"src/a.py": {"executed_lines": [1, 2, 5, 9, 10], "missing_lines": [6]},
                     "scripts/b.py": {"executed_lines": [1, 2, 4], "missing_lines": []}}}
    (repo / "cov.json").write_text(json.dumps(cov), "utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "cov")
    assert DC.main(["--base", "main", "--json", "cov.json"]) == 0

    _touch(repo, "scripts/daily.sh")
    _touch(repo, "scripts/templates/card.html")
    assert DC.changed_unmeasurable("main", repo) == {"scripts/daily.sh", "scripts/templates/card.html"}
    assert DC.main(["--base", "main", "--json", "cov.json"]) == 1
    out = capsys.readouterr().out
    assert "scripts/daily.sh" in out and "scripts/templates/card.html" in out

    (repo / "tests").mkdir()
    _touch(repo, "tests/mutations.toml", '[[mutation]]\nid = "a"\nfile = "scripts/daily.sh"\nfind = "x"\nreplace = "y"\n'
                                         '[[mutation]]\nid = "b"\n[[mutation.edits]]\nfile = "scripts/templates/card.html"\n'
                                         'find = "x"\nreplace = "y"\n')
    assert DC.mutation_targets(repo) == {"scripts/daily.sh", "scripts/templates/card.html"}
    assert DC.main(["--base", "main", "--json", "cov.json"]) == 0

    _git(repo, "checkout", "-q", "main")                     # 기준에 있던 쉘을 이 브랜치가 지운 경우
    _touch(repo, "scripts/old.sh", "echo old\n")           # 내용이 달라야 한다 — 같으면 git 이 이름 바꿈으로 읽는다
    _git(repo, "checkout", "-q", "wt")
    _git(repo, "merge", "-q", "--no-edit", "main")
    (repo / "scripts" / "old.sh").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "rm")
    assert "scripts/old.sh" not in DC.changed_unmeasurable("main", repo)
