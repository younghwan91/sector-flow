"""무작위 변이 표본 도구(`scripts/mutation_sample.py`) — 변이를 제자리에 넣고, 판정을 제대로 하는가."""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mutation_sample_t", REPO / "scripts" / "mutation_sample.py")
MS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MS)

SRC = '''def f(a, b):
    if a > 1 and b:
        return a + 2
    return None
'''
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _by_kind(ms):
    return {m["kind"]: m for m in reversed(ms)}


def test_mutants_cover_each_operator_and_skip_unexecuted_lines():
    ms = MS.mutants(SRC, "x.py")
    assert {m["kind"] for m in ms} == {"cmp", "bool", "arith", "const", "negif", "retnone"}
    k = _by_kind(ms)
    assert (k["cmp"]["old"], k["cmp"]["new"]) == (" > ", " >= ")
    assert (k["bool"]["old"], k["bool"]["new"]) == (" and ", " or ")
    assert (k["arith"]["old"], k["arith"]["new"]) == (" + ", " - ")
    assert k["retnone"]["old"] == "a + 2"
    assert not [m for m in ms if m["line"] == 4 and m["kind"] == "retnone"], "return None 은 바꿀 것이 없다"
    assert {m["line"] for m in MS.mutants(SRC, "x.py", executed={3})} == {3}
    assert {m["line"] for m in MS.mutants(SRC, "x.py", only={2})} == {2}


def test_apply_puts_each_mutant_in_place_and_stale_ones_refuse(tmp_path):
    src = '# 한글 주석으로 바이트와 글자 수를 다르게\nname = "가나"; x = 1 + 2\n'
    p = tmp_path / "x.py"
    p.write_text(src, "utf-8")
    m = next(m for m in MS.mutants(src, "x.py") if m["kind"] == "arith")
    orig = MS.apply_mutant(tmp_path, m)
    assert p.read_text("utf-8") == src.replace("1 + 2", "1 - 2") and orig == src.encode()
    p.write_text("name = 1\n\n", "utf-8")
    try:
        MS.apply_mutant(tmp_path, {**m, "line": 1})
    except ValueError as e:
        assert "코드가 바뀌었다" in str(e)
    else:
        raise AssertionError("다른 자리에 변이를 넣었다")


def test_only_a_test_failure_counts_as_killed():
    assert [MS.verdict(r) for r in (0, 1, 2, 5, "timeout")] == ["survived", "killed", "invalid", "invalid", "timeout"]


def test_judge_runs_the_group_then_everything_and_restores_the_source(tmp_path):
    (tmp_path / "lib.py").write_text("def add(a, b):\n    return a + b\n\n\ndef neg(a):\n    return a * 2\n", "utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("from lib import add\n\n\ndef test_a():\n    assert add(1, 2) == 3\n")
    (tmp_path / "tests" / "test_b.py").write_text("from lib import neg\n\n\ndef test_b():\n    assert neg(2) == 4\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\ntestpaths = ["tests"]\n')
    src = (tmp_path / "lib.py").read_text("utf-8")
    ms = MS.mutants(src, "lib.py")
    add, neg = (next(m for m in ms if m["old"] == o and m["kind"] == "arith") for o in (" + ", " * "))
    hit = MS.judge(tmp_path, {**add, "tests": ["tests/test_a.py"]}, [], 60)
    assert (hit["verdict"], hit["stage"]) == ("killed", "group")
    # 묶음 검사(test_a)는 못 잡지만 전체(test_b)가 잡는다 — 묶음 밖의 검사도 세어야 한다.
    full = MS.judge(tmp_path, {**neg, "tests": ["tests/test_a.py"]}, [], 60)
    assert (full["verdict"], full["stage"]) == ("killed", "full")
    # 그 검사를 빼면 전체를 돌아도 살아남는다.
    alive = MS.judge(tmp_path, {**neg, "tests": ["tests/test_a.py"]}, ["tests/test_b.py::test_b"], 60)
    assert (alive["verdict"], alive["stage"]) == ("survived", "full")
    assert (tmp_path / "lib.py").read_text("utf-8") == src


def test_draw_is_deterministic_per_seed_and_respects_coverage_and_diff(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, env=GIT_ENV, check=True, capture_output=True)

    (tmp_path / "x.py").write_text(SRC, "utf-8")
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    (tmp_path / "x.py").write_text(SRC + "\n\nZ = 5 + 6\n", "utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "more")
    cov = {"files": {"x.py": {"executed_lines": [2, 3, 7]}}}
    groups = {"g": (["x.py"], ["t"], False)}
    a, sizes = MS.draw(tmp_path, cov, 3, 4, "main", groups)
    assert a == MS.draw(tmp_path, cov, 3, 4, "main", groups)[0] and len(a) == 4
    assert a != MS.draw(tmp_path, cov, 4, 4, "main", groups)[0] or sizes["g"] <= 4
    assert {m["line"] for m in a} <= {2, 3, 7}
    assert all(m["group"] == "g" and m["tests"] == ["t"] for m in a)
    # 바뀐 줄만 뽑는 묶음 — 기준 뒤에 더한 줄(7)뿐.
    only, _ = MS.draw(tmp_path, cov, 3, 9, "HEAD~1", {"g": (["x.py"], ["t"], True)})
    assert only and {m["line"] for m in only} == {7}
