"""``daily_report.sh``·``cron_run.sh``·크론 정본 — 수집 미완인 날의 흐름 전체.

스크립트를 임시 레포에 복사하고 ``uv`` 와 무거운 세 스크립트를 가짜로 바꿔, 진짜 관문을 sqlite 로
태운다. 확인하는 것은 제어 흐름이다: 미완이면 안 쓰고 ``latest`` 를 지키며, **다음 실행이 그 기준일을
다시 만든다**(2026-10-27 은 다음 실행의 기준일이 달라 영영 안 만들어졌다).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
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
    pathlib.Path(a[a.index("--json") + 1]).with_name("final_diff.txt").write_text("잠정 → 확정 비교")
if "--asof" in a:
    pathlib.Path(a[a.index("--json") + 1]).with_name("ARGS_ASOF").write_text(
        a[a.index("--asof") + 1] + (" rebuilt" if "--rebuilt" in a else "")
        + (" unverified" if "--finalize-unverified" in a else ""))
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
import os, pathlib, sys
a = sys.argv
if "--layers" in a and a[a.index("--layers") + 1] == "X":      # 다이제스트 검증 — 배선만 본다(진짜는 test_digest·계약 검사)
    pathlib.Path(a[a.index("--dir") + 1], "VERIFY_X_CALLED").write_text("x")
    sys.exit(int(os.environ.get("FAKE_VERIFY_X_RC", "0")))
if "--asof" in a:
    pathlib.Path(a[a.index("--dir") + 1], "VERIFY_ASOF").write_text(a[a.index("--asof") + 1])
sys.exit(int(os.environ.get("FAKE_VERIFY_RC", "0")))
"""
#: 짓는 폴더 안에 쓰는 가짜 검증 페이지 — 진짜는 test_trust_page 가 보고, 여기서는 배선만 본다.
FAKE_TRUST = """
import os, pathlib, sys
a = sys.argv
if os.environ.get("FAKE_TRUST_RC"):
    sys.exit(int(os.environ["FAKE_TRUST_RC"]))
b = pathlib.Path(a[a.index("--build") + 1])
marks = sorted(p.name for p in b.iterdir())
(b / "trust.html").write_text(a[a.index("--asof") + 1] + " " + b.name + " " + " ".join(marks))
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


#: 수급 지도 생성기 대역 — 페이로드를 읽고 map.html 을 쓴다(진짜는 ``tests/test_map_page.py`` 가 본다).
FAKE_SECTOR_MAP = """
import json, pathlib, sys
a = sys.argv
json.loads(pathlib.Path(a[a.index("--payload") + 1]).read_text())
pathlib.Path(a[a.index("--html") + 1]).write_text("<html>map</html>")
"""


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "sector-flow"
    (r / "scripts").mkdir(parents=True)
    for name in ("daily_report.sh", "_env.sh", "_ledger.sh", "cron_run.sh", "completeness_gate.py",
                 "finalize_done.py", "freshness_check.sh", "freshness_check.py", "ops_ledger.py", "krx_calendar.py"):
        shutil.copy(REPO / "scripts" / name, r / "scripts" / name)
    (r / "scripts" / "sector_flow.py").write_text(FAKE_SECTOR_FLOW)
    (r / "scripts" / "sector_map.py").write_text(FAKE_SECTOR_MAP)
    (r / "scripts" / "sector_numbers.py").write_text(FAKE_NUMBERS)
    (r / "scripts" / "verify_report.py").write_text(FAKE_VERIFY)
    (r / "scripts" / "trust_page.py").write_text(FAKE_TRUST)
    # 다이제스트는 기본 켜짐이지만 진짜는 Playwright 가 든다 — 여기서는 끄고, 배선은 아래 검사가 따로 본다.
    (r / ".env").write_text(f"KR_QUANT_DB={tmp_path / 'db.sqlite'}\nKR_QUANT_DIGEST=0\n")
    # `uv run --quiet --extra pg [--with X] python Y` → 이 인터프리터로 Y.
    uv = tmp_path / "uv"
    uv.write_text(f'#!/bin/sh\nwhile [ "$1" != python ]; do shift; done\nshift\nexec "{sys.executable}" "$@"\n')
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
    """다음날 08:20 도 못 만들면 마지막 기회를 놓친 것이다 — 재시도 경고(75)로 묻히면 안 된다."""
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

def _cron_run(repo: Path, tmp_path: Path, *cmd: str, **env: str):
    log = tmp_path / "alert.log"
    stub = tmp_path / "alert.sh"
    stub.write_text(f'#!/bin/sh\necho "$1|$2" >> "{log}"\ncat >> "{log}"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    p = subprocess.run([str(repo / "scripts" / "cron_run.sh"), "rpt", "--", *cmd],
                       capture_output=True, text=True, timeout=30,
                       env={**os.environ, "SECTOR_FLOW_ALERT": str(stub), **env})
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


def test_cron_run_keeps_the_tail_when_tmp_is_unusable(repo, tmp_path):
    """/tmp(사용자 할당량 tmpfs)가 차면 mktemp 가 죽어 알림 본문이 비었다(`tail: cannot open ''`) — 꼬리는 디스크에 둔다."""
    (repo / "reports").mkdir(exist_ok=True)
    p, alert = _cron_run(repo, tmp_path, "sh", "-c", "echo 터졌다 >&2; exit 3", TMPDIR=str(tmp_path / "없는-tmp"))
    assert p.returncode == 3
    assert alert.startswith("error|rpt 실패 (rc=3)") and "터졌다" in alert, alert
    assert not list((repo / "reports").glob(".cron_run.*")), "임시 파일을 안 지웠다"


def test_cron_run_says_why_the_tail_is_missing(repo, tmp_path):
    """임시 파일을 어디에도 못 만들면 출력은 그대로 흘리고, 빈 본문 대신 그 사실을 알린다."""
    p, alert = _cron_run(repo, tmp_path, "sh", "-c", "echo 터졌다; exit 3", TMPDIR=str(tmp_path / "없는-tmp"))
    assert p.returncode == 3 and "터졌다" in p.stdout
    assert alert.startswith("error|rpt 실패 (rc=3)") and "임시 파일을 못 만들었" in alert, alert


def _stale_and_fresh(d: Path, prefix: str) -> tuple[Path, Path]:
    d.mkdir(parents=True, exist_ok=True)
    old, new = d / f"{prefix}old123", d / f"{prefix}new456"
    old.write_text("x")
    new.write_text("x")
    two_days_ago = time.time() - 2 * 86400
    os.utime(old, (two_days_ago, two_days_ago))
    return old, new


def test_cron_run_sweeps_tail_files_left_by_a_kill(repo, tmp_path):
    """KILL 로 끝난 크론은 trap 이 못 돈다 — 하루 넘은 꼬리 파일은 다음 실행이 지운다(돌고 있는 실행의 것은 둔다)."""
    old, new = _stale_and_fresh(repo / "reports", ".cron_run.")
    _cron_run(repo, tmp_path, "sh", "-c", "true")
    assert not old.exists() and new.exists()


def test_report_sweeps_stale_asof_err_files(repo):
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    old, new = _stale_and_fresh(repo / "reports", ".asof_err.")
    assert _run(repo).returncode == 0
    assert not old.exists() and new.exists()


def test_report_is_built_when_tmp_is_unusable(repo):
    """daily_report.sh 의 임시 파일도 디스크에 — /tmp 가 차도 리포트가 나온다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    p = _run(repo, TMPDIR=str(repo.parent / "없는-tmp"))
    assert p.returncode == 0, p.stdout + p.stderr
    assert (repo / "reports" / TODAY / "numbers.html").is_file()
    assert not list((repo / "reports").glob(".asof_err.*")), "임시 파일을 안 지웠다"


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


def test_no_cron_line_calls_naver_before_approval():
    """네이버 약관을 확인하지 못했다 — 크론이 켜면 main 푸시만으로 다음날부터 외부 호출이 돈다. 켜는 건 사용자 승인 뒤다."""
    lines = _cron_lines()
    assert len([ln for ln in lines if "scripts/daily_report.sh" in ln]) >= 3
    for ln in lines:
        assert "KR_QUANT_KRX_TRUTH" not in ln, ln


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


def test_db_failure_shows_the_exception_not_just_uv_hints(repo):
    """uv 가 예외 뒤에 힌트 줄을 붙이면 꼬리 3줄에는 힌트만 남았다(모의 실행) — 알림 본문이 원인을 못 말한다."""
    (repo.parent / "uv").write_text(
        "#!/bin/sh\ncat >/dev/null\n"
        "echo 'Traceback (most recent call last):' >&2\n"
        "echo 'psycopg2.OperationalError: connection to postgresql://u:s3cret@10.0.0.1/x failed' >&2\n"
        "for i in 1 2 3 4; do echo \"hint: uv 힌트 $i\" >&2; done\nexit 1\n")
    p = _run(repo)
    assert p.returncode == 1
    assert "OperationalError" in p.stderr, p.stderr
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


def kst_epoch(when: str) -> float:
    """``YYYY-MM-DD HH:MM`` **KST** → epoch. 이 머신의 시간대와 무관하다 — ``time.mktime`` 은 로컬 시간대로 읽어
    CI(UTC)에서 확정 실행의 날이 하루 밀렸다(2026-11-08 CI: 10-30 이 창 밖이 됐다)."""
    from datetime import datetime, timedelta, timezone
    return datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=timezone(timedelta(hours=9))).timestamp()


def write_attempt(f: Path, ended_kst: str, ok: bool = True) -> None:
    """Airflow 시도 기록 — 첫 줄은 Airflow 처럼 **UTC** 타임스탬프(finalize_done 이 그 날로 창을 센다), 파일 시각은 끝난 때."""
    from datetime import datetime, timezone
    t = kst_epoch(ended_kst)
    stamp = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    # 완료 줄은 실제 기록과 같은 모양 — finalize_done 이 fail= 을 읽는다(rebuild_adjusted 는 안 읽는다).
    f.write_text(f"[{stamp}.000+0000] {{local_task_job_runner.py:123}} INFO - ::group::Pre task\n"
                 f"[{stamp}.100+0000] {{logging_mixin.py:190}} INFO - ✅ 완료: done=2631 skip=0 fail=1 | 일봉 10,522행\n"
                 f"... Task exited with return code {0 if ok else 1}\n")
    os.utime(f, (t, t))


def _finalize_logs(root: Path, ok: bool = True, ended: str = "2026-11-03 07:20",
                   tasks: tuple[str, ...] = FINALIZE_TASKS) -> Path:
    """``ended`` 는 KST — 07:20 KST 는 UTC 로 전날이고 수집기 창은 그 UTC 날에서 센다."""
    run = root / "dag_id=daily_finalize" / "run_id=scheduled__2026-11-01T21:30:00+00:00"
    for task in tasks:
        d = run / f"task_id={task}"
        d.mkdir(parents=True, exist_ok=True)
        write_attempt(d / "attempt=1.log", ended, ok)
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
    assert "잠정 → 확정 비교" in (dest / "FINAL.txt").read_text(), "확정에서 바뀐 값을 FINAL.txt 에 안 붙였다"
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
    """휴장일 18:10 이 08:20 의 검증 실패 확정 리포트를 다시 만들며 잠정으로 내렸다.

    잠정본도 검증에 걸린 날이다(2026-11-10) — 통과한 잠정본이면 걸린 확정 판은 게시되지 않는다(아래 keeps_a_verified 검사)."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    (_published_today(repo) / "VERIFY_FAILED.txt").write_text("[FAIL] 잠정도 걸렸다")
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


# ── 재구성(KR_QUANT_ASOF) — 지난 기준일을 지금 코드·확정 데이터로 ───────────────────────────────

def test_rebuild_builds_the_past_day_as_final_and_marks_it(repo, tmp_path):
    """지난 기준일을 그날 이하 데이터로(--asof), 확정 의미로 만들고 REBUILT.txt 를 남긴다. 원본은 지우지 않고 latest 는 안 돌린다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    assert _run(repo).returncode == 0                       # 오늘(11-02) 것이 latest
    old = repo / "reports" / DAYS[-2]
    old.mkdir()
    (old / "numbers.html").write_text("<옛 정의>")
    logs = str(_finalize_logs(tmp_path / "airflow"))
    p = _run(repo, KR_QUANT_ASOF=DAYS[-2], KR_QUANT_CODE_REV="abc1234", KR_QUANT_AIRFLOW_LOGS=logs)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (old / "ARGS_ASOF").read_text() == f"{DAYS[-2]} rebuilt", "sector_flow 에 --asof·--rebuilt 를 안 넘겼다"
    assert (old / "FINAL.txt").is_file() and "재구성" in (old / "FINAL.txt").read_text()
    note = (old / "REBUILT.txt").read_text()
    assert "abc1234" in note and "원본은 " in note and ".archive" in note
    kept = list((repo / "reports" / ".archive").glob(f"{DAYS[-2]}.*"))
    assert len(kept) == 1 and (kept[0] / "numbers.html").read_text() == "<옛 정의>", "원본을 지웠다"
    assert (repo / "reports" / "latest").resolve() == (repo / "reports" / TODAY).resolve(), "latest 를 뒤로 돌렸다"
    # 다시 돌리면 건너뛰고(이어서 돌기), 휴장일은 3 으로 거절한다.
    again = _run(repo, KR_QUANT_ASOF=DAYS[-2], KR_QUANT_AIRFLOW_LOGS=logs)
    assert again.returncode == 0 and "이미 재구성" in again.stdout
    hol = _run(repo, KR_QUANT_ASOF="2026-10-31")            # 토요일
    assert hol.returncode == 3 and "거래일이 아니다" in hol.stderr
    assert not (repo / "reports" / "2026-10-31").exists()


def test_rebuild_gate_judges_the_asof_day_not_the_latest(repo):
    """완전성 관문은 재구성 기준일을 본다 — 최신일이 멀쩡해도 그날이 부분 수집이면 안 만든다."""
    db = repo.parent / "db.sqlite"
    _seed(db, today_kosdaq=50)
    con = sqlite3.connect(db)
    con.execute("DELETE FROM supply_demand WHERE date=? AND code >= '000060'", (DAYS[-2],))
    con.commit()
    con.close()
    p = _run(repo, KR_QUANT_ASOF=DAYS[-2])
    assert p.returncode == 1, p.stdout + p.stderr
    assert (repo / "reports" / DAYS[-2] / "INCOMPLETE.txt").is_file()
    assert not (repo / "reports" / DAYS[-2] / "numbers.html").exists()


def test_rebuild_without_confirmed_finalize_stays_provisional(repo, tmp_path):
    """확정치를 확인 못 한 날을 재구성하면 잠정이다 — FINAL.txt 를 쓰면 08:20 확정 모드가 그날을 영영 건너뛴다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    p = _run(repo, KR_QUANT_ASOF=TODAY, KR_QUANT_AIRFLOW_LOGS=str(tmp_path / "없음"))
    assert p.returncode == 75, p.stdout + p.stderr
    dest = repo / "reports" / TODAY
    assert (dest / "REBUILT.txt").is_file() and "잠정" in (dest / "REBUILT.txt").read_text()
    assert not (dest / "FINAL.txt").exists(), "확인 안 된 확정을 적었다"
    assert not (dest / "ARGS_FINAL").exists(), "sector_flow 에 --final 을 넘겼다"
    assert "KR_QUANT_ASOF" in p.stderr
    assert "finalize: pending" in (dest / "REBUILT.txt").read_text().splitlines()
    # 확정이 끝난 뒤 다시 돌리면 건너뛰지 않고 확정으로 다시 만든다.
    p = _run(repo, KR_QUANT_ASOF=TODAY, KR_QUANT_AIRFLOW_LOGS=str(_finalize_logs(tmp_path / "airflow")))
    assert p.returncode == 0, p.stdout + p.stderr
    assert (dest / "FINAL.txt").is_file() and (dest / "ARGS_FINAL").is_file()


def test_rebuild_of_a_weekday_with_other_tables_but_no_flows_is_a_gap_not_a_holiday(repo):
    """수급만 비고 일봉이 있는 평일은 휴장이 아니라 수집 공백이다 — 3(휴장)으로 조용히 넘기지 않는다."""
    db = repo.parent / "db.sqlite"
    _seed(db, today_kosdaq=50)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO daily_bars VALUES('000001','2026-11-03',100.0)")      # 화요일, 수급 없음
    con.commit()
    con.close()
    p = _run(repo, KR_QUANT_ASOF="2026-11-03")
    assert p.returncode == 1 and "수집 공백" in p.stderr, p.stdout + p.stderr
    assert _run(repo, KR_QUANT_ASOF="2026-10-31").returncode == 3                  # 토요일 = 휴장


def test_rebuild_outside_the_finalize_window_is_final_data_without_a_final_claim(repo, tmp_path):
    """daily_finalize 창(3일) 밖의 지난 날은 앞으로 아무 확정 실행도 덮지 않는다 — 기다릴 것이 없으니 0 으로 만들되,
    FINAL.txt('daily_finalize 확인 뒤 확정')를 쓰지 않고 REBUILT.txt 에 finalize: unverified 를 남긴다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    p = _run(repo, KR_QUANT_ASOF=DAYS[1], KR_QUANT_CODE_REV="f" * 40,       # 10-27 < 11-02 − 3일
             KR_QUANT_AIRFLOW_LOGS=str(tmp_path / "없음"))
    assert p.returncode == 0, p.stdout + p.stderr
    dest = repo / "reports" / DAYS[1]
    lines = (dest / "REBUILT.txt").read_text().splitlines()
    assert "finalize: unverified" in lines and f"commit: {'f' * 40}" in lines
    assert "확정 기록 없음" in lines[0]
    assert not (dest / "FINAL.txt").exists(), "확인 못 한 확정을 FINAL.txt 로 적었다"
    assert (dest / "ARGS_ASOF").read_text() == f"{DAYS[1]} rebuilt unverified"
    assert not (dest / "ARGS_FINAL").exists()
    again = _run(repo, KR_QUANT_ASOF=DAYS[1], KR_QUANT_AIRFLOW_LOGS=str(tmp_path / "없음"))
    assert again.returncode == 0 and "이미 재구성" in again.stdout, "창 밖 날을 매번 다시 지었다"


def test_rebuild_records_verified_finalize_and_the_full_commit(repo, tmp_path):
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    p = _run(repo, KR_QUANT_ASOF=TODAY, KR_QUANT_AIRFLOW_LOGS=str(_finalize_logs(tmp_path / "airflow")))
    assert p.returncode == 0, p.stdout + p.stderr
    lines = (repo / "reports" / TODAY / "REBUILT.txt").read_text().splitlines()
    assert "finalize: verified" in lines
    rev = [ln for ln in lines if ln.startswith("commit: ")][0].split(": ", 1)[1]
    assert rev == "?" or len(rev) >= 40, f"짧은 sha: {rev}"            # 시험 레포는 git 이 아니라 '?'


# ─────────────────────────────── 부속 산출물 — 검증 페이지·다이제스트의 배선

def _digest_on(repo: Path, tmp_path: Path, body: str = None) -> None:
    (repo / ".env").write_text(f"KR_QUANT_DB={tmp_path / 'db.sqlite'}\n")
    (repo / "scripts" / "digest.py").write_text(body or """
import pathlib, sys
b = pathlib.Path(sys.argv[sys.argv.index("--dir") + 1])
(b / "digest.md").write_text("글")
(b / "digest.png").write_bytes(b"png")
""")


def test_trust_page_is_built_inside_the_build_folder_and_swapped_with_the_report(repo):
    """배치가 부른다 — 짓는 폴더 안에서, 표시 파일을 다 쓴 뒤에. 강제 재생성 뒤에도 페이지가 있다(예전엔 사라져 404)."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    r = _run(repo)
    assert r.returncode == 0, r.stderr
    page = (repo / "reports" / TODAY / "trust.html")
    assert (repo / "reports" / "latest" / "trust.html").exists()
    said = page.read_text().split()
    assert said[0] == TODAY and ".building." in said[1], "게시된 폴더에 고쳐 쓰면 안 된다"
    assert "VERIFY.txt" in said and "numbers.html" in said
    r = _run(repo, KR_QUANT_FORCE="1")
    assert r.returncode == 0 and (repo / "reports" / "latest" / "trust.html").exists()


def test_rebuild_writes_the_page_after_its_markers(repo, tmp_path):
    """재구성은 REBUILT.txt·FINAL.txt 를 쓴 뒤에 페이지를 만든다 — 먼저 부르면 그날 줄이 '잠정' 으로 찍힌다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    r = _run(repo, KR_QUANT_ASOF=DAYS[-2], KR_QUANT_AIRFLOW_LOGS=_finalize_logs(tmp_path / "logs"))
    assert r.returncode == 0, r.stderr
    said = (repo / "reports" / DAYS[-2] / "trust.html").read_text().split()
    assert "REBUILT.txt" in said


def test_trust_page_failure_publishes_warns_and_is_recorded_in_verify(repo, tmp_path):
    """페이지를 못 만들어도 리포트는 게시한다. 다만 조용히 빠지지 않는다 — 75(경고), VERIFY.txt 끝줄, 검증 실패 아님."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    r = _run(repo, FAKE_TRUST_RC="1")
    assert r.returncode == 75, r.stderr
    assert "검증·품질 페이지를 못 만들었다" in r.stderr and "부속 산출물을 못 만들었다(trust.html)" in r.stderr
    dest = repo / "reports" / TODAY
    assert (dest / "numbers.html").exists() and (repo / "reports" / "latest").resolve() == dest
    assert "부속 산출물 실패: trust.html" in (dest / "VERIFY.txt").read_text()
    assert not (dest / "VERIFY_FAILED.txt").exists(), "리포트 숫자의 실패가 아니다"
    assert not (dest / "trust.html").exists()


def test_digest_is_on_by_default_and_lands_in_the_build(repo, tmp_path):
    """크론 줄에 스위치가 없어도 만든다 — 예전엔 켜는 곳이 없어 웹의 /latest/digest.md 가 늘 404 였다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    _digest_on(repo, tmp_path)
    r = _run(repo)
    assert r.returncode == 0, r.stderr
    assert (repo / "reports" / "latest" / "digest.md").read_text() == "글"
    assert (repo / "reports" / "latest" / "digest.png").exists()


def test_digest_failure_and_missing_card_warn_without_blocking(repo, tmp_path):
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    _digest_on(repo, tmp_path, "import sys\nsys.exit(1)\n")
    r = _run(repo)
    assert r.returncode == 75 and "다이제스트를 못 만들었다" in r.stderr
    dest = repo / "reports" / TODAY
    assert (dest / "numbers.html").exists() and "부속 산출물 실패: digest" in (dest / "VERIFY.txt").read_text()
    # 글만 되고 카드가 없는 날 — 웹의 digest.png 가 404 인데 종료코드는 0 이던 틈.
    _digest_on(repo, tmp_path, """
import pathlib, sys
(pathlib.Path(sys.argv[sys.argv.index("--dir") + 1]) / "digest.md").write_text("글")
""")
    r = _run(repo, KR_QUANT_FORCE="1")
    assert r.returncode == 75 and "카드(digest.png)를 못 찍었다" in r.stderr


def test_digest_is_checked_by_the_x_layer_and_withheld_when_it_disagrees(repo, tmp_path):
    """다이제스트는 검증 뒤에 지어진다 — 배치가 지은 뒤 X 층(``--layers X``)을 부르고, 걸리면 글·카드를 빼고 게시한다(75).
    2026-11-18 글은 화면 목록에 없는 '95' 섹터를 실었는데 VERIFY.txt 는 전부 통과였다(P2)."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    _digest_on(repo, tmp_path)
    r = _run(repo)
    assert r.returncode == 0, r.stderr
    dest = repo / "reports" / TODAY
    assert (dest / "VERIFY_X_CALLED").exists() and (dest / "digest.md").exists() and (dest / "VERIFY_DIGEST.txt").exists()
    r = _run(repo, KR_QUANT_FORCE="1", FAKE_VERIFY_X_RC="1")
    assert r.returncode == 75 and "다른 말을 한다(X 층)" in r.stderr, r.stderr
    assert not (dest / "digest.md").exists() and not (dest / "digest.png").exists(), "검증에 걸린 글이 게시됐다"
    assert (dest / "numbers.html").exists() and "부속 산출물 실패: digest-verify" in (dest / "VERIFY.txt").read_text()
    assert not (dest / "VERIFY_FAILED.txt").exists(), "리포트 숫자의 실패가 아니다"


def test_digest_can_be_turned_off(repo, tmp_path):
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    _digest_on(repo, tmp_path, "raise SystemExit('부르면 안 된다')\n")
    r = _run(repo, KR_QUANT_DIGEST="0")
    assert r.returncode == 0, r.stderr
    assert not (repo / "reports" / TODAY / "digest.md").exists()


def test_cron_timeout_during_a_side_product_still_records_and_cleans(repo):
    """부속 산출물(다이제스트·검증 페이지) 단계에서 크론 40분 상한이 걸리면 — 안쪽 ``timeout`` 은 자기 프로세스 그룹이라 바깥 TERM 이 안 간다.
    셸은 자식을 기다리느라 트랩을 못 돌리고 KILL 로 죽어 rc 137, 원장에 report 줄 없음, ``.building.N`` 잔류였다(재현 2026-11-09)."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    pidfile = repo.parent / "sleeper.pid"
    (repo / "scripts" / "trust_page.py").write_text(
        f"import os, time\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\ntime.sleep(25)\n")
    t0 = time.monotonic()
    p = subprocess.run(["timeout", "-k", "4s", "3s", str(repo / "scripts" / "daily_report.sh")],
                       capture_output=True, text=True, env=_env(repo), timeout=45)
    assert time.monotonic() - t0 < 20, "부속 단계 프로세스가 끝까지 살아 파이프를 쥐었다"
    assert p.returncode == 124, f"KILL 까지 갔다(137)면 트랩이 안 돌았다: {p.returncode}\n{p.stderr}"
    events = [json.loads(ln) for ln in (repo / "reports" / "events.jsonl").read_text().splitlines()]
    report = [e for e in events if e["event"] == "report"]
    assert report and report[-1]["rc"] == 143 and report[-1]["outcome"] == "error", events
    assert not list((repo / "reports").glob("*.building.*")), "짓다 만 폴더가 남았다"
    time.sleep(0.3)
    pid = int(pidfile.read_text())
    assert not Path(f"/proc/{pid}").exists() or "Z" in Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0], "부속 단계 프로세스가 살아 남았다"


# ── 검증에 걸린 새 판은 통과한 게시본을 밀어내지 못한다 ───────────────────────────────

def _rejected(repo: Path) -> list[Path]:
    return sorted((repo / "reports" / ".rejected").glob(f"{TODAY}.*"))


def test_failed_rebuild_keeps_the_verified_report_published(repo):
    """강제 재생성이 검증에 걸렸다 — 예전엔 통과본을 rm -rf 하고 걸린 판을 latest 로 걸었다(되돌릴 길 없음)."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    assert _run(repo).returncode == 0
    dest = repo / "reports" / TODAY
    (dest / "numbers.html").write_text("<통과본>")
    p = _run(repo, KR_QUANT_FORCE="1", FAKE_VERIFY_RC="1")
    assert p.returncode == 1, p.stdout + p.stderr
    assert "새 판이 검증에 걸려 게시하지 않았다" in p.stderr
    assert (dest / "numbers.html").read_text() == "<통과본>", "통과본을 걸린 판으로 바꿔 꼈다"
    assert not (dest / "VERIFY_FAILED.txt").exists()
    assert (repo / "reports" / "latest").resolve() == dest.resolve()
    kept = _rejected(repo)
    assert len(kept) == 1 and (kept[0] / "VERIFY_FAILED.txt").is_file(), "걸린 판을 진단용으로 남기지 않았다"
    assert not list((repo / "reports").glob("*.building.*")) and not list((repo / "reports").glob("*.replaced.*"))
    # 걸린 판은 웹·목록에 안 보인다 — 날짜 폴더 이름이 아니다.
    assert not any(c.name.startswith(TODAY + ".") for c in (repo / "reports").iterdir())
    # 고친 뒤 다시 돌리면 바꿔 낀다.
    assert _run(repo, KR_QUANT_FORCE="1").returncode == 0
    assert (dest / "numbers.html").read_text() == "<html>"


def test_failed_final_build_keeps_the_verified_provisional_and_its_markers(repo, tmp_path):
    """08:20 확정 판이 걸리면 통과한 잠정본이 그대로 게시되고 FINAL.txt 는 걸린 판(.rejected) 안에만 있다 — 표시 파일은 그 폴더의 판을 말한다.
    그래서 10:20 이 확정을 다시 해 보고, 통과하면 그때 확정본이 된다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    dest = _published_today(repo)
    logs = str(_finalize_logs(tmp_path / "airflow"))
    p = _run(repo, today="2026-11-03", KR_QUANT_FINAL="1", KR_QUANT_AIRFLOW_LOGS=logs, FAKE_VERIFY_RC="1")
    assert p.returncode == 1, p.stdout + p.stderr
    assert (dest / "numbers.html").read_text() == "<provisional>"
    assert not (dest / "FINAL.txt").exists(), "게시본은 잠정인데 확정이라 적었다"
    kept = _rejected(repo)
    assert len(kept) == 1 and (kept[0] / "FINAL.txt").is_file() and (kept[0] / "VERIFY_FAILED.txt").is_file()
    p = _run(repo, today="2026-11-03", KR_QUANT_FINAL="1", KR_QUANT_AIRFLOW_LOGS=logs)     # 10:20 재시도
    assert p.returncode == 0, p.stdout + p.stderr
    assert (dest / "FINAL.txt").is_file() and not (dest / "VERIFY_FAILED.txt").exists()


def test_failed_rebuild_of_a_verified_past_day_keeps_the_original(repo, tmp_path):
    """재구성도 같다 — 원본은 .archive 로 가지 않고 제자리에 남는다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    old = repo / "reports" / DAYS[-2]
    old.mkdir(parents=True)
    (old / "numbers.html").write_text("<원본>")
    p = _run(repo, KR_QUANT_ASOF=DAYS[-2], KR_QUANT_AIRFLOW_LOGS=str(_finalize_logs(tmp_path / "airflow")),
             FAKE_VERIFY_RC="1")
    assert p.returncode == 1, p.stdout + p.stderr
    assert (old / "numbers.html").read_text() == "<원본>" and not (old / "REBUILT.txt").exists()
    assert not (repo / "reports" / ".archive").exists()
    assert list((repo / "reports" / ".rejected").glob(f"{DAYS[-2]}.*"))


# ── 읽기는 언제나 관문이 본 기준일 이하 ────────────────────────────────────────────

def test_normal_build_is_bound_to_the_gated_asof(repo):
    """잠정 빌드도 sector_flow·검증에 --asof 를 준다 — 안 주면 관문과 빌드 사이에 들어온 다음 날 부분 수집이 이 폴더에 실린다.
    재구성 표시(--rebuilt)는 붙이지 않는다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    assert _run(repo).returncode == 0
    dest = repo / "reports" / TODAY
    assert (dest / "ARGS_ASOF").read_text() == TODAY, "sector_flow 가 기준일을 다시 고른다(또는 재구성으로 표시했다)"
    assert (dest / "VERIFY_ASOF").read_text() == TODAY, "검증이 DB 최신일을 기준일로 본다"
    assert not (dest / "REBUILT.txt").exists()


# ── 게시 뒤 수집이 더 들어왔으면 그날 저녁 재시도가 다시 만든다 ─────────────────────────

def test_evening_retry_rebuilds_when_collection_grew_after_a_partial_pass(repo):
    """18:10 에 98% 로 통과한 리포트를 19:10 이 '이미 있다' 며 건너뛰어 빠진 종목이 그날 저녁 내내 latest 에 남았다."""
    db = repo.parent / "db.sqlite"
    _seed(db, today_kosdaq=49)                           # 코스닥 49/50 = 98% — 관문 통과
    assert _run(repo).returncode == 0
    dest = repo / "reports" / TODAY
    gate = (dest / "GATE.txt").read_text()
    assert "수집 행 수" in gate and "[PASS]" in gate, "관문이 본 것을 판과 함께 남기지 않았다"
    payload = dest / "payload.json"
    before = payload.stat().st_mtime_ns
    p = _run(repo)                                       # 그대로면 건너뛴다
    assert p.returncode == 0 and "건너뛴다" in p.stdout and payload.stat().st_mtime_ns == before
    _seed(db, today_kosdaq=50)                           # 나머지 수집이 들어왔다
    p = _run(repo)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "수집이 더 들어왔다" in p.stdout and "건너뛴다" not in p.stdout
    assert "수급 100" in (dest / "GATE.txt").read_text()
    # 기준일이 오늘이 아니면(휴장일·다음날) 이 재시도는 없다 — 그날은 08:20 확정이 다시 만든다.
    _seed(db, today_kosdaq=49)
    p = _run(repo, today="2026-11-03")
    assert p.returncode == 0 and "건너뛴다" in p.stdout


# ── 끊긴 실행이 남긴 폴더 ─────────────────────────────────────────────────────

def test_stale_build_and_replaced_folders_are_swept_under_the_lock(repo):
    """KILL·OOM 이 남긴 <기준일>.building.N 이 리포트 한 벌씩 쌓였다. 바꿔 끼우기 도중 끊긴 .replaced.N 은 제자리가 비었으면 되돌린다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    root = repo / "reports"
    root.mkdir()
    old = time.time() - 2 * 86400
    stale_build = root / f"{TODAY}.building.999999"
    fresh_build = root / f"{TODAY}.building.999998"
    orphan = root / f"{DAYS[-3]}.replaced.999997"            # 제자리(DAYS[-3])가 없다 — 유일한 사본
    dup = root / f"{DAYS[-4]}.replaced.999996"               # 제자리가 있다 — 지워도 된다
    (root / DAYS[-4]).mkdir()
    for d in (stale_build, fresh_build, orphan, dup):
        d.mkdir()
        (d / "numbers.html").write_text(d.name)
    for d in (stale_build, orphan, dup):
        os.utime(d, (old, old))
    assert _run(repo).returncode == 0
    assert not stale_build.exists() and not dup.exists(), "끊긴 실행의 폴더를 안 치웠다"
    assert fresh_build.exists(), "하루가 안 된 폴더까지 지웠다"
    assert not orphan.exists() and (root / DAYS[-3] / "numbers.html").read_text() == orphan.name, "유일한 사본을 지웠다"


# ── 바꿔 끼우기는 시그널에 끊기지 않는다 ──────────────────────────────────────────────

def test_cron_timeout_between_the_two_moves_does_not_lose_the_report(repo, tmp_path):
    """크론 timeout 의 TERM 이 mv 두 번 사이에 오면 트랩이 다 지은 판을 지워 <기준일> 이 사라지고 latest 가 끊겼다.
    timeout 은 프로세스 그룹 전체에 보내 mv 자신도 죽는다 — 그룹째 TERM 을 보내 재현한다."""
    import signal
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    assert _run(repo).returncode == 0
    marker = tmp_path / "in-mv"
    shim = repo.parent / "bin"
    real_mv = shutil.which("mv")
    (shim / "mv").write_text(
        f'#!/bin/sh\ncase "$*" in *.building.*) : > "{marker}"; sleep 2 ;; esac\nexec "{real_mv}" "$@"\n')
    (shim / "mv").chmod(0o755)
    env = _env(repo, KR_QUANT_FORCE="1")
    p = subprocess.Popen([str(repo / "scripts" / "daily_report.sh")], env=env, start_new_session=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 60
    while not marker.exists():
        assert p.poll() is None and time.monotonic() < deadline, "바꿔 끼우기까지 못 갔다"
        time.sleep(0.02)
    os.killpg(p.pid, signal.SIGTERM)
    out, err = p.communicate(timeout=60)
    (shim / "mv").unlink()
    dest = repo / "reports" / TODAY
    assert (dest / "numbers.html").is_file(), f"기준일 폴더가 사라졌다\n{out}\n{err}"
    assert (repo / "reports" / "latest").resolve() == dest.resolve(), "latest 가 끊겼다"
    assert not list((repo / "reports").glob("*.building.*")) and not list((repo / "reports").glob("*.replaced.*"))
    assert p.returncode == 143, (p.returncode, err)
    report = [json.loads(ln) for ln in (repo / "reports" / "events.jsonl").read_text().splitlines()
              if '"report"' in ln][-1]
    assert report["outcome"] == "published" and report["rc"] == 143, report


def test_map_is_built_into_the_report(repo, tmp_path):
    """수급 지도는 배치가 리포트 폴더에 짓는다 — 비공개 웹이 /latest/map.html 로 잇는다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    r = _run(repo)
    assert r.returncode == 0, r.stderr
    assert (repo / "reports" / "latest" / "map.html").read_text() == "<html>map</html>"


def test_map_failure_warns_without_blocking(repo, tmp_path):
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    (repo / "scripts" / "sector_map.py").write_text("import sys\nsys.exit(1)\n")
    r = _run(repo)
    assert r.returncode == 75 and "수급 지도(map.html)를 못 만들었다" in r.stderr, r.stderr
    dest = repo / "reports" / TODAY
    assert (dest / "numbers.html").exists() and "부속 산출물 실패: map.html" in (dest / "VERIFY.txt").read_text()
    assert not (dest / "VERIFY_FAILED.txt").exists(), "리포트 숫자의 실패가 아니다"
