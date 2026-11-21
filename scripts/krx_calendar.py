#!/usr/bin/env python
"""KRX 휴장 달력 — 평일 중 장이 안 열리는 날. "수급이 없다" 만으로는 휴장과 수집 실패를 못 가른다.

freshness_check.py 는 전에 장중 틱(``ticks``)으로 휴장을 추정했다. 그러면 수집과 scalp-it 이 같은 거래일에 함께 죽은 날이
"휴장" 이 되어 경고는커녕 하트비트까지 나갔다. 이제 이 표가 정본이다. ops_ledger.py 의 SLO 분모(거래일)도 여기서 나온다.

출처:

* 2026 — KRX 휴장일 안내(KRX 정보데이터시스템 open.krx.co.kr → 시장정보 → 휴장일)와 같은 날짜이고, 11-06 까지는 실측으로 대조했다:
  kr_quant ``supply_demand``·``daily_bars`` 가 둘 다 비어 있는 평일이 정확히 이 표다(2026-11-09 조회,
  ``tests/test_krx_calendar.py`` 가 그 목록을 박아 둔다). 뒤의 12-25·12-31 은 공휴일·연말 휴장 규칙이다.
* 2027 — **잠정.** KRX 는 보통 그 전해 12월에 공지한다. 우주항공청 2027년도 월력요항(2026-06-29 발표, 음력 날짜)과
  공휴일에 관한 법률(대체공휴일, 2026 개정으로 제헌절·노동절 포함)에서 유도했다. KRX 공지가 나오면 대조하고
  :data:`PROVISIONAL` 에서 뺀다.

틀렸을 때 조용하지 않게 만들었다: 달력에 없는 임시 휴장은 수급 0 으로 오류(1)가 나고, 달력에 있는데 수급이 들어오면
경고(75)다(freshness_check.judge). 달력이 다룬 해를 벗어나면 휴장으로 넘기지 않고 경고한다.
"""
from __future__ import annotations

from datetime import date, timedelta

#: 평일 휴장만 적는다 — 주말과 겹친 공휴일은 어차피 장이 안 열린다.
HOLIDAYS: dict[int, dict[str, str]] = {
    2026: {
        "2026-01-01": "신정",
        "2026-02-16": "설 연휴",
        "2026-02-17": "설날",
        "2026-02-18": "설 연휴",
        "2026-03-02": "삼일절 대체",
        "2026-05-01": "노동절",
        "2026-05-05": "어린이날",
        "2026-05-25": "부처님오신날 대체",
        "2026-06-03": "지방선거",
        "2026-07-17": "제헌절",
        "2026-08-17": "광복절 대체",
        "2026-09-24": "추석 연휴",
        "2026-09-25": "추석",
        "2026-10-05": "개천절 대체",
        "2026-10-09": "한글날",
        "2026-12-25": "성탄절",
        "2026-12-31": "연말 휴장",
    },
    2027: {
        "2027-01-01": "신정",
        "2027-02-08": "설 연휴",
        "2027-02-09": "설날 대체",
        "2027-03-01": "삼일절",
        "2027-05-03": "노동절 대체",
        "2027-05-05": "어린이날",
        "2027-05-13": "부처님오신날",
        "2027-07-19": "제헌절 대체",
        "2027-08-16": "광복절 대체",
        "2027-09-14": "추석 연휴",
        "2027-09-15": "추석",
        "2027-09-16": "추석 연휴",
        "2027-10-04": "개천절 대체",
        "2027-10-11": "한글날 대체",
        "2027-12-27": "성탄절 대체",
        "2027-12-31": "연말 휴장",
    },
}
#: KRX 공지와 아직 대조하지 못한 해.
PROVISIONAL = frozenset({2027})


def _d(day: "str | date") -> date:
    return day if isinstance(day, date) else date.fromisoformat(day)


def covers(day: "str | date") -> bool:
    """이 달력이 그 해를 다루나 — 다루지 않는 해의 평일은 거래일인지 모른다."""
    return _d(day).year in HOLIDAYS


def holiday_name(day: "str | date") -> "str | None":
    """평일 휴장의 이름. 거래일·주말·달력 밖은 None."""
    d = _d(day)
    return HOLIDAYS.get(d.year, {}).get(d.isoformat())


def is_trading_day(day: "str | date") -> "bool | None":
    """거래일인가. 달력 밖의 평일은 None(모른다). 주말은 달력과 무관하게 False."""
    d = _d(day)
    if d.weekday() >= 5:
        return False
    if not covers(d):
        return None
    return holiday_name(d) is None


def prev_trading_day(day: "str | date") -> "str | None":
    """그날 **앞의** 마지막 거래일. 달력 밖으로 나가면 None."""
    d = _d(day) - timedelta(days=1)
    for _ in range(31):
        known = is_trading_day(d)
        if known:
            return d.isoformat()
        if known is None:
            return None
        d -= timedelta(days=1)
    return None


def trading_days(start: "str | date", end: "str | date") -> tuple[list[str], list[str]]:
    """(구간 안 거래일, 달력 밖이라 모르는 평일) — 양 끝 포함."""
    days: list[str] = []
    unknown: list[str] = []
    d, last = _d(start), _d(end)
    while d <= last:
        known = is_trading_day(d)
        if known:
            days.append(d.isoformat())
        elif known is None:
            unknown.append(d.isoformat())
        d += timedelta(days=1)
    return days, unknown
