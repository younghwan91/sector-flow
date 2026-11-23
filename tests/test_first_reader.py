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
#: README 의 긴 절(§1~§11)은 2026-11-24 가이드로 옮겼다 — 트렌딩 레포처럼 README 는 머리·데모·빠른 시작·링크만.
MANUAL = (REPO / "docs/guide.md").read_text("utf-8")
GUIDE = (REPO / "docs/reading-guide.md").read_text("utf-8")
GLOSSARY = (REPO / "docs/glossary.md").read_text("utf-8")


def _section(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i:text.index(end, i + len(start))]


def _tree() -> str:
    sec = _section(MANUAL, "## 2. 구조", "## 3. 일일 배치")
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
    "β제거", "gross", "임펄스", "가속", "재구성", "연속 순매매", "합성 데모",
    "네이버 시장값", "분봉 밖 거래 큼", "권리락", "거래대금 집중", "금투÷기관", "신규·N일째"])
def test_glossary_defines_the_term(term):
    assert re.search(rf"^\| [^|]*{re.escape(term)}[^|]*\|", GLOSSARY, re.M), f"용어집 표에 '{term}' 이 없다"


@pytest.mark.parametrize("mark", ["네이버 시장값", "분봉 밖 거래 큼", "금투÷기관", "거래대금 집중", "신규"])
def test_first_screen_marks_are_explained_in_the_guide_and_readme(mark):
    """화면에 뜨는 표지는 읽기 가이드와 가이드 §5.1 에서 찾을 수 있다 — 없어진 칩 `금액 근사 오차 ⚠` 만 적혀 있었다."""
    assert mark in GUIDE and mark in _section(MANUAL, "### 5.1 키", "### 5.2"), mark
    assert "금액 근사 오차" not in GUIDE and "금액 근사 오차" not in GLOSSARY.split("## 검증")[0]


def test_glossary_defines_every_short_evidence_mark():
    """좁은 헤더의 ``[무]``·``[측]`` 는 화면에 뜻이 안 적힌다 — 용어집이 유일한 설명이다."""
    for mark in B_SHORT.values():
        assert f"`{mark}`" in GLOSSARY, mark


def test_readers_can_find_the_glossary():
    assert "docs/glossary.md" in README and "glossary.md" in GUIDE


def test_readers_can_find_the_disclaimer():
    """투자 조언 아님·데이터 이용 조건·권리자 연락은 '예측이 아니다' 절에서 바로 찾을 수 있어야 한다."""
    sec = _section(MANUAL, "## 8. 주의", "## 9.")
    assert "](../DISCLAIMER.md)" in sec and "](../SECURITY.md)" in sec
    assert "](DISCLAIMER.md)" in _section(README, "## 이 숫자는 예측이 아니다", "## 문서"), "README 에서도 고지로 바로 간다"
    assert (REPO / "DISCLAIMER.md").exists() and (REPO / "SECURITY.md").exists()


def test_comparison_table_names_what_competitors_do():
    """'확인 못 해 뺐다' 고 적었지만 조사 노트가 그 페이지를 인용했다 — 비교에서 빼면 부풀림이다."""
    sec = _section(MANUAL, "### 1.1 다른 자료와", "**못하는 것도 있다.**")
    for must in ("[0789]", "[0797]", "[1051]", "머니레시피", "16:10", "21:30", "WICS", "코스캐너", "KRX+NXT"):
        assert must in sec, must
    assert "미검증" in sec and "400 LOGOUT" in sec, "KRX 는 미검증과 익명 거부를 그대로 적는다"
    assert "못 찾았다는 말이지 없다는 말은 아니다" in sec
    assert re.search(r"못 찾은 것은 셋", sec)
    assert "SSH" not in sec, "SSH 는 고를 이유가 아니다"


def test_received_folder_warning_is_at_both_entrances():
    """폴더를 주고받는 말이 나오는 두 곳 모두 제3자 전달 금지를 적는다."""
    readme = _section(README, "## 내 데이터로 보기", "## 이 숫자는")
    share = _section(MANUAL, "### 4.3 ", "### 4.4 ")
    for sec in (readme, share):
        assert "제3자" in sec and "재배포" in sec, sec[:200]


def _commands(block: str) -> list[str]:
    return [ln.split("  #")[0].strip() for ln in block.strip().splitlines()]


def test_english_quick_start_runs_the_same_commands():
    """영어 README 는 한국어 Quick start 의 첫 블록을 옮긴 것이다 — 명령이 갈리면 한쪽이 낡았다."""
    en = (REPO / "README.en.md").read_text("utf-8")
    ko_first = re.search(r"```bash\n(.*?)```", _section(README, "## Quick start", "## 무엇을 보나"), re.S).group(1)
    en_first = re.search(r"```bash\n(.*?)```", _section(en, "## Quick start", "## Data policy"), re.S).group(1)
    assert _commands(en_first) == _commands(ko_first)
    assert "](README.en.md)" in README.split("## Quick start")[0], "한국어 README 머리에 영어 링크가 없다"
    assert "](README.md)" in en.split("## Quick start")[0]


def test_quick_start_starts_with_something_that_runs():
    """첫 코드 블록은 DB·폴더 없이 도는 명령이어야 한다 — '리포트 폴더가 있어야 뜬다' 가 입구를 막았다."""
    first = re.search(r"```bash\n(.*?)```", _section(README, "## Quick start", "## 무엇을 보나"), re.S).group(1)
    assert "uv sync" in first and "sw-flow --demo" in first
    assert "<리포트 폴더>" not in first


def test_readme_stays_short():
    """README 는 입구다 — 572줄로 자라 첫 화면 뒤가 운영·수식 설명이었다(2026-11-24 가이드로 옮김). 다시 자라면 가이드로."""
    assert len(README.splitlines()) <= 150, len(README.splitlines())
    assert "](docs/guide.md" in README and MANUAL.startswith("# sector-flow 가이드")


def test_readme_has_no_badge_that_private_repo_breaks():
    """비공개 저장소의 CI 배지는 익명에게 404 다(2026-11-09 curl 확인)."""
    assert "actions/workflows/ci.yml/badge.svg" not in README


@pytest.mark.parametrize("doc,text", [("README", README), ("가이드", MANUAL), ("읽기 가이드", GUIDE)])
def test_docs_do_not_name_columns_that_are_gone(doc, text):
    for gone in ("포텐셜", "U = ½kx²", "U 는 x 의", "투신+연금5일", "trading-guide"):
        assert gone not in text, f"{doc} 가 없어진 것을 말한다: {gone}"


@pytest.mark.parametrize("doc,text", [("README", README), ("가이드", MANUAL), ("읽기 가이드", GUIDE)])
def test_docs_use_no_real_stock_name(doc, text):
    """데이터 이용 범위상 문서의 예도 합성 데모로 만든다."""
    for name in ("삼성전자", "SK하이닉스", "NAVER", "삼성에스디에스", "LG씨엔에스", "아이티센글로벌"):
        assert name not in text, f"{doc} 에 실종목명: {name}"


def test_exploration_label_quote_matches_the_screen():
    """README 가 인용하는 표지가 화면의 표지와 같은 글자다."""
    assert f"`[{EXPLORE_LABEL_TIERS[0]}]`" in MANUAL and "`[탐색]`" in MANUAL


def test_readme_sector_count_says_what_27_is():
    """27 칸에는 `(미분류)` 가 들어 있다 — '벤더 분류 27개' 는 틀렸다."""
    assert "벤더 분류(`stocks.sector`) 26개와 `(미분류)` 를 합친 27칸" in MANUAL
    assert "`stocks.sector`) 27개" not in README + MANUAL


def test_collection_wording_admits_the_byo_collector():
    """`src/sectorflow/collect/` 가 있는데 '수집은 이 저장소에 없다' 는 틀렸다."""
    assert "데이터 수집은 이 저장소에 없다" not in README
    assert "src/sectorflow/collect/" in _section(README, "## 내 데이터로 보기", "## 이 숫자는")
    assert "src/sectorflow/collect/" in (REPO / "CLAUDE.md").read_text("utf-8")


def test_env_example_does_not_promise_a_sqlite_fallback():
    """비워 두면 빈 sqlite 를 보고 '기준일이 없다' 로 끝난다(재현함). 쓸 수 있는 대안처럼 적으면 안 된다."""
    text = (REPO / ".env.example").read_text("utf-8")
    assert "기준일이 없다" in text and "--demo" in text
    assert "로컬 SQLite(" not in text


#: 근거를 넘는 단정 — 검증 문서 결론은 수량만 말하고 까닭은 미확인이다. '일 수 있다' 로만 쓴다(6회차 D2).
OVERCLAIMS = ("값이 틀린 것이 아니라", "값이 틀린 표지가 아니라", "정의가 갈린", "대량 체결 의심", "드물게 뜨는", "정의 차이(")
OVERCLAIM_SURFACES = ("README.md", "docs/glossary.md", "docs/reading-guide.md", "docs/runbook.md",
                      "scripts/digest.py", "scripts/templates/trust.html",
                      "scripts/templates/sector_numbers.html", "src/sectorflow/demo.py", "src/sectorflow/tui/flow_view.py")


@pytest.mark.parametrize("rel", OVERCLAIM_SURFACES)
def test_no_surface_overclaims_the_off_book_explanation(rel):
    text = (REPO / rel).read_text("utf-8")
    assert not [w for w in OVERCLAIMS if w in text], rel


def test_validation_doc_states_the_stock_check_scope():
    """종목값 대조는 10-12 까지·수량만이다. 건수를 못 쟀으면 못 쟀다고 적어야 '일치' 가 전수로 읽히지 않는다."""
    v = (REPO / "docs/validation/2026-11-09-market-total-vs-published.md").read_text("utf-8")
    scope = v.split("**종목값 대조의 범위.**")[1].splitlines()[0]
    for must in ("10-12", "수량", "미확인", "10-29"):
        assert must in scope, must


# ── 연구 재현 문서 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("study", ["q1_data", "q2_calc", "q3ab_revision"])
def test_research_inputs_point_at_the_archive(study):
    """2026-11-09 에 48개를 다시 만들어 '지금 reports/ 그대로다' 는 거짓이 됐다."""
    text = (REPO / "research/validation" / study / "README.md").read_text("utf-8")
    assert "reports/.archive/" in text
    assert "지금 `reports/` 그대로" not in text


def test_readme_head_reads_top_to_bottom():
    """머리는 트렌딩 레포 꼴이다(2026-11-24): 로고 → 한 줄 소개 → 링크 줄(데모·가이드·후원) → 데모 지도 → Quick start.
    처음 읽는 사람이 그림부터 보고, 긴 설명은 가이드로 간다."""
    head = README[:README.index("## 무엇을 보나")]
    order = ("docs/brand/logo.svg", "한 장의 지도로 봅니다", "](docs/guide.md)", "github.com/sponsors/",
             "](docs/images/map-demo.", "## Quick start")
    pos = [head.find(s) for s in order]
    assert -1 not in pos and pos == sorted(pos), dict(zip(order, pos))
    from sectorflow.wording import banned_hits, map_hits
    assert not banned_hits(head) and not map_hits(head), (banned_hits(head), map_hits(head))


def test_readme_tagline_is_the_brand_guide_tagline():
    """한 줄 소개의 정본은 브랜드 가이드다 — README 가 따로 고치면 레포 설명·소셜 미리보기와 갈린다."""
    brand = (REPO / "docs/brand/README.md").read_text("utf-8")
    m = re.search(r"<!-- brand:tagline -->\s*>\s*\*\*[^*]+\*\*\s*—\s*(.+?)\.\s", brand, re.S)
    assert m, "브랜드 가이드에서 한 줄 소개를 못 읽었다"
    first = " ".join(m.group(1).split()) + "."
    head = " ".join(README[:README.index("## Quick start")].split())
    assert f"**{first}**" in head, first
