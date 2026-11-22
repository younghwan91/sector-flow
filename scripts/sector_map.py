#!/usr/bin/env python3
"""수급 지도 페이지 — ``payload.json`` → ``map.html``(넘긴 쪽 → 섹터 → 넘겨받은 쪽, 주체별 보기, 타임랩스).

데이터는 :func:`sectorflow.flowmap.build_map_data` 가 짓고, 여기서는 템플릿 ``templates/sector_map.html`` 의
``const D = __DATA__;`` 한 줄에 심는다(수치표·뷰어와 같은 방식). 그리는 간선은 (주체, 섹터)뿐이다 —
섹터→섹터·주체→주체 연결은 관측되지 않아 데이터에도 없다(설계 ``docs/superpowers/specs/2026-11-21-flow-map-brand-design.md``).

    python scripts/sector_map.py --payload reports/latest/payload.json --html /tmp/map.html
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from sectorflow.basis import DATA_BASIS_SHORT, DEMO_NOTICE  # noqa: E402
from sectorflow.flowmap import build_map_data  # noqa: E402

#: 지도·카드의 출처 줄. 데모는 실데이터가 아니므로 출처를 따로 적는다.
SOURCE = "자료: 한국거래소 투자자별 매매동향(키움증권 API로 수집) · 금액은 순매수 수량×종가 근사 · 섹터 구분은 자체 분류"
DEMO_SOURCE = "자료: 합성 데모(sectorflow.demo 가 난수로 만든 가짜 숫자·가짜 이름) — 실데이터 아님"

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "sector_map.html")


def render(data: dict, template: str) -> str:
    """템플릿에 지도 데이터·표지를 심는다. ``</`` 는 스크립트가 닫히지 않게 바꾼다."""
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (template.replace("__DATA__", blob)
            .replace("__BASIS__", data.get("basis") or DATA_BASIS_SHORT)
            .replace("__SOURCE__", DEMO_SOURCE if data.get("demo") else SOURCE)
            .replace("__DEMO_NOTICE__", DEMO_NOTICE))


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--payload", required=True, help="sector_flow.py 가 만든 payload.json")
    ap.add_argument("--html", required=True, help="쓸 map.html")
    a = ap.parse_args(argv)
    with open(a.payload, encoding="utf-8") as f:
        P = json.load(f)
    data = build_map_data(P)
    with open(TEMPLATE, encoding="utf-8") as f:
        html = render(data, f.read())
    with open(a.html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {a.html}  (asof {data['asof']}, {len(html) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
