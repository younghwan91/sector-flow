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


# ── 당국 질의서 — 발송 전에 사실이 맞아야 한다 ─────────────────────────────────

INQ = REPO / "docs/strategy/inquiries"
FSC = (INQ / "fsc_advisory_scope.md").read_text("utf-8")
KRX = (INQ / "krx_market_data.md").read_text("utf-8")
KIWOOM = (INQ / "kiwoom_api_terms.md").read_text("utf-8")


def test_inquiries_state_what_the_screen_really_shows():
    """'종목 없음·점수 없음' 은 사실이 아니다 — 첫 화면 종목명, 드릴다운 전 종목, 탐색 모드 점수를 적고 묻는다."""
    for must in ("종목명", "전 종목", "탐색 모드", "질문 12"):
        assert must in FSC, must
    assert "점수·등급·판정" not in FSC
    assert "탐색 모드" in KRX and "전 종목" in KRX
    assert "탐색 모드" in KIWOOM


def test_inquiries_state_the_publication_schedule_and_window():
    """'확정 기준 하루 1회'·'약 250거래일' 은 틀렸다 — 18:10 잠정·08:20 확정, 260거래일."""
    for doc in (FSC, KRX):
        assert "18:10" in doc and "08:20" in doc and "하루 1회" not in doc
        assert "260거래일" in doc
    assert "약 250" not in KRX


def test_krx_inquiry_states_the_real_sources():
    """정보데이터시스템에서 '받지 않는다' 는 틀렸다 — 상장폐지 목록·업종 분류·DART 주식수를 적는다."""
    assert "정보데이터시스템에서는 받지 않습니다" not in KRX
    for must in ("상장폐지", "OpenDART", "KRX 체결만", "400 LOGOUT"):
        assert must in KRX, must


def test_fsc_inquiry_does_not_claim_a_notice_the_screen_lacks():
    """'권유 아님' 은 지금 화면에 없다 — 있다고 적으면 질의의 사실관계가 틀린다."""
    assert '화면에 "관측이며 예측·권유가 아님"과 검증 결과를 표시합니다' not in FSC
    assert "지금 화면에 없고" in FSC


@pytest.mark.parametrize("rel", [
    "docs/strategy/inquiries/fsc_advisory_scope.md", "docs/strategy/inquiries/krx_market_data.md",
    "docs/strategy/inquiries/kiwoom_api_terms.md", "docs/strategy/inquiries/opendart_terms.md",
    "docs/strategy/inquiries/attachments/A_screen_example.md", "docs/strategy/inquiries/attachments/B_metric_definitions.md",
    "docs/strategy/inquiries/attachments/C_byo_structure.md", "docs/strategy/inquiries/attachments/D_sales_copy_draft.md",
    "docs/strategy/legal/terms_of_service_draft.md", "docs/strategy/legal/privacy_policy_draft.md",
    "docs/strategy/legal/refund_policy_draft.md"])
def test_drafts_say_they_are_drafts(rel):
    head = "\n".join((REPO / rel).read_text("utf-8").splitlines()[:5])
    assert "법률 검토 전 초안" in head, rel


def test_screen_example_attachment_has_no_real_name():
    """첨부 A 는 합성 데모다. 이름은 전부 가상이어야 한다."""
    text = (INQ / "attachments/A_screen_example.md").read_text("utf-8")
    assert "수치는 가상" in text and "[합성 데모]" in text and "가상유통" in text
    for name in ("삼성전자", "SK하이닉스", "NAVER", "전기/전자"):
        assert name not in text, name


# ── 전략 문서 ────────────────────────────────────────────────────────────────

STRATEGY = sorted((REPO / "docs/strategy").rglob("*.md"))


def test_strategy_docs_have_no_local_absolute_link():
    bad = [p.name for p in STRATEGY if "/home/young" in p.read_text("utf-8")]
    assert not bad, bad


def test_strategy_report_marks_interpretation_as_interpretation():
    """'법적 방패'·'가장 싼 방법' 은 확인 없는 단정이었고, 지불 의사와 공개 검증도 없는 것을 있는 듯 적었다."""
    text = next(p for p in STRATEGY if p.parent.name == "reports").read_text("utf-8")
    for gone in ("법적 방패", "광고 규제를 피하는", "통과하는 가장 싼 방법", "예측력 없음", "관측에는 월 5,900", "업종 한 개씩",
                 "검사 144건", "(/home"):
        assert gone not in text, gone
    for must in ("지불 미검증", "해석", "미확인", "비공개", "확인하지 못한 것", "204건"):
        assert must in text, must


def test_competitor_note_does_not_call_vendor_labels_its_own_basket():
    """섹터 라벨은 벤더 업종 27칸(미분류 포함)과 덮어쓴 4종목이다 — 자체 분류가 아니다."""
    text = next(p for p in STRATEGY if p.name == "competitors.md").read_text("utf-8")
    assert "자체 바스켓" not in text and "벤더 업종" in text and "[0797]" in text


def test_scorecard_does_not_claim_being_faster():
    text = (REPO / "docs/strategy/2026-11-08-scorecard.md").read_text("utf-8")
    assert "무료 소스보다 빠르고" not in text and "16:10" in text


# ── 연구 재현 문서 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("study", ["q1_data", "q2_calc", "q3ab_revision"])
def test_research_inputs_point_at_the_archive(study):
    """2026-11-09 에 48개를 다시 만들어 '지금 reports/ 그대로다' 는 거짓이 됐다."""
    text = (REPO / "research/validation" / study / "README.md").read_text("utf-8")
    assert "reports/.archive/" in text
    assert "지금 `reports/` 그대로" not in text
