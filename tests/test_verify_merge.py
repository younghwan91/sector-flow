"""주입 검증 하네스(`scripts/verify_merge.py`)의 속도·범위 장치가 **판정을 바꾸지 않는가**.

CI 의 주입 검증이 PR 마다 4~10분씩 늘어(261개 8분 → 302개 22.7분, 잡 상한 30분) 기준선을 한 번에 돌리고,
샤드로 나누고, 시간 예산을 걸고, 로컬 pre-push 는 바뀐 것만 돌게 했다. 빨라진 하네스가 못 잡는 변이를
"잡혔다" 고 하거나 샤드가 변이를 빠뜨리면 이 게이트는 장식이 된다 — 그걸 작은 가짜 레포로 본다.
`--self-test` 는 진짜 레포 전체로 같은 것을 보지만 수십 분이라 주 1회(CI schedule)만 돈다.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("verify_merge_t", REPO / "scripts" / "verify_merge.py")
VM = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(VM)

GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

LIB = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
TESTS = ("from lib import add, sub\n\n\n"
         "def test_add():\n    assert add(2, 3) == 5\n\n\n"
         "def test_blind():\n    assert callable(sub)\n")
SPEC = '''[[mutation]]
id = "add-subtracts"
why = "더하기가 뺀다"
guards = ["tests/test_lib.py::test_add"]
file = "lib.py"
find = "    return a + b"
replace = "    return a - b"
'''
BLIND = '''
[[mutation]]
id = "sub-adds-but-guard-is-blind"
why = "빼기가 더한다"
guards = ["tests/test_lib.py::test_blind"]
file = "lib.py"
find = "    return a - b"
replace = "    return a + b"
'''


def _git(wt: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=wt, env=GIT_ENV, capture_output=True,
                          text=True, check=True).stdout.strip()


def _commit(wt: Path, msg: str) -> None:
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", msg)


@pytest.fixture
def mini(tmp_path):
    """lib.py + 검사 둘 + 변이 선언 하나가 든 레포. 기준 커밋을 돌려준다."""
    wt = tmp_path / "wt"
    (wt / "tests").mkdir(parents=True)
    (wt / "lib.py").write_text(LIB, "utf-8")
    (wt / "tests" / "test_lib.py").write_text(TESTS, "utf-8")
    (wt / "tests" / "mutations.toml").write_text(SPEC, "utf-8")
    (wt / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", "utf-8")
    _git(wt, "init", "-q", "-b", "main")
    _git(wt, "config", "user.email", "t@example.com")
    _git(wt, "config", "user.name", "t")
    _commit(wt, "base")
    return wt, _git(wt, "rev-parse", "HEAD")


def _run(wt: Path, base: str, **kw):
    s = VM.sec_mutations(wt, sys.executable, base, None, **kw)
    return s, "\n".join(s.lines)


def test_caught_blind_and_restored_with_one_baseline_run(mini):
    wt, base = mini
    (wt / "tests" / "mutations.toml").write_text(SPEC + BLIND, "utf-8")
    _commit(wt, "blind")
    s, out = _run(wt, base)
    assert s.status == VM.REJECT
    assert "[add-subtracts] 잡힘" in out
    assert "[sub-adds-but-guard-is-blind] 변이를 넣었는데 가드가 **초록이다**" in out
    assert "가드 2개를 pytest 한 번에" in out
    assert "원상복구 확인" in out and (wt / "lib.py").read_text("utf-8") == LIB
    assert "주입 검증 2건" in out and "느린 변이" in out


def test_guard_red_before_injection_is_rejected_and_a_missing_one_warns(mini):
    wt, base = mini
    (wt / "tests" / "test_lib.py").write_text(           # 이미 빨간 검사
        TESTS + "\n\ndef test_sub():\n    assert sub(5, 3) == 3\n", "utf-8")
    (wt / "tests" / "mutations.toml").write_text(SPEC + BLIND.replace(
        "tests/test_lib.py::test_blind", "tests/test_lib.py::test_sub") + BLIND.replace(
        "sub-adds-but-guard-is-blind", "typo").replace("::test_blind", "::test_nope"), "utf-8")
    _commit(wt, "broken")
    s, out = _run(wt, base)
    assert s.status == VM.REJECT
    assert "[sub-adds-but-guard-is-blind] 가드가 **주입 전에도** 실패한다" in out
    assert "[typo] 가드를 이 워크트리에서 **찾지 못했다**" in out
    assert "[add-subtracts] 잡힘" in out          # 없는 nodeid 가 기준선을 통째로 죽이지 않는다


def test_new_test_without_mutation_is_rejected_unless_exempt_with_a_reason(mini):
    wt, base = mini
    new = "\n\ndef test_new():\n    assert add(1, 1) == 2\n"
    (wt / "tests" / "test_lib.py").write_text(TESTS + new, "utf-8")
    _commit(wt, "new test")
    s, out = _run(wt, base)
    assert s.status == VM.REJECT and "tests/test_lib.py::test_new" in out

    for marker, want in (("# mutation-exempt: 덧셈 항등식이라 주입할 버그가 없다\n", VM.PASS),
                         ("# mutation-exempt: 짧\n", VM.REJECT)):
        (wt / "tests" / "test_lib.py").write_text(TESTS + "\n\n" + marker + new.lstrip("\n"), "utf-8")
        _commit(wt, "exempt")
        s, out = _run(wt, base)
        assert s.status == want, out
    s, out = _run(wt, base, shard=(2, 2))                # 판정은 1번 몫만 — 2번 몫은 말만 한다
    assert "샤드 1 이 한다" in out


def test_changed_scope_runs_only_mutations_touching_the_branch(mini):
    wt, _ = mini
    other = BLIND.replace('file = "lib.py"', 'file = "other.py"').replace(
        "sub-adds-but-guard-is-blind", "other")
    (wt / "tests" / "mutations.toml").write_text(SPEC + other, "utf-8")
    (wt / "other.py").write_text("def sub(a, b):\n    return a - b\n", "utf-8")
    _commit(wt, "other")
    base = _git(wt, "rev-parse", "HEAD")
    (wt / "lib.py").write_text(LIB + "\n", "utf-8")       # lib.py 만 바꾼다
    _commit(wt, "touch lib")
    s, out = _run(wt, base, scope="changed", shard=(1, 4))
    assert "범위: changed" in out and "1/2개" in out and "샤드 1/4 은 무시한다" in out
    assert "[add-subtracts] 잡힘" in out and "[other]" not in out


def test_budget_overrun_stops_and_rejects(mini):
    wt, base = mini
    s, out = _run(wt, base, budget_min=1e-6)
    assert s.status == VM.REJECT and "돌리지 않고" in out and "잡힘" not in out
    # 샤드 없는 실행이라 예산에 샤드 수(mini 엔 ci.yml 이 없다 → 4)를 곱했다고 적는다.
    assert "× 샤드 4" in out


def test_unsharded_budget_scales_by_the_ci_shard_count(tmp_path):
    """--budget-min 은 CI 샤드 한 몫의 예산이다 — 샤드 없이(전체 몫) 돌면 샤드 수를 곱한다.

    안 곱하면 변이가 늘 때마다 ci-local 전체 실행이 전부 REJECT 로 막힌다(2026-11-09, 692개
    12.3분 > 12분). 샤드가 주어지면 그대로다."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "    strategy:\n      matrix:\n        shard: [1, 2, 3, 4, 5, 6]\n", "utf-8")
    assert VM.ci_shard_count(tmp_path) == 6
    eff, why = VM.effective_budget(12, None, tmp_path)
    assert eff == 72 and "× 샤드 6" in why and "72" in why
    assert VM.effective_budget(12, (2, 6), tmp_path) == (12, "")     # 샤드 한 몫은 그대로
    assert VM.effective_budget(None, None, tmp_path) == (None, "")   # 예산이 없으면 아무것도 안 한다
    (wf / "ci.yml").write_text("jobs: {}\n", "utf-8")               # matrix 가 없으면 4 로 본다
    assert VM.ci_shard_count(tmp_path) == 4
    assert VM.ci_shard_count(tmp_path / "nope") == 4
    # 실제 ci.yml 의 matrix 와 같은 수를 읽는다 — 식이 어긋나면 여기서 끊긴다.
    import re
    shards = re.search(r"shard: \[([\d, ]+)\]", (REPO / ".github/workflows/ci.yml").read_text("utf-8"))
    assert VM.ci_shard_count(REPO) == len(shards.group(1).split(","))


def test_shards_partition_the_declarations_deterministically():
    muts = [{"id": f"m{i}", "guards": [g]} for i, g in enumerate(
        ["tests/test_pipeline_e2e.py::test_pipeline_report_verifies_with_no_failures"] * 3
        + ["tests/test_pipeline_e2e.py::test_x"] * 7 + ["tests/test_tui_smoke.py::test_y"] * 5
        + ["tests/test_a.py::test_z"] * 40)]
    parts = [VM.shard_of(muts, i, 4) for i in range(1, 5)]
    ids = [m["id"] for p in parts for m in p]
    assert sorted(ids) == sorted(m["id"] for m in muts)          # 빠짐·겹침 없음
    assert parts == [VM.shard_of(list(muts), i, 4) for i in range(1, 5)]
    costs = [sum(VM.mutation_cost(m) for m in p) for p in parts]
    assert max(costs) - min(costs) <= max(VM.mutation_cost(m) for m in muts)
    heavy = [sum(m["id"] in ("m0", "m1", "m2") for m in p) for p in parts]
    assert max(heavy) == 1                                      # 무거운 셋이 한 몫에 몰리지 않는다
    assert [m["id"] for m in parts[0]] == sorted((m["id"] for m in parts[0]),
                                                 key=lambda x: int(x[1:]))   # 선언 순서


@pytest.mark.parametrize("text,ok", [("1/4", (1, 4)), (" 4 / 4 ", (4, 4)), ("0/4", None),
                                     ("5/4", None), ("${{ matrix.shard }}/4", None), ("", None)])
def test_parse_shard(text, ok):
    if ok:
        assert VM.parse_shard(text) == ok
    else:
        with pytest.raises(ValueError):
            VM.parse_shard(text)


def test_changed_scope_selection_rules():
    a = {"id": "a", "guards": ["tests/t1.py::x"], "file": "src/a.py", "find": "1"}
    b = {"id": "b", "guards": ["tests/t2.py::y"], "edits": [{"file": "src/b.py", "find": "2"}]}
    c = {"id": "c", "guards": ["tests/t3.py::z"], "file": "src/c.py", "find": "3"}
    base = {"a": a, "b": b, "c": dict(c, find="old")}
    pick = lambda ch: [m["id"] for m in VM.changed_scope([a, b, c], ch, base)[0]]  # noqa: E731
    assert pick(set()) == ["c"]                                  # 선언이 바뀐 것은 늘
    assert pick({"src/a.py"}) == ["a", "c"]
    assert pick({"tests/t2.py"}) == ["b", "c"]
    assert pick({"uv.lock"}) == ["a", "b", "c"]


def test_auto_scope_is_changed_only_for_local_ci_mutation_runs():
    assert VM.auto_scope({"CI": "true"}, {"mutations"}) == "changed"
    assert VM.auto_scope({"CI": "true", "GITHUB_ACTIONS": "true"}, {"mutations"}) == "all"
    assert VM.auto_scope({}, {"mutations"}) == "all"
    assert VM.auto_scope({"CI": "true"}, {"mutations", "tests"}) == "all"


def test_exempt_marker_placement():
    lines = ["x = 1", "",
             "# mutation-exempt: 위에 붙은 이유가 있다", "@deco", "def test_a():", "    pass",
             "", "def test_b():  # mutation-exempt: 줄 끝에 단 이유도 된다", "    pass",
             "# mutation-exempt: 빈 줄 위 표시는 번지지 않는다", "", "def test_c():", "    pass",
             "", "def test_d():", "    s = '# mutation-exempt: 문자열 안의 글자는 표시가 아니다'",
             "", "def test_e():  # mutation-exempt: TODO 나중에 변이를 넣는다", "    pass",
             "", "def test_f():  # mutation-exempt: 짧다", "    pass"]
    assert VM._exempt_reason(lines, 4, 6) == "위에 붙은 이유가 있다"
    assert VM._exempt_reason(lines, 8, 9) == "줄 끝에 단 이유도 된다"
    assert VM._exempt_reason(lines, 12, 13) is None
    assert VM._exempt_reason(lines, 15, 16) is None            # 문자열 리터럴
    assert VM._exempt_reason(lines, 18, 19) is None            # 자리표시
    assert VM._exempt_reason(lines, 21, 22) is None            # 10자 미만


def test_broken_declarations_and_guards_are_each_rejected_for_their_own_reason(mini):
    wt, base = mini
    (wt / "tests" / "test_bad.py").write_text("def test_x(:\n    pass\n", "utf-8")
    (wt / "tests" / "test_skip.py").write_text(
        "import pytest\n\n\n@pytest.mark.skip(reason='x')\ndef test_s():\n    pass\n", "utf-8")
    (wt / "dup.py").write_text("a = 1\na = 1\n", "utf-8")
    one = 'id = "{i}"\nguards = ["{g}"]\nfile = "{f}"\nfind = "{find}"\nreplace = "z"\n'
    spec = SPEC + "".join("\n[[mutation]]\n" + one.format(**x) for x in (
        {"i": "no-file", "g": "tests/test_lib.py::test_add", "f": "nope.py", "find": "a"},
        {"i": "no-anchor", "g": "tests/test_lib.py::test_add", "f": "lib.py", "find": "zzz"},
        {"i": "twice", "g": "tests/test_lib.py::test_add", "f": "dup.py", "find": "a = 1"},
        {"i": "uncollectable", "g": "tests/test_bad.py::test_x", "f": "lib.py", "find": "return a + b"},
        {"i": "skipped", "g": "tests/test_skip.py::test_s", "f": "lib.py", "find": "return a + b"},
    )) + '\n[[mutation]]\nid = "no-guards"\nfile = "lib.py"\nfind = "a"\n'
    (wt / "tests" / "mutations.toml").write_text(spec, "utf-8")
    _commit(wt, "broken declarations")
    s, out = _run(wt, base)
    assert s.status == VM.REJECT
    for want in ("[no-file] 대상 파일이 없다", "[no-anchor] 앵커가 lib.py 에 없어",
                 "[twice] find 가 dup.py 에서 2번", "[uncollectable] 가드 파일이 **주입 전에도** 수집되지",
                 "[skipped] 가드가 주입 전에 **건너뛰어졌다**", "[no-guards] 필드 누락",
                 "[add-subtracts] 잡힘"):
        assert want in out, want


def test_budget_verdict():
    assert VM.budget_verdict(10_000, None, 0)[0] == VM.PASS
    assert VM.budget_verdict(60, 12, 0)[0] == VM.PASS
    assert VM.budget_verdict(10 * 60, 12, 0)[0] == VM.WARN
    assert VM.budget_verdict(13 * 60, 12, 0)[0] == VM.REJECT
    assert "돌리지 않고" in VM.budget_verdict(60, 12, 3)[1]


def test_cli_passes_shard_budget_and_scope_through(monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_verify(wt, base, report, only, py, spec, shard, budget, scope):
        seen.update(only=only, shard=shard, budget=budget, scope=scope)
        return VM.PASS, []
    monkeypatch.setattr(VM, "verify", fake_verify)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("CI", "true")
    # ci-local: matrix 식이 글자 그대로 와도 changed 범위면 샤드 없이 돈다.
    assert VM.main([str(tmp_path), "--only", "mutations", "--shard", "${{ matrix.shard }}/4"]) == 0
    assert seen == {"only": {"mutations"}, "shard": None, "budget": None, "scope": "changed"}
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert VM.main([str(tmp_path), "--only", "mutations", "--shard", "2/4", "--budget-min", "12"]) == 0
    assert seen == {"only": {"mutations"}, "shard": (2, 4), "budget": 12.0, "scope": "all"}
    with pytest.raises(SystemExit):                        # GitHub 에서 틀린 샤드는 오류다
        VM.main([str(tmp_path), "--only", "mutations", "--shard", "${{ matrix.shard }}/4"])
    with pytest.raises(SystemExit):
        VM.main([str(tmp_path), "--only", "nope"])


def test_an_aborted_baseline_session_is_rejected_not_read_as_missing_guards(mini):
    """기준선 pytest 가 중간에 끝나면(pytest.exit·내부 오류·쓰기 실패) 안 돈 가드가 "못 찾음" 경고로
    묻히면 안 된다 — 결과를 믿지 않고 출력과 함께 REJECT 한다. 한 번은 /tmp 가 차서 결과 파일이
    빈 채로 남아 하네스가 JSON 오류로 죽었다."""
    wt, base = mini
    (wt / "tests" / "test_abort.py").write_text(
        "import pytest\n\n\ndef test_stop():\n    pytest.exit('중단', returncode=3)\n", "utf-8")
    (wt / "tests" / "mutations.toml").write_text(SPEC.replace(
        'guards = ["tests/test_lib.py::test_add"]',
        'guards = ["tests/test_abort.py::test_stop", "tests/test_lib.py::test_add"]'), "utf-8")
    _commit(wt, "abort")
    s, out = _run(wt, base)
    assert s.status == VM.REJECT
    assert "기준선 pytest 가 결과를 남기지 않았다" in out and "종료코드 3" in out
    assert "찾지 못했다" not in out


def test_a_full_temp_dir_is_reported_as_environment_not_as_a_broken_or_catching_guard(mini):
    """/tmp 할당량이 바닥나면(Errno 122) 가드가 '이미 깨졌다' 거나 변이를 '잡았다' 고 읽히면 안 된다."""
    wt, base = mini
    (wt / "tests" / "test_full.py").write_text(
        "def test_disk():\n    raise OSError(122, 'Disk quota exceeded')\n", "utf-8")
    (wt / "tests" / "mutations.toml").write_text(SPEC.replace(
        'replace = "    return a - b"',
        'replace = "    raise OSError(28, \'No space left on device\')"') + SPEC.replace(
        "add-subtracts", "full").replace("::test_add", "::test_full").replace(
        "tests/test_lib.py::test_full", "tests/test_full.py::test_disk"), "utf-8")
    _commit(wt, "full disk")
    s, out = _run(wt, base)
    assert s.status == VM.REJECT
    assert "[full] 환경: 임시 폴더 용량" in out and "이미 깨진" not in out
    assert "[add-subtracts] 환경: 임시 폴더 용량" in out and "[add-subtracts] 잡힘" not in out
    # 단언 메시지 안에 인용된 같은 글자는 환경 실패가 아니다(이 검사 자신의 실패 출력이 그렇다).
    assert VM.env_full("E   AssertionError: assert 'FAILED t - OSError: [Errno 122] x' in y") is None
    assert VM.env_full("FAILED tests/t.py::x - OSError: [Errno 28] No space left on device")


def test_only_a_test_failure_counts_as_caught(mini):
    """주입 후 종료코드 1(검사 실패)만 잡힘이다. 변이가 import 를 깨뜨리면(2·4) 동작 버그를 잡은 게 아니다."""
    wt, base = mini
    (wt / "tests" / "mutations.toml").write_text(
        SPEC.replace('replace = "    return a - b"', 'replace = "    return a +"'), "utf-8")
    _commit(wt, "syntax mutation")
    s, out = _run(wt, base)
    # 이름을 대어 부른 검사의 파일이 import 에서 깨지면 pytest 는 2 나 4 로 끝난다.
    assert s.status == VM.REJECT and "[add-subtracts] 판정 불가" in out and "종료코드" in out
    assert "[add-subtracts] 잡힘" not in out


def test_changed_src_reaches_guards_through_imports_scripts_and_strings(tmp_path):
    """바뀐 src 모듈 → 그걸 import 하는 src 모듈 → 그걸 쓰는 스크립트 → 그 스크립트를 띄우는 검사까지 닿는다."""
    files = {
        "src/sectorflow/__init__.py": "",
        "src/sectorflow/a.py": "X = 1\n",
        "src/sectorflow/b.py": "from sectorflow.a import X\n",
        "src/sectorflow/c.py": "Y = 2\n",
        "scripts/s.py": "from sectorflow import b\n",
        "tests/t1.py": "from sectorflow.b import X\n",
        "tests/t2.py": "import subprocess\nsubprocess.run(['python', 'scripts/s.py'])\n",
        "tests/t3.py": "from sectorflow.c import Y\n",
        "tests/t4.py": "import subprocess\nsubprocess.run(['python', '-c', 'from sectorflow.a import X'])\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, "utf-8")
    guards = {"tests/t1.py", "tests/t2.py", "tests/t3.py", "tests/t4.py"}
    assert VM.affected_guard_files(tmp_path, {"src/sectorflow/a.py"}, guards) == \
        {"tests/t1.py", "tests/t2.py", "tests/t4.py"}
    assert VM.affected_guard_files(tmp_path, {"src/sectorflow/c.py"}, guards) == {"tests/t3.py"}
    assert VM.affected_guard_files(tmp_path, {"README.md"}, guards) == set()
    m = {"id": "m", "guards": ["tests/t3.py::x"], "file": "src/other.py", "find": "1"}
    assert VM.changed_scope([m], {"src/sectorflow/c.py"}, {"m": m}, tmp_path)[0] == [m]
    assert VM.changed_scope([m], {"src/sectorflow/a.py"}, {"m": m}, tmp_path)[0] == []
    for full in ("scripts/verify_merge.py", "scripts/sector_flow.py", "scripts/templates/x.html"):
        assert VM.changed_scope([m], {full}, {"m": m}, tmp_path)[0] == [m], full


def test_render_smoke_expects_the_g_column_rounded_like_the_tui():
    """렌더 스모크의 G 기대값이 TUI 가 그리는 글자와 같다(`rhu`) — `.2f` 는 0.595(이진 0.59499…)를 0.59 로 보아
    맞게 그린 0.60 을 어긋남으로 냈다(2026-10-21 재구성 종합 G 로 verify_merge REJECT)."""
    import ast

    sys.path.insert(0, str(REPO / "src"))
    from sectorflow.tui import flow_view as FV
    tree = ast.parse(VM.RENDER_SMOKE)
    keep = [n for n in tree.body
            if (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "EXPECT" for t in n.targets))
            or (isinstance(n, ast.FunctionDef) and n.name == "want")]
    ns: dict = {"V": FV}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "render_smoke", "exec"), ns)
    g_col = next(c for c in FV._TABLE_COLS if c.header == "G")
    for v in (0.595, 0.125, -0.335, 0.6, None):
        assert ns["want"]({"G": v}, "G") == g_col.fn({"G": v}, None)


def test_a_same_named_guard_in_another_file_does_not_cover_a_new_test(mini):
    """다른 파일의 동명 가드는 내 검사가 자기 버그를 잡는다는 증거가 아니다."""
    wt, base = mini
    (wt / "tests" / "test_other.py").write_text("from lib import add\n\n\ndef test_add():\n    assert add(0, 0) == 0\n",
                                                "utf-8")
    _commit(wt, "same name, other file")
    s, out = _run(wt, base)
    assert s.status == VM.REJECT and "tests/test_other.py::test_add" in out, out
    assert VM.guard_names_test("tests/a.py::Cls::test_x[p1]", "tests/a.py::test_x")
    assert not VM.guard_names_test("tests/b.py::test_x", "tests/a.py::test_x")


def test_removed_mutation_needs_a_written_reason(mini):
    """변이를 지우기만 하면 하네스가 볼 것이 줄어 초록이 된다 — 이유가 diff 에 남아야 한다."""
    wt, base = mini
    (wt / "tests" / "mutations.toml").write_text(SPEC + BLIND.replace("test_blind", "test_add"), "utf-8")
    _commit(wt, "base2")
    base2 = _git(wt, "rev-parse", "HEAD")
    (wt / "tests" / "mutations.toml").write_text(SPEC, "utf-8")
    _commit(wt, "drop one")
    s, out = _run(wt, base2)
    assert s.status == VM.REJECT and "[sub-adds-but-guard-is-blind] 가 기준 이후 사라졌다" in out, out

    (wt / "tests" / "mutations.toml").write_text(
        SPEC + '\n[[removed]]\nid = "sub-adds-but-guard-is-blind"\nwhy = "짧"\n', "utf-8")
    _commit(wt, "short reason")
    assert _run(wt, base2)[0].status == VM.REJECT
    (wt / "tests" / "mutations.toml").write_text(
        SPEC + '\n[[removed]]\nid = "sub-adds-but-guard-is-blind"\nwhy = "빼기 함수를 지워 버그가 표현 불가능해졌다"\n', "utf-8")
    _commit(wt, "reason")
    s, out = _run(wt, base2)
    assert s.status == VM.PASS, out
