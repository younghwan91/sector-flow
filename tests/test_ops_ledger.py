"""운영 사건 원장(``reports/events.jsonl``) — 추가 전용 쓰기·회전·통계·백필·SLO 점검.

폴더 바꿔 끼우기가 운영 흔적을 지우는 설계라(INCOMPLETE 는 다음 성공이 없애고, mtime 은 재생성마다 리셋,
cron.log 는 5MB 회전) 원장이 없으면 "제때 게시됐나·몇 번 막혔나·숫자가 바뀐 날" 을 셀 수 없다. 여기서는
원장 자체(섞이지 않는 동시 기록, 자르지 않는 회전, 실패해도 리포트를 안 죽임)와 그 위의 통계·SLO 판정을 본다.

시각은 전부 시간대를 박은 ISO 문자열이다 — ``time.mktime``·``localtime`` 금지(머신 시간대에 따라 판정이 민다).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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


def test_reader_counts_what_it_skipped_and_stats_say_so(tmp_path):
    """옛 serve.sh 의 "status":000 은 JSON 이 아니다 — 조용히 버리면 실패 프로브가 사라져 5회 중 2회 실패가 100% 가 됐다."""
    (tmp_path / "events.jsonl").write_text(
        '{"ts":"2026-11-08T00:00:00Z","event":"probe","status":200,"ok":true}\n'
        '{"ts":"2026-11-08T00:05:00Z","event":"probe","status":000,"ok":false}\n'
        '[1, 2]\n', "utf-8")
    broken: list[str] = []
    events = list(ol.iter_events(tmp_path, broken=broken))
    assert len(events) == 1 and broken == ["events.jsonl:2", "events.jsonl:3"]
    s = ol.stats(events, 30, now=NOW, broken=broken)
    assert s["broken_lines"] == broken
    assert "못 읽은 원장 줄 2개" in ol.render(s)


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
    _report("2026-11-07T02:00:00Z", "2026-11-02", "rebuilt", mode="rebuild", commit="abc123", final=True,
            source="backfill", origin="marker"),
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
    assert s["prov"]["no_record"] == ["2026-11-02"]                   # 복원된 재구성만 남은 날
    assert s["prov"]["unpublished"] == []
    assert s["final"]["by_target"] == 1
    assert [r["asof"] for r in s["final"]["by_slo"]] == ["2026-11-04"]
    assert [r["asof"] for r in s["final"]["late"]] == ["2026-11-05"]
    assert s["final"]["missing"] == ["2026-11-06"]
    # 재구성의 final=true 를 확정 시각으로 세면 흔적이 지워진 날이 전부 "확정 지연" 이 된다 — 따로 센다.
    assert s["final"]["rebuilt_only"] == ["2026-10-30"]
    assert s["gate_blocked_days"] == ["2026-11-05"]
    assert s["verify_failed_days"] == ["2026-11-05"]
    assert s["final_diff_unknown"] == ["2026-11-05"]
    assert (s["probe"]["n"], s["probe"]["ok"], s["probe"]["share"]) == (2, 1, 0.5)
    assert s["job_failures"] == {"sector-flow-report rc=1": 1}
    assert s["freshness"] == {"n": 1, "warn": 1, "error": 0}


def test_final_diff_unknown_counts_days_not_events():
    """첫 확정에 기록이 있으면 재시도의 null 은 '비교 불가' 가 아니다. 검증에 걸린 기록 뒤의 null 은 믿지 않아 비운 것이다."""
    def ev(asof, hh, diff, verify="pass"):
        return {"asof": asof, "outcome": "final", "ts": f"2026-11-09T{hh:02d}:00:00Z", "diff": diff, "verify": verify}
    reports = [
        ev("2026-11-02", 1, 410), ev("2026-11-02", 2, None),                 # 첫 확정 기록 → 재시도 null: 안다
        ev("2026-11-03", 1, 415, "fail"), ev("2026-11-03", 2, 410),           # 옛 코드 실패 → 이어받음: 안다
        ev("2026-11-04", 1, 415, "fail"), ev("2026-11-04", 2, None),          # 걸린 기록을 안 이음: 모른다
        ev("2026-11-05", 1, None),                                            # 처음부터 없음: 모른다
        ev("2026-11-06", 1, 415, "fail"),                                     # 걸렸지만 숫자는 있다(verify_failed_days 가 센다)
        {"asof": "2026-10-30", "outcome": "rebuilt", "ts": "2026-11-09T01:00:00Z", "diff": None},
    ]
    assert ol.final_diff_unknown(reports) == ["2026-11-04", "2026-11-05"]
    # 순서가 뒤섞여도 시각으로 본다.
    assert ol.final_diff_unknown([ev("2026-11-04", 2, None), ev("2026-11-04", 1, 415, "fail")]) == ["2026-11-04"]
    assert ol.final_diff_unknown([ev("2026-11-07", 2, 3, "fail"), ev("2026-11-07", 1, None)]) == []


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


# ── SLO 분모 — 거래일이다(원장에 나온 날이 아니다) ─────────────────────────────────

_LIVE = _ev("2026-11-02T00:00:00Z", event="probe", status=200, ok=True)     # 원장이 이때부터 돌고 있었다


def _prov(events, now=NOW, days=30):
    return ol.stats([_LIVE, *events], days, now=now)["prov"]


def test_trading_day_with_no_events_is_unpublished_not_absent():
    """18:10·19:10 이 다 실패해 게시가 없거나 크론이 안 돌면 원장에 그날이 안 나오거나 게시가 없다 — 분모에서 빠지면 "놓친 날 없음" 이다."""
    now = datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc)
    p = _prov([_report("2026-11-03T09:12:00Z", "2026-11-03", "published", verify="pass"),
               _report("2026-11-04T09:12:00Z", "2026-11-04", "incomplete", gate="blocked", rc=75),
               _ev("2026-11-05T09:12:00Z", event="report", asof=None, mode="normal", outcome="error", rc=1)], now=now)
    assert p["on_time"] == 1
    assert {r["asof"]: r["why"] for r in p["unpublished"]} == {
        "2026-11-02": "실행 기록 없음", "2026-11-04": "incomplete×1", "2026-11-05": "기준일 못 읽음×1",
        "2026-11-06": "실행 기록 없음"}


def test_denominator_skips_weekends_and_calendar_holidays():
    now = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)           # 10-05·10-09 휴장, 10-10·11 주말
    s = ol.stats([_ev("2026-10-01T00:00:00Z", event="probe", status=200, ok=True)], 11, now=now)
    assert [r["asof"] for r in s["prov"]["unpublished"]] == ["2026-10-01", "2026-10-02", "2026-10-06",
                                                             "2026-10-07", "2026-10-08"]
    assert s["prov"]["pending"] == ["2026-10-12"] and s["trading_days"] == 6


def test_days_before_the_ledger_are_not_called_unpublished():
    s = ol.stats([_LIVE], 30, now=datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc))
    assert "2026-10-30" in s["prov"]["before_ledger"] and s["prov"]["unpublished"] == [
        {"asof": "2026-11-02", "why": "실행 기록 없음"}]


def test_today_before_its_deadline_is_pending():
    now = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)            # 11-03 17:00 KST
    assert "2026-11-03" in _prov([], now=now)["pending"]


def test_first_publication_ignores_mode():
    """관문에 막힌 날의 첫 게시가 다음날 08:25 확정 모드면 "제때" 가 아니다 — normal 만 보면 그날이 안 잡혔다."""
    now = datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc)
    p = _prov([_report("2026-11-03T09:12:00Z", "2026-11-03", "incomplete", gate="blocked", rc=75),
               _report("2026-11-03T23:25:00Z", "2026-11-03", "final", mode="final", verify="pass")], now=now)
    assert [r["asof"] for r in p["late"]] == ["2026-11-03"] and "11-04 08:25" in p["late"][0]["at"]


def test_on_time_and_verified_are_counted_separately():
    """제때 게시됐지만 검증에 걸린 날은 "제때" 이지 "제때·검증 통과" 가 아니다 — 뒤늦은 통과로 메워지지도 않는다."""
    now = datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc)
    p = _prov([_report("2026-11-03T09:12:00Z", "2026-11-03", "published", verify="fail"),
               _report("2026-11-03T10:12:00Z", "2026-11-03", "published", verify="pass"),
               _report("2026-11-04T09:12:00Z", "2026-11-04", "published", verify="pass")], now=now)
    assert p["on_time"] == 2 and p["on_time_verified"] == 1


def test_a_live_rebuild_is_a_late_publication_but_a_restored_one_is_not():
    now = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)
    live = _prov([_report("2026-11-06T01:00:00Z", "2026-11-03", "rebuilt", mode="rebuild", final=True)], now=now)
    assert [r["asof"] for r in live["late"]] == ["2026-11-03"]
    old = ol.stats([_ev("2026-11-08T00:00:00Z", event="probe", status=200, ok=True),
                    _report("2026-11-09T01:00:00Z", "2026-11-03", "rebuilt", mode="rebuild", final=True)], 30, now=now)
    assert old["prov"]["no_record"] == ["2026-11-03"] and old["prov"]["late"] == []


# ── 프로브 — 표본·범위·공백을 말한다 ────────────────────────────────────────────

def _probes(start: datetime, n: int, step_min: int = 5, status=200) -> list[dict]:
    return [_ev((start + timedelta(minutes=i * step_min)).strftime("%Y-%m-%dT%H:%M:%SZ"), event="probe",
                status=status, ok=status == 200) for i in range(n)]


def test_a_few_hours_of_probes_do_not_read_as_thirty_days():
    """프로브 48회(약 4시간)가 "최근 30일 100%" 로 적혔다 — 범위와 창 대비 비율을 같이 낸다."""
    first = datetime(2026, 11, 8, 20, 0, tzinfo=timezone.utc)
    now = first + timedelta(minutes=47 * 5)
    s = ol.stats(_probes(first, 48), 30, now=now)
    pr = s["probe"]
    assert pr["n"] == 48 and pr["span_hours"] == 3.9 and pr["window_coverage"] < 0.01
    text = ol.render(s)
    assert "표본 48회" in text and "약 3.9시간" in text and "창의 0.5%" in text


def test_probe_gaps_are_counted():
    """서버가 내려가 있던 주기는 줄 자체가 없다 — 응답률 100% 뒤에 숨는다."""
    t0 = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
    evs = _probes(t0, 3) + _probes(t0 + timedelta(minutes=40), 3)    # 00:10 → 00:40: 5칸 빠졌다
    now = t0 + timedelta(minutes=50)
    pr = ol.stats(evs, 30, now=now)["probe"]
    assert (pr["gaps"], pr["gap_cycles"], pr["max_gap_min"], pr["tail_gap_min"]) == (1, 5, 30, 0)
    assert pr["share"] == 1.0


def test_silence_up_to_now_is_a_gap():
    t0 = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
    pr = ol.stats(_probes(t0, 2), 30, now=t0 + timedelta(minutes=65))["probe"]
    assert (pr["gaps"], pr["gap_cycles"], pr["tail_gap_min"]) == (1, 11, 60)


def test_unreached_probes_are_failures_with_a_count():
    t0 = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
    pr = ol.stats(_probes(t0, 3) + _probes(t0 + timedelta(minutes=15), 2, status=None), 30,
                  now=t0 + timedelta(minutes=25))["probe"]
    assert (pr["n"], pr["ok"], pr["unreached"], pr["share"]) == (5, 3, 2, 0.6)


def test_a_5xx_answer_failed_but_it_was_reached():
    """5xx 는 서버가 대답한 실패다. '닿지 못함' 에 섞으면 프로세스가 죽은 것과 앱 오류가 안 갈린다 — 대응이 다르다."""
    t0 = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
    pr = ol.stats(_probes(t0, 2) + _probes(t0 + timedelta(minutes=10), 2, status=503)
                  + _probes(t0 + timedelta(minutes=20), 1, status=None), 30, now=t0 + timedelta(minutes=25))["probe"]
    assert (pr["n"], pr["ok"], pr["unreached"]) == (5, 2, 1)


# ── 정오표 — 2번째 게시부터, 게시 하나마다 한 줄 ──────────────────────────────────

def _rows(*events):
    return [(r["kind"], r["ts"]) for r in ol.errata_rows(list(events))]


def test_force_republish_with_new_numbers_is_an_erratum():
    a = _report("2026-11-03T09:12:00Z", "2026-11-03", "published", verify="pass", payload_sha="a" * 64)
    b = _report("2026-11-03T12:00:00Z", "2026-11-03", "published", verify="pass", payload_sha="b" * 64)
    assert _rows(a, b) == [("재게시", "2026-11-03T12:00:00Z")]
    assert "aaaaaaaa → bbbbbbbb" in ol.errata_rows([a, b])[0]["detail"]


def test_republish_with_the_same_numbers_is_not_an_erratum():
    a = _report("2026-11-03T09:12:00Z", "2026-11-03", "published", verify="pass", payload_sha="a" * 64)
    b = _report("2026-11-03T12:00:00Z", "2026-11-03", "published", verify="pass", payload_sha="a" * 64)
    assert _rows(a, b) == []


def test_regeneration_after_a_failed_verification_is_named():
    a = _report("2026-11-03T09:12:00Z", "2026-11-03", "published", verify="fail", payload_sha="a" * 64)
    b = _report("2026-11-03T10:12:00Z", "2026-11-03", "published", verify="pass", payload_sha="b" * 64)
    assert _rows(a, b) == [("검증 실패 뒤 재생성", "2026-11-03T10:12:00Z")]


def test_republish_without_a_hash_says_it_cannot_tell():
    a = _report("2026-11-03T09:12:00Z", "2026-11-03", "published")
    b = _report("2026-11-03T12:00:00Z", "2026-11-03", "published")
    rows = ol.errata_rows([a, b])
    assert rows[0]["kind"] == "재게시" and "모른다" in rows[0]["detail"]


def test_first_final_is_judged_by_its_diff_and_the_next_final_is_a_republish():
    """첫 확정은 원래 숫자가 바뀌는 자리라 칸 차이로 보고, 그 뒤의 확정 재생성(FORCE)은 2번째 게시다."""
    a = _report("2026-11-03T09:12:00Z", "2026-11-03", "published", payload_sha="a" * 64)
    f0 = _report("2026-11-03T23:25:00Z", "2026-11-03", "final", mode="final", diff=0, payload_sha="b" * 64)
    f1 = _report("2026-11-04T03:00:00Z", "2026-11-03", "final", mode="final", diff=0, payload_sha="c" * 64)
    assert _rows(a, f0) == [] and _rows(a, f0, f1) == [("재게시", "2026-11-04T03:00:00Z")]


def test_two_rebuilds_of_one_day_are_two_rows_with_times(relics, capsys):
    a = _report("2026-11-07T02:00:00Z", "2026-11-02", "rebuilt", mode="rebuild", commit="aaa")
    b = _report("2026-11-08T02:00:00Z", "2026-11-02", "rebuilt", mode="rebuild", commit="bbb")
    assert _rows(a, b) == [("재구성", "2026-11-07T02:00:00Z"), ("재구성", "2026-11-08T02:00:00Z")]
    ol.append_event(relics, a)
    ol.append_event(relics, b)
    assert ol.main(["--root", str(relics), "--errata"]) == 0
    out = capsys.readouterr().out
    assert "2026-11-07 11:00 KST" in out and "2026-11-08 11:00 KST" in out


def test_backfill_traces_of_one_rebuild_merge_into_one_row(relics):
    """표시 파일(REBUILT.txt)과 .archive 는 같은 재구성의 흔적이다 — 시각을 넣어도 두 줄로 불어나면 안 된다."""
    assert ol.backfill(relics, [relics / "cron.log"])[0] == 0
    rows = [r for r in ol.errata_rows(list(ol.iter_events(relics))) if r["asof"] == "2026-11-02"]
    assert [r["kind"] for r in rows] == ["재구성"] and rows[0]["detail"].startswith("commit abcdef")


def test_marker_restored_publications_are_not_republications():
    a = {**_report("2026-11-03T09:12:00Z", "2026-11-03", "published"), "source": "backfill", "origin": "cron.log"}
    m = {**_report("2026-11-03T10:00:00Z", "2026-11-03", "published", verify="fail"),
         "source": "backfill", "origin": "marker"}
    assert ol.errata_rows([a, m]) == []


# ── 관문 도입 전 — 통과가 아니다 ────────────────────────────────────────────────

def test_backfill_before_the_gate_existed_has_no_gate_result(tmp_path):
    """관문은 2026-11-02 19:07 에 생겼다. 10-27 의 부분 수집을 gate:"pass" 로 복원하면 관문이 통과시킨 것처럼 읽힌다."""
    log = tmp_path / "cron.log"
    log.write_text("[2026-10-27 18:10:03] 생성 시작 — 기준일 2026-10-27, 구간 260거래일\n"
                   "[2026-10-27 18:12:21] 완료 — /x\n"
                   "[2026-11-02 18:15:26] 생성 시작 — 기준일 2026-11-02, 구간 260거래일\n"
                   "[2026-11-02 18:15:27] 완료 — /x\n"
                   "[2026-11-03 18:10:03] 생성 시작 — 기준일 2026-11-03, 구간 260거래일\n"
                   "[2026-11-03 18:12:21] 완료 — /x\n", "utf-8")
    got = {e["asof"]: e for e in ol.backfill_from_cron_log(log)}
    assert got["2026-10-27"]["gate"] is None and got["2026-10-27"]["gate_note"] == ol.GATE_NOTE
    assert got["2026-11-02"]["gate"] is None, "19:07 관문 커밋보다 앞선 18:15 게시다"
    assert got["2026-11-03"]["gate"] == "pass"


def test_old_backfill_lines_are_read_without_their_false_pass(tmp_path):
    """이미 운영 원장에 들어간 줄은 고치지 않는다(덧붙이기 전용) — 읽을 때 푼다. 파일은 그대로다."""
    line = ('{"ts":"2026-10-27T09:12:00Z","event":"report","asof":"2026-10-27","mode":"normal","outcome":"published",'
            '"gate":"pass","source":"backfill","origin":"cron.log"}\n')
    keep = ('{"ts":"2026-11-03T09:12:00Z","event":"report","asof":"2026-11-03","mode":"normal","outcome":"published",'
            '"gate":"pass","source":"backfill","origin":"cron.log"}\n')
    live = '{"ts":"2026-10-28T09:12:00Z","event":"report","asof":"2026-10-28","outcome":"published","gate":"pass"}\n'
    (tmp_path / "events.jsonl").write_text(line + keep + live, "utf-8")
    evs = list(ol.iter_events(tmp_path))
    assert evs[0]["gate"] is None and evs[0]["gate_note"] == ol.GATE_NOTE
    assert evs[1]["gate"] == "pass" and evs[2]["gate"] == "pass"
    assert (tmp_path / "events.jsonl").read_text("utf-8") == line + keep + live
    s = ol.stats(evs, 30, now=datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc))
    assert s["gate_before_intro_days"] == ["2026-10-27"]
    assert "관문 도입 전 게시 1일" in ol.render(s)


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
    assert rc == 0 and any("도입 전" in x for x in lines)
    ol.append_event(tmp_path, _report("2026-11-04T09:12:00Z", "2026-11-04", "published"))
    ol.append_event(tmp_path, _report("2026-11-04T23:25:00Z", "2026-11-04", "final", mode="final"))
    rc, lines = fc.check_slo(tmp_path, "2026-11-04")
    assert rc == 0 and any("기한 안이다" in x for x in lines)


def test_slo_check_calls_a_day_with_no_publication_unpublished(tmp_path):
    """18:10·19:10 이 다 막힌 날이 rc 0 "기한 안이다" 였다."""
    ol.append_event(tmp_path, _report("2026-11-04T09:12:00Z", "2026-11-04", "incomplete", gate="blocked", rc=75))
    rc, lines = fc.check_slo(tmp_path, "2026-11-04")
    assert rc == fc.WARN and "미게시" in lines[0] and "incomplete×1" in lines[0], lines


def test_slo_check_sees_db_outages_with_no_asof(tmp_path):
    ol.append_event(tmp_path, _ev("2026-11-03T00:00:00Z", event="probe", status=200, ok=True))
    ol.append_event(tmp_path, _ev("2026-11-04T09:12:00Z", event="report", asof=None, outcome="error", rc=1))
    rc, lines = fc.check_slo(tmp_path, "2026-11-04")
    assert rc == fc.WARN and "미게시" in lines[0] and "기준일 못 읽음×1" in lines[0], lines


def test_slo_check_flags_a_first_publication_by_the_morning_final(tmp_path):
    """관문 차단 뒤 다음날 08:25 첫 게시가 "기한 안이다" 였다 — normal 모드 게시만 봤다."""
    ol.append_event(tmp_path, _report("2026-11-04T09:12:00Z", "2026-11-04", "incomplete", gate="blocked", rc=75))
    ol.append_event(tmp_path, _report("2026-11-04T23:25:00Z", "2026-11-04", "final", mode="final"))
    rc, lines = fc.check_slo(tmp_path, "2026-11-04")
    assert rc == fc.WARN and any("잠정 리포트가 기한" in x and "11-05 08:25" in x for x in lines), lines
    assert not any("기한 안이다" in x for x in lines)


def test_slo_check_treats_a_silent_day_as_a_ledger_gap(tmp_path):
    """원장이 돌고 있었는데 그 거래일 실행이 없다 — 크론이 안 돌았거나 쓰기가 실패했다. "재지 않는다" 로 넘기면 공백이 조용하다."""
    ol.append_event(tmp_path, _ev("2026-11-03T00:00:00Z", event="probe", status=200, ok=True))
    rc, lines = fc.check_slo(tmp_path, "2026-11-04")
    assert rc == fc.WARN and "[원장]" in lines[0] and "실행 기록이 원장에 없다" in lines[0], lines


def test_slo_check_mentions_unreadable_lines(tmp_path):
    (tmp_path / "events.jsonl").write_text('{"ts":"2026-11-04T09:12:00Z","ev\n', "utf-8")
    assert any("못 읽은 줄 1개" in x for x in fc.check_slo(tmp_path, "2026-11-04")[1])


def test_todays_silence_in_the_ledger_warns(tmp_path):
    """게시는 됐는데 원장 쓰기만 실패한 날 — latest 는 멀쩡해 신선도 점검이 못 본다."""
    now = datetime(2026, 11, 6, 20, 30, tzinfo=fc.KST)
    assert fc.check_ledger_today(tmp_path, "2026-11-06", now) == (0, [])     # 원장 도입 전
    ol.append_event(tmp_path, _ev("2026-11-05T00:00:00Z", event="probe", status=200, ok=True))
    rc, lines = fc.check_ledger_today(tmp_path, "2026-11-06", now)
    assert rc == fc.WARN and "오늘(2026-11-06) 18:10 뒤 실행 기록" in lines[0]
    assert fc.check_ledger_today(tmp_path, "2026-10-09", datetime(2026, 11, 6, 20, 30, tzinfo=fc.KST))[0] == 0  # 휴장
    assert fc.check_ledger_today(tmp_path, "2026-11-06", datetime(2026, 11, 6, 17, 0, tzinfo=fc.KST))[0] == 0   # 아직 전
    ol.append_event(tmp_path, _report("2026-11-06T09:12:00Z", "2026-11-06", "skipped"))
    assert fc.check_ledger_today(tmp_path, "2026-11-06", now) == (0, [])


def test_probe_silence_warns_and_a_past_gap_is_a_note(tmp_path):
    now = datetime(2026, 11, 6, 11, 30, tzinfo=timezone.utc)
    assert fc.check_probes(tmp_path, now) == (0, [])                  # 표본이 없다 — 말할 것 없다
    ol.append_event(tmp_path, _ev("2026-11-06T09:00:00Z", event="probe", status=200, ok=True))
    rc, lines = fc.check_probes(tmp_path, now)
    assert rc == fc.WARN and "150분째 없다" in lines[0]
    for t in ("11:00", "11:05", "11:10", "11:15", "11:20", "11:25", "11:30"):
        ol.append_event(tmp_path, _ev(f"2026-11-06T{t}:00Z", event="probe", status=200, ok=True))
    rc, lines = fc.check_probes(tmp_path, now)
    assert rc == 0 and "공백 1곳" in lines[0]


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
                 "finalize_done.py", "serve.sh", "ops_ledger.py", "krx_calendar.py"):
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


def test_daily_report_ledger_records_what_it_published(repo):
    """게시한 payload 의 지문 — 같은 기준일 재게시에서 숫자가 바뀌었는지를 이것으로 가른다(--errata)."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    assert _run(repo).returncode == 0
    pub = next(e for e in _events(repo) if e.get("outcome") == "published")
    want = hashlib.sha256((repo / "reports" / drf.TODAY / "payload.json").read_bytes()).hexdigest()
    assert pub["payload_sha"] == want
    assert _run(repo).returncode == 0
    assert next(e for e in _events(repo) if e.get("outcome") == "skipped")["payload_sha"] is None


def test_inherited_build_variable_is_not_deleted_by_the_trap(repo):
    """EXIT 트랩이 $BUILD 를 rm -rf 한다 — 본문이 정하기 전에 끝나면(건너뜀·잠금·DB 실패) 환경에서 물려받은 값을 지웠다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    victim = repo.parent / "남의폴더"
    victim.mkdir()
    (victim / "소중한것").write_text("x")
    assert _run(repo).returncode == 0                                 # 먼저 게시
    assert _run(repo, BUILD=str(victim)).returncode == 0              # 이번 실행은 건너뛰고 끝난다
    assert (victim / "소중한것").exists(), "환경변수 BUILD 가 가리키는 폴더를 지웠다"


def test_every_variable_the_exit_trap_reads_is_set_before_the_trap():
    """같은 부류의 검사 — 트랩이 읽는 변수는 전부 트랩 전에 정한다(환경 상속 금지). 새 변수를 트랩에 넣고 초기화를 잊으면 빨개진다."""
    src = (REPO / "scripts" / "daily_report.sh").read_text("utf-8")
    body = src[src.index("_dr_event() {"):src.index("\n}\n", src.index("_dr_event() {"))]
    before = src[:src.index("trap '_dr_event $?' EXIT")]
    names = set(re.findall(r"\$\{?([A-Z][A-Z0-9_]+)", body)) - {"KR_QUANT_CODE_REV"}   # 환경에서 받는 것이 목적인 값
    assert {"BUILD", "EV_OUTCOME", "EV_SHA", "OUT_ROOT"} <= names, "트랩을 못 읽었다 — 이 검사가 빈손이다"
    unset = sorted(n for n in names if not re.search(rf"(^|[\s;]){n}=", before, re.M))
    assert not unset, f"트랩이 읽는데 트랩 전에 안 정한 변수: {unset}"


def test_rm_rf_targets_in_shell_scripts_are_assigned_first():
    """rm -rf "$X" 의 X 가 그 줄 앞에 대입되지 않으면 환경값을 지운다."""
    for sh in sorted((REPO / "scripts").glob("*.sh")):
        src = sh.read_text("utf-8")
        for m in re.finditer(r'rm -rf? "\$\{?([A-Za-z_]+)', src):
            assert re.search(rf"(^|[\s;]){m.group(1)}=", src[:m.start()], re.M), (sh.name, m.group(1))


def test_timeout_term_is_recorded_as_a_signal_not_as_success(repo):
    """크론 timeout 의 TERM 으로 끝난 실행이 rc:0, outcome:error 로 남았다 — 트랩의 $? 는 직전 명령의 것이다."""
    _seed(repo.parent / "db.sqlite", today_kosdaq=50)
    (repo / "scripts" / "sector_flow.py").write_text("import time\ntime.sleep(30)\n")
    p = subprocess.run(["timeout", "-k", "5s", "3s", str(repo / "scripts" / "daily_report.sh")],
                       capture_output=True, text=True, env=_env(repo), timeout=60)
    assert p.returncode == 124, p.stdout + p.stderr
    last = [e for e in _events(repo) if e.get("event") == "report"][-1]
    assert last["rc"] == 143 and last["outcome"] == "error", last
    assert not list((repo / "reports").glob("*.building.*")), "끊긴 실행의 짓던 폴더가 남았다"


def test_serve_probe_that_cannot_reach_writes_null_and_stays_readable(repo, tmp_path):
    """닿지 못하면 curl 은 000 을 준다 — "status":000 은 JSON 이 아니라 그 줄이 사라졌다(SIGSTOP 재현에서 5회 중 2회)."""
    (repo / "reports").mkdir(exist_ok=True)
    lock = repo / "reports" / ".serve.lock"
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "curl").write_text('#!/bin/sh\nprintf "curl: (28) timed out\\n000"\n')
    (shim / "curl").chmod(0o755)
    holder = subprocess.Popen(["flock", str(lock), "sleep", "30"])
    try:
        import time as _t
        deadline = _t.monotonic() + 10
        while subprocess.run(["flock", "-n", str(lock), "true"]).returncode == 0:
            assert _t.monotonic() < deadline, "잠금을 못 잡았다"
            _t.sleep(0.05)
        subprocess.run([str(repo / "scripts" / "serve.sh")], capture_output=True, text=True, timeout=30,
                       env={**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}",
                            "SECTOR_FLOW_HOST": "127.0.0.1", "SECTOR_FLOW_PORT": "1"})
    finally:
        holder.terminate()
        holder.wait(timeout=10)
    broken: list[str] = []
    probes = [e for e in ol.iter_events(repo / "reports", broken=broken) if e.get("event") == "probe"]
    assert broken == [] and len(probes) == 1
    assert probes[0]["status"] is None and probes[0]["ok"] is False


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
