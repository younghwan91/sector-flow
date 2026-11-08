#!/usr/bin/env python
"""기준일의 확정치가 DB 에 들어왔나 — quant-airflow ``daily_finalize`` 의 작업 기록을 본다.

배치(18:10)는 그날 잠정치로 리포트를 만든다. 확정치는 다음날 06:30 ``daily_finalize`` 가
``sector_index``(``finalize_sector``)·``daily_bars``·``supply_demand``(``finalize_both``)와
``daily_bars_adjusted``(``rebuild_adjusted``)에 덮는다(~07:25 끝). 그 뒤 08:20 크론이 이 스크립트로 확인하고 전날 리포트를 확정치로 다시 만든다
(2026-11-08 지표 검증: 게시본의 G 가 잠정치에 흔들려 합격선 90% 에 62.5%).

DB 에는 "확정했다" 는 표시가 없다. 같은 머신의 Airflow 작업 기록에서 :data:`TASKS` 의 **마지막 시도**가
``Task exited with return code 0`` 으로 끝났고, 그 기록이 기준일 **다음날 이후**에 끝났고, 그 시도의 **창이 기준일을
덮으면** 확정으로 본다. 확인이 안 되면 리포트를 그대로 둔다 — 잠정이라고 적혀 있는 리포트가 틀린 확정보다 낫다.

**창.** 수집기는 ``--days 3``·``--daily-days 3``·``--sd-days 3`` 으로 ``date.today() - 3일`` 이상(포함)만 덮는다
(quant-airflow ``collectors/storage.date_days_ago``, ``dt >= cutoff``). ``date.today()`` 는 **태스크가 실제로 돈 순간**의
컨테이너 날짜이고 컨테이너는 UTC 다(2026-11-08 ``docker exec … date`` 확인) — 토 06:30 KST 실행은 UTC 로 금요일이라
창이 화~금이다. run_id 의 날짜(logical date = data_interval_start)는 그보다 한 틱 앞이라 쓰면 틀린다: 화요일 실행의
run_id 는 지난 토요일이어서 월요일 기준일이 창 밖으로 보인다. 그래서 시도 기록 첫 줄의 Airflow 타임스탬프
(``[2026-11-06T21:31:18.005+0000]``, 늘 UTC)로 실행일을 잡고, 없으면 파일 시각(끝난 때 — 늦으면 창을 좁게 본다)을 쓴다.
창 밖 기준일(재구성으로 지난 날을 다시 만들 때 등)은 그 뒤 실행이 성공했어도 확정이 아니다 — 아무도 그날을 다시 받지 않았다.

종료코드 0 = 확정 확인, 1 = 확인 안 됨(이유를 찍는다).
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
#: 리포트가 읽는 표를 덮는 ``daily_finalize`` 태스크(quant-airflow ``dags/daily_finalize.py``,
#: 순서 finalize_sector >> finalize_both >> rebuild_adjusted >> check_data_quality).
#:
#: * ``finalize_sector`` — ``sector_index``. ``sector_flow.py`` 가 업종지수 수익률로 읽는다.
#: * ``finalize_both`` — ``supply_demand``·``daily_bars``. 수급 전체와 시총의 원 종가.
#: * ``rebuild_adjusted`` — ``daily_bars_adjusted``. 섹터 수익률의 원천.
#:
#: 넣지 않은 것: ``check_data_quality`` 는 읽기만 한다(덮는 값이 없다). ``daily_bars_raw`` 는 이 DAG 가
#: 아니라 평일 19:00 ``daily_bars_raw`` DAG 가 최근 5일을 다시 받아 확정한다 — 리포트는 시총 가격으로
#: ``supply_demand.close`` 를 먼저 쓰고 그게 없을 때만 raw 를 쓰므로(storage.market_cap_asof_bulk) 여기서
#: 기다리지 않는다.
TASKS = ("finalize_sector", "finalize_both", "rebuild_adjusted")
OK_LINE = "Task exited with return code 0"
#: 수집기 창(달력 일) — dags/daily_finalize.py 의 ``--days 3``·``--daily-days 3``·``--sd-days 3``.
WINDOW_DAYS = 3
#: 수집기의 ``date.today()`` 가 도는 시간대 — Airflow 컨테이너는 UTC 다.
COLLECTOR_TZ = timezone.utc
_STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?([+-]\d{4})\]")
DEFAULT_LOGS = os.path.expanduser("~/git/quant-airflow/logs")


def _last_attempt(run_dir: str, task: str) -> str | None:
    files = glob.glob(os.path.join(run_dir, f"task_id={task}", "attempt=*.log"))
    if not files:
        return None
    return max(files, key=lambda p: int(p.rsplit("attempt=", 1)[1].split(".")[0]))


def run_day(path: str) -> date:
    """그 시도가 돈 날 — 수집기의 ``date.today()``(:data:`COLLECTOR_TZ`). 첫 줄 타임스탬프, 없으면 파일 시각."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            m = _STAMP.match(f.readline())
    except OSError:
        m = None
    if m:
        started = datetime.strptime(f"{m[1]}T{m[2]}{m[3]}", "%Y-%m-%dT%H:%M:%S%z")
    else:
        started = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
    return started.astimezone(COLLECTOR_TZ).date()


def finalize_done(logs_root: str, asof: str) -> tuple[bool, str]:
    """(확정 여부, 이유). 기준일 다음날 0시(KST) 이후에 끝난 가장 최근 실행을 본다."""
    runs = glob.glob(os.path.join(logs_root, "dag_id=daily_finalize", "run_id=*"))
    if not runs:
        return False, f"daily_finalize 기록이 없다({logs_root})"
    day = date.fromisoformat(asof)
    after = datetime.combine(day + timedelta(days=1), datetime.min.time(), KST)
    best = None
    late = 0                 # 기준일 뒤에 끝났는데 창이 기준일을 못 덮은 실행
    seen: set[str] = set()   # 어느 실행에서든 시도 기록이 나온 태스크
    for run in runs:
        logs = [_last_attempt(run, t) for t in TASKS]
        seen.update(t for t, p in zip(TASKS, logs) if p is not None)
        if any(p is None for p in logs):
            continue
        ended = min(datetime.fromtimestamp(os.path.getmtime(p), KST) for p in logs)
        if ended < after:
            continue
        # 창은 태스크마다 그 시도가 돈 날에서 센다 — 하나라도 기준일을 못 덮으면 그 표는 덮이지 않았다.
        if any(day < run_day(p) - timedelta(days=WINDOW_DAYS) for p in logs):
            late += 1
            continue
        if best is None or ended > best[0]:
            best = (ended, run, logs)
    # 실행 폴더는 있는데 기대한 자리에 시도 기록이 하나도 없으면 "아직 안 돌았다" 가 아니라 경로 규칙이
    # 어긋난 것이다(Airflow 업그레이드로 로그 배치가 바뀌었거나 태스크 이름이 바뀌었다). 그 말을 그대로 한다 —
    # 안 그러면 매일 "아직 안 돌았다" 로 보이며 확정 리포트가 영영 안 나온다.
    if not seen:
        return False, (f"daily_finalize 실행 폴더 {len(runs)}개 어디에도 task_id=<태스크>/attempt=*.log 가 없다 — "
                       f"Airflow 로그 경로 형식이 바뀌었을 수 있다. finalize_done.py 의 경로 규칙을 고쳐라({logs_root})")
    missing = [t for t in TASKS if t not in seen]
    if missing:
        return False, (f"daily_finalize 기록에 {', '.join(missing)} 의 시도가 한 번도 없다 — 처음 도는 중이 아니면 태스크 이름이 바뀐 것이다. "
                       f"quant-airflow dags/daily_finalize.py 와 finalize_done.TASKS 를 맞춰라")
    if best is None and late:
        return False, (f"{asof} 뒤에 끝난 daily_finalize 실행 {late}개 모두 창(달력 {WINDOW_DAYS}일)이 {asof} 를 덮지 않는다 — "
                       f"그날 값은 다시 받지 않았다(확정 실행을 놓친 지난 날). 확정치가 필요하면 그 날짜로 재수집해야 한다")
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
