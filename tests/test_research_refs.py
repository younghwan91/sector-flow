"""연구 스크립트(`research/`)가 쓰는 리포트 코드의 이름이 **아직 있는가** — DB·실행 없이.

연구 스크립트는 `scripts/sector_flow.py`·`sector_numbers.py`·`sectorflow.storage` 를 sys.path 로 끌어다
쓴다(`vcommon.use_report_code`). 리팩터가 거기서 이름 하나를 지우거나 바꾸면 연구 스크립트는 다음에
누가 돌릴 때(몇 주 뒤, DB 에 붙어서)야 죽는다. 연구 스크립트는 import 하는 순간 DB·리포트 폴더를
읽으므로 진짜 import 는 못 한다 — 그래서 AST 로 ``sf.build_payload`` 같은 참조와 ``from X import Y`` 를
모아, 그 스크립트가 고른 **판**(지금 트리, 또는 사전등록 커밋 ``FIRST_RUN_REV``)의 최상위 이름과 맞댄다.
연구 폴더 안의 서로 참조(``from vcommon import out_dir``)도 같이 본다.
"""
from __future__ import annotations

import ast
import subprocess
from functools import cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RESEARCH = REPO / "research"
#: 연구 스크립트가 import 하는 리포트 코드 모듈 → 레포 경로.
REPORT_MODULES = {"sector_flow": "scripts/sector_flow.py",
                  "sector_numbers": "scripts/sector_numbers.py",
                  "sectorflow.storage": "src/sectorflow/storage.py"}
FILES = sorted(RESEARCH.rglob("*.py"))


def _top_names(tree: ast.Module) -> set[str]:
    out: set[str] = set()

    def walk(body):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(n.name)
            elif isinstance(n, ast.Assign):
                out.update(t.id for t in n.targets if isinstance(t, ast.Name))
                for t in n.targets:
                    if isinstance(t, ast.Tuple):
                        out.update(e.id for e in t.elts if isinstance(e, ast.Name))
            elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and isinstance(n.target, ast.Name):
                out.add(n.target.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                out.update((a.asname or a.name).split(".")[0] for a in n.names)
            elif isinstance(n, ast.If):
                walk(n.body)
                walk(n.orelse)
            elif isinstance(n, ast.Try):
                walk(n.body)
                for h in n.handlers:
                    walk(h.body)
                walk(n.orelse)
                walk(n.finalbody)
    walk(tree.body)
    return out


@cache
def _names_at(path: str, rev: str | None) -> frozenset[str] | None:
    if rev is None:
        src = (REPO / path).read_text("utf-8")
    else:
        p = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{path}"],
                           capture_output=True, text=True)
        if p.returncode != 0:
            return None                       # 얕은 클론 — 그 판이 없다
        src = p.stdout
    return frozenset(_top_names(ast.parse(src)))


def _first_run_rev() -> str:
    tree = ast.parse((RESEARCH / "validation" / "vcommon.py").read_text("utf-8"))
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "FIRST_RUN_REV" for t in n.targets):
            return n.value.value
    raise AssertionError("vcommon.FIRST_RUN_REV 가 없다")


def _rev_of(tree: ast.Module) -> str | None:
    """``use_report_code(FIRST_RUN_REV, …)`` 면 사전등록 판, 그 밖(None·인자 변수)은 지금 트리."""
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "use_report_code" and n.args:
            if isinstance(n.args[0], ast.Name) and n.args[0].id == "FIRST_RUN_REV":
                return _first_run_rev()
    return None


def _local_module(f: Path, name: str) -> Path | None:
    for d in (f.parent, RESEARCH / "validation"):
        if (d / f"{name}.py").exists():
            return d / f"{name}.py"
    return None


def _refs(f: Path) -> list[tuple[str, str | None, str]]:
    """(모듈 레포 경로, 판, 이름) — 이 파일이 쓰는 남의 최상위 이름."""
    tree = ast.parse(f.read_text("utf-8"))
    rev = _rev_of(tree)
    alias: dict[str, str] = {}
    out: list[tuple[str, str | None, str]] = []

    def target(mod: str) -> tuple[str, str | None] | None:
        if mod in REPORT_MODULES:
            return REPORT_MODULES[mod], rev
        local = _local_module(f, mod)
        return (local.relative_to(REPO).as_posix(), None) if local else None

    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if target(a.name):
                    alias[a.asname or a.name] = a.name
        elif isinstance(n, ast.ImportFrom) and n.module:
            if n.module == "sectorflow":
                for a in n.names:
                    if f"sectorflow.{a.name}" in REPORT_MODULES:
                        alias[a.asname or a.name] = f"sectorflow.{a.name}"
                continue
            t = target(n.module)
            if t:
                out += [(t[0], t[1], a.name) for a in n.names]
    for n in ast.walk(tree):                  # ``sn = sector_numbers`` 같은 다시 묶기
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Name) and n.value.id in alias:
            alias.update({t.id: alias[n.value.id] for t in n.targets if isinstance(t, ast.Name)})
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in alias \
                and not n.attr.startswith("__"):
            t = target(alias[n.value.id])
            out.append((t[0], t[1], n.attr))
    return out


def test_research_scripts_exist():
    assert FILES, "research/ 에 파이썬 파일이 없다 — 경로가 바뀌었나"


@pytest.mark.parametrize("f", FILES, ids=[str(f.relative_to(RESEARCH)) for f in FILES])
def test_research_script_refs_resolve(f):
    bad, skipped = [], []
    for path, rev, name in _refs(f):
        names = _names_at(path, rev)
        if names is None:
            skipped.append(f"{rev}:{path}")
        elif name not in names:
            bad.append(f"{path}@{rev or '작업트리'} 에 `{name}` 이 없다")
    if skipped and not bad:
        pytest.skip(f"그 판이 이 클론에 없다(얕은 클론?): {sorted(set(skipped))}")
    assert not bad, "\n".join(sorted(set(bad)))


def test_version_follows_use_report_code():
    """사전등록 판을 고른 스크립트는 그 판과, 지금 트리를 고른 스크립트는 지금 트리와 맞댄다.

    지금은 옛 판의 이름이 지금 트리에도 다 있어 위 검사만으로는 판 고르기가 틀려도 모른다."""
    assert _rev_of(ast.parse("use_report_code(FIRST_RUN_REV, d)")) == _first_run_rev()
    assert _rev_of(ast.parse("use_report_code(None, d)")) is None
    assert _rev_of(ast.parse("use_report_code(rev, d)")) is None
    assert len(_first_run_rev()) >= 7
