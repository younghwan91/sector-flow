"""주입 검증 하네스(`scripts/verify_merge.py`)의 속도·범위 장치가 **판정을 바꾸지 않는가**.

CI 의 주입 검증이 PR 마다 4~10분씩 늘어(261개 8분 → 302개 22.7분, 잡 상한 30분) 기준선을 한 번에 돌리고,
샤드로 나누고, 시간 예산을 걸고, 로컬 pre-push 와 PR 은 바뀐 것만 돌게 했다. 빨라진 하네스가 못 잡는 변이를
"잡혔다" 고 하거나 샤드가 변이를 빠뜨리면 이 게이트는 장식이 된다 — 그걸 작은 가짜 레포로 본다.
`--self-test` 는 진짜 레포로 같은 것을 보지만 오래 걸려 주 1회(CI schedule)만 돈다.
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


def test_identity_section_rejects_another_address_on_either_side(mini):
    """작성자·커미터 어느 쪽이든 레포 설정과 다르면 REJECT, GitHub 머지 주소는 통과. 자기 검증의 같은 시나리오는 주 1회이고,
    로컬에서는 전역 신원 훅이 그 커밋을 막아 시나리오가 성립하지 않는다 — 판정 자체는 여기서 매번 본다."""
    wt, _ = mini
    def commit_as(author: str, committer: str) -> str:
        (wt / "lib.py").write_text((wt / "lib.py").read_text("utf-8") + "\n", "utf-8")
        _git(wt, "add", "-A")
        subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=wt, check=True, capture_output=True,
                       env={**GIT_ENV, "GIT_AUTHOR_EMAIL": author, "GIT_COMMITTER_EMAIL": committer})
        return _git(wt, "rev-parse", "HEAD")
    me, other = "t@example.com", "someone@corp.example.com"
    assert VM.sec_identity(wt, [commit_as(me, me)]).status == VM.PASS
    assert VM.sec_identity(wt, [commit_as(me, "noreply@github.com")]).status == VM.PASS
    for a, c in ((other, me), (me, other)):
        s = VM.sec_identity(wt, [commit_as(a, c)])
        assert s.status == VM.REJECT and other in "\n".join(s.lines)


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


def test_direct_scope_does_not_widen_and_runs_new_declarations_first():
    """PR 범위 — 직접 닿은 변이만. FULL_TRIGGERS 로 넓히면 머지의 절반이 다시 전체다(최근 34 머지 중 17건 이상)."""
    a = {"id": "a", "guards": ["tests/t1.py::x"], "file": "src/a.py", "find": "1"}
    b = {"id": "b", "guards": ["tests/t2.py::y"], "edits": [{"file": "src/b.py", "find": "2"}]}
    c = {"id": "c", "guards": ["tests/t3.py::z"], "file": "src/c.py", "find": "3"}
    base = {"a": a, "b": b, "c": dict(c, find="old")}
    pick = lambda ch: [m["id"] for m in VM.direct_scope([a, b, c], ch, base)[0]]  # noqa: E731
    assert pick(set()) == ["c"]                                  # 선언이 바뀐 것은 늘
    assert pick({"src/a.py"}) == ["c", "a"]                      # 새 선언이 먼저
    assert pick({"tests/t2.py"}) == ["c", "b"]
    for full in ("uv.lock", "pyproject.toml", "scripts/sector_flow.py", "scripts/templates/x.html"):
        assert pick({full}) == ["c"], full                       # 넓히지 않는다 — 전체는 main push 가 돈다
    assert pick({"src/a.py", "src/b.py", "src/c.py"}) == ["c", "a", "b"]


def test_direct_scope_runs_the_touched_mutation_and_only_warns_on_budget(mini):
    """PR 범위가 진짜로 주입하고(이름만 바꾼 건너뛰기가 아니다), 시간이 넘치면 PR 을 막는 대신 멈추고 경고한다."""
    wt, _ = mini
    other = BLIND.replace('file = "lib.py"', 'file = "other.py"').replace(
        "sub-adds-but-guard-is-blind", "other")
    (wt / "tests" / "mutations.toml").write_text(SPEC + other, "utf-8")
    (wt / "other.py").write_text("def sub(a, b):\n    return a - b\n", "utf-8")
    _commit(wt, "other")
    base = _git(wt, "rev-parse", "HEAD")
    (wt / "lib.py").write_text(LIB + "\n", "utf-8")
    _commit(wt, "touch lib")
    s, out = _run(wt, base, scope="direct", budget_min=12)
    assert s.status == VM.PASS and "범위: direct" in out and "1/2개" in out
    assert "[add-subtracts] 잡힘" in out and "[other]" not in out
    s, out = _run(wt, base, scope="direct", budget_min=1e-6)
    assert s.status == VM.WARN and "돌리지 않고" in out and "main push" in out and "잡힘" not in out


def test_budget_overrun_stops_and_rejects(mini):
    wt, base = mini
    s, out = _run(wt, base, budget_min=1e-6)
    assert s.status == VM.REJECT and "돌리지 않고" in out and "잡힘" not in out


def test_local_changed_scope_warns_on_time_instead_of_blocking_the_push(mini):
    """pre-push 가 sector_flow·verify_merge 를 건드리면 changed 가 전체라 37분쯤 든다. 시간으로 막으면 푸시가 전부 막힌다 — 전체는 main 이 돈다."""
    wt, base = mini
    (wt / "lib.py").write_text(LIB + "\n", "utf-8")
    _commit(wt, "touch")
    s, out = _run(wt, base, scope="changed", budget_min=1e-6)
    assert s.status == VM.WARN and "돌리지 않고" in out and "main push" in out


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

    def fake_verify(wt, base, report, only, py, spec, shard, budget, scope, *rest):
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


def test_children_get_the_memory_cap(tmp_path, monkeypatch):
    """하네스가 띄우는 모든 자식(변이 주입 pytest·기준선 포함)에 상한이 걸린다. 값만 읽는다 — 할당하지 않는다."""
    monkeypatch.setenv(VM.memcap.LIMIT_ENV, "1024")
    p = VM.run([sys.executable, "-c", "import resource as r; print(r.getrlimit(r.RLIMIT_DATA)[0])"], tmp_path)
    assert int(p.stdout) == 1024 ** 3


def test_a_mutant_stopped_by_the_memory_cap_is_not_caught(mini, monkeypatch):
    """폭주를 상한(또는 커널)이 끊은 빨강을 '잡힘' 으로 세면 안 된다 — 11:05·11:25 의 OOM 이 그렇게 '잡힘 14.9초' 로 지나갔다.
    상한이 없으면 변이가 할당 전에 단언으로 죽으니 이 검사 자신은 폭주하지 않는다."""
    monkeypatch.setenv(VM.memcap.LIMIT_ENV, "1024")
    wt, base = mini
    grow = ("    import resource\\n    assert resource.getrlimit(resource.RLIMIT_DATA)[0] == " + str(1024 ** 3)
            + "\\n    return [bytearray(10 ** 8) for _ in iter(int, 1)]")
    (wt / "tests" / "mutations.toml").write_text(SPEC.replace('replace = "    return a - b"', f'replace = "{grow}"'), "utf-8")
    _commit(wt, "runaway mutation")
    s, out = _run(wt, base)
    assert s.status == VM.REJECT and "[add-subtracts] 판정 불가 — 메모리 상한" in out, out
    assert "[add-subtracts] 잡힘" not in out
    assert (wt / "lib.py").read_text("utf-8") == LIB


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


# ── 머지당 분을 줄인 장치들(잡 하나·기준선 공유·동시 실행·바이트코드)이 판정을 바꾸지 않는가 ──────────────────────────


def _with_package(wt: Path) -> None:
    """하네스의 임포트 출처 검사가 지나가게 — 자식 하네스는 ``sectorflow`` 가 자기 사본에서 오는지 본다."""
    (wt / "src" / "sectorflow").mkdir(parents=True)
    (wt / "src" / "sectorflow" / "__init__.py").write_text("", "utf-8")
    _commit(wt, "package")


def test_full_baseline_doubles_as_the_test_run_and_rejects_a_red_test_no_mutation_guards(mini):
    """전체 pytest 가 가드 기준선을 겸하면 main 의 pytest 한 벌이 사라진다 — 가드가 아닌 검사의 실패도 막아야 그 한 벌을 대신한다."""
    wt, base = mini
    s, out = _run(wt, base, full_baseline=True)
    assert s.status == VM.PASS and "전체 pytest 2건이 가드 1개를 겸한다" in out and "[add-subtracts] 잡힘" in out
    (wt / "tests" / "test_other.py").write_text("def test_red():\n    assert False\n", "utf-8")
    _commit(wt, "red, not a guard")
    s, out = _run(wt, base, full_baseline=True)
    assert s.status == VM.REJECT and "pytest 실패 1건" in out and "test_red" in out
    s, out = _run(wt, _git(wt, "rev-parse", "HEAD"))                # 가드만 도는 기준선은 그 검사를 안 본다
    assert s.status == VM.PASS, out


def test_baseline_leaves_bytecode_that_injection_keeps_valid(mini):
    """기준선만 .pyc 를 쓴다 — 안 쓰면 변이마다 전부 다시 컴파일해 러너에서 주입이 두 배였다. 주입이 .pyc 를 덮거나 원본 mtime 을 잃으면
    다음 실행이 옛 바이트코드를 읽거나 다시 컴파일한다."""
    wt, base = mini
    before = (wt / "lib.py").stat().st_mtime_ns
    s, out = _run(wt, base)
    assert s.status == VM.PASS, out
    assert (wt / "lib.py").stat().st_mtime_ns == before            # 복구는 mtime 까지
    pycs = list((wt / "__pycache__").glob("lib.*.pyc"))
    assert pycs, "기준선이 .pyc 를 안 남겼다"
    # 헤더: 매직 4 + 플래그 4 + 원본 mtime(초) 4 + 크기 4. 주입 실행이 쓴 것이면 변이의 mtime 이 박힌다.
    assert int.from_bytes(pycs[0].read_bytes()[8:12], "little") == before // 10 ** 9


def test_each_mutant_gets_an_mtime_no_bytecode_can_match(tmp_path):
    """.pyc 는 (mtime 초, 크기)로 맞춰 본다 — 같은 크기의 변이가 원본·앞 변이와 같은 초면 변이가 **안 들어간 채** 판정한다."""
    f = tmp_path / "m.py"
    f.write_text("x = 1\n", "utf-8")
    t0 = f.stat().st_mtime_ns
    seen = {t0 // 10 ** 9}
    for _ in range(3):
        VM._stamp_mutant(f, t0)
        seen.add(f.stat().st_mtime_ns // 10 ** 9)
    assert len(seen) == 4


def _parallel(wt: Path, base: str, **kw):
    s = VM.sec_mutations_parallel(wt, sys.executable, base, None, kw.pop("budget_min", None), 2, kw.pop("full", False))
    return s, "\n".join(s.lines)


def test_parallel_injects_every_mutation_once_in_copies_and_baselines_once(mini):
    """동시 실행이 몫을 빠뜨리거나 겹치면 초록이 거짓말이다. 기준선은 부모 한 번뿐이고 원본 트리는 손대지 않는다."""
    wt, _ = mini
    _with_package(wt)
    (wt / "tests" / "test_lib.py").write_text(TESTS + "\n\ndef test_sub():\n    assert sub(5, 3) == 2\n", "utf-8")
    other = BLIND.replace("tests/test_lib.py::test_blind", "tests/test_lib.py::test_sub").replace(
        "sub-adds-but-guard-is-blind", "sub-adds")
    (wt / "tests" / "mutations.toml").write_text(SPEC + other, "utf-8")
    _commit(wt, "two mutations")
    base = _git(wt, "rev-parse", "HEAD")
    s, out = _parallel(wt, base, budget_min=30, full=True)
    assert s.status == VM.PASS, out
    assert out.count("[add-subtracts] 잡힘") == 1 and out.count("[sub-adds] 잡힘") == 1, out
    assert out.count("기준선:") == 1 and "전체 pytest 3건" in out and "동시 2" in out
    assert (wt / "lib.py").read_text("utf-8") == LIB
    assert not _git(wt, "status", "--porcelain")


def test_clone_keeps_source_folders_named_like_outputs_and_the_bytecode_timestamps(mini, tmp_path):
    """뿌리의 ``reports/`` 는 산출물이지만 ``docs/strategy/reports`` 는 소스다 — 빠지면 사본의 문서 검사가 주입 없이도 빨갛다.
    mtime 을 잃으면 기준선의 ``.pyc`` 가 사본에서 안 맞아 동시 실행이 다시 컴파일한다."""
    wt, _ = mini
    (wt / "docs" / "reports").mkdir(parents=True)
    (wt / "docs" / "reports" / "x.md").write_text("문서", "utf-8")
    (wt / "reports").mkdir()
    (wt / "reports" / "big.html").write_text("산출물", "utf-8")
    before = (wt / "lib.py").stat().st_mtime_ns
    copy = tmp_path / "copy"
    VM._clone_tree(wt, copy)
    assert (copy / "docs" / "reports" / "x.md").exists() and not (copy / "reports").exists()
    assert (copy / "lib.py").stat().st_mtime_ns == before
    assert (copy / ".git").is_dir()


def test_parallel_passes_a_red_mutation_verdict_up(mini):
    wt, _ = mini
    _with_package(wt)
    (wt / "tests" / "mutations.toml").write_text(SPEC + BLIND, "utf-8")
    _commit(wt, "blind")
    s, out = _parallel(wt, _git(wt, "rev-parse", "HEAD"))
    assert s.status == VM.REJECT and "[sub-adds-but-guard-is-blind] 변이를 넣었는데" in out and "REJECT 다" in out, out


def test_parallel_full_baseline_failure_is_rejected(mini):
    wt, _ = mini
    _with_package(wt)
    (wt / "tests" / "test_other.py").write_text("def test_red():\n    assert False\n", "utf-8")
    _commit(wt, "red")
    s, out = _parallel(wt, _git(wt, "rev-parse", "HEAD"), full=True)
    assert s.status == VM.REJECT and "pytest 실패 1건" in out, out


def test_parallel_child_that_leaves_no_result_is_rejected(mini, monkeypatch):
    """자식이 죽으면(메모리·시그널) 그 몫은 돌지 않았다 — 나머지 몫이 초록이어도 통과가 아니다."""
    wt, base = mini
    monkeypatch.setattr(VM, "run_baseline", lambda *a, **k: ({"tests/test_lib.py::test_add": {"outcome": "passed"}}, [], ""))
    s = VM.sec_mutations_parallel(wt, "/bin/false", base, None, None, 2, False)
    assert s.status == VM.REJECT and "결과를 못 남겼다" in "\n".join(s.lines)


def test_parallel_is_only_for_a_tree_with_its_own_git_dir(tmp_path):
    """링크드 워크트리의 .git 은 파일이다 — 사본들이 인덱스 하나를 나눠 쓰게 된다."""
    (tmp_path / ".git").write_text("gitdir: /x\n", "utf-8")
    assert not VM.can_parallel(tmp_path)
    (tmp_path / ".git").unlink()
    (tmp_path / ".git").mkdir()
    assert VM.can_parallel(tmp_path)


def test_scoped_runs_ignore_workers_and_the_full_baseline(mini, monkeypatch):
    """ci-local 은 gate 잡을 changed 범위로 돈다 — 전체 pytest 는 같은 워크플로의 test 잡이 돌았고, 사본 동시 실행은 전체 범위의 것이다."""
    wt, base = mini
    calls = {}

    def seq(_wt, _py, _base, _spec, shard, budget, scope, full, bj):
        calls["seq"] = (scope, full)
        return VM.Section("mutations")
    monkeypatch.setattr(VM, "sec_mutations", seq)
    monkeypatch.setattr(VM, "sec_mutations_parallel", lambda *a: calls.update(par=a) or VM.Section("mutations"))
    monkeypatch.setattr(VM, "sec_worktree", lambda *a: (VM.Section("worktree"), []))
    VM.verify(wt, base, "", {"mutations"}, sys.executable, None, None, 35, "changed", workers=2, full_baseline=True)
    assert calls == {"seq": ("changed", False)}
    calls.clear()
    VM.verify(wt, base, "", {"mutations"}, sys.executable, None, None, 35, "all", workers=2, full_baseline=True)
    assert "par" in calls and calls["par"][-2:] == (2, True) and "seq" not in calls
    calls.clear()
    monkeypatch.setattr(VM, "can_parallel", lambda _wt: False)
    VM.verify(wt, base, "", {"mutations"}, sys.executable, None, None, 35, "all", workers=2, full_baseline=True)
    assert calls == {"seq": ("all", True)}


def test_a_tree_kill_during_a_mutant_is_not_a_catch(mini, monkeypatch):
    """감시자가 검사의 자식을 죽이면 검사가 실패해 종료코드 1 이 된다 — 그 빨강을 '잡힘' 으로 세면 안 된다."""
    wt, base = mini
    counts = iter(range(100))
    monkeypatch.setattr(VM.memcap, "tree_kills", lambda: next(counts))
    s, out = _run(wt, base)
    assert s.status == VM.REJECT and "[add-subtracts] 판정 불가 — 이 변이가 도는 동안" in out and "잡힘" not in out


def _main_seen(monkeypatch, argv: list[str]) -> dict:
    seen: dict = {}

    def fake(wt, base, report, only, py, spec, shard, budget, scope, demo, required, *rest):
        seen.update(report=report, demo=demo, required=required)
        return VM.PASS, []
    monkeypatch.setattr(VM, "verify", fake)
    assert VM.main(argv) == 0
    return seen


def test_cli_uses_the_demo_only_when_no_folder_was_given_and_the_default_is_missing(monkeypatch, tmp_path):
    (tmp_path / "wt").mkdir()
    wt = str(tmp_path / "wt")
    monkeypatch.setattr(VM, "DEFAULT_REPORT", str(tmp_path / "none"))
    assert _main_seen(monkeypatch, [wt, "--only", "render"])["demo"] is True
    assert _main_seen(monkeypatch, [wt, "--only", "render", "--report-dir", str(tmp_path / "typo")])["demo"] is False
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "numbers.html").write_text("x", "utf-8")
    monkeypatch.setattr(VM, "DEFAULT_REPORT", str(tmp_path / "real"))
    assert _main_seen(monkeypatch, [wt, "--only", "render"])["demo"] is False
    assert _main_seen(monkeypatch, [wt, "--only", "render", "--demo"])["demo"] is True
    # 이름을 댄 층·폴더를 댄 화면 층만 필수다 — 인자 없는 전체 실행의 SKIP 은 SKIP 으로 남는다.
    assert _main_seen(monkeypatch, [wt, "--only", "render"])["required"] == {"render"}
    assert _main_seen(monkeypatch, [wt, "--report-dir", str(tmp_path / "typo")])["required"] == {"render", "pty"}
    assert _main_seen(monkeypatch, [wt])["required"] == set()


def test_a_layer_that_was_asked_for_and_could_not_run_is_rejected_not_passed(mini, tmp_path):
    """폴더 오타로 화면 층이 전부 건너뛰어졌는데 ``판정: PASS``·rc 0 이었다. 요청한 층의 SKIP 은 통과가 아니다."""
    wt, base = mini
    _with_package(wt)
    missing = str(tmp_path / "typo")
    verdict, secs = VM.verify(wt, base, missing, {"render", "pty"}, sys.executable, None, required={"render", "pty"})
    assert verdict == VM.REJECT and all(s.status == VM.REJECT for s in secs if s.name in ("render", "pty"))
    verdict, secs = VM.verify(wt, base, missing, {"render", "pty"}, sys.executable, None)      # 요청 안 했으면 SKIP 그대로
    assert verdict == VM.PASS and {s.status for s in secs if s.name in ("render", "pty")} == {VM.SKIP}


def test_demo_that_cannot_be_built_is_rejected(mini):
    wt, base = mini
    _with_package(wt)                                       # 빈 패키지 — sectorflow.demo 가 없다
    verdict, secs = VM.verify(wt, base, "", {"render"}, sys.executable, None, demo=True)
    assert verdict == VM.REJECT and "합성 데모를 짓지 못했다" in "\n".join(secs[0].lines)


def test_render_smoke_expects_the_rights_mark_on_the_cap_share_column():
    """시총대비 칸의 `!`(cap_pending)를 기대값이 몰라 합성 데모에서 거짓 위반 736건이 났다. 화면이 표지를 빠뜨리면 이 기대값이 잡는다."""
    import ast

    sys.path.insert(0, str(REPO / "src"))
    from sectorflow.tui import flow_view as FV
    tree = ast.parse(VM.RENDER_SMOKE)
    keep = [n for n in tree.body
            if (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "EXPECT" for t in n.targets))
            or (isinstance(n, ast.FunctionDef) and n.name == "want")]
    ns: dict = {"V": FV}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "render_smoke", "exec"), ns)
    col = next(c for c in FV.names_cols() if c.header == "시총대비[%p]")
    for row in ({"a": 0.43, "cap_pending": True}, {"a": 0.43}, {"a": None}):
        assert ns["want"](row, "시총대비[%p]") == col.fn(row, None), row
    assert ns["want"]({"a": 0.43, "cap_pending": True}, "시총대비[%p]").endswith(FV.CAP_PENDING_MARK)


def test_self_test_clears_base_mutations_for_mutation_scenarios(mini, monkeypatch):
    """변이 시나리오는 기준선 mutations.toml 을 비워 '변이 사라짐' 오탐 없이 1건만 본다."""
    wt, _ = mini
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (wt / "README.md").write_text("# demo\n", "utf-8")
    (wt / "tests/test_tui_flow_view.py").write_text("def test_data_cells_sit_under_their_headers(): pass\n", "utf-8")
    _commit(wt, "with tui test")
    monkeypatch.setattr(VM, "REPO", wt)
    seen = {}

    def fake_verify(sc_wt, base_branch, report, only, py, m_path):
        if only == {"mutations"}:
            seen[sc_wt.name] = _git(sc_wt, "show", f"{base_branch}:tests/mutations.toml")
        want_reject = sc_wt.name in {"broken-test", "bad-identity", "blind-guard", "dead-anchor", "undeclared-test", "bare-exempt-test"}
        st = VM.REJECT if want_reject else VM.PASS
        sec = next(iter(only)) if len(only) == 1 else "tests"
        return st, [VM.Section(sec, st)]

    monkeypatch.setattr(VM, "verify", fake_verify)
    assert VM.self_test(sys.executable, "") == 0
    assert seen["real-guard"] == ""
    assert seen["exempt-test"] == ""
