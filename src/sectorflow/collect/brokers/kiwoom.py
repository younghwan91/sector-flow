"""키움 어댑터 — 기존 클라이언트(:mod:`..kiwoom`)와 파서(:mod:`..fetch`)를 그대로 감싼다. 요청은 옮기기 전과 바이트째 같다.

기준 어댑터다: 11주체 전부·KRX 단독(접미사 없는 코드)이고 Phase 1 에서 kr_quant 와 수급 차 0 이었다(``docs/byo/phase1-equivalence.md``).
전체 수집(일봉·주식수·업종지수)은 ``run.py`` 가 :attr:`KiwoomBroker.api` 로 직접 부른다 — 다른 증권사에는 없는 길이다.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date

from sectorflow.storage import INVESTOR_COLUMNS

from .. import fetch
from ..auth import KEY_ENV, SECRET_ENV, load_keys
from ..kiwoom import KiwoomRest
from .base import SD_FIELDS, VENUE_KRX, Capability, Settings

#: ka10059 한 쪽의 거래일 수(실측 약 100).
SD_PAGE_DAYS = 100
__all__ = ["KEY_ENV", "SECRET_ENV", "CAPABILITY", "KiwoomBroker", "make", "rest", "sd_pages"]
#: TR 하나의 기본 속도[req/s] — quant-airflow ``--rate`` 와 같다.
DEFAULT_RATE = 0.9

CAPABILITY = Capability(
    actors=frozenset(INVESTOR_COLUMNS),
    venue=VENUE_KRX,
    verified=True,
    terms_note="키움: 받은 시세·수급은 본인 이용만, 제3자 제공 금지(약관 제5조③). 과부하는 이용 중지·해지 사유(제8·9조).",
)


def sd_pages(days: int) -> int:
    """``days`` 거래일을 덮는 ka10059 쪽 수 — 한 쪽 더 받아 둔다(쪽 경계가 휴장일로 밀려도 모자라지 않게)."""
    return math.ceil(days / SD_PAGE_DAYS) + 1


class KiwoomBroker:
    """:class:`..kiwoom.KiwoomRest` 세션 하나를 어댑터 모양으로."""

    name = "kiwoom"
    capability = CAPABILITY
    list_label = "ka10099"
    flow_label = "ka10059"
    lists_stocks = True

    def __init__(self, api: KiwoomRest):
        self.api = api

    @property
    def calls(self) -> Counter:
        return self.api.calls

    @property
    def retries(self) -> Counter:
        return self.api.retries

    @property
    def reissues(self) -> int:
        return self.api.reissues

    @property
    def total_calls(self) -> int:
        return self.api.total_calls

    def universe(self) -> list[dict]:
        """ka10099 두 시장(거래소·코스닥) → 보통주만. 업종은 키움 업종명(``upName``), 출처 ``kiwoom``."""
        return [{**s, "sector_source": self.name}
                for m in fetch.MARKETS for s in fetch.stock_list(self.api, m) if fetch.is_common_stock(s)]

    def investor_daily(self, code: str, start: str, end: str, *, days: "int | None" = None,
                       info: "dict | None" = None) -> list[dict]:
        """ka10059 — ``end`` 부터 과거로, ``start`` 에 닿으면 멈춘다. ``days`` 가 없으면 달력 일수로 넉넉히 잡는다."""
        oldest, anchor = start.replace("-", ""), end.replace("-", "")
        pages = sd_pages(days if days is not None else _calendar_days(start, end))
        rows = fetch.investor_flow(self.api, code, anchor, max_pages=pages, oldest=oldest, info=info)
        return [dict(zip(SD_FIELDS, t)) for t in fetch.sd_records(code, rows, oldest=oldest)]


def _calendar_days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def rest(s: Settings, only: "tuple[str, ...] | None" = None) -> KiwoomRest:
    """설정 → 키움 세션. ``only`` 는 TR 제한(지도 전용 수집은 ka10099·ka10059 만)."""
    key, secret = load_keys(s.env_file)
    t = s.transport
    transport = (lambda url, h, b, timeout: t("POST", url, h, b, timeout)) if t is not None else None
    return KiwoomRest(key, secret, rate=DEFAULT_RATE if s.rate is None else s.rate, max_calls=s.max_calls,
                      guard=s.guard, only=only, notice=s.notice, transport=transport)


def make(s: Settings) -> KiwoomBroker:
    return KiwoomBroker(rest(s))
