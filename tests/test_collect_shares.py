"""주식수 시드(현재 주식수 + 수정주가 이벤트 역적용)가 ``market_cap_asof_bulk`` 와 왕복으로 맞는가.

시드가 틀리면 과거 시총 가중치가 배수로 틀린다(078130: 10:1 병합 전후 시총이 10배 — storage 주석).
"""

from __future__ import annotations

import pandas as pd
import pytest

from sectorflow.collect import write
from sectorflow.collect.shares import seed_rows
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
