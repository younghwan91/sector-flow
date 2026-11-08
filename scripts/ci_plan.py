#!/usr/bin/env python3
"""CI 가 이번 실행에서 무엇을 돌릴지 정한다 — 표준 라이브러리만(``uv sync`` 전에 러너의 python3 로 돈다).

왜 ``paths`` 필터가 아닌가: 배포 관문 ``pull-all --ci-gate`` 는 **머리 커밋**의 체크만 본다(success·skipped·neutral 이면
통과, 체크가 0개면 보류). 워크플로째 건너뛰면 그 커밋엔 신원 검사만 남아, 빨간 코드 커밋 위에 문서 커밋 하나를 얹는 것으로
관문을 지나간다. 그래서 워크플로는 늘 돌고, 이 잡이 잡 단위 건너뛰기를 정한다.

무엇과 견주나:
- ``pull_request`` — merge-base 이후. 문서만이면 ``docs``(변이 없음, 검사는 한 판). 판정은 PR 피드백일 뿐 배포와 무관하다.
- ``push``(main) — 직전 커밋이 아니라 **마지막으로 초록이었던 main 커밋**(``--verified``) 이후. 그 사이가 문서뿐일 때만
  ``docs`` 다. 귀납: 초록 실행의 코드 트리는 전체 검증을 받았거나, 받은 트리와 같다. 그래서 머리가 초록이면 머리의
  코드는 전체 변이를 지났다. 커밋이 빨갛거나 끊겼으면 다음 문서 커밋도 전체를 돈다.

모르면 전체다 — 기준을 못 찾거나, 바뀐 파일이 비었으면(같은 커밋의 재실행) ``code``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

#: 검사 파이썬 판 — push 는 ci.yml 의 전부, PR 은 양 끝, 문서는 하나(문서를 읽는 검사가 있어 pytest 는 돈다).
PY_ALL = ["3.11", "3.12", "3.13"]
PY_PR = ["3.11", "3.13"]
PY_DOCS = ["3.13"]


def is_doc(path: str) -> bool:
    """코드가 실행하지 않는 파일. ``tests/`` 안의 .md 는 픽스처일 수 있어 뿌리·``docs/`` 만 본다."""
    return path.startswith("docs/") or path == "LICENSE" or ("/" not in path and path.endswith(".md"))


def _git(*args: str) -> str | None:
    p = subprocess.run(["git", *args], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def _changed(base: str) -> set[str] | None:
    # --no-renames: 코드를 docs/ 로 옮긴 커밋이 새 이름만 보여 문서로 판정되지 않게.
    out = _git("diff", "--no-renames", "--name-only", base, "HEAD")
    return None if out is None else {f for f in out.splitlines() if f}


def changed_since(event: str, base_ref: str, verified: str) -> set[str] | None:
    """견줄 기준 이후 바뀐 파일. 기준을 믿을 수 없으면 None."""
    if event == "pull_request":
        out = _git("merge-base", base_ref, "HEAD")
        return _changed(out.strip()) if out else None
    if event == "push" and verified:
        return _changed(verified)                 # 트리를 견주므로 조상 여부는 안 본다 — 없는 커밋이면 실패해 None 이다
    return None


def plan(event: str, changed: set[str] | None) -> dict[str, str]:
    docs = bool(changed) and all(is_doc(f) for f in changed)
    py = PY_DOCS if docs else (PY_PR if event == "pull_request" else PY_ALL)
    return {"kind": "docs" if docs else "code", "python": json.dumps(py)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--event", required=True, help="github.event_name")
    ap.add_argument("--base", default="origin/main", help="PR 기준 브랜치")
    ap.add_argument("--verified", default="", help="마지막으로 초록이었던 main 커밋(없으면 전체)")
    a = ap.parse_args(argv)
    changed = changed_since(a.event, a.base, a.verified)
    out = plan(a.event, changed)
    print(f"ci_plan: {out} — 바뀐 파일 {'모름' if changed is None else len(changed)}개", file=sys.stderr)
    for k, v in out.items():
        print(f"{k}={v}")                         # $GITHUB_OUTPUT 형식
    return 0


if __name__ == "__main__":
    sys.exit(main())
