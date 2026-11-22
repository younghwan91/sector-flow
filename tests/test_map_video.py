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
