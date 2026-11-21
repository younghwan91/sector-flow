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


def _sums(rows: dict) -> FM.Sums:
    """섹터 → 4주체 손값. 잔여는 window_sums 와 같은 규칙(−Σ4주체)으로 채운다."""
    out = {}
    for s, r in rows.items():
        row = {a: float(r.get(a, 0)) for a in FM.ACTORS}
        row[FM.RESID] = 0.0 - sum(row.values())
        out[s] = row
    return out


def _sector_rows(G):
    return [*G["sectors"], *([G["tail"]] if G["tail"] else [])]


def test_conservation_per_sector():
    S = FM.window_sums(_payload(), ["거래소", "코스닥"], 0, 2)
    S.update(_sums({"다": dict(indiv=-123.45, forgn=67.8, inst=40.1, etc=-3.3), "라": dict(indiv=0.1, forgn=-0.3, inst=0.7)}))
    G = FM.exchange_graph(S)
    for r in _sector_rows(G):
        assert sum(r["give"].values()) == pytest.approx(sum(r["take"].values()), abs=1e-9), r
    assert sum(G["left"].values()) == pytest.approx(G["gross"], abs=1e-9)
    assert sum(G["right"].values()) == pytest.approx(G["gross"], abs=1e-9)
    assert G["gross"] == pytest.approx(sum(sum(r["take"].values()) for r in _sector_rows(G)))


def test_resid_side_follows_sign():
    """잔여가 섹터 순매수보다 큰 칸(얇은 섹터·결측) — 잔여는 부호대로 받는 쪽·넘기는 쪽에 서고 보존식이 맞는다."""
    G = FM.exchange_graph(_sums({"얇음": dict(indiv=-100, forgn=10, inst=5), "넘침": dict(indiv=-20, inst=30, etc=5)}))
    by = {r["name"]: r for r in G["sectors"]}
    assert by["얇음"]["give"] == {"indiv": 100} and by["얇음"]["take"] == {"forgn": 10, "inst": 5, "resid": 85}
    assert by["넘침"]["give"] == {"indiv": 20, "resid": 15} and by["넘침"]["take"] == {"inst": 30, "etc": 5}
    assert G["left"]["resid"] == 15 and G["right"]["resid"] == 85


def test_zero_edges_dropped():
    """한 주체만 판 날 — 간선 0 인 주체는 키가 없고, 노드 높이는 0 이 아닌 쪽 합이다. 전부 0 인 섹터는 빠진다."""
    G = FM.exchange_graph(_sums({"가": dict(indiv=-50, inst=50), "빈": {}}))
    assert [r["name"] for r in G["sectors"]] == ["가"] and G["tail"] is None
    assert G["sectors"][0]["give"] == {"indiv": 50} and G["sectors"][0]["take"] == {"inst": 50}
    assert G["left"] == {"indiv": 50} and G["right"] == {"inst": 50} and G["gross"] == 50


def _ladder(n: int, big: float = 1000.0, small: float = 1.0) -> FM.Sums:
    """큰 섹터 몇 개와 작은 섹터들 — 노드 높이 = 기관 순매수 = 개인 순매도."""
    rows = {}
    for i in range(n):
        v = big - i if i < 3 else small + (n - i) * 0.01
        rows[f"s{i:02d}"] = dict(indiv=-v, inst=v)
    return _sums(rows)


def test_tail_collapses_small():
    S = _sums({f"s{i:02d}": dict(indiv=-(100 - i), inst=100 - i) for i in range(15)})   # 몫이 다 3% 이상, 15개
    G = FM.exchange_graph(S)
    assert [r["name"] for r in G["sectors"]] == [f"s{i:02d}" for i in range(FM.MAX_SHOWN)], "순위가 MAX_SHOWN 을 넘으면 꼬리"
    assert G["tail"]["names"] == ["s12", "s13", "s14"]
    assert G["tail"]["give"] == {"indiv": 88 + 87 + 86} and G["tail"]["take"] == {"inst": 88 + 87 + 86}
    S2 = _sums({"큰1": dict(indiv=-500, inst=500), "큰2": dict(indiv=-300, forgn=300), "큰3": dict(etc=-100, inst=100),
                "큰4": dict(indiv=-60, inst=60), "큰5": dict(indiv=-40, inst=40),
                "작1": dict(indiv=-20, inst=20), "작2": dict(forgn=-5, inst=5)})
    G2 = FM.exchange_graph(S2)          # gross 1025 — 작1 1.95%, 작2 0.49% 는 문턱 아래
    assert [r["name"] for r in G2["sectors"]] == ["큰1", "큰2", "큰3", "큰4", "큰5"]
    assert G2["tail"] == {"names": ["작1", "작2"], "give": {"indiv": 20, "forgn": 5}, "take": {"inst": 25}}


def test_tail_keeps_minimum():
    """모든 섹터가 문턱 아래로 들어가는 날(작은 시장·1일)에도 최소 MIN_SHOWN 섹터는 남는다."""
    G = FM.exchange_graph(_ladder(10, big=10_000.0))
    assert len(G["sectors"]) == FM.MIN_SHOWN and len(G["tail"]["names"]) == 10 - FM.MIN_SHOWN
    few = FM.exchange_graph(_sums({"a": dict(indiv=-1000, inst=1000), "b": dict(indiv=-1, inst=1)}))
    assert [r["name"] for r in few["sectors"]] == ["a", "b"] and few["tail"] is None, "섹터가 MIN_SHOWN 보다 적으면 다 보인다"


def _keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from _keys(v)
    elif isinstance(o, list):
        for v in o:
            yield from _keys(v)


def test_no_sector_to_sector_keys():
    """섹터 이름은 값(``name``·``names``)으로만 나온다 — 어느 dict 의 키도 섹터가 아니다(섹터→섹터 짝을 만들지 않는다).
    give·take 의 키는 주체뿐이다(주체→주체 짝도 없다: 한 간선의 두 끝은 늘 주체 하나와 섹터 하나)."""
    S = FM.window_sums(_payload(), ["거래소", "코스닥"], 0, 2)
    S.update(_ladder(16))
    G = FM.exchange_graph(S)
    assert not set(_keys(G)) & set(S)
    for r in _sector_rows(G):
        assert set(r["give"]) | set(r["take"]) <= {*FM.ACTORS, FM.RESID}
    assert set(G) == {"sectors", "tail", "left", "right", "gross"}
