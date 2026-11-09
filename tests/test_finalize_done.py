"""``scripts/finalize_done.py`` 의 창 — 기준일 뒤에 끝난 실행이라도 그 창(달력 3일)이 기준일을 덮어야 확정이다.

창은 수집기의 ``date.today()``(Airflow 컨테이너, UTC)에서 센다. 시도 기록 첫 줄의 Airflow 타임스탬프가 그 날이다.
"""
from __future__ import annotations

import calendar
import importlib.util
import os
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("finalize_done", REPO / "scripts" / "finalize_done.py")
fd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fd)


#: 실제 기록의 완료 줄 모양(2026-11-07 ``daily_finalize``).
DONE = {"finalize_sector": "✅ 완료: done=65 fail=0 rows=260",
        "finalize_both": "✅ 완료: done=2631 skip=0 fail=1 | 일봉 10,522행 수급 10,522행"}


def _run(root: Path, run_id: str, started_utc: str, ended_local: str, ok: bool = True, done: "dict | None" = None,
         ended: "dict | None" = None) -> None:
    """``started_utc`` 는 Airflow 가 첫 줄에 찍는 시각(UTC), ``ended_local`` 은 파일 시각(**KST** — 이 머신의 시간대와
    무관하게. ``time.mktime`` 으로 읽으면 CI(UTC)에서 9시간 밀린다). ``done``·``ended`` 는 태스크별로 완료 줄·끝난 시각을 바꾼다."""
    lines = {**DONE, **(done or {})}
    for task in fd.TASKS:
        d = root / "dag_id=daily_finalize" / f"run_id={run_id}" / f"task_id={task}"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "attempt=1.log"
        f.write_text(f"[{started_utc}.005+0000] {{local_task_job_runner.py:123}} INFO - ::group::Pre task\n"
                     f"[{started_utc}.900+0000] {{logging_mixin.py:190}} INFO - {lines.get(task, '')}\n"
                     f"... Task exited with return code {0 if ok else 1}\n")
        at = (ended or {}).get(task, ended_local)
        t = datetime.strptime(at, "%Y-%m-%d %H:%M").replace(tzinfo=fd.KST).timestamp()
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


def test_a_run_counts_only_once_every_task_ended_after_the_day(tmp_path):
    """"끝난 때" 는 **가장 먼저 끝난 태스크**다. 한 태스크라도 기준일 안에 끝났으면(그날 저녁 수동 재실행 등) 그 태스크는
    기준일 다음날의 확정 시도가 아니다 — 가장 늦은 태스크로 재면 나머지가 확정을 대신 받는다."""
    _run(tmp_path, "scheduled__2026-11-05T21:30:00+00:00", "2026-11-06T09:00:00", "2026-11-07 07:20",
         ended={"finalize_sector": "2026-11-06 20:00"})
    ok, why = fd.finalize_done(str(tmp_path), "2026-11-06")
    assert not ok and "아직 안 돌았" in why, why


def test_the_worst_failure_count_seen_in_24_runs_is_still_final(tmp_path):
    """실측 최대(2026-11-03 실행) 13/2,633 = 0.49% — 평소의 개별 종목 실패다."""
    _run(tmp_path, "scheduled__2026-11-05T21:30:00+00:00", "2026-11-06T21:31:18", "2026-11-07 07:20",
         done={"finalize_both": "✅ 완료: done=2620 skip=0 fail=13 | 일봉 5,245행 수급 5,233행"})
    ok, why = fd.finalize_done(str(tmp_path), "2026-11-06")
    assert ok, why


@pytest.mark.parametrize("task,line", [
    ("finalize_both", "✅ 완료: done=2600 skip=0 fail=33 | 일봉 5,200행 수급 5,200행"),   # 1.25% — 종료코드는 0 이다(문턱 20%)
    ("finalize_sector", "✅ 완료: done=60 fail=5 rows=240"),
])
def test_exit_code_zero_with_too_many_failures_is_not_final(tmp_path, task, line):
    """``collectors.combined`` 는 실패율 20% 까지 종료코드 0 을 낸다. 그 종목들은 잠정치 그대로다."""
    _run(tmp_path, "scheduled__2026-11-05T21:30:00+00:00", "2026-11-06T21:31:18", "2026-11-07 07:20", done={task: line})
    ok, why = fd.finalize_done(str(tmp_path), "2026-11-06")
    assert not ok and task in why and "문턱" in why, why


def test_a_log_without_the_completion_line_is_named_not_passed(tmp_path):
    """종료코드 0 인데 완료 줄이 없으면 출력 형식이 바뀐 것이다 — 조용히 통과하면 실패율 검사가 죽어도 모른다."""
    _run(tmp_path, "scheduled__2026-11-05T21:30:00+00:00", "2026-11-06T21:31:18", "2026-11-07 07:20",
         done={"finalize_both": "끝났다"})
    ok, why = fd.finalize_done(str(tmp_path), "2026-11-06")
    assert not ok and "완료 줄" in why, why


def test_run_day_falls_back_to_file_time_without_a_stamp(tmp_path):
    """첫 줄 타임스탬프가 없으면 파일 시각의 **UTC** 날짜다 — 토 07:20 KST 에 끝난 기록은 금요일(UTC 22:20)."""
    f = tmp_path / "attempt=1.log"
    f.write_text("no stamp\n")
    t = calendar.timegm((2026, 11, 6, 22, 20, 0))      # = 2026-11-07 07:20 KST — 이 머신 시간대와 무관
    os.utime(f, (t, t))
    assert fd.run_day(str(f)).isoformat() == "2026-11-06"


def test_no_test_reads_the_machine_timezone():
    """시험의 시각은 시간대를 박아 쓴다 — ``time.mktime``·``time.localtime`` 은 이 머신의 시간대로 읽어, KST 에서
    초록이던 확정 창 시험이 CI(UTC)에서 하루 밀려 빨개졌다(2026-11-08, PR #28 머지 뒤)."""
    import re
    bad = []
    for p in sorted((Path(__file__).parent).glob("test_*.py")):
        if p.name == Path(__file__).name:
            continue                                  # 이 검사 자신은 금지어를 적는다
        for i, line in enumerate(p.read_text("utf-8").splitlines(), 1):
            if re.search(r"\btime\.(mktime|localtime)\(", line):
                bad.append(f"{p.name}:{i}")
    assert not bad, f"로컬 시간대로 시각을 바꾸는 시험: {bad} — datetime(..., tzinfo=KST/UTC).timestamp() 를 쓴다"
