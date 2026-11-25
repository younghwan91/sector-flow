"""하루 수급 지도의 게시용 글 **초안** — ``payload.json`` → X·텔레그램에 붙일 짧은 글. 표준 라이브러리만.

    uv run python -m sectorflow.caption --demo                        # 합성 데모의 마지막 날(표지 [합성 데모])
    uv run python -m sectorflow.caption --payload reports/latest/payload.json --date 2026-11-25

**보내지 않는다.** 표준 출력에 글을 쓸 뿐이고 네트워크를 쓰지 않는다. 게시는 사람이 정한다 — 실데이터 글은 KRX·키움 자료의
재배포 권리 회신 전에는 밖에 게시하지 않는다(``scripts/digest.py`` 머리말과 같은 사정). 실데이터면 표준 오류에 그 경고를 낸다.

글의 숫자는 지도와 같은 길로 센다 — 창 합계는 ``flowmap.window_sums``, 가장 큰 주체는 ``summary.dominant``(``map_headline`` 과
같은 규칙), 금액 꼴은 ``summary.fmt_big``. 두 주체를 잇지 않는다: "개인이 판 것을 외국인이 받았다" 는 관측이 아니다(주변합으로
쌍별 이전량이 정해지지 않는다). 그래서 "가장 크게 순매도한 주체가 가장 많이 넘긴 섹터" 와 "가장 크게 순매수한 주체가 가장 많이
받은 섹터" 를 **따로** 적는다. 지도 낱말 검사(``wording.map_hits``)에 걸리면 글을 내지 않는다.

하루 폴더가 아니라 지난 날의 지도(영구 링크 ``/d/<날짜>/``)를 지을 때는 :func:`upto` 로 페이로드를 그날까지 자른다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sectorflow import flowmap, summary
from sectorflow.basis import DEMO_MARK
from sectorflow.josa import josa
from sectorflow.wording import map_hits

#: 공개 랜딩 — 글 끝의 링크. 날짜 영구 링크는 ``<SITE>d/<날짜>/``(``site/build.py``).
SITE = "https://younghwan91.github.io/sector-flow/"
#: 공개 랜딩이 날짜 영구 링크를 짓는 날 수 — 합성 데모의 마지막 이만큼(``site/build.py``·카드 ``scripts/day_cards.py``).
PERMALINK_DAYS = 5
#: 지도가 쓰는 페이로드 키(``flowmap.build_map_data`` 머리말) — :func:`upto` 는 이것만 남긴다. 그날의 ``today``·``change`` 등은
#: 그날 배치가 계산한 값이라 잘라서 만들 수 없다 — 남기면 다른 날의 요약이 섞인다.
MAP_KEYS = ("dates", "sectors", "markets", "flows", "detail", "detail_labels", "capability", "venue", "demo",
            "sector_source", "n_by_sector")
#: 맺음 — 동시기 관측이고 쌍별 거래가 아니다.
TAIL = "주체별 순매매 합계(누가 누구에게 판 것이 아님) · 동시기 관측, 예측·투자 조언 아님"
#: X 무게(한글·한자 2, 나머지 1, 링크 23) 상한.
X_LIMIT = 280


class CaptionError(ValueError):
    """글을 만들지 않았다 — 이유가 메시지다."""


def _slice(o, n: int, k: int):
    """길이 ``n`` 인 목록(날짜축)을 앞 ``k`` 개로 — 사전은 안으로 들어간다."""
    if isinstance(o, dict):
        return {key: _slice(v, n, k) for key, v in o.items()}
    if isinstance(o, list) and len(o) == n:
        return o[:k]
    return o


def upto(P: dict, day: str) -> dict:
    """``day`` 로 끝나는 지도용 페이로드 — 날짜축 값을 그날까지 자르고 :data:`MAP_KEYS` 만 남긴다."""
    dates = list(P["dates"])
    if day not in dates:
        raise CaptionError(f"{day} 는 페이로드 날짜에 없다({dates[0]}~{dates[-1]}, 거래일만)")
    n, k = len(dates), dates.index(day) + 1
    out = {key: P[key] for key in MAP_KEYS if key in P}
    out["dates"] = dates[:k]
    for key in ("flows", "detail"):
        if key in out:
            out[key] = _slice(out[key], n, k)
    return out


def day_facts(P: dict, market: str = flowmap.ALL_MARKETS) -> dict:
    """마지막 날 한 시장의 사실 — 주체별 순매매, 가장 크게 판·산 주체와 그 주체의 맨 위 섹터."""
    N = len(P["dates"])
    if not N:
        raise CaptionError("날짜가 없다")
    sums = flowmap.window_sums(P, flowmap.market_list(P, market), N - 1, N - 1)
    sums = {s: row for s, row in sums.items() if s != summary.UNCLASSIFIED}
    actors = flowmap.present_actors(P)
    net = {a: sum(r[a] for r in sums.values()) for a in actors}
    pick = {f: {a: {"v": v} for a, v in net.items() if (v < -flowmap.EPS if f == "gave" else v > flowmap.EPS)}
            for f in ("gave", "took")}
    out = {"date": P["dates"][-1], "market": market, "demo": bool(P.get("demo")), "net": net}
    for side, sign in (("gave", -1), ("took", 1)):
        a = summary.dominant(pick[side])
        if a is None:
            out[side] = None
            continue
        top = max(sums, key=lambda s: (sign * sums[s][a], s))
        out[side] = {"actor": a, "label": flowmap.ACTOR_LABEL[a], "total": net[a],
                     "sector": top if sign * sums[top][a] > flowmap.EPS else None, "sector_v": sums[top][a]}
    return out


def _big(v: float) -> str:
    return summary.fmt_big(v).replace("-", "−")


def x_weight(text: str) -> int:
    """X 글자 무게 — 링크는 23, 한글·한자·전각은 2, 나머지 1(근사)."""
    w = 0
    for tok in text.split(" "):
        if tok.startswith("http"):
            w += 23
        else:
            w += sum(2 if ord(ch) >= 0x1100 else 1 for ch in tok)
        w += 1
    return w - 1


def caption(P: dict, *, url: "str | None" = None, long: bool = False, today: bool = True) -> str:
    """마지막 날의 게시용 글 — ``today`` 면 "오늘 …" 으로 연다(지난 날 글이면 끈다). ``long`` 이면 시장 4주체 줄과 지도 머리글의 큰 칸을 더한다(텔레그램). 낱말 검사에 걸리면 :class:`CaptionError`."""
    f = day_facts(P)
    d = f["date"]
    head = (f"{DEMO_MARK} " if f["demo"] else "") + f"{d[5:7]}/{d[8:10]} 섹터 수급 지도"
    g, t = f["gave"], f["took"]
    if not g and not t:
        raise CaptionError(f"{d}: 순매수·순매도가 없다 — 글을 만들지 않는다")
    parts = []
    if g:
        end = "넘겼고" if t else "넘겼습니다"
        parts.append(f"{josa(g['label'], '은/는')} {josa(g['sector'], '을/를')} 가장 많이 {end}({_big(g['sector_v'])})"
                     if g["sector"] else f"{josa(g['label'], '이/가')} 가장 크게 순매도{'했고' if t else '했습니다'}({_big(g['total'])})")
    if t:
        parts.append(f"{josa(t['label'], '은/는')} {josa(t['sector'], '을/를')} 가장 많이 받았습니다({_big(t['sector_v'])})"
                     if t["sector"] else f"{josa(t['label'], '이/가')} 가장 크게 순매수했습니다({_big(t['total'])})")
    body = ("오늘 " if today else "") + ", ".join(parts) + "."
    lines = [head, body]
    if long:
        lines.append("시장 전체 순매매: " + " · ".join(f"{flowmap.ACTOR_LABEL[a]} {_big(v)}" for a, v in f["net"].items()
                                                 if a in dict(summary.ACT4)))
        lines += ["· " + s for s in flowmap.map_headline(P, flowmap.ALL_MARKETS, 1)["facts"]]
    lines.append(TAIL)
    if url:
        lines.append(url)
    text = "\n".join(lines)
    bad = map_hits(text)
    if bad:
        raise CaptionError(f"글에 피할 말이 들었다 {bad} — 만들지 않는다")
    return text


def permalink(day: str, site: str = SITE) -> str:
    return f"{site}d/{day}/"


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--payload", help="리포트 폴더의 payload.json")
    src.add_argument("--demo", action="store_true", help="합성 데모로(sectorflow.demo 가 짓는다, 처음 한 번 수십 초)")
    ap.add_argument("--date", help="그날까지 자른 지도로(기본: 마지막 날)")
    ap.add_argument("--long", action="store_true", help="텔레그램용 — 시장 4주체 줄과 큰 칸 셋을 더한다")
    ap.add_argument("--url", help="글 끝 링크(기본: 데모면 공개 랜딩의 날짜 영구 링크, 실데이터면 없음)")
    a = ap.parse_args(argv)
    if a.demo:
        from sectorflow import demo  # noqa: PLC0415 — pandas 등이 데모를 지을 때만 필요하다

        path = Path(demo.ensure()) / "payload.json"
    else:
        path = Path(a.payload)
    P = json.loads(path.read_text("utf-8"))
    try:
        if a.date:
            P = upto(P, a.date)
        url = a.url or (permalink(P["dates"][-1]) if P.get("demo") else None)
        text = caption(P, url=url, long=a.long, today=not a.date)
    except CaptionError as e:
        print(f"[caption] {e}", file=sys.stderr)
        return 1
    if not P.get("demo"):
        print("[caption] 실데이터 글이다 — KRX·키움 재배포 권리 회신 전에는 밖에 게시하지 않는다(초안으로만).", file=sys.stderr)
    w = x_weight(text)
    if w > X_LIMIT and not a.long:
        print(f"[caption] X 무게 {w} > {X_LIMIT} — 줄여서 올려라", file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
