"""운영 창 — 같은 앱키를 쓰는 프로그램이 도는 시간에는 키움을 부르지 않는다(호스트별 설정).

예전 판은 창을 코드에 박고(평일 06:30~07:30·15:30~19:30) 시작할 때 한 번만 봤다. 그래서 scalp-it 장중 수집
(08:55~15:40)·주말 DAG 를 몰랐고, 긴 백필이 창 안으로 걸어 들어가도 계속 불렀다.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pytest

from sectorflow.collect import busy
from sectorflow.collect import run as R
from sectorflow.collect.kiwoom import BusyWindow

REPO = Path(__file__).resolve().parent.parent
HOST = REPO / "deploy" / "kiwoom_busy.simnode.toml"


def _at(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=busy.KST)


@pytest.mark.parametrize("when,why", [
    ("2026-11-09 12:00", "평일 장중 — scalp-it 장중 수집"),
    ("2026-11-09 09:00", "평일 장 시작"),
    ("2026-11-09 16:30", "daily_collection"),
    ("2026-11-09 19:30", "daily_bars_raw"),
    ("2026-11-10 07:00", "화요일 daily_finalize"),
    ("2026-11-14 07:00", "토요일 daily_finalize"),
    ("2026-11-14 10:30", "토요일 daily_short_credit"),
    ("2026-11-15 11:30", "일요일 weekly_history_backfill"),
])
def test_simnode_windows_cover_every_job_using_the_key(when, why):
    assert busy.active(busy.load(HOST), _at(when)) is not None, why


@pytest.mark.parametrize("when", [
    "2026-11-08 17:05",   # Phase 1 실측을 돌린 일요일 오후
    "2026-11-09 07:00",   # 월요일 아침 — daily_finalize 는 화~토
    "2026-11-09 21:00",
    "2026-11-14 13:00",
])
def test_simnode_free_times(when):
    assert busy.active(busy.load(HOST), _at(when)) is None


def test_guard_raises_inside_a_window_and_byo_default_is_empty():
    w = busy.load(HOST)
    with pytest.raises(BusyWindow):
        busy.make_guard(w, now=lambda: _at("2026-11-09 12:00"))()
    busy.make_guard(w, now=lambda: _at("2026-11-08 17:05"))()
    assert busy.load(None) == []


def test_bad_window_is_rejected(tmp_path):
    f = tmp_path / "b.toml"
    f.write_text('[[window]]\ndays=["mon"]\nstart="10:00"\nend="09:00"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        busy.load(f)


def test_no_window_file_warns_about_shared_keys(monkeypatch, capsys):
    monkeypatch.setenv("KIWOOM_APP_KEY", "k")
    monkeypatch.setenv("KIWOOM_APP_SECRET", "s")
    R._client(argparse.Namespace(busy_file=None, env_file=None, rate=1.0, max_calls=1))
    assert "토큰을 무효화" in capsys.readouterr().err
    R._client(argparse.Namespace(busy_file=str(HOST), env_file=None, rate=1.0, max_calls=1))
    assert capsys.readouterr().err == ""
