"""문서가 가리키는 `파일:줄` 이 지금 코드에 맞는가 — 규약과 고치는 도구는 ``scripts/docs_refs.py``.

docs/formulas.md 는 식마다 계산하는 코드 위치를 `path:줄` 로 적는다. 코드가 움직이면 줄 번호가
조용히 낡는다(2026-11-08 하루 아홉 PR 뒤 짝 인용 105개 중 55개가 어긋났다). 그래서 모든 인용이 있는
파일·줄 범위를 가리키는지, 짝 지은 기호가 그 줄 ±3 안에 있는지 본다. 어긋나면 ``--fix`` 가 옮긴다.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("docs_refs", REPO / "scripts" / "docs_refs.py")
dr = importlib.util.module_from_spec(_spec)
sys.modules["docs_refs"] = dr           # dataclass 가 자기 모듈을 sys.modules 에서 찾는다
_spec.loader.exec_module(dr)


def test_docs_cite_code_at_all():
    # 정규식이 깨져 아무것도 못 찾으면 아래 검사가 전부 헛돈다.
    rs = dr.refs((REPO / "docs/formulas.md").read_text(encoding="utf-8"))
    assert len(rs) >= 150
    assert sum(1 for r in rs if r.sym) >= 60


#: 짝 없는(줄만 적은) 인용의 상한. 이 수는 **줄기만** 한다 — 새 인용은 기호를 같이 적는다(검사가 위치를
#: 못 보는 인용이 늘면 이 파일의 보장이 그만큼 줄어든다). 짝을 지으면 낮춘다.
UNPAIRED_MAX = 123


def test_line_only_refs_do_not_grow():
    rs = dr.refs((REPO / "docs/formulas.md").read_text(encoding="utf-8"))
    n = sum(1 for r in rs if r.sym is None)
    assert n <= UNPAIRED_MAX, f"짝 없는 인용이 {n}개로 늘었다(상한 {UNPAIRED_MAX}) — 새 인용에 `기호` 를 같이 적는다"


def test_every_cited_line_exists_and_paired_symbol_is_there():
    bad = dr.check(REPO)
    assert not bad, ("문서의 코드 인용이 낡았다 — `" + dr.FIX_HINT + "` 를 돌리고, 남는 것은 손으로 고친다:\n"
                     + "\n".join(bad))


@pytest.mark.parametrize(
    "line, want",
    [
        ("`EOK = 1e8` `scripts/sector_flow.py:37`.", "EOK"),
        ("`src/a.py:117`(`load`), `scripts/b.sh:1`", "load"),
        ("`State._leads` `src/a.py:388-418`(부호", "_leads"),
        ("열 `src/a.py:1212`", None),
        ("`(미분류)` `src/a.py:3`", None),
        ("`src/a.py:1` `src/b.py:2`", None),
        ("`def load(path)` `src/a.py:3`", "load"),      # 키워드는 건너뛴다
        ("`class State` `src/a.py:3`", "State"),
    ],
)
def test_pairing_convention(line, want):
    assert dr.refs(line)[0].sym == want


def test_def_span_requires_the_definition_line(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("x = load(1)\n\n\n\n\n\n\ndef load(p):\n    return p\n", encoding="utf-8")
    ok = dr.refs("`def load` `src/a.py:8`")[0]
    call = dr.refs("`def load` `src/a.py:1`")[0]
    assert ok.defn and dr.problem(tmp_path, ok) is None
    assert dr.problem(tmp_path, call)                # 부르는 줄은 정의가 아니다


# ── --fix ───────────────────────────────────────────────────────────────

SRC = """\
import os

EOK = 1e8


def load(path):
    x = 1
    return x


def helper():
    return load("a")
"""

DOC = """\
# 문서
<!-- refs-verified-at: 0000000 -->

`EOK = 1e8` `src/m.py:3` · `src/m.py:6-8`(`def load`) · 줄만 `src/m.py:12` · 부르는 곳 `src/m.py:12`(`load`).
"""
DOCS = ("docs/formulas.md",)


def _tree(root: Path, src: str = SRC, doc: str = DOC) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "src/m.py").write_text(src, encoding="utf-8")
    (root / "docs/formulas.md").write_text(doc, encoding="utf-8")
    return root


def _shift(root: Path, n: int, at: int = 1) -> None:
    f = root / "src/m.py"
    lines = f.read_text(encoding="utf-8").split("\n")
    f.write_text("\n".join(lines[:at] + ["# pad"] * n + lines[at:]), encoding="utf-8")


def _doc(root: Path) -> str:
    return (root / "docs/formulas.md").read_text(encoding="utf-8")


def test_fixture_starts_clean(tmp_path):
    assert dr.check(_tree(tmp_path), DOCS) == []


@pytest.mark.parametrize("n", [2, 25])
def test_fix_restores_shifted_file_from_the_base_version(tmp_path, n):
    """기준 소스와 지금을 diff 해 짝 인용도, 줄만 적은 인용도, ±3 안에서 밀린 인용도 따라간다."""
    base = _tree(tmp_path / "base")
    work = tmp_path / "work"
    shutil.copytree(base, work)
    _shift(work, n)
    changed, left, _ = dr.fix(work, DOCS, base_tree=base)
    assert left == [] and changed == 4
    assert dr.check(work, DOCS) == []
    got = _doc(work)
    assert f"`src/m.py:{3 + n}`" in got
    assert f"`src/m.py:{6 + n}-{8 + n}`(`def load`)" in got
    assert f"줄만 `src/m.py:{12 + n}`" in got
    assert f"부르는 곳 `src/m.py:{12 + n}`(`load`)" in got


def _call_ref() -> dr.Ref:
    return [r for r in dr.refs(DOC) if r.sym == "load" and not r.defn][0]


def test_relocation_follows_content_not_the_definition():
    """diff 가 못 따라간 줄(블록이 옮겨짐)은 기준 소스의 그 줄과 내용이 같은 줄로 — 부르는 곳은 부르는 곳으로."""
    old = SRC.split("\n")
    helper = ["def helper():", '    return load("a")', "", ""]
    cur = old[:4] + helper + [ln for ln in old[4:] if ln not in helper[:2]]
    n1, n2 = dr._by_content(old, cur, _call_ref())
    assert cur[n1 - 1] == '    return load("a")' and n1 == n2 == 6


def test_relocation_reports_ambiguous_content_instead_of_guessing():
    old = SRC.split("\n")
    cur = old + ["def other():", '    return load("a")']
    got = dr._by_content(old, ["# pad"] + cur, _call_ref())
    assert isinstance(got, str) and "모호" in got


def test_fix_without_history_moves_paired_refs_by_symbol(tmp_path):
    """기준 소스가 없으면 기호로 — 하나뿐이거나 정의 꼴일 때만. 줄만 적은 인용은 손대지 않는다."""
    root = _tree(tmp_path, doc=DOC.replace(" · 부르는 곳 `src/m.py:12`(`load`)", ""))
    _shift(root, 10)
    changed, left, notes = dr.fix(root, DOCS, base_tree=tmp_path / "없음")
    assert left == [] and changed == 2
    assert "`src/m.py:13`" in _doc(root) and "`src/m.py:16-18`(`def load`)" in _doc(root)
    assert "줄만 `src/m.py:12`" in _doc(root)
    assert any("범위만" in x for x in notes)


def test_fix_without_history_reports_ambiguous_symbol(tmp_path):
    root = _tree(tmp_path)
    _shift(root, 10)
    _, left, _ = dr.fix(root, DOCS, base_tree=tmp_path / "없음")
    assert any("`load`" in x and "모호" in x for x in left)
    assert "부르는 곳 `src/m.py:12`(`load`)" in _doc(root)


def test_fix_reports_missing_symbol(tmp_path):
    root = _tree(tmp_path, doc=DOC.replace("(`def load`)", "(`unload`)"))
    _, left, _ = dr.fix(root, DOCS, base_tree=tmp_path / "없음")
    assert any("`unload`" in x and "없다" in x for x in left)


def test_fix_leaves_uncommitted_new_refs_to_the_symbol_step(tmp_path):
    """기준 문서에 없던 줄(그 뒤에 쓴 인용)은 지금 코드를 보고 쓴 것이라 diff 로 옮기지 않는다."""
    base = _tree(tmp_path / "base")
    work = tmp_path / "work"
    shutil.copytree(base, work)
    _shift(work, 5)
    doc = work / "docs/formulas.md"
    doc.write_text(_doc(work) + "새 줄 `src/m.py:16`(`def helper`).\n", encoding="utf-8")
    _, left, _ = dr.fix(work, DOCS, base_tree=base)
    assert left == []
    assert "새 줄 `src/m.py:16`(`def helper`)" in _doc(work)


def test_check_requires_the_stamp(tmp_path):
    root = _tree(tmp_path, doc=DOC.replace("<!-- refs-verified-at: 0000000 -->\n", ""))
    assert any("refs-verified-at" in x for x in dr.check(root, DOCS))


def test_cli_check_prints_the_fix_hint(tmp_path, monkeypatch, capsys):
    root = _tree(tmp_path)
    _shift(root, 10)
    monkeypatch.setattr(dr, "ROOT", root)
    assert dr.main(list(DOCS)) == 1
    assert dr.FIX_HINT in capsys.readouterr().out


# ── 기본 CLI 경로 — 진짜 git 저장소에서 기준 표시를 읽고 갱신한다 ─────────────────

def _g(root: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True,
                          check=True).stdout.strip()


def _repo(root: Path) -> Path:
    _tree(root)
    _g(root, "init", "-q")
    # 임시 저장소 안에서만 쓰는 신원 — 이 레포의 설정을 그대로 빌린다(verify_merge 자체 검사와 같은 방식).
    mail = subprocess.run(["git", "-C", str(REPO), "config", "--get", "user.email"],
                          capture_output=True, text=True).stdout.strip() or "ci@example.invalid"
    for k, v in (("user.email", mail), ("user.name", "docs-refs test"), ("commit.gpgsign", "false")):
        _g(root, "config", k, v)
    return root


def _commit(root: Path, msg: str) -> str:
    _g(root, "add", "-A")
    _g(root, "commit", "-q", "--no-verify", "-m", msg)
    return _g(root, "rev-parse", "HEAD")


def test_cli_fix_uses_the_stamp_not_the_last_doc_commit(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path / "r")
    c1 = _commit(root, "code")
    doc = root / "docs/formulas.md"
    doc.write_text(_doc(root).replace("0000000", c1), encoding="utf-8")
    _commit(root, "stamp")
    _shift(root, 7)
    c3 = _commit(root, "code moves")
    doc.write_text(_doc(root).replace("# 문서", "# 문서 (오타 고침)"), encoding="utf-8")
    _commit(root, "typo-only doc commit")            # 문서를 마지막으로 고친 커밋이 기준이면 diff 가 0 이다
    monkeypatch.setattr(dr, "ROOT", root)
    assert dr.main(["--fix", *DOCS]) == 0
    got = _doc(root)
    assert "줄만 `src/m.py:19`" in got and "`src/m.py:13-15`(`def load`)" in got
    assert f"refs-verified-at: {_g(root, 'rev-parse', 'HEAD')}" in got and c3 != c1

    # 커밋 안 한 코드 변경 위에서는 옮기되 기준 표시는 그대로 둔다.
    typo = STAMP_OF(got)
    _commit(root, "refs")
    _shift(root, 3)
    capsys.readouterr()
    assert dr.main(["--fix", *DOCS]) == 0
    assert "커밋 안 한 변경" in capsys.readouterr().out
    got = _doc(root)
    assert "줄만 `src/m.py:22`" in got and "`src/m.py:16-18`(`def load`)" in got
    assert STAMP_OF(got) == typo


def STAMP_OF(text: str) -> str:
    m = dr.STAMP.search(text)
    return m.group(1) if m else ""
