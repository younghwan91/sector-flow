#!/usr/bin/env python3
"""수급 지도 공유 카드 — 리포트 폴더의 ``map.html`` 을 카드 배치로 열어 ``map.png``(1080×1350)·``map_wide.png``(1200×675)를 찍는다.

카드 배치는 페이지 자신이 그린다(``map.html?card=tall|wide`` — 정지, 입자 없음, 조작 없음, 단위·근사·출처 줄, 데모면 ``[합성 데모]``).
여기서는 그 주소를 Chromium 으로 열어 찍을 뿐이다. 카드는 **폴더 안의 파일**이다 — 밖으로 보내는 기능은 없다(다이제스트와 같다).
네트워크를 쓰지 않는다(파일 밖 요청은 막는다 — 글꼴은 시스템 것으로 떨어진다).

    uv run --with 'playwright==1.48.0' python scripts/map_card.py --dir <짓는 폴더>
    uv run --with 'playwright==1.48.0' python scripts/map_card.py --dir reports/latest --out /tmp/card

종료코드: 0 찍었다, 1 못 찍었다(넘침·빈 그림·브라우저 오류 — 반쯤 찍힌 카드는 지운다), 2 Playwright·Chromium 이 없다.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

#: (``?card=`` 값, 파일, 폭, 높이).
CARDS = (("tall", "map.png", 1080, 1350), ("wide", "map_wide.png", 1200, 675))
OUTPUTS = tuple(c[1] for c in CARDS)
#: 그림이 다 그려졌는가 — 노드가 있고 두 프레임이 지났다(ResizeObserver 가 그린 뒤).
READY_JS = """() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() =>
  r(document.documentElement.classList.contains("card") ? document.querySelectorAll("#svg .node").length : -1))))"""

#: 그림 밖으로 나간 노드 — SVG 는 제 상자 밖을 잘라 그리므로 넘친 노드는 넘침 검사(요소 상자)로는 안 보이고 그냥 사라진다.
NODES_OUTSIDE_JS = """() => { const b = document.getElementById("svg").getBoundingClientRect();
  return [...document.querySelectorAll("#svg .node rect:first-child")].filter(r => { const q = r.getBoundingClientRect();
    return q.top < b.top - 1 || q.bottom > b.bottom + 1 || q.left < b.left - 1 || q.right > b.right + 1; }).length; }"""


class CardError(RuntimeError):
    """카드를 찍지 않았다 — 이유가 메시지다."""


class NoBrowser(CardError):
    """Playwright·Chromium 이 없다 — 카드 결함이 아니라 환경 사정이다(종료코드 2)."""


def _digest():
    """Chromium 찾기·대기 상한·넘침 측정은 다이제스트 카드와 같은 것을 쓴다(``scripts/digest.py``)."""
    spec = importlib.util.spec_from_file_location("_map_card_digest", Path(__file__).with_name("digest.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def shoot(html: Path, out: Path) -> list[str]:
    """``html`` 을 카드마다 열어 ``out`` 에 찍는다 — 찍은 파일 이름들. 하나라도 못 찍으면 :class:`CardError`."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise NoBrowser("Playwright 가 없다 — uv run --with 'playwright==1.48.0' 로 돌려라") from e
    dg = _digest()
    done = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(timeout=dg.RENDER_TIMEOUT_MS)
        except Exception as e:                          # 판이 맞는 브라우저가 없다
            exe = dg._cached_chromium()
            if not exe:
                raise NoBrowser(f"Chromium 을 못 띄웠다: {type(e).__name__}") from e
            browser = p.chromium.launch(executable_path=exe, timeout=dg.RENDER_TIMEOUT_MS)
        try:
            for mode, name, w, h in CARDS:
                page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1, reduced_motion="reduce")
                errors: list[str] = []
                page.on("pageerror", lambda e, x=errors: x.append(str(e)))
                page.route("**/*", lambda r: r.continue_() if r.request.url.startswith("file:") else r.abort())
                page.set_default_timeout(dg.RENDER_TIMEOUT_MS)
                page.goto(html.resolve().as_uri() + f"?card={mode}", wait_until="load", timeout=dg.RENDER_TIMEOUT_MS)
                page.evaluate(dg.FONTS_READY_JS)
                nodes = page.evaluate(READY_JS)
                if errors:
                    raise CardError(f"{name}: 페이지 예외 {errors[0]}")
                if nodes == -1:
                    raise CardError(f"{name}: 페이지가 카드 배치(?card={mode})를 모른다 — 옛 map.html 인가")
                if nodes <= 0:
                    raise CardError(f"{name}: 그림에 노드가 없다 — 빈 카드를 찍지 않는다")
                outside = page.evaluate(NODES_OUTSIDE_JS)
                if outside:
                    raise CardError(f"{name}: 노드 {outside}개가 그림 밖으로 나가 잘린다")
                sw, sh = page.evaluate(dg.CONTENT_EXTENT_JS)
                if sw > w + 1 or sh > h + 1:
                    raise CardError(f"{name}: 내용이 {sw:.0f}×{sh:.0f}px 로 {w}×{h} 를 넘친다 — 잘린 카드를 찍지 않는다")
                page.screenshot(path=str(out / name), clip={"x": 0, "y": 0, "width": w, "height": h},
                                timeout=dg.RENDER_TIMEOUT_MS)
                page.close()
                done.append(name)
        finally:
            browser.close()
    return done


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", required=True, help="리포트 폴더(map.html 이 있는 곳)")
    ap.add_argument("--out", help="카드를 쓸 폴더(기본 --dir). 게시된 폴더에서 밖으로 쓸 때")
    a = ap.parse_args(argv)
    d = Path(a.dir)
    out = Path(a.out) if a.out else d
    if not a.out and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.resolve().name):
        print(f"[map_card] 게시된 폴더({d.resolve().name})에는 쓰지 않는다 — --out 으로 밖에 쓰거나, "
              "그 기준일을 다시 만들어라(배치는 짓는 폴더 안에서 찍는다)", file=sys.stderr)
        return 2
    html = d / "map.html"

    def clear():
        for n in OUTPUTS:
            (out / n).unlink(missing_ok=True)

    out.mkdir(parents=True, exist_ok=True)
    clear()                        # 옛 카드가 오늘 것처럼 남지 않게
    try:
        if not html.is_file():
            raise CardError(f"{html} 이 없다 — 수급 지도를 먼저 짓는다(scripts/sector_map.py)")
        done = shoot(html, out)
    except NoBrowser as e:
        print(f"[map_card] 찍지 않았다 — {e}", file=sys.stderr)
        return 2
    except Exception as e:         # 넘침·빈 그림·브라우저 오류 — 반쯤 찍힌 카드를 남기지 않는다
        clear()
        print(f"[map_card] 찍지 않았다 — {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"[map_card] {out}: {' · '.join(done)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
