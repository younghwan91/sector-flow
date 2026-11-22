"""브랜드 가이드(``docs/brand/README.md``)가 스스로 말한 것을 지키는가.

가이드는 개발부가 CSS 로 옮기는 색 값의 정본이고, 사람이 읽는 금칙어 표다. 문서라 틀려도 안 빨개지니
낡기 쉬운 자리 셋을 기계로 본다: 적어 둔 대비비가 hex 와 맞고 기준을 넘는가, 금칙어 정본의 말이 표에 다 있는가,
한 줄 소개가 그 표의 말을 쓰지 않는가.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sectorflow import wording as W

REPO = Path(__file__).resolve().parent.parent
GUIDE = (REPO / "docs/brand/README.md").read_text("utf-8")

ROW = re.compile(r"^\| `--(\w+)` \|[^|]*\| `(#[0-9a-f]{6})` \| ([\d.]+|—) \| `(#[0-9a-f]{6})` \| ([\d.]+|—) \| ([^|]+) \|$", re.M)


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _rows() -> dict[str, tuple[str, str, str, str, str]]:
    return {m[0]: m[1:] for m in ROW.findall(GUIDE)}


def _section(start: str) -> str:
    i = GUIDE.index(start)
    j = GUIDE.find("\n## ", i + len(start))
    return GUIDE[i:j if j > 0 else None]


# mutation-exempt: 검사기(정규식)가 눈을 떴는지 보는 검사다 — 주입할 소스가 아니라 이 파일의 정규식이 대상이다
def test_colour_table_is_read():
    """정규식이 표 꼴 변화로 아무것도 못 읽으면 아래 대비 검사가 빈손으로 초록이다."""
    rows = _rows()
    for tok in ("bg", "ink", "muted", "give", "take", "indiv", "forgn", "inst", "etc", "resid"):
        assert tok in rows, f"색 표에 --{tok} 행이 없다(또는 꼴이 바뀌어 못 읽는다)"


@pytest.mark.parametrize("mode", [0, 1], ids=["light", "dark"])
def test_stated_contrast_matches_the_hex_and_meets_the_bar(mode):
    """표에 적은 대비비는 hex 에서 다시 잰 값과 같고, 기준(글자 4.5:1 · 띠 3:1)을 넘는다."""
    rows = _rows()
    bg = rows["bg"][2 * mode]
    checked = 0
    for tok, cells in rows.items():
        hexv, stated, bar = cells[2 * mode], cells[2 * mode + 1], cells[4]
        need = 4.5 if "4.5:1" in bar else 3.0 if "3:1" in bar else None
        if need is None:
            assert stated == "—", f"--{tok}: 기준이 없는데 대비비를 적었다"
            continue
        got = _contrast(hexv, bg)
        assert stated != "—" and abs(float(stated) - got) < 0.006, f"--{tok} {hexv}: 표 {stated} ≠ 실제 {got:.2f}"
        assert got >= need, f"--{tok} {hexv}: 대비 {got:.2f} < {need}"
        checked += 1
    assert checked >= 9


def _avoid_words() -> list[str]:
    """'쓰는 말 · 안 쓰는 말' 표의 가운데 열을 ` · ` 로 가른 말들."""
    sec = _section("## 쓰는 말 · 안 쓰는 말")
    cells = [c for c in re.findall(r"^\| [^|\n]* \| ([^|\n]*) \| [^|\n]* \|$", sec, re.M) if c != "안 쓰는 말"]
    return [w.strip() for c in cells for w in c.split(" · ") if w.strip()]


def test_avoid_table_lists_every_word_the_wording_table_bans():
    """금칙어 정본에 말이 늘면 가이드의 '안 쓰는 말' 에도 들어가야 한다 — 사람은 가이드를 읽는다."""
    avoid = _avoid_words()
    assert len(avoid) >= 20, avoid
    missing = [w for w in (*W.BANNED, *getattr(W, "MAP_AVOID", ())) if w not in avoid]
    assert not missing, f"가이드의 안 쓰는 말 표에 없다: {missing}"


def test_tagline_uses_no_avoided_word():
    """한 줄 소개는 README 머리·랜딩·레포 설명이 옮긴다 — 금칙어 검사와 가이드의 '안 쓰는 말' 둘 다 통과해야 한다."""
    tagline = GUIDE.split("<!-- brand:tagline -->")[1].split("<!-- /brand:tagline -->")[0]
    assert "수급 지도" in tagline and "예측이 아니다" in tagline
    assert W.page_hits(tagline) == [], W.page_hits(tagline)
    flat = re.sub(r"\s+", "", tagline)
    hits = [w for w in _avoid_words() if re.sub(r"\s+", "", w) in flat]
    assert not hits, f"한 줄 소개에 안 쓰는 말: {hits}"


# ── 워드마크·파비콘 ──────────────────────────────────────────────────────────

BRAND = REPO / "docs/brand"
SVGS = {
    "wordmark.svg": 0,
    "wordmark-dark.svg": 1,
    "favicon.svg": 1,
    "logo.svg": 0,
    "logo-dark.svg": 1,
    "logo-mark.svg": 0,
}  # 파일 → 색 표의 열(0 라이트·1 다크)


@pytest.mark.parametrize("name", SVGS)
def test_logo_has_no_font_dependency(name):
    """글자는 아웃라인 path 다 — 글꼴이 없는 기기·README 미리보기에서도 같은 모양이어야 한다."""
    import xml.etree.ElementTree as ET
    text = (BRAND / name).read_text("utf-8")
    root = ET.fromstring(text)
    assert root.get("viewBox"), name
    tags = {el.tag.split("}")[-1] for el in root.iter()}
    assert not tags & {"text", "tspan", "style", "script", "image", "foreignObject"}, (name, tags)
    assert not re.search(r"font-family|@import|@font-face|href=|url\(", text), name
    assert root.find("{http://www.w3.org/2000/svg}title") is not None, f"{name}: 화면 낭독용 <title> 이 없다"


@pytest.mark.parametrize("name", SVGS)
def test_logo_colours_come_from_the_table(name):
    """로고가 표에 없는 색을 쓰면 가이드와 갈린다 — 색은 그 모드 열의 토큰 값만."""
    col = SVGS[name]
    allowed = {cells[2 * col] for cells in _rows().values()}
    used = set(re.findall(r'fill="(#[0-9a-f]{6})"', (BRAND / name).read_text("utf-8")))
    assert used and used <= allowed, f"{name}: 표에 없는 색 {sorted(used - allowed)}"


def test_map_page_carries_the_same_wordmark_and_favicon():
    """지도 페이지 머리의 워드마크·파비콘은 이 폴더 파일의 사본이다 — 파일만 고치고 페이지를 잊으면 둘이 갈린다."""
    from urllib.parse import unquote
    page = (REPO / "scripts/templates/sector_map.html").read_text("utf-8")
    body = (BRAND / "wordmark-dark.svg").read_text("utf-8").strip()
    body = body[body.index("<title>"):]                      # 여는 태그(크기·xmlns)만 페이지에 맞게 다르다
    assert body in page, "지도 페이지 머리의 워드마크가 docs/brand/wordmark-dark.svg 와 다르다"
    m = re.search(r'<link rel="icon" type="image/svg\+xml" href="data:image/svg\+xml,([^"]+)">', page)
    assert m, "지도 페이지에 파비콘 링크가 없다"
    fav = (BRAND / "favicon.svg").read_text("utf-8").strip().replace('"', "'")
    assert unquote(m.group(1)) == fav, "지도 페이지 파비콘이 docs/brand/favicon.svg 와 다르다"
