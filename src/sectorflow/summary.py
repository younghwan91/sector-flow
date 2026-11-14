"""'오늘 한 장' 의 사실 요약 문장 — 웹 두 장·TUI 첫 화면·다이제스트가 **같은 문장**을 쓴다. 표준 라이브러리만.

페이로드 ``today`` 의 값을 옮길 뿐이다(정의 ``sector_flow.today_block``·``streak_block``·``day_change_block``,
``docs/formulas.md`` §오늘 한 장). 점수·판정·추천은 없다 — 문장마다 "누가 얼마나 · 평소와 다른 곳 · 어제와 바뀐 것" 을
센 값으로 적는다. 웹(``sector_numbers.html``·``sector_flow.html`` 의 ``summaryItems``)은 같은 규칙을 JS 로 다시 적고,
검증기 W 층이 그려진 글자를 이 함수의 결과와 맞춘다. 관심 섹터 줄은 브라우저에만 있다(사용자가 고른 것, 서버에 없다).

음수 부호는 ASCII ``-`` 다(TUI·다이제스트와 같다). 웹은 ``−`` 를 쓰고, 대조는 부호를 맞춘 뒤 한다.
"""
from __future__ import annotations

from sectorflow.basis import rhu

#: 시장 4주체 — 합 ≈ 0. '가장 큰 주체' 는 이 넷에서 고른다(``flow_view.DOMINANT_FROM`` 과 같은 넷).
ACT4 = (("inst", "기관"), ("forgn", "외국인"), ("indiv", "개인"), ("etc", "기타법인"))
#: 섹터 × 주체 칸의 주체 — 기관, 그 안에서 방향이 있는 투신+연기금, 외국인, 개인(CLAUDE.md 수급 신뢰 순서).
HEAT_ACTORS = (("inst", "기관"), ("invpen", "투신+연기금"), ("forgn", "외국인"), ("indiv", "개인"))
#: 시장 연속 줄이 보는 주체(``sector_flow.STREAK_MARKET_ACTORS``). 같은 길이면 앞의 것.
STREAK_ORDER = (("inst", "기관"), ("invpen", "투신+연기금"), ("forgn", "외국인"), ("indiv", "개인"))
#: 평소 범위 — 1년 %ile 의 **보이는 정수**가 이 안(5 초과 95 미만)이면 '평소 범위 안'. 말로 옮길 뿐 판정이 아니다.
PCT_HI, PCT_LO = 95, 5
#: 얇은 섹터 — 종목 수가 이보다 적으면 칸 수에서 뺀다(``sector_flow.STREAK_MIN_MEMBERS`` 와 같은 값).
MIN_MEMBERS = 10
UNCLASSIFIED = "(미분류)"
#: 목록 문장에 이름을 적는 개수 — 나머지는 `외 N`.
TOP_K = 3

#: 요약 아래 늘 붙는 줄 — 이 요약이 말하지 않는 것. 1일·%ile·연속은 앞날을 검정하지 않았고,
#: 섹터 20·60일 지표는 검정했지만 관계가 확인되지 않았다(docs/validation/).
NOT_SAID = ("1일 순매수·1년 %ile·연속 일수는 앞날 수익률과의 관계를 검정하지 않았다(앞날 검정 안 함) — "
            "이 숫자로 방향을 읽을 근거가 없다. 섹터 20·60일 수급 지표는 검정했고 관계가 확인되지 않았다 — 방향 단서가 아니다.")
#: '평소 범위 안/밖' 범례 — 그 말이 무엇인가.
BAND_NOTE = "평소 범위 안·밖 = 1년 %ile(5~95)을 말로 옮긴 것, 판정 아님"


def fmt_amt(v) -> str:
    return "—" if v is None else ("+" if v >= 0 else "-") + f"{abs(rhu(v)):,.0f}"


def fmt_big(v) -> str:
    """누적·시장 금액 — |값| 1조(10,000억) 이상은 `-4.1조`, 아래는 `-3,210억`(``flow_view.fmt_big`` 와 같은 꼴)."""
    if v is None:
        return "—"
    if abs(rhu(v)) >= 10000:
        return ("+" if v >= 0 else "-") + f"{abs(rhu(v / 10000, 1)):.1f}조"
    return fmt_amt(v) + "억"


def pct_str(p) -> str:
    return "—" if p is None else str(int(rhu(p)))


def band(p) -> str:
    """1년 %ile 을 말로 — 보이는 정수 ≥95 는 '평소 범위 밖(위)', ≤5 는 '밖(아래)', 그 사이 '안'. 값 없으면 빈 글자."""
    if p is None:
        return ""
    q = int(rhu(p))
    return "평소 범위 밖(위)" if q >= PCT_HI else "평소 범위 밖(아래)" if q <= PCT_LO else "평소 범위 안"


def is_extreme(p) -> bool:
    return p is not None and (int(rhu(p)) >= PCT_HI or int(rhu(p)) <= PCT_LO)


def side(v) -> str:
    return "순매수" if (v or 0) >= 0 else "순매도"


def rarity_text(f: "dict | None") -> str:
    """`260일 중 12번째로 큰 순매도 · 더 많이 판 날은 3거래일 전` — 순위가 없으면 빈 글자."""
    if not f or f.get("rank") is None:
        return ""
    v = f.get("v") or 0.0
    s = side(v)
    # 더 큰 날이 없으면 순위(1번째)가 이미 말한다.
    tail = "" if f.get("last_more") is None else f" · 더 많이 {'산' if v > 0 else '판'} 날은 {f['last_more']}거래일 전"
    return f"{f.get('n')}일 중 {f['rank']}번째로 큰 {s}{tail}"


def strip_lines(d: dict, market: str) -> "list[tuple[str, str, str]] | None":
    """시장 4주체 띠의 글자 — (주체, 첫 줄, 둘째 줄). 첫 줄 `+1,140억 · 1년 %ile 44 · 평소 범위 안`, 둘째 줄 순위.

    ``today.market[시장].series`` 가 없는 옛 리포트면 None.
    """
    M = (((d.get("today") or {}).get("market") or {}).get(market)) or {}
    if not isinstance(M.get("series"), dict):
        return None
    net = M.get("net") or {}
    out = []
    for a, _ko in (*ACT4, ("invpen", "투신+연기금")):
        f = net.get(a) or {}
        b = band(f.get("pct"))
        out.append((a, f"{fmt_big(f.get('v'))} · 1년 %ile {pct_str(f.get('pct'))}" + (f" · {b}" if b else ""),
                    rarity_text(f)))
    return out


def dominant(net: dict) -> "str | None":
    """그날 |순매수| 가 가장 큰 4주체 — 같으면 키가 큰 쪽(``flow_view.dominant_of`` 와 같은 규칙)."""
    live = [(abs((net.get(k) or {}).get("v") or 0.0), k) for k, _ko in ACT4 if (net.get(k) or {}).get("v") is not None]
    return max(live)[1] if live else None


def _members(d: dict, markets: list[str], sec: str) -> int:
    return sum(int(((d.get("n_by_sector") or {}).get(m) or {}).get(sec) or 0) for m in markets)


def thin(d: dict, markets: list[str], sec: str) -> bool:
    return sec == UNCLASSIFIED or _members(d, markets, sec) < MIN_MEMBERS


def extreme_cells(d: dict, market: str, markets: list[str]) -> tuple[list[tuple[str, str, dict]], int]:
    """평소 범위 밖 칸 — (섹터, 주체 이름, 칸) 을 50 에서 먼 순(같으면 섹터·주체 순), 그리고 센 칸 수(얇은 섹터 제외)."""
    S = (((d.get("today") or {}).get("sectors") or {}).get(market)) or {}
    out, total = [], 0
    for s in sorted(S):
        if thin(d, markets, s):
            continue
        for i, (a, ko) in enumerate(HEAT_ACTORS):
            f = S[s].get(a)
            if not f or f.get("pct") is None:
                continue
            total += 1
            if is_extreme(f["pct"]):
                out.append((abs(f["pct"] - 50.0), s, i, ko, f))
    out.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [(s, ko, f) for _g, s, _i, ko, f in out], total


def _more(n: int) -> str:
    return f" 외 {n - TOP_K}" if n > TOP_K else ""


def summary_items(d: dict, market: str, markets: list[str]) -> "list[tuple[str, str, str]] | None":
    """사실 요약 — (키, 굵은 머리, 나머지). 순서: 가장 큰 주체 → 가장 긴 연속 → 평소 범위 밖 칸 → 어제와 바뀐 섹터.

    ``markets`` = 그 시장 키가 묶는 시장들(``전체`` 면 거래소·코스닥). ``today`` 가 없으면 None.
    """
    T = d.get("today")
    if not isinstance(T, dict) or not T.get("sectors"):
        return None
    out = []
    net = (((T.get("market") or {}).get(market)) or {}).get("net") or {}
    k = dominant(net)
    if k:
        f = net[k]
        ko = dict(ACT4)[k]
        b = band(f.get("pct"))
        r = rarity_text(f)
        out.append(("dom", f"{ko} {fmt_big(f['v'])} {side(f['v'])}",
                    f" — 오늘 가장 큰 주체. 1년 %ile {pct_str(f.get('pct'))}" + (f" · {b}" if b else "")
                    + (f" · {r}" if r else "")))
    SK = (((T.get("streaks") or {}).get("market") or {}).get(market)) or {}
    best = None
    for a, ko in STREAK_ORDER:
        f = SK.get(a)
        if f and f.get("days") and (best is None or f["days"] > best[2]["days"]):
            best = (a, ko, f)
    if best and best[2]["days"] >= 2:          # 하루짜리는 '연속' 이 아니다 — 문장을 내지 않는다
        _a, ko, f = best
        n = (T.get("streaks") or {}).get("lookback") or T.get("lookback")
        out.append(("streak", f"{ko} {f['days']}일 연속 {'순매수' if f.get('sign', 0) > 0 else '순매도'}",
                    f" (누적 {fmt_big(f.get('sum'))})"
                    + (f" — {n}일 중 같은 방향 {f['days']}일 이상 연속 {f['times']}번(오늘 포함)"
                       if f.get("times") is not None else "")))
    cells, total = extreme_cells(d, market, markets)
    out.append(("ext", f"평소 범위(1년 %ile {PCT_LO}~{PCT_HI}) 밖 {len(cells)}칸/{total}칸",
                (": " + " · ".join(f"{s} {ko} {fmt_amt(f['v'])}억({pct_str(f['pct'])})" for s, ko, f in cells[:TOP_K])
                 + _more(len(cells))) if cells else " — 없음"))
    DC = T.get("day_change") or {}
    if DC.get("prev_date"):
        fl = ((DC.get("flips") or {}).get(market) or {}).get("inst") or []
        out.append(("flip", f"어제({str(DC['prev_date'])[5:]})와 기관 부호가 바뀐 섹터 {len(fl)}곳",
                    (": " + " · ".join(f"{x['sector']} {fmt_amt(x['prev'])}→{fmt_amt(x['now'])}" for x in fl[:TOP_K])
                     + _more(len(fl))) if fl else ""))
    return out


def summary_texts(d: dict, market: str, markets: list[str]) -> "list[str] | None":
    """:func:`summary_items` 를 한 줄 글자로 — TUI·다이제스트·검증기가 쓰는 꼴."""
    items = summary_items(d, market, markets)
    return None if items is None else [h + t for _k, h, t in items]


def streak_rows(d: dict, market: str) -> "list[tuple[str, str, int, str]] | None":
    """시장 연속 막대 — (주체, 이름, 부호 있는 일수, 글자). 글자 `4일 연속 순매수 · 누적 +8,828억 · 260일 중 같은 방향 4일 이상 15번`.

    ``today.streaks`` 가 없으면 None. 웹 연속 막대가 같은 글자를 적는다(검증기 W 층).
    """
    S = (d.get("today") or {}).get("streaks")
    if not isinstance(S, dict):
        return None
    M = (S.get("market") or {}).get(market) or {}
    n = S.get("lookback")
    out = []
    for a, ko in STREAK_ORDER:
        f = M.get(a) or {}
        if not f.get("days"):
            out.append((a, ko, 0, "연속 없음(오늘 ≈0)"))
            continue
        sg = 1 if f.get("sign", 0) > 0 else -1
        text = f"{f['days']}일 연속 {'순매수' if sg > 0 else '순매도'} · 누적 {fmt_big(f.get('sum'))}"
        if f["days"] >= 2 and f.get("times") is not None:
            text += f" · {n}일 중 같은 방향 {f['days']}일 이상 {f['times']}번"
        out.append((a, ko, sg * f["days"], text))
    return out


#: 어제와 부호가 바뀐 섹터를 적는 주체 — 기관과 그 안의 투신+연기금.
FLIP_ACTORS = (("inst", "기관"), ("invpen", "투신+연기금"))


def flip_rows(d: dict, market: str) -> "list[tuple[str, str, float, float]] | None":
    """어제 → 오늘 부호가 바뀐 섹터 — (주체 이름, 섹터, 어제, 오늘). ``today.day_change`` 의 목록 그대로(|오늘| 큰 순)."""
    DC = (d.get("today") or {}).get("day_change") or {}
    if not DC.get("prev_date"):
        return None
    return [(ko, x["sector"], x["prev"], x["now"]) for a, ko in FLIP_ACTORS
            for x in ((DC.get("flips") or {}).get(market) or {}).get(a) or []]
