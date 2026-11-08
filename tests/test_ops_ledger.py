"""운영 사건 원장(``reports/events.jsonl``) — 추가 전용 쓰기·회전·통계·백필·SLO 점검.

폴더 바꿔 끼우기가 운영 흔적을 지우는 설계라(INCOMPLETE 는 다음 성공이 없애고, mtime 은 재생성마다 리셋,
cron.log 는 5MB 회전) 원장이 없으면 "제때 게시됐나·몇 번 막혔나·숫자가 바뀐 날" 을 셀 수 없다. 여기서는
원장 자체(섞이지 않는 동시 기록, 자르지 않는 회전, 실패해도 리포트를 안 죽임)와 그 위의 통계·SLO 판정을 본다.

시각은 전부 시간대를 박은 ISO 문자열이다 — ``time.mktime``·``localtime`` 금지(머신 시간대에 따라 판정이 민다).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("ops_ledger", REPO / "scripts" / "ops_ledger.py")
ol = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ol)

_fspec = importlib.util.spec_from_file_location("freshness_check_slo", REPO / "scripts" / "freshness_check.py")
fc = importlib.util.module_from_spec(_fspec)
_fspec.loader.exec_module(fc)


def _ev(ts: str, **kw) -> dict:
    return {"ts": ts, **kw}


def _report(ts: str, asof: str, outcome: str, mode: str = "normal", **kw) -> dict:
    return {"ts": ts, "event": "report", "asof": asof, "mode": mode, "outcome": outcome,
            "final": outcome == "final" or kw.pop("final", False), **kw}


# ── 쓰기 — 한 줄 = 한 사건, 동시에도 섞이지 않는다 ─────────────────────────────

def test_append_lands_whole_lines_that_read_back(tmp_path):
    assert ol.append_event(tmp_path, {"ts": "2026-11-05T09:12:24Z", "event": "report", "asof": "2026-11-05"})
    assert ol.append_event(tmp_path, {"ts": "2026-11-05T09:13:00Z", "event": "job", "job": "rpt", "rc": 0})
    got = list(ol.iter_events(tmp_path))
    assert [e["event"] for e in got] == ["report", "job"]
    text = (tmp_path / "events.jsonl").read_text("utf-8")
    assert text.count("\n") == 2 and all(json.loads(x) for x in text.splitlines())


def test_concurrent_writers_lose_nothing(tmp_path):
    """여러 프로세스가 각자 열어 덧붙여도(크론 겹침·수동 실행) 줄이 사라지거나 찢기지 않는다 — O_APPEND 한 번의 write."""
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('ops_ledger', {str(REPO / 'scripts' / 'ops_ledger.py')!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "w = int(sys.argv[1])\n"
        "for i in range(40):\n"
        f"    assert m.append_event({str(tmp_path)!r}, {{'ts': '2026-11-05T09:00:00Z', 'event': 't', 'w': w, 'i': i}})\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", code, str(w)]) for w in range(6)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    got = [(e["w"], e["i"]) for e in ol.iter_events(tmp_path) if e.get("event") == "t"]
    assert sorted(got) == [(w, i) for w in range(6) for i in range(40)], f"{len(got)}줄만 남았다 — 동시 기록이 섞였다"


def test_rotation_continues_without_truncating(tmp_path, monkeypatch):
    monkeypatch.setattr(ol, "MAX_BYTES", 120)
    for i in range(6):
        assert ol.append_event(tmp_path, {"ts": f"2026-11-0{i + 1}T00:00:00Z", "event": "t", "i": i})
    first = tmp_path / "events.jsonl"
    cont = tmp_path / "events.jsonl.2"
    assert cont.exists(), "상한을 넘었는데 연속 파일을 안 시작했다"
    assert first.stat().st_size >= 120, "원장을 잘랐다 — 절대 자르면 안 된다"
    assert [e["i"] for e in ol.iter_events(tmp_path)] == list(range(6)), "읽기가 연속 파일을 잇지 못한다"
    assert ol.append_path(tmp_path) == cont or cont.stat().st_size >= 120


def test_append_failure_is_a_stderr_note_not_a_crash(tmp_path, capsys):
    root = tmp_path / "not-a-dir"
    root.write_text("파일이다 — 그 밑에 events.jsonl 을 못 만든다")
    assert ol.append_event(root, {"ts": "2026-11-05T00:00:00Z", "event": "t"}) is False
    err = capsys.readouterr().err
    assert "운영 사건을 기록하지 못했다" in err and "계속한다" in err


def test_reader_skips_torn_lines(tmp_path):
    (tmp_path / "events.jsonl").write_text(
        '{"ts":"2026-11-05T00:00:00Z","event":"a"}\n{"ts":"2026-11-05T00:00:01Z","ev\n'
        '{"ts":"2026-11-05T00:00:02Z","event":"b"}\n', "utf-8")
    assert [e["event"] for e in ol.iter_events(tmp_path)] == ["a", "b"]


# ── 웹 — 원장은 허용 목록에 없다 ──────────────────────────────────────────────

def test_ledger_is_not_in_the_web_allowlist():
    spec = importlib.util.spec_from_file_location("serve_reports", REPO / "scripts" / "serve_reports.py")
    sr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sr)
    assert not any(k.endswith(".jsonl") for k in sr.FILES), "운영 원장이 웹으로 샌다"


# ── SLO 상수 — 문서(docs/slo.md)와 코드가 같은 숫자를 말한다 ─────────────────────

def test_slo_constants_match_the_doc():
    doc = (REPO / "docs" / "slo.md").read_text("utf-8")
    assert f"{ol.PROV_SLO_KST:%H:%M}" == "18:30" and "18:30" in doc
    assert f"{ol.FINAL_TARGET_KST:%H:%M}" == "08:40" and "08:40" in doc
    assert f"{ol.FINAL_SLO_KST:%H:%M}" == "10:40" and "10:40" in doc


# ── 통계 ────────────────────────────────────────────────────────────────────

NOW = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)

# KST 벽시계 참고: 18:12 KST = 09:12Z · 19:12 KST = 10:12Z · 다음날 08:25 KST = 전날 23:25Z ·
# 다음날 10:25 KST = 다음날 01:25Z.
FIX = [
    _report("2026-11-03T09:12:00Z", "2026-11-03", "published"),                     # 제때
    _report("2026-11-03T23:25:00Z", "2026-11-03", "final", mode="final", diff=0),   # 08:25 — 목표 내
    _report("2026-11-04T10:12:00Z", "2026-11-04", "published"),                     # 19:12 — 늦음
    _report("2026-11-05T01:25:00Z", "2026-11-04", "final", mode="final", diff=3),   # 10:25 — 재시도로 SLO 내
    _report("2026-11-05T09:05:00Z", "2026-11-05", "incomplete", gate="blocked", rc=75),
    _report("2026-11-05T09:08:00Z", "2026-11-05", "published", verify="fail", rc=1),  # 18:08 — 제때
    _report("2026-11-08T09:14:00Z", "2026-11-05", "final", mode="final", diff=None),  # 11-08 18:14 — SLO 초과
    _report("2026-11-06T09:12:00Z", "2026-11-06", "published"),                     # 확정 기한 지났는데 확정 없음
    _report("2026-11-07T02:00:00Z", "2026-11-02", "rebuilt", mode="rebuild", commit="abc123", final=True),
    _report("2026-10-30T09:12:00Z", "2026-10-30", "published"),                     # 제때 — 그러나 확정 기록은
    _report("2026-11-08T05:00:00Z", "2026-10-30", "rebuilt", mode="rebuild", final=True),  # 재구성뿐(시각 불명)
    _ev("2026-11-05T09:00:00Z", event="probe", status=200, ok=True),
    _ev("2026-11-05T09:05:00Z", event="probe", status=503, ok=False),
    _ev("2026-11-05T11:30:00Z", event="freshness", today="2026-11-05", rc=75),
    _ev("2026-11-05T09:06:00Z", event="job", job="sector-flow-report", rc=1, duration_s=12),
]


def test_stats_measures_punctuality_gate_verify_final_delays_and_rebuilds():
    s = ol.stats(FIX, 30, now=NOW)
    assert s["asof_days"] == 6
    assert s["prov"]["on_time"] == 4                                  # 10-30·11-03·11-05·11-06
    assert [r["asof"] for r in s["prov"]["late"]] == ["2026-11-04"]
    assert s["prov"]["no_record"] == ["2026-11-02"]                   # 재구성만 남은 날
    assert s["final"]["by_target"] == 1
    assert [r["asof"] for r in s["final"]["by_slo"]] == ["2026-11-04"]
    assert [r["asof"] for r in s["final"]["late"]] == ["2026-11-05"]
    assert s["final"]["missing"] == ["2026-11-06"]
    # 재구성의 final=true 를 확정 시각으로 세면 흔적이 지워진 날이 전부 "확정 지연" 이 된다 — 따로 센다.
    assert s["final"]["rebuilt_only"] == ["2026-10-30"]
    assert s["gate_blocked_days"] == ["2026-11-05"]
    assert s["verify_failed_days"] == ["2026-11-05"]
    assert s["final_diff_unknown"] == ["2026-11-05"]
    assert s["probe"] == {"n": 2, "ok": 1, "share": 0.5}
    assert s["job_failures"] == {"sector-flow-report rc=1": 1}
    assert s["freshness"] == {"n": 1, "warn": 1, "error": 0}


def test_stats_window_cuts_old_events():
    old = _report("2026-09-01T09:12:00Z", "2026-09-01", "published")
    s30 = ol.stats([*FIX, old], 30, now=NOW)
    s90 = ol.stats([*FIX, old], 90, now=NOW)
    assert s30["asof_days"] == 6 and s90["asof_days"] == 7


def test_errata_lists_rebuilds_and_changed_finals_only():
    rows = ol.errata_rows(FIX)
    assert [(r["asof"], r["kind"]) for r in rows] == [("2026-10-30", "재구성"), ("2026-11-02", "재구성"),
                                                      ("2026-11-04", "확정 정정")]
    assert "3개" in rows[2]["detail"]


def test_render_speaks_the_numbers():
    text = ol.render(ol.stats(FIX, 30, now=NOW))
    assert "늦음 1일" in text and "2026-11-04" in text
    assert "SLO 초과 1일" in text and "미확정 1일" in text
    assert "관문 차단(INCOMPLETE): 2026-11-05" in text
    assert "정정(첫 게시 뒤 숫자 변경): 3건" in text
    assert "재구성으로만 확정 1일" in text


# ── 백필 — 남아 있는 흔적에서, 출처를 밝히고 ───────────────────────────────────

@pytest.fixture
def relics(tmp_path):
    """원장 도입 전의 흔적 — cron.log(옛 '완료 — 경로' 꼴 포함)·표시 파일·.archive."""
    root = tmp_path / "reports"
    root.mkdir()
    (root / "cron.log").write_text(
        "[2026-11-03 18:10:03] 생성 시작 — 기준일 2026-11-03, 구간 260거래일\n"
        "[2026-11-03 18:12:21] 검증 통과 — /x/reports/2026-11-03/VERIFY.txt\n"
        "[2026-11-03 18:12:21] 완료 — /x/reports/2026-11-03\n"
        "[2026-11-04 08:20:02] 생성 시작 — 기준일 2026-11-03, 구간 260거래일\n"
        "[2026-11-04 08:20:02] 확정치로 만든다 — 제목이 '마감·확정' 이 된다\n"
        "[2026-11-04 08:22:40] 검증 통과 — /x/reports/2026-11-03/VERIFY.txt\n"
        "[2026-11-04 08:22:41] 완료 — /x/reports/2026-11-03 를 latest 로 게시했다\n"
        "[2026-11-05 18:10:02] ⚠️ 2026-11-05 수집 미완 — 게시하지 않는다(latest 유지): /x/INCOMPLETE.txt\n",
        "utf-8")
    d2 = root / "2026-11-02"
    d2.mkdir()
    (d2 / "numbers.html").write_text("<html>")
    (d2 / "REBUILT.txt").write_text("2026-11-08 18:15 현재 코드(abc)·확정 데이터로 다시 만듦; 원본은 x\n"
                                    "commit: abcdef0123\nfinalize: verified\n", "utf-8")
    (d2 / "FINAL.txt").write_text("[2026-11-08 18:15:00] 재구성 — …\n", "utf-8")
    d5 = root / "2026-11-05"
    d5.mkdir()
    (d5 / "INCOMPLETE.txt").write_text("[2026-11-05 18:10:02] 2026-11-05 수집 미완 — …\n", "utf-8")
    arch = root / ".archive"
    arch.mkdir()
    (arch / "2026-11-02.20261108181500").mkdir()
    return root


def test_backfill_seeds_from_relics_with_provenance(relics):
    rc, msg = ol.backfill(relics, [relics / "cron.log"])
    assert rc == 0 and "백필" in msg
    evs = list(ol.iter_events(relics))
    assert evs and all(e.get("source") == "backfill" for e in evs), "출처 표시가 없다 — 통계가 복원을 기록인 척 센다"
    # cron.log: 잠정(18:12 KST = 09:12Z)과 확정(08:22 KST = 전날 23:22Z) — 시간대 변환이 맞아야 SLO 판정이 선다.
    prov = ol.first_ts([e for e in evs if e.get("asof") == "2026-11-03"], ol.is_prov_publication)
    fin = ol.first_ts([e for e in evs if e.get("asof") == "2026-11-03"], ol.is_final_confirmation)
    assert prov == datetime(2026, 11, 3, 9, 12, 21, tzinfo=timezone.utc)
    assert fin == datetime(2026, 11, 3, 23, 22, 41, tzinfo=timezone.utc)
    kinds = {(e.get("asof"), e.get("outcome", e.get("event"))) for e in evs}
    assert ("2026-11-05", "incomplete") in kinds
    assert ("2026-11-02", "rebuilt") in kinds and ("2026-11-02", "archive") in kinds
    rebuilt = next(e for e in evs if e.get("outcome") == "rebuilt")
    assert rebuilt["commit"] == "abcdef0123" and rebuilt["ts"] == "2026-11-08T09:15:00Z"


def test_backfill_refuses_to_double_seed(relics):
    assert ol.backfill(relics, [relics / "cron.log"])[0] == 0
    n = len(list(ol.iter_events(relics)))
    rc, msg = ol.backfill(relics, [relics / "cron.log"])
    assert rc == 1 and "--force" in msg and len(list(ol.iter_events(relics))) == n
    assert ol.backfill(relics, [relics / "cron.log"], force=True)[0] == 0
    assert len(list(ol.iter_events(relics))) == 2 * n


def test_cli_backfill_stats_json_and_errata(relics, capsys):
    assert ol.main(["--root", str(relics), "--backfill"]) == 0
    assert ol.main(["--root", str(relics), "--days", "30", "--now", "2026-11-09T00:00:00Z"]) == 0
    out = capsys.readouterr().out
    assert "운영 통계" in out and "백필" in out
    assert ol.main(["--root", str(relics), "--days", "30", "--json", "--now", "2026-11-09T00:00:00Z"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["backfill_events"] > 0 and s["prov"]["on_time"] >= 1
    assert ol.main(["--root", str(relics), "--errata"]) == 0
    assert "2026-11-02  재구성" in capsys.readouterr().out


# mutation-exempt: 빈 원장의 안내문 한 줄 — 분기가 print 뿐이라 주입으로 증명할 버그가 없다(통계·백필 변이가 본문을 본다)
def test_cli_errata_empty_ledger_says_so(tmp_path, capsys):
    assert ol.main(["--root", str(tmp_path), "--errata"]) == 0
    assert "정정 없음" in capsys.readouterr().out


# ── freshness_check 의 SLO 점검 — 늦은 기록이 있을 때만 경고 ──────────────────────

def test_slo_check_warns_on_late_provisional(tmp_path):
    ol.append_event(tmp_path, _report("2026-11-04T10:12:00Z", "2026-11-04", "published"))   # 19:12 KST
    rc, lines = fc.check_slo(tmp_path, "2026-11-04")
    assert rc == fc.WARN and any("잠정 리포트가 기한" in x for x in lines)


def test_slo_check_warns_on_late_final(tmp_path):
    ol.append_event(tmp_path, _report("2026-11-04T09:12:00Z", "2026-11-04", "published"))
    ol.append_event(tmp_path, _report("2026-11-08T09:14:00Z", "2026-11-04", "final", mode="final"))
    rc, lines = fc.check_slo(tmp_path, "2026-11-04")
    assert rc == fc.WARN and any("확정이 기한" in x for x in lines)


def test_slo_check_is_quiet_when_on_time_or_unrecorded(tmp_path):
    assert fc.check_slo(tmp_path, None) == (0, [])
    rc, lines = fc.check_slo(tmp_path, "2026-11-04")                 # 원장이 비었다 — 위반이 아니라 측정 불가
    assert rc == 0 and any("기록이 없다" in x for x in lines)
    ol.append_event(tmp_path, _report("2026-11-04T09:12:00Z", "2026-11-04", "published"))
    ol.append_event(tmp_path, _report("2026-11-04T23:25:00Z", "2026-11-04", "final", mode="final"))
    rc, lines = fc.check_slo(tmp_path, "2026-11-04")
    assert rc == 0 and any("기한 안이다" in x for x in lines)


def test_freshness_main_appends_its_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("SECTOR_FLOW_ALERT", str(tmp_path / "없는-alert.sh"))
    rc = fc.main(["--root", str(tmp_path), "--today", "2026-11-07"])  # 토요일 — DB 없이 돈다
    assert rc == fc.WARN                                              # alert.sh 없음
    evs = [e for e in ol.iter_events(tmp_path) if e.get("event") == "freshness"]
    assert len(evs) == 1 and evs[0]["today"] == "2026-11-07" and evs[0]["rc"] == fc.WARN


# ── 셸 쪽 — daily_report.sh·cron_run.sh·serve.sh·_ledger.sh ─────────────────────

from tests import test_daily_report_flow as drf  # noqa: E402 — 같은 샌드박스(가짜 uv·sqlite)를 쓴다

_env, _run, _seed = drf._env, drf._run, drf._seed


@pytest.fixture
def repo(tmp_path):
    """test_daily_report_flow 와 같은 임시 레포 — 그 fixture 는 모듈 밖에서 못 쓰므로 같은 구성으로 만든다."""
    r = tmp_path / "sector-flow"
    (r / "scripts").mkdir(parents=True)
    for name in ("daily_report.sh", "_env.sh", "_ledger.sh", "cron_run.sh", "completeness_gate.py",
                 "finalize_done.py", "serve.sh", "ops_ledger.py"):
        shutil.copy(REPO / "scripts" / name, r / "scripts" / name)
    (r / "scripts" / "sector_flow.py").write_text(drf.FAKE_SECTOR_FLOW)
    (r / "scripts" / "sector_numbers.py").write_text(drf.FAKE_NUMBERS)
    (r / "scripts" / "verify_report.py").write_text(drf.FAKE_VERIFY)
    (r / "scripts" / "trust_page.py").write_text(drf.FAKE_TRUST)
    (r / ".env").write_text(f"KR_QUANT_DB={tmp_path / 'db.sqlite'}\nKR_QUANT_DIGEST=0\n")
    uv = tmp_path / "uv"
    uv.write_text(f'#!/bin/sh\nshift 5\nexec "{sys.executable}" "$@"\n')
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    return r


def _events(r: Path) -> list[dict]:
    return list(ol.iter_events(r / "reports"))


def test_daily_report_ledger_records_publish_skip_and_gate_block(repo):
    db = repo.parent / "db.sqlite"
    _seed(db, today_kosdaq=1)
    assert _run(repo).returncode == 75                               # 관문 차단
    _seed(db, today_kosdaq=50)
    assert _run(repo).returncode == 0                                # 게시
    assert _run(repo).returncode == 0                                # 건너뜀
    evs = [e for e in _events(repo) if e.get("event") == "report"]
    assert [e["outcome"] for e in evs] == ["incomplete", "published", "skipped"], evs
    pub = evs[1]
    assert pub["asof"] == drf.TODAY and pub["mode"] == "normal" and pub["rc"] == 0
    assert pub["gate"] == "pass" and pub["verify"] == "pass" and pub["final"] is False
    assert evs[0]["gate"] == "blocked" and evs[0]["rc"] == 75
    assert all("T" in e["ts"] and e["ts"].endswith("Z") and "T" in e["started"] for e in evs)


def test_daily_report_ledger_records_a_final_rebuild(repo):
    """확정 모드가 확정치로 다시 만들면 outcome=final·final=true — SLO 의 확정 시각이 이 줄에서 나온다."""
    db = repo.parent / "db.sqlite"
    _seed(db, today_kosdaq=50)
    assert _run(repo).returncode == 0
    (repo / "scripts" / "finalize_done.py").write_text("import sys; sys.exit(0)")   # 확정 확인 성공
    assert _run(repo, KR_QUANT_FINAL="1").returncode == 0
    fin = [e for e in _events(repo) if e.get("event") == "report" and e.get("outcome") == "final"]
    assert len(fin) == 1 and fin[0]["mode"] == "final" and fin[0]["final"] is True


# mutation-exempt: 기록은 EXIT 트랩 안이고 실패는 _ledger.sh 가 삼킨다(그 변이는 test_ledger_sh_rotation 이 잡는다) —
# 여기서는 "원장에 못 써도 리포트가 게시된다" 는 전제 자체가 깨질 때(기록을 트랩 밖 본문으로 옮기는 리팩터)를 위한 회귀다.
def test_daily_report_ledger_never_blocks_the_report(repo):
    """원장(reports/events.jsonl)에 못 써도 리포트는 게시된다 — 기록 실패는 stderr 한 줄뿐이다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    (repo / "reports" / "events.jsonl").mkdir(parents=True)          # 파일이 아니라 폴더 — 모든 append 가 실패한다
    p = subprocess.run([str(repo / "scripts" / "daily_report.sh")], capture_output=True,
                       text=True, env=_env(repo), timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (repo / "reports" / drf.TODAY / "numbers.html").is_file()
    assert "기록하지 못했다" in p.stderr


def test_cron_run_appends_a_job_event(repo):
    (repo / "reports").mkdir(exist_ok=True)
    p = subprocess.run([str(repo / "scripts" / "cron_run.sh"), "rpt", "--", "sh", "-c", "exit 3"],
                       capture_output=True, text=True, timeout=30,
                       env={**os.environ, "SECTOR_FLOW_ALERT": "/없는/alert.sh"})
    assert p.returncode == 3
    jobs = [e for e in _events(repo) if e.get("event") == "job"]
    assert len(jobs) == 1 and jobs[0]["job"] == "rpt" and jobs[0]["rc"] == 3
    assert isinstance(jobs[0]["duration_s"], int) and jobs[0]["duration_s"] >= 0


def test_serve_probe_appends_one_event_while_the_server_is_up(repo, tmp_path):
    """잠금이 잡혀 있고(서버가 떠 있다) /healthz 가 200 이면 probe 사건 한 줄 — SLO 의 웹 가용이 이 줄에서 나온다."""
    (repo / "reports").mkdir(exist_ok=True)
    lock = repo / "reports" / ".serve.lock"
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "curl").write_text('#!/bin/sh\nprintf "ok\\n200"\n')
    (shim / "curl").chmod(0o755)
    (repo / "reports" / ".serve.restarts").write_text("3")
    holder = subprocess.Popen(["flock", str(lock), "sleep", "30"])
    try:
        import time as _t
        deadline = _t.monotonic() + 10
        while subprocess.run(["flock", "-n", str(lock), "true"]).returncode == 0:
            assert _t.monotonic() < deadline, "잠금을 못 잡았다"
            _t.sleep(0.05)
        p = subprocess.run([str(repo / "scripts" / "serve.sh")], capture_output=True, text=True, timeout=30,
                           env={**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}",
                                "SECTOR_FLOW_HOST": "127.0.0.1", "SECTOR_FLOW_PORT": "1"})
    finally:
        holder.terminate()
        holder.wait(timeout=10)
    assert p.returncode == 0, p.stdout + p.stderr
    probes = [e for e in _events(repo) if e.get("event") == "probe"]
    assert probes == [{"ts": probes[0]["ts"], "event": "probe", "status": 200, "ok": True}]
    assert not (repo / "reports" / ".serve.restarts").exists()        # 건강하면 재시작 횟수를 지운다(기존 동작)


def test_ledger_sh_rotation_matches_python_and_failure_is_quiet(tmp_path):
    root = tmp_path / "reports"
    root.mkdir()
    (root / "events.jsonl").write_text("x" * 64 + "\n")
    script = (f'. "{REPO}/scripts/_ledger.sh"\n'
              f'SF_LEDGER_MAX_BYTES=16\n'
              f'sf_ledger_file "{root}"\n'
              f'sf_event_append "{root}" \'{{"event":"t2"}}\'\n'
              f'chmod 0 "{root}" 2>/dev/null\n'
              f'sf_event_append "{root}" \'{{"event":"t3"}}\'\n'
              f'echo "rc=$?"\n'
              f'chmod 755 "{root}"\n')
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stdout.splitlines()[0].endswith("events.jsonl.2"), "상한을 넘겼는데 연속 파일을 안 고른다"
    assert "rc=0" in p.stdout, "기록 실패가 종료코드를 바꿨다 — 원장이 리포트를 죽인다"
    assert "기록하지 못했다" in p.stderr
    assert (root / "events.jsonl.2").read_text().strip() == '{"event":"t2"}'
    assert (root / "events.jsonl").read_text().startswith("x" * 64), "원장을 잘랐다"
