"""증권사 어댑터의 공통 모양 — 능력(:class:`Capability`)·설정(:class:`Settings`)·어댑터(:class:`Broker`)·수급 행.

어댑터는 두 가지만 한다: 전 종목 목록(``stocks`` 행)과 종목 하나의 일별 투자자 순매매(``supply_demand`` 행). 나머지(일봉·주식수·
업종지수)는 키움 전체 수집에만 있다(``run.py`` 가 키움 클라이언트로 직접 부른다). 조사·필드 대응은 ``docs/byo/brokers.md`` §4.

수급 행은 ``storage.SCHEMA`` 의 ``supply_demand`` 열 그대로다. 증권사가 주지 않는 주체는 **``None``(모름)** 이다 — 0 은
"순매매 없음" 이라 뜻이 다르다(``CLAUDE.md`` 데이터 함정). 어느 칸이 늘 ``None`` 인지는 :attr:`Capability.actors` 가 말한다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from sectorflow.storage import INVESTOR_COLUMNS

#: 거래소 기준. sector-flow 의 기존 리포트는 KRX 단독이다(``sectorflow.basis.DATA_BASIS``).
VENUE_KRX = "KRX"
#: KRX·넥스트레이드 통합 — 거래소를 고를 수 없는 증권사(토스).
VENUE_KRX_NXT = "KRX+NXT"
VENUES = (VENUE_KRX, VENUE_KRX_NXT)

#: ``supply_demand`` 한 행의 열 순서 — ``write.upsert_supply_demand`` 의 튜플 순서와 같다.
SD_FIELDS: tuple[str, ...] = ("code", "date", "close", "flu_rt", "acc_trde_qty", *INVESTOR_COLUMNS, "source")

#: ``transport(url, headers, body, timeout) -> (status, response_headers, json_body)`` — 키움 클라이언트와 같은 모양.
Transport = Callable[[str, dict, dict, float], "tuple[int, dict[str, str], dict]"]


@dataclass(frozen=True)
class Capability:
    """어댑터가 무엇을 주는가.

    Attributes:
        actors: ``storage.INVESTOR_COLUMNS`` 중 이 증권사가 주는 주체. 밖의 칸은 늘 ``None``.
        venue: :data:`VENUE_KRX` 또는 :data:`VENUE_KRX_NXT`.
        verified: 실제 키로 받은 값을 키움과 대조(``sector-flow-collect equiv``)해 맞았나. 거짓이면 지도에 "미검증 어댑터".
        terms_note: 약관상 주의 한 줄 — 시작할 때 찍는다.
    """

    actors: frozenset[str]
    venue: str
    verified: bool
    terms_note: str

    def __post_init__(self) -> None:
        unknown = set(self.actors) - set(INVESTOR_COLUMNS)
        if unknown:
            raise ValueError(f"모르는 주체 칸: {sorted(unknown)}")
        if self.venue not in VENUES:
            raise ValueError(f"거래소 기준은 {VENUES} 중 하나다: {self.venue!r}")
        if not self.terms_note.strip() or "\n" in self.terms_note:
            raise ValueError("terms_note 는 비지 않은 한 줄이다")

    @property
    def missing(self) -> tuple[str, ...]:
        """주지 않는 주체 칸 — ``INVESTOR_COLUMNS`` 순서."""
        return tuple(c for c in INVESTOR_COLUMNS if c not in self.actors)


@dataclass(frozen=True)
class Settings:
    """어댑터를 만드는 데 필요한 것 — CLI 인자에서 온다(``run._broker``).

    ``rate`` 가 ``None`` 이면 어댑터의 기본 속도다. ``guard`` 는 요청 직전마다 부른다(운영 창, :mod:`..busy`).
    ``accepted_terms`` 는 사용자가 명시적으로 받아들인 약관 경고의 증권사 이름들이다(LS). ``transport`` 는 테스트가 바꿔 끼운다.
    """

    env_file: "str | None" = None
    rate: "float | None" = None
    max_calls: "int | None" = None
    guard: "Callable[[], None] | None" = None
    notice: "Callable[[str], None] | None" = None
    accepted_terms: frozenset[str] = frozenset()
    transport: "Transport | None" = None


@runtime_checkable
class Broker(Protocol):
    """증권사 어댑터 하나(세션 하나). 호출 회계(``calls``·``retries``·``reissues``)는 실행 원장(``collect_runs``)에 실린다."""

    name: str
    capability: Capability
    #: 원장(``collect_runs`` 의 failed·notes)의 요청 칸 이름 — 종목 목록·수급 요청(키움 ``ka10099``·``ka10059``).
    list_label: str
    flow_label: str
    calls: Counter
    retries: Counter
    reissues: int

    @property
    def total_calls(self) -> int: ...

    def universe(self) -> list[dict]:
        """코스피·코스닥 보통주 — ``stocks`` 행(``code``·``name``·``market``·``sector``·``kind``·``sector_source``).

        ``sector`` 는 빈 값일 수 있다(그 증권사가 업종을 주지 않거나 쓰지 않는다 — 토스의 다중 소속 섹터). 주면
        ``sector_source`` 는 어댑터 이름이다. 저장된 다른 출처의 섹터는 ``write.upsert_stocks`` 가 지킨다.
        """
        ...

    def investor_daily(self, code: str, start: str, end: str, *, days: "int | None" = None,
                       info: "dict | None" = None) -> list[dict]:
        """``start``~``end``(YYYY-MM-DD, 양끝 포함)의 ``supply_demand`` 행(:data:`SD_FIELDS` 키의 dict).

        ``days`` 는 그 구간의 거래일 수(쪽 수를 정하는 힌트). ``info`` 를 주면 ``{"more": bool}`` 을 채운다 — 서버는 더
        있다는데 쪽 상한에서 멈췄다(잘림)는 뜻이다(``"pages"`` 에 받은 쪽 수). 신규 상장의 짧은 이력과 가르는 데 쓴다.
        """
        ...


def sd_row(code: str, date: str, *, close: "int | None", flu_rt: "float | None", acc_trde_qty: "int | None",
           actors: "dict[str, int | None]", source: str) -> dict:
    """수급 행 하나 — ``actors`` 에 없는 주체 칸은 ``None``(모름)."""
    unknown = set(actors) - set(INVESTOR_COLUMNS)
    if unknown:
        raise ValueError(f"모르는 주체 칸: {sorted(unknown)}")
    return {"code": code, "date": date, "close": close, "flu_rt": flu_rt, "acc_trde_qty": acc_trde_qty,
            **{c: actors.get(c) for c in INVESTOR_COLUMNS}, "source": source}


def sd_tuple(row: dict) -> tuple:
    """수급 행 dict → ``write.upsert_supply_demand`` 튜플(:data:`SD_FIELDS` 순서)."""
    return tuple(row[k] for k in SD_FIELDS)
