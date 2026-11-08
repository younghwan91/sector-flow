"""KRX 휴장 달력(``scripts/krx_calendar.py``) — 수급이 빈 평일의 휴장·수집 실패를 가르고 SLO 분모를 정한다."""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("krx_calendar", REPO / "scripts" / "krx_calendar.py")
kc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kc)

#: kr_quant 에서 ``supply_demand``·``daily_bars`` 가 둘 다 빈 평일(2026-01-01 ~ 11-06, 2026-11-09 조회) — 달력의 실측 대조.
MEASURED_2026 = ["2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02", "2026-05-01", "2026-05-05",
                 "2026-05-25", "2026-06-03", "2026-07-17", "2026-08-17", "2026-09-24", "2026-09-25", "2026-10-05",
                 "2026-10-09"]


def test_2026_matches_the_days_the_database_has_no_rows():
    days = [d for d in kc.HOLIDAYS[2026] if d <= "2026-11-06"]
    assert days == MEASURED_2026


@pytest.mark.parametrize("year", sorted(kc.HOLIDAYS))
def test_every_entry_is_a_weekday_of_its_year_in_order(year):
    """주말을 적으면 분모가 안 변해 틀린 줄이 조용히 남는다 — 대체공휴일을 잘못 옮긴 신호다."""
    days = list(kc.HOLIDAYS[year])
    assert days == sorted(days) and all(d.startswith(f"{year}-") for d in days)
    assert all(date.fromisoformat(d).weekday() < 5 for d in days), days


def test_trading_day_answers():
    assert kc.is_trading_day("2026-11-06") is True
    assert kc.is_trading_day("2026-10-09") is False                   # 한글날
    assert kc.is_trading_day("2026-11-07") is False                   # 토요일
    assert kc.is_trading_day("2029-01-02") is None                    # 달력 밖은 모른다 — 휴장이라 하지 않는다
    assert kc.is_trading_day("2029-01-06") is False                   # 달력 밖이어도 토요일은 토요일
    assert kc.holiday_name("2026-10-09") == "한글날" and kc.holiday_name("2026-11-06") is None


def test_previous_trading_day_steps_over_weekends_and_holidays():
    assert kc.prev_trading_day("2026-11-06") == "2026-11-05"
    assert kc.prev_trading_day("2026-11-09") == "2026-11-06"          # 월 → 금
    assert kc.prev_trading_day("2026-10-12") == "2026-10-08"          # 주말과 10-09 휴장을 건넌다
    assert kc.prev_trading_day("2026-01-02") is None                  # 2025 는 달력 밖
    assert kc.prev_trading_day("2029-06-05") is None


def test_trading_days_split_known_from_unknown():
    assert kc.trading_days("2026-10-08", "2026-10-13") == (["2026-10-08", "2026-10-12", "2026-10-13"], [])
    days, unknown = kc.trading_days("2027-12-30", "2028-01-04")
    assert days == ["2027-12-30"] and unknown == ["2028-01-03", "2028-01-04"]
