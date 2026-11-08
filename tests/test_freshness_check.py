"""``scripts/freshness_check.py`` — 리포트가 안 걸린 날과 알림 통로가 빈 날을 결과로 잡는다.

판정은 함수로 직접, 크론 경로(``freshness_check.sh`` → ``_env.sh`` → uv)는 가짜 uv 와 sqlite 로 한 번 태운다.
진짜 ``.env`` 에 기대지 않는다 — 임시 레포에 제 ``.env`` 를 두고 HOME 도 임시로 바꾼다.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("freshness_check", REPO / "scripts" / "freshness_check.py")
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)

TODAY = "2026-11-06"          # 금요일


def _reports(tmp_path: Path, latest: str | None, *, today_mark: str | None = None) -> Path:
    root = tmp_path / "reports"
    root.mkdir(parents=True, exist_ok=True)
    if latest:
        (root / latest).mkdir(exist_ok=True)
        (root / latest / "numbers.html").write_text("<html>")
        os.symlink(root / latest, root / "latest")
    if today_mark:
        (root / TODAY).mkdir(exist_ok=True)
        (root / TODAY / today_mark).write_text("x")
    return root


def test_data_without_todays_report_is_an_error(tmp_path):
    """수집은 끝났는데 리포트가 안 걸렸다 — daily_report.sh 가 아예 안 돈 날은 아무도 안 알린다."""
    rc, lines = fc.judge(_reports(tmp_path, "2026-11-05"), TODAY, 2500)
    assert rc == 1
    assert "2026-11-05" in lines[0] and "안 걸렸다" in lines[0]


def test_incomplete_mark_is_pointed_at(tmp_path):
    rc, lines = fc.judge(_reports(tmp_path, "2026-11-05", today_mark="INCOMPLETE.txt"), TODAY, 2500)
    assert rc == 1 and any("INCOMPLETE.txt" in x for x in lines)


def test_no_rows_on_a_weekday_is_a_warning(tmp_path):
    """휴장과 수집 실패를 못 가른다 — 오류가 아니라 경고로."""
    rc, lines = fc.judge(_reports(tmp_path, "2026-11-05"), TODAY, 0)
    assert rc == 75 and "수집이" in lines[0]


def test_todays_report_is_fine(tmp_path):
    rc, _ = fc.judge(_reports(tmp_path, TODAY), TODAY, 2500)
    assert rc == 0


@pytest.mark.parametrize("value", [None, "", "  "])
def test_empty_webhook_warns(value):
    """웹훅이 비면 alert.sh 가 로그에만 남긴다 — 모든 알림이 아무에게도 안 간다."""
    rc, lines = fc.check_webhook(value)
    assert rc == 75 and "ALERT_WEBHOOK_URL" in lines[0]


def test_set_webhook_is_silent():
    assert fc.check_webhook("https://example.invalid/hook") == (0, [])


def test_error_outranks_warning():
    assert fc.worse(1, 75) == 1 and fc.worse(75, 1) == 1 and fc.worse(0, 75) == 75


# ── 크론 경로 ──────────────────────────────────────────────────────────────

@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "sector-flow"
    (r / "scripts").mkdir(parents=True)
    for name in ("_env.sh", "freshness_check.sh", "freshness_check.py"):
        shutil.copy(REPO / "scripts" / name, r / "scripts" / name)
    db = tmp_path / "db.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE supply_demand(code TEXT, date TEXT)")
    con.executemany("INSERT INTO supply_demand VALUES(?,?)",
                    [(f"{i:06d}", d) for i in range(10) for d in ("2026-11-05", TODAY)])
    con.commit()
    con.close()
    (r / ".env").write_text(f"KR_QUANT_DB={db}\n")
    uv = tmp_path / "uv"
    uv.write_text(f'#!/bin/sh\nshift 5\nexec "{sys.executable}" "$@"\n')
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    return r


def _run(repo: Path, *args: str, **env) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items()
         if k not in ("KR_QUANT_DB", "KR_QUANT_REPORTS", "ALERT_WEBHOOK_URL")}
    e.update(KR_QUANT_UV=str(repo.parent / "uv"), HOME=str(repo.parent), **env)
    return subprocess.run([str(repo / "scripts" / "freshness_check.sh"), "--today", TODAY, *args],
                          capture_output=True, text=True, env=e, timeout=60)


def test_cron_path_flags_a_missing_report(repo):
    _reports(repo, "2026-11-05")
    p = _run(repo, ALERT_WEBHOOK_URL="https://example.invalid/hook")
    assert p.returncode == 1, p.stdout + p.stderr
    assert "안 걸렸다" in p.stdout


def test_cron_path_warns_about_the_webhook_even_when_fresh(repo):
    _reports(repo, TODAY)
    p = _run(repo)
    assert p.returncode == 75, p.stdout + p.stderr
    assert "[신선도] OK" in p.stdout and "ALERT_WEBHOOK_URL" in p.stdout


def test_cron_path_db_failure_is_an_error_and_hides_the_password(repo):
    (repo / ".env").write_text("KR_QUANT_DB=postgresql://u:s3cret@127.0.0.1:1/x\n")
    _reports(repo, TODAY)
    p = _run(repo, ALERT_WEBHOOK_URL="https://example.invalid/hook")
    assert p.returncode == 1, p.stdout + p.stderr
    assert "못 셌다" in p.stdout
    assert "s3cret" not in p.stdout + p.stderr
