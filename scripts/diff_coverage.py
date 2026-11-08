#!/usr/bin/env python3
"""변경 줄 커버리지 — merge-base 이후 **바뀐 줄**이 테스트로 실행됐는가(CI 관문).

전체 커버리지는 오래된 코드의 빚이 섞여 PR 마다 판단할 수 없다. 여기서는 이 브랜치가 더하거나
고친 ``src/``·``scripts/`` 의 파이썬 줄만 센다. 실행 가능한 줄(coverage 가 센 줄) 중 실행된 몫이
``--min`` 미만이면 실패한다. 리포트 단계·검증기는 테스트가 서브프로세스로 돌리므로 커버리지는
``pyproject.toml`` 의 ``patch = ["subprocess"]`` 로 거기까지 잰다.

사용::

    uv run pytest -q --cov --cov-report=json:coverage.json
    uv run python scripts/diff_coverage.py --base origin/main --json coverage.json --min 80

coverage 가 모르는 바뀐 파일(어느 테스트도 import 하지 않은 새 파일)은 **실행 가능한 줄을 직접 세어**
전부 안 덮인 것으로 친다 — 측정 안 된 파일을 빼고 세면 아무도 안 돌린 새 스크립트가 통과한다.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOTS = ("src/", "scripts/")


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout


def changed_lines(base: str, cwd: Path | None = None) -> dict[str, set[int]]:
    """merge-base(base, HEAD) 이후 바뀐(더해진) 줄 번호 — 새 파일 기준. 작업 트리 변경도 넣는다."""
    mb = git("merge-base", base, "HEAD", cwd=cwd).strip()
    diff = git("diff", "-U0", "--no-color", "--no-ext-diff", mb, "--", *ROOTS, cwd=cwd)
    out: dict[str, set[int]] = {}
    path = None
    for ln in diff.splitlines():
        if ln.startswith("+++ "):
            path = ln[6:] if ln.startswith("+++ b/") else None
            continue
        m = re.match(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", ln)
        if m and path and path.endswith(".py"):
            start, n = int(m.group(1)), int(m.group(2) or 1)
            out.setdefault(path, set()).update(range(start, start + n))
    return {p: s for p, s in out.items() if s}


def statement_lines(source: str) -> set[int]:
    """coverage 가 없을 때 쓰는 근사 — 문장이 시작하는 줄(독스트링·함수 머리 포함)."""
    lines: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.stmt):
            lines.add(node.lineno)
    return lines


def measure(changed: dict[str, set[int]], cov: dict, root: Path) -> list[dict]:
    """파일마다 (바뀐 실행 줄, 그중 실행된 줄, 안 덮인 줄)."""
    files = cov.get("files", {})
    rows = []
    for path, lines in sorted(changed.items()):
        f = files.get(path)
        if f is not None:
            executable = set(f["executed_lines"]) | set(f["missing_lines"])
            executed = set(f["executed_lines"])
        else:
            p = root / path
            if not p.exists():
                continue                      # 지운 파일
            try:
                executable = statement_lines(p.read_text("utf-8"))
            except SyntaxError:
                executable = set(lines)
            executed = set()
        mine = lines & executable
        if mine:
            rows.append({"path": path, "lines": mine, "hit": mine & executed,
                         "miss": sorted(mine - executed), "measured": f is not None})
    return rows


def ranges(nums: list[int]) -> str:
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(out)


def report(rows: list[dict], minimum: float) -> tuple[bool, str]:
    total = sum(len(r["lines"]) for r in rows)
    hit = sum(len(r["hit"]) for r in rows)
    if not total:
        return True, "바뀐 실행 줄이 없다 (src/·scripts/ 의 .py) — 통과"
    pct = 100.0 * hit / total
    out = [f"변경 줄 커버리지 {hit}/{total} = {pct:.1f}% (기준 {minimum:g}%)"]
    for r in rows:
        n = len(r["lines"])
        tag = "" if r["measured"] else "  (측정 안 됨 — 어느 테스트도 실행하지 않았다)"
        out.append(f"  {100.0 * len(r['hit']) / n:5.1f}%  {len(r['hit'])}/{n}  {r['path']}{tag}")
        if r["miss"]:
            out.append(f"         안 덮인 줄: {ranges(r['miss'])}")
    ok = pct >= minimum
    out.append("통과" if ok else "실패 — 바뀐 줄을 실행하는 검사를 더하라(테스트가 돌리지 않는 줄은 "
               "이 저장소에서 아무도 확인하지 않은 줄이다)")
    return ok, "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="origin/main", help="기준 브랜치/커밋 (merge-base 를 쓴다)")
    ap.add_argument("--json", default="coverage.json", help="`coverage json` 출력")
    ap.add_argument("--min", type=float, default=80.0, help="최소 변경 줄 커버리지(%%)")
    a = ap.parse_args(argv)
    root = Path(git("rev-parse", "--show-toplevel").strip())
    cov = json.loads(Path(a.json).read_text("utf-8"))
    ok, text = report(measure(changed_lines(a.base, root), cov, root), a.min)
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
