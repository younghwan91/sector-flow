"""수급 지도(``map.html``)의 데이터 — 페이로드 ``flows`` 에서 **주체 ↔ 섹터** 이분 그래프를 짓는다. 표준 라이브러리만.

관측되는 것은 (날짜, 시장, 섹터, 주체) 순매수뿐이다. 그래서 간선은 (주체, 섹터) 하나하나이고, 섹터→섹터·주체→주체
구조는 데이터에도 만들지 않는다 — 돈에 꼬리표가 없고(섹터 쌍), 주변합 4개로 쌍별 이전량 6개가 정해지지 않는다(주체 쌍).
근거는 원장 설계 ``docs/superpowers/specs/2026-08-29-money-flow-ledger.md`` §2, 지도 설계 ``2026-11-21-flow-map-brand-design.md``
§3.1. 4주체 합의 잔차는 4주체에 안분하지 않고 별도 노드 :data:`RESID` 로 둔다(원장 §2.3).

단위는 페이로드와 같은 억원(종가 환산 근사)이다. 반환값은 전부 JSON 으로 직렬화되는 dict/list 다.
"""
from __future__ import annotations

#: 페이로드 ``flows[시장][섹터]`` 의 주체 키 — 순서가 화면의 주체 노드 순서다(양쪽 같게).
ACTORS = ("indiv", "forgn", "inst", "etc")
#: 잔여 = ``−Σ ACTORS`` — KRX 분류 중 싣지 않는 주체(기타외국인 등)의 순매수 추정치이자 근사 오차.
RESID = "resid"
ACTOR_LABEL = {"indiv": "개인", "forgn": "외국인", "inst": "기관", "etc": "기타법인", "resid": "잔여"}

#: 섹터 → 주체(+잔여) → 억원.
Sums = dict[str, dict[str, float]]


def _span(vals, i0: int, i1: int) -> float:
    return float(sum(v for v in vals[i0:i1 + 1] if v is not None))


def window_sums(P: dict, markets: list[str], i0: int, i1: int) -> Sums:
    """``markets`` 를 더한 섹터별 주체 순매수 — 날짜 색인 ``i0`` 부터 ``i1`` 까지(**양끝 포함**).

    ``None`` 은 0 으로 센다(미보고·결측 — 0 과 구별하지 않는다, 합계 자리라서). 그 시장에 없는 섹터도 0 이다.
    섹터 순서는 ``P["sectors"]`` 이고, :data:`RESID` 는 4주체 합의 부호를 뒤집은 값이다.
    """
    out: Sums = {}
    for s in P["sectors"]:
        row = {a: 0.0 for a in ACTORS}
        for m in markets:
            f = (P["flows"].get(m) or {}).get(s)
            if not f:
                continue
            for a in ACTORS:
                row[a] += _span(f.get(a) or (), i0, i1)
        row[RESID] = 0.0 - sum(row[a] for a in ACTORS)    # 0.0 − : 합이 0 이면 −0.0 이 아니라 0.0
        out[s] = row
    return out
