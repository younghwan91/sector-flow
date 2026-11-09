"""문서가 적은 ``scripts/*.py --옵션`` 이 그 스크립트의 argparse 에 있는가.

README·런북의 명령을 그대로 쳤는데 argparse 가 거절하면 그 문서는 거짓말이다. 옵션 이름이 바뀌어도 아무 검사도 안 빨개졌다(``--dumpp``·``--scope chgd``
같은 드리프트가 초록). 스크립트를 띄우지 않고 AST 로 ``add_argument`` 를 읽는다 — 옛 계획·연구 메모(``docs/superpowers``·``docs/strategy``)는 당시 기록이라 안 본다.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
#: 한 명령의 끝 — 인라인 코드 닫힘·표 칸·셸 연결·주석.
_CMD = re.compile(r"scripts/([a-z_]+\.py)\b([^`\n|;&#]*)")
_OPT = re.compile(r"(?<![\w-])(--[a-z][\w-]*)(?:[ =]([A-Za-z][\w-]*))?")


def declared(script: Path) -> dict[str, tuple[str, ...] | None]:
    """옵션 → 허용 값(``choices`` 가 글자 목록이면). ``add_argument`` 를 못 찾으면 빈 표."""
    out: dict[str, tuple[str, ...] | None] = {"--help": None}
    for node in ast.walk(ast.parse(script.read_text("utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument":
            choices = next((k.value for k in node.keywords if k.arg == "choices"), None)
            allowed = None
            if isinstance(choices, (ast.Tuple, ast.List)) and all(isinstance(e, ast.Constant) for e in choices.elts):
                allowed = tuple(e.value for e in choices.elts)
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("--"):
                    out[a.value] = allowed
    return out


def drift(text: str, scripts: Path) -> list[str]:
    """이 글이 적은 명령 중 argparse 가 거절할 옵션·값."""
    bad = []
    for name, rest in _CMD.findall(text):
        path = scripts / name
        if not path.exists():
            continue                                   # 없는 스크립트 인용은 test_docs_refs 쪽 일이다
        table = declared(path)
        for opt, val in _OPT.findall(rest):
            if opt not in table:
                bad.append(f"{name} {opt}")
            elif table[opt] and val and val not in table[opt]:
                bad.append(f"{name} {opt} {val}")
    return bad


def _docs() -> list[Path]:
    return [REPO / "README.md", REPO / "CLAUDE.md", *sorted((REPO / "docs").glob("*.md")),
            *sorted((REPO / "docs" / "byo").glob("*.md"))]


def test_documented_script_options_exist():
    seen = 0
    for doc in _docs():
        text = doc.read_text("utf-8")
        seen += len(_CMD.findall(text))
        assert not drift(text, REPO / "scripts"), f"{doc.name}: {drift(text, REPO / 'scripts')}"
    assert seen >= 20, "명령을 거의 못 찾았다 — 정규식이 헛돈다"


def test_drift_reads_a_typo_a_bad_choice_and_a_good_command():
    """검사 자신이 눈멀지 않았는가 — 실제 스크립트의 옵션으로 본다."""
    scripts = REPO / "scripts"
    assert drift("`python scripts/digest.py --dumpp x`", scripts) == ["digest.py --dumpp"]
    assert drift("`python scripts/verify_merge.py . --scope chgd`", scripts) == ["verify_merge.py --scope chgd"]
    assert drift("`python scripts/verify_merge.py . --scope changed --only render,pty`", scripts) == []
    assert drift("    scripts/rebuild_history.py --root r --from a --to b\n", scripts) == []
    assert drift("`scripts/nope.py --x`", scripts) == []
