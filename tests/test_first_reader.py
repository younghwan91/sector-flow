"""처음 보는 독자가 위에서 아래로 읽는 길 — README·용어집·가이드가 실제와 맞는가.

문서는 코드와 달리 틀려도 안 빨개진다. 그래서 낡기 쉬운 자리를 기계로 본다: 스크립트 목록, 용어집, 비교표의 말투,
없어진 열 이름, 실종목명, 비공개 저장소라 열리지 않는 배지.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sectorflow.tui.flow_view import B_SHORT, EXPLORE_LABEL_TIERS

REPO = Path(__file__).resolve().parent.parent
README = (REPO / "README.md").read_text("utf-8")
GUIDE = (REPO / "docs/reading-guide.md").read_text("utf-8")
GLOSSARY = (REPO / "docs/glossary.md").read_text("utf-8")


def _section(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i:text.index(end, i + len(start))]


def _tree() -> str:
    sec = _section(README, "## 2. 구조", "## 3. 일일 배치")
    return re.findall(r"^```\w*\n(.*?)^```$", sec, re.S | re.M)[-1]


def test_every_script_is_in_the_readme_tree():
    """§2 트리에서 9개가 빠져 있었다. 새 스크립트가 생기면 여기서 빨개진다 — 한 줄 설명을 같이 적는다."""
    tree = _section(_tree(), "scripts/\n", "deploy/")
    named = set(re.findall(r"[\w.]+\.(?:py|sh)", tree))
    for stem, exts in re.findall(r"(\w+)\.\{([\w,]+)\}", tree):
        named |= {f"{stem}.{e}" for e in exts.split(",")}
    have = {p.name for p in (REPO / "scripts").iterdir() if p.is_file()}
    assert not have - named, f"§2 트리에 없는 스크립트: {sorted(have - named)}"
    assert not named - have, f"§2 트리가 없는 스크립트를 적는다: {sorted(named - have)}"


@pytest.mark.parametrize("term", [
    "투신", "연기금", "금투", "기타법인", "VWAP", "%p", "잠정", "확정", "ρ", "IC", "OLS", "K1", "K2", "K3", "S1", "Q3c",
    "β제거", "gross", "임펄스", "가속", "재구성", "연속 순매매", "합성 데모"])
def test_glossary_defines_the_term(term):
    assert re.search(rf"^\| [^|]*{re.escape(term)}[^|]*\|", GLOSSARY, re.M), f"용어집 표에 '{term}' 이 없다"


def test_glossary_defines_every_short_evidence_mark():
    """좁은 헤더의 ``[무]``·``[측]`` 는 화면에 뜻이 안 적힌다 — 용어집이 유일한 설명이다."""
    for mark in B_SHORT.values():
        assert f"`{mark}`" in GLOSSARY, mark


def test_readers_can_find_the_glossary():
    assert "docs/glossary.md" in README and "glossary.md" in GUIDE


def test_comparison_table_names_what_competitors_do():
    """'확인 못 해 뺐다' 고 적었지만 조사 노트가 그 페이지를 인용했다 — 비교에서 빼면 부풀림이다."""
    sec = _section(README, "### 1.1 다른 자료와", "**못하는 것도 있다.**")
    for must in ("[0789]", "[0797]", "[1051]", "머니레시피", "16:10", "21:30", "WICS", "코스캐너", "KRX+NXT"):
        assert must in sec, must
    assert "미검증" in sec and "400 LOGOUT" in sec, "KRX 는 미검증과 익명 거부를 그대로 적는다"
    assert "못 찾았다는 말이지 없다는 말은 아니다" in sec
    assert re.search(r"못 찾은 것은 셋", sec)
    assert "SSH" not in sec, "SSH 는 고를 이유가 아니다"


def test_received_folder_warning_is_at_both_entrances():
    """폴더를 주고받는 말이 나오는 두 곳 모두 회신 전 제3자 전달 금지를 적는다."""
    quick = _section(README, "## Quick start", "## 목차")
    share = _section(README, "### 4.3 ", "### 4.4 ")
    for sec in (quick, share):
        assert "제3자" in sec and "회신" in sec, sec[:200]


def test_quick_start_starts_with_something_that_runs():
    """첫 코드 블록은 DB·폴더 없이 도는 명령이어야 한다 — '리포트 폴더가 있어야 뜬다' 가 입구를 막았다."""
    first = re.search(r"```bash\n(.*?)```", _section(README, "## Quick start", "## 목차"), re.S).group(1)
    assert "uv sync" in first and "sw-flow --demo" in first
    assert "<리포트 폴더>" not in first


def test_readme_has_no_badge_that_private_repo_breaks():
    """비공개 저장소의 CI 배지는 익명에게 404 다(2026-11-09 curl 확인)."""
    assert "actions/workflows/ci.yml/badge.svg" not in README


@pytest.mark.parametrize("doc,text", [("README", README), ("가이드", GUIDE)])
def test_docs_do_not_name_columns_that_are_gone(doc, text):
    for gone in ("포텐셜", "U = ½kx²", "U 는 x 의", "투신+연금5일", "trading-guide"):
        assert gone not in text, f"{doc} 가 없어진 것을 말한다: {gone}"


@pytest.mark.parametrize("doc,text", [("README", README), ("가이드", GUIDE)])
def test_docs_use_no_real_stock_name(doc, text):
    """이용 범위 회신 전이라 문서의 예도 합성 데모로 만든다."""
    for name in ("삼성전자", "SK하이닉스", "NAVER", "삼성에스디에스", "LG씨엔에스", "아이티센글로벌"):
        assert name not in text, f"{doc} 에 실종목명: {name}"


def test_exploration_label_quote_matches_the_screen():
    """README 가 인용하는 표지가 화면의 표지와 같은 글자다."""
    assert f"`[{EXPLORE_LABEL_TIERS[0]}]`" in README and "`[탐색]`" in README


def test_readme_sector_count_says_what_27_is():
    """27 칸에는 `(미분류)` 가 들어 있다 — '벤더 분류 27개' 는 틀렸다."""
    assert "벤더 분류(`stocks.sector`) 26개와 `(미분류)` 를 합친 27칸" in README
    assert "`stocks.sector`) 27개" not in README


def test_collection_wording_admits_the_byo_collector():
    """`src/sectorflow/collect/` 가 있는데 '수집은 이 저장소에 없다' 는 틀렸다."""
    assert "데이터 수집은 이 저장소에 없다" not in README
    assert "src/sectorflow/collect/" in _section(README, "## Quick start", "## 목차")
    assert "src/sectorflow/collect/" in (REPO / "CLAUDE.md").read_text("utf-8")


def test_env_example_does_not_promise_a_sqlite_fallback():
    """비워 두면 빈 sqlite 를 보고 '기준일이 없다' 로 끝난다(재현함). 쓸 수 있는 대안처럼 적으면 안 된다."""
    text = (REPO / ".env.example").read_text("utf-8")
    assert "기준일이 없다" in text and "--demo" in text
    assert "로컬 SQLite(" not in text
