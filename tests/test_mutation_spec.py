"""변이 선언(`tests/mutations.toml`)이 **아직 지금 코드에 붙어 있는가** — DB·주입 없이.

`scripts/verify_merge.py` 는 변이를 실제로 넣어 가드가 빨개지는지 본다(수 분). 그런데
앵커(`find`)가 리팩터로 사라지면 그 변이는 돌지 않았고, 예전엔 경고만 남겼다. 그렇게
변이 셋(`chip-claims-final`·`ledger-footer-writes-both-cases-again`·
`ledger-help-reports-a-stale-neighbour`)이 **조용히 죽었다** — 회귀 검사는 초록인데 그
검사가 버그를 잡는지는 아무도 확인하지 않는 상태였다.

여기서는 주입 없이 선언의 정합성만 본다. 빠르니 평소 pytest·CI 에서 돈다:

* id 가 비어 있지 않고 겹치지 않는다.
* 모든 편집의 `find` 가 대상 파일에 **정확히 한 번** 나온다 — 다중 편집은 앞 편집을
  넣은 뒤의 텍스트에서(하네스가 그 순서로 넣는다).
* `find` 와 `replace` 가 다르다 — 같으면 주입이 아무것도 안 바꾼다.
* `guards` 의 nodeid 가 실제 검사 함수를 가리킨다 — 오타면 하네스는 "가드를 못 찾았다"
  경고만 낸다.
"""

from __future__ import annotations

import ast
import importlib.util
import tomllib
from collections import Counter
from functools import cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "tests" / "mutations.toml"
MUTATIONS = tomllib.loads(SPEC.read_text("utf-8"))["mutation"]


def _edits(m: dict) -> list[dict]:
    """하네스(`verify_merge.sec_mutations`)와 같은 해석."""
    return m.get("edits") or [{"file": m.get("file"), "find": m.get("find"),
                               "replace": m.get("replace", "")}]


@cache
def _test_names(path: str) -> frozenset[str]:
    """그 파일의 검사 nodeid 꼬리 — `test_x` 와 `Class::test_x`."""
    tree = ast.parse((REPO / path).read_text("utf-8"))
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(node.name)
        elif isinstance(node, ast.ClassDef):
            out.update(f"{node.name}::{f.name}" for f in node.body
                       if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return frozenset(out)


def test_spec_is_not_empty_and_ids_are_unique():
    assert MUTATIONS, f"{SPEC} 에 [[mutation]] 이 없다"
    ids = [m.get("id") for m in MUTATIONS]
    assert all(ids), "id 가 빈 변이가 있다"
    dup = [i for i, n in Counter(ids).items() if n > 1]
    assert not dup, f"id 가 겹친다: {dup}"


@pytest.mark.parametrize("m", MUTATIONS, ids=[m.get("id", "?") for m in MUTATIONS])
def test_every_anchor_still_matches_exactly_once(m):
    texts: dict[str, str] = {}
    for i, e in enumerate(_edits(m)):
        f, find, rep = e.get("file"), e.get("find"), e.get("replace", "")
        assert f and find, f"편집 {i}: file·find 가 필요하다"
        assert find != rep, f"편집 {i}: find 와 replace 가 같아 주입이 아무것도 안 바꾼다"
        if f not in texts:
            assert (REPO / f).is_file(), f"대상 파일이 없다: {f}"
            texts[f] = (REPO / f).read_text("utf-8")
        n = texts[f].count(find)
        assert n == 1, (f"편집 {i}: 앵커가 {f} 에 {n}번 나온다(정확히 1번이어야 한다). "
                        f"0번이면 리팩터로 사라진 것이다 — 앵커를 지금 코드에 맞추거나, "
                        f"버그가 표현 불가능해졌으면 변이를 지워라.\n앵커: {find!r}")
        texts[f] = texts[f].replace(find, rep, 1)


@pytest.mark.parametrize("m", MUTATIONS, ids=[m.get("id", "?") for m in MUTATIONS])
def test_every_guard_names_a_real_test(m):
    guards = m.get("guards")
    assert guards, "guards 가 비었다 — 무엇이 이 변이를 잡아야 하는지 선언이 없다"
    for g in guards:
        path, sep, name = g.partition("::")
        assert sep and name, f"nodeid 형식이 아니다: {g}"
        assert (REPO / path).is_file(), f"가드 파일이 없다: {path}"
        assert name.split("[")[0] in _test_names(path), f"가드 검사가 없다: {g}"


def test_the_harness_self_test_anchor_is_live():
    """`verify_merge.py --self-test` 의 변이도 앵커를 잃으면 시나리오가 엉뚱한 이유로 판정된다."""
    spec = importlib.util.spec_from_file_location("verify_merge",
                                                  REPO / "scripts" / "verify_merge.py")
    vm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vm)
    assert (REPO / vm.SELF_TEST_FILE).read_text("utf-8").count(vm.SELF_TEST_FIND) == 1
    path, _, name = vm.SELF_TEST_GUARD.partition("::")
    assert name in _test_names(path)


#: 변이가 없어도 되는 쉘·템플릿 — (경로, 이유). 그 파일에 변이가 생기면 여기서 지운다.
NO_MUTATION_OK = {
    "scripts/freshness_check.sh": "환경 변수 사본을 만들고 exec 만 한다 — 크론 경로는 test_freshness_check 가 가짜 uv 로 태운다",
}


def test_every_shell_script_and_template_is_a_mutation_target():
    """.sh·.html 은 줄 커버리지 밖이라 변이가 유일한 근거다 — 대상이 아닌 파일은 무엇을 고쳐도 아무도 모른다."""
    targets = {e.get("file") for m in MUTATIONS for e in _edits(m)}
    files = {p.relative_to(REPO).as_posix() for pat in ("scripts/*.sh", "scripts/templates/*.html")
             for p in REPO.glob(pat)}
    assert files - targets == set(NO_MUTATION_OK), files - targets
    assert not set(NO_MUTATION_OK) - files, "지운 파일이 표에 남았다"


# mutation-exempt: 지금 [[removed]] 가 비어 있어 주입해도 돌 행이 없다 — 선언 표의 정합성만 본다
def test_removed_entries_have_reasons_and_are_really_gone():
    doc = tomllib.loads(SPEC.read_text("utf-8"))
    ids = {m.get("id") for m in MUTATIONS}
    for r in doc.get("removed", []):
        assert r.get("id") and r.get("why"), r
        assert r["id"] not in ids, f"지웠다고 적었는데 남아 있다: {r['id']}"
