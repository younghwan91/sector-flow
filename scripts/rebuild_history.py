#!/usr/bin/env python
"""지난 기준일들을 지금 코드·확정 데이터로 다시 만든다 — ``daily_report.sh`` 를 ``KR_QUANT_ASOF`` 로 날마다 부른다.

    python scripts/rebuild_history.py --root /tmp/history --from 2026-08-27 --to 2026-11-06 \\
        --origin-root reports --origin-label reports/.archive/pre-rebuild-2026-11-08

* **오래된 날부터 차례로** 만든다 — 직전 대비(``change``)가 같은 루트의 직전 거래일 폴더를 읽는다.
  거꾸로 만들면 그 칸이 빈다. ``--jobs N`` 은 구간을 N 덩어리로 나눠 덩어리마다 차례로 짓고, 모은 뒤
  덩어리 첫날만(그때는 직전 폴더가 있다) 다시 지어 잇는다 — 빈 루트에서만 쓴다.
* 주말은 부르지 않는다. 평일 휴장은 ``daily_report.sh`` 가 :data:`EX_HOLIDAY` 로 알려 주고 요약에 "휴장" 으로 남는다
  (달력이 없어 DB 의 수급일로 가른다).
* 이미 재구성한 날은 ``daily_report.sh`` 가 건너뛴다 — 끊겨도 다시 돌리면 이어서 간다. 다시 만들려면 ``--force``.
* 날마다 출력은 ``<root>/.rebuild/<날>.log``, 요약은 ``<root>/.rebuild/summary.tsv`` 와 표준 출력의 표.

DB 접속정보는 ``daily_report.sh`` 가 불러 쓴다(``scripts/_env.sh``). 이 스크립트는 DB 를 직접 읽지 않는다.
종료코드 0 = 거래일 전부 검증 통과, 1 = 하나라도 실패·미완·오류.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
#: ``daily_report.sh`` 가 재구성 기준일이 거래일이 아닐 때 내는 코드.
EX_HOLIDAY = 3
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_COUNT = re.compile(r"검사 (\d+)건 · 실패 (\d+)건")
_CLAIMS = re.compile(r"문서 주장 어긋남 (\d+)건")


def weekdays(start: str, end: str) -> list[str]:
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    while d <= last:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def verify_summary(folder: Path) -> tuple[str, str]:
    """(검사 결과 한 줄, 실패 목록). 폴더의 ``VERIFY.txt`` 끝을 읽는다."""
    v = folder / "VERIFY.txt"
    if not v.is_file():
        return "-", ""
    text = v.read_text(encoding="utf-8", errors="replace")
    m = _COUNT.search(text)
    fails = next((ln.split(":", 1)[1].strip() for ln in reversed(text.splitlines())
                  if ln.startswith("실패 목록:")), "")
    head = f"{m.group(1)}건 중 실패 {m.group(2)}" if m else "형식 불명"
    c = _CLAIMS.search(text)        # 문서 주장 어긋남은 실패가 아니다(CLAIMS.txt) — 따로 센다
    if c and int(c.group(1)):
        head += f" · 주장 {c.group(1)}"
    return head, fails


def code_rev() -> str:
    if os.environ.get("KR_QUANT_CODE_REV"):
        return os.environ["KR_QUANT_CODE_REV"]
    try:
        return subprocess.run(["git", "-C", str(REPO), "describe", "--always", "--dirty"],
                              capture_output=True, text=True, check=True).stdout.strip() or "?"
    except (OSError, subprocess.CalledProcessError):
        return "?"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="리포트를 지을 루트(운영 reports/ 가 아니라 따로 둔 곳을 권한다)")
    ap.add_argument("--from", dest="start", help="첫 기준일(포함)")
    ap.add_argument("--to", dest="end", help="마지막 기준일(포함)")
    ap.add_argument("--dates", nargs="*", default=[], help="날짜를 직접 준다(--from/--to 대신)")
    ap.add_argument("--origin-root", help="원본 리포트가 지금 있는 루트 — 그날 폴더가 있으면 REBUILT.txt 에 원본 위치를 적는다")
    ap.add_argument("--origin-label", help="REBUILT.txt 에 적을 원본 루트 이름(기본 --origin-root). 바꿔 낀 뒤 옮길 곳을 적는다")
    ap.add_argument("--force", action="store_true", help="이미 재구성한 날도 다시 만든다")
    ap.add_argument("--jobs", type=int, default=1,
                    help="날짜 구간을 N 덩어리로 나눠 나란히 짓는다(빈 루트에서만). 덩어리 첫날은 끝에 다시 짓는다")
    a = ap.parse_args(argv)

    if a.dates:
        days = sorted(set(a.dates))
    elif a.start and a.end:
        days = weekdays(a.start, a.end)
    else:
        ap.error("--from 과 --to, 또는 --dates 가 필요하다")
    root = Path(a.root).expanduser().resolve()
    logs = root / ".rebuild"
    logs.mkdir(parents=True, exist_ok=True)
    rev = code_rev()
    label = a.origin_label or a.origin_root

    def build(d: str, where: Path, force: bool) -> tuple:
        """그날 하나를 ``where`` 루트에 만든다 → (날, rc, 결과, VERIFY, 실패 항목, 걸린 시간)."""
        env = {**os.environ, "KR_QUANT_ASOF": d, "KR_QUANT_REPORTS": str(where), "KR_QUANT_CODE_REV": rev}
        env.pop("KR_QUANT_FINAL", None)
        env.pop("KR_QUANT_REBUILD_ORIGIN", None)
        if force:
            env["KR_QUANT_FORCE"] = "1"
        else:
            env.pop("KR_QUANT_FORCE", None)
        if a.origin_root and (Path(a.origin_root).expanduser() / d).is_dir():
            env["KR_QUANT_REBUILD_ORIGIN"] = f"{label.rstrip('/')}/{d}"
        t0 = time.monotonic()
        log_path = logs / f"{d}.log"
        start = log_path.stat().st_size if log_path.exists() else 0
        with open(log_path, "a", encoding="utf-8") as log:
            rc = subprocess.run([str(REPO / "scripts" / "daily_report.sh")], env=env,
                                stdout=log, stderr=subprocess.STDOUT).returncode
        secs = time.monotonic() - t0
        with open(log_path, encoding="utf-8", errors="replace") as f:
            f.seek(start)
            said = f.read()
        folder = where / d
        if rc == EX_HOLIDAY:
            state = "휴장"
        elif rc != 0 and "수집 공백" in said:
            state = "수집 공백"
        elif rc != 0 and ("수집 미완" in said or (folder / "INCOMPLETE.txt").is_file()):
            # 원본이 있는 폴더에는 INCOMPLETE.txt 를 안 남기므로 그날 출력으로도 가른다.
            state = "수집 미완"
        elif not (folder / "REBUILT.txt").is_file():
            state = "오류"
        elif (folder / "VERIFY_FAILED.txt").is_file():
            state = "검증 실패"
        else:
            state = "재구성"
        verdict, fails = verify_summary(folder) if state != "휴장" else ("-", "")
        row = (d, rc, state, verdict, fails, f"{secs:.0f}s")
        print(f"{d}\trc={rc}\t{state}\t{verdict}\t{fails}\t{secs:.0f}s", flush=True)
        return row

    for d in days:
        (logs / f"{d}.log").unlink(missing_ok=True)
    jobs = max(1, min(a.jobs, len(days)))
    if jobs == 1:
        rows = [build(d, root, a.force) for d in days]
    else:
        # 덩어리마다 따로 둔 루트에 차례로 짓는다(daily_report.sh 는 루트마다 잠금 하나라 한 루트에 나란히 못 짓는다).
        # 다 모은 뒤 덩어리 첫날만 — 그때는 직전 거래일 폴더가 옆에 있다 — 다시 지어 change 를 잇는다.
        # 첫 판은 지우지 않고 .rebuild/first-pass/<날>.<시각> 으로 비켜 둔다.
        busy = [d for d in days if (root / d).exists()]
        if busy:
            ap.error(f"--jobs 는 빈 루트에서만 쓴다 — 이미 있는 날: {' '.join(busy[:3])}")
        size = -(-len(days) // jobs)
        chunks = [days[i:i + size] for i in range(0, len(days), size)]
        parts = [logs / f"chunk-{k}" for k in range(len(chunks))]

        def run_chunk(k: int) -> list:
            parts[k].mkdir(parents=True, exist_ok=True)
            return [build(d, parts[k], a.force) for d in chunks[k]]

        with ThreadPoolExecutor(len(chunks)) as ex:
            got = list(ex.map(run_chunk, range(len(chunks))))
        rows = [r for part in got for r in part]
        for part in parts:
            for d in sorted(p.name for p in part.iterdir() if _DAY.fullmatch(p.name)):
                (part / d).rename(root / d)
        first_pass = logs / "first-pass"
        first_pass.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d%H%M%S")
        for k in range(len(chunks)):
            # 덩어리 첫날이 휴장이면 다음 날이 첫 거래일이다. 0번 덩어리도 루트에 더 이른 리포트가 이미 있으면 잇는다.
            d = next((r[0] for r in got[k] if r[2] != "휴장"), None)
            if d is None:
                continue
            if not any(p.name < d and (p / "payload.json").is_file()
                       for p in root.iterdir() if _DAY.fullmatch(p.name)):
                continue
            if (root / d).exists():
                (root / d).rename(first_pass / f"{d}.{stamp}")
            rows = [build(d, root, False) if r[0] == d else r for r in rows]
        last = max((p.name for p in root.iterdir() if _DAY.fullmatch(p.name) and (p / "numbers.html").is_file()),
                   default=None)
        if last:
            tmp = root / ".latest.tmp"
            tmp.unlink(missing_ok=True)
            tmp.symlink_to(root / last)
            tmp.replace(root / "latest")
    bad = sum(1 for _d, rc, state, *_ in rows if state not in ("휴장", "재구성") or rc not in (0, EX_HOLIDAY))

    with open(logs / "summary.tsv", "w", encoding="utf-8") as f:
        f.write("date\trc\tstate\tverify\tfailed\tsecs\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    print("\n| 기준일 | rc | 결과 | VERIFY | 실패 항목 |\n|---|---:|---|---|---|")
    for d, rc, state, verdict, fails, _s in rows:
        print(f"| {d} | {rc} | {state} | {verdict} | {fails or ''} |")
    print(f"\n요약: {logs / 'summary.tsv'} · 날짜별 출력: {logs}/<날>.log · 문제 {bad}건")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
