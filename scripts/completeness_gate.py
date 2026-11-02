#!/usr/bin/env python
"""완전성 관문 — 기준일 수집이 끝났나. 리포트를 만들기 **전에** 본다.

2026-10-27 사고: ``daily_collection`` 이 18:47 까지 도는 날 18:10 리포트가 그 중간을 읽었다.
코스닥 거래대금 1,099억(다음 리포트 59,125억의 1.9%), 코스닥 시총 0 인 채 ``latest`` 가 됐고,
다음 실행은 기준일이 달라 그날을 다시 만들지 않았다. 기준일은 ``max(date)`` 라 행 하나로도 바뀌고,
``finalized`` 는 시계만 본다. 마지막 날을 종목 10%로 줄여도 DB 밖 검사 113개가 통과했다.

규칙 — **시장별로** 다음을 전부 만족해야 통과한다(리포트와 같은 유니버스: 키움 수급 ∩ ``stocks``).

* 수급 종목수 ≥ 기준의 :data:`MIN_COVERAGE`
* 시총이 잡히는 종목수(``daily_bars`` 종가 × 상장주식수) ≥ 기준의 :data:`MIN_COVERAGE`
* 시총 합 > 0 · 그날 행이 있음

기준은 직전 :data:`BASELINE_DAYS` 거래일 **중앙값**이다. 전 거래일 하나만 보면 이틀 연속 부분
수집이 서로를 통과시킨다. 거래대금은 쓰지 않는다 — 하루에 몇 배씩 움직이는 측정값이라 문턱을
못 잡는다. 종목수는 상장·폐지로만 움직인다.

문턱 97%: 2025-09~2026-11 의 279거래일에서 이 값의 시장별 최저가 99.1%(2026-11-02 거래소
796/803)이고 그다음이 99.8% 다. 90% 로 두면 코스닥 180종목이 빠진 날이 통과한다. 97% 면 빠지는
종목이 거래소 24·코스닥 54 이내이고, 실측 최저와도 2%p 간격이 있어 거짓 차단이 없다.
막혀도 비용은 다음 재시도까지 지연뿐이고 ``latest`` 는 전날 것이 남는다.

종료코드 0 = 통과, :data:`EX_INCOMPLETE` = 미완(재시도 대상), 그 밖 = 오류(접속 실패 등).
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys

import pandas as pd

from sectorflow.storage import _is_pg, connect, db_default, market_cap_asof_bulk

MIN_COVERAGE = 0.97
BASELINE_DAYS = 5
#: sysexits 의 EX_TEMPFAIL — "나중에 다시 하면 된다".
EX_INCOMPLETE = 75
STATEMENT_TIMEOUT = "120s"


def read_counts(con, asof: str) -> dict[str, dict[str, tuple[int, int, float]]]:
    """시장 → 날짜 → (수급 종목수, 시총 있는 종목수, 시총 합). 기준일과 직전 거래일들.

    날짜는 ``sector_flow.load`` 처럼 모든 소스의 날짜에서 고른다. 키움 행이 없는 날도 "빈 날"로
    남아야 한다 — 소스를 좁혀 고르면 그날을 건너뛰어 직전 날을 기준일로 착각한다.
    """
    ph = "%s" if _is_pg(con) else "?"
    cur = con.cursor()
    if _is_pg(con):
        cur.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
    cur.execute(f"SELECT DISTINCT date FROM supply_demand WHERE date <= {ph} "
                f"ORDER BY date DESC LIMIT {ph}", (asof, BASELINE_DAYS + 1))
    dates = [str(r[0]) for r in cur.fetchall()]
    if not dates:
        return {}
    date_ph = ",".join([ph] * len(dates))
    # institution·close 가 NULL 인 행은 그날 수급이 아직 없는 행이다 — 세면 빈 칸이 찬 것으로 보인다.
    cur.execute(
        "SELECT s.code, s.date, m.market FROM supply_demand s JOIN stocks m ON m.code = s.code "
        f"WHERE s.date IN ({date_ph}) AND s.source = 'kiwoom' "
        "AND s.close IS NOT NULL AND s.institution IS NOT NULL",
        dates)
    df = pd.DataFrame(cur.fetchall(), columns=["code", "date", "market"])
    markets = [r[0] for r in _distinct_markets(cur)]
    out: dict = {m: {} for m in markets}
    if df.empty:
        return out
    df["date"] = df["date"].astype(str)
    df["cap"] = market_cap_asof_bulk(con, df).to_numpy()
    df["has_cap"] = df["cap"].fillna(0) > 0
    for (market, date), g in df.groupby(["market", "date"]):
        if market in out:
            out[market][date] = (len(g), int(g["has_cap"].sum()), float(g["cap"].fillna(0).sum()))
    return out


def _distinct_markets(cur) -> list:
    cur.execute("SELECT DISTINCT market FROM stocks WHERE market IS NOT NULL ORDER BY market")
    return cur.fetchall()


def evaluate(counts: dict, asof: str) -> tuple[bool, list[str]]:
    """(통과 여부, 시장별 판정 줄). 순수 함수 — DB 를 안 탄다."""
    lines, ok = [], True
    if not counts:
        return False, [f"[FAIL] {asof} 시장 목록이 비었다 — 비교할 수 없다"]
    for market, by_date in sorted(counts.items()):
        prior = sorted(d for d in by_date if d < asof)[-BASELINE_DAYS:]
        n, n_cap, cap_sum = by_date.get(asof, (0, 0, 0.0))
        why = []
        if not prior:
            why.append("직전 거래일 행이 없어 비교할 수 없다")
            base_n = base_cap = 0
        else:
            base_n = statistics.median(by_date[d][0] for d in prior)
            base_cap = statistics.median(by_date[d][1] for d in prior)
            if n < base_n * MIN_COVERAGE:
                why.append(f"수급 종목수 {n}/{base_n:.0f} < {MIN_COVERAGE:.0%}")
            if n_cap < base_cap * MIN_COVERAGE:
                why.append(f"시총 종목수 {n_cap}/{base_cap:.0f} < {MIN_COVERAGE:.0%}")
        if cap_sum <= 0:
            why.append("시총 합이 0 이다")
        if why:
            ok = False
            lines.append(f"[FAIL] {market}: " + " · ".join(why))
        else:
            lines.append(f"[PASS] {market}: 수급 {n}/{base_n:.0f} · 시총 {n_cap}/{base_cap:.0f}")
    return ok, lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--asof", required=True, help="검사할 기준일(YYYY-MM-DD)")
    ap.add_argument("--db", default=None)
    a = ap.parse_args(argv)

    con = connect(a.db or os.environ.get("KR_QUANT_DB") or db_default())
    try:
        counts = read_counts(con, a.asof)
    finally:
        con.close()
    ok, lines = evaluate(counts, a.asof)
    print(f"완전성 관문 — 기준일 {a.asof} · 기준 직전 {BASELINE_DAYS}거래일 중앙값 · "
          f"문턱 {MIN_COVERAGE:.0%}")
    for ln in lines:
        print("  " + ln)
    return 0 if ok else EX_INCOMPLETE


if __name__ == "__main__":
    sys.exit(main())
