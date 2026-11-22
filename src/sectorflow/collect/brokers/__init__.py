"""증권사 어댑터 — 각자 자기 증권사 키로 지도 전용 수집(종목 목록 + 종목별 일별 투자자 순매매)을 한다.

조사: ``docs/byo/brokers.md``(증권사별 TR·필드·주체 구분·상한·약관). 계획: ``docs/superpowers/plans/2026-11-21-flow-map-brand.md``
Phase 6b. 공통 모양은 :mod:`.base`, 키움(기준)은 :mod:`.kiwoom`.

소유자는 키움 외 실제 키가 없다. 다른 어댑터는 공개 문서의 응답 모양을 보고 손으로 쓴 최소 표본으로 시험했고, 실제 키로 키움과
대조하기 전까지 ``Capability.verified`` 가 거짓이다(지도 머리에 "미검증 어댑터"). 벤더 명세 파일·공식 클라이언트 코드는 싣지 않는다.
"""

from __future__ import annotations

from typing import Callable

from . import kiwoom
from .base import Broker, Capability, Settings

#: 이름 → 어댑터 생성기. 순서가 CLI ``--broker`` 선택지 순서다(키움이 기본·처음).
BROKERS: dict[str, Callable[[Settings], Broker]] = {
    "kiwoom": kiwoom.make,
}
DEFAULT = "kiwoom"


def capability(name: str) -> Capability:
    """이름 → 능력. 어댑터를 만들지 않는다(키·네트워크 없이)."""
    return CAPABILITIES[name]


#: 이름 → 능력 — 지도·대조 명령이 수급 행의 ``source`` 로 찾는다.
CAPABILITIES: dict[str, Capability] = {
    "kiwoom": kiwoom.CAPABILITY,
}

__all__ = ["BROKERS", "CAPABILITIES", "DEFAULT", "Broker", "Capability", "Settings", "capability"]
