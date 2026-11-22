#!/usr/bin/env python3
"""웹 스모크 — 진짜 Chromium(Playwright)으로 리포트 쪽들을 폭 1440·390 에서 열어 본다.

numbers.html·viewer.html 은 늘, 검증·품질(trust.html)·수급 지도(map.html)는 폴더에 있으면, 기준일 목록은 폴더 이름이 기준일이면(부모가 루트) 본다.
검증기 W 층은 node 최소 DOM 으로 **숫자**를 맞춘다 — 브라우저 오류와 레이아웃은 못 본다. 여기서는 넷만 본다.

* 콘솔 오류·페이지 예외가 없는가.
* 문서가 가로로 넘치지 않는가 — ``documentElement.scrollWidth > innerWidth``. 본표(``.tw``)는 제 안에서 옆으로 미는
  것이 의도라 문서 폭만 본다(폰 390 에서 페이지 전체가 밀리면 결함이다).
* numbers.html 맨 위 '오늘 한 장'(``#oneday``)이 보이고 요약 문장이 있는가(예전 '오늘' 패널은 그 아래 접힌 세부 숫자다).
* numbers.html 이 접힌 채 :data:`MAX_SCREENS` 화면 안인가 — 요약을 얹고도 길어져(1440폭 4.8화면) 섹터 표·설명을 접었다.
* 합성 데모 폴더면 쪽 안의 상대 링크가 다 열리는가 — 면책 문구의 근거 링크가 404 였다.
* 수급 지도(map.html)는 폭 1440·600·390 과 카드 배치 둘(``?card=tall`` 1080×1350·``?card=wide`` 1200×675)에서 — 노드 라벨끼리
  겹치지 않고(글꼴이 없거나 좁은 폭에서 글자가 포개지면 읽을 수 없다) 노드가 그림 밖으로 나가 잘리지 않는가. 두 번째 보기
  '시간 흐름'(``#m0-w1-ts``)은 1440·390 에서 띠가 그려지는가.

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
import importlib.util
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

#: 볼 폭 — 데스크톱과 폰(README 의 웹 캡처와 같은 폭).
WIDTHS = (1440, 390)
PAGES = ("numbers.html", "viewer.html")
#: 폴더에 있을 때만 여는 쪽.
OPTIONAL_PAGES = ("trust.html", "map.html")
#: 수급 지도 — 폭 하나를 더 본다(600 은 좁은 배치로 넘어가는 문턱 바로 위·아래가 갈리는 폭).
MAP_PAGE = "map.html"
MAP_WIDTHS = (1440, 600, 390)
#: 지도 카드 배치 — (``?card=`` 값, 폭, 높이). ``scripts/map_card.py`` 가 찍는 크기와 같다.
MAP_CARDS = (("tall", 1080, 1350), ("wide", 1200, 675))
#: 지도 두 번째 보기(시간 흐름)의 주소 — 폰 폭에서 띠가 그려지고 가로로 넘치지 않는가.
MAP_STREAM = "#m0-w1-ts"
#: 라벨 겹침 여유[px] — 글자 상자의 소수 픽셀 반올림.
OVERLAP_SLACK = 0.5
#: 기준일 목록의 표시 이름 — 파일이 아니라 ``serve_reports.index_html`` 이 만든다.
LIST_PAGE = "index.html"
#: 이 파일이 있으면 합성 데모 폴더다(``sectorflow.demo.MARKER``) — 그 안에서 링크가 닫혀야 한다.
DEMO_MARKER = "DEMO.txt"
#: 넘침 여유[px] — 소수 픽셀 반올림.
OVERFLOW_SLACK = 1
#: numbers.html 길이 상한(문서 높이 ÷ 창 높이, 접힌 칸은 닫힌 채) — 폭별. 2026-11-13 실데이터 2.86·5.33, 데모 2.38·4.66.
#: 넘으면 접은 칸이 열렸거나 첫 화면('오늘 한 장')이 길어진 것이다.
MAX_SCREENS = {1440: 3.0, 390: 6.0}

_MEASURE = """() => {
  const t = document.getElementById("oneday");
  let today = null;
  if (t) { const r = t.getBoundingClientRect(), cs = getComputedStyle(t);
    today = {w: r.width, h: r.height, top: r.top + window.scrollY, display: cs.display, visibility: cs.visibility,
             cards: document.querySelectorAll("#a-sum li").length}; }
  const links = [...document.querySelectorAll("a[href]")].map(a => a.getAttribute("href"));
  return {scroll_w: document.documentElement.scrollWidth, inner_w: window.innerWidth,
          scroll_h: document.documentElement.scrollHeight, inner_h: window.innerHeight, today, links};
}"""


#: 지도 — 서로 다른 노드의 라벨(``#svg .node text``) 상자끼리의 겹침과, 그림(svg) 밖으로 나간 노드 상자. 띠·입자는 보지 않는다.
_MAP_MEASURE = """() => {
  const svg = document.getElementById("svg");
  if (!svg) return {map: null};
  const b = svg.getBoundingClientRect(), S = OVERLAP;
  const boxes = [...svg.querySelectorAll(".node text")].map(t => ({r: t.getBoundingClientRect(), s: t.textContent, n: t.parentNode}))
    .filter(x => x.r.width > 0 && x.r.height > 0);
  const over = [];
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    if (boxes[i].n === boxes[j].n) continue;   // 한 노드의 두 줄(이름·금액)은 글자 상자(em)가 1~2px 겹치게 놓였다 — 글자는 안 겹친다
    const p = boxes[i].r, q = boxes[j].r;
    if (Math.min(p.right, q.right) - Math.max(p.left, q.left) > S && Math.min(p.bottom, q.bottom) - Math.max(p.top, q.top) > S)
      over.push(boxes[i].s + " / " + boxes[j].s);
  }
  const outside = [...svg.querySelectorAll(".node rect:first-child")].filter(r => { const q = r.getBoundingClientRect();
    return q.top < b.top - 1 || q.bottom > b.bottom + 1 || q.left < b.left - 1 || q.right > b.right + 1; }).length;
  return {map: {nodes: svg.querySelectorAll(".node").length, labels: boxes.length, overlaps: over, outside,
                stream: document.querySelectorAll("#st-svg .band").length}};
}""".replace("OVERLAP", str(OVERLAP_SLACK))


def judge(page: str, width: int, facts: dict) -> list[str]:
    """한 페이지·한 폭의 잰 값 → 문제 목록(빈 목록이면 통과). 브라우저 없이 시험한다."""
    tag = f"{page}@{width}"
    out = [f"{tag} 콘솔 오류: {m}" for m in facts.get("console") or []]
    out += [f"{tag} 페이지 예외: {m}" for m in facts.get("errors") or []]
    if facts.get("scroll_w", 0) > facts.get("inner_w", 0) + OVERFLOW_SLACK:
        out.append(f"{tag} 가로 넘침: 문서 폭 {facts['scroll_w']}px > 창 {facts['inner_w']}px")
    out += [f"{tag} 깨진 링크: {x}" for x in facts.get("dead_links") or []]
    if page.startswith(MAP_PAGE) and page.endswith(MAP_STREAM):
        if not (facts.get("map") or {}).get("stream"):
            out.append(f"{tag} 시간 흐름에 띠가 없다(빈 그림)")
    elif page.startswith(MAP_PAGE):
        m = facts.get("map")
        if not m or not m.get("nodes"):
            out.append(f"{tag} 지도에 노드가 없다(빈 그림)")
        else:
            out += [f"{tag} 노드 라벨 겹침: {x}" for x in m.get("overlaps") or []]
            if m.get("outside"):
                out.append(f"{tag} 노드 {m['outside']}개가 그림 밖으로 나가 잘린다")
    if page == "numbers.html":
        t = facts.get("today")
        if not t:
            out.append(f"{tag} '오늘 한 장'(#oneday)이 없다")
        elif t.get("display") == "none" or t.get("visibility") == "hidden" or not t.get("w") or not t.get("h"):
            out.append(f"{tag} '오늘 한 장' 이 안 보인다({t})")
        elif not t.get("cards"):
            out.append(f"{tag} '오늘 한 장' 이 비었다(요약 문장 0)")
        cap = MAX_SCREENS.get(width)
        if cap and facts.get("scroll_h") and facts.get("inner_h") and facts["scroll_h"] / facts["inner_h"] > cap:
            out.append(f"{tag} 길이 {facts['scroll_h'] / facts['inner_h']:.2f}화면 > 상한 {cap} — "
                       "접힌 칸(세부 숫자·섹터 표)이 열렸거나 '오늘 한 장' 이 길어졌다")
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


def dead_links(page: Path, links: "list[str] | None") -> list[str]:
    """쪽 안의 상대 링크 중 디스크에 없는 것 — 외부·절대(서버 경로)·같은 쪽 안 링크는 안 본다."""
    out = []
    for href in links or []:
        u = urlsplit(href)
        if u.scheme or u.netloc or not u.path or u.path.startswith("/"):
            continue
        if not (page.parent / unquote(u.path)).exists():
            out.append(href)
    return out


def _list_page(folder: str, out_dir: str) -> "Path | None":
    """폴더 이름이 기준일이면 그 부모를 루트로 기준일 목록을 만들어 준다 — 서버 없이 같은 HTML 을 본다."""
    path = Path(folder).resolve()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name):
        return None
    spec = importlib.util.spec_from_file_location("_smoke_serve_reports", Path(__file__).with_name("serve_reports.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    page = Path(out_dir) / LIST_PAGE
    page.write_text(mod.index_html(str(path.parent)), "utf-8")
    return page


def smoke(folder: str, widths=WIDTHS, shots: "str | None" = None) -> list[str]:
    """``folder`` 의 쪽들을 폭마다 열어 :func:`judge` 의 문제를 모은다. Playwright 가 없으면 ImportError."""
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as tmp:
        return _smoke(sync_playwright, folder, widths, shots, _list_page(folder, tmp))


def _views(page_name: str, widths) -> list[tuple[str, int, int, str]]:
    """한 쪽을 열 (판정 이름, 폭, 높이, 주소 뒤 질의) — 지도는 :data:`MAP_WIDTHS`·카드 배치 둘·시간 흐름(1440·390)을 본다."""
    if page_name != MAP_PAGE:
        return [(page_name, w, 900 if w > 600 else 844, "") for w in widths]
    return ([(page_name, w, 900 if w > 600 else 844, "") for w in MAP_WIDTHS]
            + [(f"{page_name}?card={c}", w, h, f"?card={c}") for c, w, h in MAP_CARDS]
            + [(f"{page_name}{MAP_STREAM}", w, 900 if w > 600 else 844, MAP_STREAM) for w in (1440, 390)])


def _smoke(sync_playwright, folder: str, widths, shots: "str | None", list_page: "Path | None") -> list[str]:
    problems: list[str] = []
    is_demo = (Path(folder) / DEMO_MARKER).exists()
    targets = [(n, Path(folder) / n, True) for n in PAGES]
    targets += [(n, Path(folder) / n, False) for n in OPTIONAL_PAGES if (Path(folder) / n).is_file()]
    if list_page:
        targets.append((LIST_PAGE, list_page, False))
    with sync_playwright() as p:
        browser = launch(p)
        try:
            for page_name, path, required in targets:
                if required and not path.is_file():
                    problems.append(f"{page_name} 이 없다({path})")
                    continue
                for name, w, h, query in _views(page_name, widths):
                    ctx = browser.new_context(viewport={"width": w, "height": h})
                    ctx.route("http*://**", lambda r: r.fulfill(status=200, body="", content_type="text/css"))
                    pg = ctx.new_page()
                    console, errors = [], []
                    pg.on("console", lambda m, c=console: c.append(m.text) if m.type == "error" else None)
                    pg.on("pageerror", lambda e, x=errors: x.append(str(e)))
                    pg.goto(path.resolve().as_uri() + query, wait_until="load")
                    pg.wait_for_timeout(200)
                    facts = dict(pg.evaluate(_MEASURE), console=console, errors=errors)
                    if page_name == MAP_PAGE:
                        facts.update(pg.evaluate(_MAP_MEASURE))
                    if is_demo:
                        facts["dead_links"] = dead_links(path, facts.get("links"))
                    problems += judge(name, w, facts)
                    if shots:
                        os.makedirs(shots, exist_ok=True)
                        tail = query.replace("?card=", "-card-").replace(MAP_STREAM, "-stream")
                        pg.screenshot(path=os.path.join(shots, f"{name.split('.')[0]}{tail}-{w}.png"))
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
    print(f"웹 스모크: 폭 {'·'.join(map(str, WIDTHS))}(지도 {'·'.join(map(str, MAP_WIDTHS))}·카드 {len(MAP_CARDS)}) — 문제 {len(problems)}건")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
