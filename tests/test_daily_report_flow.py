"""``daily_report.sh``·``cron_run.sh``·크론 정본 — 수집 미완인 날의 흐름 전체.

스크립트를 임시 레포에 복사하고 ``uv`` 와 무거운 세 스크립트를 가짜로 바꿔, 진짜 관문을 sqlite 로
태운다. 확인하는 것은 제어 흐름이다: 미완이면 안 쓰고 ``latest`` 를 지키며, **다음 실행이 그 기준일을
다시 만든다**(2026-10-27 은 다음 실행의 기준일이 달라 영영 안 만들어졌다).
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash 스크립트")

REPO = Path(__file__).resolve().parent.parent
DAYS = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02"]
TODAY = DAYS[-1]
FAKE_SECTOR_FLOW = """
import pathlib, sys
a = sys.argv
if "--final" in a:
    pathlib.Path(a[a.index("--json") + 1]).with_name("ARGS_FINAL").write_text("1")
if "--json" in a:
    pathlib.Path(a[a.index("--json") + 1]).write_text("{}")
if "--html" in a:
    pathlib.Path(a[a.index("--html") + 1]).write_text("<html>")
"""
FAKE_NUMBERS = """
import pathlib, sys
a = sys.argv
pathlib.Path(a[a.index("--html") + 1]).write_text("<html>")
"""
FAKE_VERIFY = """
import os, sys
sys.exit(int(os.environ.get("FAKE_VERIFY_RC", "0")))
"""


def _seed(db: Path, today_kosdaq: int) -> None:
    con = sqlite3.connect(db)
    con.executescript("""
      CREATE TABLE IF NOT EXISTS stocks(code TEXT PRIMARY KEY, name TEXT, market TEXT, sector TEXT);
      CREATE TABLE IF NOT EXISTS supply_demand(code TEXT, date TEXT, close REAL, institution REAL,
                                               source TEXT);
      CREATE TABLE IF NOT EXISTS daily_bars(code TEXT, date TEXT, close REAL);
      CREATE TABLE IF NOT EXISTS shares_outstanding_history(code TEXT, date TEXT,
                                                            shares_outstanding INTEGER);
      CREATE TABLE IF NOT EXISTS daily_bars_raw(code TEXT, date TEXT, close REAL);
      CREATE TABLE IF NOT EXISTS daily_bars_adjusted(code TEXT, date TEXT, close REAL);
      CREATE TABLE IF NOT EXISTS corporate_actions(code TEXT, date TEXT, upd_stkpc_tp TEXT, upd_rt REAL);
      DELETE FROM stocks; DELETE FROM supply_demand; DELETE FROM daily_bars;
      DELETE FROM shares_outstanding_history; DELETE FROM daily_bars_raw; DELETE FROM daily_bars_adjusted;
    """)
    for i in range(100):
        code = f"{i:06d}"
        market = "거래소" if i < 50 else "코스닥"
        con.execute("INSERT INTO stocks VALUES(?,?,?,?)", (code, code, market, "S"))
        con.execute("INSERT INTO shares_outstanding_history VALUES(?,?,?)",
                    (code, "2020-01-01", 1000))
        for d in DAYS:
            if d == TODAY and market == "코스닥" and i - 50 >= today_kosdaq:
                continue
            con.execute("INSERT INTO supply_demand VALUES(?,?,?,?,?)",
                        (code, d, 100.0, 1.0, "kiwoom"))
            con.execute("INSERT INTO daily_bars VALUES(?,?,?)", (code, d, 100.0))
            con.execute("INSERT INTO daily_bars_raw VALUES(?,?,?)", (code, d, 100.0))
            con.execute("INSERT INTO daily_bars_adjusted VALUES(?,?,?)", (code, d, 100.0))
    con.commit()
    con.close()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "sector-flow"
    (r / "scripts").mkdir(parents=True)
    for name in ("daily_report.sh", "_env.sh", "cron_run.sh", "completeness_gate.py", "finalize_done.py",
                 "freshness_check.sh", "freshness_check.py"):
        shutil.copy(REPO / "scripts" / name, r / "scripts" / name)
    (r / "scripts" / "sector_flow.py").write_text(FAKE_SECTOR_FLOW)
    (r / "scripts" / "sector_numbers.py").write_text(FAKE_NUMBERS)
    (r / "scripts" / "verify_report.py").write_text(FAKE_VERIFY)
    (r / ".env").write_text(f"KR_QUANT_DB={tmp_path / 'db.sqlite'}\n")
    # `uv run --quiet --extra pg python X` → 이 인터프리터로 X.
    uv = tmp_path / "uv"
    uv.write_text(f'#!/bin/sh\nshift 5\nexec "{sys.executable}" "$@"\n')
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    return r


def _env(repo: Path, today: str = TODAY, **env) -> dict[str, str]:
    """``today`` 를 오늘로 보는 환경. 시험 날짜는 고정이라 ``date +%F`` 만 가짜로 바꾼다."""
    shim = repo.parent / "bin"
    shim.mkdir(exist_ok=True)
    fake = shim / "date"
    fake.write_text(f'#!/bin/sh\n[ "$1" = "+%F" ] && {{ echo "{today}"; exit 0; }}\n'
                    f'exec "{shutil.which("date")}" "$@"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    e = {k: v for k, v in os.environ.items() if k not in ("KR_QUANT_DB", "KR_QUANT_REPORTS")}
    e.update(KR_QUANT_UV=str(repo.parent / "uv"), PATH=f"{shim}{os.pathsep}{e['PATH']}", **env)
    return e


def _run(repo: Path, today: str = TODAY, **env) -> subprocess.CompletedProcess:
    return subprocess.run([str(repo / "scripts" / "daily_report.sh")], capture_output=True,
                          text=True, env=_env(repo, today, **env), timeout=120)


def _publish_previous(repo: Path) -> Path:
    """전날 리포트가 ``latest`` 인 상태."""
    prev = repo / "reports" / DAYS[-2]
    prev.mkdir(parents=True)
    (prev / "numbers.html").write_text("<prev>")
    (repo / "reports" / "latest").symlink_to(prev)
    return prev


def test_partial_day_is_not_published_and_keeps_latest(repo):
    _seed(repo.parent / "db.sqlite", today_kosdaq=1)
    prev = _publish_previous(repo)
    p = _run(repo)
    assert p.returncode == 75, p.stdout + p.stderr
    dest = repo / "reports" / TODAY
    assert (dest / "INCOMPLETE.txt").is_file()
    assert "코스닥" in (dest / "INCOMPLETE.txt").read_text()
    assert not (dest / "numbers.html").exists()
    assert not (dest / "payload.json").exists()
    assert (repo / "reports" / "latest").resolve() == prev.resolve()


def test_partial_day_that_is_no_longer_today_exits_1_and_keeps_latest(repo):
    """다음날 07:30 도 못 만들면 마지막 기회를 놓친 것이다 — 재시도 경고(75)로 묻히면 안 된다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=1)
    prev = _publish_previous(repo)
    p = _run(repo, today="2026-11-03")
    assert p.returncode == 1, p.stdout + p.stderr
    assert TODAY in p.stderr
    dest = repo / "reports" / TODAY
    assert (dest / "INCOMPLETE.txt").is_file()
    assert not (dest / "numbers.html").exists()
    assert (repo / "reports" / "latest").resolve() == prev.resolve()


def test_giving_up_on_a_past_day_alerts_as_error_through_cron_run(repo, tmp_path):
    _seed(repo.parent / "db.sqlite", today_kosdaq=1)
    _publish_previous(repo)
    log, stub = tmp_path / "alert.log", tmp_path / "alert.sh"
    stub.write_text(f'#!/bin/sh\necho "$1|$2" >> "{log}"\ncat >> "{log}"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    p = subprocess.run(
        [str(repo / "scripts" / "cron_run.sh"), "rpt", "--",
         str(repo / "scripts" / "daily_report.sh")],
        capture_output=True, text=True, timeout=120,
        env=_env(repo, "2026-11-03", SECTOR_FLOW_ALERT=str(stub)))
    assert p.returncode == 1, p.stdout + p.stderr
    assert log.read_text().startswith("error|")


def test_later_run_regenerates_the_incomplete_day(repo):
    db = repo.parent / "db.sqlite"
    _seed(db, today_kosdaq=1)
    _publish_previous(repo)
    assert _run(repo).returncode == 75
    _seed(db, today_kosdaq=50)                         # 수집이 끝났다
    p = _run(repo)
    assert p.returncode == 0, p.stdout + p.stderr
    dest = repo / "reports" / TODAY
    assert (dest / "numbers.html").is_file()
    assert not (dest / "INCOMPLETE.txt").exists(), "미완 표시가 남아 있다"
    assert (repo / "reports" / "latest").resolve() == dest.resolve()
    assert not list((repo / "reports").glob("*.building.*"))
    assert not list((repo / "reports").glob("*.replaced.*"))


def test_complete_day_is_skipped_on_rerun(repo):
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    assert _run(repo).returncode == 0
    payload = repo / "reports" / TODAY / "payload.json"
    before = payload.stat().st_mtime_ns
    p = _run(repo)
    assert p.returncode == 0
    assert "건너뛴다" in p.stdout
    assert payload.stat().st_mtime_ns == before


def test_force_cannot_overwrite_a_good_report_with_a_partial_day(repo):
    db = repo.parent / "db.sqlite"
    _seed(db, today_kosdaq=50)
    assert _run(repo).returncode == 0
    dest = repo / "reports" / TODAY
    _seed(db, today_kosdaq=1)
    assert _run(repo, KR_QUANT_FORCE="1").returncode == 75
    assert (dest / "numbers.html").is_file()
    assert not (dest / "INCOMPLETE.txt").exists(), "멀쩡한 리포트에 미완 표시를 붙였다"


def test_verify_failure_publishes_alerts_and_is_retried(repo):
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    assert _run(repo, FAKE_VERIFY_RC="1").returncode == 1
    dest = repo / "reports" / TODAY
    assert (dest / "VERIFY_FAILED.txt").is_file()
    assert (repo / "reports" / "latest").resolve() == dest.resolve()
    assert _run(repo).returncode == 0
    assert not (dest / "VERIFY_FAILED.txt").exists(), "재시도가 검증 실패를 그대로 뒀다"


def test_empty_table_is_not_a_reference_date(repo):
    db = repo.parent / "db.sqlite"
    _seed(db, today_kosdaq=50)
    con = sqlite3.connect(db)
    con.execute("DELETE FROM supply_demand")
    con.commit()
    con.close()
    assert _run(repo).returncode == 1
    assert not (repo / "reports" / "None").exists()


# ── cron_run.sh ──────────────────────────────────────────────────────────

def _cron_run(repo: Path, tmp_path: Path, *cmd: str):
    log = tmp_path / "alert.log"
    stub = tmp_path / "alert.sh"
    stub.write_text(f'#!/bin/sh\necho "$1|$2" >> "{log}"\ncat >> "{log}"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    p = subprocess.run([str(repo / "scripts" / "cron_run.sh"), "rpt", "--", *cmd],
                       capture_output=True, text=True, timeout=30,
                       env={**os.environ, "SECTOR_FLOW_ALERT": str(stub)})
    return p, (log.read_text() if log.exists() else "")


def test_cron_run_is_silent_on_success(repo, tmp_path):
    p, alert = _cron_run(repo, tmp_path, "sh", "-c", "echo 잘 됨")
    assert p.returncode == 0 and "잘 됨" in p.stdout and alert == ""


def test_cron_run_alerts_with_tail_and_keeps_exit_code(repo, tmp_path):
    p, alert = _cron_run(repo, tmp_path, "sh", "-c", "echo 터졌다 >&2; exit 3")
    assert p.returncode == 3
    assert alert.startswith("error|rpt 실패 (rc=3)")
    assert "터졌다" in alert


def test_cron_run_marks_incomplete_as_warning(repo, tmp_path):
    p, alert = _cron_run(repo, tmp_path, "sh", "-c", "exit 75")
    assert p.returncode == 75
    assert alert.startswith("warn|")


def test_cron_run_names_a_timeout(repo, tmp_path):
    """크론 줄의 timeout 이 끊은 것(124)을 '실패' 로만 부르면 어디를 볼지 모른다."""
    p, alert = _cron_run(repo, tmp_path, "sh", "-c", "exit 124")
    assert p.returncode == 124
    assert alert.startswith("error|") and "시간 초과" in alert


def test_cron_run_survives_missing_alert_channel(repo, tmp_path):
    p = subprocess.run([str(repo / "scripts" / "cron_run.sh"), "rpt", "--", "sh", "-c", "exit 4"],
                       capture_output=True, text=True, timeout=30,
                       env={**os.environ, "SECTOR_FLOW_ALERT": str(tmp_path / "없음.sh")})
    assert p.returncode == 4
    assert "알림 통로 없음" in p.stdout


# ── 크론 정본 ─────────────────────────────────────────────────────────────

def _cron_lines() -> list[str]:
    text = (REPO / "deploy" / "crontab.simnode").read_text(encoding="utf-8")
    return [x for x in text.splitlines() if x.strip() and not x.startswith("#")]


def test_every_report_cron_line_makes_the_log_dir_and_wraps():
    """새 clone 엔 ``reports/`` 가 없다 — ``>> reports/cron.log`` 가 스크립트보다 먼저 터진다."""
    lines = _cron_lines()
    assert lines
    for ln in lines:
        assert re.search(r"mkdir -p \S+/reports && ", ln), ln
        assert "scripts/cron_run.sh" in ln, ln
        assert any(f"scripts/{x}" in ln for x in ("daily_report.sh", "serve.sh", "freshness_check.sh")), ln


def test_report_cron_lines_are_time_bounded():
    """DB 가 걸려 멈춘 배치가 다음 크론과 겹치고 알림도 없이 매달린다 — 크론 줄이 상한을 건다."""
    for ln in _cron_lines():
        if "daily_report.sh" in ln or "freshness_check.sh" in ln:
            assert re.search(r"cron_run\.sh \S+ -- timeout (-k \S+ )?\d+m \S+/scripts/", ln), ln


def test_freshness_check_runs_on_weekday_evenings_through_cron_run():
    """제가 안 돈 날은 아무도 안 알린다 — 결과(latest)를 보는 점검이 평일 저녁에 따로 돈다."""
    fresh = [ln for ln in _cron_lines() if "scripts/freshness_check.sh" in ln]
    assert len(fresh) == 1 and fresh[0].split()[:5] == ["30", "20", "*", "*", "1-5"], fresh
    assert "scripts/cron_run.sh" in fresh[0]


def test_web_service_is_kept_alive_by_cron():
    """웹 서비스는 크론이 5분마다 살린다 — 줄이 빠지면 재부팅·배포 뒤 아무도 안 띄운다."""
    web = [ln for ln in _cron_lines() if "scripts/serve.sh" in ln]
    assert len(web) == 1 and web[0].split()[:5] == ["*/5", "*", "*", "*", "*"], web
    assert ">> /home/young/git/sector-flow/reports/serve.log" in web[0]


def test_cron_retries_after_the_longest_seen_collection_and_before_next_day():
    """18:47 까지 간 수집을 놓치지 않고, 기준일이 넘어가기 전(다음날 아침)에도 한 번 더 본다."""
    sched = [tuple(ln.split()[:5]) for ln in _cron_lines() if "daily_report.sh" in ln]
    assert ("10", "18,19", "*", "*", "1-5") in sched
    assert ("20", "8", "*", "*", "2-6") in sched


def test_past_day_incomplete_note_tells_what_to_do_next(repo):
    """포기한 뒤에도 '다음 크론이 다시 시도' 라고 적으면 아무도 안 움직인다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=1)
    _publish_previous(repo)
    _run(repo, today="2026-11-03")
    note = (repo / "reports" / TODAY / "INCOMPLETE.txt").read_text()
    assert "다음 크론이 다시" not in note
    assert "손으로 돌려라" in note


def test_todays_incomplete_note_still_promises_the_retry(repo):
    _seed(repo.parent / "db.sqlite", today_kosdaq=1)
    _publish_previous(repo)
    _run(repo)
    assert "다음 크론이 다시" in (repo / "reports" / TODAY / "INCOMPLETE.txt").read_text()


def test_crash_after_numbers_html_does_not_block_the_retry(repo):
    """처음 만드는 날 도중에 죽으면 numbers.html 만 남아 다음 재시도가 '이미 있다' 로 건너뛰었다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    prev = _publish_previous(repo)
    good = (repo / "scripts" / "sector_numbers.py").read_text()
    (repo / "scripts" / "sector_numbers.py").write_text(good + "\nsys.exit(3)\n")
    p = _run(repo)
    assert p.returncode == 3, p.stdout + p.stderr
    assert not (repo / "reports" / TODAY).exists()
    assert not list((repo / "reports").glob("*.building.*")), "짓다 만 폴더가 남았다"
    assert (repo / "reports" / "latest").resolve() == prev.resolve()

    (repo / "scripts" / "sector_numbers.py").write_text(good)
    p = _run(repo)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (repo / "reports" / "latest").resolve() == (repo / "reports" / TODAY).resolve()


def test_verify_failure_is_visible_in_the_html_and_the_log(repo):
    """브라우저로만 보는 사람은 화면 제목의 ⚠ 를 못 본다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    (repo / "scripts" / "sector_numbers.py").write_text(
        FAKE_NUMBERS.replace('"<html>"', '"<header>"'))
    p = _run(repo, FAKE_VERIFY_RC="1")
    assert p.returncode == 1
    html = (repo / "reports" / TODAY / "numbers.html").read_text()
    assert "VERIFY_FAILED.txt" in html and html.endswith("<header>")
    assert "게시했지만 검증에 걸렸다" in p.stdout
    assert "완료" not in p.stdout


def test_db_failure_explains_itself_and_hides_the_password(repo):
    """stderr 를 버려 `set -e` 로 죽으면 제목만 있고 본문이 빈 알림이 간다."""
    (repo / ".env").write_text("KR_QUANT_DB=postgresql://u:s3cret@127.0.0.1:1/x\n")
    p = _run(repo)
    assert p.returncode == 1
    assert "DB 에서 기준일을 못 읽었다" in p.stderr
    assert "s3cret" not in p.stderr + p.stdout


def test_empty_database_is_not_called_a_connection_failure(repo):
    con = sqlite3.connect(repo.parent / "db.sqlite")
    con.execute("CREATE TABLE supply_demand(code TEXT, date TEXT)")
    con.commit()
    con.close()
    p = _run(repo)
    assert p.returncode == 1
    assert "supply_demand 가 비었다" in p.stderr
    assert not (repo / "reports" / "None").exists()


def test_no_connection_info_says_what_to_do(repo, tmp_path):
    (repo / ".env").unlink()
    p = _run(repo, HOME=str(tmp_path))
    assert p.returncode == 1
    assert "cp .env.example .env" in p.stderr


def test_missing_uv_says_what_to_do(repo):
    e = _env(repo)
    e["KR_QUANT_UV"] = "/nonexistent/uv"
    p = subprocess.run([str(repo / "scripts" / "daily_report.sh")], capture_output=True,
                       text=True, env=e, timeout=60)
    assert p.returncode == 127
    assert "uv 를 찾지 못했다" in p.stderr


def test_skip_on_a_later_day_mentions_the_collection(repo):
    """휴장과 수집 실패를 못 가르므로 갈라야 할 사람에게 사실을 준다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    _run(repo)
    p = _run(repo, today="2026-11-03")
    assert p.returncode == 0
    assert "quant-airflow 를 확인하라" in p.stdout


# ── 확정 모드(08:20) — daily_finalize 를 확인하고 전날 리포트를 확정치로 다시 만든다 ──────────

FINALIZE_TASKS = ("finalize_sector", "finalize_both", "rebuild_adjusted")


def _finalize_logs(root: Path, ok: bool = True, ended: str = "2026-11-03 07:20",
                   tasks: tuple[str, ...] = FINALIZE_TASKS) -> Path:
    import time
    run = root / "dag_id=daily_finalize" / "run_id=scheduled__2026-11-01T21:30:00+00:00"
    for task in tasks:
        d = run / f"task_id={task}"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "attempt=1.log"
        f.write_text("... Task exited with return code 0\n" if ok else "... Task exited with return code 1\n")
        t = time.mktime(time.strptime(ended, "%Y-%m-%d %H:%M"))
        os.utime(f, (t, t))
    return root


def _published_today(repo: Path) -> Path:
    dest = repo / "reports" / TODAY
    dest.mkdir(parents=True)
    (dest / "numbers.html").write_text("<provisional>")
    return dest


def test_final_mode_rebuilds_with_final_data_once_finalize_is_done(repo, tmp_path):
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    dest = _published_today(repo)
    logs = _finalize_logs(tmp_path / "airflow")
    p = _run(repo, today="2026-11-03", KR_QUANT_FINAL="1", KR_QUANT_AIRFLOW_LOGS=str(logs))
    assert p.returncode == 0, p.stdout + p.stderr
    assert (dest / "FINAL.txt").is_file()
    assert (dest / "ARGS_FINAL").is_file(), "sector_flow 에 --final 을 안 넘겼다 — 제목이 잠정으로 남는다"
    assert (dest / "numbers.html").read_text() != "<provisional>"
    # 한 번 확정한 기준일은 다시 안 만든다.
    p = _run(repo, today="2026-11-03", KR_QUANT_FINAL="1", KR_QUANT_AIRFLOW_LOGS=str(logs))
    assert p.returncode == 0 and "이미 확정치로" in p.stdout


@pytest.mark.parametrize("logs_ok", [False, None])
def test_final_mode_keeps_the_provisional_report_when_finalize_is_not_confirmed(repo, tmp_path, logs_ok):
    """잠정 리포트는 지키되 **경고로 끝난다** — 0 이면 확정이 깨진 날이 영영 잠정으로 남아도 아무도 모른다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    dest = _published_today(repo)
    logs = tmp_path / "airflow"
    if logs_ok is not None:
        _finalize_logs(logs, ok=logs_ok)
    p = _run(repo, today="2026-11-03", KR_QUANT_FINAL="1", KR_QUANT_AIRFLOW_LOGS=str(logs))
    assert p.returncode == 75, p.stdout + p.stderr
    assert (dest / "numbers.html").read_text() == "<provisional>"
    assert not (dest / "FINAL.txt").exists()
    assert "잠정 리포트를 그대로" in p.stderr
    assert "KR_QUANT_FINAL=1" in p.stderr, "손으로 고치는 법을 안 적었다"


def test_final_mode_without_a_report_builds_provisional_and_still_warns(repo, tmp_path):
    """아직 못 만든 기준일이면 잠정으로라도 만들되(마지막 기회), 확정이 아니라는 사실은 알린다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    p = _run(repo, today="2026-11-03", KR_QUANT_FINAL="1", KR_QUANT_AIRFLOW_LOGS=str(tmp_path / "없음"))
    assert p.returncode == 75, p.stdout + p.stderr
    dest = repo / "reports" / TODAY
    assert (dest / "numbers.html").is_file() and not (dest / "FINAL.txt").exists()
    assert (repo / "reports" / "latest").resolve() == dest.resolve()


def test_final_mode_waits_for_the_sector_index_task(repo, tmp_path):
    """리포트는 sector_index 도 읽는다 — finalize_sector 가 안 끝났으면 확정이 아니다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    dest = _published_today(repo)
    logs = _finalize_logs(tmp_path / "airflow", tasks=("finalize_both", "rebuild_adjusted"))
    p = _run(repo, today="2026-11-03", KR_QUANT_FINAL="1", KR_QUANT_AIRFLOW_LOGS=str(logs))
    assert p.returncode == 75, p.stdout + p.stderr
    assert not (dest / "FINAL.txt").exists()
    assert "finalize_sector" in p.stdout


def test_finalize_log_layout_change_is_named_not_called_not_yet_run(tmp_path):
    """Airflow 가 로그 배치를 바꾸면 매일 '아직 안 돌았다' 로 보여 아무도 의심하지 않는다."""
    run = tmp_path / "dag_id=daily_finalize" / "run_id=scheduled__2026-11-01T21:30:00+00:00"
    (run / "finalize_both").mkdir(parents=True)            # task_id= 접두가 없는 새 배치
    (run / "finalize_both" / "1.log").write_text("Task exited with return code 0\n")
    p = subprocess.run([sys.executable, str(REPO / "scripts" / "finalize_done.py"), "--asof", TODAY,
                        "--logs", str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert p.returncode == 1
    assert "경로 형식이 바뀌었을 수 있다" in p.stdout


def test_retrying_a_final_report_keeps_it_final(repo, tmp_path):
    """휴장일 18:10 이 08:20 의 검증 실패 확정 리포트를 다시 만들며 잠정으로 내렸다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    _published_today(repo)
    logs = _finalize_logs(tmp_path / "airflow")
    p = _run(repo, today="2026-11-03", KR_QUANT_FINAL="1", KR_QUANT_AIRFLOW_LOGS=str(logs), FAKE_VERIFY_RC="1")
    dest = repo / "reports" / TODAY
    assert p.returncode == 1 and (dest / "VERIFY_FAILED.txt").is_file() and (dest / "FINAL.txt").is_file()
    p = _run(repo, today="2026-11-03")                      # 확정 모드가 아닌 재시도
    assert p.returncode == 0, p.stdout + p.stderr
    assert not (dest / "VERIFY_FAILED.txt").exists()
    assert (dest / "FINAL.txt").is_file(), "재시도가 확정 리포트를 잠정으로 내렸다"
    assert (dest / "ARGS_FINAL").is_file(), "재시도가 --final 을 안 넘겼다"


def test_overlapping_run_is_refused_with_a_warning(repo):
    """손으로 돌린 실행과 크론이 겹치면 같은 기준일을 둘이 바꿔 끼운다 — 뒤의 것은 경고로 물러난다."""
    import fcntl
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    (repo / "reports").mkdir()
    with open(repo / "reports" / ".report.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        p = _run(repo)
    assert p.returncode == 75, p.stdout + p.stderr
    assert "다른 리포트 실행이 돌고 있다" in p.stderr
    assert not (repo / "reports" / TODAY).exists()
    assert _run(repo).returncode == 0                       # 잠금이 풀리면 정상으로 돈다


def test_finalize_that_ran_before_the_report_day_does_not_count(repo, tmp_path):
    """기준일 전에 끝난 확정 작업은 그 기준일을 덮지 않았다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    dest = _published_today(repo)
    logs = _finalize_logs(tmp_path / "airflow", ended="2026-11-02 07:20")
    p = _run(repo, today="2026-11-03", KR_QUANT_FINAL="1", KR_QUANT_AIRFLOW_LOGS=str(logs))
    assert p.returncode == 75
    assert not (dest / "FINAL.txt").exists()
