"""조사 고르기 — 앞말의 끝소리(받침)를 보고 ``은/는``·``이/가``·``을/를``·``으로/로``·``과/와`` 중 하나를 붙인다. 표준 라이브러리만.

한글 음절은 받침을 계산한다. 숫자·영문으로 끝나면 **읽는 소리**로 본다(``3`` 삼 → 받침, ``2`` 이 → 없음, ``L`` 엘 → ㄹ).
``%`` 는 퍼센트로 읽어 받침이 없다(``3%는``). 끝의 괄호·문장부호·공백은 건너뛴다(``가상전자(코스닥)`` 은 ``닥``). 지도 헤드라인이 쓴다(``sectorflow.flowmap.map_headline``).
"""
from __future__ import annotations

#: 조사 쌍 — (받침 있을 때, 없을 때).
PAIRS = {"은/는": ("은", "는"), "이/가": ("이", "가"), "을/를": ("을", "를"), "으로/로": ("으로", "로"), "과/와": ("과", "와")}
#: 받침 ㄹ 의 종성 번호 — ``으로/로`` 는 ㄹ 받침 뒤에서 ``로`` 다(``서울로``).
_RIEUL = 8
#: 숫자를 읽는 소리의 받침 — 0 영(ㅇ) 1 일(ㄹ) 3 삼(ㅁ) 6 육(ㄱ) 7 칠(ㄹ) 8 팔(ㄹ), 나머지(이·사·오·구)는 받침이 없다.
_DIGIT = {"0": "o", "1": "l", "2": "", "3": "o", "4": "", "5": "", "6": "o", "7": "l", "8": "l", "9": ""}
#: 영문 글자 이름의 받침 — 엘·알(ㄹ), 엠·엔(ㅁ·ㄴ). 나머지(에이·비·씨…)는 받침이 없다.
_LATIN = {"l": "l", "r": "l", "m": "o", "n": "o"}


def _coda(word: str) -> "str | None":
    """끝소리 — ``""`` 받침 없음, ``"l"`` ㄹ 받침, ``"o"`` 그 밖의 받침, 읽을 글자가 없으면 None."""
    for ch in reversed(word):
        if "가" <= ch <= "힣":
            j = (ord(ch) - 0xAC00) % 28
            return "" if j == 0 else ("l" if j == _RIEUL else "o")
        if ch in _DIGIT:
            return _DIGIT[ch]
        if ch == "%":  # 퍼센트 — 받침 없음
            return ""
        if ch.isascii() and ch.isalpha():
            return _LATIN.get(ch.lower(), "")
    return None


def josa(word: str, pair: str) -> str:
    """``word`` 에 알맞은 조사를 붙인 글 — ``josa("기관", "이/가") == "기관이"``. 모르는 쌍이면 ``KeyError``.

    끝소리를 못 읽으면(빈 글·기호뿐) 받침 없는 쪽을 붙인다.
    """
    with_coda, without = PAIRS[pair]
    c = _coda(word)
    if pair == "으로/로":
        return word + (with_coda if c == "o" else without)
    return word + (with_coda if c else without)
