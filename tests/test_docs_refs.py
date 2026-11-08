"""문서가 가리키는 `파일:줄` 이 지금 코드에 맞는가.

docs/formulas.md 는 식마다 계산하는 코드 위치를 `path:줄` 로 적는다. 코드가 움직이면 줄 번호가
조용히 낡는다(2026-11-08 하루 아홉 PR 뒤 ~250개 중 대부분이 어긋났다). 그래서 둘을 본다.

1. 모든 `path:N`·`path:N-M` — 파일이 있고 N ≤ M ≤ 줄 수.
2. 짝 지은 기호 — 인용 바로 앞 `` `기호` `path:N` `` 이나 바로 뒤 `` `path:N`(`기호`) `` 로 적힌 기호가
   인용 줄 ±3 안에 실제로 있다. 기호는 코드 스팬의 맨 앞 식별자(`EOK = 1e8` → `EOK`,
   `State._leads` → `_leads`)이고, 식별자로 시작하지 않는 스팬(`(미분류)`·열 이름)은 짝이 아니다.

줄만 적은 인용(짝 없음)은 1 만 본다 — 위치를 틀리게 가리켜도 못 잡으니 새 인용은 기호를 같이 적는다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ("docs/formulas.md", "README.md", "docs/trading-guide.md")
SLACK = 3

_PATH = r"(?:scripts|src|tests|research|deploy)/[A-Za-z0-9_./-]+"
REF = re.compile(rf"`({_PATH}):(\d+)(?:-(\d+))?`")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
# 인용 바로 앞의 코드 스팬(사이에 공백만), 바로 뒤의 괄호 속 코드 스팬.
_BEFORE = re.compile(r"`([^`\n]+)`\s*$")
_AFTER = re.compile(r"^\s*\(`([^`\n]+)`")


def _symbol(span: str) -> str | None:
    if REF.fullmatch(f"`{span}`"):
        return None
    m = _IDENT.match(span)
    if not m:
        return None
    return m.group(0).rsplit(".", 1)[-1]


def refs(text: str):
    """(줄 번호, path, n1, n2, 기호|None) — 기호는 짝 지은 경우만."""
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in REF.finditer(line):
            p, n1 = m.group(1), int(m.group(2))
            n2 = int(m.group(3)) if m.group(3) else n1
            sym = None
            after = _AFTER.match(line[m.end():])
            before = _BEFORE.search(line[:m.start()])
            if after:
                sym = _symbol(after.group(1))
            if sym is None and before:
                sym = _symbol(before.group(1))
            yield lineno, p, n1, n2, sym


def _all_refs():
    out = []
    for doc in DOCS:
        f = ROOT / doc
        if f.exists():
            out += [(doc, *r) for r in refs(f.read_text(encoding="utf-8"))]
    return out


_REFS = _all_refs()


def test_docs_cite_code_at_all():
    # 정규식이 깨져 아무것도 못 찾으면 아래 검사가 전부 헛돈다.
    assert sum(1 for r in _REFS if r[0] == "docs/formulas.md") >= 150
    assert sum(1 for r in _REFS if r[-1]) >= 60


def _check(doc, lineno, path, n1, n2, sym):
    f = ROOT / path
    where = f"{doc}:{lineno} `{path}:{n1}{'' if n1 == n2 else f'-{n2}'}`"
    if not f.is_file():
        return f"{where}: 파일이 없다"
    lines = f.read_text(encoding="utf-8").splitlines()
    if not (1 <= n1 <= n2 <= len(lines)):
        return f"{where}: 줄 범위 밖(파일 {len(lines)}줄)"
    if sym:
        lo, hi = max(0, n1 - 1 - SLACK), min(len(lines), n2 + SLACK)
        if not any(re.search(rf"\b{re.escape(sym)}\b", ln) for ln in lines[lo:hi]):
            return f"{where}: `{sym}` 가 ±{SLACK}줄 안에 없다"
    return None


def test_every_cited_line_exists_and_paired_symbol_is_there():
    bad = [e for r in _REFS if (e := _check(*r))]
    assert not bad, "문서의 코드 인용이 낡았다 — 줄 번호를 지금 코드에 맞춘다:\n" + "\n".join(bad)


@pytest.mark.parametrize(
    "line, want",
    [
        ("`EOK = 1e8` `scripts/sector_flow.py:37`.", "EOK"),
        ("`src/a.py:117`(`load`), `scripts/b.sh:1`", "load"),
        ("`State._leads` `src/a.py:388-418`(부호", "_leads"),
        ("열 `src/a.py:1212`", None),
        ("`(미분류)` `src/a.py:3`", None),
        ("`src/a.py:1` `src/b.py:2`", None),
    ],
)
def test_pairing_convention(line, want):
    got = [r[-1] for r in refs(line)]
    assert got[0] == want
