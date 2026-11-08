#!/usr/bin/env python
"""신선도 점검 — 평일 저녁, 오늘 리포트가 걸렸나와 알림 통로가 살아 있나를 본다.

daily_report.sh 는 **제가 돈 날**만 알린다. 크론 줄이 빠졌거나, 배포가 깨져 스크립트가 아예 안 떴거나,
수집이 통째로 안 돌아 기준일이 어제에 머물면(daily_report.sh 는 "이미 있다" 며 0 으로 건너뛴다) 아무 알림도
없다. 이 점검은 결과만 본다(크론 평일 20:30 — 19:10 재시도와 18:47 까지 간 수집 뒤).

* 오늘 ``supply_demand`` 행이 있는데 ``reports/latest`` 가 오늘이 아니다 → **1(오류)**. 데이터는 왔는데 리포트가
  안 걸렸다. 오늘 폴더에 남은 표시(INCOMPLETE·VERIFY_FAILED)가 있으면 같이 적는다.
* 평일인데 오늘 행이 하나도 없다 → **75(경고)**. 휴장일이 아니면 수집이 실패했다. 달력이 없어 휴장과 못 가른다.
* ``ALERT_WEBHOOK_URL`` 이 비었다 → **75(경고)**, 다른 결과와 같이(주말·DB 실패여도 본다). 비어 있으면 quant-airflow ``alert.sh`` 가
  경고·오류를 로그에만 남겨 위의 모든 알림이 아무에게도 안 간다. 하루 한 번(이 크론) 시끄럽게 말한다.

여러 개가 겹치면 더 심한 쪽(1 > 75 > 0)으로 끝난다. DB 접속 실패는 1.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
REPO = Path(__file__).resolve().parent.parent
WARN = 75


def worse(a: int, b: int) -> int:
    """심한 쪽 — 0 < 75(경고) < 그 밖(오류)."""
    rank = {0: 0, WARN: 1}
    return a if rank.get(a, 2) >= rank.get(b, 2) else b


def latest_date(root: Path) -> str | None:
    link = root / "latest"
    if not link.exists():
        return None
    return link.resolve().name


def judge(root: Path, today: str, rows_today: int) -> tuple[int, list[str]]:
    """(종료코드, 찍을 줄). DB 를 안 보는 순수 판정 — 시험이 이걸 직접 부른다."""
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
    elif rows_today == 0:
        rc = worse(rc, WARN)
        out.append(f"⚠️ [신선도] 평일인데 오늘({today}) supply_demand 가 비었다 — 휴장일이 아니면 수집이 "
                   f"실패했다. quant-airflow daily_collection 을 확인하라.")
    else:
        out.append(f"[신선도] OK — 오늘({today}) 수급 {rows_today:,}행, latest = {latest}")
    return rc, out


def check_webhook(webhook: str | None) -> tuple[int, list[str]]:
    if (webhook or "").strip():
        return 0, []
    return WARN, ["⚠️ [알림 통로] ALERT_WEBHOOK_URL 이 비었다 — 경고·오류가 로그에만 남고 아무에게도 안 간다. "
                  "quant-airflow/.env 에 웹훅 주소를 넣어라."]


def rows_on(db: str, day: str) -> int:
    from sectorflow.storage import _is_pg, connect  # noqa: PLC0415 — 순수 판정 시험은 DB 없이 돈다

    con = connect(db)
    try:
        cur = con.cursor()
        ph = "%s" if _is_pg(con) else "?"
        cur.execute(f"SELECT count(*) FROM supply_demand WHERE date = {ph}", (day,))  # noqa: S608
        return int(cur.fetchone()[0])
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
            n = rows_on(a.db or os.environ.get("KR_QUANT_DB") or str(db_default()), today)
        except Exception as e:  # noqa: BLE001 — 무엇이든 접속·조회 실패다
            # libpq 가 접속 문자열을 되읊을 수 있어 비밀번호 자리는 가린다.
            msg = re.sub(r"://[^@ ]*@", "://***@", f"{type(e).__name__}: {e}".strip())
            rc, lines = 1, [f"⚠️ [신선도] DB 에서 오늘 수급을 못 셌다 — {msg}"]
        else:
            rc, lines = judge(Path(a.root), today, n)
    # 알림 통로는 무슨 일이 있어도 본다 — 위가 실패한 날일수록 알림이 가야 한다.
    wrc, wlines = check_webhook(os.environ.get("ALERT_WEBHOOK_URL"))
    print("\n".join(lines + wlines))
    return worse(rc, wrc)


if __name__ == "__main__":
    sys.exit(main())
