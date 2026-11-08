"""금칙어 표 — 수치표·뷰어·검증 페이지·다이제스트·README 가 같이 쓰는 **하나뿐인** 표. 표준 라이브러리만.

예전엔 다이제스트와 검증 페이지가 표를 따로 들고 내용도 달랐다. 그 틈으로 ``주도주`` 가 다이제스트에서는 금칙어인데
수치표·터미널에는 적혀 있었다. 산출물이 늘어도 표는 여기 하나고, 어디를 어떻게 검사하는지는 ``tests/test_wording.py``
(소스)와 ``verify_report.py`` W 층(그날 만든 페이지)·``trust_page.py``·``digest.py``(쓰기 전)가 말한다.

``docs/`` 의 문서는 대상이 아니다 — 그 말들이 왜 틀렸는지 설명하는 글이라 낱말이 나올 수밖에 없다.
"""
from __future__ import annotations

import re
import unicodedata

#: 나오면 안 되는 말 — 권유·전망·보장·적중처럼 읽히는 말.
BANNED = ("추천", "매수 신호", "매도 신호", "신호", "급등", "폭등", "급락", "세력", "목표가", "수익 보장", "보장",
          "확실", "유망", "수혜", "주도주", "대장주", "매수하라", "담아라", "전망", "기대", "적중")

#: 화면·문서가 일부러 적는 부정문 — 그것이 "추천" 을 말하는 것은 아니다. 다이제스트 본문은 이 예외를 쓰지 않는다
#: (:func:`banned_hits`). 꼬리의 면책 문구는 다이제스트가 따로 뺀다.
NEGATED = re.compile(r"추천\s*(?:없음|없이|아님|이\s*아니다)")

#: 비교에서 한 항목으로 보는 단위의 경계 — 목록 구분자(` · `·` | `), 줄, `: `.
_ITEM_SEP = re.compile(r"\s+[·|]\s+|\n|:\s")


def _letters(t: str) -> str:
    return "".join(c for c in t if unicodedata.category(c)[0] in "LN")


def banned_hits(text: str) -> list[str]:
    """``text`` 에 든 금칙어. NFKC 로 정규화한 뒤 항목(:data:`_ITEM_SEP` 로 나눈 것)마다 글자·숫자만 남겨
    비교한다 — 공백(`수익  보장`)·폭 0 문자·문장부호(`추.천`·`급-등`)를 끼워도 잡는다. 항목 경계를 넘어
    붙이지는 않는다(`통신 · 호텔` 이 `신호` 가 되지 않게).
    """
    items = [_letters(it) for it in _ITEM_SEP.split(unicodedata.normalize("NFKC", text))]
    return [w for w in BANNED if any(_letters(w) in it for it in items)]


#: 화면 글은 문장이 길다 — 문장을 넘어 붙여 읽지 않는다(`바꾸기. 대문자` 가 `기대` 가 되지 않게). 공백 없는 `추.천` 은 그대로 잡힌다.
_SENTENCE = re.compile(r"[.,;!?]\s+")


def page_hits(text: str) -> list[str]:
    """화면·문서용 — :data:`NEGATED` 의 부정문을 빼고 문장으로 나눈 뒤 :func:`banned_hits`."""
    return banned_hits(_SENTENCE.sub("\n", NEGATED.sub(" ", unicodedata.normalize("NFKC", text))))


#: 페이지에 심은 데이터 줄(``const D = {...};``) — 종목·섹터 이름은 벤더가 준 고유명사라 검사 대상이 아니다.
_DATA_LINE = re.compile(r"^const D = .*$", re.M)


def rendered_page_hits(html: str) -> list[str]:
    """그날 만든 수치표·뷰어 HTML — 심은 데이터를 뺀 나머지(글·표지·도움말)에서 :func:`page_hits`."""
    return page_hits(_DATA_LINE.sub("", html))
