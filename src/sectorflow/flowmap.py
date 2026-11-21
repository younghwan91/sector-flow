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


#: 꼬리 묶기 — 섹터 몫(노드 높이 ÷ gross)이 이보다 작거나 순위가 :data:`MAX_SHOWN` 을 넘으면 ``기타 N개 섹터`` 로 묶는다.
#: 단 보이는 섹터는 최소 :data:`MIN_SHOWN` 개(그만큼 있으면). 문턱은 목록 안 몫이다(CLAUDE.md 비율 열 규칙).
TAIL_SHARE = 0.03
MAX_SHOWN = 12
MIN_SHOWN = 5
#: 이보다 작은 |값| 은 간선이 아니다 — 합의 부동소수 찌꺼기(1e-13 따위)가 폭 0 인 잔여 간선이 되지 않게.
EPS = 1e-9
#: 노드 순서 — 양쪽 주체 노드가 같은 순서로 선다(개인·외국인·기관·기타법인·잔여).
NODE_ORDER = (*ACTORS, RESID)


def _split(row: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    """한 섹터의 (넘긴 쪽, 넘겨받은 쪽) — 순매도는 절댓값으로 give, 순매수는 take. 잔여도 부호대로 한쪽에 선다."""
    give = {a: -row[a] for a in NODE_ORDER if row.get(a, 0.0) < -EPS}
    take = {a: row[a] for a in NODE_ORDER if row.get(a, 0.0) > EPS}
    return give, take


def _add(acc: dict[str, float], part: dict[str, float]) -> None:
    for a, v in part.items():
        acc[a] = acc.get(a, 0.0) + v


def _ordered(d: dict[str, float]) -> dict[str, float]:
    return {a: d[a] for a in NODE_ORDER if a in d}


def _n_shown(sizes: list[float], gross: float) -> int:
    """내림차순 노드 높이 중 보일 개수 — 몫 문턱·:data:`MAX_SHOWN`, 그리고 바닥 :data:`MIN_SHOWN`."""
    k = sum(1 for v in sizes[:MAX_SHOWN] if gross > 0 and v / gross >= TAIL_SHARE)
    return min(len(sizes), max(k, MIN_SHOWN))


def exchange_graph(sums: Sums) -> dict:
    """보기 1 "넘긴 쪽 → 섹터 → 넘겨받은 쪽" — 간선은 (주체, 섹터) 하나하나다.

    ``sectors`` 는 노드 높이(Σtake, 보존식으로 Σgive 와 같다) 큰 순이고 같으면 입력 순서다. 간선이 없는(전부 0) 섹터는 뺀다.
    ``left``·``right`` 는 주체 노드의 높이 — 왼쪽 개인 = 개인이 순매도한 섹터들의 합, 오른쪽 개인 = 순매수한 섹터들의 합이고
    둘은 다른 숫자다. 0 인 주체는 키가 없다(노드를 그리지 않는다). ``gross`` = Σ노드 높이 = Σleft = Σright.
    왼쪽 주체와 오른쪽 주체를 잇는 구조는 없다 — 한 간선의 두 끝은 늘 주체 하나와 섹터 하나다.
    """
    rows = []
    for name, row in sums.items():
        give, take = _split(row)
        size = max(sum(give.values()), sum(take.values()))
        if size > EPS:
            rows.append((size, name, give, take))
    rows.sort(key=lambda r: -r[0])           # 안정 정렬 — 같은 높이는 입력(페이로드) 순서
    gross = sum(r[0] for r in rows)
    k = _n_shown([r[0] for r in rows], gross)
    left: dict[str, float] = {}
    right: dict[str, float] = {}
    for _size, _name, give, take in rows:
        _add(left, give)
        _add(right, take)
    tail = None
    if rows[k:]:
        tg: dict[str, float] = {}
        tt: dict[str, float] = {}
        for _size, _name, give, take in rows[k:]:
            _add(tg, give)
            _add(tt, take)
        tail = {"names": [r[1] for r in rows[k:]], "give": _ordered(tg), "take": _ordered(tt)}
    return {"sectors": [{"name": n, "give": g, "take": t} for _s, n, g, t in rows[:k]],
            "tail": tail, "left": _ordered(left), "right": _ordered(right), "gross": gross}


def _cut(items: list[list], gross: float) -> tuple[list[list], "list | None"]:
    """[섹터, 금액>0] 큰 순 목록을 (보이는 것, 꼬리 ``[이름들, 합]`` 또는 None) 로 — :func:`exchange_graph` 와 같은 규칙."""
    k = _n_shown([v for _s, v in items], gross)
    rest = items[k:]
    return items[:k], ([[s for s, _v in rest], sum(v for _s, v in rest)] if rest else None)


def actor_graph(sums: Sums, actor: str) -> dict:
    """보기 2 — 주체 하나가 순매도한 섹터(``gave``)와 순매수한 섹터(``took``), 각각 금액 큰 순(같으면 입력 순서).

    꼬리 규칙은 :func:`exchange_graph` 와 같은 상수이고, 몫의 분모는 **그 주체의** gross(Σ|순매수|)다. ``net`` = Σtook − Σgave
    (꼬리 포함) = 그 주체의 순매수 합계. 넘긴 섹터와 넘겨받은 섹터를 잇는 구조는 없다 — "A 에서 빼서 B 에 넣었다" 는 관측이 아니다.
    ``actor`` 는 :data:`NODE_ORDER` 의 하나이거나 :func:`with_detail` 이 붙인 기관 세부 ``inst:<세부 키>`` 다. 모르는 주체면 ``KeyError``.
    """
    if actor not in NODE_ORDER and not (actor.startswith(DETAIL_PREFIX) and any(actor in r for r in sums.values())):
        raise KeyError(actor)
    vals = [(s, row.get(actor, 0.0)) for s, row in sums.items()]
    gave = sorted(([s, -v] for s, v in vals if v < -EPS), key=lambda t: -t[1])
    took = sorted(([s, v] for s, v in vals if v > EPS), key=lambda t: -t[1])
    gross = sum(v for _s, v in gave) + sum(v for _s, v in took)
    net = sum(v for _s, v in took) - sum(v for _s, v in gave)
    gave, tail_gave = _cut(gave, gross)
    took, tail_took = _cut(took, gross)
    return {"actor": actor, "gave": gave, "took": took, "net": net, "tail_gave": tail_gave, "tail_took": tail_took}


#: 기관 세부를 먼저 보일 키 — 투신·연기금(CLAUDE.md 수급 신뢰 순서). 나머지는 페이로드 ``detail_labels`` 순서.
DETAIL_FIRST = ("invtrt", "penfnd_etc")
#: 기관 − Σ세부 — 세부로 잡히지 않은 기관 몫. 세부에 안분하지 않는다(잔여와 같은 원칙).
INST_OTHER = "inst_other"
#: :func:`with_detail` 이 합계에 붙이는 세부 키의 머리 — ``actor_graph(sums, "inst:invtrt")``.
DETAIL_PREFIX = "inst:"


def detail_keys(P: dict) -> list[str]:
    """기관 세부 키의 화면 순서 — :data:`DETAIL_FIRST` 가 먼저, 나머지는 ``P["detail_labels"]`` 순서."""
    keys = list(P.get("detail_labels") or {})
    return [k for k in DETAIL_FIRST if k in keys] + [k for k in keys if k not in DETAIL_FIRST]


def inst_detail_sums(P: dict, markets: list[str], i0: int, i1: int) -> dict[str, dict[str, float]]:
    """섹터 → 기관 세부 키(:func:`detail_keys` 순) → 억원, 끝에 :data:`INST_OTHER` = 기관 − Σ세부. 창 규칙은 :func:`window_sums`."""
    inst = window_sums(P, markets, i0, i1)
    keys = detail_keys(P)
    out: dict[str, dict[str, float]] = {}
    for s in P["sectors"]:
        row = {k: 0.0 for k in keys}
        for m in markets:
            d = ((P.get("detail") or {}).get(m) or {}).get(s) or {}
            for k in keys:
                row[k] += _span(d.get(k) or (), i0, i1)
        row[INST_OTHER] = inst[s]["inst"] - sum(row[k] for k in keys)
        out[s] = row
    return out


def with_detail(sums: Sums, detail: dict[str, dict[str, float]]) -> Sums:
    """합계에 기관 세부를 ``inst:<키>`` 로 붙인 새 합계 — :func:`actor_graph` 가 세부를 주체처럼 받게. 보기 1 은 그대로다
    (:func:`exchange_graph` 는 :data:`NODE_ORDER` 만 본다 — 세부는 기관의 부분이지 다른 주체가 아니다)."""
    return {s: {**row, **{DETAIL_PREFIX + k: v for k, v in (detail.get(s) or {}).items()}} for s, row in sums.items()}
