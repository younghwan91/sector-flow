#!/usr/bin/env python3
"""README 그림을 **합성 데모**로 다시 만든다 — 실종목·실숫자를 문서에 싣지 않는다.

    uv run --with playwright --with pillow --with imageio-ffmpeg python scripts/demo_shots.py   # docs/images 그림 + 원장 sha

터미널은 tmux 160×45 ``capture-pane -e`` → ANSI → HTML → Chromium(128색), 웹은 Chromium 1440·390(DPR 2, 64색 — 수급 지도는 128색).
수급 지도 타임랩스 GIF(``map-demo.gif``)는 ``scripts/map_video.py`` 로 — ffmpeg 가 없으면 아무것도 찍기 전에 멈춘다.
그림은 **HEAD 의 화면 코드**여야 뜻이 있다 — ``docs/SCREENSHOTS.md`` 의 감시 경로에 커밋 안 한 변경이 있으면 찍지 않는다.
원장 sha 를 HEAD 로 고쳐 쓰고 ``tests/test_screenshots_fresh.py`` 가 그 뒤의 화면 변경을 잡는다.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from html import escape
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


MANIFEST = REPO / "docs" / "SCREENSHOTS.md"
IMAGES = REPO / "docs" / "images"
#: README 머리의 타임랩스 GIF — 원장에 PNG 와 같이 적는다.
VIDEO_GIF = "map-demo.gif"
TERM_W, TERM_H = 160, 45
#: 터미널 그림 — (파일, 명령, 키, 끝 빈 줄을 지우나). 키는 tmux send-keys 인자.
TUI_SHOTS = (("kqflow-today.png", "sw-flow", (), True),
             ("kqflow-table.png", "sw-flow", ("x",), False),
             ("kqflow-names.png", "sw-flow", ("x", "Enter"), False),
             ("kqflow-combined.png", "sw-flow", ("x", "e", "w", "w", "w"), False),
             ("kqledger-table.png", "sw-ledger", ("x",), False))

BG, FG = "#181a21", "#e6e6e6"
#: xterm 기본 16색 — 다크 테마 값.
BASE16 = ("#181a21", "#e05561", "#8cc265", "#d18f52", "#4aa5f0", "#c162de", "#42b3c2", "#d7dae0",
          "#5c6370", "#ff616e", "#a5e075", "#f0a45d", "#4dc4ff", "#de73ff", "#4cd1e0", "#ffffff")
SGR = re.compile(r"\x1b\[([0-9;]*)m")
OTHER = re.compile(r"\x1b\[[0-9;?]*[A-Za-ln-z]|\x1b[()][B0]|\x0f|\x0e")


def xterm(n: int) -> str:
    """xterm-256 팔레트 번호 → 색. 16 은 앱의 배경색이라 페이지 배경과 같아야 상자가 안 생긴다."""
    if n == 16:
        return BG
    if n < 16:
        return BASE16[n]
    if n < 232:
        n -= 16
        rgb = (n // 36, (n % 36) // 6, n % 6)
        return "#%02x%02x%02x" % tuple(0 if v == 0 else 55 + v * 40 for v in rgb)
    v = 8 + (n - 232) * 10
    return "#%02x%02x%02x" % (v, v, v)


class Pen:
    """SGR 상태 — 줄을 넘어 이어진다."""

    def __init__(self) -> None:
        self.fg = self.bg = None
        self.bold = self.rev = False

    def css(self) -> str:
        fg, bg = self.fg or FG, self.bg or BG
        if self.rev:
            fg, bg = bg, fg
        return ";".join(([f"color:{fg}"] if fg != FG else []) + ([f"background:{bg}"] if bg != BG else [])
                        + (["font-weight:700"] if self.bold else []))

    def apply(self, params: str) -> None:
        ps = [int(x) if x else 0 for x in params.split(";")] if params else [0]
        i = 0
        while i < len(ps):
            p = ps[i]
            if p == 0:
                self.__init__()
            elif p == 1:
                self.bold = True
            elif p in (21, 22):
                self.bold = False
            elif p in (7, 27):
                self.rev = p == 7
            elif 30 <= p <= 37 or 90 <= p <= 97:
                self.fg = xterm(p - 30 if p < 40 else p - 82)
            elif p == 39:
                self.fg = None
            elif 40 <= p <= 47 or 100 <= p <= 107:
                self.bg = xterm(p - 40 if p < 50 else p - 92)
            elif p == 49:
                self.bg = None
            elif p in (38, 48) and i + 2 < len(ps) and ps[i + 1] == 5:
                setattr(self, "fg" if p == 38 else "bg", xterm(ps[i + 2]))
                i += 2
            elif p in (38, 48) and i + 4 < len(ps) and ps[i + 1] == 2:
                setattr(self, "fg" if p == 38 else "bg", "#%02x%02x%02x" % tuple(ps[i + 2:i + 5]))
                i += 4
            i += 1


def _chars(text: str) -> str:
    """비 ASCII 는 표시 폭만큼의 격자에 가둔다 — 표의 열이 어긋나지 않게."""
    out = []
    for ch in text:
        if ord(ch) < 128:
            out.append(escape(ch))
        else:
            w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
            out.append(f'<span class="g{w}">{escape(ch)}</span>')
    return "".join(out)


def _line_html(line: str, pen: Pen) -> str:
    spans, pos = [], 0

    def flush(seg: str) -> None:
        seg = OTHER.sub("", seg)
        if seg:
            css = pen.css()
            spans.append(f'<span style="{css}">{_chars(seg)}</span>' if css else _chars(seg))
    for m in SGR.finditer(line):
        flush(line[pos:m.start()])
        pen.apply(m.group(1))
        pos = m.end()
    flush(line[pos:])
    return "".join(spans) or "&nbsp;"


def ansi_html(raw: str, trim: bool = False) -> str:
    """``capture-pane -e`` 글 → 한 쪽 HTML."""
    lines = raw.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    while trim and lines and not SGR.sub("", OTHER.sub("", lines[-1])).strip():
        lines.pop()
    pen = Pen()
    rows = "".join(f'<div class="r">{_line_html(ln, pen)}</div>' for ln in lines)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:{BG}}} #t{{font:14px/1.3 "DejaVu Sans Mono",monospace;color:{FG};padding:2px 4px;display:inline-block}}
.r{{white-space:pre;height:1.3em}} .g1,.g2{{display:inline-block;text-align:center;overflow:hidden;vertical-align:bottom;height:1.3em;
font-family:"Noto Sans KR","DejaVu Sans Mono",monospace}} .g1{{width:1ch;font-family:"DejaVu Sans Mono",monospace}} .g2{{width:2ch}}
</style></head><body><div id="t">{rows}</div></body></html>"""


# ── 그림 ──────────────────────────────────────────────────────────────────────────────

def _tmux(*args: str) -> str:
    return subprocess.run(["tmux", "-L", f"sf-shots-{os.getpid()}", "-u", *args], check=True, capture_output=True,
                          text=True, timeout=30).stdout


def capture(cmd: list[str], keys: tuple) -> str:
    """별도 tmux 서버에서 화면을 띄워 키를 보내고 ANSI 를 뜬다 — 사용자의 tmux 를 건드리지 않는다."""
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "KR_QUANT_DB")}
    shell = " ".join(["env", "TERM=xterm-256color", "LANG=C.UTF-8", *cmd, ";", "sleep", "30"])
    subprocess.run(["tmux", "-L", f"sf-shots-{os.getpid()}", "-u", "new-session", "-d", "-s", "s",
                    "-x", str(TERM_W), "-y", str(TERM_H), shell], check=True, env=env, timeout=30)
    try:
        for _ in range(100):
            if _tmux("capture-pane", "-p", "-t", "s").strip():
                break
            time.sleep(0.2)
        time.sleep(0.6)
        for key in keys:
            _tmux("send-keys", "-t", "s", key)
            time.sleep(0.5)
        return _tmux("capture-pane", "-e", "-p", "-t", "s")
    finally:
        subprocess.run(["tmux", "-L", f"sf-shots-{os.getpid()}", "kill-server"], capture_output=True, timeout=30)


#: 이보다 색을 줄이지 않는 그림 — 수급 지도는 64색이면 머리 워드마크의 파랑이 청록으로 바랜다(브랜드 색이 그림에서 갈린다).
MIN_COLORS = {"map-demo.png": 128}


def _quantize(path: Path, colors: int = 128) -> None:
    from PIL import Image  # noqa: PLC0415 — 그림을 찍을 때만

    colors = max(colors, MIN_COLORS.get(path.name, 0))
    Image.open(path).convert("RGB").quantize(colors=colors, method=Image.MEDIANCUT).save(path, optimize=True)
    print(f"{path.name}: {Image.open(path).size}, {path.stat().st_size:,} bytes")


def _web_smoke():
    spec = importlib.util.spec_from_file_location("_shots_web_smoke", REPO / "scripts" / "web_smoke.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def shoot(report: Path, out: Path) -> None:
    from playwright.sync_api import sync_playwright  # noqa: PLC0415 — 그림을 찍을 때만

    bin_dir = Path(sys.executable).parent
    tmp = Path(tempfile.mkdtemp(prefix="shots."))
    try:
        ansi = {name: (capture([str(bin_dir / exe), "--dir", str(report)], keys), trim)
                for name, exe, keys, trim in TUI_SHOTS}
        with sync_playwright() as p:
            browser = _web_smoke().launch(p)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            for name, (raw, trim) in ansi.items():
                html_path = tmp / f"{name}.html"
                html_path.write_text(ansi_html(raw, trim), "utf-8")
                page.goto(html_path.as_uri())
                page.wait_for_timeout(200)
                page.locator("#t").screenshot(path=str(out / name))
                _quantize(out / name)
            page.close()
            numbers = (report / "numbers.html").as_uri()
            # 표지 [합성 데모] 는 쪽 맨 위 띠다 — 맨 위부터 찍어 어느 그림에도 보이게. '오늘 한 장' 을 데스크톱은 섹터×주체 칸까지,
            # 폰은 요약 문장과 시장 4주체까지.
            for name, w, h, dpr, sel in (("web-today.png", 1440, 900, 1, "#a-heat"),
                                         ("web-today-mobile.png", 390, 844, 2, "#a-mkt")):
                ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=dpr)
                ctx.route("http*://**", lambda r: r.fulfill(status=200, body="", content_type="text/css"))
                pg = ctx.new_page()
                pg.goto(numbers, wait_until="load")
                pg.wait_for_timeout(300)
                pg.evaluate("document.querySelector('a.totop')?.remove()")
                bottom = pg.evaluate(f"(()=>{{const r=document.querySelector('{sel}').getBoundingClientRect();"
                                     "return Math.ceil(r.bottom+scrollY);})()")
                pg.screenshot(path=str(out / name), full_page=True, clip={"x": 0, "y": 0, "width": w, "height": bottom})
                _quantize(out / name, 64)       # 쪽이 커서 터미널보다 색을 더 줄인다 — 그림당 100KB 안
                ctx.close()
            # 수급 지도 — 데모 페이로드로 그 자리에서 짓는다(배치가 아직 map.html 을 만들지 않는다). 머리부터 그림 아래 끝까지.
            sm = importlib.util.spec_from_file_location("_shots_sector_map", REPO / "scripts" / "sector_map.py")
            smod = importlib.util.module_from_spec(sm)
            sm.loader.exec_module(smod)
            payload = json.loads((report / "payload.json").read_text("utf-8"))
            map_path = tmp / "map.html"
            map_path.write_text(smod.render(smod.build_map_data(payload), Path(smod.TEMPLATE).read_text("utf-8")), "utf-8")
            ctx = browser.new_context(viewport={"width": 1440, "height": 1100}, device_scale_factor=1,
                                      reduced_motion="reduce")
            ctx.route("http*://**", lambda r: r.fulfill(status=200, body="", content_type="text/css"))
            pg = ctx.new_page()
            pg.goto(map_path.as_uri(), wait_until="load")
            pg.wait_for_timeout(400)
            bottom = pg.evaluate("(()=>{const r=document.getElementById('fig').getBoundingClientRect();"
                                 "return Math.ceil(r.bottom+scrollY)+8;})()")
            pg.screenshot(path=str(out / "map-demo.png"), full_page=True, clip={"x": 0, "y": 0, "width": 1440, "height": bottom})
            _quantize(out / "map-demo.png", 64)
            ctx.close()
            browser.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _map_video():
    spec = importlib.util.spec_from_file_location("_shots_map_video", REPO / "scripts" / "map_video.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True).stdout.strip()


#: 원장 행 — | 이미지 | 감시 경로 | 커밋 |. 날짜 카드(``docs/images/days/``)는 ``scripts/day_cards.py`` 가 찍고 고친다.
ROW = re.compile(r"^(\|\s*docs/images/(?!days/)\S+?\.(?:png|gif)\s*\|\s*(\S+?)\s*\|\s*)[0-9a-f]{40}(\s*\|)$", re.M)


def watched(text: str) -> list[str]:
    return sorted({m.group(2).rstrip("/") for m in ROW.finditer(text)})


def stamp_manifest(text: str, sha: str) -> str:
    """원장의 모든 행을 ``sha`` 로 — 찍을 때 체크아웃한 커밋."""
    new, n = ROW.subn(lambda m: f"{m.group(1)}{sha}{m.group(3)}", text)
    if not n:
        raise SystemExit("원장 행을 하나도 못 읽었다 — docs/SCREENSHOTS.md 표 형식이 바뀌었나")
    return new


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--demo-dir", help="이미 만든 합성 데모 폴더 — 없으면 임시로 만든다")
    a = ap.parse_args(argv)
    from sectorflow import demo  # noqa: PLC0415 — pandas 등이 데모를 지을 때만 필요하다

    keep = tempfile.mkdtemp(prefix="sf-shots.") if not a.demo_dir else None
    try:
        report = Path(a.demo_dir or demo.ensure(Path(keep) / "demo"))
        text = MANIFEST.read_text("utf-8")
        dirty = _git("status", "--porcelain", "--", *watched(text))
        if dirty:
            print(f"감시 경로에 커밋 안 한 변경이 있다 — 그림이 HEAD 의 화면이 아니게 된다. 먼저 커밋하라:\n{dirty}", file=sys.stderr)
            return 2
        video = _map_video()
        try:
            ffmpeg = video.find_ffmpeg([VIDEO_GIF])
        except video.VideoError as e:
            print(f"{VIDEO_GIF} 를 못 만든다 — 아무것도 찍지 않았다(원장이 반만 맞게 되지 않게).\n{e}", file=sys.stderr)
            return 2
        shoot(report, IMAGES)
        video.make(report, ffmpeg, gif=str(IMAGES / VIDEO_GIF))
        print(f"{VIDEO_GIF}: {(IMAGES / VIDEO_GIF).stat().st_size:,} bytes")
        MANIFEST.write_text(stamp_manifest(text, _git("rev-parse", "HEAD")), "utf-8")
        print(f"wrote {MANIFEST.relative_to(REPO)}")
        return 0
    finally:
        if keep:
            shutil.rmtree(keep, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
