#!/usr/bin/env python
"""지난 기준일들을 지금 코드·확정 데이터로 다시 만든다 — ``daily_report.sh`` 를 ``KR_QUANT_ASOF`` 로 날마다 부른다.

    python scripts/rebuild_history.py --root /tmp/history --from 2026-08-27 --to 2026-11-06 \\
        --origin-root reports --origin-label reports/.archive/pre-rebuild-2026-11-08

* **오래된 날부터 차례로** 만든다 — 직전 대비(``change``)가 같은 루트의 직전 거래일 폴더를 읽는다.
  거꾸로 또는 나란히 만들면 그 칸이 비거나 옛 루트의 리포트와 섞인다.
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
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
#: ``daily_report.sh`` 가 재구성 기준일이 거래일이 아닐 때 내는 코드.
EX_HOLIDAY = 3
_COUNT = re.compile(r"검사 (\d+)건 · 실패 (\d+)건")


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

    rows, bad = [], 0
    for d in days:
        env = {**os.environ, "KR_QUANT_ASOF": d, "KR_QUANT_REPORTS": str(root), "KR_QUANT_CODE_REV": rev}
        env.pop("KR_QUANT_FINAL", None)
        env.pop("KR_QUANT_REBUILD_ORIGIN", None)
        if a.force:
            env["KR_QUANT_FORCE"] = "1"
        else:
            env.pop("KR_QUANT_FORCE", None)
        if a.origin_root and (Path(a.origin_root).expanduser() / d).is_dir():
            env["KR_QUANT_REBUILD_ORIGIN"] = f"{label.rstrip('/')}/{d}"
        t0 = time.monotonic()
        with open(logs / f"{d}.log", "w", encoding="utf-8") as log:
            rc = subprocess.run([str(REPO / "scripts" / "daily_report.sh")], env=env,
                                stdout=log, stderr=subprocess.STDOUT).returncode
        secs = time.monotonic() - t0
        folder = root / d
        if rc == EX_HOLIDAY:
            state = "휴장"
        elif (folder / "INCOMPLETE.txt").is_file() and not (folder / "numbers.html").is_file():
            state = "수집 미완"
        elif not (folder / "REBUILT.txt").is_file():
            state = "오류"
        elif (folder / "VERIFY_FAILED.txt").is_file():
            state = "검증 실패"
        else:
            state = "재구성"
        verdict, fails = verify_summary(folder) if state != "휴장" else ("-", "")
        if state not in ("휴장", "재구성") or rc not in (0, EX_HOLIDAY):
            bad += 1
        rows.append((d, rc, state, verdict, fails, f"{secs:.0f}s"))
        print(f"{d}\trc={rc}\t{state}\t{verdict}\t{fails}\t{secs:.0f}s", flush=True)

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
