"""조합 리허설(`scripts/rehearse_merge.py`) — 작은 가짜 레포로 **합친 결과를 재는지**, 원래 레포에 안 닿는지 본다."""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rehearse_merge_t", REPO / "scripts" / "rehearse_merge.py")
RM = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RM)

GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _git(wt: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=wt, env=GIT_ENV, capture_output=True, text=True, check=True).stdout.strip()


def _branch(repo: Path, name: str, rel: str, text: str) -> None:
    _git(repo, "checkout", "-q", "-b", name, "main")
    (repo / rel).write_text(text, "utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", name)
    _git(repo, "checkout", "-q", "main")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "a.txt").write_text("a\n", "utf-8")
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    _branch(r, "one", "one.txt", "1\n")
    _branch(r, "two", "two.txt", "2\n")
    _branch(r, "clash", "one.txt", "다른 내용\n")
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(RM.tempfile, "tempdir", str(tmp))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.chdir(r)
    return r


def _tmp_children(repo: Path) -> list[Path]:
    return sorted((repo.parent / "tmp").iterdir())


def _main(*args: str) -> int:
    return RM.main(["--base", "main", *args])


def test_gates_see_every_branch_merged_and_the_repo_is_untouched(repo, capsys):
    """관문은 **합친** 트리에서 돈다. 원래 레포의 브랜치·HEAD·작업 트리는 그대로고 통과하면 임시 폴더가 사라진다."""
    before = _git(repo, "for-each-ref"), _git(repo, "status", "--porcelain"), _git(repo, "rev-parse", "HEAD")
    rc = _main("one", "two", "--gate", "both=test -f one.txt && test -f two.txt && test -f a.txt")
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "합침 one" in out and "합침 two" in out and "초록 CI 규칙을 대신하지 않는다" in out
    assert (_git(repo, "for-each-ref"), _git(repo, "status", "--porcelain"), _git(repo, "rev-parse", "HEAD")) == before
    assert _tmp_children(repo) == []
    assert not (repo / "one.txt").exists()


def test_a_failing_gate_fails_the_run_and_later_gates_still_run(repo, capsys):
    rc = _main("one", "--gate", "red=exit 7", "--gate", "after=touch ran-after")
    out = capsys.readouterr().out
    assert rc == 1 and "rc 7" in out and "실패 — 관문 red" in out, out
    tmp = _tmp_children(repo)[0]                              # 살펴보라고 남긴다
    assert (tmp / "wt" / "ran-after").exists()
    assert (tmp / "logs" / "red.log").exists()


def test_a_conflict_names_the_file_and_runs_no_gate(repo, capsys):
    rc = _main("one", "clash", "--gate", "never=touch gate-ran")
    out = capsys.readouterr().out
    assert rc == 1 and "clash 에서 멈췄다" in out and "one.txt" in out, out
    assert not (_tmp_children(repo)[0] / "wt" / "gate-ran").exists()


def test_bad_calls_exit_2_before_touching_anything(repo, capsys):
    assert _main("nope", "--gate", "x=true") == 2
    assert "없는 브랜치: nope" in capsys.readouterr().err
    assert RM.main(["--base", "nope", "one", "--gate", "x=true"]) == 2
    assert _main("one", "--skip", "nope") == 2
    assert _tmp_children(repo) == []


def test_gates_never_see_the_production_db_or_reports(repo, monkeypatch):
    """실 DB·운영 리포트 폴더가 환경에 있어도 관문으로 넘어가지 않는다 — 관문엔 변이 주입도 있다."""
    monkeypatch.setenv("KR_QUANT_DB", "postgres://prod")
    monkeypatch.setenv("KR_QUANT_REPORTS", "/prod/reports")
    assert _main("one", "--gate", 'clean=test -z "$KR_QUANT_DB" && test -z "$KR_QUANT_REPORTS"') == 0


def test_branches_merge_in_the_given_order_with_the_repo_identity(repo):
    assert _main("two", "one", "--keep", "--gate", "x=true") == 0
    wt = _tmp_children(repo)[0] / "wt"
    log = _git(wt, "log", "--first-parent", "--format=%s|%ae", "-2").splitlines()
    assert log == ["rehearsal: one|t@example.com", "rehearsal: two|t@example.com"]


def test_shifted_doc_refs_are_fixed_and_reported(repo, capsys):
    """인용 보정이 필요한 조합이면 고쳐 커밋하고 말한다(가짜 docs_refs 가 파일을 고친다)."""
    (repo / "scripts").mkdir()
    (repo / "scripts" / "docs_refs.py").write_text(
        "import pathlib, sys\nassert sys.argv[1:] == ['--fix']\npathlib.Path('doc.md').write_text('고침')\n", "utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "tool")
    assert _main("one", "--gate", "fixed=test -f doc.md && git diff --quiet HEAD") == 0
    assert "docs_refs --fix 로 고쳤다" in capsys.readouterr().out


def test_default_gates_match_the_ci_steps():
    """CI 의 단계와 같은 것을 돈다 — 변이 주입이 빠지거나 부분 범위로 돌면 '전체 관문' 이 아니다."""
    gates = dict(RM.GATES)
    assert set(gates) == {"lint", "research-compiles", "pytest", "pytest-utc", "diff-coverage", "verify-merge"}
    assert "--scope all" in gates["verify-merge"] and "--shard" not in gates["verify-merge"]
    assert "--cov" in gates["pytest"] and "TZ=UTC" in gates["pytest-utc"]
    assert "--min 80" in gates["diff-coverage"]
