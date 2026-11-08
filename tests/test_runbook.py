"""운영 런북(README §3.1)과 크론 래퍼 — 알림이 오면 할 일이 적혀 있는가, 래퍼가 제 알림을 망치지 않는가.

* 운영자에게 가는 문구(크론 꼬리 → alert.sh)마다 §3.1 에 줄이 있다. 목록은 손으로 고른다(:data:`MESSAGES`) —
  같은 뜻의 문구 여럿을 한 줄이 받기도 하고, 표에는 원문 그대로가 아니라 열쇠말만 적기도 해서다. 대신 원천에서
  ⚠️·stderr·알림 제목 꼴의 줄을 정규식으로 긁어, 목록에 없는 **새 문구**가 생기면 빨개진다.
* ``cron_run.sh`` 는 임시 파일·로그·KILL 알림을 진짜 셸로 돌려 본다(가짜 alert.sh, 임시 레포).
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _runbook() -> str:
    text = (REPO / "README.md").read_text("utf-8")
    m = re.search(r"^### 3\.1 .*?(?=^## )", text, re.S | re.M)
    assert m, "README 에 §3.1 이 없다"
    return m.group(0)


# (원천 파일, 원천에 있는 문구, §3.1 에 있어야 할 문구). 원천 문구가 사라지면(말을 바꿨으면) 여기도 고친다.
MESSAGES: list[tuple[str, str, str]] = [
    # cron_run.sh — 알림 제목과 본문
    ("scripts/cron_run.sh", "실패 (rc=", "실패 (rc="),
    ("scripts/cron_run.sh", "경고 (rc=75)", "경고 (rc=75)"),
    ("scripts/cron_run.sh", "시간 초과 (rc=124)", "시간 초과 (rc=124)"),
    ("scripts/cron_run.sh", "임시 파일을 못 만들었", "임시 파일을 못 만들었다"),
    ("scripts/cron_run.sh", "알림 통로 없음", "알림 통로 없음"),
    ("scripts/cron_run.sh", "재시작 연속", "web 실패 (rc=137)"),
    # _env.sh
    ("scripts/_env.sh", "uv 를 찾지 못했다", "uv 를 찾지 못했다"),
    ("scripts/_env.sh", "exit 127", "rc=127"),
    ("scripts/_env.sh", "DB 접속정보가 없다", "DB 접속정보가 없다"),
    # daily_report.sh
    ("scripts/daily_report.sh", "다른 리포트 실행이 돌고 있다", "다른 리포트 실행이 돌고 있다"),
    ("scripts/daily_report.sh", "임시 파일을 못 만들었다", "임시 파일을 못 만들었다"),
    ("scripts/daily_report.sh", "DB 에서 기준일을 못 읽었다", "DB 에서 기준일을 못 읽었다"),
    ("scripts/daily_report.sh", "기준일이 없다", "기준일이 없다"),
    ("scripts/daily_report.sh", "확정치를 확인하지 못했다", "확정치를 확인하지 못했다"),
    ("scripts/daily_report.sh", "확정치를 확인하지 못해 잠정치로 만들었다", "확정치를 확인하지 못해 잠정치로 만들었다"),
    ("scripts/freshness_check.py", "[외부 참값] 전 거래일", "[외부 참값] 전 거래일"),
    # SLO(docs/slo.md) — 원장(events.jsonl)으로 재는 기한 위반과 원장 기록 실패
    ("scripts/freshness_check.py", "잠정 리포트가 기한(", "잠정 리포트가 기한"),
    ("scripts/freshness_check.py", "확정이 기한(", "확정이 기한"),
    ("scripts/_ledger.sh", "운영 사건을 기록하지 못했다", "운영 사건을 기록하지 못했다"),
    ("scripts/ops_ledger.py", "운영 사건을 기록하지 못했다", "운영 사건을 기록하지 못했다"),
    ("scripts/daily_report.sh", "수집 미완 — 게시하지 않는다", "수집 미완"),
    ("scripts/daily_report.sh", "는 오늘이 아닌데 수집 미완이다", "오늘이 아닌데 수집 미완"),
    ("scripts/daily_report.sh", "완전성 관문 오류(rc=", "완전성 관문 오류(rc=N)"),
    ("scripts/daily_report.sh", "검증 실패", "검증 실패"),
    ("scripts/daily_report.sh", "게시했지만 검증에 걸렸다", "게시했지만 검증에 걸렸다"),
    # daily_report.sh — 지난 기준일 재구성(KR_QUANT_ASOF, 손 실행·rebuild_history)
    ("scripts/daily_report.sh", "KR_QUANT_ASOF 가 날짜 꼴(YYYY-MM-DD)이 아니다", "KR_QUANT_ASOF 가 날짜 꼴(YYYY-MM-DD)이 아니다"),
    ("scripts/daily_report.sh", "휴장이 아니라 수급 수집 공백이다", "수급 수집 공백"),
    ("scripts/daily_report.sh", "는 거래일이 아니다(그 이하 마지막 수급일", "거래일이 아니다 … 휴장일이다"),
    ("scripts/daily_report.sh", "지금 DB 에서도 수집 미완이다", "지금 DB 에서도 수집 미완"),
    ("scripts/daily_report.sh", "확정치를 확인하지 못해 잠정으로 재구성했다", "확정치를 확인하지 못해 잠정으로 재구성했다"),
    ("scripts/daily_report.sh", "확정 기록 없음", "확정 기록 없음"),
    ("scripts/serve_reports.py", "확정 기록 없음", "확정 기록 없음"),
    ("scripts/rebuild_history.py", "는 git 체크아웃이 아니다", "git 체크아웃이 아니다"),
    ("scripts/rebuild_history.py", "HEAD 가 origin/main 에 없다", "origin/main 에 없다"),
    ("scripts/rebuild_history.py", "작업 트리에 커밋 안 한 변경이 있다", "커밋 안 한 변경"),
    # serve.sh — 재시작 알림과 --soft-fail(한 시간 이어지면 web 실패 rc=1)
    ("scripts/serve.sh", "재시작 — /healthz 실패", "재시작 — /healthz 실패"),
    ("scripts/serve.sh", "Tailscale 주소가 없다", "Tailscale 주소가 없다"),
    ("scripts/serve.sh", "누구인지(.serve.pid) 몰라 못 끈다", "누구인지(.serve.pid) 몰라 못 끈다"),
    ("scripts/serve_reports.py", "에 못 묶었다", "에 못 묶었다"),
    # freshness_check.py
    ("scripts/freshness_check.py", "오늘 리포트가 안 걸렸다", "오늘 리포트가 안 걸렸다"),
    ("scripts/freshness_check.py", "supply_demand 가 비었다", "supply_demand 가 비었다"),
    ("scripts/freshness_check.py", "휴장일이면 무시", "휴장일이면 무시"),
    ("scripts/freshness_check.py", "이 확정되지 않았다", "이 확정되지 않았다"),
    ("scripts/freshness_check.py", "ALERT_WEBHOOK_URL 이 비었다", "ALERT_WEBHOOK_URL 이 비었다"),
    ("scripts/freshness_check.py", "200 으로 답했지만 JSON 이 아니다", "200 으로 답했지만 JSON 이 아니다"),
    ("scripts/freshness_check.py", "웹훅이 폐기됐거나 틀렸다", "웹훅이 폐기됐거나 틀렸다"),
    ("scripts/freshness_check.py", "로 답했다", "웹훅이 HTTP N 로 답했다"),
    ("scripts/freshness_check.py", "초 안에 닿지 못했다", "초 안에 닿지 못했다"),
    ("scripts/freshness_check.py", "alert.sh 가 없다", "alert.sh 가 없다"),
    ("scripts/freshness_check.py", "DB 에서 오늘 수급을 못 셌다", "DB 에서 오늘 수급을 못 셌다"),
    ("scripts/freshness_check.py", "[하트비트] 안 보낸다", "[하트비트]"),
    ("scripts/freshness_check.py", "[하트비트] HEARTBEAT_URL 없음", "HEARTBEAT_URL=<주소>"),
    # finalize_done.py — 08:20·10:20 본문의 [확정치] 줄
    ("scripts/finalize_done.py", "기록이 없다", "기록이 없다"),
    ("scripts/finalize_done.py", "로그 경로 형식이 바뀌었을 수 있다", "로그 경로 형식이 바뀌었을 수 있다"),
    ("scripts/finalize_done.py", "태스크 이름이 바뀐 것이다", "태스크 이름이 바뀐 것이다"),
    ("scripts/finalize_done.py", "를 덮지 않는다", "덮지 않는다"),
    ("scripts/finalize_done.py", "아직 안 돌았거나 도는 중", "아직 안 돌았거나 도는 중"),
    ("scripts/finalize_done.py", "성공으로 끝나지 않았다", "성공으로 끝나지 않았다"),
    # krx_truth.py — 공표치 대조의 ⚠️ 줄(리포트 빌드 로그 → 크론 꼬리)
    ("scripts/krx_truth.py", "[공표치 대조] SKIP", "[공표치 대조] SKIP"),
    ("scripts/krx_truth.py", "오차 큼", "[공표치 대조] 오차 큼"),
    ("scripts/krx_truth.py", "내부 오류", "[공표치 대조] 내부 오류"),
    ("scripts/freshness_check.py", "[공표치] 전 거래일", "[공표치] 전 거래일"),
    ("scripts/krx_truth.py", "krx_truth.json 을 못 재웠다", "krx_truth.json 을 못 재웠다"),
    # completeness_gate.py — 수집 미완 본문의 [FAIL] 줄
    ("scripts/completeness_gate.py", "[FAIL]", "[FAIL]"),
    ("scripts/completeness_gate.py", "daily_price_adjust", "daily_price_adjust"),
    ("scripts/completeness_gate.py", "시장 목록이 비었다", "시장 목록이 비었다"),
    # daily_report.sh — 다이제스트(선택 기능, KR_QUANT_DIGEST=1)
    ("scripts/daily_report.sh", "다이제스트를 못 만들었다", "다이제스트를 못 만들었다"),
]

#: 긁힌 줄 가운데 운영자에게 가지 않거나 런북 줄이 필요 없는 것 — (원천 파일, 문구, 이유).
EXEMPT: list[tuple[str, str, str]] = [
    ("scripts/cron_run.sh", "이름이 없다", "사용법 오류 — 크론 줄은 정본(deploy/crontab.simnode)이고 그 꼴은 test_daily_report_flow 가 본다"),
    ("scripts/cron_run.sh", "명령이 없다", "사용법 오류 — 위와 같다"),
    ("scripts/_env.sh", "cp .env.example .env", "바로 위 'DB 접속정보가 없다' 의 둘째 줄"),
    ("scripts/daily_report.sh", "KR_QUANT_FINAL=1 $0", "'확정치를 확인하지 못했다' 의 이어지는 줄"),
    ("scripts/daily_report.sh", "기준일이 넘어가 이 날은 잠정으로 남는다", "'확정치를 확인하지 못했다' 의 이어지는 줄"),
    ("scripts/freshness_check.py", "[하트비트] 못 보냈다", "종료코드를 안 바꾼다 — 외부 감시가 알린다(하트비트 줄)"),
    ("scripts/freshness_check.py", "[하트비트] 보냈다", "정상 — 알림이 아니다"),
]

#: 셸 원천에서 운영자 문구를 긁는 정규식 — stderr 로 나가는 echo, ⚠️ 줄, 알림 제목, soft-fail 이유.
SH_SCRAPE: dict[str, list[str]] = {
    "scripts/cron_run.sh": [r'echo "([^"]+)"[^\n]*>&2', r'echo "([^"]*⚠️[^"]*)"', r'title="([^"]+)"',
                            r'echo "(\[cron_run\][^"]+)"'],
    "scripts/_env.sh": [r'echo "([^"]+)"[^\n]*>&2'],
    "scripts/daily_report.sh": [r'echo "([^"]+)"[^\n]*>&2', r'echo "([^"]*⚠️[^"]*)"'],
    "scripts/serve.sh": [r'--soft-fail "([^"]+)"', r'"\$ALERT" \w+ "([^"]+)"'],
    "scripts/_ledger.sh": [r'echo "([^"]+)"[^\n]*>&2'],
}


def _strings(node: ast.AST) -> str | None:
    """문자열 상수·f-문자열(이어 붙인 조각은 파서가 하나로 합친다)의 글자. 끼운 값은 ``{}``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    return None


def _walk_outer(node: ast.AST):
    """``ast.walk`` 와 같되 f-문자열 안(조각·끼운 식)으로는 안 들어간다 — 조각이 따로 긁히면 문구가 잘린다."""
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return                                                      # 독스트링 — 운영자에게 안 간다
    yield node
    if not isinstance(node, ast.JoinedStr):
        for child in ast.iter_child_nodes(node):
            yield from _walk_outer(child)


def _py_messages(rel: str) -> list[str]:
    """파이썬 원천의 운영자 문구 — 파일마다 무엇이 꼬리로 나가는지가 다르다."""
    out: list[str] = []
    for node in _walk_outer(ast.parse(_src(rel))):
        if rel == "scripts/finalize_done.py":
            # (False, 이유) — 이유가 08:20 본문의 [확정치] 줄이다.
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple) and len(node.value.elts) == 2 \
                    and isinstance(node.value.elts[0], ast.Constant) and node.value.elts[0].value is False:
                out.append(_strings(node.value.elts[1]) or "")
        elif rel == "scripts/serve_reports.py":
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "soft_fail" and len(node.args) == 2:
                s = _strings(node.args[1])
                if s:
                    out.append(s)
        else:
            s = _strings(node)
            if s and any(k in s for k in ("⚠️", "[하트비트]", "[FAIL]", "daily_price_adjust")):
                out.append(s)
    return out


PY_SCRAPE = ["scripts/freshness_check.py", "scripts/finalize_done.py", "scripts/completeness_gate.py",
             "scripts/serve_reports.py", "scripts/krx_truth.py", "scripts/ops_ledger.py"]


def _src(rel: str) -> str:
    return (REPO / rel).read_text("utf-8")


@pytest.mark.parametrize(("src", "phrase", "row"), MESSAGES, ids=[f"{s.split('/')[-1]}:{p}" for s, p, _ in MESSAGES])
def test_every_operator_message_has_a_runbook_row(src, phrase, row):
    """알림이 왔는데 README §3.1 에 그 문구가 없으면 새벽에 코드를 읽어야 한다."""
    assert phrase in _src(src), f"{src} 에 '{phrase}' 가 없다 — 문구를 바꿨으면 MESSAGES 와 §3.1 을 같이 고쳐라"
    assert row in _runbook(), f"README §3.1 에 '{row}' 가 없다({src} 의 '{phrase}' 알림에 할 일이 없다)"


def _scraped() -> list[tuple[str, str]]:
    out = []
    for rel, pats in SH_SCRAPE.items():
        text = _src(rel)
        for pat in pats:
            out += [(rel, m.group(1)) for m in re.finditer(pat, text)]
    for rel in PY_SCRAPE:
        out += [(rel, s) for s in _py_messages(rel)]
    return out


def test_scraper_still_finds_messages_in_every_source():
    """긁개가 원천의 꼴 변화로 아무것도 못 긁으면 아래 검사가 빈손으로 초록이다."""
    found = {rel for rel, _ in _scraped()}
    assert found == set(SH_SCRAPE) | set(PY_SCRAPE), (set(SH_SCRAPE) | set(PY_SCRAPE)) - found


def test_new_operator_messages_need_a_runbook_entry():
    """⚠️·stderr·알림 제목 꼴의 새 문구는 MESSAGES(→ §3.1 줄) 나 EXEMPT(이유) 에 들어가야 한다."""
    known = [(s, p) for s, p, _ in MESSAGES] + [(s, p) for s, p, _ in EXEMPT]
    orphans = [(rel, msg) for rel, msg in _scraped()
               if not any(s == rel and p in msg for s, p in known)]
    assert not orphans, "런북 줄이 없는 운영자 문구:\n" + "\n".join(f"  {r}: {m}" for r, m in orphans)


def test_exemptions_still_point_at_real_lines():
    for rel, phrase, why in EXEMPT:
        assert why and phrase in _src(rel), (rel, phrase)


# ── cron_run.sh ──────────────────────────────────────────────────────────

@pytest.fixture
def sandbox(tmp_path):
    """임시 레포(scripts/cron_run.sh·reports/)와 가짜 alert.sh. (레포, 알림 기록 파일, 환경)."""
    r = tmp_path / "sector-flow"
    (r / "scripts").mkdir(parents=True)
    (r / "reports").mkdir()
    shutil.copy(REPO / "scripts" / "cron_run.sh", r / "scripts" / "cron_run.sh")
    log, stub = tmp_path / "alert.log", tmp_path / "alert.sh"
    stub.write_text(f'#!/bin/sh\necho "$1|$2" >> "{log}"\ncat >> "{log}"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {k: v for k, v in os.environ.items() if k != "SECTOR_FLOW_KILL_STREAK"}
    env["SECTOR_FLOW_ALERT"] = str(stub)
    return r, log, env


def _cron_run(r: Path, env: dict, *cmd: str, name: str = "rpt", **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(r / "scripts" / "cron_run.sh"), name, "--", *cmd], text=True,
                          timeout=30, env=env, **({"capture_output": True} | kw))


def _until(cond, timeout: float = 15) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def _age(p: Path, days: float = 2) -> None:
    t = time.time() - days * 86400
    os.utime(p, (t, t))


def test_cron_run_keeps_a_live_wrappers_tail_and_alerts_with_it(sandbox):
    """웹 서버를 감싼 래퍼는 며칠씩 돈다 — 하루 넘은 꼬리 쓸기가 그 파일을 지우면 서버가 죽은 날 본문이
    '임시 파일을 못 만들었다' 로 나갔다."""
    r, log, env = sandbox
    stop = r / "stop"
    srv = subprocess.Popen([str(r / "scripts" / "cron_run.sh"), "web", "--", "sh", "-c",
                            f'echo 서버가 남긴 마지막 줄; while [ ! -e "{stop}" ]; do sleep 0.1; done; exit 3'],
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           start_new_session=True)
    try:
        assert _until(lambda: any(p.stat().st_size for p in (r / "reports").glob(".cron_run.*")))
        tail = next((r / "reports").glob(".cron_run.*"))
        assert str(srv.pid) in tail.name, f"꼬리 파일 이름에 래퍼 pid 가 없다: {tail.name}"
        _age(tail)
        assert _cron_run(r, env, "true").returncode == 0           # 다른 크론이 쓸기를 돈다
        assert tail.exists(), "살아 있는 래퍼의 꼬리를 지웠다"
        stop.touch()
        assert srv.wait(timeout=15) == 3
    finally:
        if srv.poll() is None:
            os.killpg(srv.pid, signal.SIGKILL)
    alert = log.read_text()
    assert "서버가 남긴 마지막 줄" in alert and "임시 파일을 못 만들었" not in alert, alert


def test_cron_run_sweeps_a_stale_tail_whose_owner_is_gone_or_someone_else(sandbox):
    """주인이 죽었거나(pid 없음) 그 pid 를 다른 프로세스가 물려받은 하루 넘은 꼬리는 지운다."""
    r, _, env = sandbox
    dead = r / "reports" / ".cron_run.999999999.aaaaaa"
    reused = r / "reports" / f".cron_run.{os.getpid()}.bbbbbb"       # 이 pytest — cron_run.sh 가 아니다
    fresh = r / "reports" / ".cron_run.999999998.cccccc"
    for p in (dead, reused, fresh):
        p.write_text("x")
    _age(dead)
    _age(reused)
    assert _cron_run(r, env, "true").returncode == 0
    assert not dead.exists() and not reused.exists() and fresh.exists()


@pytest.mark.parametrize(("streak", "alerts"), [(None, True), ("1", True), ("5", False), ("12", True), ("x", True)])
def test_kill_alerts_follow_the_serve_restart_streak(sandbox, streak, alerts):
    """TERM 을 버티는 서버를 serve.sh 가 5분마다 KILL 하면 그 래퍼의 137 알림이 시간당 12건이었다 —
    serve.sh 의 재시작 연속 횟수와 같은 박자(첫 회·12회마다)로만 보낸다. 횟수 파일이 없으면(다른 무엇이 KILL) 늘 알린다."""
    r, log, env = sandbox
    counter = r / "reports" / ".serve.restarts"
    if streak is not None:
        counter.write_text(streak + "\n")
    p = _cron_run(r, {**env, "SECTOR_FLOW_KILL_STREAK": str(counter)}, "sh", "-c", "kill -9 $$", name="web")
    assert p.returncode == 137, p.stdout + p.stderr
    assert log.exists() == alerts, (log.read_text() if log.exists() else "") + p.stdout
    if alerts:
        assert log.read_text().startswith("error|web 실패 (rc=137)")
    else:
        assert "알리지 않는다" in p.stdout and f"연속 {streak}회째" in p.stdout


def test_kill_streak_only_quiets_kills(sandbox):
    """솎는 것은 KILL(137) 뿐이다 — 같은 래퍼의 다른 실패는 연속 횟수와 상관없이 알린다."""
    r, log, env = sandbox
    counter = r / "reports" / ".serve.restarts"
    counter.write_text("5")
    p = _cron_run(r, {**env, "SECTOR_FLOW_KILL_STREAK": str(counter)}, "sh", "-c", "exit 1", name="web")
    assert p.returncode == 1 and log.read_text().startswith("error|web 실패 (rc=1)")


def test_kill_streak_matches_serve_sh_and_the_web_cron_line():
    """박자·파일이 serve.sh 와 어긋나면 KILL 알림이 재시작 알림과 따로 논다(영영 안 오거나 매번 온다)."""
    cron_run, serve = _src("scripts/cron_run.sh"), _src("scripts/serve.sh")
    every = re.search(r"^\s*KILL_ALERT_EVERY=(\d+)", cron_run, re.M)
    assert every and every.group(1) == re.search(r"^RESTART_ALERT_EVERY=(\d+)", serve, re.M).group(1)
    assert 'RESTARTS="$REPO/reports/.serve.restarts"' in serve
    web = [ln for ln in _cron_lines() if "scripts/serve.sh" in ln]
    assert len(web) == 1
    assert "SECTOR_FLOW_KILL_STREAK=/home/young/git/sector-flow/reports/.serve.restarts " in web[0], web[0]


BIG = 5 * 1024 * 1024 + 1


def _log_with(path: Path, size: int) -> None:
    with open(path, "wb") as f:
        f.write(b"\xec\x98\x9b\n")                                 # "옛" — 회전된 쪽에 남아야 한다
        f.truncate(size)


def test_cron_log_rotates_past_5mb_and_keeps_one_old_copy(sandbox):
    """크론 로그가 끝없이 자란다(경고 잡음 1,491줄이 한 파일에) — 5MB 를 넘으면 .1 로 돌리고 새로 쓴다."""
    r, _, env = sandbox
    log = r / "reports" / "cron.log"
    _log_with(log, BIG)
    with open(log, "ab") as out:
        p = _cron_run(r, env, "sh", "-c", "echo 새 실행", stdout=out, stderr=subprocess.STDOUT, capture_output=False)
    assert p.returncode == 0
    old = r / "reports" / "cron.log.1"
    assert old.stat().st_size == BIG and old.read_bytes().startswith("옛".encode())
    assert log.read_text("utf-8") == "새 실행\n"


def test_cron_log_under_the_cap_or_not_appending_is_left_alone(sandbox):
    """작은 로그는 그대로. `>>`(O_APPEND)가 아닌 리다이렉트는 비우면 다음 쓰기가 옛 오프셋에 구멍을 낸다 — 안 건드린다."""
    r, _, env = sandbox
    small = r / "reports" / "small.log"
    small.write_text("앞\n")
    with open(small, "ab") as out:
        _cron_run(r, env, "sh", "-c", "echo 뒤", stdout=out, stderr=subprocess.STDOUT, capture_output=False)
    assert small.read_text("utf-8") == "앞\n뒤\n" and not (r / "reports" / "small.log.1").exists()
    plain = r / "reports" / "plain.log"
    _log_with(plain, BIG)
    with open(plain, "r+b") as out:
        out.seek(0, os.SEEK_END)
        _cron_run(r, env, "sh", "-c", "echo 뒤", stdout=out, stderr=subprocess.STDOUT, capture_output=False)
    assert not (r / "reports" / "plain.log.1").exists() and plain.stat().st_size == BIG + len("뒤\n".encode())


# ── 크론 정본·명령 문서 ────────────────────────────────────────────────────

def _cron_lines() -> list[str]:
    text = _src("deploy/crontab.simnode")
    return [x for x in text.splitlines() if x.strip() and not x.startswith("#")]


def test_final_mode_is_retried_late_morning():
    """daily_finalize 가 08:20 뒤에 끝난 날은 손으로 돌리기 전까지 잠정으로 남았다 — 10:20 에 같은 확정 모드를 한 번 더."""
    final = [ln for ln in _cron_lines() if "daily_report.sh" in ln and "KR_QUANT_FINAL=1 " in ln]
    sched = {tuple(ln.split()[:5]) for ln in final}
    assert ("20", "8", "*", "*", "2-6") in sched and ("20", "10", "*", "*", "2-6") in sched, sched
    late = next(ln for ln in final if ln.split()[:2] == ["20", "10"])
    assert "timeout" in late and "cron_run.sh sector-flow-report" in late


def test_documented_lint_commands_match_ci():
    """CLAUDE.md·README 의 린트 줄이 CI 범위에서 빠지면(research/·compileall) 로컬 초록이 CI 빨강이 된다."""
    ci = _src(".github/workflows/ci.yml")
    ruff = re.search(r"ruff check ([^\n]+)", ci).group(1).strip()
    compile_ = re.search(r"python -m compileall -q (\S+)", ci).group(1)
    for doc in ("CLAUDE.md", "README.md"):
        text = _src(doc)
        assert f"ruff check {ruff}" in text, f"{doc} 의 린트 범위가 CI({ruff}) 와 다르다"
        assert f"compileall -q {compile_}" in text, f"{doc} 에 CI 의 compileall({compile_}) 이 없다"


# ── 크론 로그의 pandas 경고 잡음 ────────────────────────────────────────────

def _pandas_readers() -> set[str]:
    """``storage.py`` 에서 ``read_sql`` 을 부르는 함수 이름."""
    tree = ast.parse(_src("src/sectorflow/storage.py"))
    return {f.name for f in tree.body if isinstance(f, ast.FunctionDef) and "read_sql" in ast.unparse(f)}


def test_every_script_reading_sql_through_pandas_silences_the_dbapi_warning():
    """psycopg2 연결로 read_sql 을 부르면 호출마다 UserWarning 이 크론 로그에 두 줄씩 쌓인다(cron.log 1,491줄).
    새 스크립트가 직접이든 storage 를 거쳐서든 read_sql 을 타면 같은 필터를 단다."""
    readers = _pandas_readers()
    assert {"read_supply_demand", "market_cap_asof_bulk"} <= readers, readers
    missing = []
    for path in sorted((REPO / "scripts").glob("*.py")):
        text = path.read_text("utf-8")
        imported = set(re.findall(r"\b(\w+)\b", " ".join(re.findall(r"from sectorflow\.storage import \(?([^)\n]+(?:\n[^)]*)?)\)?",
                                                                   text))))
        if "read_sql" in text or imported & readers:
            if 'filterwarnings("ignore", message="pandas only supports SQLAlchemy"' not in text:
                missing.append(path.name)
    assert not missing, f"read_sql 을 타는데 pandas DBAPI 경고를 안 끈 스크립트: {missing}"
