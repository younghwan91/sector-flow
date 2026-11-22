"""``sector-flow-collect backfill --map-only`` — 지도 전용 수집, 가짜 전송으로(네트워크 없음).

지키는 것: 종목 목록·수급(ka10099·ka10059) 둘만 부른다 — 클라이언트가 그 밖의 TR 을 요청 전에 막는다 · 하한은
KRX 휴장 달력 정본으로 센 ``--days`` 거래일(기본 120)이고 끝날은 ``daily`` 와 같은 :func:`run.target_day` 다 ·
일봉·주식수·업종지수 표는 비어 있다 · 이어서 ``daily --map-only`` 가 다음 거래일만 더한다.
"""

from __future__ import annotations

import sqlite3

import pytest

from sectorflow.collect import run as R
from tests import test_collect_daily as TD
from tests.test_collect_daily import CAL, FakeServer, _dates, _patch


@pytest.fixture(autouse=True)
def _long_truth(monkeypatch):
    """가짜 서버가 120거래일을 덮게 — 2026 년 거래일 전부."""
    monkeypatch.setattr(TD, "TRUTH", CAL.trading_days("2026-01-02", "2026-12-30")[0])


def _window(end: str, n: int) -> list[str]:
    return CAL.trading_days("2026-01-01", end)[0][-n:]


def test_map_only_calls_two_trs(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    fake = FakeServer()
    asked = _patch(monkeypatch, fake, "2026-11-20 21:00")
    assert R.main(["backfill", "--db", str(db), "--map-only", "--sectors", "운송/창고"]) == 0
    assert asked["only"] == R.map_only_trs                  # 클라이언트가 그 밖의 TR 을 요청 전에 막는다
    assert set(fake.trs()) == {"ka10099", "ka10059"}
    # 120거래일은 ka10059 두 쪽(한 쪽 약 100일) — 종목마다 2회, 그 앞은 안 받는다.
    assert fake.trs().count("ka10059") == 4
    assert all(b["dt"] == "20261120" for t, b in fake.seen if t == "ka10059")
    con = sqlite3.connect(db)
    want = _window("2026-11-20", R.MAP_ONLY_DAYS)
    for code in ("000120", "044490"):
        assert _dates(con, code) == want, "기본 창은 KRX 달력으로 센 120거래일이다"
    for t in ("daily_bars", "daily_bars_adjusted", "daily_bars_raw", "sector_index", "shares_outstanding_history"):
        assert con.execute(f"SELECT count(*) FROM {t}").fetchone() == (0,), t
    kind, ok, asof = con.execute("SELECT kind, ok, asof FROM collect_runs").fetchone()
    assert (kind, ok, asof) == ("backfill-map", 1, "2026-11-20")


def test_map_only_window_follows_days_and_the_close(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    fake = FakeServer()
    _patch(monkeypatch, fake, "2026-10-12 08:00")           # 월요일 장 전 — 끝날은 한글날 앞 10-08
    assert R.main(["backfill", "--db", str(db), "--map-only", "--days", "30"]) == 0
    con = sqlite3.connect(db)
    assert _dates(con, "000120") == _window("2026-10-08", 30)
    assert fake.trs().count("ka10059") == 2                 # 30일은 한 쪽 — 종목당 1회


def test_map_only_then_daily_adds_only_the_next_day(tmp_path, monkeypatch):
    """문서의 순서 그대로 — 지도 전용 백필 뒤 다음 거래일 저녁의 ``daily --map-only`` 는 그날(과 마지막 저장일)만 받는다."""
    db = tmp_path / "m.db"
    _patch(monkeypatch, FakeServer(), "2026-11-20 21:00")
    assert R.main(["backfill", "--db", str(db), "--map-only", "--days", "30"]) == 0
    fake = FakeServer()
    _patch(monkeypatch, fake, "2026-11-23 21:00")
    assert R.main(["daily", "--db", str(db), "--map-only"]) == 0
    con = sqlite3.connect(db)
    assert _dates(con, "000120") == _window("2026-11-23", 31)
    assert set(fake.trs()) == {"ka10099", "ka10059"} and fake.trs().count("ka10059") == 2

