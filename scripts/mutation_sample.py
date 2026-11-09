#!/usr/bin/env python3
"""무작위 변이 표본 — **선언 안 한** 변이에 검사가 얼마나 무나.

``tests/mutations.toml`` 은 "적어둔 버그는 잡힌다" 만 말한다. 여기서는 소스의 AST 에서 변이(비교·논리·산술 연산자, 숫자 +1,
``if`` 부정, ``return None``)를 무작위로 뽑아 실제로 주입하고 pytest 가 빨개지는지 본다. 실행된 적 없는 줄은 뽑지 않는다
(커버리지가 이미 말한다). 결과와 판단은 ``docs/validation/2026-11-09-mutation-sample.md``.

    uv run --extra dev pytest -q --cov --cov-report=json:coverage.json     # 뽑을 줄을 고르는 데 쓴다
    uv run --extra dev python scripts/mutation_sample.py --seed 8 --per 25 --workers 6 --out /임시/s8.json \\
        [--deselect 노드ID] [--base main]
    uv run --extra dev python scripts/mutation_sample.py --replay /임시/s8.json --workers 6 --out /임시/s8b.json

주입은 **HEAD 의 임시 클론**(``$TMPDIR``)에서 한다 — 작업 트리·운영 체크아웃·다른 워크트리를 건드리지 않는다. 변이마다 먼저 그
묶음의 검사(:data:`GROUPS`)만, 살아남으면 전체 pytest 를 돈다. 판정: ``killed``(pytest 종료코드 1) · ``survived``(0) · ``timeout`` ·
``invalid``(수집 오류 등 — 변이가 문법·import 를 깨뜨렸다, 잡힌 것으로 세지 않는다. 메모리 상한에 닿은 것도 여기다: ``rc`` 가 ``"memory"``).

**거짓 잡힘을 세지 않는다.** 표본 숫자가 부풀면 도구가 없는 것만 못하다. 그래서 (1) 소스 글자를 보는 검사(:data:`SHALLOW` — 앵커·인용
대조)는 돌리지 않고, (2) 잡았다는 검사를 **변이를 뺀 트리에서 다시 돌려** 거기서도 빨가면(부하 탓 등) 그 검사를 빼고 다시 판정한다
(``flaky`` 에 적는다), (3) ``TMPDIR`` 를 클론 밖에 둔다 — 클론 안이면 "git 밖" 을 전제한 검사가 빨개진다. 무한 루프로 메모리를 먹는
변이가 머신을 흔들지 않게 검사 프로세스에 메모리 상한(``scripts/memcap.py``)을 건다.

표본은 시드와 코드에 달렸다(코드·커버리지가 바뀌면 같은 시드도 다른 변이를 뽑는다) — 같은 변이를 다시 돌리려면 ``--replay``.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # scripts/ 는 패키지가 아니다
import memcap  # noqa: E402 — 검사 프로세스의 메모리 상한

REPO = Path(__file__).resolve().parent.parent
_COLLECT = ("dart", "kiwoom", "fetch", "shares", "write", "busy", "auth", "run", "equiv")
#: 묶음 → (대상 파일, 먼저 돌릴 검사, 바뀐 줄만 뽑을까). 리뷰 표본(2026-11-09)과 같은 네 묶음이다.
GROUPS: dict[str, tuple[list[str], list[str], bool]] = {
    "digest": (["scripts/digest.py"], ["tests/test_digest.py", "tests/test_artifact_contracts.py"], False),
    "trust": (["scripts/trust_page.py"], ["tests/test_trust_page.py", "tests/test_artifact_contracts.py",
                                          "tests/test_serve_reports.py"], False),
    "collect": ([f"src/sectorflow/collect/{n}.py" for n in _COLLECT],
                ["tests/test_collect_backfill.py", "tests/test_collect_busy.py", "tests/test_collect_dart.py",
                 "tests/test_collect_equiv.py", "tests/test_collect_fetch.py", "tests/test_collect_kiwoom.py",
                 "tests/test_collect_shares.py", "tests/test_collect_transport.py"], False),
    "stock_diff": (["scripts/sector_flow.py", "scripts/verify_report.py", "scripts/sector_numbers.py",
                    "scripts/seed_revisions.py"],
                   ["tests/test_streaks_revisions.py", "tests/test_web_render.py", "tests/test_payload_today.py",
                    "tests/test_verifier_depth.py"], True),
}
CMP = {ast.Gt: ">=", ast.GtE: ">", ast.Lt: "<=", ast.LtE: "<", ast.Eq: "!=", ast.NotEq: "==",
       ast.In: "not in", ast.NotIn: "in", ast.Is: "is not", ast.IsNot: "is"}
ARITH = {ast.Add: "-", ast.Sub: "+", ast.Mult: "/", ast.Div: "*"}
#: 소스 글자를 대조하는 검사 — 줄이 바뀌면 동작과 무관하게 빨개진다. 이걸로 잡힌 것은 잡힌 것이 아니다.
SHALLOW = ("tests/test_mutation_spec.py", "tests/test_docs_refs.py")


def mutants(source: str, path: str, executed: set[int] | None = None, only: set[int] | None = None) -> list[dict]:
    """``source`` 에서 뽑을 수 있는 변이 전부. 열은 바이트 기준(``ast`` 와 같다). 줄을 넘는 식은 뺀다."""
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    out: list[dict] = []

    def add(lno: int, c0: int, c1: int, new: str, kind: str) -> None:
        if (executed is not None and lno not in executed) or (only is not None and lno not in only):
            return
        old = lines[lno - 1].encode()[c0:c1].decode()
        if old != new:
            out.append({"file": path, "line": lno, "c0": c0, "c1": c1, "old": old, "new": new, "kind": kind,
                        "text": lines[lno - 1].strip()[:150]})

    def between(a: ast.expr, b: ast.expr) -> str:
        return lines[a.end_lineno - 1].encode()[a.end_col_offset:b.col_offset].decode().strip()

    for n in ast.walk(tree):
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and type(n.ops[0]) in CMP:
            a, b = n.left, n.comparators[0]
            if a.end_lineno == b.lineno:
                add(a.end_lineno, a.end_col_offset, b.col_offset, f" {CMP[type(n.ops[0])]} ", "cmp")
        elif isinstance(n, ast.BoolOp) and len(n.values) == 2:
            a, b = n.values
            if a.end_lineno == b.lineno and between(a, b) in ("and", "or"):          # 괄호가 끼면 뺀다
                add(a.end_lineno, a.end_col_offset, b.col_offset, " or " if between(a, b) == "and" else " and ", "bool")
        elif isinstance(n, ast.BinOp) and type(n.op) in ARITH:
            a, b = n.left, n.right
            if (a.end_lineno == b.lineno and between(a, b) in ("+", "-", "*", "/")
                    and not (isinstance(a, ast.Constant) and isinstance(a.value, str))
                    and not isinstance(a, ast.JoinedStr) and not isinstance(b, ast.JoinedStr)):
                add(a.end_lineno, a.end_col_offset, b.col_offset, f" {ARITH[type(n.op)]} ", "arith")
        elif isinstance(n, ast.Constant) and type(n.value) in (int, float) and n.lineno == n.end_lineno:
            add(n.lineno, n.col_offset, n.end_col_offset, repr(n.value + 1), "const")
        elif isinstance(n, ast.If) and n.test.lineno == n.test.end_lineno:
            t = n.test
            add(t.lineno, t.col_offset, t.end_col_offset,
                "not (" + lines[t.lineno - 1].encode()[t.col_offset:t.end_col_offset].decode() + ")", "negif")
        elif (isinstance(n, ast.Return) and n.value is not None and n.lineno == n.end_lineno
              and not (isinstance(n.value, ast.Constant) and n.value.value is None)):
            add(n.value.lineno, n.value.col_offset, n.value.end_col_offset, "None", "retnone")
    return out


def apply_mutant(root: Path, m: dict) -> bytes:
    """주입하고 원본 바이트를 돌려준다. 그 자리가 ``old`` 가 아니면 코드가 바뀐 것이다 — ``ValueError``."""
    p = root / m["file"]
    orig = p.read_bytes()
    lines = orig.split(b"\n")
    ln = lines[m["line"] - 1]
    if ln[m["c0"]:m["c1"]].decode() != m["old"]:
        raise ValueError(f"{m['file']}:{m['line']} 자리가 표본과 다르다 — 코드가 바뀌었다")
    lines[m["line"] - 1] = ln[:m["c0"]] + m["new"].encode() + ln[m["c1"]:]
    p.write_bytes(b"\n".join(lines))
    return orig


def verdict(rc: "int | str") -> str:
    return {0: "survived", 1: "killed", "timeout": "timeout"}.get(rc, "invalid")


def first_failure(stdout: str) -> str:
    """요약의 첫 ``FAILED``·``ERROR`` 줄의 nodeid. 없으면 빈 문자열."""
    for ln in stdout.splitlines():
        if ln.startswith(("FAILED ", "ERROR ")):
            return ln.split(" ", 1)[1].split(" - ", 1)[0].strip()
    return ""


def pytest_once(root: Path, tmp: Path, tests: list[str], deselect: list[str], timeout: int) -> tuple["int | str", str]:
    tmp.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": f"{root}/src{os.pathsep}{root}", "PYTHONDONTWRITEBYTECODE": "1",
           "TMPDIR": str(tmp)}
    env.pop("KR_QUANT_DB", None)
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--no-header", "--tb=short", *tests]
    for f in SHALLOW:
        if (root / f).exists():
            cmd += ["--ignore", f]
    for d in deselect:
        cmd += ["--deselect", d]
    try:
        p = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, timeout=timeout,
                           preexec_fn=memcap.cap_memory)
    except subprocess.TimeoutExpired:
        return "timeout", ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if memcap.hit_cap(p.returncode, p.stdout + p.stderr):
        return "memory", first_failure(p.stdout)      # 상한에 닿은 빨강은 검사가 잡은 것이 아니다
    return p.returncode, first_failure(p.stdout)


def _pytest_with_confirm(root: Path, tmp: Path, m: dict, orig: bytes, mutated: bytes, tests: list[str],
                         deselect: list[str], timeout: int, flaky: list[str]) -> tuple["int | str", str]:
    """주입한 채 돌리고, 잡았으면 원본에서 그 검사를 다시 본다. 원본에서도 빨간 검사는 빼고 다시 돈다(세 번까지)."""
    p = root / m["file"]
    skip = list(deselect)
    for _ in range(3):
        rc, first = pytest_once(root, tmp, tests, skip, timeout)
        if rc != 1 or not first:
            return rc, first
        p.write_bytes(orig)
        clean, _f = pytest_once(root, tmp, [first], [], timeout)
        p.write_bytes(mutated)
        if clean == 0:
            return 1, first
        flaky.append(first)
        skip.append(first)
    return "invalid", first


def judge(root: Path, m: dict, deselect: list[str], timeout: int, tmp: "Path | None" = None) -> dict:
    """묶음 검사 → 살아남으면 전체 pytest. 소스는 무슨 일이 있어도 되돌린다."""
    t0 = time.monotonic()
    tmp = tmp or Path(tempfile.gettempdir()) / f"sf-mutsample-{root.name}-tmp"
    orig = apply_mutant(root, m)
    mutated = (root / m["file"]).read_bytes()
    flaky: list[str] = []
    try:
        rc, first = _pytest_with_confirm(root, tmp, m, orig, mutated, m["tests"], deselect, timeout, flaky)
        stage = "group"
        if rc == 0:
            rc, first = _pytest_with_confirm(root, tmp, m, orig, mutated, [], deselect + flaky, timeout, flaky)
            stage = "full"
    finally:
        (root / m["file"]).write_bytes(orig)
    return {**m, "rc": rc, "verdict": verdict(rc), "stage": stage, "first": first[:150], "flaky": flaky,
            "dt": round(time.monotonic() - t0, 1)}


def changed_lines(root: Path, base: str, path: str) -> set[int]:
    d = subprocess.run(["git", "diff", "-U0", base, "HEAD", "--", path], cwd=root, capture_output=True, text=True).stdout
    out: set[int] = set()
    for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", d, re.M):
        s, n = int(m.group(1)), int(m.group(2) or 1)
        out |= set(range(s, s + n))
    return out


def draw(root: Path, cov: dict, seed: int, per: int, base: str,
         groups: "dict[str, tuple[list[str], list[str], bool]]" = GROUPS) -> tuple[list[dict], dict[str, int]]:
    """묶음마다 ``per`` 개. 연산자 종류를 고르게 맞추지 않는다 — 코드에 있는 비율 그대로."""
    rnd = random.Random(seed)
    sample, sizes = [], {}
    for g, (files, tests, diff_only) in groups.items():
        pool: list[dict] = []
        for f in files:
            executed = set(cov["files"].get(f, {}).get("executed_lines", []))
            only = changed_lines(root, base, f) if diff_only else None
            pool += mutants((root / f).read_text("utf-8"), f, executed, only)
        sizes[g] = len(pool)
        rnd.shuffle(pool)
        sample += [{**m, "group": g, "tests": tests} for m in pool[:per]]
    return sample, sizes


def clone_at_head(repo: Path, dst: Path) -> Path:
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(repo), str(dst)], check=True)
    return dst


def run_all(sample: list[dict], repo: Path, workers: int, deselect: list[str], timeout: int,
            work: Path) -> list[dict]:
    q: "queue.Queue[Path]" = queue.Queue()
    for i in range(workers):
        q.put(clone_at_head(repo, work / f"w{i}"))

    def job(m: dict) -> dict:
        root = q.get()
        try:
            r = judge(root, m, deselect, timeout, work / f"{root.name}.tmp")
        finally:
            q.put(root)
        print(r["group"], r["verdict"], r["stage"], r["file"], r["line"], repr(r["old"]), "->", repr(r["new"]),
              r["dt"], flush=True)
        return r

    with cf.ThreadPoolExecutor(workers) as ex:
        return list(ex.map(job, sample))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=8)
    ap.add_argument("--per", type=int, default=25, help="묶음마다 뽑을 변이 수")
    ap.add_argument("--cov", default="coverage.json", help="`pytest --cov --cov-report=json:` 출력(뽑을 줄을 고른다)")
    ap.add_argument("--base", default="main", help="바뀐 줄만 뽑는 묶음의 기준")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=900, help="한 pytest 의 상한(초)")
    ap.add_argument("--deselect", action="append", default=[], help="이미 빨간 검사(잡힌 것으로 세지 않게)")
    ap.add_argument("--replay", help="이전 --out 파일의 변이를 그대로 다시 돈다")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.replay:
        sample = [{k: m[k] for k in ("file", "line", "c0", "c1", "old", "new", "kind", "text", "group", "tests")}
                  for m in json.loads(Path(a.replay).read_text("utf-8"))]
    else:
        cov = json.loads(Path(a.cov).read_text("utf-8"))
        sample, sizes = draw(REPO, cov, a.seed, a.per, a.base)
        print(f"뽑을 수 있는 변이 {sizes} · 표본 {len(sample)}", flush=True)
    work = Path(tempfile.mkdtemp(prefix="sf-mutsample-"))
    try:
        res = run_all(sample, REPO, a.workers, a.deselect, a.timeout, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=0), "utf-8")
    print(dict(Counter(r["verdict"] for r in res)))
    flakes = Counter(f for r in res for f in r["flaky"])
    if flakes:
        print(f"원본에서도 빨갛던 검사(잡힘으로 안 셌다): {dict(flakes)}")
    for r in res:
        if r["rc"] == "memory":
            print(f"메모리 상한(잡힘 아님) {r['file']}:{r['line']} {r['old']!r} -> {r['new']!r} | {r['text'][:100]}")
        if r["verdict"] == "survived":
            print(f"생존 {r['file']}:{r['line']} {r['old']!r} -> {r['new']!r} | {r['text'][:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
