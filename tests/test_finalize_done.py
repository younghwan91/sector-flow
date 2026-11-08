"""``scripts/finalize_done.py`` 의 창 — 기준일 뒤에 끝난 실행이라도 그 창(달력 3일)이 기준일을 덮어야 확정이다.

창은 수집기의 ``date.today()``(Airflow 컨테이너, UTC)에서 센다. 시도 기록 첫 줄의 Airflow 타임스탬프가 그 날이다.
"""
from __future__ import annotations

import calendar
import importlib.util
import os
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("finalize_done", REPO / "scripts" / "finalize_done.py")
fd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fd)


def _run(root: Path, run_id: str, started_utc: str, ended_local: str, ok: bool = True) -> None:
    """``started_utc`` 는 Airflow 가 첫 줄에 찍는 시각(UTC), ``ended_local`` 은 파일 시각(이 머신 시간대)."""
    for task in fd.TASKS:
        d = root / "dag_id=daily_finalize" / f"run_id={run_id}" / f"task_id={task}"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "attempt=1.log"
        f.write_text(f"[{started_utc}.005+0000] {{local_task_job_runner.py:123}} INFO - ::group::Pre task\n"
                     f"... Task exited with return code {0 if ok else 1}\n")
        t = time.mktime(time.strptime(ended_local, "%Y-%m-%d %H:%M"))
        os.utime(f, (t, t))


def test_saturday_run_finalizes_friday(tmp_path):
    """토 06:30 KST 실행 = UTC 금요일 → 창 화~금."""
    _run(tmp_path, "scheduled__2026-11-05T21:30:00+00:00", "2026-11-06T21:31:18", "2026-11-07 07:20")
    ok, why = fd.finalize_done(str(tmp_path), "2026-11-06")
    assert ok, why


def test_tuesday_run_finalizes_monday_though_its_run_id_says_saturday(tmp_path):
    """화(11-10) 06:30 KST 실행의 run_id 는 ``scheduled__2026-11-06T21:30Z`` — logical date 는 한 틱 앞(토 11-07 06:30 KST)이다.

    run_id 날짜(UTC 11-06)로 창을 세면 11-03~11-06 이라 월요일(11-09)이 창 밖으로 보인다. 수집기는 실제로 돈 순간의
    UTC 날짜(첫 줄 ``2026-11-09T21:31Z`` → 11-09)에서 3일을 세므로 창은 11-06~11-09 이고 월요일을 덮는다."""
    _run(tmp_path, "scheduled__2026-11-06T21:30:00+00:00", "2026-11-09T21:31:18", "2026-11-10 07:20")
    ok, why = fd.finalize_done(str(tmp_path), "2026-11-09")
    assert ok, why


def test_a_later_run_whose_window_misses_the_day_is_not_its_finalize(tmp_path):
    """기준일 뒤에 끝났다는 것만으로는 부족하다 — 열흘 뒤 실행은 그날을 다시 받지 않았다(재구성·놓친 날)."""
    _run(tmp_path, "scheduled__2026-11-12T21:30:00+00:00", "2026-11-13T21:31:18", "2026-11-14 07:20")
    ok, why = fd.finalize_done(str(tmp_path), "2026-11-02")
    assert not ok
    assert "창" in why and "덮지 않는다" in why


def test_the_covering_run_is_judged_not_the_newest(tmp_path):
    """창이 덮는 실행이 실패했으면, 그 뒤 창 밖 실행이 성공했어도 확정이 아니다."""
    _run(tmp_path, "scheduled__2026-11-02T21:30:00+00:00", "2026-11-03T21:31:18", "2026-11-04 07:20", ok=False)
    _run(tmp_path, "scheduled__2026-11-12T21:30:00+00:00", "2026-11-13T21:31:18", "2026-11-14 07:20")
    ok, why = fd.finalize_done(str(tmp_path), "2026-11-03")
    assert not ok and "성공으로 끝나지 않았다" in why


def test_window_edge_is_inclusive(tmp_path):
    """수집기는 ``dt >= today - 3일`` — 창의 첫날도 덮인다(월요일 휴장 뒤 화요일 실행이 금요일을 덮는다)."""
    _run(tmp_path, "scheduled__2026-11-06T21:30:00+00:00", "2026-11-09T21:31:18", "2026-11-10 07:20")
    assert fd.finalize_done(str(tmp_path), "2026-11-06")[0]
    assert not fd.finalize_done(str(tmp_path), "2026-11-05")[0]


def test_run_day_falls_back_to_file_time_without_a_stamp(tmp_path):
    """첫 줄 타임스탬프가 없으면 파일 시각의 **UTC** 날짜다 — 토 07:20 KST 에 끝난 기록은 금요일(UTC 22:20)."""
    f = tmp_path / "attempt=1.log"
    f.write_text("no stamp\n")
    t = calendar.timegm((2026, 11, 6, 22, 20, 0))      # = 2026-11-07 07:20 KST — 이 머신 시간대와 무관
    os.utime(f, (t, t))
    assert fd.run_day(str(f)).isoformat() == "2026-11-06"
