"""조사 고르기 — 받침·ㄹ 규칙과 숫자·영문의 읽는 소리."""
from __future__ import annotations

import pytest

from sectorflow.josa import josa


def test_josa_basic():
    assert josa("기관", "이/가") == "기관이"
    assert josa("외국인", "은/는") == "외국인은"
    assert josa("가상물류", "으로/로") == "가상물류로"
    assert josa("개인", "을/를") == "개인을" and josa("가상소재", "을/를") == "가상소재를"
    assert josa("기타법인", "과/와") == "기타법인과" and josa("외국인", "과/와") == "외국인과"
    assert josa("잔여", "이/가") == "잔여가"


def test_josa_rieul_and_readings():
    """ㄹ 받침 뒤 '으로/로' 는 '로', 숫자·영문은 읽는 소리 — 그 밖의 받침 규칙은 그대로."""
    assert josa("가상물", "으로/로") == "가상물로" and josa("가상전자", "으로/로") == "가상전자로"
    assert josa("가상건설", "으로/로") == "가상건설로" and josa("가상금융", "으로/로") == "가상금융으로"
    assert josa("가상물", "은/는") == "가상물은", "ㄹ 도 받침이다 — '으로/로' 만 다르다"
    assert josa("5", "이/가") == "5가" and josa("3", "이/가") == "3이" and josa("1", "으로/로") == "1로"
    assert josa("120", "은/는") == "120은" and josa("2,927억", "은/는") == "2,927억은"
    assert josa("IT", "은/는") == "IT는" and josa("URL", "으로/로") == "URL로" and josa("ETF", "을/를") == "ETF를"
    assert josa("KRX", "이/가") == "KRX가" and josa("NXT", "과/와") == "NXT와" and josa("CMN", "이/가") == "CMN이"
    assert josa("가상전자(코스닥)", "이/가") == "가상전자(코스닥)이"
    with pytest.raises(KeyError):
        josa("기관", "의")
