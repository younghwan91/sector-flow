#!/usr/bin/env python3
"""오늘의 수급 다이제스트 — 리포트 폴더의 ``payload.json`` 하나로 짧은 글·카드 이미지·구조화 사실을 낸다.

산출(같은 리포트 폴더에):

* ``digest.md``   — 뉴스레터·텔레그램에 붙일 한국어 글(``MAX_CHARS`` 자 이하, 고정 면책 꼬리 포함).
* ``digest.json`` — 글과 카드가 쓴 사실 그대로(뒤에 붙일 API·뉴스레터 도구용).
* ``digest.png``  — 1080×1350 카드. ``scripts/templates/digest_card.html`` 을 Playwright Chromium 으로 찍는다.
  Playwright 가 없으면 건너뛴다(``--png auto``). ``digest_card.html`` 은 늘 남긴다.

**보내지 않는다.** 네트워크를 쓰지 않고 폴더에 파일만 쓴다. 게시는 사람이 정한다 — 그리고 KRX·키움 자료의
재배포 권리와 유사투자자문업 해당 여부 문의(docs/strategy/inquiries)에 답을 받기 전에는 **밖에 게시하지 않는다.**
자료는 키움증권 계좌 보유자 API 로 받은 한국거래소 자료이고, 재배포 허락은 아직 확인되지 않았다.

화면 배너(``flow_view.banner_lines``)와 같은 원칙이다 — 페이로드 ``today``·``change``·``final_diff`` 의
값을 옮길 뿐 점수·순위 판정·추천을 싣지 않는다. 섹터 수급 지표는 앞날 수익률과의 관계를 검정했고 증거가
없었다(docs/validation/). 그래서 본문에 권유처럼 읽히는 말이 들어가면 **만들지 않는다**
(:func:`check_compliance` — 금칙어가 하나라도 있으면 세 파일 모두 안 쓰고, 옛 산출물도 지운다).

``flow_view`` 를 import 하지 않는다 — 화면 코드와 엮이면 화면을 고칠 때마다 이 글이 같이 흔들린다.
같은 정의(1년 분위 문턱 95/5, 상태 칩)를 여기서 다시 적는다. 다만 문턱 비교는 화면과 달리 **보이는 정수**
(반올림한 값)로 한다 — 글에 "95" 로 적힌 섹터가 "≥95" 목록에서 빠지거나 "100" 이 99.6 인 어긋남을 없앤다.

``(미분류)``(벤더가 섹터를 안 준 종목 묶음)는 목록에 싣지 않는다 — 섹터가 아니다. ``digest.json`` 의
``excluded`` 에 남긴다.

사용::

    python scripts/digest.py --dir reports/2026-11-06            # md·json·카드 html, 되면 png
    python scripts/digest.py --dir reports/latest --out /tmp/digest-out   # 읽기 전용 리포트는 밖으로
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import math
import os
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "templates" / "digest_card.html"

#: 글이 다루는 시장 — 거래소+코스닥 합.
MARKET = "전체"
#: 시장 순매수 줄의 4주체. 합 ≈ 0 이라 하나도 빼지 않는다.
NET_ACTORS = (("inst", "기관"), ("forgn", "외국인"), ("indiv", "개인"), ("etc", "기타법인"))
#: 섹터 상·하위를 늘 적는 주체 — 기관 총액과 그 안에서 방향성이 있는 투신+연기금(CLAUDE.md 수급 신뢰 순서).
SECTOR_ACTORS = (("inst", "기관"), ("invpen", "투신+연기금"))
KO = {"inst": "기관", "invtrt": "투신", "penfnd_etc": "연기금", "invpen": "투신+연기금",
      "forgn": "외국인", "indiv": "개인", "etc": "기타법인"}
TOP_N = 3
#: 1년 분위 상단·하단 문턱(포함) — 화면 배너(flow_view.PCT_HI/PCT_LO)와 같은 값. 비교는 보이는 정수로.
PCT_HI, PCT_LO = 95, 5
#: 목록에서 빼는 묶음 — 섹터가 아니다.
EXCLUDED_SECTORS = ("(미분류)",)
#: 글 길이 상한(문자 수, 줄바꿈 포함) — 텔레그램 미리보기·뉴스레터 한 화면.
MAX_CHARS = 1200
#: 길이가 넘치면 극단 목록을 이 개수까지 차례로 줄인다(0 = 개수만). 그래도 넘치면 만들지 않는다.
EXTREME_CAPS = (4, 2, 1, 0)
#: 카드 크기(px) — 인스타그램 세로·텔레그램 미리보기 비율 4:5.
CARD_W, CARD_H = 1080, 1350
#: 카드가 넘치면 (극단 목록 개수, 연속 줄 꼴) 순으로 줄여 다시 찍는다 — 글의 :data:`TRIM_LADDER` 와 같은 원리.
CARD_LADDER = ((3, "full"), (1, "nosector"), (0, "none"))

#: 본문에 나오면 안 되는 말 — 권유·전망·보장으로 읽히는 말. 비교 방법은 :func:`banned_hits`.
BANNED = ("추천", "매수 신호", "매도 신호", "신호", "급등", "폭등", "급락", "세력", "목표가", "수익 보장", "보장",
          "확실", "유망", "수혜", "주도주", "대장주", "매수하라", "담아라", "전망", "기대")

#: 고정 꼬리 — 금칙어 검사에서 빠지는 유일한 글. 바꾸면 테스트가 정확한 문자열로 잡는다.
DISCLAIMER = ("이 글은 수급 사실 요약이며 투자 판단·추천이 아니다. "
              "섹터 수급 지표와 앞날 수익률의 관계는 검정했고 증거가 없다.")
SOURCE = ("자료: 한국거래소 투자자별 매매동향·지수(키움증권 API로 수집). "
          "금액은 순매수 수량×종가 근사, 섹터 구분·수익률은 자체 계산.")
FOOTER = f"---\n{DISCLAIMER}\n{SOURCE}"

_WEEKDAY = "월화수목금토일"


class DigestError(RuntimeError):
    """다이제스트를 만들지 않는다 — 이유를 사람에게 그대로 보인다."""


class CardOverflow(DigestError):
    """카드 내용이 카드 크기를 넘친다 — 줄인 판으로 다시 찍는다."""


# ─────────────────────────────────────────────────────────── 사실 모으기

def status_label(P: dict) -> str:
    """값의 지위 — 화면 상태 칩(``flow_view.status_chip``)과 같은 정의.

    ``final`` 은 다음날 아침 확정치로 다시 만든 리포트에만 있다. ``finalized`` 는 시계만 본다(수집 창이
    지났나) — 값이 확정됐다는 뜻이 아니다.
    """
    if P.get("final"):
        return "마감·확정"
    return "마감·잠정" if P.get("finalized") else "장중·미확정"


def pct_int(p) -> "int | None":
    """보이는 백분위 — 반올림(0.5 는 올림). 글·카드·문턱 비교가 모두 이 값을 쓴다."""
    return None if p is None else int(math.floor(p + 0.5))


def _fact(x) -> tuple:
    x = x or {}
    return x.get("v"), x.get("pct")


def _rank(secs: dict, actor: str) -> list[tuple[str, float, "float | None"]]:
    """(섹터, 오늘 순매수, 1년%ile) — 금액 큰 순, 동률은 이름순. 값이 없는 섹터는 뺀다."""
    out = [(s, *_fact(row.get(actor))) for s, row in secs.items() if s not in EXCLUDED_SECTORS]
    out = [t for t in out if t[1] is not None]
    out.sort(key=lambda t: (-t[1], t[0]))
    return out


def _sectors_only(names: list[str]) -> list[str]:
    return [s for s in names if s not in EXCLUDED_SECTORS]


def dominant_actor(net: dict) -> "str | None":
    """오늘 시장 순매수 **금액 절댓값**이 가장 큰 주체(4주체 중). 판정이 아니라 크기 순이다."""
    vals = [(a, (net.get(a) or {}).get("v")) for a, _ in NET_ACTORS]
    vals = [(a, v) for a, v in vals if v is not None]
    if not vals:
        return None
    return max(vals, key=lambda t: (abs(t[1]), -[a for a, _ in NET_ACTORS].index(t[0])))[0]


def collect(P: dict) -> dict:
    """페이로드에서 글·카드가 쓸 사실을 뽑는다 — 계산하지 않고 옮긴다(정렬·문턱 비교만)."""
    T = P.get("today")
    if not T:
        raise DigestError("페이로드에 'today' 블록이 없다(옛 형식) — KR_QUANT_FORCE=1 로 리포트를 다시 만들어라")
    asof = T.get("date") or (P.get("dates") or [None])[-1]
    M = (T.get("market") or {}).get(MARKET)
    if not M:
        raise DigestError(f"today.market 에 '{MARKET}' 이 없다")
    secs = (T.get("sectors") or {}).get(MARKET) or {}
    net = M.get("net") or {}

    dom = dominant_actor(net)
    actors = list(SECTOR_ACTORS)
    if dom and dom not in dict(actors):
        actors.append((dom, KO[dom]))
    sectors = []
    for a, ko in actors:
        ranked = _rank(secs, a)
        shown = [(s, pct_int(p)) for s, _v, p in ranked if p is not None]
        hi = sorted(((s, q) for s, q in shown if q >= PCT_HI), key=lambda t: (-t[1], t[0]))
        lo = sorted(((s, q) for s, q in shown if q <= PCT_LO), key=lambda t: (t[1], t[0]))
        top = ranked[:TOP_N]
        # 섹터가 6개 미만인 날 같은 섹터가 상위와 하위에 함께 서지 않게 — 하위는 상위에 없는 것만.
        bottom = [t for t in ranked[::-1] if t not in top][:TOP_N]
        sectors.append({
            "actor": a, "ko": ko, "dominant": a == dom,
            "top": [list(t) for t in top],
            "bottom": [list(t) for t in bottom],
            "hi": [list(t) for t in hi], "lo": [list(t) for t in lo],
        })

    idx = T.get("krx_index") or {}
    rets = []
    for m in P.get("markets") or []:
        own = ((T.get("market") or {}).get(m) or {}).get("ret") or {}
        k = idx.get(m) or {}
        rets.append({"market": m, "index_name": k.get("name"),
                     "index": {w: (k.get("ret") or {}).get(w) for w in ("1", "20")},
                     "basket": {w: own.get(w) for w in ("1", "20")}})

    warn = T.get("approx_gap_warn", 5.0)
    gaps = []
    for m in [MARKET, *(P.get("markets") or [])]:
        g = ((T.get("market") or {}).get(m) or {}).get("approx_gap")
        if g and g.get("pct") is not None and g["pct"] > warn:
            gaps.append({"market": m, "pct": g["pct"]})

    C = P.get("change")
    change = {"prev_date": None, "window": 20, "top_n": 5, "actors": [],
              "reason": P.get("change_reason") or ("비교 못 함" if not C else None)}
    if C:
        change.update(prev_date=C.get("prev_date"), window=C.get("window", 20), top_n=C.get("top_n", 5))
        for a, ko in SECTOR_ACTORS:
            t = ((C.get("top") or {}).get(MARKET) or {}).get(a) or {}
            ok = t.get("entered") is not None and t.get("exited") is not None
            change["actors"].append({"actor": a, "ko": ko,
                                     "entered": _sectors_only(t["entered"]) if ok else None,
                                     "exited": _sectors_only(t["exited"]) if ok else None})

    streak = None
    S = T.get("streaks")
    if isinstance(S, dict) and dom:
        mkt_f = ((S.get("market") or {}).get(MARKET) or {}).get(dom)
        sec = ((S.get("longest") or {}).get(MARKET) or {}).get(dom)
        sec_f = (((S.get("sectors") or {}).get(MARKET) or {}).get(sec) or {}).get(dom) if sec else None
        if mkt_f:
            streak = {"actor": dom, "ko": KO[dom], "market": mkt_f,
                      "sector": sec, "sector_fact": sec_f}

    revision = None
    if not P.get("final") and not P.get("finalize_unverified"):
        R = P.get("revision_stats")
        if isinstance(R, dict):
            revision = {"n_dates": R.get("n_dates", 0), "rel_median_pct": R.get("rel_median_pct"),
                        "flips": R.get("flips", 0), "top3_keep_pct": R.get("top3_keep_pct"),
                        "seed_dates": len(((R.get("seed") or {}).get("dates")) or [])}

    final = None
    if P.get("final"):
        fd = P.get("final_diff")
        final = ({"n_changed": fd.get("n_changed", 0), "n_cells": fd.get("n_cells", 0),
                  "tol": fd.get("tol"), "reason": None} if fd
                 else {"n_changed": None, "n_cells": None, "tol": None,
                       "reason": P.get("final_diff_reason") or "이유 없음"})

    return {
        "schema": 1, "asof": asof, "status": status_label(P), "final": bool(P.get("final")),
        "market": MARKET, "lookback": T.get("lookback"),
        "net": [{"actor": a, "ko": ko, "v": _fact(net.get(a))[0], "pct": _fact(net.get(a))[1]}
                for a, ko in NET_ACTORS],
        "dominant": dom, "returns": rets, "approx_gap": gaps,
        "sectors": sectors, "pct_hi": PCT_HI, "pct_lo": PCT_LO,
        "streak": streak, "revision": revision,
        "change": change, "final_diff": final, "excluded": list(EXCLUDED_SECTORS),
    }


# ─────────────────────────────────────────────────────────── 글

def amt(v) -> str:
    return "—" if v is None else ("+" if v >= 0 else "-") + f"{abs(v):,.0f}"


def pct_tag(p) -> str:
    return "(—)" if p is None else f"({pct_int(p)})"


def ret_str(v) -> str:
    return "—" if v is None else f"{v:+.2f}%"


def fmt_big(v) -> str:
    """누적 금액 — |값| 이 1조(10,000억) 이상이면 `-4.1조`, 아래는 `-3,210억`. 화면 ``fmt_big`` 와 같은 꼴."""
    if v is None:
        return "—"
    if abs(v) >= 9999.5:
        return ("+" if v >= 0 else "-") + f"{abs(v) / 10000:.1f}조"
    return amt(v) + "억"


def streak_text(f: "dict | None") -> str:
    """연속 순매매 한 토막 — `7일 연속 순매도 누적 -4.1조(98)`. 화면 ``_streak_text`` 와 같은 꼴."""
    if not f or not f.get("days"):
        return "연속 없음(오늘 ≈0)"
    side = "순매수" if f.get("sign", 0) > 0 else "순매도"
    return f"{f['days']}일 연속 {side} 누적 {fmt_big(f.get('sum'))}{pct_tag(f.get('pct'))}"


def streak_line(F: dict, with_sector: bool = True) -> "str | None":
    """오늘 금액 최대 주체의 연속 순매매 — 시장 합계와 최장 섹터. 옛 리포트(streaks 없음)는 None."""
    st = F.get("streak")
    if not st:
        return None
    line = f"연속 순매매[일·누적(연속 길이 1년%ile)] — {st['ko']}: {streak_text(st['market'])}"
    if with_sector and st["sector"]:
        line += f" · 최장 섹터 {st['sector']} {streak_text(st['sector_fact'])}"
    return line


def revision_line(F: dict) -> "str | None":
    """잠정 다이제스트의 정직 줄 — 최근 확정일들의 잠정→확정 수정 폭. 확정이거나 기록이 없으면 그렇다고 적는다."""
    R = F.get("revision")
    if R is None:
        return None
    if not R["n_dates"] or R["rel_median_pct"] is None:
        return "잠정→확정 수정 폭: 아직 확정 기록이 없다"
    keep = R["top3_keep_pct"]
    return (f"최근 {R['n_dates']}일 잠정→확정: 섹터 칸 중앙 {pct_int(R['rel_median_pct'])}%,"
            f" 부호 뒤집힘 {R['flips']}회"
            + (f", 상위3 유지 {pct_int(keep)}%" if keep is not None else ""))


def day_label(asof: str) -> str:
    try:
        return f"{asof}({_WEEKDAY[date.fromisoformat(asof).weekday()]})"
    except (TypeError, ValueError):
        return str(asof)


def _items(xs: list, empty: str = "없음") -> str:
    return " · ".join(xs) if xs else empty


def _capped(xs: list[str], cap: int) -> str:
    if not xs:
        return "없음"
    if cap == 0:
        return f"{len(xs)}곳"
    return " · ".join(xs[:cap]) + (f" 외 {len(xs) - cap}" if len(xs) > cap else "")


def change_phrase(a: dict, top_n: int) -> str:
    """직전 리포트 대비 상위 N 의 변동 한 구 — `상위5에 새로 든 섹터: X · 빠진 섹터: Y`."""
    if a["entered"] is None:
        return "비교 없음"
    if not a["entered"] and not a["exited"]:
        return "변동 없음"
    return (f"상위{top_n}에 새로 든 섹터: {_items(a['entered'])}"
            f" · 빠진 섹터: {_items(a['exited'])}")


def body_text(F: dict, extreme_cap: int = EXTREME_CAPS[0], streak: str = "full") -> str:
    """꼬리를 뺀 본문 — 금칙어 검사 대상."""
    L = [f"오늘의 수급 · {day_label(F['asof'])} · {F['status']}"]
    if not F["final"]:
        L.append("잠정치다 — 다음 거래일 새벽 확정치로 바뀔 수 있다.")
        rl = revision_line(F)
        if rl:
            L.append(rl)
    L += ["", f"시장 순매수[억] · {F['market']}(거래소+코스닥) · 괄호는 1년 일별 %ile",
          "- " + " · ".join(f"{n['ko']} {amt(n['v'])}{pct_tag(n['pct'])}" for n in F["net"])]
    for g in F["approx_gap"]:
        L.append(f"- 금액 근사 오차 큼({g['market']} {g['pct']:.1f}%)")
    L += ["", "1일 수익률 — KRX 지수(정규장 15:30) | 자체 바구니(~20시 통합 종가)"]
    for r in F["returns"]:
        L.append(f"- {r['market']}: {r['index_name'] or '지수 없음'} {ret_str(r['index']['1'])}"
                 f" | 바구니 {ret_str(r['basket']['1'])}")
    L += ["", "오늘 섹터 순매수 상·하위 3[억(1년%ile)]"]
    for s in F["sectors"]:
        tag = " — 오늘 금액이 가장 큰 주체" if s["dominant"] else ""
        L.append(f"- {s['ko']}{tag}")
        L.append("  상위: " + _items([f"{n} {amt(v)}{pct_tag(p)}" for n, v, p in s["top"]]))
        L.append("  하위: " + _items([f"{n} {amt(v)}{pct_tag(p)}" for n, v, p in s["bottom"]]))
    L += ["", f"1년 분위 상단(≥{F['pct_hi']})·하단(≤{F['pct_lo']}) — 오늘 값이 1년 일별 분포의 어디인가"]
    for s in F["sectors"]:
        hi = [f"{n} {q}" for n, q in s["hi"]]
        lo = [f"{n} {q}" for n, q in s["lo"]]
        L.append(f"- {s['ko']} 상단: {_capped(hi, extreme_cap)} / 하단: {_capped(lo, extreme_cap)}")
    sl = None if streak == "none" else streak_line(F, with_sector=streak == "full")
    if sl:
        L += ["", sl]
    C = F["change"]
    L.append("")
    if C["prev_date"]:
        L.append(f"{C['window']}일 순매수 상위{C['top_n']} 변동 — {C['window']}일 누적 기준, "
                 f"{C['prev_date'][5:]} 리포트 대비")
        for a in C["actors"]:
            L.append(f"- {a['ko']}: {change_phrase(a, C['top_n'])}")
    else:
        L.append(f"{C['window']}일 순매수 상위{C['top_n']} 변동({C['window']}일 누적 기준): 비교 없음 — {C['reason']}")
    fd = F["final_diff"]
    if fd is not None:
        L.append("")
        if fd["reason"]:
            L.append(f"잠정→확정 비교 없음: {fd['reason']}")
        else:
            L.append(f"잠정→확정 변경: {fd['n_changed']:,}칸 / {fd['n_cells']:,}칸"
                     f"(±{fd['tol']}억 초과, 섹터 1·5일 순매수)")
    return "\n".join(L)


#: 길이가 넘칠 때 줄이는 순서 — 극단 목록부터, 마지막에 연속 줄의 최장 섹터 → 연속 줄 전체.
TRIM_LADDER = tuple([(cap, "full") for cap in EXTREME_CAPS] + [(0, "nosector"), (0, "none")])


def compose(F: dict) -> tuple[str, str]:
    """(본문, 전문). 길이가 넘치면 :data:`TRIM_LADDER` 순으로 줄이고, 그래도 넘치면 :class:`DigestError`."""
    for cap, streak in TRIM_LADDER:
        body = body_text(F, cap, streak)
        full = f"{body}\n\n{FOOTER}\n"
        if len(full) <= MAX_CHARS:
            return body, full
    raise DigestError(f"글이 {len(full)}자로 상한 {MAX_CHARS}자를 넘는다(줄일 수 있는 것을 다 줄인 뒤에도)")


#: 금칙어 비교에서 한 항목으로 보는 단위의 경계 — 목록 구분자(` · `·` | `), 줄, `: `.
_ITEM_SEP = re.compile(r"\s+[·|]\s+|\n|:\s")


def _letters(t: str) -> str:
    return "".join(c for c in t if unicodedata.category(c)[0] in "LN")


def banned_hits(text: str) -> list[str]:
    """``text`` 에 든 금칙어. NFKC 로 정규화한 뒤 항목(:data:`_ITEM_SEP` 로 나눈 것)마다 글자·숫자만 남겨
    비교한다 — 공백(`수익  보장`)·폭 0 문자·문장부호(`추.천`·`급-등`)를 끼워도 잡는다. 항목 경계를 넘어
    붙이지는 않는다(`통신 · 호텔` 이 `신호` 가 되지 않게).
    """
    items = [_letters(it) for it in _ITEM_SEP.split(unicodedata.normalize("NFKC", text))]
    return [w for w in BANNED if any(_letters(w) in it for it in items)]


def check_compliance(body: str, where: str = "digest.md") -> None:
    """금칙어가 하나라도 있으면 만들지 않는다. 꼬리(:data:`FOOTER`)는 넘기지 않는다 — 면책 문구 자체가
    "추천이 아니다" 를 말해야 하므로."""
    hits = banned_hits(body)
    if hits:
        raise DigestError(f"{where} 본문에 금칙어 {hits} — 만들지 않는다. 섹터 이름이면 사람이 확인하라")


# ─────────────────────────────────────────────────────────── 카드

def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _sign(v) -> str:
    return "flat" if not v else ("buy" if v > 0 else "sell")


def card_html(F: dict, extreme_cap: int = CARD_LADDER[0][0], streak: str = CARD_LADDER[0][1]) -> str:
    """카드 HTML — 값은 여기서 박아 넣는다(스크립트 없음). 템플릿의 ``__KEY__`` 를 다 채워야 한다."""
    vmax = max((abs(n["v"]) for n in F["net"] if n["v"] is not None), default=0.0) or 1.0
    net_rows = []
    for n in F["net"]:
        v = n["v"]
        w = 0.0 if v is None else abs(v) / vmax * 50.0
        side = "left:50%" if (v or 0) >= 0 else f"left:{50.0 - w:.2f}%"
        net_rows.append(
            f'<div class="net-row"><span class="net-ko">{_e(n["ko"])}</span>'
            f'<span class="net-track"><span class="zero"></span>'
            f'<span class="bar {_sign(v)}" style="{side};width:{w:.2f}%"></span></span>'
            f'<span class="net-v">{_e(amt(v))}</span><span class="net-p">{_e(pct_tag(n["pct"]))}</span></div>')
    ret_rows = []
    for r in F["returns"]:
        ret_rows.append(
            f'<tr><th>{_e(r["market"])}</th><td>{_e(r["index_name"] or "지수 없음")}</td>'
            f'<td class="num">{_e(ret_str(r["index"]["1"]))}</td><td class="num">{_e(ret_str(r["index"]["20"]))}</td>'
            f'<td class="num">{_e(ret_str(r["basket"]["1"]))}</td><td class="num">{_e(ret_str(r["basket"]["20"]))}</td></tr>')
    cols = []
    for s in F["sectors"]:
        def li(rows):
            return "".join(
                f'<li><i class="dot {_sign(v)}"></i><span class="sn">{_e(n)}</span>'
                f'<span class="sv">{_e(amt(v))}</span><span class="sp">{_e(pct_tag(p))}</span></li>'
                for n, v, p in rows) or "<li>없음</li>"
        tag = '<em class="dom">오늘 금액 최대</em>' if s["dominant"] else ""
        cols.append(f'<div class="col"><h3>{_e(s["ko"])}{tag}</h3><h4>상위 3</h4><ol>{li(s["top"])}</ol>'
                    f'<h4>하위 3</h4><ol>{li(s["bottom"])}</ol></div>')
    ext = []
    for s in F["sectors"]:
        hi = _capped([f"{n} {q}" for n, q in s["hi"]], extreme_cap)
        lo = _capped([f"{n} {q}" for n, q in s["lo"]], extreme_cap)
        ext.append(f'<li><b>{_e(s["ko"])}</b> 상단: {_e(hi)} <span class="sep">/</span> 하단: {_e(lo)}</li>')
    C = F["change"]
    if C["prev_date"]:
        chg_head = f'{C["window"]}일 순매수 상위{C["top_n"]} 변동 · {C["window"]}일 누적 기준 · {C["prev_date"][5:]} 리포트 대비'
        chg = "".join(f'<li><b>{_e(a["ko"])}</b> {_e(change_phrase(a, C["top_n"]))}</li>' for a in C["actors"])
    else:
        chg_head = f'{C["window"]}일 순매수 상위{C["top_n"]} 변동 · {C["window"]}일 누적 기준'
        chg = f"<li>비교 없음 — {_e(C['reason'])}</li>"
    fd = F["final_diff"]
    notes = []
    sl = None if streak == "none" else streak_line(F, with_sector=streak == "full")
    if sl:
        notes.append(sl)
    if not F["final"]:
        notes.append("잠정치다 — 다음 거래일 새벽 확정치로 바뀔 수 있다."
                     + (f" {revision_line(F)}." if revision_line(F) else ""))
    if fd is not None:
        notes.append(f"잠정→확정 비교 없음: {fd['reason']}" if fd["reason"] else
                     f"잠정→확정 변경: {fd['n_changed']:,}칸 / {fd['n_cells']:,}칸(±{fd['tol']}억 초과)")
    gaps = "".join(f'<span class="chip warn">⚠ 금액 근사 오차 큼 {_e(g["market"])} {g["pct"]:.1f}%</span>'
                   for g in F["approx_gap"])
    fill = {
        "CARD_W": str(CARD_W), "CARD_H": str(CARD_H),
        "DATE": _e(day_label(F["asof"])), "STATUS": _e(F["status"]),
        "STATUS_CLASS": "final" if F["final"] else "prov",
        "MARKET": _e(F["market"]), "NET_ROWS": "".join(net_rows), "GAPS": gaps,
        "RET_ROWS": "".join(ret_rows), "SECTOR_COLS": "".join(cols),
        "PCT_HI": str(F["pct_hi"]), "PCT_LO": str(F["pct_lo"]), "EXTREMES": "".join(ext), "CHANGE_HEAD": _e(chg_head), "CHANGE": chg,
        "NOTES": "".join(f"<p>{_e(n)}</p>" for n in notes),
        "DISCLAIMER": _e(DISCLAIMER), "SOURCE": _e(SOURCE),
    }
    out = TEMPLATE.read_text("utf-8")
    for k, v in fill.items():
        out = out.replace(f"__{k}__", v)
    left = re.findall(r"__[A-Z_]+__", out)
    if left:
        raise DigestError(f"카드 템플릿에 못 채운 자리 {sorted(set(left))}")
    return out


def card_visible_text(page: str) -> str:
    """카드에서 사람이 읽는 글 — 스타일·태그를 지우고 고정 꼬리를 뺀다(금칙어 검사용)."""
    t = re.sub(r"<style.*?</style>|<!--.*?-->", " ", page, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    for fixed in (DISCLAIMER, SOURCE):
        t = t.replace(fixed, " ")
    return t


def _cached_chromium() -> "str | None":
    """Playwright 판이 기대하는 브라우저가 없을 때 — 이미 받아 둔 Chromium 중 가장 새 것."""
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.expanduser("~/.cache/ms-playwright")
    found = sorted(glob.glob(os.path.join(root, "chromium-*", "chrome-linux*", "chrome")),
                   key=lambda p: int(re.search(r"chromium-(\d+)", p).group(1)))
    return found[-1] if found else None


#: 브라우저 단계마다의 상한(ms). 배치 쪽 상한은 daily_report.sh 의 ``timeout`` 이 따로 건다.
RENDER_TIMEOUT_MS = 30_000
#: 글꼴 로딩 대기 — 5초가 지나면 있는 글꼴로 찍는다(넘침 검사가 그 결과를 다시 본다).
FONTS_READY_JS = "Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 5000))]).then(() => true)"

#: 카드 안 모든 요소의 (오른쪽 끝, 아래 끝) 최댓값 — 넘침 검사. body 의 안쪽 여백까지 포함해 잰다.
CONTENT_EXTENT_JS = """() => {
  const pb = parseFloat(getComputedStyle(document.body).paddingBottom) || 0;
  let r = 0, b = 0;
  for (const el of document.body.querySelectorAll('*')) {
    const q = el.getBoundingClientRect();
    if (q.width === 0 && q.height === 0) continue;
    r = Math.max(r, q.right); b = Math.max(b, q.bottom);
  }
  return [r, b + pb];
}"""


def check_extent(sw: float, sh: float) -> None:
    """내용 상자 끝(:data:`CONTENT_EXTENT_JS`)이 카드 밖이면 :class:`CardOverflow` — 찍지 않는다."""
    if sh > CARD_H or sw > CARD_W:
        raise CardOverflow(f"카드 내용이 {sw:.0f}×{sh:.0f}px 로 {CARD_W}×{CARD_H} 를 넘친다 — 잘린 카드를 찍지 않는다")


def render_png(html_path: Path, png_path: Path) -> None:
    """카드를 찍는다. 내용이 카드 높이를 넘치면(잘린 채 찍히면) :class:`DigestError`.

    네트워크를 쓰지 않는다 — 카드는 외부 자원이 없고(글꼴은 시스템 것), 브라우저를 받지도 않는다.
    """
    from playwright.sync_api import sync_playwright      # 없으면 ImportError — 부른 쪽이 건너뛴다

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(timeout=RENDER_TIMEOUT_MS)
        except Exception as e:                          # 판이 맞는 브라우저가 없다
            exe = _cached_chromium()
            if not exe:
                raise DigestError(f"Chromium 을 못 띄웠다: {type(e).__name__}") from e
            browser = p.chromium.launch(executable_path=exe, timeout=RENDER_TIMEOUT_MS)
        try:
            page = browser.new_page(viewport={"width": CARD_W, "height": CARD_H}, device_scale_factor=1)
            page.route("**/*", lambda r: r.continue_() if r.request.url.startswith("file:") else r.abort())
            page.set_default_timeout(RENDER_TIMEOUT_MS)
            page.goto(html_path.resolve().as_uri(), wait_until="load", timeout=RENDER_TIMEOUT_MS)
            # 글꼴 대기는 상한을 둔다 — fonts.ready 가 안 풀리는 환경에서 배치가 멈추면 안 된다.
            page.evaluate(FONTS_READY_JS)
            # scrollHeight 는 못 쓴다 — 카드가 html·body 를 고정 크기·overflow:hidden 으로 묶어 늘 카드 높이를
            # 돌려준다(꼬리 면책 문구가 잘린 카드가 이 검사를 통과했다). 요소마다 실제 상자 끝을 잰다.
            check_extent(*page.evaluate(CONTENT_EXTENT_JS))
            page.screenshot(path=str(png_path), clip={"x": 0, "y": 0, "width": CARD_W, "height": CARD_H},
                            timeout=RENDER_TIMEOUT_MS)
        finally:
            browser.close()


# ─────────────────────────────────────────────────────────── 실행

OUTPUTS = ("digest.md", "digest.json", "digest_card.html", "digest.png")


def build(P: dict) -> tuple[dict, str, list[str]]:
    """(사실, 글, 카드 HTML 판들) — 금칙어·길이·꼬리를 다 통과해야 돌려준다. 파일은 쓰지 않는다.

    카드 판은 극단 목록 길이·연속 줄 꼴만 다르다(:data:`CARD_LADDER`). 다 미리 검사해 둔다 — 찍다가 넘쳐
    줄인 판이 검사 없이 나가지 않게."""
    F = collect(P)
    body, text = compose(F)
    check_compliance(body, "digest.md")
    if not text.endswith(FOOTER + "\n"):
        raise DigestError("고정 꼬리(면책·자료 출처)가 글 끝에 없다")
    pages = [card_html(F, cap, st) for cap, st in CARD_LADDER]
    for page in pages:
        check_compliance(card_visible_text(page), "digest_card.html")
        if DISCLAIMER not in html.unescape(page):
            raise DigestError("카드에 면책 문구가 없다")
    F["text_chars"] = len(text)
    F["disclaimer"], F["source"] = DISCLAIMER, SOURCE
    return F, text, pages


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", required=True, help="리포트 폴더(payload.json 이 있는 곳)")
    ap.add_argument("--out", help="산출물을 쓸 폴더(기본 --dir). 읽기 전용 리포트에서 밖으로 쓸 때")
    ap.add_argument("--png", choices=("auto", "require", "skip"), default="auto",
                    help="auto = Playwright 가 없으면 건너뜀 · require = 못 찍으면 실패 · skip")
    ap.add_argument("--allow-unverified", action="store_true",
                    help="VERIFY_FAILED.txt 가 있는 폴더에서도 만든다(기본은 거부)")
    a = ap.parse_args(argv)
    d = Path(a.dir)
    out = Path(a.out) if a.out else d

    def clear():
        for n in OUTPUTS:
            (out / n).unlink(missing_ok=True)

    try:
        if (d / "VERIFY_FAILED.txt").exists() and not a.allow_unverified:
            raise DigestError("자기 검증에 걸린 리포트다(VERIFY_FAILED.txt) — 다이제스트를 만들지 않는다")
        with open(d / "payload.json", encoding="utf-8") as f:
            P = json.load(f)
        F, text, pages = build(P)
    except (DigestError, OSError, ValueError) as e:
        clear()                    # 옛 다이제스트가 오늘 것처럼 남지 않게
        print(f"[digest] 만들지 않았다 — {e}", file=sys.stderr)
        return 1

    out.mkdir(parents=True, exist_ok=True)
    clear()
    (out / "digest.md").write_text(text, encoding="utf-8")
    (out / "digest_card.html").write_text(pages[0], encoding="utf-8")
    png = "건너뜀"
    if a.png != "skip":
        try:
            for i, page in enumerate(pages):
                (out / "digest_card.html").write_text(page, encoding="utf-8")
                try:
                    render_png(out / "digest_card.html", out / "digest.png")
                    break
                except CardOverflow as e:
                    if i == len(pages) - 1:
                        raise
                    cap, st = CARD_LADDER[i + 1]
                    print(f"[digest] {e} — 극단 목록 {cap}개·연속 줄 {st} 로 줄여 다시 찍는다",
                          file=sys.stderr)
            png = "digest.png"
        except ImportError:
            if a.png == "require":
                print("[digest] Playwright 가 없다 — uv run --with playwright 로 돌려라", file=sys.stderr)
                clear()
                return 1
            png = "건너뜀(Playwright 없음)"
        except Exception as e:     # 카드만 못 찍었다 — 글·사실은 남긴다(require 면 실패)
            (out / "digest.png").unlink(missing_ok=True)
            print(f"[digest] 카드를 못 찍었다 — {type(e).__name__}: {e}", file=sys.stderr)
            if a.png == "require":
                clear()
                return 1
            png = "실패"
    F["png"] = png if png == "digest.png" else None
    (out / "digest.json").write_text(json.dumps(F, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[digest] {out}: digest.md {F['text_chars']}자 · digest.json · 카드 {png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
