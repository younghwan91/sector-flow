"""수급 지도(``map.html``)의 데이터 — 페이로드 ``flows`` 에서 **주체 ↔ 섹터** 이분 그래프를 짓는다. 표준 라이브러리만.

관측되는 것은 (날짜, 시장, 섹터, 주체) 순매수뿐이다. 그래서 간선은 (주체, 섹터) 하나하나이고, 섹터→섹터·주체→주체
구조는 데이터에도 만들지 않는다 — 돈에 꼬리표가 없고(섹터 쌍), 주변합 4개로 쌍별 이전량 6개가 정해지지 않는다(주체 쌍).
근거는 원장 설계 ``docs/superpowers/specs/2026-08-29-money-flow-ledger.md`` §2, 지도 설계 ``2026-11-21-flow-map-brand-design.md``
§3.1. 4주체 합의 잔차는 4주체에 안분하지 않고 별도 노드 :data:`RESID` 로 둔다(원장 §2.3).

단위는 페이로드와 같은 억원(종가 환산 근사)이다. 반환값은 전부 JSON 으로 직렬화되는 dict/list 다.
"""
from __future__ import annotations

import json

from sectorflow import summary
from sectorflow.basis import DATA_BASIS_INTEGRATED_SHORT, DATA_BASIS_SHORT, sector_source_label
from sectorflow.josa import josa

#: 페이로드 ``flows[시장][섹터]`` 의 주체 키 — 순서가 화면의 주체 노드 순서다(양쪽 같게).
ACTORS = ("indiv", "forgn", "inst", "etc")
#: 잔여 = ``−Σ ACTORS`` — KRX 분류 중 싣지 않는 주체(기타외국인 등)의 순매수 추정치이자 근사 오차.
RESID = "resid"
ACTOR_LABEL = {"indiv": "개인", "forgn": "외국인", "inst": "기관", "etc": "기타법인", "resid": "잔여"}

#: 섹터 → 주체(+잔여) → 억원.
Sums = dict[str, dict[str, float]]


def present_actors(P: dict) -> tuple[str, ...]:
    """이 페이로드가 가진 주체 — BYO 증권사 능력(``P["capability"]["actors"]``)이 있으면 그것, 없으면(배치·데모) 4주체 다.

    기관계만 주는 증권사(DB·NH)는 기타법인이 없다. 없는 주체는 노드·간선·머리글·시간 흐름 어디에도 그리지 않는다 — 0 으로 그리면
    "순매매 없음" 으로 읽힌다(NULL 은 모름이다).
    """
    cap = P.get("capability")
    if not cap:
        return ACTORS
    have = set(cap.get("actors") or ())
    return tuple(a for a in ACTORS if a in have)


def has_resid(P: dict) -> bool:
    """잔여 노드를 그리나 — 4주체가 **다** 있을 때만. 하나라도 없으면 −Σ 가 그 주체를 삼켜 잔여가 아니게 된다."""
    return len(present_actors(P)) == len(ACTORS)


def _span(vals, i0: int, i1: int) -> float:
    return float(sum(v for v in vals[i0:i1 + 1] if v is not None))


def window_sums(P: dict, markets: list[str], i0: int, i1: int) -> Sums:
    """``markets`` 를 더한 섹터별 주체 순매수 — 날짜 색인 ``i0`` 부터 ``i1`` 까지(**양끝 포함**).

    ``None`` 은 0 으로 센다(미보고·결측 — 0 과 구별하지 않는다, 합계 자리라서). 그 시장에 없는 섹터도 0 이다.
    섹터 순서는 ``P["sectors"]`` 이고, :data:`RESID` 는 4주체 합의 부호를 뒤집은 값이다. 주체는 :func:`present_actors` 뿐이고
    잔여는 :func:`has_resid` 일 때만 있다.
    """
    out: Sums = {}
    actors, resid = present_actors(P), has_resid(P)
    for s in P["sectors"]:
        row = {a: 0.0 for a in actors}
        for m in markets:
            f = (P["flows"].get(m) or {}).get(s)
            if not f:
                continue
            for a in actors:
                row[a] += _span(f.get(a) or (), i0, i1)
        if resid:
            row[RESID] = 0.0 - sum(row[a] for a in actors)    # 0.0 − : 합이 0 이면 −0.0 이 아니라 0.0
        out[s] = row
    return out


#: 꼬리 묶기 — 섹터 몫(노드 높이 ÷ gross)이 이보다 작거나 순위가 :data:`MAX_SHOWN` 을 넘으면 ``기타 N개 섹터`` 로 묶는다.
#: 단 보이는 섹터는 최소 :data:`MIN_SHOWN` 개(그만큼 있으면). 문턱은 목록 안 몫이다(CLAUDE.md 비율 열 규칙).
TAIL_SHARE = 0.01
MAX_SHOWN = 12
MIN_SHOWN = 8
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
    rows = _rows(sums)
    rows.sort(key=lambda r: -r[0])           # 안정 정렬 — 같은 높이는 입력(페이로드) 순서
    k = _n_shown([r[0] for r in rows], sum(r[0] for r in rows))
    return _assemble(rows, rows[:k], rows[k:])


#: (노드 높이, 섹터, give, take)
_Row = tuple[float, str, dict[str, float], dict[str, float]]


def _rows(sums: Sums) -> list[_Row]:
    """간선이 하나라도 있는 섹터의 행 — 입력 순서."""
    rows = []
    for name, row in sums.items():
        give, take = _split(row)
        size = max(sum(give.values()), sum(take.values()))
        if size > EPS:
            rows.append((size, name, give, take))
    return rows


def _assemble(rows: list[_Row], shown: list[_Row], rest: list[_Row]) -> dict:
    """보이는 행·꼬리 행으로 그래프 dict — 주체 노드 높이와 gross 는 ``rows`` 전부에서."""
    left: dict[str, float] = {}
    right: dict[str, float] = {}
    for _size, _name, give, take in rows:
        _add(left, give)
        _add(right, take)
    tail = None
    if rest:
        tg: dict[str, float] = {}
        tt: dict[str, float] = {}
        for _size, _name, give, take in rest:
            _add(tg, give)
            _add(tt, take)
        tail = {"names": [r[1] for r in rest], "give": _ordered(tg), "take": _ordered(tt)}
    return {"sectors": [{"name": n, "give": g, "take": t} for _s, n, g, t in shown],
            "tail": tail, "left": _ordered(left), "right": _ordered(right), "gross": sum(r[0] for r in rows)}


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
    """기관 세부 키의 화면 순서 — :data:`DETAIL_FIRST` 가 먼저, 나머지는 ``P["detail_labels"]`` 순서.
    라벨 표가 없는 페이로드(BYO 수급만)는 ``P["detail"]`` 에 처음 나온 순서."""
    keys = list(P.get("detail_labels") or {})
    if not keys:
        for by_sec in (P.get("detail") or {}).values():
            for d in by_sec.values():
                keys += [k for k in d if k not in keys]
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


def frames(P: dict, markets: list[str], *, span: int = 1, n: int = 60) -> dict:
    """타임랩스 — 마지막 ``n`` 거래일 각각의 보기 1 그래프. 프레임 하나는 그날까지 ``span`` 일 합(1 이면 하루, 5 면 5일 이동합).

    노드 순서 ``order`` 는 구간 전체(첫 프레임 창의 시작 ~ 마지막 날) 합의 :func:`exchange_graph` 로 **한 번** 정한다 —
    프레임마다 다시 줄세우면 띠가 깜빡인다. 프레임의 ``sectors`` 는 늘 ``order`` 의 섹터 전부이고(그날 간선이 없으면 빈
    give·take), 꼬리는 ``order`` 밖의 섹터(고정 집합) 중 그날 간선이 있는 것이다. 날짜가 ``span`` 보다 적으면 프레임이 없다.
    """
    N = len(P["dates"])
    n = max(0, min(n, N - span + 1))
    if n == 0:
        return {"dates": [], "order": [], "frames": []}
    first = N - n
    whole = exchange_graph(window_sums(P, markets, first - span + 1, N - 1))
    order = [r["name"] for r in whole["sectors"]]
    out = []
    for j in range(first, N):
        rows = _rows(window_sums(P, markets, j - span + 1, j))
        by = {r[1]: r for r in rows}
        shown = [by.get(s) or (0.0, s, {}, {}) for s in order]
        out.append(_assemble(rows, shown, [r for r in rows if r[1] not in order]))
    return {"dates": P["dates"][first:], "order": order, "frames": out}


#: 시간 흐름 — 한 그림에 따로 그리는 섹터 수(나머지는 ``기타 N개 섹터`` 한 띠), 날짜 수 상한, 값 자릿수(억원 소수 1자리).
STREAM_TOP = 10
STREAM_N = 120
STREAM_DIGITS = 1


def stream_series(P: dict, markets: list[str], actor: str, n: int = STREAM_N) -> dict:
    """시간 흐름 — 주체 하나의 섹터별 **하루** 순매수, 마지막 ``n`` 거래일(``markets`` 를 더한다, None 은 0).

    ``series`` 는 구간 Σ|하루 값| 큰 순 :data:`STREAM_TOP` 개 섹터, 같으면 페이로드 섹터 순서. 나머지 섹터(구간 내내 0 인 섹터는 뺀다)는
    날마다 더해 ``기타 N개 섹터`` 한 띠(``rest: True``, ``names``)로 둔다. 값은 소수 :data:`STREAM_DIGITS` 자리로 반올림한다(화면 표시용 —
    5일 합·누적은 화면이 이 값으로 더한다). 섹터와 섹터를 잇는 구조는 없다 — 한 섹터의 순매도와 다른 섹터의 순매수가 같은 날에
    있어도 같은 돈이라는 관측이 아니다(돈에 꼬리표가 없다). ``actor`` 는 :data:`ACTORS` 의 하나, 아니면 ``KeyError``.
    """
    if actor not in ACTORS:
        raise KeyError(actor)
    N = len(P["dates"])
    n = max(0, min(n, N))
    i0 = N - n
    rows = []
    for k, s in enumerate(P["sectors"]):
        v = [0.0] * n
        for m in markets:
            f = ((P["flows"].get(m) or {}).get(s) or {}).get(actor) or ()
            for j, x in enumerate(f[i0:N]):
                if x is not None:
                    v[j] += x
        g = sum(abs(x) for x in v)
        if g > EPS:
            rows.append((g, k, s, v))
    rows.sort(key=lambda r: (-r[0], r[1]))
    top, rest = rows[:STREAM_TOP], rows[STREAM_TOP:]
    series = [{"name": s, "v": [round(x, STREAM_DIGITS) for x in v]} for _g, _k, s, v in top]
    if rest:
        series.append({"name": f"기타 {len(rest)}개 섹터", "rest": True, "names": [r[2] for r in rest],
                       "v": [round(sum(r[3][j] for r in rest), STREAM_DIGITS) for j in range(n)]})
    return {"dates": P["dates"][i0:], "series": series}


#: 전체 시장 — ``P["markets"]`` 를 다 더한 것.
ALL_MARKETS = "전체"
#: 헤드라인 아래 사실 줄 수 — 간선 큰 순.
N_FACTS = 3


def market_list(P: dict, market: str) -> list[str]:
    """화면의 시장 하나 → 더할 페이로드 시장들. :data:`ALL_MARKETS` 면 전부."""
    return list(P["markets"]) if market == ALL_MARKETS else [market]


def _big(v: float) -> str:
    """``−2.9조``·``+3,210억`` — 부호는 웹처럼 ``−``(U+2212), 자릿수·반올림·조 단위는 ``summary.fmt_big`` 그대로."""
    return summary.fmt_big(v).replace("-", "−")


def map_headline(P: dict, market: str, win: int) -> dict:
    """지도 머리글 — ``lead`` 한 줄과 ``facts`` 간선 큰 순 :data:`N_FACTS` 줄. 주변합만 말한다.

    ``lead`` 는 창 합계에서 순매도가 가장 큰 주체와 순매수가 가장 큰 주체 — 고르는 규칙은 ``summary.dominant``
    (|값| 큰 4주체, 같으면 키가 큰 쪽)를 부호별로 부른 것이다. 두 주체를 잇지 않는다: "개인이 판 것을 기관이 받았다" 는
    관측이 아니다(주변합으로 쌍별 이전량이 정해지지 않는다). ``facts`` 는 ``가상전자 — 개인 −2,927억`` 꼴이고
    4주체 간선만 센다(잔여는 주체가 아니다). 같은 크기면 페이로드 섹터 순서·주체 순서.
    """
    N = len(P["dates"])
    sums = window_sums(P, market_list(P, market), N - win, N - 1)
    actors = present_actors(P)
    net = {a: {"v": sum(r[a] for r in sums.values())} for a in actors}
    gave = summary.dominant({a: f for a, f in net.items() if f["v"] < -EPS})
    took = summary.dominant({a: f for a, f in net.items() if f["v"] > EPS})
    period = "기준일" if win == 1 else f"최근 {win}거래일"
    parts = []
    if gave:
        parts.append(f"{josa(ACTOR_LABEL[gave], '이/가')} 가장 크게 순매도 {_big(net[gave]['v'])}")
    if took:
        parts.append(f"{josa(ACTOR_LABEL[took], '이/가')} 가장 크게 순매수 {_big(net[took]['v'])}")
    lead = f"{period} {market} — " + (" · ".join(parts) if parts else "순매수·순매도가 없다")
    edges = [(abs(row[a]), i, j, s, a, row[a]) for i, (s, row) in enumerate(sums.items())
             for j, a in enumerate(actors) if abs(row[a]) > EPS]
    edges.sort(key=lambda e: (-e[0], e[1], e[2]))
    facts = [f"{s} — {ACTOR_LABEL[a]} {_big(v)}" for _m, _i, _j, s, a, v in edges[:N_FACTS]]
    return {"lead": lead, "facts": facts}


#: 지도의 창(거래일 누적) — 리포트의 기존 창. 날짜 수보다 긴 창은 빠진다(BYO 20일 폴더면 1·5·20).
MAP_WINDOWS = (1, 5, 20, 60, 120)
#: 지도 데이터를 JSON 으로 적었을 때의 상한(바이트) — 넘으면 타임랩스 프레임 수를 줄여 맞춘다.
MAP_JSON_MAX = 600_000
#: 타임랩스 기본 프레임 수와, 상한을 넘을 때 한 번에 줄이는 수.
TIMELAPSE_N = 60
TIMELAPSE_STEP = 10


def _size(o) -> int:
    return len(json.dumps(o, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def actor_keys(P: dict) -> list[str]:
    """보기 2 에서 누를 수 있는 주체 — 4주체·잔여, 그리고 기관 세부(``inst:<키>``, 투신·연기금 먼저)와 기관 나머지."""
    det = detail_keys(P) if "inst" in present_actors(P) else []
    return [*present_actors(P), *([RESID] if has_resid(P) else []),
            *([DETAIL_PREFIX + k for k in (*det, INST_OTHER)] if det else [])]


def stream_data(P: dict, markets: list[str], n: int = STREAM_N) -> dict:
    """지도 페이지 두 번째 보기("시간 흐름")의 묶음 — 날짜는 한 번만, 시장(화면 이름) × 4주체마다 :func:`stream_series` 의 띠."""
    n = max(0, min(n, len(P["dates"])))
    return {"dates": P["dates"][len(P["dates"]) - n:],
            "views": {m: {a: stream_series(P, market_list(P, m), a, n)["series"] for a in present_actors(P)}
                      for m in markets}}


def capability_marks(P: dict) -> dict:
    """지도 머리의 증권사 표지 — ``actors``(그릴 주체 노드 순서, 잔여는 4주체가 다 있을 때만), ``missing``(주지 않는 주체 이름),
    ``broker``(어댑터 이름, 배치는 ``None``), ``verified``(실제 키로 키움과 대조했나 — 거짓이면 "미검증 어댑터" 배지), ``cap_note``.
    """
    cap = P.get("capability") or {}
    actors = present_actors(P)
    missing = [ACTOR_LABEL[a] for a in ACTORS if a not in actors]
    names = cap.get("broker_labels") or []
    who = "·".join(names) or "이 증권사"
    note = None
    if missing:
        note = (f"{who} 어댑터는 {josa('·'.join(missing), '을/를')} 주지 않는다 — 그 주체와 잔여는 그리지 않는다"
                f"(0 이 아니라 모름)")
    return {"actors": [*actors, *([RESID] if has_resid(P) else [])], "missing": missing,
            "broker": who if names else None, "verified": bool(cap.get("verified", True)), "cap_note": note}


def build_map_data(P: dict) -> dict:
    """``map.html`` 에 심을 묶음 — 시장(전체·각 시장) × 창마다 보기 1(``exchange``)·보기 2(``actors``)·머리글, 타임랩스,
    그리고 두 번째 보기 시간 흐름(``stream``, :func:`stream_data`).

    쓰는 페이로드 키는 ``dates``·``sectors``·``markets``·``flows``·``detail``·``detail_labels`` 뿐이다(수익률 등 없이 —
    BYO 수급만 페이로드가 이 길을 탄다). 창 키는 JSON 과 같게 문자열(``"5"``)이다. 크기는 :data:`MAP_JSON_MAX` 안으로
    타임랩스 프레임 수를 줄여 맞춘다(보기 1·2 는 줄이지 않는다).
    """
    N = len(P["dates"])
    windows = [w for w in MAP_WINDOWS if w <= N]
    markets = [ALL_MARKETS, *P["markets"]]
    keys = actor_keys(P)
    views: dict = {}
    for m in markets:
        ms = market_list(P, m)
        views[m] = {}
        for w in windows:
            sums = window_sums(P, ms, N - w, N - 1)
            full = with_detail(sums, inst_detail_sums(P, ms, N - w, N - 1)) if any(
                k.startswith(DETAIL_PREFIX) for k in keys) else sums
            views[m][str(w)] = {"exchange": exchange_graph(sums),
                                "actors": {a: actor_graph(full, a) for a in keys},
                                "headline": map_headline(P, m, w)}
    # 거래소 기준 표지 — KRX+NXT 통합(토스 어댑터 BYO)이면 KRX 단독 문구 대신 통합 문구(basis.DATA_BASIS_INTEGRATED).
    basis = DATA_BASIS_INTEGRATED_SHORT if P.get("venue") == "KRX+NXT" else DATA_BASIS_SHORT
    out = {"asof": P["dates"][-1] if N else None, "demo": bool(P.get("demo")), "basis": basis,
           "sector_source": sector_source_label(P.get("sector_source")), **capability_marks(P),
           "markets": markets, "windows": windows, "labels": {**ACTOR_LABEL, **{DETAIL_PREFIX + k: v for k, v in
                                                                                  (P.get("detail_labels") or {}).items()},
                                                               DETAIL_PREFIX + INST_OTHER: "기관 나머지"},
           "views": views, "timelapse": {}, "stream": stream_data(P, markets)}
    n = min(TIMELAPSE_N, N)
    while True:
        out["timelapse"] = {m: frames(P, market_list(P, m), span=1, n=n) for m in markets}
        if n <= 0 or _size(out) <= MAP_JSON_MAX:
            return out
        n = max(0, n - TIMELAPSE_STEP)
