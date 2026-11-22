"""수급 지도 영상(``scripts/map_video.py``) — 브라우저·ffmpeg 없이 보는 것: 인자 검사, ffmpeg 고르기, 재생 구간.

녹화 자체는 Chromium·ffmpeg 가 있어야 해 여기서 돌리지 않는다. 대신 그 산출물(``docs/images/map-demo.gif``)의
크기와 원장 등록을 본다 — 다시 찍는 길은 ``scripts/demo_shots.py`` 하나다.
"""
from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("_map_video", REPO / "scripts" / "map_video.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _report(tmp_path: Path) -> Path:
    d = tmp_path / "report"
    d.mkdir()
    (d / "payload.json").write_text("{}", "utf-8")
    return d


def _fake_ffmpeg(tmp_path: Path, names: "list[str]", label: str = "ffmpeg") -> str:
    """``-encoders``·``-filters`` 에 ``names`` 만 있다고 답하는 가짜 ffmpeg."""
    lines = " ".join(f"' V....D {n} fake'" for n in names)      # PATH 를 비워도 돌게 셸 내장 printf 만
    p = tmp_path / label
    p.write_text(f"#!/bin/sh\nprintf '%s\\n' ' ------' {lines}\n", "utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


def _no_ffmpeg_anywhere(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FFMPEG", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "no-playwright"))
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)       # import 가 ImportError 로


@pytest.mark.parametrize("dir_ok, argv, says", [
    (False, ["--out", "x.mp4"], "폴더가 아니다"),
    (True, [], "중 하나는 주어야 한다"),
    (True, ["--out", "x.webm"], ".mp4 여야 한다"),
    (True, ["--gif", "x.png"], ".gif 여야 한다"),
    (True, ["--out", "x.mp4", "--seconds", "0.5"], "초여야 한다"),
    (True, ["--out", "x.mp4", "--seconds", "600"], "초여야 한다"),
    (True, ["--out", "x.mp4", "--width", "4000"], "390~1920"),
], ids=["no-dir", "no-output", "mp4-ext", "gif-ext", "too-short", "too-long", "too-wide"])
def test_bad_arguments_exit_2_before_anything_runs(tmp_path, monkeypatch, capsys, dir_ok, argv, says):
    """인자 문제는 브라우저·ffmpeg 를 띄우기 전에 2 로 — 몇 분 찍은 뒤에 실패하지 않는다."""
    d = _report(tmp_path) if dir_ok else tmp_path / "missing"
    called = []
    monkeypatch.setattr(M, "find_ffmpeg", lambda *a, **k: called.append(a) or "ffmpeg")
    monkeypatch.setattr(M, "make", lambda *a, **k: called.append(a) or (1, 1, 1))
    assert M.main(["--dir", str(d), *argv]) == 2
    assert says in capsys.readouterr().err
    assert not called, "인자가 틀렸는데 ffmpeg·녹화로 갔다"


def test_folder_without_map_or_payload_is_refused(tmp_path, capsys):
    d = tmp_path / "empty"
    d.mkdir()
    assert M.main(["--dir", str(d), "--gif", str(tmp_path / "x.gif")]) == 2
    assert "map.html 도 payload.json 도 없다" in capsys.readouterr().err


def test_no_ffmpeg_gives_a_friendly_fix_and_writes_nothing(tmp_path, monkeypatch, capsys):
    """ffmpeg 가 어디에도 없으면 고치는 법(패키지·apt·경로)을 적고 2 — 트레이스백이 아니다."""
    _no_ffmpeg_anywhere(monkeypatch, tmp_path)
    out = tmp_path / "x.mp4"
    assert M.main(["--dir", str(_report(tmp_path)), "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "ffmpeg 를 못 찾았다" in err and "imageio-ffmpeg" in err and "apt install ffmpeg" in err and "--ffmpeg" in err
    assert "Traceback" not in err and not out.exists()


def test_ffmpeg_without_the_needed_encoder_is_skipped_and_named(tmp_path, monkeypatch, capsys):
    """Playwright 의 녹화용 ffmpeg 처럼 인코더가 빠진 것은 고르지 않고, 무엇이 없었는지 적는다."""
    _no_ffmpeg_anywhere(monkeypatch, tmp_path)
    monkeypatch.setenv("FFMPEG", _fake_ffmpeg(tmp_path, ["png", "libvpx-vp9", "scale"]))
    assert M.main(["--dir", str(_report(tmp_path)), "--out", str(tmp_path / "x.mp4")]) == 2
    err = capsys.readouterr().err
    assert "없음: libx264" in err


def test_first_capable_ffmpeg_wins_and_needs_follow_the_outputs(tmp_path, monkeypatch):
    _no_ffmpeg_anywhere(monkeypatch, tmp_path)
    gif_only = _fake_ffmpeg(tmp_path, ["gif", "palettegen", "paletteuse"], "gif-only")
    full = _fake_ffmpeg(tmp_path, ["gif", "palettegen", "paletteuse", "libx264"], "full")
    monkeypatch.setenv("FFMPEG", gif_only)
    assert M.find_ffmpeg(["a.gif"]) == gif_only
    assert M.find_ffmpeg(["a.gif"], full) == full                    # --ffmpeg 가 먼저
    with pytest.raises(M.NoFFmpeg, match="libx264"):
        M.find_ffmpeg(["a.mp4", "a.gif"])
    with pytest.raises(M.NoFFmpeg, match="실행 파일이 아니다"):
        M.find_ffmpeg(["a.gif"], str(tmp_path / "nope"))


def test_playwright_cached_ffmpeg_is_a_candidate(tmp_path, monkeypatch):
    """설계가 말한 자리(``~/.cache/ms-playwright/ffmpeg-*``)도 본다 — 인코더가 있으면 쓴다."""
    _no_ffmpeg_anywhere(monkeypatch, tmp_path)
    root = tmp_path / "pw"
    (root / "ffmpeg-1013").mkdir(parents=True)
    exe = _fake_ffmpeg(root / "ffmpeg-1013", ["gif", "palettegen", "paletteuse"], "ffmpeg-linux")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(root))
    assert M.find_ffmpeg(["a.gif"]) == exe


@pytest.mark.parametrize("n, seconds, want", [(60, 8, (49, 11)), (5, 8, (0, 5)), (60, 0.3, (59, 1))])
def test_clip_ends_on_the_base_date(n, seconds, want):
    """영상은 기준일(마지막 프레임)로 끝난다 — 처음 며칠이 아니라 최근 며칠을 보인다."""
    assert M.plan(n, seconds) == want
    start, days = M.plan(n, seconds)
    assert start + days == n


# mutation-exempt: 커밋된 산출물(GIF)의 크기·원장을 보는 검사다 — 주입할 소스가 없다(다시 찍는 코드는 demo_shots.py)
def test_readme_gif_is_small_and_in_the_ledger():
    """README 머리의 데모 GIF 는 4MB 안이고, 스크린샷 원장이 어느 코드로 찍었는지 적는다."""
    gif = REPO / "docs" / "images" / "map-demo.gif"
    assert gif.is_file() and gif.read_bytes()[:6] in (b"GIF89a", b"GIF87a")
    assert os.path.getsize(gif) <= 4 * 1024 * 1024, f"{gif.name} {os.path.getsize(gif) / 1e6:.1f}MB > 4MB"
    ledger = (REPO / "docs" / "SCREENSHOTS.md").read_text("utf-8")
    assert "| docs/images/map-demo.gif | scripts/map_video.py |" in ledger


# ── 녹화·인코딩의 순서 — 진짜 Chromium·ffmpeg 대신 부른 것을 적는 가짜로 본다(CI 러너엔 Playwright 가 없다) ─────────
def _payload() -> dict:
    f = lambda **k: {a: list(v) for a, v in k.items()}  # noqa: E731
    return {"dates": ["2026-01-05", "2026-01-06"], "sectors": ["가상가", "가상나"], "markets": ["거래소"],
            "flows": {"거래소": {"가상가": f(indiv=[-10, -20], forgn=[6, 12], inst=[4, 8], etc=[0, 0]),
                                "가상나": f(indiv=[3, 5], forgn=[-1, -2], inst=[-2, -3], etc=[0, 0])}}}


def test_folder_without_map_html_gets_one_built_from_payload(tmp_path):
    """옛 폴더·데모 폴더엔 map.html 이 없다 — 같은 폴더의 payload 로 그 자리에서 짓고, 원래 폴더엔 쓰지 않는다."""
    import json

    d = tmp_path / "r"
    d.mkdir()
    (d / "payload.json").write_text(json.dumps(_payload(), ensure_ascii=False), "utf-8")
    work = tmp_path / "w"
    work.mkdir()
    page = M.map_html(d, work)
    assert page.parent == work and "const D = {" in page.read_text("utf-8") and not (d / "map.html").exists()
    (d / "map.html").write_text("<html>mine</html>", "utf-8")
    assert M.map_html(d, work) == d / "map.html"                  # 있으면 그대로 쓴다


class _Clock:
    def __init__(self, log):
        self.log = log

    def install(self, time=None):
        self.log.append(("install", time))

    def pause_at(self, t):
        self.log.append(("pause_at", t))

    def run_for(self, ms):
        assert isinstance(ms, int), "Playwright 시계는 정수 ms 만 받는다"
        self.log.append(("run_for", ms))


class _Page:
    def __init__(self, log, n_frames):
        self.log, self.n = log, n_frames
        self.clock = _Clock(log)

    def goto(self, url, wait_until=None):
        self.log.append(("goto", url))

    def evaluate(self, js):
        self.log.append(("evaluate", js))
        return 901 if "getBoundingClientRect" in js else self.n if "frames.length" in js else None

    def set_viewport_size(self, size):
        self.log.append(("viewport", size["width"], size["height"]))

    def click(self, sel):
        self.log.append(("click", sel))

    def screenshot(self, path, animations=None):
        Path(path).write_bytes(b"png")
        self.log.append(("shot", Path(path).name))


class _Ctx:
    def __init__(self, log, n):
        self.log, self.n = log, n

    def route(self, pattern, handler):
        self.log.append(("route", pattern))

    def add_init_script(self, js):
        self.log.append(("init", js))

    def new_page(self):
        return _Page(self.log, self.n)


class _Browser:
    def __init__(self, log, n):
        self.log, self.n = log, n

    def new_context(self, **kw):
        self.log.append(("context", kw))
        return _Ctx(self.log, self.n)

    def close(self):
        self.log.append(("close",))


def _fake_playwright(monkeypatch, log, n_frames=60):
    import types

    class _PW:
        chromium = types.SimpleNamespace(launch=lambda **k: _Browser(log, n_frames))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = _PW
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)


def test_record_freezes_the_clock_blocks_the_network_and_ends_on_the_base_date(tmp_path, monkeypatch):
    """녹화 순서: 외부 요청 막기 → 시계 설치 → 열기 → **멈추기**(안 멈추면 가짜 시계도 실시간으로 흘러 영상이 두 배로 빨랐다)
    → 그림 끝까지 창 높이 → 기준일로 끝나는 첫 날로 막대 → 재생 → 프레임마다 정수 ms."""
    log: list = []
    _fake_playwright(monkeypatch, log, n_frames=60)
    frames = tmp_path / "f"
    frames.mkdir()
    n, w, h = M.record(tmp_path / "map.html", frames, seconds=2, width=1280)
    kinds = [e[0] for e in log]
    assert ("route", "http*://**") in log and kinds.index("route") < kinds.index("goto")
    assert any(e[0] == "init" and "Math.random" in e[1] for e in log)
    assert kinds.index("install") < kinds.index("goto") < kinds.index("pause_at") < kinds.index("click")
    assert ("viewport", 1280, 901 + M.BOTTOM_PAD + (901 + M.BOTTOM_PAD) % 2) in log and h % 2 == 0
    start, _ = M.plan(60, 2)
    slider = [e[1] for e in log if e[0] == "evaluate" and "'day'" in e[1]]
    assert slider and f"r.value = {start};" in slider[0]
    assert kinds.index("click") > kinds.index("evaluate")
    shots = [e for e in log if e[0] == "shot"]
    assert n == len(shots) == 2 * M.FPS and (w, h) == (1280, h)
    after = [e[1] for e in log[kinds.index("click"):] if e[0] == "run_for"]
    assert sum(after) == 2000, "프레임 사이 시계를 모두 더하면 영상 길이와 같아야 한다(누적 오차 없음)"


def test_record_without_timelapse_frames_is_a_clean_error(tmp_path, monkeypatch):
    _fake_playwright(monkeypatch, [], n_frames=0)
    with pytest.raises(M.VideoError, match="타임랩스 프레임이 없다"):
        M.record(tmp_path / "map.html", tmp_path, seconds=2, width=1280)


def _argv_ffmpeg(tmp_path: Path) -> tuple[str, Path]:
    """받은 인자를 한 줄씩 적고 마지막 인자(출력 파일)를 만드는 가짜 ffmpeg."""
    log = tmp_path / "argv.txt"
    p = tmp_path / "ffmpeg-argv"
    p.write_text(f'#!/bin/sh\nfor a in "$@"; do printf "%s\\t" "$a" >> {log}; done\nprintf "\\n" >> {log}\n'
                 'for a in "$@"; do last="$a"; done\n: > "$last"\n', "utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p), log


def test_encode_makes_a_web_playable_mp4_and_a_shared_palette_gif(tmp_path):
    """MP4 는 브라우저가 트는 H.264·yuv420p·faststart, GIF 는 공용 팔레트·무한 반복·README 폭."""
    exe, log = _argv_ffmpeg(tmp_path)
    M.encode(exe, tmp_path, mp4=str(tmp_path / "a.mp4"), gif=str(tmp_path / "a.gif"), width=1280)
    mp4, gif = [line.split("\t") for line in log.read_text("utf-8").splitlines()]
    assert {"libx264", "yuv420p", "+faststart"} <= set(mp4) and "scale=1280:-2:flags=lanczos" in mp4
    vf = gif[gif.index("-vf") + 1]
    assert "palettegen" in vf and "paletteuse" in vf and f"scale={M.GIF_WIDTH}:" in vf and gif[gif.index("-loop") + 1] == "0"


def test_make_records_then_encodes_and_leaves_no_temp(tmp_path, monkeypatch):
    exe, log = _argv_ffmpeg(tmp_path)
    seen = {}

    def fake_record(page, frames, *, seconds, width):
        seen["frames"] = frames
        return 3, width, 700

    monkeypatch.setattr(M, "record", fake_record)
    monkeypatch.setattr(M, "map_html", lambda report, tmp: tmp / "map.html")
    out = tmp_path / "deep" / "x.gif"
    assert M.make(tmp_path, exe, gif=str(out), seconds=2, width=1000) == (3, 1000, 700)
    assert out.exists() and not seen["frames"].exists(), "임시 프레임 폴더가 남았다"
    assert f"scale={M.GIF_WIDTH}:" in log.read_text("utf-8")


def _shots():
    spec = importlib.util.spec_from_file_location("_shots_for_video", REPO / "scripts" / "demo_shots.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_demo_shots_stops_before_shooting_when_the_gif_cannot_be_made(tmp_path, monkeypatch, capsys):
    """ffmpeg 가 없으면 PNG 도 안 찍는다 — 반만 다시 찍고 원장을 전부 HEAD 로 고치면 GIF 가 새 것인 척한다."""
    S = _shots()
    manifest = tmp_path / "SCREENSHOTS.md"
    manifest.write_text((REPO / "docs" / "SCREENSHOTS.md").read_text("utf-8"), "utf-8")
    monkeypatch.setattr(S, "MANIFEST", manifest)
    monkeypatch.setattr(S, "_git", lambda *a: "")
    shot = []
    monkeypatch.setattr(S, "shoot", lambda *a: shot.append(a))

    video = _load()

    def no_ffmpeg(outputs, explicit=None):
        raise video.NoFFmpeg("ffmpeg 를 못 찾았다")

    monkeypatch.setattr(video, "find_ffmpeg", no_ffmpeg)
    monkeypatch.setattr(S, "_map_video", lambda: video)
    before = manifest.read_text("utf-8")
    assert S.main(["--demo-dir", str(tmp_path)]) == 2
    assert not shot and manifest.read_text("utf-8") == before
    assert "아무것도 찍지 않았다" in capsys.readouterr().err


def test_demo_shots_retakes_the_gif_next_to_the_pngs(tmp_path, monkeypatch):
    S = _shots()
    manifest = tmp_path / "SCREENSHOTS.md"
    manifest.write_text((REPO / "docs" / "SCREENSHOTS.md").read_text("utf-8"), "utf-8")
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(S, "MANIFEST", manifest)
    monkeypatch.setattr(S, "IMAGES", images)
    monkeypatch.setattr(S, "REPO", tmp_path)
    monkeypatch.setattr(S, "_git", lambda *a: "b" * 40 if a[0] == "rev-parse" else "")
    monkeypatch.setattr(S, "shoot", lambda *a: None)
    video = _load()
    made = []
    monkeypatch.setattr(video, "find_ffmpeg", lambda outputs, explicit=None: "ffmpeg")
    monkeypatch.setattr(video, "make", lambda report, ff, **k: made.append(k) or Path(k["gif"]).write_bytes(b"GIF89a"))
    monkeypatch.setattr(S, "_map_video", lambda: video)
    assert S.main(["--demo-dir", str(tmp_path)]) == 0
    assert made and made[0]["gif"] == str(images / "map-demo.gif")
    assert "| docs/images/map-demo.gif | scripts/map_video.py | " + "b" * 40 in manifest.read_text("utf-8")
