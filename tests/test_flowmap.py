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
    S = _sums({f"s{i:02d}": dict(indiv=-(100 - i), inst=100 - i) for i in range(15)})   # 몫이 다 1% 이상, 15개
    G = FM.exchange_graph(S)
    assert [r["name"] for r in G["sectors"]] == [f"s{i:02d}" for i in range(FM.MAX_SHOWN)], "순위가 MAX_SHOWN 을 넘으면 꼬리"
    assert G["tail"]["names"] == ["s12", "s13", "s14"]
    assert G["tail"]["give"] == {"indiv": 88 + 87 + 86} and G["tail"]["take"] == {"inst": 88 + 87 + 86}
    S2 = _sums({**{f"큰{i}": dict(indiv=-100, inst=100) for i in range(1, 9)},
                "작1": dict(indiv=-5, inst=5), "작2": dict(forgn=-2, inst=2)})
    G2 = FM.exchange_graph(S2)          # gross 807 — 작1 0.62%, 작2 0.25% 는 문턱(1%) 아래, 큰 8개 = MIN_SHOWN
    assert [r["name"] for r in G2["sectors"]] == [f"큰{i}" for i in range(1, 9)]
    assert G2["tail"] == {"names": ["작1", "작2"], "give": {"indiv": 5, "forgn": 2}, "take": {"inst": 7}}


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


def test_actor_graph_splits_by_sign():
    """보기 2 — 한 주체가 순매도한 섹터(gave)와 순매수한 섹터(took), 금액 큰 순. 0 인 섹터는 어느 쪽에도 없다."""
    S = _sums({"가": dict(forgn=-30, indiv=30), "나": dict(forgn=50, indiv=-50), "다": dict(forgn=-10, indiv=10),
               "라": dict(indiv=5, etc=-5)})
    A = FM.actor_graph(S, "forgn")
    assert A["actor"] == "forgn"
    assert A["gave"] == [["가", 30], ["다", 10]] and A["took"] == [["나", 50]]
    assert A["tail_gave"] is None and A["tail_took"] is None
    R = FM.actor_graph(S, FM.RESID)
    assert R["gave"] == [] and R["took"] == [] and R["net"] == 0, "잔여도 주체처럼 누를 수 있다 — 여기선 전부 0"


def test_net_equals_took_minus_gave():
    S = _sums({f"s{i:02d}": dict(inst=(-1) ** i * (200 - 10 * i), indiv=-((-1) ** i) * (200 - 10 * i)) for i in range(16)}
              | {f"t{i}": dict(inst=1 + i * 0.1, indiv=-(1 + i * 0.1)) for i in range(4)})
    A = FM.actor_graph(S, "inst")
    shown = sum(v for _s, v in A["took"]) - sum(v for _s, v in A["gave"])
    tails = (A["tail_took"][1] if A["tail_took"] else 0) - (A["tail_gave"][1] if A["tail_gave"] else 0)
    assert A["net"] == pytest.approx(shown + tails) == pytest.approx(sum(r["inst"] for r in S.values()))
    assert A["tail_took"] is not None and set(A["tail_took"][0]) >= {f"t{i}" for i in range(4)}, "주체의 gross 대비 작은 섹터는 꼬리"
    assert len(A["took"]) <= FM.MAX_SHOWN and len(A["gave"]) >= FM.MIN_SHOWN


def test_unknown_actor_raises():
    S = _sums({"가": dict(indiv=-1, inst=1)})
    with pytest.raises(KeyError):
        FM.actor_graph(S, "foreign")
    with pytest.raises(KeyError):
        FM.actor_graph(S, "가")


#: 페이로드 ``detail_labels`` 순서 그대로(``sector_flow.py`` 가 싣는 순서) — 화면 순서는 투신·연기금이 먼저다.
_LABELS = {"fnnc_invt": "금투", "insrnc": "보험", "invtrt": "투신", "bank": "은행", "penfnd_etc": "연기금", "samo_fund": "사모"}


def _payload_detail():
    """_payload 에 기관 세부를 붙인다 — 거래소 '가' 의 기관(4, 8, 12) 중 일부만 세부로 잡힌다(나머지는 inst_other)."""
    P = _payload()
    P["detail_labels"] = dict(_LABELS)
    P["detail"] = {
        "거래소": {"가": {"fnnc_invt": [1, 1, 1], "insrnc": [0, 0, 0], "invtrt": [2, 3, None], "bank": [0, 0, 0],
                         "penfnd_etc": [1, 2, 4], "samo_fund": [0, 1, 0]},
                  "나": {"invtrt": [-1, -1, -1], "penfnd_etc": [-2, -2, -2]}},
    }
    return P


def test_detail_plus_other_equals_inst():
    """세부 6종 + 나머지 = 기관 — 나머지는 안분하지 않고 따로 둔다(잔여와 같은 원칙)."""
    P = _payload_detail()
    S = FM.window_sums(P, ["거래소", "코스닥"], 0, 2)
    D = FM.inst_detail_sums(P, ["거래소", "코스닥"], 0, 2)
    assert list(D) == ["가", "나"]
    for s in D:
        assert sum(D[s].values()) == pytest.approx(S[s]["inst"], abs=1e-12)
    assert D["가"]["invtrt"] == 5 and D["가"]["penfnd_etc"] == 7 and D["가"]["inst_other"] == 24 - (3 + 5 + 7 + 1)
    assert D["나"]["inst_other"] == -12 - (-3 - 6) and D["나"]["bank"] == 0, "그 섹터에 없는 세부 키는 0"


def test_actor_graph_accepts_detail_key():
    P = _payload_detail()
    S = FM.with_detail(FM.window_sums(P, ["거래소"], 0, 2), FM.inst_detail_sums(P, ["거래소"], 0, 2))
    A = FM.actor_graph(S, "inst:penfnd_etc")
    assert A["took"] == [["가", 7]] and A["gave"] == [["나", 6]] and A["net"] == 1
    assert FM.actor_graph(S, "inst:inst_other")["net"] == pytest.approx(24 + (-12) - (16 - 9))
    assert FM.exchange_graph(S) == FM.exchange_graph(FM.window_sums(P, ["거래소"], 0, 2)), "세부 키는 보기 1 을 바꾸지 않는다"
    with pytest.raises(KeyError):
        FM.actor_graph(S, "inst:nope")
    with pytest.raises(KeyError):
        FM.actor_graph(FM.window_sums(P, ["거래소"], 0, 2), "inst:invtrt")


def test_detail_order_puts_invtrt_penfnd_first():
    """수급 신뢰 순서 — 기관 안에서는 투신·연기금을 먼저 보인다(CLAUDE.md). 나머지는 페이로드 순서, 끝에 나머지(inst_other)."""
    D = FM.inst_detail_sums(_payload_detail(), ["거래소"], 0, 2)
    assert list(D["가"]) == ["invtrt", "penfnd_etc", "fnnc_invt", "insrnc", "bank", "samo_fund", "inst_other"]
    assert FM.detail_keys(_payload_detail()) == ["invtrt", "penfnd_etc", "fnnc_invt", "insrnc", "bank", "samo_fund"]


def _long_payload(n_days: int = 8, n_sec: int = 8):
    """한 시장, 날마다 순위가 바뀌는 섹터들 — 섹터 i 의 기관 순매수는 (i + 3·날) mod n_sec 에 비례, 개인이 반대로."""
    secs = [f"섹{i}" for i in range(n_sec)]
    flows = {}
    for i, s in enumerate(secs):
        v = [float(((i + 3 * d) % n_sec + 1) * 10) for d in range(n_days)]
        flows[s] = _flows(indiv=[-x for x in v], forgn=[0.0] * n_days, inst=v, etc=[0.0] * n_days)
    return {"dates": [f"2026-02-{d + 1:02d}" for d in range(n_days)], "sectors": secs, "markets": ["거래소"],
            "flows": {"거래소": flows}}


def test_frames_order_fixed():
    """노드 순서는 구간 전체 합으로 한 번 정한다 — 프레임마다 다시 줄세우면 띠가 깜빡인다. 꼬리 집합도 고정이다."""
    P = _long_payload()
    T = FM.frames(P, ["거래소"], span=1, n=6)
    assert len(T["frames"]) == 6
    days = [[r["name"] for r in sorted(f["sectors"], key=lambda r: -sum(r["take"].values()))] for f in T["frames"]]
    assert len({tuple(d) for d in days}) > 1, "픽스처가 날마다 순위를 바꾸지 않으면 이 검사는 헛돈다"
    whole = FM.exchange_graph(FM.window_sums(P, ["거래소"], 2, 7))
    assert T["order"] == [r["name"] for r in whole["sectors"]]
    for f in T["frames"]:
        assert [r["name"] for r in f["sectors"]] == T["order"]
        assert set((f["tail"] or {"names": []})["names"]) <= set(P["sectors"]) - set(T["order"])


def test_frame_count_capped_by_dates():
    P = _payload()
    T = FM.frames(P, ["거래소", "코스닥"])
    assert T["dates"] == P["dates"] and len(T["frames"]) == 3, "n=60 이어도 날짜 수까지"
    T2 = FM.frames(P, ["거래소", "코스닥"], span=2, n=60)
    assert T2["dates"] == P["dates"][1:] and len(T2["frames"]) == 2, "5일 이동합은 앞의 span−1 일이 프레임이 되지 못한다"
    assert FM.frames(P, ["거래소"], span=5)["frames"] == [] and FM.frames(P, ["거래소"], span=5)["dates"] == []


def test_span5_is_rolling_sum():
    P = _long_payload(n_days=8)
    T = FM.frames(P, ["거래소"], span=5, n=3)
    assert T["dates"] == P["dates"][5:]
    for f, j in zip(T["frames"], range(5, 8)):
        G = FM.exchange_graph(FM.window_sums(P, ["거래소"], j - 4, j))
        want = {r["name"]: (r["give"], r["take"]) for r in G["sectors"]}
        if G["tail"]:
            assert set(G["tail"]["names"]) | set(want) <= set(P["sectors"])
        got = {r["name"]: (r["give"], r["take"]) for r in f["sectors"]}
        for s, gt in want.items():
            if s in got:
                assert got[s] == gt, (j, s)
        assert f["left"] == G["left"] and f["right"] == G["right"] and f["gross"] == pytest.approx(G["gross"])


@pytest.fixture(scope="module")
def demo_payload(tmp_path_factory):
    """합성 데모의 페이로드 — 데모 DB 를 짓고 배치와 같은 ``sector_flow.py`` 로 한 번(약 6초). 실데이터를 쓰지 않는다."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    from sectorflow import demo

    root = tmp_path_factory.mktemp("flowmap-demo")
    demo.build_db(root / "demo.db")
    script = Path(__file__).resolve().parent.parent / "scripts" / "sector_flow.py"
    subprocess.run([sys.executable, str(script), "--db", str(root / "demo.db"), "--days", str(demo.REPORT_DAYS),
                    "--reports-root", str(root / "r"), "--final", "--json", str(root / "payload.json")],
                   check=True, capture_output=True, timeout=300)
    return json.loads((root / "payload.json").read_text("utf-8"))


def test_headline_uses_summary(monkeypatch):
    """가장 큰 주체는 ``summary.dominant`` 가 고른다 — 지도가 규칙을 다시 정하지 않는다."""
    P = _long_payload()
    H = FM.map_headline(P, "거래소", 1)
    assert H["lead"].startswith("기준일 거래소 — ") and "개인이 가장 크게 순매도 −" in H["lead"]
    assert "기관이 가장 크게 순매수 +" in H["lead"]
    picked = []

    def fake(net):
        picked.append(sorted(net))
        return "etc" if "etc" in net else None
    monkeypatch.setattr(FM.summary, "dominant", fake)
    P["flows"]["거래소"]["섹0"]["etc"] = [5.0] * 8
    H2 = FM.map_headline(P, "거래소", 5)
    assert H2["lead"] == "최근 5거래일 거래소 — 기타법인이 가장 크게 순매수 +25억"
    assert picked == [["indiv"], ["etc", "inst"]], "부호별로 나눠 부른다 — 순매도 쪽, 순매수 쪽"


def test_headline_facts_are_marginal_edges():
    """사실 줄은 (섹터, 주체) 간선 큰 순 — '가상전자 — 개인 −2,927억' 꼴, 두 주체를 잇는 말이 없다."""
    S = _payload()
    H = FM.map_headline(S, FM.ALL_MARKETS, 3)
    assert H["facts"] == ["가 — 개인 −54억", "가 — 외국인 +27억", "가 — 기관 +24억"]
    one = FM.map_headline(S, "코스닥", 1)
    assert one["facts"] == ["가 — 개인 +3억", "가 — 외국인 −1억"], "0 간선은 사실이 아니다"


def test_headline_wording_clean(demo_payload):
    from sectorflow.wording import map_hits

    for m in (FM.ALL_MARKETS, *demo_payload["markets"]):
        for w in (1, 5, 20):
            H = FM.map_headline(demo_payload, m, w)
            assert len(H["facts"]) == FM.N_FACTS
            assert map_hits(" · ".join([H["lead"], *H["facts"]])) == [], (m, w, H)
            assert all(" — " in f and f.endswith(("억", "조")) for f in H["facts"])


def test_windows_beyond_dates_skipped():
    """창이 날짜 수보다 길면(BYO 20일 폴더) 그 창은 빠진다 — 화면은 없는 창의 버튼을 감춘다."""
    P = _long_payload(n_days=25)
    D = FM.build_map_data(P)
    assert D["windows"] == [1, 5, 20] and D["markets"] == ["전체", "거래소"]
    assert list(D["views"]["전체"]) == ["1", "5", "20"]
    assert D["asof"] == P["dates"][-1] and D["demo"] is False
    v = D["views"]["거래소"]["20"]
    assert v["exchange"] == FM.exchange_graph(FM.window_sums(P, ["거래소"], 5, 24))
    assert set(v["actors"]) == {*FM.ACTORS, FM.RESID}, "기관 세부가 없는 페이로드는 세부 주체가 없다"
    assert len(D["timelapse"]["전체"]["frames"]) == 25


def test_size_budget(demo_payload, monkeypatch):
    import json

    D = FM.build_map_data(demo_payload)
    assert len(json.dumps(D, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= FM.MAP_JSON_MAX
    assert D["windows"] == list(FM.MAP_WINDOWS) and len(D["timelapse"]["전체"]["frames"]) == FM.TIMELAPSE_N
    assert "inst:invtrt" in D["views"]["전체"]["20"]["actors"] and D["labels"]["inst:invtrt"] == "투신"
    json.loads(json.dumps(D))
    monkeypatch.setattr(FM, "MAP_JSON_MAX", FM._size({**D, "timelapse": {}}) + 60_000)
    small = FM.build_map_data(demo_payload)
    assert FM._size(small) <= FM.MAP_JSON_MAX, "상한을 넘으면 타임랩스를 줄여 맞춘다"
    assert 0 < len(small["timelapse"]["전체"]["frames"]) < FM.TIMELAPSE_N
    assert small["views"] == D["views"], "보기 1·2 는 줄이지 않는다"


def test_flows_only_payload_ok(demo_payload):
    """수익률·오늘 블록 없이 수급만 든 페이로드(BYO --flows-only 의 전제)로도 지도가 선다."""
    keep = ("flows", "detail", "sectors", "markets", "dates", "n_by_sector")
    P = {k: demo_payload[k] for k in keep}
    D = FM.build_map_data(P)
    assert D["views"]["전체"]["5"]["exchange"] == FM.build_map_data(demo_payload)["views"]["전체"]["5"]["exchange"]
    assert "inst:invtrt" in D["views"]["전체"]["5"]["actors"], "라벨 표가 없어도 세부 키는 페이로드에서 읽는다"
    assert list(D["views"]["코스닥"]["1"]["actors"])[5:7] == ["inst:invtrt", "inst:penfnd_etc"]


# ── 시간 흐름(두 번째 보기) — 주체 하나의 섹터별 하루 순매수 띠 ───────────────────────────

def test_stream_series_adds_markets_and_keeps_daily_values():
    """두 시장을 날마다 더한다(None 은 0). 그 시장에 없는 섹터도 0. 값은 반올림 자릿수까지 손계산과 같다."""
    P = _payload()
    T = FM.stream_series(P, ["거래소", "코스닥"], "indiv", n=3)
    assert T["dates"] == P["dates"]
    by = {s["name"]: s["v"] for s in T["series"]}
    assert by["가"] == [-9, -18, -27] and by["나"] == [7, 0, 3]
    assert FM.stream_series(P, ["거래소"], "inst", n=2) == {
        "dates": P["dates"][1:], "series": [{"name": "가", "v": [8, 12]}, {"name": "나", "v": [-4, -4]}]}


def test_stream_series_orders_by_gross_and_bundles_the_rest():
    """구간 Σ|하루 값| 큰 순 STREAM_TOP 개, 나머지는 날마다 더한 '기타 N개 섹터' 한 띠(이름 목록 포함). 구간 내내 0 인 섹터는 없다."""
    P = _long_payload(n_days=8, n_sec=14)
    P["flows"]["거래소"]["섹13"]["inst"] = [0.0] * 8                     # 내내 0 — 띠도, 기타 이름에도 없다
    T = FM.stream_series(P, ["거래소"], "inst", n=8)
    named, rest = T["series"][:-1], T["series"][-1]
    assert len(named) == FM.STREAM_TOP and rest["rest"] is True
    gross = [sum(abs(x) for x in s["v"]) for s in named]
    assert gross == sorted(gross, reverse=True)
    assert rest["name"] == f"기타 {len(rest['names'])}개 섹터" and "섹13" not in rest["names"]
    assert sorted([s["name"] for s in named] + rest["names"]) == sorted(f"섹{i}" for i in range(13))
    for j in range(8):
        want = sum(P["flows"]["거래소"][s]["inst"][j] for s in rest["names"])
        assert rest["v"][j] == pytest.approx(want, abs=0.051)


def test_stream_has_no_sector_to_sector_structure_and_rejects_unknown_actor():
    """띠는 (섹터, 날짜) 값뿐 — 섹터와 섹터를 잇는 칸이 없다. 잔여는 주체가 아니라 시간 흐름에 없다."""
    P = _long_payload(n_days=6, n_sec=12)
    T = FM.stream_series(P, ["거래소"], "forgn", n=5)
    for s in T["series"]:
        assert set(s) <= {"name", "v", "rest", "names"} and len(s["v"]) == 5
    with pytest.raises(KeyError):
        FM.stream_series(P, ["거래소"], FM.RESID)


def test_stream_data_in_the_map_bundle(demo_payload):
    """지도 묶음의 stream — 날짜는 한 번, 시장(전체·각 시장) × 4주체. 120거래일(데모 260일), 날짜가 적으면 그만큼."""
    D = FM.build_map_data(demo_payload)
    S = D["stream"]
    assert S["dates"] == demo_payload["dates"][-FM.STREAM_N:]
    assert list(S["views"]) == D["markets"] and all(list(v) == list(FM.ACTORS) for v in S["views"].values())
    assert S["views"]["코스닥"]["inst"] == FM.stream_series(demo_payload, ["코스닥"], "inst")["series"]
    short = FM.build_map_data(_long_payload(n_days=25))["stream"]
    assert len(short["dates"]) == 25 and all(len(s["v"]) == 25 for s in short["views"]["전체"]["inst"])
