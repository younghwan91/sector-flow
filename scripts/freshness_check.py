#!/usr/bin/env python
"""신선도 점검 — 평일 저녁, 오늘 리포트가 걸렸나와 알림 통로가 살아 있나를 본다.

daily_report.sh 는 **제가 돈 날**만 알린다. 크론 줄이 빠졌거나, 배포가 깨져 스크립트가 아예 안 떴거나,
수집이 통째로 안 돌아 기준일이 어제에 머물면(daily_report.sh 는 "이미 있다" 며 0 으로 건너뛴다) 아무 알림도
없다. 이 점검은 결과만 본다(크론 평일 20:30 — 19:10 재시도와 18:47 까지 간 수집 뒤).

* 오늘 ``supply_demand`` 행이 있는데 ``reports/latest`` 가 오늘이 아니다 → **1(오류)**. 데이터는 왔는데 리포트가
  안 걸렸다. 오늘 폴더에 남은 표시(INCOMPLETE·VERIFY_FAILED)가 있으면 같이 적는다.
* 평일인데 오늘 행이 하나도 없다 → 휴장과 수집 실패를 같은 DB 의 장중 틱(``ticks``, scalp-it 이 장중에 쌓는다)으로
  가른다 — quant-airflow ``scripts/daily_health_check.sh`` 와 같은 규약이다. 틱도 없으면 **휴장으로 보고 0**,
  틱이 있으면(장이 열렸다) **75(경고)** — 수집 실패다. 틱 표가 없거나 못 읽으면 못 가르니 75 에 "휴장일이면 무시".
  2026-09~11 실측: 평일 휴장 4일(09-24·25·10-05·10-09)은 수급·틱 모두 0, 거래일 중 틱이 빈 날이 하루(10-28,
  scalp-it 정지) 있었다 — 틱 없음은 휴장의 증거가 아니라서 수급이 **있는** 날엔 틱을 보지 않는다.
  남는 구멍: scalp-it 과 수집이 같은 거래일에 함께 죽으면 휴장으로 보인다(daily_collection 실패는 Airflow 가 따로 알린다).
* 전 거래일 폴더에 ``FINAL.txt``(또는 재구성 표시 ``REBUILT.txt``)가 없다 → **75(경고)**. 그날 아침 08:20 확정 모드가
  안 돌았거나 확정을 확인 못 한 채 지나갔다. 다음 거래일 수집이 들어오면 08:20 은 그 날을 다시 안 보므로 그날 저녁에 알린다.
* 전 거래일 확정 검증(``VERIFY.txt``)이 외부 참값(정규장 종가 바구니 ≈ KRX 지수) 대조를 **연달아 3번** 건너뛰었다(``[WARN]``
  외부 참값 연속 SKIP) → **75(경고)**. 한 번 건너뛴 것은 한 줄로만 적는다(다음날 수급이 들어와야 채워지는 뷰라 흔하다).
* 알림 통로 — quant-airflow ``alert.sh`` 가 **실제로 읽을 값**을 같은 방식으로 읽는다(:func:`alert_value`). 비었으면 75,
  있으면 GET 한 번(5초, 메시지는 안 보낸다 — Discord 웹훅은 GET 에 200 + JSON 으로 답한다)으로 살아 있나 본다.
  401·404 는 폐기됐거나 틀린 주소다. 주말·DB 실패여도 본다. 주소는 어디에도 찍지 않는다.
* 하트비트(죽은 사람 스위치) — ``HEARTBEAT_URL``(웹훅과 같은 방식으로 읽는다)이 있으면 **리포트·수급·확정 점검이 0 인 날에**
  GET 한 번. 외부 감시(healthchecks.io 등)가 정해진 시각에 이 핑을 못 받으면 알린다 — 이 머신·크론·알림 통로가 통째로 죽은 날도
  잡힌다. 웹훅과는 따로다: 웹훅이 **없어도** 보낸다(하트비트만 쓰는 설정). 다만 웹훅이 **있는데 죽었으면** 안 보낸다 —
  그날은 경고가 아무에게도 안 가니 외부 감시가 유일한 통로다. 없으면 한 줄 적고 넘어간다(주소만 넣으면 동작한다).
  핑 실패는 종료코드를 바꾸지 않는다 — 그 알림은 외부 감시의 몫이다.

여러 개가 겹치면 더 심한 쪽(1 > 75 > 0)으로 끝난다. DB 접속 실패는 1.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # scripts/ 는 패키지가 아니다
import ops_ledger  # noqa: E402 — 운영 사건 원장(기록·SLO 기한, scripts/ops_ledger.py)

KST = timezone(timedelta(hours=9))
REPO = Path(__file__).resolve().parent.parent
WARN = 75
#: 웹훅·하트비트 GET 상한(초).
PROBE_TIMEOUT = 5
_UA = "sector-flow-freshness/1"   # Discord(Cloudflare)는 urllib 기본 UA 를 막는다 — alert.py 도 제 UA 를 단다
#: freshness_check.sh 가 ``_env.sh`` 로 .env 를 불러오기 **전의** 크론 환경을 넘기는 접두. 이 표시가 있으면
#: 지금 환경(.env 가 섞였다)이 아니라 그 사본을 본다 — alert.sh 는 .env 를 불러온 환경에서 돌지 않는다.
CRON_ENV_MARK = "SF_CRON_ENV"
CRON_ENV_PREFIX = "SF_CRON_"


def worse(a: int, b: int) -> int:
    """심한 쪽 — 0 < 75(경고) < 그 밖(오류)."""
    rank = {0: 0, WARN: 1}
    return a if rank.get(a, 2) >= rank.get(b, 2) else b


def latest_date(root: Path) -> str | None:
    link = root / "latest"
    if not link.exists():
        return None
    return link.resolve().name


def judge(root: Path, today: str, rows_today: int, *, market_open: bool | None = None) -> tuple[int, list[str]]:
    """(종료코드, 찍을 줄). DB 를 안 보는 순수 판정 — 시험이 이걸 직접 부른다.

    ``market_open`` 은 오늘 장중 틱이 있었나(None = 모른다). 수급이 빈 날에만 쓴다.
    """
    rc, out = 0, []
    latest = latest_date(root)
    if rows_today > 0 and latest != today:
        rc = worse(rc, 1)
        out.append(f"⚠️ [신선도] 오늘({today}) 수급이 {rows_today:,}행 들어왔는데 리포트 latest 가 "
                   f"{latest or '없음'} 이다 — 오늘 리포트가 안 걸렸다.")
        d = root / today
        for mark, why in (("INCOMPLETE.txt", "완전성 관문이 막았다(수집 미완) — 수집이 끝났는지 보고"),
                          ("VERIFY_FAILED.txt", "검증에 걸렸다 — 그 파일을 보고")):
            if (d / mark).is_file():
                out.append(f"    {d / mark}: {why}")
        out.append("    reports/cron.log 의 오늘 18:10·19:10 줄을 보고, 고친 뒤 손으로: scripts/daily_report.sh")
    elif rows_today == 0 and market_open is False:
        out.append(f"[신선도] {today} 휴장으로 본다 — 수급도 장중 틱(ticks)도 없다. 볼 것 없다")
    elif rows_today == 0:
        rc = worse(rc, WARN)
        if market_open:
            out.append(f"⚠️ [신선도] 오늘({today}) 장중 틱은 있는데(장이 열렸다) supply_demand 가 비었다 — 수집이 "
                       f"실패했다. quant-airflow daily_collection 을 확인하라.")
        else:
            out.append(f"⚠️ [신선도] 평일인데 오늘({today}) supply_demand 가 비었다 — 휴장일이면 무시. 아니면 수집이 "
                       f"실패했다(장중 틱 표를 못 읽어 못 가른다). quant-airflow daily_collection 을 확인하라.")
    else:
        out.append(f"[신선도] OK — 오늘({today}) 수급 {rows_today:,}행, latest = {latest}")
    return rc, out


def check_prev_final(root: Path, prev: str | None) -> tuple[int, list[str]]:
    """전 거래일이 확정치로 다시 만들어졌나(``FINAL.txt``).

    재구성(``REBUILT.txt``)만으로는 확정이 아니다 — 확정치를 확인한 재구성만 ``FINAL.txt`` 를 쓴다. 확인 못 한
    재구성(잠정·창 밖 ``finalize: unverified``)을 확정으로 세면 확정 작업이 깨진 날을 놓친다.
    """
    if not prev:
        return 0, []
    d = root / prev
    if (d / "FINAL.txt").is_file():
        return 0, [f"[확정] 전 거래일({prev}) 확정 리포트 있음"]
    if (d / "REBUILT.txt").is_file():
        state = "재구성은 했지만 확정치를 확인하지 못했다(REBUILT.txt 의 finalize 줄)"
    elif (d / "numbers.html").is_file():
        state = "잠정 리포트만 있다"
    elif (d / "INCOMPLETE.txt").is_file():
        state = "리포트가 없다(INCOMPLETE.txt — 수집 미완으로 안 만들었다)"
    else:
        state = "리포트 폴더가 없다"
    return WARN, [f"⚠️ [확정] 전 거래일({prev})이 확정되지 않았다 — {state}(FINAL.txt 없음). 08:20 확정 모드가 "
                  "안 돌았거나 daily_finalize 를 확인하지 못했다.",
                  f"    reports/cron.log 의 08:20 줄과 `uv run python scripts/finalize_done.py --asof {prev}` 를 본다. "
                  "다음 거래일 수집이 들어와 08:20 은 이 날을 다시 안 본다 — 지난 기준일 재구성으로 다시 만든다(README §3.1)."]


def check_slo(root: Path, prev: str | None) -> tuple[int, list[str]]:
    """전 거래일이 SLO(docs/slo.md) 안이었나 — 운영 사건 원장(``events.jsonl``)으로 잰다.

    잠정은 그날 18:30(KST)까지 게시, 확정은 다음날 10:40 까지(08:40 목표, 10:20 재시도 뒤 상한).
    늦은 **기록이 있을 때만** 경고한다(75) — 원장에 그날 기록이 없으면(원장 도입 전·백필 공백) 못 재는 것이지
    위반이 아니고, 게시·확정이 아예 없는 날은 위의 신선도·확정 점검이 이미 잡는다.
    """
    if not prev:
        return 0, []
    evs = [e for e in ops_ledger.iter_events(root) if e.get("event") == "report" and e.get("asof") == prev]
    if not evs:
        return 0, [f"[SLO] 원장에 전 거래일({prev}) 기록이 없다 — 재지 않는다(기록은 reports/events.jsonl, "
                   "통계는 scripts/ops_ledger.py)"]
    rc, out = 0, []
    prov = ops_ledger.first_ts(evs, ops_ledger.is_prov_publication)
    fin = ops_ledger.first_ts(evs, ops_ledger.is_final_confirmation)
    if prov is not None and prov > ops_ledger.prov_deadline(prev):
        rc = worse(rc, WARN)
        out.append(f"⚠️ [SLO] 전 거래일({prev}) 잠정 리포트가 기한({ops_ledger.PROV_SLO_KST:%H:%M} KST)을 넘겨 "
                   f"게시됐다({prov.astimezone(KST):%H:%M} KST) — 18:10 실행이 늦었거나 수집이 늦게 끝났다. "
                   "reports/cron.log 의 그날 18:10·19:10 줄을 보라(docs/slo.md).")
    if fin is not None and fin > ops_ledger.final_deadline(prev):
        rc = worse(rc, WARN)
        out.append(f"⚠️ [SLO] 전 거래일({prev}) 확정이 기한({ops_ledger.FINAL_SLO_KST:%H:%M} KST)을 넘겼다"
                   f"({fin.astimezone(KST):%m-%d %H:%M} KST) — 08:20·10:20 확정 모드가 둘 다 못 끝냈다. "
                   "reports/cron.log 의 08:20·10:20 줄과 daily_finalize 를 보라(docs/slo.md).")
    if rc == 0:
        said = []
        if prov is not None:
            said.append(f"잠정 {prov.astimezone(KST):%H:%M}")
        if fin is not None:
            said.append(f"확정 {fin.astimezone(KST):%m-%d %H:%M}")
        out.append(f"[SLO] 전 거래일({prev}) {' · '.join(said) or '게시 기록 없음(다른 점검이 본다)'} — 기한 안이다")
    return rc, out


#: verify_report.py 가 VERIFY.txt 에 남기는 표지 — **같은 글**이어야 한다(``tests/test_freshness_check.py`` 가 맞춘다).
SKIP_MARK = "SKIP 정규장 종가 대조"
SKIP_STREAK_MARK = "외부 참값 연속 SKIP"


def check_external_truth(root: Path, prev: str | None) -> tuple[int, list[str]]:
    """전 거래일 확정 검증이 외부 참값 대조를 했나 — 연속 SKIP 경고(``[WARN]``)가 있으면 75, 한 번 SKIP 은 한 줄."""
    if not prev:
        return 0, []
    try:
        text = (root / prev / "VERIFY.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, []
    hit = next((ln.strip() for ln in text.splitlines() if "[WARN]" in ln and SKIP_STREAK_MARK in ln), None)
    if hit:
        return WARN, [f"⚠️ [외부 참값] 전 거래일({prev}) 확정 검증: {hit}",
                      f"    {root / prev / 'VERIFY.txt'} 의 '{SKIP_MARK}' 사유를 보라 — 바구니 ≈ KRX 지수 대조가 며칠째 안 돌았다."]
    if SKIP_MARK in text:
        return 0, [f"[외부 참값] 전 거래일({prev}) 확정 검증이 정규장 종가 대조를 건너뛰었다(SKIP) — "
                   "3번 이어지면 경고한다"]
    return 0, []


#: verify_report.py 의 공표치 대조 표지 — 역시 **같은 글**.
PUB_SKIP_MARK = "SKIP 공표치 대조"
PUB_SKIP_STREAK_MARK = "공표치 대조 연속 SKIP"


def check_published_truth(root: Path, prev: str | None) -> tuple[int, list[str]]:
    """전 거래일 검증이 공표 시장값 대조를 했나 — 연속 SKIP 경고(``[WARN]``)가 있으면 75, 한 번 SKIP 은 한 줄."""
    if not prev:
        return 0, []
    try:
        text = (root / prev / "VERIFY.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, []
    hit = next((ln.strip() for ln in text.splitlines() if "[WARN]" in ln and PUB_SKIP_STREAK_MARK in ln), None)
    if hit:
        return WARN, [f"⚠️ [공표치] 전 거래일({prev}) 검증: {hit}",
                      f"    {root / prev / 'VERIFY.txt'} 의 '{PUB_SKIP_MARK}' 사유를 보라 — 시장 합계를 공표치에 며칠째 못 댔다."]
    if PUB_SKIP_MARK in text:
        return 0, [f"[공표치] 전 거래일({prev}) 검증이 공표 시장값 대조를 건너뛰었다(SKIP) — 3번 이어지면 경고한다"]
    return 0, []


# ── 알림 통로 — quant-airflow scripts/alert.sh 가 읽는 그대로 ─────────────────────

def alert_sh_path(environ) -> Path:
    """cron_run.sh 가 부르는 alert.sh — ``SECTOR_FLOW_ALERT`` 또는 옆자리 quant-airflow."""
    return Path(cron_env("SECTOR_FLOW_ALERT", environ) or REPO.parent / "quant-airflow" / "scripts" / "alert.sh")


def cron_env(key: str, environ) -> str | None:
    """크론 환경에서 ``key`` 의 값(정의 안 됐으면 None). freshness_check.sh 를 거쳤으면 .env 를 불러오기 전의 사본."""
    if environ.get(CRON_ENV_MARK):
        return environ.get(CRON_ENV_PREFIX + key)
    return environ.get(key)


def env_file_value(path: Path, key: str) -> str:
    """alert.sh 의 ``env_get`` — ``grep "^KEY=" | tail -n 1 | cut -d= -f2- | sed`` (양끝 따옴표 하나씩 뗀다).

    ``export KEY=…``·``KEY = …`` 줄은 grep 에 안 걸린다 — alert.sh 가 못 보는 값을 여기서도 못 본다.
    파일이 없으면 빈 값(grep 이 조용히 실패한다).
    """
    prefix = f"{key}="
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    hits = [line for line in text.split("\n") if line.startswith(prefix)]
    if not hits:
        return ""
    value = hits[-1][len(prefix):]
    value = re.sub(r"""^["']""", "", value)
    return re.sub(r"""["']$""", "", value)


def alert_value(key: str, environ, alert_sh: Path) -> str:
    """alert.sh 가 ``key`` 로 쓸 값. 크론 환경에 **정의돼 있으면**(빈 값 포함) 그것, 아니면 alert.sh 옆 ``.env``.

    alert.py 가 ``.strip()`` 하므로 여기서도 벗긴다.
    """
    v = cron_env(key, environ)
    if v is None:
        v = env_file_value(alert_sh.parent.parent / ".env", key)
    return v.strip()


def _why(e: BaseException) -> str:
    """실패 이유 — 예외 **종류와 errno 만**. ``str(e)`` 는 절대 안 쓴다: 주소 꼴 오류(ValueError ``unknown url type``,
    InvalidURL ``can't contain control characters``)는 주소나 그 경로(=웹훅 토큰)를 그대로 되읊고, 공백 등으로
    잘린 조각이라 ``replace(url, …)`` 로도 못 가린다. 이 줄은 cron_run 꼬리 → alert.sh → Discord 로 나간다."""
    inner = getattr(e, "reason", None)
    inner = inner if isinstance(inner, BaseException) else None
    parts = [type(e).__name__] + ([type(inner).__name__] if inner else [])
    eno = getattr(inner or e, "errno", None)
    if isinstance(eno, int):
        parts.append(f"errno {eno}")
    return " ".join(parts)


def probe(url: str, timeout: float = PROBE_TIMEOUT) -> tuple[int | None, bytes, str]:
    """GET 한 번 — (HTTP 상태 또는 None, 본문 앞부분, 실패 이유). 요청 만들기부터 try 안이다 — 주소 꼴이 틀려
    Request 가 던지면 트레이스백이 주소를 통째로 찍는다."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA}, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — 운영자가 넣은 주소다
            return r.status, r.read(65536), ""
    except urllib.error.HTTPError as e:
        return e.code, b"", ""
    except Exception as e:  # noqa: BLE001 — 접속·시간 초과·주소 꼴 무엇이든 "닿지 못했다"
        return None, b"", _why(e)


def check_webhook(webhook: str | None, *, probe_fn=probe) -> tuple[int, list[str]]:
    if not (webhook or "").strip():
        return WARN, ["⚠️ [알림 통로] ALERT_WEBHOOK_URL 이 비었다 — 경고·오류가 로그에만 남고 아무에게도 안 간다. "
                      "~/git/quant-airflow/.env 에 `ALERT_WEBHOOK_URL=<주소>` 줄로 넣어라(alert.sh 는 그 꼴의 줄만 읽는다 — "
                      "`export` 를 붙이거나 이 레포 .env 에 넣으면 못 본다)."]
    status, body, why = probe_fn(webhook.strip())
    if status == 200:
        try:
            json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            return WARN, ["⚠️ [알림 통로] 웹훅 주소가 200 으로 답했지만 JSON 이 아니다 — Discord 웹훅 주소가 맞는지 보라."]
        return 0, ["[알림 통로] OK — 웹훅이 살아 있다(GET 200)"]
    if status in (401, 404):
        return WARN, [f"⚠️ [알림 통로] 웹훅이 폐기됐거나 틀렸다(HTTP {status}) — 알림이 전달되지 않는다"
                      "([alert.py] failed). 채널에서 웹훅을 새로 만들어 ~/git/quant-airflow/.env 의 ALERT_WEBHOOK_URL= 을 바꿔라."]
    if status is not None:
        return WARN, [f"⚠️ [알림 통로] 웹훅이 HTTP {status} 로 답했다 — 알림이 전달되지 않을 수 있다. "
                      "~/logs/quant-airflow/alerts.log 의 [alert.py] failed 를 보라."]
    return WARN, [f"⚠️ [알림 통로] 웹훅에 {PROBE_TIMEOUT}초 안에 닿지 못했다({why}) — 네트워크가 잠깐 끊겼을 수 있다. "
                  "이어지면 알림도 안 간다."]


def heartbeat(url: str, ok: bool, *, probe_fn=probe, why: str = "점검이 깨끗하지 않다") -> list[str]:
    """죽은 사람 스위치 — ``ok`` 인 날에만 핑(``why`` 는 안 보낸 이유). 종료코드는 바꾸지 않는다."""
    if not url:
        return ["[하트비트] HEARTBEAT_URL 없음 — 외부 감시가 꺼져 있다(주소만 넣으면 동작한다, README §3.1)"]
    if not ok:
        return [f"[하트비트] 안 보낸다 — {why}(외부 감시가 핑이 빈 것을 알린다)"]
    status, _, why = probe_fn(url)
    if status is not None and 200 <= status < 300:
        return ["[하트비트] 보냈다"]
    return [f"[하트비트] 못 보냈다({f'HTTP {status}' if status is not None else why}) — 외부 감시가 알릴 것이다"]


# ── DB ────────────────────────────────────────────────────────────────────────

def db_facts(db: str, day: str) -> tuple[int, str | None, bool | None]:
    """(오늘 수급 행 수, 오늘 전 마지막 수급일, 오늘 장중 틱이 있었나 — 수급이 있으면·못 읽으면 None)."""
    from sectorflow.storage import _is_pg, connect  # noqa: PLC0415 — 순수 판정 시험은 DB 없이 돈다

    con = connect(db)
    try:
        cur = con.cursor()
        ph = "%s" if _is_pg(con) else "?"
        cur.execute(f"SELECT count(*) FROM supply_demand WHERE date = {ph}", (day,))  # noqa: S608
        rows = int(cur.fetchone()[0])
        cur.execute(f"SELECT max(date) FROM supply_demand WHERE date < {ph}", (day,))  # noqa: S608
        prev = cur.fetchone()[0]
        prev = None if prev is None else str(prev)[:10]
        market_open = None
        if rows == 0:
            if _is_pg(con):
                # 틱 표는 크다(하이퍼테이블). 범위 조회라 평소 ms 지만, 걸려도 점검 전체(크론 10분)를 잡지 않게.
                cur.execute("SET LOCAL statement_timeout = '15s'")
            # 틱은 scalp-it 이 장중에 쌓는다(quant-airflow daily_health_check 와 같은 휴장 판정). 표가 없으면 모른다.
            # ticks.ts 는 시간대 없는 timestamp 로 KST 벽시계(09:00~15:20)를 담는다 — timestamptz 로 비교하면 세션 시간대(UTC)
            # 만큼 밀려 휴장일에 전날 장중 틱이 걸린다(2026-10-09 로 확인).
            nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
            try:
                cur.execute(f"SELECT EXISTS (SELECT 1 FROM ticks WHERE ts >= {ph} AND ts < {ph})",  # noqa: S608
                            (f"{day} 00:00:00", f"{nxt} 00:00:00"))
                market_open = bool(cur.fetchone()[0])
            except Exception:  # noqa: BLE001 — 틱 표가 없는 DB(시험·분석용 사본)
                market_open = None
        return rows, prev, market_open
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.environ.get("KR_QUANT_REPORTS") or str(REPO / "reports"),
                    help="리포트 폴더(latest 링크가 있는 곳)")
    ap.add_argument("--db", default=None, help="기본: $KR_QUANT_DB")
    ap.add_argument("--today", default=None, help="오늘(YYYY-MM-DD, KST). 시험용")
    a = ap.parse_args(argv)
    today = a.today or datetime.now(KST).date().isoformat()

    if date.fromisoformat(today).weekday() >= 5:
        rc, lines = 0, [f"[신선도] {today} 는 주말 — 볼 것 없다"]
    else:
        try:
            from sectorflow.storage import db_default  # noqa: PLC0415
            n, prev, market_open = db_facts(a.db or os.environ.get("KR_QUANT_DB") or str(db_default()), today)
        except Exception as e:  # noqa: BLE001 — 무엇이든 접속·조회 실패다
            # libpq 가 접속 문자열을 되읊을 수 있어 비밀번호 자리는 가린다.
            msg = re.sub(r"://[^@ ]*@", "://***@", f"{type(e).__name__}: {e}".strip())
            rc, lines = 1, [f"⚠️ [신선도] DB 에서 오늘 수급을 못 셌다 — {msg}"]
        else:
            rc, lines = judge(Path(a.root), today, n, market_open=market_open)
            prc, plines = check_prev_final(Path(a.root), prev)
            rc, lines = worse(rc, prc), lines + plines
            xrc, xlines = check_external_truth(Path(a.root), prev)
            rc, lines = worse(rc, xrc), lines + xlines
            trc, tlines = check_published_truth(Path(a.root), prev)
            rc, lines = worse(rc, trc), lines + tlines
            src, slines = check_slo(Path(a.root), prev)
            rc, lines = worse(rc, src), lines + slines
    data_rc = rc
    # 알림 통로는 무슨 일이 있어도 본다 — 위가 실패한 날일수록 알림이 가야 한다.
    alert_sh = alert_sh_path(os.environ)
    webhook = ""
    if os.access(alert_sh, os.X_OK):
        webhook = alert_value("ALERT_WEBHOOK_URL", os.environ, alert_sh)
        wrc, wlines = check_webhook(webhook)
    else:
        wrc, wlines = WARN, [f"⚠️ [알림 통로] alert.sh 가 없다({alert_sh}) — cron_run.sh 가 모든 알림을 로그에만 남긴다."]
    rc = worse(rc, wrc)
    lines += wlines
    # 하트비트는 웹훅과 따로 — 웹훅이 없어도(하트비트만 쓰는 설정) 데이터 점검이 깨끗하면 보낸다. 웹훅이 **있는데 죽은**
    # 날은 안 보낸다: 그 경고가 죽은 웹훅으로 가 아무도 못 보니, 핑이 비는 것이 유일한 신호다.
    dead_hook = bool(webhook) and wrc != 0
    lines += heartbeat(alert_value("HEARTBEAT_URL", os.environ, alert_sh), data_rc == 0 and not dead_hook,
                       why="알림 웹훅이 죽어 있다" if data_rc == 0 else "점검이 깨끗하지 않다")
    print("\n".join(lines))
    # 이 점검의 판정도 원장에 한 줄 — 실패해도(디스크·권한) append_event 가 stderr 로만 말하고 rc 를 안 바꾼다.
    ops_ledger.append_event(Path(a.root), {"ts": ops_ledger.utc_now_iso(), "event": "freshness",
                                           "today": today, "rc": rc})
    return rc


if __name__ == "__main__":
    sys.exit(main())
