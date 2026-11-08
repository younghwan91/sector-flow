"""상장주식수 시드 — 키움 키 하나로 과거 시총 가중치를 만든다(설계 §3 대안 B).

ka10001 은 **현재** 상장주식수만 준다. 이력이 없으면 ``market_cap_asof_bulk`` 가 과거 날짜에 NaN 을 내고
섹터 수익률이 조용히 0 이 된다(``scripts/sector_flow.py`` ``if w else 0.0``). 그래서 현재 주식수에 구간 안의
분할·병합·감자(``storage.SHARE_EVENTS``)를 **거꾸로** 적용해 구간 첫날 행 하나를 만든다.

``market_cap_asof_bulk`` 는 주식수 행 ``s`` 뒤의 이벤트를 ``주식수(t) = 주식수(s) × exp(C(t) − C(s))``,
``C(x) = Σ_{이벤트일 ≤ x} −ln(1 + upd_rt/100)`` 로 앞으로 다시 곱한다. 시드를

    ``seed = 현재 × exp(C(시드일) − C(스냅숏일))``

로 두면 그 식이 스냅숏일에 정확히 현재 주식수로 돌아온다(왕복 항등 — ``tests/test_collect_shares.py``).

못 잡는 것: 유상증자·CB 전환·자사주 소각처럼 수정주가 이벤트가 아닌 주식수 변화. 그 차이가 시총 가중치 오차가
된다(동등성 문서에 크기를 적는다). 정확한 대안은 DART ``stockTotqySttus`` 분기 시점값이다(선택, 미구현).
"""

from __future__ import annotations

import math

from sectorflow.storage import SHARE_EVENTS


def _share_event(tp: str, rt: "float | None") -> bool:
    try:
        bits = int(str(tp).strip())
    except ValueError:
        return False
    return bool(bits & SHARE_EVENTS) and rt is not None and rt != 0 and rt > -100


def _c_at(events: list[tuple[str, str, "float | None"]], day: str) -> float:
    """``C(day)`` — ``day`` 이하 이벤트의 −ln(1+upd_rt/100) 합. ``events`` = (date, tp, upd_rt)."""
    return sum(-math.log1p(rt / 100.0) for d, tp, rt in events
               if d <= day and _share_event(tp, rt))


def seed_rows(code: str, current: int, snapshot_date: str, seed_date: str,
              events: list[tuple[str, str, "float | None"]]) -> list[tuple[str, str, int]]:
    """``[(code, seed_date, 시드 주식수), (code, snapshot_date, 현재 주식수)]``.

    시드일이 스냅숏일 이상이면 현재 행 하나만 돌려준다.
    """
    rows = [(code, snapshot_date, int(current))]
    if seed_date >= snapshot_date:
        return rows
    seed = current * math.exp(_c_at(events, seed_date) - _c_at(events, snapshot_date))
    return [(code, seed_date, int(round(seed)))] + rows


def reseed(con, codes: "list[str] | None" = None) -> int:
    """저장된 키움 스냅숏·``corporate_actions``·첫 수급일로 시드 행을 다시 만든다(키움 호출 없음).

    종목마다 가장 늦은 ``kiwoom`` 스냅숏을 현재 값으로 본다. 옛 시드 행은 지우고 새로 쓴다(멱등).
    Returns: 쓴 시드 행 수.
    """
    from . import write

    if codes is None:
        codes = [r[0] for r in con.execute(
            "SELECT DISTINCT code FROM shares_provenance WHERE source = 'kiwoom'").fetchall()]
    n = 0
    for code in codes:
        snap = con.execute(
            "SELECT h.date, h.shares_outstanding FROM shares_outstanding_history h JOIN shares_provenance p "
            "USING (code, date) WHERE code = ? AND p.source = 'kiwoom' ORDER BY h.date DESC LIMIT 1",
            (code,)).fetchone()
        first = con.execute("SELECT min(date) FROM supply_demand WHERE code = ?", (code,)).fetchone()[0]
        old = [r[0] for r in con.execute(
            "SELECT date FROM shares_provenance WHERE code = ? AND source = 'seed'", (code,)).fetchall()]
        for d in old:
            con.execute("DELETE FROM shares_outstanding_history WHERE code = ? AND date = ?", (code, d))
            con.execute("DELETE FROM shares_provenance WHERE code = ? AND date = ?", (code, d))
        if snap is None or first is None:
            continue
        events = [(str(d), str(tp), rt) for d, tp, rt in con.execute(
            "SELECT date, upd_stkpc_tp, upd_rt FROM corporate_actions WHERE code = ?", (code,)).fetchall()]
        *seed, _ = seed_rows(code, int(snap[1]), str(snap[0]), str(first), events)
        n += write.upsert_shares(con, seed, "seed")
    con.commit()
    return n
