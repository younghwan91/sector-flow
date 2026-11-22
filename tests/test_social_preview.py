"""소셜 미리보기(``scripts/social_preview.py`` → ``docs/brand/social-preview.png``) — 합성 데모만, 가이드 문구 그대로.

브라우저 없이 본다: 카드 HTML 이 표지·한 줄 소개를 담고 금칙어가 없는가, 실데이터 폴더를 거절하는가, 올린 그림이 1280×640 인가.
"""
from __future__ import annotations

import importlib.util
import re
import struct
from pathlib import Path

import pytest

from sectorflow.basis import DEMO_MARK
from sectorflow.wording import banned_hits, map_hits

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("social_preview", REPO / "scripts" / "social_preview.py")
SP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SP)


def _card() -> str:
    return SP.card_html(SP.GUIDE.read_text("utf-8"), SP.WORDMARK.read_text("utf-8"), b"\x89PNG")


def _visible(html: str) -> str:
    html = re.sub(r"<style>.*?</style>|<svg.*?</svg>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def test_card_shows_the_demo_mark_on_the_picture_and_under_the_text():
    """그림만 떼어 가도 표지가 남게 그림 위에, 글만 읽어도 보이게 글 아래에 — 두 곳."""
    assert _visible(_card()).count(DEMO_MARK) == 2


def test_card_quotes_the_guide_tagline():
    """문구의 정본은 가이드다 — 카드가 따로 지어 쓰면 README·레포 설명과 갈린다."""
    head, rest, tail = SP.tagline(SP.GUIDE.read_text("utf-8"))
    assert head.startswith("오늘 누가") and rest.endswith("본다.") and "예측이 아니다" in tail
    text = " ".join(_visible(_card()).split())
    for part in (head, rest, tail):
        assert part in text


# mutation-exempt: 카드 본문은 가이드 한 줄 소개에서 오며 금칙어 표 통과 여부를 보는 정책 검사다
def test_card_text_passes_the_wording_tables():
    text = _visible(_card())
    assert not banned_hits(text) and not map_hits(text), (banned_hits(text), map_hits(text))


# mutation-exempt: 생성된 카드 HTML 이 외부 http(s) 요청을 포함하지 않는지 보는 정적 불변 검사다
def test_card_makes_no_network_request():
    """그림은 data URI, 글꼴은 설치본 — 찍는 기기마다 원격 글꼴 유무로 모양이 갈리지 않게."""
    assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", _card())


def test_refuses_a_folder_that_is_not_the_synthetic_demo(tmp_path):
    (tmp_path / "payload.json").write_text("{}", "utf-8")
    with pytest.raises(SystemExit, match="합성 데모 폴더가 아니다"):
        SP.shoot(tmp_path, tmp_path / "x.png")


# mutation-exempt: 커밋된 PNG 파일(바이너리 산출물)의 규격(1280×640·1MB 미만)을 검사한다
def test_committed_preview_is_1280_by_640():
    png = (REPO / "docs/brand/social-preview.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (1280, 640)
    assert len(png) < 1_000_000, "GitHub 소셜 미리보기 상한 1MB"


def test_stamp_rewrites_only_the_preview_rows():
    """찍은 뒤 이 그림의 줄만 HEAD 로 — docs/images 줄은 demo_shots.py 가 고친다(서로 남의 줄을 새것처럼 적지 않게)."""
    text = SP.MANIFEST.read_text("utf-8")
    mine = [ln for ln in text.splitlines() if ln.startswith("| docs/brand/social-preview.png |")]
    new = SP.stamp_manifest(text, "b" * 40)
    assert len(mine) >= 5 and new.count("b" * 40) == len(mine)
    assert {"scripts/social_preview.py", "docs/brand/wordmark-dark.svg", "src/sectorflow/demo.py"} <= set(SP.watched(text))
