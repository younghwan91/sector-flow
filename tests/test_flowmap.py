"""수급 지도 데이터 모델(``sectorflow.flowmap``) — 손으로 만든 작은 페이로드로 손계산과 맞춘다. DB·네트워크 없음.

그리는 간선은 (주체, 섹터)뿐이다 — 섹터→섹터·주체→주체 구조는 데이터에도 없다
(설계 ``2026-11-21-flow-map-brand-design.md`` §3.1, 원장 설계 ``2026-08-29-money-flow-ledger.md`` §2).
"""
from __future__ import annotations

import pytest

from sectorflow import flowmap as FM


def _flows(**by_actor):
    return {a: list(v) for a, v in by_actor.items()}


def _payload():
    """2시장 × 2섹터 × 3일. 코스닥에는 '나' 섹터가 없다(그 시장에 종목이 없는 섹터)."""
    return {
        "dates": ["2026-01-05", "2026-01-06", "2026-01-07"],
        "sectors": ["가", "나"],
        "markets": ["거래소", "코스닥"],
        "flows": {
            "거래소": {
                "가": _flows(indiv=[-10, -20, -30], forgn=[5, 10, 15], inst=[4, 8, 12], etc=[1, 1, 1]),
                "나": _flows(indiv=[7, None, 3], forgn=[-2, -2, -2], inst=[-4, -4, -4], etc=[0, 0, 0]),
            },
            "코스닥": {
                "가": _flows(indiv=[1, 2, 3], forgn=[-1, -1, -1], inst=[0, 0, 0], etc=[0, -1, 0]),
            },
        },
    }


def test_window_sums_adds_markets_and_days():
    S = FM.window_sums(_payload(), ["거래소", "코스닥"], 1, 2)
    assert list(S) == ["가", "나"], "섹터는 페이로드 순서"
    assert S["가"] == {"indiv": -50 + 5, "forgn": 25 - 2, "inst": 20, "etc": 2 - 1, "resid": -(-45 + 23 + 20 + 1)}
    assert S["나"]["indiv"] == 3 and S["나"]["forgn"] == -4 and S["나"]["inst"] == -8
    one = FM.window_sums(_payload(), ["거래소"], 0, 0)
    assert one["가"] == {"indiv": -10, "forgn": 5, "inst": 4, "etc": 1, "resid": 0}


def test_resid_is_negative_sum():
    S = FM.window_sums(_payload(), ["거래소", "코스닥"], 0, 2)
    for v in S.values():
        assert v["resid"] == pytest.approx(-sum(v[a] for a in FM.ACTORS), abs=1e-12)
        assert sum(v[a] for a in (*FM.ACTORS, FM.RESID)) == pytest.approx(0, abs=1e-12)
    assert S["나"]["resid"] == -(10 - 6 - 12 + 0)


def test_none_counts_as_zero():
    S = FM.window_sums(_payload(), ["거래소"], 1, 1)
    assert S["나"]["indiv"] == 0 and S["나"]["resid"] == 6
    assert FM.window_sums(_payload(), ["코스닥"], 0, 2)["나"] == {"indiv": 0, "forgn": 0, "inst": 0, "etc": 0, "resid": 0}
