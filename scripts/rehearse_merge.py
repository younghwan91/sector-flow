#!/usr/bin/env python3
"""머지 전 조합 리허설 — 대기 브랜치를 임시 클론에 차례로 합쳐 전체 관문을 돈다.

브랜치 하나하나가 초록이어도 합치면 깨진다(인용 줄 번호·변이 앵커·테스트 수·같은 줄 수정). PR 마다의 CI 는 그 조합을 안
본다. 절차와 이 도구의 자리는 ``docs/merge-rehearsal.md`` — **초록 CI 규칙을 대신하지 않는다.**

    uv run python scripts/rehearse_merge.py 브랜치1 브랜치2 … [--base origin/main]
    uv run python scripts/rehearse_merge.py 브랜치1 --skip verify-merge         # 변이 주입(수십 분)만 빼고
    uv run python scripts/rehearse_merge.py 브랜치1 --gate 'lint=uv run ruff check .'

**워크트리가 아니라 클론이다.** ``git worktree add`` 는 Orca 기록과 어긋나고 이 레포의 ``.git`` 을 건드린다. 클론은 지우면
끝이고, 변이 주입·``.venv``·병합 커밋이 원래 레포·운영 체크아웃·다른 워크트리에 닿지 않는다. 브랜치는 이름으로 받되
시작할 때 커밋으로 고정한다.

병합은 준 순서대로다. 충돌이 나면 충돌 파일을 적고 멈춘다(관문을 안 돈다 — 합쳐지지 않은 것을 잰 숫자는 뜻이 없다). 관문은
하나가 실패해도 끝까지 돈다. 병합 뒤 ``docs_refs.py --fix`` 가 고칠 것이 있으면 커밋하고 적는다 — 실제 머지에서도 필요한 일이다.

종료코드: 0 전부 통과 · 1 충돌이나 관문 실패 · 2 잘못 불렀다(없는 브랜치 등). 통과하면 임시 폴더를 지우고, 실패하면 살펴보라고 남긴다(``--keep`` 은 늘 남긴다).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

#: 기본 관문 — (이름, 명령). ``{base}`` 는 기준 커밋. CI 의 Lint·Research compiles·Test·Diff coverage·Mutation gate 와 같고,
#: TZ=UTC 를 더한다(CI 러너는 UTC, 이 머신은 KST). 합친 결과는 샤드 전체를 한 번에 돈다.
GATES = (
    ("lint", "uv run --extra dev ruff check src/ tests/ scripts/ research/"),
    ("research-compiles", "uv run --extra dev python -m compileall -q research"),
    ("pytest", "uv run --extra dev pytest -q --cov --cov-report=json:coverage.json"),
    ("pytest-utc", "TZ=UTC uv run --extra dev pytest -q"),
    ("diff-coverage", "uv run --extra dev python scripts/diff_coverage.py --base {base} --json coverage.json --min 80"),
    ("verify-merge", "uv run --extra dev python scripts/verify_merge.py . --base {base} --only mutations --scope all"),
)
#: 관문에 넘기지 않는 환경 — 실 DB·운영 리포트 폴더를 보지 않게, 바깥의 uv 환경을 쓰지 않게.
SCRUB_ENV = ("KR_QUANT_DB", "KR_QUANT_REPORTS", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "PYTHONPATH")


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=cwd, capture_output=True, text=True,
                          check=check)


def resolve(repo: Path, rev: str) -> str | None:
    p = git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False)
    return p.stdout.strip() if p.returncode == 0 else None


def make_clone(repo: Path, wt: Path, base_sha: str) -> None:
    """임시 클론 + 기준 커밋 체크아웃. 신원은 원래 레포 설정을 그대로 옮긴다(병합 커밋은 클론 안에만 남는다)."""
    git(repo.parent, "clone", "-q", "--no-hardlinks", "--no-checkout", str(repo), str(wt))
    for key in ("user.name", "user.email"):
        v = git(repo, "config", key, check=False).stdout.strip()
        if v:
            git(wt, "config", key, v)
    git(wt, "checkout", "-q", "-b", "rehearsal", base_sha)


def merge_all(wt: Path, branches: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """(합친 줄들, 충돌 파일). 충돌이 나면 그 브랜치에서 멈추고 마지막 원소에 이름을 적는다."""
    done: list[str] = []
    for name, sha in branches:
        p = git(wt, "merge", "--no-ff", "--no-edit", "-m", f"rehearsal: {name}", sha, check=False)
        if p.returncode != 0:
            files = git(wt, "diff", "--name-only", "--diff-filter=U").stdout.split()
            return done, [f"{name} 에서 멈췄다", *(files or [p.stderr.strip()[:300] or p.stdout.strip()[:300]])]
        done.append(f"{name} {sha[:10]}")
    return done, []


def fix_doc_refs(wt: Path) -> str | None:
    """병합으로 밀린 ``파일:줄`` 인용을 고친다. 고친 것이 있으면 커밋하고 한 줄을 돌려준다."""
    tool = wt / "scripts" / "docs_refs.py"
    if not tool.is_file():
        return None
    p = subprocess.run([sys.executable, str(tool), "--fix"], cwd=wt, capture_output=True, text=True, env=gate_env())
    if p.returncode != 0:
        return f"docs_refs --fix 실패(rc {p.returncode}) — {(p.stdout + p.stderr).strip()[-200:]}"
    if not git(wt, "status", "--porcelain").stdout.strip():
        return None
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "rehearsal: docs_refs --fix")
    return "병합으로 밀린 인용을 docs_refs --fix 로 고쳤다 — 실제 머지 뒤에도 같은 수정이 필요하다"


def gate_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in SCRUB_ENV}


def run_gates(wt: Path, logs: Path, gates: list[tuple[str, str]], base_sha: str) -> list[dict]:
    """관문을 차례로, 끝까지. 출력은 ``logs/<이름>.log``."""
    logs.mkdir(parents=True, exist_ok=True)
    out = []
    for name, cmd in gates:
        t0 = time.monotonic()
        log = logs / f"{name}.log"
        with open(log, "w", encoding="utf-8") as f:
            p = subprocess.run(cmd.replace("{base}", base_sha), shell=True, cwd=wt, env=gate_env(),
                               stdout=f, stderr=subprocess.STDOUT)
        row = {"name": name, "rc": p.returncode, "s": time.monotonic() - t0, "log": str(log)}
        out.append(row)
        print(f"  {'통과' if row['rc'] == 0 else '실패':4} {name} ({row['s']:.0f}초)"
              + ("" if row["rc"] == 0 else f" rc {row['rc']} — {log}"), flush=True)
    return out


def parse_gate(text: str) -> tuple[str, str]:
    name, sep, cmd = text.partition("=")
    if not sep or not name.strip() or not cmd.strip():
        raise argparse.ArgumentTypeError(f"--gate 는 이름=명령 꼴이다: {text!r}")
    return name.strip(), cmd.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("branches", nargs="+", help="합칠 브랜치(또는 커밋), 머지할 순서대로")
    ap.add_argument("--base", default="origin/main", help="기준 브랜치/커밋")
    ap.add_argument("--repo", default=".", help="원래 레포(기본: 지금 폴더)")
    ap.add_argument("--gate", action="append", type=parse_gate, default=[], metavar="이름=명령",
                    help="기본 관문 대신 돌릴 명령(여러 번). {base} 는 기준 커밋으로 바뀐다")
    ap.add_argument("--skip", action="append", default=[], metavar="이름", help="뺄 기본 관문(여러 번)")
    ap.add_argument("--keep", action="store_true", help="통과해도 임시 폴더를 남긴다")
    a = ap.parse_args(argv)

    repo = Path(git(Path(a.repo), "rev-parse", "--show-toplevel").stdout.strip())
    base_sha = resolve(repo, a.base)
    if base_sha is None:
        print(f"기준을 못 찾았다: {a.base}", file=sys.stderr)
        return 2
    pinned = [(b, resolve(repo, b)) for b in a.branches]
    missing = [b for b, s in pinned if s is None]
    if missing:
        print(f"없는 브랜치: {', '.join(missing)}", file=sys.stderr)
        return 2
    names = {n for n, _c in GATES}
    unknown = set(a.skip) - names
    if unknown:
        print(f"모르는 관문: {', '.join(sorted(unknown))} (있는 관문: {', '.join(n for n, _c in GATES)})", file=sys.stderr)
        return 2
    gates = a.gate or [(n, c) for n, c in GATES if n not in a.skip]

    tmp = Path(tempfile.mkdtemp(prefix="sf-rehearse-"))
    wt = tmp / "wt"
    print(f"리허설 {tmp} — 기준 {a.base} {base_sha[:10]}", flush=True)
    make_clone(repo, wt, base_sha)
    done, conflict = merge_all(wt, pinned)  # type: ignore[arg-type]
    for line in done:
        print(f"  합침 {line}", flush=True)
    if conflict:
        print(f"충돌 — {conflict[0]}:", *conflict[1:], sep="\n  ")
        print(f"실패 — 합쳐지지 않았다. 살펴볼 폴더: {wt}")
        return 1
    fixed = fix_doc_refs(wt)
    if fixed:
        print(f"  {fixed}", flush=True)
    rows = run_gates(wt, tmp / "logs", gates, base_sha)
    bad = [r["name"] for r in rows if r["rc"] != 0]
    if bad:
        print(f"실패 — 관문 {', '.join(bad)}. 살펴볼 폴더: {tmp}")
        return 1
    print(f"통과 — 브랜치 {len(pinned)}개·관문 {len(rows)}개. 초록 CI 규칙을 대신하지 않는다(docs/merge-rehearsal.md).")
    if a.keep:
        print(f"남겼다: {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
