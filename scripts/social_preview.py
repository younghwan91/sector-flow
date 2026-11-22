#!/usr/bin/env python3
"""GitHub 소셜 미리보기(1280×640)를 **합성 데모** 수급 지도로 만든다 — 실데이터 그림은 공개 레포에 싣지 않는다.

    uv run --with playwright --with pillow python scripts/social_preview.py     # → docs/brand/social-preview.png + 원장 sha

왼쪽은 워드마크(``docs/brand/wordmark-dark.svg``)와 가이드의 한 줄 소개, 오른쪽은 데모 지도의 그림 칸이다.
``[합성 데모]`` 표지가 그림 위와 글 아래 두 곳에 보인다. 업로드는 레포 소유자가 웹 UI(Settings → Social preview)에서 한다.
``demo_shots.py`` 처럼 감시 경로가 커밋된 뒤에만 찍고 ``docs/SCREENSHOTS.md`` 의 이 그림 줄을 HEAD 로 고친다.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from sectorflow.basis import DEMO_MARK  # noqa: E402

GUIDE = REPO / "docs" / "brand" / "README.md"
WORDMARK = REPO / "docs" / "brand" / "wordmark-dark.svg"
OUT = REPO / "docs" / "brand" / "social-preview.png"
MANIFEST = REPO / "docs" / "SCREENSHOTS.md"
#: 원장의 이 그림 줄 — | docs/brand/social-preview.png | 감시 경로 | sha |. demo_shots.py 의 줄(docs/images)과 겹치지 않는다.
ROW = re.compile(r"^(\|\s*docs/brand/social-preview\.png\s*\|\s*(\S+?)\s*\|\s*)[0-9a-f]{40}(\s*\|)$", re.M)
W, H = 1280, 640
_TAGLINE = re.compile(r"<!-- brand:tagline -->\s*>\s*\*\*[^*]+\*\*\s*—\s*(.+?)\s*<!-- /brand:tagline -->", re.S)


def tagline(guide: str) -> tuple[str, str, str]:
    """가이드의 한 줄 소개 → (첫 마디, 둘째 마디, 맺음). 문구는 가이드가 정본이라 여기서 다시 쓰지 않는다."""
    m = _TAGLINE.search(guide)
    if not m:
        raise SystemExit("docs/brand/README.md 에서 한 줄 소개(<!-- brand:tagline -->)를 못 찾았다")
    text = " ".join(m.group(1).split())
    body, _, tail = text.partition(". ")
    head, _, rest = body.partition(", ")
    return head, rest + ".", tail          # 첫 마디는 제목 줄이라 쉼표를 뗀다


def card_html(guide: str, wordmark_svg: str, figure_png: bytes) -> str:
    """미리보기 한 장의 HTML — 외부 요청 없음(그림은 data URI, 글꼴은 설치된 것 또는 시스템 글꼴)."""
    head, rest, tail = (escape(s) for s in tagline(guide))
    fig = base64.b64encode(figure_png).decode()
    svg = wordmark_svg.strip().replace('width="395" height="48"', 'class="wm"')
    notice = escape(DEMO_MARK)
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><style>
:root{{--bg:#0a0d16;--ink:#eef1f8;--muted:#8b93a8;--rule:#262c3b}}
*{{box-sizing:border-box;margin:0}}
body{{width:{W}px;height:{H}px;overflow:hidden;color:var(--ink);font-family:"Noto Sans KR",system-ui,sans-serif;
  background:radial-gradient(900px 500px at 70% -20%,#14203a 0%,var(--bg) 65%),var(--bg);display:flex}}
.l{{width:500px;flex:none;padding:60px 24px 52px 60px;display:flex;flex-direction:column}}
.wm{{height:40px;width:329px;flex:none}}
h1{{font:900 38px/1.34 "Noto Serif KR",serif;margin-top:56px;letter-spacing:-.01em;word-break:keep-all}}
p{{font-size:21px;line-height:1.5;margin-top:18px;word-break:keep-all}}
.m{{color:var(--muted);font-size:17px;margin-top:10px}}
.foot{{margin-top:auto;display:flex;gap:12px;align-items:center;color:var(--muted);font-size:15px}}
.chip{{border:1.5px solid #bd871c;color:#f3b44b;border-radius:999px;padding:3px 12px;font-weight:700;white-space:nowrap}}
.r{{flex:1;position:relative;padding:44px 44px 36px 0;display:flex;align-items:center}}
.r img{{width:100%;height:100%;object-fit:contain;object-position:right center}}
.r .chip{{position:absolute;top:18px;right:44px;background:var(--bg)}}
</style></head><body>
<div class="l">{svg}<h1>{head}</h1><p>{rest}</p><p class="m">{tail}</p>
<div class="foot"><span class="chip">{notice}</span><span>그림의 섹터·숫자는 전부 난수</span></div></div>
<div class="r"><img alt="" src="data:image/png;base64,{fig}"><span class="chip">{notice}</span></div>
</body></html>"""


def _sector_map():
    spec = importlib.util.spec_from_file_location("_sp_sector_map", REPO / "scripts" / "sector_map.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _launch(p):
    spec = importlib.util.spec_from_file_location("_sp_web_smoke", REPO / "scripts" / "web_smoke.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.launch(p)


def shoot(report: Path, out: Path) -> None:
    if not (report / "DEMO.txt").exists():
        raise SystemExit(f"{report} 는 합성 데모 폴더가 아니다(DEMO.txt 없음) — 실데이터로 공개 그림을 만들지 않는다")
    from playwright.sync_api import sync_playwright  # noqa: PLC0415 — 그림을 찍을 때만
    from PIL import Image  # noqa: PLC0415
    sm = _sector_map()
    payload = json.loads((report / "payload.json").read_text("utf-8"))
    tmp = Path(tempfile.mkdtemp(prefix="social."))
    try:
        page = tmp / "map.html"
        page.write_text(sm.render(sm.build_map_data(payload), Path(sm.TEMPLATE).read_text("utf-8")), "utf-8")
        with sync_playwright() as p:
            browser = _launch(p)
            ctx = browser.new_context(viewport={"width": 900, "height": 1000}, device_scale_factor=2,
                                      reduced_motion="reduce")
            ctx.route("http*://**", lambda r: r.fulfill(status=200, body="", content_type="text/css"))
            pg = ctx.new_page()
            pg.goto(page.as_uri(), wait_until="load")
            pg.wait_for_timeout(400)
            pg.add_style_tag(content="html,body{background:transparent!important}")
            fig = pg.locator("#fig").screenshot(omit_background=True)
            ctx.close()
            card = tmp / "card.html"
            card.write_text(card_html(GUIDE.read_text("utf-8"), WORDMARK.read_text("utf-8"), fig), "utf-8")
            ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1)
            ctx.route("http*://**", lambda r: r.abort())
            pg = ctx.new_page()
            pg.goto(card.as_uri(), wait_until="load")
            pg.wait_for_timeout(300)
            pg.screenshot(path=str(out))
            ctx.close()
            browser.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    Image.open(out).convert("RGB").save(out, optimize=True)   # 색을 줄이지 않는다 — 표지의 파랑이 바랜다(1MB 상한 안)
    size = Image.open(out).size
    if size != (W, H):
        raise SystemExit(f"{out}: {size} — {W}×{H} 이어야 한다")
    print(f"{out.relative_to(REPO) if out.is_relative_to(REPO) else out}: {size}, {out.stat().st_size:,} bytes")


def watched(text: str) -> list[str]:
    return sorted({m.group(2).rstrip("/") for m in ROW.finditer(text)})


def stamp_manifest(text: str, sha: str) -> str:
    """원장의 이 그림 줄만 ``sha`` 로 — 다른 그림의 줄은 그 그림을 찍은 도구가 고친다."""
    new, n = ROW.subn(lambda m: f"{m.group(1)}{sha}{m.group(3)}", text)
    if not n:
        raise SystemExit("원장에서 docs/brand/social-preview.png 줄을 못 읽었다 — docs/SCREENSHOTS.md 표 형식이 바뀌었나")
    return new


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True).stdout.strip()


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--demo-dir", help="이미 만든 합성 데모 폴더 — 없으면 임시로 만든다")
    ap.add_argument("--out", default=str(OUT), help=f"쓸 PNG (기본 {OUT.relative_to(REPO)})")
    a = ap.parse_args(argv)
    from sectorflow import demo  # noqa: PLC0415 — pandas 등이 데모를 지을 때만 필요하다

    out = Path(a.out).resolve()
    text = MANIFEST.read_text("utf-8")
    stamp = out == OUT
    if stamp and (dirty := _git("status", "--porcelain", "--", *watched(text))):
        print(f"감시 경로에 커밋 안 한 변경이 있다 — 그림이 HEAD 의 코드가 아니게 된다. 먼저 커밋하라:\n{dirty}", file=sys.stderr)
        return 2
    keep = tempfile.mkdtemp(prefix="sf-social.") if not a.demo_dir else None
    try:
        shoot(Path(a.demo_dir or demo.ensure(Path(keep) / "demo")), out)
        if stamp:                       # 다른 곳에 쓴 미리 보기는 원장을 건드리지 않는다
            MANIFEST.write_text(stamp_manifest(text, _git("rev-parse", "HEAD")), "utf-8")
            print(f"wrote {MANIFEST.relative_to(REPO)}")
    finally:
        if keep:
            shutil.rmtree(keep, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
