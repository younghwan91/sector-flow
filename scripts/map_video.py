#!/usr/bin/env python3
"""수급 지도 타임랩스를 영상으로 — 리포트 폴더의 ``map.html`` 을 Chromium 에서 재생해 MP4·GIF 로 뽑는다.

    uv run --with playwright --with imageio-ffmpeg python scripts/map_video.py --dir <리포트 폴더> --out map.mp4 --gif map.gif

배치는 영상을 만들지 않는다(무겁다, 설계 §3.3). 데모 폴더(``sector-flow-demo``)로 README·랜딩의 그림을 만들고,
자기 리포트 폴더도 받지만 **출력은 그 기기에만 남는다** — 올리거나 보내는 길은 없다. 외부 요청(글꼴 등)은 막는다.

화면 녹화(실시간)가 아니라 Playwright 의 가짜 시계(``page.clock``)로 한 프레임씩 시간을 밀고 찍는다 —
기계가 바빠도 프레임이 빠지지 않고, 입자 난수도 씨앗 고정이라 같은 폴더면 같은 영상이다.
재생은 지도 페이지의 타임랩스 단추 그대로(하루 700ms)이고, ``--seconds`` 에 들어가는 만큼 **기준일로 끝나는** 마지막 날들을 보인다.

ffmpeg 는 ``--ffmpeg`` → ``$FFMPEG`` → PATH 의 ``ffmpeg`` → ``imageio-ffmpeg`` 패키지 → Playwright 가 받아 둔
``~/.cache/ms-playwright/ffmpeg-*`` 순으로 찾고, 쓸 인코더(MP4 는 libx264, GIF 는 gif·palettegen)가 있는 첫 것을 쓴다.
Playwright 의 ffmpeg 는 녹화용 축소판이라 대개 둘 다 없다. 못 찾으면 고치는 법을 적고 2 로 끝난다.

종료코드: 0 만듦, 2 인자·ffmpeg·Chromium 문제(아무것도 안 씀). 다른 스크립트는 :func:`find_ffmpeg` → :func:`make` 로 부른다(``demo_shots.py``).
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

#: 지도 페이지 타임랩스의 하루 간격(ms) — ``templates/sector_map.html`` 의 ``setInterval(…, 700)``.
DAY_MS = 700
FPS = 30
GIF_FPS = 15
GIF_WIDTH = 960
GIF_COLORS = 128
#: 영상 길이 범위(초) — 짧으면 하루도 못 넘기고, 길면 프레임 PNG 가 디스크를 먹는다.
MIN_SECONDS, MAX_SECONDS = 2.0, 60.0
#: 렌더 배율 — 2배로 찍어 줄이면 글자 가장자리가 깨끗하다.
SCALE = 2
#: 입자 난수 씨앗 — 같은 폴더면 같은 영상.
SEED = 20261030
#: 가짜 시계를 멈춰 세우는 시각(ms, 설치 시각 0 기준) — 쪽을 여는 데 걸리는 실제 시간보다 넉넉히 뒤.
PAUSE_AT_MS = 60_000
#: 그림 아래 여백(px) — 그림 바로 밑에서 자른다(표·각주는 영상에 넣지 않는다).
BOTTOM_PAD = 12

INSTALL_HINT = ("ffmpeg 를 못 찾았다{why}. 다음 중 하나로 고친다:\n"
                "  · uv run --with playwright --with imageio-ffmpeg python scripts/map_video.py …   (정적 ffmpeg 를 패키지로)\n"
                "  · sudo apt install ffmpeg   (또는 brew install ffmpeg)\n"
                "  · --ffmpeg <경로> 또는 FFMPEG=<경로>\n"
                "Playwright 가 받아 둔 ffmpeg(~/.cache/ms-playwright/ffmpeg-*)는 녹화용 축소판이라 MP4·GIF 인코더가 없다.")


class VideoError(Exception):
    """영상을 못 만든다(ffmpeg·Chromium·프레임 없음) — 메시지에 까닭과 고치는 법이 있다. 아무것도 쓰지 않았다."""


class NoFFmpeg(VideoError):
    """쓸 수 있는 ffmpeg 가 없다."""


def need(outputs: "list[str]") -> set[str]:
    """출력 파일 확장자 → 있어야 할 ffmpeg 인코더·필터 이름."""
    out: set[str] = set()
    for p in outputs:
        out |= {"libx264"} if p.lower().endswith(".mp4") else {"gif", "palettegen", "paletteuse"}
    return out


def capabilities(exe: str) -> set[str]:
    """ffmpeg 가 가진 인코더·필터 이름. 실행이 안 되면 빈 집합."""
    names: set[str] = set()
    for what in ("-encoders", "-filters"):
        try:
            r = subprocess.run([exe, "-hide_banner", what], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return set()
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and not line.startswith(" ="):
                names.add(parts[1])
    return names


def candidates(explicit: "str | None" = None) -> list[str]:
    """ffmpeg 후보 — 찾는 순서대로(없는 것은 뺀다)."""
    out = [explicit, os.environ.get("FFMPEG"), shutil.which("ffmpeg")]
    try:
        import imageio_ffmpeg  # noqa: PLC0415 — 있으면 쓴다

        out.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:  # noqa: BLE001 — 패키지가 없거나 바이너리를 못 받았다
        pass
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.expanduser("~/.cache/ms-playwright")
    out += sorted(glob.glob(os.path.join(root, "ffmpeg-*", "ffmpeg-*")), reverse=True)
    seen, keep = set(), []
    for c in out:
        if c and c not in seen and os.path.isfile(c) and os.access(c, os.X_OK):
            seen.add(c)
            keep.append(c)
    return keep


def find_ffmpeg(outputs: "list[str]", explicit: "str | None" = None) -> str:
    """쓸 인코더를 다 가진 첫 ffmpeg. 없으면 :class:`NoFFmpeg`(고치는 법 포함)."""
    want = need(outputs)
    if explicit and not (os.path.isfile(explicit) and os.access(explicit, os.X_OK)):
        raise NoFFmpeg(INSTALL_HINT.format(why=f" — --ffmpeg {explicit} 는 실행 파일이 아니다"))
    tried = []
    for exe in candidates(explicit):
        lack = want - capabilities(exe)
        if not lack:
            return exe
        tried.append(f"{exe}(없음: {', '.join(sorted(lack))})")
    raise NoFFmpeg(INSTALL_HINT.format(why=" — 살펴본 것: " + "; ".join(tried) if tried else ""))


def check_args(a: argparse.Namespace) -> list[str]:
    """인자 문제 목록(빈 목록이면 통과). 브라우저·ffmpeg 를 띄우기 전에 다 본다."""
    bad = []
    d = Path(a.dir)
    if not d.is_dir():
        bad.append(f"--dir {a.dir} 는 폴더가 아니다")
    elif not (d / "map.html").is_file() and not (d / "payload.json").is_file():
        bad.append(f"--dir {a.dir} 에 map.html 도 payload.json 도 없다 — 리포트 폴더(또는 sector-flow-demo --out 폴더)를 주어라")
    if not a.out and not a.gif:
        bad.append("--out(.mp4) 이나 --gif(.gif) 중 하나는 주어야 한다")
    if a.out and not a.out.lower().endswith(".mp4"):
        bad.append(f"--out {a.out} 는 .mp4 여야 한다")
    if a.gif and not a.gif.lower().endswith(".gif"):
        bad.append(f"--gif {a.gif} 는 .gif 여야 한다")
    if not MIN_SECONDS <= a.seconds <= MAX_SECONDS:
        bad.append(f"--seconds {a.seconds:g} 는 {MIN_SECONDS:g}~{MAX_SECONDS:g} 초여야 한다")
    if a.width < 390 or a.width > 1920:
        bad.append(f"--width {a.width} 는 390~1920 이어야 한다")
    return bad


def plan(n_frames: int, seconds: float) -> tuple[int, int]:
    """타임랩스 프레임 ``n_frames`` 개 중 영상에 넣을 (첫 날 번호, 날 수) — 기준일(마지막 날)로 끝난다."""
    days = max(1, min(n_frames, int(seconds * 1000 // DAY_MS)))
    return n_frames - days, days


def map_html(report: Path, tmp: Path) -> Path:
    """폴더의 ``map.html`` — 없으면(옛 폴더·데모) 같은 폴더의 ``payload.json`` 으로 그 자리에서 짓는다."""
    if (report / "map.html").is_file():
        return report / "map.html"
    spec = importlib.util.spec_from_file_location("_video_sector_map", REPO / "scripts" / "sector_map.py")
    sm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sm)
    payload = json.loads((report / "payload.json").read_text("utf-8"))
    out = tmp / "map.html"
    out.write_text(sm.render(sm.build_map_data(payload), Path(sm.TEMPLATE).read_text("utf-8")), "utf-8")
    return out


#: 입자 난수를 씨앗 고정으로(mulberry32) — 페이지 스크립트보다 먼저 돈다.
SEED_JS = """(() => { let s = %d >>> 0;
  Math.random = () => { s = (s + 0x6D2B79F5) >>> 0; let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; })();"""


def _web_smoke():
    spec = importlib.util.spec_from_file_location("_video_web_smoke", REPO / "scripts" / "web_smoke.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def record(page_path: Path, frames_dir: Path, *, seconds: float, width: int) -> tuple[int, int, int]:
    """가짜 시계로 타임랩스를 재생하며 PNG 를 찍는다 → (프레임 수, 폭, 높이)[CSS px]."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415 — 찍을 때만

    ws = _web_smoke()
    with sync_playwright() as p:
        try:
            browser = ws.launch(p)
        except ws.NoBrowser as e:
            raise VideoError(str(e)) from e
        ctx = browser.new_context(viewport={"width": width, "height": 900}, device_scale_factor=SCALE)
        # 외부 요청은 보내지 않는다 — 자기 리포트를 찍을 때도 기기 밖으로 아무것도 안 나간다. 글꼴은 시스템 것으로.
        ctx.route("http*://**", lambda r: r.fulfill(status=200, body="", content_type="text/css"))
        ctx.add_init_script(SEED_JS % SEED)
        page = ctx.new_page()
        page.clock.install(time=0)
        page.goto(page_path.as_uri(), wait_until="load")
        # 설치만 하면 가짜 시계도 실시간으로 흐른다 — 멈춰 두고 프레임마다 손으로 민다.
        page.clock.pause_at(PAUSE_AT_MS)
        page.clock.run_for(600)
        # 그림 아래 끝까지 보이게 창 높이를 맞춘다(머리글·숫자 띠·단추·그림).
        bottom = page.evaluate("Math.ceil(document.getElementById('fig').getBoundingClientRect().bottom + scrollY)")
        height = int(bottom) + BOTTOM_PAD
        height += height % 2
        page.set_viewport_size({"width": width, "height": height})
        page.clock.run_for(300)
        n = page.evaluate("D.timelapse[D.markets[0]].frames.length")
        if not n:
            raise VideoError("타임랩스 프레임이 없다 — 이 폴더의 날짜가 너무 짧다")
        start, _days = plan(n, seconds)
        page.evaluate(f"(() => {{ const r = document.getElementById('day'); r.value = {start};"
                      " r.dispatchEvent(new Event('input')); })()")
        page.clock.run_for(200)
        page.click("#play")
        total = int(round(seconds * FPS))
        for i in range(total):
            page.screenshot(path=str(frames_dir / f"f{i:05d}.png"), animations="allow")
            # 시계는 정수 ms 만 받는다 — 누적 목표로 밀어 33·33·34ms 처럼 오차가 쌓이지 않게.
            page.clock.run_for(round((i + 1) * 1000 / FPS) - round(i * 1000 / FPS))
        browser.close()
    return total, width, height


def encode(ffmpeg: str, frames_dir: Path, *, mp4: "str | None", gif: "str | None", width: int) -> None:
    """PNG 프레임 → MP4(H.264, 원래 폭)·GIF(공용 팔레트, 바뀐 사각형만)."""
    src = ["-framerate", str(FPS), "-i", str(frames_dir / "f%05d.png")]
    base = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if mp4:
        subprocess.run([*base, *src, "-vf", f"scale={width}:-2:flags=lanczos", "-c:v", "libx264", "-preset", "slow",
                        "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", mp4], check=True)
    if gif:
        gw = min(GIF_WIDTH, width)
        vf = (f"fps={GIF_FPS},scale={gw}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors={GIF_COLORS}:stats_mode=diff[p];"
              "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
        subprocess.run([*base, *src, "-vf", vf, "-loop", "0", gif], check=True)


def make(report: Path, ffmpeg: str, *, mp4: "str | None" = None, gif: "str | None" = None,
         seconds: float = 8.0, width: int = 1280) -> tuple[int, int, int]:
    """리포트 폴더 → 영상 파일. ``ffmpeg`` 는 :func:`find_ffmpeg` 로 미리 고른다(그림을 다 찍고 나서 실패하지 않게)."""
    tmp = Path(tempfile.mkdtemp(prefix="map-video."))
    try:
        frames = tmp / "frames"
        frames.mkdir()
        n, w, h = record(map_html(report, tmp), frames, seconds=seconds, width=width)
        for o in (mp4, gif):
            if o:
                Path(o).parent.mkdir(parents=True, exist_ok=True)
        encode(ffmpeg, frames, mp4=mp4, gif=gif, width=w)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return n, w, h


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, help="리포트 폴더(map.html 또는 payload.json) — 데모는 sector-flow-demo --out 폴더")
    ap.add_argument("--out", help="쓸 MP4(.mp4)")
    ap.add_argument("--gif", help="쓸 GIF(.gif) — README 용은 4MB 안")
    ap.add_argument("--seconds", type=float, default=8.0, help=f"영상 길이(초, 기본 8) — 하루 {DAY_MS}ms 라 8초면 11일")
    ap.add_argument("--width", type=int, default=1280, help="창 폭(CSS px, 기본 1280). 높이는 그림 아래 끝까지")
    ap.add_argument("--ffmpeg", help="ffmpeg 경로(기본: 찾는다)")
    a = ap.parse_args(argv)
    bad = check_args(a)
    if bad:
        print("map_video: " + "\n  ".join(bad), file=sys.stderr)
        return 2
    outputs = [x for x in (a.out, a.gif) if x]
    try:
        ffmpeg = find_ffmpeg(outputs, a.ffmpeg)
        n, w, h = make(Path(a.dir), ffmpeg, mp4=a.out, gif=a.gif, seconds=a.seconds, width=a.width)
    except VideoError as e:
        print(f"map_video: {e}", file=sys.stderr)
        return 2
    for o in outputs:
        print(f"wrote {o}  ({n} 프레임, {w}×{h}, {Path(o).stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
