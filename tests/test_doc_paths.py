"""문서가 가리키는 레포 경로가 **실제로 있는가** — README·CLAUDE.md·docs/*.md.

파일을 옮기거나 지운 뒤 문서의 `scripts/…` 나 상대 링크가 남으면, 장애 대응 중에 없는 명령을 치게 된다
(런북 이 지금 없는 스크립트를 가리킨 적이 있다). 인용 줄 번호(`파일:줄`)는 tests/test_docs_refs.py 가 본다 —
여기서는 경로만 본다.

* 백틱 안의 `scripts/`·`src/`·`docs/`·`tests/`·`deploy/`·`research/` 로 시작하는 경로(앞이 `/`·`~` 면 다른 레포라 뺀다)
* 상대 마크다운 링크 `[…](경로)`
* 기록 문서(사전등록·결과·옛 설계)는 그때의 경로를 적은 것이라 뺀다(:data:`HISTORICAL`). 다른 레포나 지운 파일을
  일부러 말하는 자리는 (문서, 경로) 로 :data:`ALLOWED` 에 이유와 함께 둔다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: 그때의 경로를 남긴 기록 문서 — 지금 레포와 대조하지 않는다.
HISTORICAL = {
    "docs/validation": "사전등록·결과 기록 — 그 시점의 경로와 커밋을 적는다",
    "docs/strategy": "전략 연구 기록(있으면) — 위와 같다",
    "docs/superpowers": "swing-it 시절 설계 문서 — swing-it 경로를 적는다",
}

#: (문서, 경로) → 이유. 문서에서 그 언급이 사라지면 여기서도 지운다(아래 검사가 본다).
ALLOWED = {
    ("CLAUDE.md", "docs/schema.md"): "quant-airflow 의 문서다",
    ("docs/formulas.md", "docs/schema.md"): "quant-airflow 의 문서다",
    ("docs/formulas.md", "docs/kqflow-formulas.pdf"): "2026-11-08 지운 옛 판 — 지웠다고 적은 자리",
    ("docs/formulas.md", "src/kr_quant/tui/"): "swing-it 옛 경로 대조표",
    ("docs/formulas.md", "docs/GUARDRAILS.md"): "swing-it 의 파일이라고 적은 자리",
}

TICK = re.compile(r"`([^`\n]+)`")
PATH = re.compile(r"(?<![\w./~-])((?:scripts|src|docs|tests|deploy|research)/[\w./*-]*)")
LINK = re.compile(r"\]\(([A-Za-z0-9_./-]+)(?:#[^)]*)?\)")


def _docs() -> list[Path]:
    out = [REPO / "README.md", REPO / "CLAUDE.md"]
    for p in sorted((REPO / "docs").rglob("*.md")):
        rel = p.relative_to(REPO).as_posix()
        if not any(rel.startswith(h + "/") for h in HISTORICAL):
            out.append(p)
    return out


def _mentions(doc: Path) -> list[tuple[str, Path]]:
    """(문서에 적힌 그대로, 레포에서 찾을 경로)."""
    text = doc.read_text("utf-8")
    out = []
    for m in TICK.finditer(text):
        for p in PATH.findall(m.group(1)):
            p = p.rstrip(".")
            out.append((p, REPO / p))
    for m in LINK.finditer(text):
        target = m.group(1)
        if "/" in target or "." in target:
            out.append((target, (doc.parent / target).resolve()))
    return out


def _exists(p: Path) -> bool:
    return p.exists() or ("*" in p.name and any(p.parent.glob(p.name)))


@pytest.mark.parametrize("doc", _docs(), ids=lambda p: p.relative_to(REPO).as_posix())
def test_every_repo_path_in_the_docs_exists(doc):
    rel = doc.relative_to(REPO).as_posix()
    missing = sorted({said for said, p in _mentions(doc) if not _exists(p) and (rel, said) not in ALLOWED})
    assert not missing, (f"{rel} 가 없는 경로를 가리킨다: {missing} — 옮겼으면 문서를 고치고, 다른 레포·지운 파일을 "
                         f"일부러 말하는 자리면 ALLOWED 에 이유와 함께 넣어라")


# mutation-exempt: 검사기(정규식) 자체가 눈을 떴는지 보는 검사다 — 주입할 소스가 아니라 이 파일의 정규식이 대상이다
def test_scanner_sees_paths_and_links():
    """정규식이 꼴 변화로 아무것도 못 보면 위 검사가 빈손으로 초록이다."""
    said = {s for s, _ in _mentions(REPO / "README.md")}
    assert {"scripts/daily_report.sh", "deploy/crontab.simnode"} <= said, sorted(said)[:20]
    assert any(s.endswith(".md") or s.endswith("/") for s, p in _mentions(REPO / "README.md") if "](" not in s)


def test_allowed_entries_are_still_mentioned():
    """문서에서 사라진 허용 항목이 남으면, 같은 이름으로 생긴 진짜 오타를 덮는다."""
    by_doc: dict[str, set[str]] = {}
    for rel, said in ALLOWED:
        by_doc.setdefault(rel, {s for s, _ in _mentions(REPO / rel)})
        assert said in by_doc[rel], f"{rel} 에 더는 `{said}` 가 없다 — ALLOWED 에서 지워라"


def test_allowed_entries_really_point_nowhere():
    """허용 항목은 '없는 경로를 일부러 말하는 자리' 다. 그 경로가 생기면 항목이 진짜 오타를 덮을 일만 남는다."""
    alive = sorted((rel, said) for rel, said in ALLOWED if _exists(REPO / said))
    assert not alive, f"이제 있는 경로다 — ALLOWED 에서 지워라: {alive}"
