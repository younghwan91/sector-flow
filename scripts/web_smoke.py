#!/usr/bin/env python3
"""웹 스모크 — 진짜 Chromium(Playwright)으로 numbers.html·viewer.html 을 폭 1440·390 에서 열어 본다.

검증기 W 층은 node 최소 DOM 으로 **숫자**를 맞춘다 — 브라우저 오류와 레이아웃은 못 본다. 여기서는 셋만 본다.

* 콘솔 오류·페이지 예외가 없는가.
* 문서가 가로로 넘치지 않는가 — ``documentElement.scrollWidth > innerWidth``. 본표(``.tw``)는 제 안에서 옆으로 미는
  것이 의도라 문서 폭만 본다(폰 390 에서 페이지 전체가 밀리면 결함이다).
* numbers.html 의 '오늘' 패널(``#today``)이 보이고 비어 있지 않은가.

외부 글꼴(fonts.googleapis.com) 요청은 빈 응답으로 막는다 — 네트워크 사정이 콘솔 오류로 섞이지 않게.

사용::

    uv run --with playwright python scripts/web_smoke.py --dir reports/latest
    uv run --with playwright python scripts/web_smoke.py --dir <폴더> --shots <캡처 폴더>

Chromium 은 Playwright 기본 설치 → 없으면 ``SF_CHROMIUM`` → ``~/.cache/ms-playwright/chromium-*/chrome-linux/chrome``.
종료코드: 0 통과, 1 문제 있음, 2 Playwright·Chromium 이 없어 못 돌았다.
주간 ``harness-selftest`` 워크플로가 ``tests/test_web_smoke.py`` 로 이것을 돈다(실패해도 배포를 막지 않는다).
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

#: 볼 폭 — 데스크톱과 폰(README 의 웹 캡처와 같은 폭).
WIDTHS = (1440, 390)
PAGES = ("numbers.html", "viewer.html")
#: 넘침 여유[px] — 소수 픽셀 반올림.
OVERFLOW_SLACK = 1

_MEASURE = """() => {
  const t = document.getElementById("today"), g = document.getElementById("t-grid");
  let today = null;
  if (t) { const r = t.getBoundingClientRect(), cs = getComputedStyle(t);
    today = {w: r.width, h: r.height, top: r.top + window.scrollY, display: cs.display, visibility: cs.visibility,
             cards: g ? g.children.length : 0}; }
  return {scroll_w: document.documentElement.scrollWidth, inner_w: window.innerWidth, today};
}"""


def judge(page: str, width: int, facts: dict) -> list[str]:
    """한 페이지·한 폭의 잰 값 → 문제 목록(빈 목록이면 통과). 브라우저 없이 시험한다."""
    tag = f"{page}@{width}"
    out = [f"{tag} 콘솔 오류: {m}" for m in facts.get("console") or []]
    out += [f"{tag} 페이지 예외: {m}" for m in facts.get("errors") or []]
    if facts.get("scroll_w", 0) > facts.get("inner_w", 0) + OVERFLOW_SLACK:
        out.append(f"{tag} 가로 넘침: 문서 폭 {facts['scroll_w']}px > 창 {facts['inner_w']}px")
    if page == "numbers.html":
        t = facts.get("today")
        if not t:
            out.append(f"{tag} '오늘' 패널(#today)이 없다")
        elif t.get("display") == "none" or t.get("visibility") == "hidden" or not t.get("w") or not t.get("h"):
            out.append(f"{tag} '오늘' 패널이 안 보인다({t})")
        elif not t.get("cards"):
            out.append(f"{tag} '오늘' 패널이 비었다(#t-grid 카드 0)")
    return out


def _chromium_path() -> "str | None":
    env = os.environ.get("SF_CHROMIUM")
    if env and os.path.exists(env):
        return env
    cands = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")))
    return cands[-1] if cands else None


class NoBrowser(RuntimeError):
    """Chromium 을 못 띄웠다 — 페이지 결함이 아니라 환경 사정이다(종료코드 2)."""


def launch(p):
    """Playwright 기본 Chromium → 안 되면 :func:`_chromium_path`. 둘 다 안 되면 :class:`NoBrowser`."""
    try:
        return p.chromium.launch()
    except Exception as e:
        exe = _chromium_path()
        if not exe:
            raise NoBrowser(f"Chromium 이 없다 — python -m playwright install chromium ({type(e).__name__})") from e
        try:
            return p.chromium.launch(executable_path=exe)
        except Exception as e2:
            raise NoBrowser(f"Chromium({exe})을 못 띄웠다 ({type(e2).__name__})") from e2


def smoke(folder: str, widths=WIDTHS, shots: "str | None" = None) -> list[str]:
    """``folder`` 의 두 페이지를 폭마다 열어 :func:`judge` 의 문제를 모은다. Playwright 가 없으면 ImportError."""
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    with sync_playwright() as p:
        browser = launch(p)
        try:
            for page_name in PAGES:
                path = Path(folder) / page_name
                if not path.is_file():
                    problems.append(f"{page_name} 이 없다({path})")
                    continue
                for w in widths:
                    ctx = browser.new_context(viewport={"width": w, "height": 900 if w > 600 else 844})
                    ctx.route("http*://**", lambda r: r.fulfill(status=200, body="", content_type="text/css"))
                    pg = ctx.new_page()
                    console, errors = [], []
                    pg.on("console", lambda m, c=console: c.append(m.text) if m.type == "error" else None)
                    pg.on("pageerror", lambda e, x=errors: x.append(str(e)))
                    pg.goto(path.resolve().as_uri(), wait_until="load")
                    pg.wait_for_timeout(200)
                    facts = dict(pg.evaluate(_MEASURE), console=console, errors=errors)
                    problems += judge(page_name, w, facts)
                    if shots:
                        os.makedirs(shots, exist_ok=True)
                        pg.screenshot(path=os.path.join(shots, f"{page_name.split('.')[0]}-{w}.png"))
                    ctx.close()
        finally:
            browser.close()
    return problems


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, help="리포트 폴더(numbers.html·viewer.html)")
    ap.add_argument("--shots", default=None, help="캡처를 남길 폴더(페이지-폭.png)")
    a = ap.parse_args(argv)
    try:
        problems = smoke(a.dir, shots=a.shots)
    except ImportError:
        print("Playwright 가 없다 — uv run --with playwright python scripts/web_smoke.py …", file=sys.stderr)
        return 2
    except NoBrowser as e:
        print(str(e), file=sys.stderr)
        return 2
    for ln in problems:
        print(f"⚠️ {ln}")
    print(f"웹 스모크: {len(PAGES)}쪽 × 폭 {'·'.join(map(str, WIDTHS))} — 문제 {len(problems)}건")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
