#!/usr/bin/env python
"""기준일의 확정치가 DB 에 들어왔나 — quant-airflow ``daily_finalize`` 의 작업 기록을 본다.

배치(18:10)는 그날 잠정치로 리포트를 만든다. 확정치는 다음날 06:30 ``daily_finalize`` 가
``daily_bars``·``supply_demand``(``finalize_both``)와 ``daily_bars_adjusted``(``rebuild_adjusted``)에
덮는다(~07:25 끝). 그 뒤 08:20 크론이 이 스크립트로 확인하고 전날 리포트를 확정치로 다시 만든다
(2026-11-08 지표 검증: 게시본의 G 가 잠정치에 흔들려 합격선 90% 에 62.5%).

DB 에는 "확정했다" 는 표시가 없다. 같은 머신의 Airflow 작업 기록에서 두 태스크의 **마지막 시도**가
``Task exited with return code 0`` 으로 끝났고, 그 기록이 기준일 **다음날 이후**에 끝났으면 확정으로 본다
(창이 달력 3일이라 기준일을 덮는다). 확인이 안 되면 리포트를 그대로 둔다 — 잠정이라고 적혀 있는
리포트가 틀린 확정보다 낫다.

종료코드 0 = 확정 확인, 1 = 확인 안 됨(이유를 찍는다).
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
TASKS = ("finalize_both", "rebuild_adjusted")
OK_LINE = "Task exited with return code 0"
DEFAULT_LOGS = os.path.expanduser("~/git/quant-airflow/logs")


def _last_attempt(run_dir: str, task: str) -> str | None:
    files = glob.glob(os.path.join(run_dir, f"task_id={task}", "attempt=*.log"))
    if not files:
        return None
    return max(files, key=lambda p: int(p.rsplit("attempt=", 1)[1].split(".")[0]))


def finalize_done(logs_root: str, asof: str) -> tuple[bool, str]:
    """(확정 여부, 이유). 기준일 다음날 0시(KST) 이후에 끝난 가장 최근 실행을 본다."""
    runs = glob.glob(os.path.join(logs_root, "dag_id=daily_finalize", "run_id=*"))
    if not runs:
        return False, f"daily_finalize 기록이 없다({logs_root})"
    after = datetime.combine(date.fromisoformat(asof) + timedelta(days=1), datetime.min.time(), KST)
    best = None
    for run in runs:
        logs = [_last_attempt(run, t) for t in TASKS]
        if any(p is None for p in logs):
            continue
        ended = min(datetime.fromtimestamp(os.path.getmtime(p), KST) for p in logs)
        if ended < after:
            continue
        if best is None or ended > best[0]:
            best = (ended, run, logs)
    if best is None:
        return False, f"{asof} 다음날 이후에 끝난 daily_finalize 실행이 없다 — 아직 안 돌았거나 도는 중"
    ended, run, logs = best
    for task, path in zip(TASKS, logs):
        with open(path, encoding="utf-8", errors="replace") as f:
            if OK_LINE not in f.read():
                return False, f"{os.path.basename(run)} 의 {task} 가 성공으로 끝나지 않았다({path})"
    return True, f"{os.path.basename(run)} {ended:%F %T} 확정 완료({', '.join(TASKS)})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--asof", required=True, help="기준일 YYYY-MM-DD")
    ap.add_argument("--logs", default=os.environ.get("KR_QUANT_AIRFLOW_LOGS", DEFAULT_LOGS),
                    help="quant-airflow 의 Airflow 로그 폴더")
    a = ap.parse_args(argv)
    ok, why = finalize_done(a.logs, a.asof)
    print(f"[확정치] {why}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
