"""주식수 시드(현재 주식수 + 수정주가 이벤트 역적용)가 ``market_cap_asof_bulk`` 와 왕복으로 맞는가.

시드가 틀리면 과거 시총 가중치가 배수로 틀린다(078130: 10:1 병합 전후 시총이 10배 — storage 주석).
"""

from __future__ import annotations

import pandas as pd
import pytest

from sectorflow.collect import write
from sectorflow.collect.shares import _share_event, seed_rows
from sectorflow.storage import market_cap_asof_bulk

CUR = 1_000_000
SNAP = "2026-11-08"
SEED = "2025-08-01"
#: 2025-12-01 2:1 분할(가격 −50% → 주식수 ×2), 2026-05-20 10:1 병합(가격 +900% → 주식수 ÷10), 배당락(무시).
EVENTS = [("2025-12-01", "8", -50.0), ("2026-03-02", "4", -1.2), ("2026-05-20", "16", 900.0)]


def test_seed_back_applies_share_events():
    (seed, now) = seed_rows("078130", CUR, SNAP, SEED, EVENTS)
    # 현재 = 시드 × 2 ÷ 10  →  시드 = 현재 × 5
    assert seed == ("078130", SEED, 5 * CUR)
    assert now == ("078130", SNAP, CUR)


def test_seed_on_or_after_snapshot_is_just_the_snapshot():
    assert seed_rows("000001", CUR, SNAP, SNAP, EVENTS) == [("000001", SNAP, CUR)]


@pytest.mark.parametrize("day,want_shares", [
    ("2025-08-01", 5 * CUR), ("2025-11-28", 5 * CUR),
    ("2025-12-01", 10 * CUR), ("2026-05-19", 10 * CUR),
    ("2026-05-20", CUR), ("2026-11-06", CUR),
])
def test_seed_round_trips_through_market_cap_asof(tmp_path, day, want_shares):
    con = write.open_db(tmp_path / "s.db")
    seeded = seed_rows("078130", CUR, SNAP, SEED, EVENTS)
    write.upsert_shares(con, seeded[:1], "seed")
    write.upsert_shares(con, seeded[1:], "kiwoom")
    con.executemany("INSERT INTO corporate_actions VALUES (?,?,?,?)",
                    [("078130", d, tp, rt) for d, tp, rt in EVENTS])
    con.execute("INSERT INTO supply_demand(code, date, close, source) VALUES (?,?,?,?)",
                ("078130", day, 1000, "kiwoom"))
    con.commit()
    cap = market_cap_asof_bulk(con, pd.DataFrame({"code": ["078130"], "date": [day]}))
    assert cap.iloc[0] == pytest.approx(1000 * want_shares, rel=1e-9)
    prov = dict(con.execute("SELECT date, source FROM shares_provenance").fetchall())
    assert prov == {SEED: "seed", SNAP: "kiwoom"}


@pytest.mark.parametrize("tp,rt,want", [
    ("16", 1.0, True), ("8", -50.0, True), ("16", 0.0, False), ("16", None, False), ("16", -100.0, False),
    ("4", -1.2, False), ("x", 50.0, False),
])
def test_share_event_counts_only_real_share_count_changes(tp, rt, want):
    """배율이 0·결측·−100% 이하이면 주식수 이벤트가 아니다(``log1p`` 가 0 이거나 터진다). 1% 는 진짜 이벤트다."""
    assert _share_event(tp, rt) is want


def _cap_of(con, day: str) -> float:
    return market_cap_asof_bulk(con, pd.DataFrame({"code": ["078130"], "date": [day]})).iloc[0]


def test_zero_close_in_supply_demand_falls_back_to_the_raw_price(tmp_path):
    """수급의 종가 0 은 '못 받음' 이다 — 그대로 곱하면 그날 시총이 0 이 되고 시총대비가 터진다."""
    con = write.open_db(tmp_path / "s.db")
    write.upsert_shares(con, [("078130", "2026-01-02", 1000)], "seed")
    con.execute("INSERT INTO supply_demand(code, date, close, source) VALUES (?,?,?,?)", ("078130", "2026-01-05", 0, "kiwoom"))
    con.execute("INSERT INTO daily_bars_raw(code, date, close) VALUES (?,?,?)", ("078130", "2026-01-05", 100))
    con.commit()
    assert _cap_of(con, "2026-01-05") == pytest.approx(100_000)


def test_minus_100_percent_event_is_not_a_share_event(tmp_path):
    """−100% 는 배율이 아니다(``log1p(-1)`` = −∞) — 세면 그 뒤 시총이 전부 무한대다."""
    con = write.open_db(tmp_path / "s.db")
    write.upsert_shares(con, [("078130", "2026-01-02", 1000)], "seed")
    con.execute("INSERT INTO corporate_actions VALUES (?,?,?,?)", ("078130", "2026-01-03", "16", -100.0))
    con.execute("INSERT INTO supply_demand(code, date, close, source) VALUES (?,?,?,?)", ("078130", "2026-01-05", 100, "kiwoom"))
    con.commit()
    assert _cap_of(con, "2026-01-05") == pytest.approx(100_000)
