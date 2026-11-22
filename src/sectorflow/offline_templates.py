"""설치본(wheel) 템플릿은 밖으로 나가는 요청을 하지 않는다 — BYO 설계 §4.2 "텔레메트리·제3자 네트워크 금지".

레포의 ``scripts/templates/*.html`` 은 Google Fonts 를 불러온다(서버에서 보는 웹 페이지라 그대로 둔다). wheel 빌드
때(``hatch_build.py``) 그 ``<link>`` 만 떼어 낸 사본을 싣는다. 글꼴은 CSS 의 다음 후보(시스템 글꼴)로 떨어진다.

남겨 두는 것: 사용자가 눌러야 열리는 ``<a href>`` 링크(문서 안내)와 SVG 네임스페이스(``xmlns``) — 둘 다 자동 요청이 아니다.
"""

from __future__ import annotations

import re

#: wheel 에 싣는 템플릿 — 파일 하나씩 적는다(새 템플릿은 여기와 ``tests/test_packaging.py`` 에서 판단을 받는다).
TEMPLATES = ("sector_flow.html", "sector_numbers.html", "sector_map.html")
#: wheel 에 **안** 싣는 템플릿 — 운영 서버의 웹 전용 페이지다. 그걸 그리는 ``digest.py``·``trust_page.py`` 는 설치본에 없다.
WEB_ONLY_TEMPLATES = ("digest_card.html", "trust.html")

_REMOTE_LINK = re.compile(r"<link\b[^>]*\bhref=[\"']https?://[^>]*>\s*\n?", re.I)
_CSS_IMPORT = re.compile(r"@import\s+url\(\s*[\"']?https?://[^)]*\)\s*;?", re.I)
#: 자동으로 불러오는 원격 자원 — 이게 남으면 오프라인 위반이다.
AUTO_FETCH = re.compile(
    r"<(?:link|script|img|iframe|source|audio|video|embed)\b[^>]*\b(?:href|src)=[\"']https?://"
    r"|@import\s+url\(\s*[\"']?https?://|url\(\s*[\"']?https?://", re.I)


def strip_remote(html: str) -> str:
    """원격 ``<link>``(글꼴·preconnect)와 CSS ``@import url(http…)`` 를 뗀다."""
    return _CSS_IMPORT.sub("", _REMOTE_LINK.sub("", html))
