"""``scripts/freshness_check.py`` — 리포트가 안 걸린 날과 알림 통로가 빈 날을 결과로 잡는다.

판정은 함수로 직접, 크론 경로(``freshness_check.sh`` → ``_env.sh`` → uv)는 가짜 uv 와 sqlite 로 한 번 태운다.
진짜 ``.env`` 에 기대지 않는다 — 임시 레포에 제 ``.env`` 를 두고 HOME 도 임시로 바꾼다.
"""
from __future__ import annotations

import http.server
import importlib.util
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("freshness_check", REPO / "scripts" / "freshness_check.py")
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)

TODAY = "2026-11-06"          # 금요일


@pytest.fixture
def hook():
    """가짜 웹훅·하트비트 — 127.0.0.1 에 띄운다. 진짜 네트워크는 안 쓴다. (주소, 받은 메서드 목록)."""
    servers = []

    def make(status: int, body: bytes):
        hits: list[str] = []

        class H(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                hits.append(self.command)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            do_GET = do_POST = _answer

            def log_message(self, *a):
                pass

        s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        servers.append(s)
        return f"http://127.0.0.1:{s.server_address[1]}/api/webhooks/1/secret-token", hits
    yield make
    for s in servers:
        s.shutdown()
        s.server_close()


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


def test_live_webhook_passes(hook):
    url, _ = hook(200, b'{"type": 1, "id": "1"}')
    rc, lines = fc.check_webhook(url)
    assert rc == 0 and "OK" in lines[0]


@pytest.mark.parametrize("status", [401, 404])
def test_dead_webhook_warns(hook, status):
    """채널에서 웹훅을 지우면 주소는 그대로 남아 '비었나' 검사를 통과한다 — 알림은 [alert.py] failed 로 사라진다."""
    url, hits = hook(status, b'{"message": "Unknown Webhook"}')
    rc, lines = fc.check_webhook(url)
    assert rc == 75 and "폐기됐거나 틀렸다" in lines[0]
    assert hits == ["GET"], "메시지를 보내면 안 된다 — GET 한 번만"
    assert url not in "\n".join(lines) and "secret-token" not in "\n".join(lines)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:9/api/webhooks/1/FAKE-tok3n-closed",            # discard 포트 — 닫혀 있다
    "discord.com/api/webhooks/1/FAKE-tok3n-noscheme",                 # 스킴 없음 — Request 가 ValueError(주소 통째)
    "https://discord.invalid/api/webhooks/1/FAKE tok3n-space",        # 공백 — InvalidURL 이 경로(토큰)를 되읊는다
], ids=["closed-port", "no-scheme", "space"])
def test_unreachable_webhook_warns_without_printing_the_url(url, capsys):
    """이 줄은 cron_run 꼬리 → alert.sh → Discord 로 나간다. 주소 꼴 오류는 str(e) 에 토큰을 싣는다 — 종류만 적는다."""
    rc, lines = fc.check_webhook(url)
    assert rc == 75 and "닿지 못했다" in lines[0]
    out = "\n".join(lines) + "".join(capsys.readouterr())
    assert "tok3n" not in out and "webhooks" not in out, out


def test_bad_url_in_heartbeat_does_not_crash_or_leak(capsys):
    lines = fc.heartbeat("hc-ping.invalid/FAKE-tok3n", ok=True)
    assert "못 보냈다" in lines[0]
    assert "tok3n" not in "\n".join(lines) + "".join(capsys.readouterr())


def test_webhook_answering_html_is_not_taken_as_discord(hook):
    url, _ = hook(200, b"<html>login</html>")
    rc, lines = fc.check_webhook(url)
    assert rc == 75 and "JSON" in lines[0]


# ── alert.sh 가 읽는 값 ────────────────────────────────────────────────────

@pytest.mark.parametrize(("line", "want"), [
    ("ALERT_WEBHOOK_URL=https://h/x", "https://h/x"),
    ('ALERT_WEBHOOK_URL="https://h/x?a=b"', "https://h/x?a=b"),
    ("ALERT_WEBHOOK_URL='https://h/x'", "https://h/x"),
    ("export ALERT_WEBHOOK_URL=https://h/x", ""),       # alert.sh 의 grep '^KEY=' 에 안 걸린다
    ("ALERT_WEBHOOK_URL = https://h/x", ""),
    ("#ALERT_WEBHOOK_URL=https://h/x", ""),
])
def test_env_file_is_read_like_alert_sh(tmp_path, line, want):
    env = tmp_path / ".env"
    env.write_text(f"KR_QUANT_DB=x\n{line}\n")
    assert fc.env_file_value(env, "ALERT_WEBHOOK_URL") == want


def test_last_line_wins_like_tail(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ALERT_WEBHOOK_URL=https://old\nALERT_WEBHOOK_URL=https://new\n")
    assert fc.env_file_value(env, "ALERT_WEBHOOK_URL") == "https://new"


def test_matches_alert_sh_env_get_byte_for_byte(tmp_path):
    """같은 .env 를 진짜 alert.sh 의 env_get 과 이 함수가 같은 값으로 읽는다(셸 함수를 그대로 옮겨 돌린다)."""
    env = tmp_path / ".env"
    env.write_text("export ALERT_WEBHOOK_URL=https://a\nALERT_WEBHOOK_URL='https://b?x=1&y=\"2\"'\nX=1\n")
    env_get = r"""grep "^$1=" "$F" 2>/dev/null | tail -n 1 | cut -d= -f2- | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//'"""
    out = subprocess.run(["bash", "-c", f"f() {{ {env_get}; }}; printf %s \"$(f ALERT_WEBHOOK_URL)\""],
                         env={"F": str(env), "PATH": os.environ["PATH"]}, capture_output=True, text=True).stdout
    assert fc.env_file_value(env, "ALERT_WEBHOOK_URL") == out


def test_cron_env_wins_even_when_empty(tmp_path):
    """alert.sh 는 `${VAR+x}` — 크론 환경에 빈 값으로 정의돼 있으면 .env 를 안 본다."""
    sh = tmp_path / "qa" / "scripts" / "alert.sh"
    sh.parent.mkdir(parents=True)
    (tmp_path / "qa" / ".env").write_text("ALERT_WEBHOOK_URL=https://from-env-file\n")
    assert fc.alert_value("ALERT_WEBHOOK_URL", {}, sh) == "https://from-env-file"
    assert fc.alert_value("ALERT_WEBHOOK_URL", {"ALERT_WEBHOOK_URL": ""}, sh) == ""
    # freshness_check.sh 를 거쳤으면 .env 가 섞인 지금 환경이 아니라 그 전 사본을 본다.
    sourced = {"SF_CRON_ENV": "1", "ALERT_WEBHOOK_URL": "https://from-sector-flow-env"}
    assert fc.alert_value("ALERT_WEBHOOK_URL", sourced, sh) == "https://from-env-file"


# ── 하트비트 ──────────────────────────────────────────────────────────────

def test_heartbeat_pings_only_a_clean_check(hook):
    url, hits = hook(200, b"OK")
    assert fc.heartbeat(url, ok=False) and hits == [], "실패한 날 핑을 보내면 외부 감시가 영영 조용하다"
    assert "보냈다" in fc.heartbeat(url, ok=True)[0] and hits == ["GET"]


def test_heartbeat_absent_is_a_one_line_note():
    lines = fc.heartbeat("", ok=True)
    assert len(lines) == 1 and "HEARTBEAT_URL 없음" in lines[0]


# ── 휴장·전 거래일 확정 ─────────────────────────────────────────────────────

def test_weekday_holiday_is_quiet(tmp_path):
    """수급도 장중 틱도 없으면 휴장이다 — 연 10번 '수집 실패' 경고를 내면 경고를 안 읽게 된다."""
    rc, lines = fc.judge(_reports(tmp_path, "2026-11-05"), TODAY, 0, market_open=False)
    assert rc == 0 and "휴장" in lines[0]


def test_open_market_without_rows_still_warns(tmp_path):
    """틱이 있는데(장이 열렸다) 수급이 없으면 수집 실패다 — 휴장으로 넘기면 안 된다."""
    rc, lines = fc.judge(_reports(tmp_path, "2026-11-05"), TODAY, 0, market_open=True)
    assert rc == 75 and "수집이 실패했다" in lines[0]


def test_previous_day_without_final_warns(tmp_path):
    """08:20 확정 모드를 놓친 날은 다음 거래일 수집이 들어오면 아무도 다시 안 본다 — 그날 저녁에 알린다."""
    root = _reports(tmp_path, TODAY)
    (root / "2026-11-05").mkdir()
    (root / "2026-11-05" / "numbers.html").write_text("<html>")
    rc, lines = fc.check_prev_final(root, "2026-11-05")
    assert rc == 75 and "확정되지 않았다" in lines[0] and "잠정 리포트만" in lines[0]
    (root / "2026-11-05" / "FINAL.txt").write_text("x")
    assert fc.check_prev_final(root, "2026-11-05")[0] == 0


def test_rebuilt_previous_day_counts_as_final_only_with_final_txt(tmp_path):
    """재구성은 확정치를 확인했을 때만 FINAL.txt 를 쓴다 — REBUILT.txt 만으로는 확정이 아니다(잠정·창 밖 재구성)."""
    root = _reports(tmp_path, TODAY)
    (root / "2026-11-05").mkdir()
    (root / "2026-11-05" / "numbers.html").write_text("<html>")
    (root / "2026-11-05" / "REBUILT.txt").write_text("…\nfinalize: pending\n")
    rc, lines = fc.check_prev_final(root, "2026-11-05")
    assert rc == 75 and "재구성은 했지만" in lines[0], lines
    (root / "2026-11-05" / "FINAL.txt").write_text("재구성 — daily_finalize 확인 뒤")
    assert fc.check_prev_final(root, "2026-11-05")[0] == 0


def test_previous_day_external_truth_skip_streak_warns_and_a_single_skip_is_a_note(tmp_path):
    """전 거래일 확정 검증이 외부 참값 대조를 3번째 연달아 건너뛰었으면([WARN]) 75, 한 번 건너뛴 것은 한 줄(0).

    주입: [WARN] 줄을 못 보거나 한 번 SKIP 에도 경고하면 실패한다.
    """
    root = _reports(tmp_path, TODAY)
    d = root / "2026-11-05"
    d.mkdir()
    assert fc.check_external_truth(root, "2026-11-05") == (0, [])          # VERIFY.txt 없음 — 할 말 없다
    assert fc.check_external_truth(root, None) == (0, [])
    (d / "VERIFY.txt").write_text("       SKIP 정규장 종가 대조 — 2026-11-04 regular_close 준비 안 됨\n"
                                  "       외부 참값 연속 SKIP 1회(이 리포트 포함, 확정 리포트만 센다)\n", "utf-8")
    rc, lines = fc.check_external_truth(root, "2026-11-05")
    assert rc == 0 and "건너뛰었다(SKIP)" in lines[0], lines
    (d / "VERIFY.txt").write_text("       SKIP 정규장 종가 대조 — x\n  [WARN] F · 외부 참값 연속 SKIP 3회(이 리포트 포함)"
                                  "   정규장 종가(regular_close)가 며칠째 준비 안 됐다\n", "utf-8")
    rc, lines = fc.check_external_truth(root, "2026-11-05")
    assert rc == 75 and "외부 참값 연속 SKIP 3회" in lines[0] and "2026-11-05" in lines[0], lines


def test_freshness_looks_for_the_same_marks_verify_report_writes():
    """두 스크립트가 같은 글을 쓰고 찾는다 — verify_report 쪽 문구만 바꾸면 경고가 조용히 안 뜬다."""
    spec = importlib.util.spec_from_file_location("verify_report", REPO / "scripts" / "verify_report.py")
    vr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vr)
    assert (fc.SKIP_MARK, fc.SKIP_STREAK_MARK) == (vr.VF_SKIP_MARK, vr.VF_SKIP_STREAK_MARK)


def test_error_outranks_warning():
    assert fc.worse(1, 75) == 1 and fc.worse(75, 1) == 1 and fc.worse(0, 75) == 75


# ── 크론 경로 ──────────────────────────────────────────────────────────────

@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "sector-flow"
    (r / "scripts").mkdir(parents=True)
    for name in ("_env.sh", "freshness_check.sh", "freshness_check.py", "ops_ledger.py"):
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
    # cron_run.sh 가 부르는 옆자리 quant-airflow/scripts/alert.sh — 그 옆 .env 가 웹훅의 정본이다.
    alert = tmp_path / "quant-airflow" / "scripts" / "alert.sh"
    alert.parent.mkdir(parents=True)
    alert.write_text("#!/bin/sh\nexit 0\n")
    alert.chmod(alert.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "quant-airflow" / ".env").write_text("")
    return r


def _qa_env(repo: Path, text: str) -> None:
    (repo.parent / "quant-airflow" / ".env").write_text(text)


def _fresh(repo: Path) -> None:
    """오늘 리포트가 걸렸고 전 거래일도 확정됐다."""
    _reports(repo, TODAY)
    (repo / "reports" / "2026-11-05").mkdir()
    (repo / "reports" / "2026-11-05" / "FINAL.txt").write_text("x")


def _run(repo: Path, *args: str, **env) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items()
         if k not in ("KR_QUANT_DB", "KR_QUANT_REPORTS", "ALERT_WEBHOOK_URL", "HEARTBEAT_URL",
                      "SECTOR_FLOW_ALERT") and not k.startswith("SF_CRON_")}
    e.update(KR_QUANT_UV=str(repo.parent / "uv"), HOME=str(repo.parent), **env)
    return subprocess.run([str(repo / "scripts" / "freshness_check.sh"), "--today", TODAY, *args],
                          capture_output=True, text=True, env=e, timeout=60)


def test_cron_path_flags_a_missing_report(repo, hook):
    _reports(repo, "2026-11-05")
    _qa_env(repo, f"ALERT_WEBHOOK_URL={hook(200, b'{}')[0]}\n")
    p = _run(repo)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "안 걸렸다" in p.stdout


def test_cron_path_warns_about_the_webhook_even_when_fresh(repo):
    _fresh(repo)
    p = _run(repo)
    assert p.returncode == 75, p.stdout + p.stderr
    assert "[신선도] OK" in p.stdout and "ALERT_WEBHOOK_URL" in p.stdout


@pytest.mark.parametrize("where", ["export", "sector-flow-env"])
def test_cron_path_does_not_count_a_webhook_alert_sh_cannot_see(repo, hook, where):
    """`export KEY=` 줄이나 이 레포 .env 의 값은 _env.sh 가 불러와 '있다' 로 보이지만 alert.sh 는 못 읽는다."""
    url, hits = hook(200, b"{}")
    _fresh(repo)
    if where == "export":
        _qa_env(repo, f"export ALERT_WEBHOOK_URL={url}\n")
    else:
        with open(repo / ".env", "a") as f:
            f.write(f"ALERT_WEBHOOK_URL={url}\n")
    p = _run(repo)
    assert p.returncode == 75, p.stdout + p.stderr
    assert "ALERT_WEBHOOK_URL 이 비었다" in p.stdout and hits == []


def test_cron_path_clean_day_passes_and_pings_the_heartbeat(repo, hook):
    url, hits = hook(200, b'{"type": 1}')
    beat, beats = hook(200, b"OK")
    _fresh(repo)
    _qa_env(repo, f"ALERT_WEBHOOK_URL='{url}'\nHEARTBEAT_URL={beat}\n")
    p = _run(repo)
    assert p.returncode == 0, p.stdout + p.stderr
    assert hits == ["GET"] and beats == ["GET"]
    assert url not in p.stdout + p.stderr and beat not in p.stdout + p.stderr


def test_cron_path_heartbeat_works_without_a_webhook(repo, hook):
    """하트비트만 쓰는 설정 — 웹훅이 비었다고 핑을 안 보내면 외부 감시가 매일 같은 이유로 울린다."""
    beat, beats = hook(200, b"OK")
    _fresh(repo)
    _qa_env(repo, f"HEARTBEAT_URL={beat}\n")
    p = _run(repo)
    assert p.returncode == 75 and "ALERT_WEBHOOK_URL 이 비었다" in p.stdout, p.stdout + p.stderr
    assert beats == ["GET"] and "[하트비트] 보냈다" in p.stdout


def test_cron_path_dead_webhook_withholds_the_heartbeat(repo, hook):
    """웹훅이 **있는데 죽은** 날은 그 경고가 아무에게도 안 간다 — 핑이 비는 것이 유일한 신호다."""
    url, _ = hook(404, b'{"message": "Unknown Webhook"}')
    beat, beats = hook(200, b"OK")
    _fresh(repo)
    _qa_env(repo, f"ALERT_WEBHOOK_URL={url}\nHEARTBEAT_URL={beat}\n")
    p = _run(repo)
    assert p.returncode == 75, p.stdout + p.stderr
    assert beats == [] and "알림 웹훅이 죽어 있다" in p.stdout


@pytest.mark.parametrize(("webhook", "pinged"), [(None, True), (200, True), (404, False)])
def test_heartbeat_follows_the_data_checks_not_the_webhook_in_process(tmp_path, hook, monkeypatch, capsys,
                                                                      webhook, pinged):
    """``main`` 을 직접 — 주말(데이터 점검 0)에 웹훅이 없거나 살았으면 핑, 있는데 죽었으면 안 보낸다."""
    beat, beats = hook(200, b"OK")
    alert = tmp_path / "qa" / "scripts" / "alert.sh"
    alert.parent.mkdir(parents=True)
    alert.write_text("#!/bin/sh\nexit 0\n")
    alert.chmod(alert.stat().st_mode | stat.S_IEXEC)
    env = f"HEARTBEAT_URL={beat}\n"
    if webhook is not None:
        env += f"ALERT_WEBHOOK_URL={hook(webhook, b'{}')[0]}\n"
    (tmp_path / "qa" / ".env").write_text(env)
    for k in ("SF_CRON_ENV", "ALERT_WEBHOOK_URL", "HEARTBEAT_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SECTOR_FLOW_ALERT", str(alert))
    rc = fc.main(["--today", "2026-11-07", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == (0 if webhook == 200 else 75), out
    assert (beats == ["GET"]) == pinged, out
    if not pinged:
        assert "알림 웹훅이 죽어 있다" in out


def test_cron_path_dead_webhook_warns(repo, hook):
    url, _ = hook(404, b'{"message": "Unknown Webhook"}')
    _fresh(repo)
    _qa_env(repo, f"ALERT_WEBHOOK_URL={url}\n")
    p = _run(repo)
    assert p.returncode == 75, p.stdout + p.stderr
    assert "폐기됐거나 틀렸다" in p.stdout and "secret-token" not in p.stdout + p.stderr


def test_cron_path_previous_day_not_final_warns(repo, hook):
    _reports(repo, TODAY)
    (repo / "reports" / "2026-11-05").mkdir()
    (repo / "reports" / "2026-11-05" / "numbers.html").write_text("<html>")
    _qa_env(repo, f"ALERT_WEBHOOK_URL={hook(200, b'{}')[0]}\n")
    p = _run(repo)
    assert p.returncode == 75, p.stdout + p.stderr
    assert "2026-11-05" in p.stdout and "확정되지 않았다" in p.stdout


def test_cron_path_flags_a_previous_day_external_truth_skip_streak(repo, hook):
    _reports(repo, TODAY)
    prev = repo / "reports" / "2026-11-05"
    prev.mkdir()
    (prev / "FINAL.txt").write_text("x")
    (prev / "VERIFY.txt").write_text("  [WARN] F · 외부 참값 연속 SKIP 4회(이 리포트 포함)\n", "utf-8")
    _qa_env(repo, f"ALERT_WEBHOOK_URL={hook(200, b'{}')[0]}\n")
    p = _run(repo)
    assert p.returncode == 75, p.stdout + p.stderr
    assert "[외부 참값]" in p.stdout and "연속 SKIP 4회" in p.stdout, p.stdout


def test_cron_path_holiday_without_ticks_is_quiet(repo, hook):
    """평일 휴장 — 수급도 틱도 없다. 틱 표가 있는 DB 에서만 휴장이라 말한다."""
    con = sqlite3.connect(repo.parent / "db.sqlite")
    con.execute("DELETE FROM supply_demand WHERE date = ?", (TODAY,))
    con.execute("CREATE TABLE ticks(code TEXT, ts TEXT)")
    con.execute("INSERT INTO ticks VALUES('000001', '2026-11-05 15:19:59')")      # 전날 장 끝
    con.commit()
    con.close()
    _reports(repo, "2026-11-05")
    (repo / "reports" / "2026-11-05" / "FINAL.txt").write_text("x")
    _qa_env(repo, f"ALERT_WEBHOOK_URL={hook(200, b'{}')[0]}\n")
    p = _run(repo)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "휴장으로 본다" in p.stdout
    # 같은 날 틱이 있으면(장이 열렸다) 수집 실패다.
    con = sqlite3.connect(repo.parent / "db.sqlite")
    con.execute("INSERT INTO ticks VALUES('000001', ?)", (f"{TODAY} 09:00:02",))
    con.commit()
    con.close()
    p = _run(repo)
    assert p.returncode == 75 and "수집이 실패했다" in p.stdout, p.stdout + p.stderr


def test_cron_path_db_failure_is_an_error_and_hides_the_password(repo, hook):
    (repo / ".env").write_text("KR_QUANT_DB=postgresql://u:s3cret@127.0.0.1:1/x\n")
    _reports(repo, TODAY)
    _qa_env(repo, f"ALERT_WEBHOOK_URL={hook(200, b'{}')[0]}\n")
    p = _run(repo)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "못 셌다" in p.stdout
    assert "s3cret" not in p.stdout + p.stderr
