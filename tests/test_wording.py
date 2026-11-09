"""금칙어 표는 하나다 — 수치표·뷰어·검증 페이지·다이제스트·README 가 같은 표로 검사받는다.

예전엔 다이제스트와 검증 페이지가 표를 따로 들고 내용도 달랐고, 그 틈으로 ``주도주`` 가 수치표와 터미널에 들어갔다.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

from sectorflow import wording as W

REPO = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_wording_{name}", REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: 사람이 읽는 산출물의 원천 — 코드가 아니라 글이 나오는 곳.
SOURCES = ["README.md", "scripts/templates/sector_numbers.html", "scripts/templates/sector_flow.html",
           "scripts/templates/trust.html", "scripts/templates/digest_card.html"]
#: 화면 글이 문자열로 든 코드 — 주석·독스트링은 사람에게 안 보이니 문자열 상수만 본다.
CODE_SOURCES = ["src/sectorflow/tui/flow_view.py", "src/sectorflow/tui/ledger_view.py", "scripts/sector_numbers.py"]


@pytest.mark.parametrize("rel", SOURCES)
def test_no_output_source_uses_a_banned_word(rel):
    hits = W.page_hits((REPO / rel).read_text("utf-8"))
    assert hits == [], f"{rel}: {hits}"


def _string_constants(tree: ast.AST):
    docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            yield n.value


@pytest.mark.parametrize("rel", CODE_SOURCES)
def test_no_screen_string_uses_a_banned_word(rel):
    tree = ast.parse((REPO / rel).read_text("utf-8"))
    hits = {s: W.page_hits(s) for s in _string_constants(tree) if W.page_hits(s)}
    assert not hits, f"{rel}: {hits}"


def test_there_is_one_table():
    """표를 따로 들면 다시 어긋난다 — 다이제스트·검증 페이지는 정본을 가져다 쓰고, 다른 표는 없다."""
    dg, tp = _load("digest"), _load("trust_page")
    assert dg.BANNED is W.BANNED and dg.banned_hits is W.banned_hits
    assert tp.page_hits is W.page_hits and not hasattr(tp, "BANNED")
    tables = [p for p in [*REPO.glob("scripts/*.py"), *REPO.glob("src/sectorflow/**/*.py")]
              if re.search(r"^BANNED\s*=", p.read_text("utf-8"), re.M)]
    assert tables == [REPO / "src/sectorflow/wording.py"]


def test_table_covers_what_either_old_table_covered():
    for w in ("추천", "매수 신호", "급등", "수익 보장", "적중", "주도주", "대장주", "유망", "전망", "기대"):
        assert w in W.BANNED, w


def test_a_page_may_say_it_is_not_a_recommendation_but_nothing_more():
    for ok in ("추천 없음 — 관측이다", "사실만(추천 아님)", "점수·순위·추천 없이", "순위·추천이 아니다"):
        assert W.page_hits(ok) == [], ok
    assert W.page_hits("오늘의 추천 섹터") == ["추천"]
    assert W.page_hits("추천 없음 — 그래도 주도주") == ["주도주"]
    assert W.banned_hits("추천 없음") == ["추천"], "다이제스트 본문에는 예외가 없다"


def test_rendered_page_check_skips_only_the_data_line():
    page = 'const D = {"names":{"1":{"name":"기대산업"}}};\n<p>글</p>\n'
    assert W.rendered_page_hits(page) == []
    assert W.rendered_page_hits(page + "<p>유망 섹터</p>") == ["유망"]


def test_verifier_fails_a_built_page_with_a_banned_word(tmp_path):
    """그날 만든 페이지 — 소스 검사가 못 보는 끼워 넣은 글(주입 문구·표지)까지 본다."""
    VR = _load("verify_report")
    (tmp_path / "numbers.html").write_text('const D = {"a":"기대"};\n<p>추천 없음</p>', "utf-8")
    (tmp_path / "viewer.html").write_text("<p>주도주</p>", "utf-8")
    VR.FAILS.clear()
    VR._w_wording(str(tmp_path))
    assert [f for f in VR.FAILS] == ["W/금칙어 — viewer.html 의 글에 권유·전망처럼 읽히는 말이 없는가"]
