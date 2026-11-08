#!/usr/bin/env python
"""운영 사건 원장(``reports/events.jsonl``) — 형식 정본, 추가 전용 쓰기, 30/90일 통계.

폴더 바꿔 끼우기(재구성·재시도)가 운영 흔적을 지운다: INCOMPLETE·VERIFY_FAILED 는 다음 성공이 폴더째
없애고, mtime 은 다시 만들 때마다 리셋되고, cron.log 는 5MB 에서 돈다. 그래서 "며칠이나 제때 게시됐나,
관문이 몇 번 막았나, 게시 뒤 숫자가 바뀐 날이 언제인가" 를 나중에 셀 수 없었다. 이 원장은 그 사건들을
**덧붙이기만** 하는 한 줄 JSON 으로 남긴다. 쓰는 쪽은 daily_report.sh·cron_run.sh·serve.sh(`scripts/_ledger.sh`)와
freshness_check.py(이 모듈의 :func:`append_event`)다. SLO 정의는 ``docs/slo.md``.

형식 — 한 줄 = 사건 하나(JSON 객체). 공통 키 ``ts``(UTC ISO8601, 사건이 끝난 시각)·``event``:

* ``report`` — daily_report.sh 실행 하나의 결말. ``asof``(기준일, 못 읽었으면 null) ·
  ``mode``(``normal``/``final``/``rebuild``) · ``rc`` · ``outcome``(``published`` 잠정 게시 · ``final`` 확정 재생성 ·
  ``rebuilt`` 재구성 · ``incomplete`` 관문 차단 · ``skipped`` 이미 있어 건너뜀 · ``locked`` 잠금 충돌 ·
  ``holiday`` 재구성 대상이 휴장일 · ``error``) · ``gate``(``pass``/``blocked``/``error``/null=안 봄) ·
  ``verify``(``pass``/``fail``/null) · ``verify_checks``/``verify_fails``(VERIFY.txt 요약 줄의 검사·실패 건수) ·
  ``final``(bool — FINAL.txt 를 썼나) · ``diff``(final_diff.json 의 ``n_changed`` — 잠정→확정에서 바뀐 섹터 칸 수,
  비교 불가·해당 없음은 null) · ``commit``(만든 코드) · ``started``(UTC).
* ``job`` — cron_run.sh 가 감싼 크론 줄 하나. ``job``(sector-flow-report 등) · ``rc`` · ``duration_s``.
* ``probe`` — serve.sh 의 5분 /healthz 질의(서버가 떠 있을 때만 — 내려가 있으면 그 주기는 재기동이라 줄이 없다).
  ``status``(HTTP, 닿지 못했으면 null) · ``ok``(bool).
* ``freshness`` — 평일 20:30 점검의 판정. ``today`` · ``rc``.
* 백필(``--backfill``)이 만든 사건은 ``source: "backfill"`` 이 붙고, cron.log(``origin: "cron.log"``)나 폴더
  표시 파일(``origin: "marker"``·``"archive"``)에서 복원한 것이라 시각·필드가 성기다 — 통계는 그 출처를 따로 센다.

규칙 — **절대 자르지 않는다.** 쓰던 파일이 :data:`MAX_BYTES` 를 넘으면 ``events.jsonl.2``, ``.3`` … 으로 이어
쓴다(``.1`` 을 건너뛰는 것은 cron.log 의 "회전 사본 .1" 과 헷갈리지 않기 위해서다). 읽기는 그 순서대로 잇는다.
쓰기는 O_APPEND 에 write 한 번 — 동시 기록이 섞이지 않는다(회전 직전의 한두 줄은 파일 경계와 시각이 어긋날 수
있어 통계는 ``ts`` 로 다시 줄 세운다). 기록 실패는 stderr 한 줄로만 말하고 리포트·점검을 멈추지 않는다.
웹(serve_reports.py)은 날짜 폴더의 허용 목록만 내보내므로 이 원장은 애초에 안 보인다(tests/test_ops_ledger.py 가 지킨다).

사용::

    uv run python scripts/ops_ledger.py                  # 최근 30일 통계
    uv run python scripts/ops_ledger.py --days 90 --json
    uv run python scripts/ops_ledger.py --errata         # 첫 게시 뒤 숫자가 바뀐 날
    uv run python scripts/ops_ledger.py --backfill       # 한 번만: cron.log·폴더 표시에서 과거를 복원(source=backfill)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
REPO = Path(__file__).resolve().parent.parent
#: 이 상한을 넘으면 다음 연속 파일로 — 자르는 회전이 아니다(머리말).
MAX_BYTES = 50 * 1024 * 1024
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ── SLO (docs/slo.md 가 설명의 정본) ─────────────────────────────────────────
#: 잠정 리포트 게시 기한 — 기준일 당일 KST. 18:10 크론 + 빌드 2~5분 + 여유.
PROV_SLO_KST = time(18, 30)
#: 확정 목표 — 다음날 08:40 KST(08:20 크론). 놓치면 10:20 재시도가 있어 SLO 는 10:40.
FINAL_TARGET_KST = time(8, 40)
FINAL_SLO_KST = time(10, 40)

PUBLISHED = ("published", "final", "rebuilt")


# ── 쓰기·읽기 ────────────────────────────────────────────────────────────────

def ledger_files(root: Path) -> list[Path]:
    """시간순 원장 파일 — ``events.jsonl``, ``events.jsonl.2``, … 있는 것만."""
    root = Path(root)
    out = [root / "events.jsonl"]
    n = 2
    while (root / f"events.jsonl.{n}").exists():
        out.append(root / f"events.jsonl.{n}")
        n += 1
    return [f for f in out if f.exists()]


def append_path(root: Path) -> Path:
    """지금 덧붙일 파일 — 마지막 연속 파일, 그것이 상한을 넘었으면 다음 번호(scripts/_ledger.sh 와 같은 규칙)."""
    root = Path(root)
    files = ledger_files(root)
    cur = files[-1] if files else root / "events.jsonl"
    if cur.exists() and cur.stat().st_size >= MAX_BYTES:
        n = 2 if cur.name == "events.jsonl" else int(cur.name.rsplit(".", 1)[1]) + 1
        cur = root / f"events.jsonl.{n}"
    return cur


def append_event(root: Path, obj: dict) -> bool:
    """사건 하나를 한 줄로 덧붙인다 — O_APPEND 에 write 한 번(동시 기록 안전). 실패는 stderr 한 줄, False."""
    try:
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        path = append_path(Path(root))
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except Exception as e:  # noqa: BLE001 — 원장이 리포트·점검을 죽이면 안 된다
        print(f"⚠️ [원장] 운영 사건을 기록하지 못했다({type(e).__name__}) — 리포트·점검은 계속한다. "
              "reports/events.jsonl 의 디스크·권한을 보라(README §3.1).", file=sys.stderr)
        return False


def iter_events(root: Path):
    """원장을 시간순 파일 순서로 읽는다. 깨진 줄(동시 기록 찢김·손 편집)은 건너뛴다 — 원장은 절대 고쳐 쓰지 않는다."""
    for f in ledger_files(Path(root)):
        try:
            text = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                yield e


# ── 시각·SLO 판정 ────────────────────────────────────────────────────────────

def parse_ts(s: str | None) -> datetime | None:
    """원장 ``ts``(``…Z`` 또는 오프셋 붙은 ISO8601) → aware datetime. 못 읽으면 None."""
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prov_deadline(asof: str) -> datetime:
    """잠정 게시 기한 — 기준일 당일 :data:`PROV_SLO_KST`."""
    return datetime.combine(date.fromisoformat(asof), PROV_SLO_KST, KST)


def final_target(asof: str) -> datetime:
    return datetime.combine(date.fromisoformat(asof) + timedelta(days=1), FINAL_TARGET_KST, KST)


def final_deadline(asof: str) -> datetime:
    """확정 기한 — 다음날 :data:`FINAL_SLO_KST` (10:20 재시도까지 본 상한)."""
    return datetime.combine(date.fromisoformat(asof) + timedelta(days=1), FINAL_SLO_KST, KST)


def first_ts(events: list[dict], pred) -> datetime | None:
    """조건을 만족하는 사건 중 가장 이른 ``ts``."""
    hits = [t for e in events if pred(e) and (t := parse_ts(e.get("ts")))]
    return min(hits, default=None)


def is_publication(e: dict) -> bool:
    return e.get("event") == "report" and e.get("outcome") in PUBLISHED


def is_prov_publication(e: dict) -> bool:
    """잠정 게시 — normal 모드의 게시. 백필의 cron.log 복원도 mode 를 추정해 싣는다."""
    return is_publication(e) and e.get("mode") == "normal"


def is_final_confirmation(e: dict) -> bool:
    """확정 **확인**(08:20·10:20 확정 모드) — 재구성은 final=true 여도 아니다: 재구성 시각을 확정 시각으로 세면
    흔적이 지워진 날(2026-11-08 일괄 재구성)이 전부 "확정 지연" 으로 둔갑한다. 그런 날은 따로 센다(:func:`stats`)."""
    return e.get("event") == "report" and (e.get("outcome") == "final"
                                           or (e.get("final") is True and e.get("mode") == "final"))


# ── 통계 ────────────────────────────────────────────────────────────────────

def _hhmm_kst(dt: datetime) -> str:
    return dt.astimezone(KST).strftime("%m-%d %H:%M")


def errata_rows(events: list[dict]) -> list[dict]:
    """첫 게시 뒤 숫자가 바뀐 날 — 재구성(폴더를 바꿔 끼웠다)과 잠정→확정에서 섹터 칸이 바뀐 확정(diff>0).

    diff 가 null 인 확정은 "비교 불가"(섹터 정의가 달라졌다 등)지 "안 바뀜" 이 아니다 — 여기 싣지 않되
    통계의 ``final_diff_unknown`` 으로 센다. 백필 사건은 diff 를 모르므로 재구성만 실린다.
    """
    rows: dict[tuple[str, str], dict] = {}
    for e in events:
        asof = e.get("asof")
        if not (isinstance(asof, str) and DATE_RE.match(asof)):
            continue
        kind = None
        detail = ""
        if e.get("event") == "report" and e.get("outcome") == "rebuilt":
            kind = "재구성"
            detail = f"commit {str(e.get('commit') or '?')[:12]}"
        elif e.get("event") == "archive":
            kind = "재구성"
            detail = "원본 보관(.archive)"
        elif is_final_confirmation(e) and isinstance(e.get("diff"), int) and e["diff"] > 0:
            kind = "확정 정정"
            detail = f"섹터 칸 {e['diff']}개 변경"
        if kind is None:
            continue
        key = (asof, kind)
        t = parse_ts(e.get("ts"))
        if key not in rows or (t and (rows[key]["_ts"] is None or t < rows[key]["_ts"])):
            rows[key] = {"asof": asof, "kind": kind, "detail": detail, "backfill": e.get("source") == "backfill",
                         "ts": e.get("ts"), "_ts": t}
    out = sorted(rows.values(), key=lambda r: (r["asof"], r["kind"]))
    for r in out:
        r.pop("_ts")
    return out


def stats(events: list[dict], days: int, now: datetime | None = None) -> dict:
    """창(``days`` 일) 안의 운영 통계 — 게시 준시성(SLO 대비)·관문 차단·검증 실패·확정 지연·재구성/정정·프로브."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    evs = [e for e in events if (t := parse_ts(e.get("ts"))) and since <= t <= now]
    # 준시성은 **기준일이 창 안**인 날로 센다(사건 시각이 아니라) — 옛 기준일의 최근 재구성 사건이
    # 창에 들어와 그 날을 통계에 끌어들이면, 그날의 잠정 게시(창 밖 과거)가 "기록 없음" 으로 둔갑한다.
    reports = [e for e in events if e.get("event") == "report"
               and isinstance(e.get("asof"), str) and DATE_RE.match(e["asof"])
               and since.strftime("%Y-%m-%d") <= e["asof"] <= now.strftime("%Y-%m-%d")]
    asofs = sorted({e["asof"] for e in reports})

    prov_on, prov_late, prov_none = [], [], []
    fin_target, fin_slo, fin_late, fin_none, fin_rebuilt = [], [], [], [], []
    for a in asofs:
        es = [e for e in reports if e["asof"] == a]
        if not any(is_publication(e) for e in es):
            continue                               # 게시가 없던 날(관문 차단만 등)은 아래 가로줄들이 센다
        p = first_ts(es, is_prov_publication)
        if p is None:
            # 잠정 게시 기록이 없다(재구성만 남은 날·백필 공백) — 확정 준시성도 재지 않는다.
            # 뒤늦은 재구성을 "확정 지각" 으로 세면 복원이 위반으로 둔갑한다.
            prov_none.append(a)
            continue
        if p <= prov_deadline(a):
            prov_on.append(a)
        else:
            prov_late.append({"asof": a, "at": _hhmm_kst(p)})
        f = first_ts(es, is_final_confirmation)
        if f is None:
            if any(e.get("final") is True for e in es):
                fin_rebuilt.append(a)              # 재구성으로만 확정 — 처음 확정된 시각의 기록이 없다
            elif final_deadline(a) < now:
                fin_none.append(a)
        elif f <= final_target(a):
            fin_target.append(a)
        elif f <= final_deadline(a):
            fin_slo.append({"asof": a, "at": _hhmm_kst(f)})
        else:
            fin_late.append({"asof": a, "at": _hhmm_kst(f)})

    gate_dates = sorted({e["asof"] for e in reports if e.get("outcome") == "incomplete" or e.get("gate") == "blocked"})
    verify_dates = sorted({e["asof"] for e in reports if e.get("verify") == "fail"})
    probes = [e for e in evs if e.get("event") == "probe"]
    probe_ok = sum(1 for e in probes if e.get("ok") is True)
    job_fail = Counter(f"{e.get('job')} rc={e.get('rc')}" for e in evs
                       if e.get("event") == "job" and e.get("rc") not in (0, None))
    fresh = [e for e in evs if e.get("event") == "freshness"]
    err = errata_rows(evs)
    return {
        "window_days": days,
        "from": since.strftime("%Y-%m-%d"), "to": now.strftime("%Y-%m-%d"),
        "asof_days": len(asofs),
        "prov": {"on_time": len(prov_on), "late": prov_late, "no_record": prov_none},
        "final": {"by_target": len(fin_target), "by_slo": fin_slo, "late": fin_late, "missing": fin_none,
                  "rebuilt_only": fin_rebuilt},
        "gate_blocked_days": gate_dates,
        "verify_failed_days": verify_dates,
        "final_diff_unknown": sorted({e["asof"] for e in reports
                                      if e.get("outcome") == "final" and e.get("diff") is None}),
        "errata": err,
        "probe": {"n": len(probes), "ok": probe_ok,
                  "share": round(probe_ok / len(probes), 4) if probes else None},
        "job_failures": dict(sorted(job_fail.items())),
        "freshness": {"n": len(fresh), "warn": sum(1 for e in fresh if e.get("rc") == 75),
                      "error": sum(1 for e in fresh if e.get("rc") not in (0, 75, None))},
        "backfill_events": sum(1 for e in evs if e.get("source") == "backfill"),
    }


def render(s: dict) -> str:
    """사람용 요약 — 판정이 아니라 사실 집계다(SLO 위반 알림은 freshness_check 가 보낸다)."""
    L = [f"운영 통계 — 최근 {s['window_days']}일({s['from']} ~ {s['to']}) · 기준일 {s['asof_days']}일"]
    p = s["prov"]
    L.append(f"잠정 게시({PROV_SLO_KST:%H:%M} KST 기한): 제때 {p['on_time']}일 · 늦음 {len(p['late'])}일"
             + (" — " + ", ".join(f"{r['asof']}({r['at']})" for r in p["late"]) if p["late"] else "")
             + (f" · 기록 없음 {len(p['no_record'])}일" if p["no_record"] else ""))
    f = s["final"]
    L.append(f"확정({FINAL_TARGET_KST:%H:%M} 목표, {FINAL_SLO_KST:%H:%M} SLO): 목표 내 {f['by_target']}일 · "
             f"재시도로 SLO 내 {len(f['by_slo'])}일 · SLO 초과 {len(f['late'])}일 · 기한 지나 미확정 {len(f['missing'])}일"
             + (" — 초과: " + ", ".join(f"{r['asof']}({r['at']})" for r in f["late"]) if f["late"] else "")
             + (" — 미확정: " + ", ".join(f["missing"]) if f["missing"] else "")
             + (f" · 재구성으로만 확정 {len(f['rebuilt_only'])}일(처음 확정 시각 기록 없음)"
                if f["rebuilt_only"] else ""))
    L.append("관문 차단(INCOMPLETE): " + (", ".join(s["gate_blocked_days"]) or "없음"))
    L.append("검증 실패: " + (", ".join(s["verify_failed_days"]) or "없음"))
    L.append(f"정정(첫 게시 뒤 숫자 변경): {len(s['errata'])}건" + (" — --errata 로 본다" if s["errata"] else "")
             + (f" · 비교 불가 확정 {len(s['final_diff_unknown'])}일" if s["final_diff_unknown"] else ""))
    pr = s["probe"]
    L.append(f"웹 프로브(5분 /healthz): {pr['n']}회 중 200 OK {pr['ok']}회"
             + (f" ({pr['share']:.1%})" if pr["share"] is not None else "")
             + " — 서버가 내려가 있던 주기는 줄 자체가 없다(docs/slo.md)")
    if s["job_failures"]:
        L.append("잡 비정상 종료: " + ", ".join(f"{k} ×{v}" for k, v in s["job_failures"].items()))
    fr = s["freshness"]
    L.append(f"신선도 점검: {fr['n']}회 · 경고 {fr['warn']} · 오류 {fr['error']}")
    if s["backfill_events"]:
        L.append(f"(이 창의 사건 중 {s['backfill_events']}건은 백필 복원이다 — 시각·필드가 성기다)")
    return "\n".join(L)


# ── 백필 — cron.log·폴더 표시에서 과거를 복원한다(한 번만) ──────────────────────

_CRON_LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.*)$")


def _kst_iso(stamp: str, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """cron.log·표시 파일의 KST 벽시계 → 원장 ``ts``(UTC)."""
    dt = datetime.strptime(stamp, fmt).replace(tzinfo=KST)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def backfill_from_cron_log(path: Path) -> list[dict]:
    """cron.log 의 daily_report 흔적 → report 사건. 상태 기계: ``생성 시작`` 이 열고 ``완료``/``바꿔 끼웠다`` 가 닫는다."""
    out: list[dict] = []
    run: dict | None = None
    try:
        lines = path.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for raw in lines:
        m = _CRON_LINE.match(raw)
        if not m:
            continue
        stamp, msg = m.groups()
        if (d := re.search(r"생성 시작 — 기준일 (\d{4}-\d{2}-\d{2})", msg)):
            run = {"asof": d.group(1), "started": _kst_iso(stamp), "mode": "normal", "verify": None}
        elif run and "확정치로 만든다" in msg:
            run["mode"] = "final"
        elif run and msg.startswith("재구성 — 지금 코드로"):
            run["mode"] = "rebuild"
        elif run and "검증 통과" in msg:
            run["verify"] = "pass"
        elif run and "검증 실패" in msg:
            run["verify"] = "fail"
        elif run and ("완료 — " in msg or "바꿔 끼웠다" in msg or "게시했지만 검증에 걸렸다" in msg):
            outcome = {"normal": "published", "final": "final", "rebuild": "rebuilt"}[run["mode"]]
            out.append({"ts": _kst_iso(stamp), "event": "report", "asof": run["asof"], "mode": run["mode"],
                        "rc": None, "outcome": outcome, "gate": "pass", "verify": run["verify"],
                        "final": run["mode"] == "final", "diff": None, "commit": None,
                        "started": run["started"], "source": "backfill", "origin": "cron.log"})
            run = None
        elif (d := re.search(r"(\d{4}-\d{2}-\d{2}) 수집 미완 — 게시하지 않는다", msg)):
            out.append({"ts": _kst_iso(stamp), "event": "report", "asof": d.group(1),
                        "mode": run["mode"] if run else "normal", "rc": 75, "outcome": "incomplete",
                        "gate": "blocked", "verify": None, "final": False, "diff": None, "commit": None,
                        "source": "backfill", "origin": "cron.log"})
            run = None
    return out


def _marker_ts(folder: Path, name: str, pattern: str, fmt: str) -> tuple[str, str]:
    """표시 파일 첫 줄의 시각(KST) → (ts, 출처). 못 읽으면 mtime(재생성마다 리셋된다 — 그래서 출처를 적는다)."""
    p = folder / name
    try:
        first = p.read_text("utf-8", errors="replace").splitlines()[0]
        if (m := re.search(pattern, first)):
            return _kst_iso(m.group(1), fmt), "text"
    except (OSError, IndexError):
        pass
    try:
        return (datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mtime")
    except OSError:
        return utc_now_iso(), "unknown"


def backfill_from_markers(root: Path) -> list[dict]:
    """날짜 폴더의 표시 파일·``.archive`` → 사건. 확정·재구성은 cron.log 가 돌아 지워진 날도 여기 남는다."""
    out: list[dict] = []
    root = Path(root)
    for d in sorted(p for p in root.iterdir() if p.is_dir() and DATE_RE.match(p.name)) if root.is_dir() else []:
        asof = d.name
        base = {"event": "report", "asof": asof, "rc": None, "commit": None,
                "source": "backfill", "origin": "marker"}
        if (d / "REBUILT.txt").exists():
            ts, how = _marker_ts(d, "REBUILT.txt", r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", "%Y-%m-%d %H:%M")
            commit = None
            for line in (d / "REBUILT.txt").read_text("utf-8", errors="replace").splitlines():
                if line.startswith("commit:"):
                    commit = line.split(":", 1)[1].strip()
            out.append(base | {"ts": ts, "ts_source": how, "mode": "rebuild", "outcome": "rebuilt",
                               "final": (d / "FINAL.txt").exists(), "diff": None, "commit": commit})
        elif (d / "FINAL.txt").exists():
            ts, how = _marker_ts(d, "FINAL.txt", r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", "%Y-%m-%d %H:%M:%S")
            out.append(base | {"ts": ts, "ts_source": how, "mode": "final", "outcome": "final",
                               "final": True, "diff": None})
        if (d / "INCOMPLETE.txt").exists() and not (d / "numbers.html").exists():
            ts, how = _marker_ts(d, "INCOMPLETE.txt", r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]",
                                 "%Y-%m-%d %H:%M:%S")
            out.append(base | {"ts": ts, "ts_source": how, "mode": "normal", "outcome": "incomplete",
                               "gate": "blocked", "final": False, "diff": None})
        if (d / "VERIFY_FAILED.txt").exists():
            ts, how = _marker_ts(d, "VERIFY_FAILED.txt", r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]",
                                 "%Y-%m-%d %H:%M:%S")
            out.append(base | {"ts": ts, "ts_source": how, "mode": "normal", "outcome": "published",
                               "verify": "fail", "final": False, "diff": None})
    arch = root / ".archive"
    if arch.is_dir():
        for p in sorted(arch.iterdir()):
            # 새 꼴 "<기준일>.<YYYYmmddHHMMSS>" 또는 손으로 둔 폴더 안의 날짜 폴더들.
            if (m := re.match(r"^(\d{4}-\d{2}-\d{2})\.(\d{14})$", p.name)):
                out.append({"ts": _kst_iso(m.group(2), "%Y%m%d%H%M%S"), "event": "archive", "asof": m.group(1),
                            "source": "backfill", "origin": "archive"})
            elif p.is_dir():
                for q in sorted(p.iterdir()):
                    if q.is_dir() and DATE_RE.match(q.name):
                        out.append({"ts": datetime.fromtimestamp(q.stat().st_mtime, tz=timezone.utc)
                                    .strftime("%Y-%m-%dT%H:%M:%SZ"), "ts_source": "mtime",
                                    "event": "archive", "asof": q.name, "source": "backfill", "origin": "archive"})
    return out


def backfill(root: Path, cron_logs: list[Path], *, force: bool = False) -> tuple[int, str]:
    """원장을 과거 흔적으로 심는다 — 남아 있는 것(cron.log·표시 파일·.archive)만, 전부 ``source=backfill``.

    이미 백필 사건이 있으면 거부한다(``--force`` 로 덮어 **추가**는 가능 — 원장은 지우지 않는다).
    """
    root = Path(root)
    if not force and any(e.get("source") == "backfill" for e in iter_events(root)):
        return 1, "이미 백필 사건이 있다 — 두 번 심으면 같은 날이 두 배로 센다. 정말 다시면 --force."
    events = []
    for log in cron_logs:
        events += backfill_from_cron_log(log)
    events += backfill_from_markers(root)
    events.sort(key=lambda e: e.get("ts") or "")
    n = sum(1 for e in events if append_event(root, e))
    return 0, f"백필 {n}건 기록(원천: cron.log {len(cron_logs)}개 + 폴더 표시) — 통계의 backfill_events 로 표시된다."


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.environ.get("KR_QUANT_REPORTS") or str(REPO / "reports"),
                    help="원장이 있는 리포트 폴더")
    ap.add_argument("--days", type=int, default=30, help="통계 창(일) — 30·90 을 주로 쓴다")
    ap.add_argument("--json", action="store_true", help="통계를 JSON 으로")
    ap.add_argument("--errata", action="store_true", help="첫 게시 뒤 숫자가 바뀐 날만 나열")
    ap.add_argument("--backfill", action="store_true",
                    help="한 번만: cron.log·폴더 표시·.archive 에서 과거 사건을 복원해 덧붙인다(source=backfill)")
    ap.add_argument("--cron-log", action="append", default=None,
                    help="백필이 읽을 cron.log (여러 번 가능; 기본 <root>/cron.log.1 과 <root>/cron.log)")
    ap.add_argument("--force", action="store_true", help="백필이 이미 있어도 추가한다")
    ap.add_argument("--now", default=None, help="통계의 기준 시각(ISO8601, 시험용)")
    a = ap.parse_args(argv)
    root = Path(a.root)
    if a.backfill:
        logs = [Path(p) for p in (a.cron_log or [root / "cron.log.1", root / "cron.log"]) if Path(p).exists()]
        rc, msg = backfill(root, logs, force=a.force)
        print(msg)
        return rc
    events = list(iter_events(root))
    if a.errata:
        rows = errata_rows(events)
        if not rows:
            print("정정 없음 — 첫 게시 뒤 숫자가 바뀐 날이 원장에 없다.")
            return 0
        for r in rows:
            print(f"{r['asof']}  {r['kind']}  {r['detail']}" + ("  [backfill]" if r["backfill"] else ""))
        return 0
    now = parse_ts(a.now) if a.now else None
    s = stats(events, a.days, now=now)
    print(json.dumps(s, ensure_ascii=False, indent=1) if a.json else render(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
