"""DB증권 OPEN API 어댑터 — ``DAYSTOCKTJJ`` 일별종목별투자자조회. **미검증, 기관계만.**

조사 ``docs/byo/brokers.md`` §2.5·§4. 응답 모양은 포털 API 메타와 공식 샘플 저장소(``DBsecurities/dbsec-open-api``)의 필드 이름표를
보고 손으로 쓴 최소 표본으로 시험했다(``tests/test_collect_db_nh.py``). 실제 키로 대조하기 전까지 ``verified=False``.

- **주체가 셋뿐이다**: 개인(``Prsn``)·외국인(``FrgnReg`` — 등록외국인)·기관계(``Orgn``) 매수·매도 수량 → 순매수 = 매수 − 매도.
  기관 세부·기타법인·국가는 없다 → **NULL**(0 이 아니다). 기타법인을 잔여(−(개인+외국인+기관))로 메우지 않는다 — 잔여에는 미등록외국인·
  국가·기타법인이 섞이고 네 주체 합 ≈ 0 가정이 깨진다(§4).
- 토큰: ``POST /oauth2/token``(폼: ``appkey``·``appsecretkey``·``grant_type=client_credentials``·``scope=oob``).
- 수급: ``POST /api/v1/quote/kr-stock/inquiry/daily-investor``, 본문 ``In`` = ``InputCondMrktDivCode=J``(KRX 단독)·``InputIscd1``·
  ``InputDate1``~``InputDate2``(시작·끝으로 읽었다 — 순서 미확인). 응답 ``Out`` 이 일별 행. 머리 ``cont_yn: Y``(+``cont_key``)면
  다음 쪽, 아니면 ``InputDate2`` 를 받은 가장 오래된 날의 앞날로 옮긴다.
- 종목 목록: ``JCODES``(``/api/v1/quote/kr-stock/inquiry/stock-ticker``, ``J``) — ``MrktClsCode`` 4 코스피·1 코스닥, 업종 칸이 없어
  섹터는 저장된 것을 둔다.
- 상한: DAYSTOCKTJJ 초당 2건(포털 ``ThroughputQuotaRule``), JCODES 3건 — 합쳐 초당 2건.
"""

from __future__ import annotations

import time
from collections import Counter

from .. import fetch
from ..auth import load_pair
from .base import VENUE_KRX, Capability, Settings, iso8, opt_int, sd_row, walk_back
from .http import BrokerError, HttpSession

NAME = "db"
PROD_URL = "https://openapi.dbsec.co.kr:8443"
TOKEN_PATH = "/oauth2/token"
FLOW_PATH, FLOW_TR = "/api/v1/quote/kr-stock/inquiry/daily-investor", "DAYSTOCKTJJ"
LIST_PATH, LIST_TR = "/api/v1/quote/kr-stock/inquiry/stock-ticker", "JCODES"
ALLOWED = frozenset({("POST", TOKEN_PATH), ("POST", FLOW_PATH), ("POST", LIST_PATH)})
KEY_ENV, SECRET_ENV = "DB_APP_KEY", "DB_APP_SECRET"
MARKET_DIV = "J"
LIMITS: tuple[tuple[int, float], ...] = ((2, 1.0),)
DEFAULT_RATE = 2.0
PAGE_ROWS_GUESS = 20
MARKETS = {"4": "거래소", "1": "코스닥"}

#: sector-flow 칸 ← (매수 수량, 매도 수량). 이 셋 밖의 칸은 NULL.
FIELDS: dict[str, tuple[str, str]] = {
    "individual": ("PrsnShnuVol", "PrsnSelnVol"),
    "foreign_": ("FrgnRegShnuVol", "FrgnRegSelnVol"),
    "institution": ("OrgnShnuVol", "OrgnSelnVol"),
}

CAPABILITY = Capability(
    actors=frozenset(FIELDS),
    venue=VENUE_KRX,
    verified=False,
    terms_note="DB증권: 약관을 확인하지 못했다(포털 세션 필요) — 받은 시세는 본인만 쓴다 · 주체는 개인·외국인(등록)·기관계 셋뿐.",
)


def _net(r: dict, keys: tuple[str, str]) -> "int | None":
    buy, sell = opt_int(r.get(keys[0])), opt_int(r.get(keys[1]))
    return None if buy is None or sell is None else buy - sell


def flow_rows(code: str, rows: list[dict], start: str, end: str) -> list[dict]:
    """``Out`` 행 → 수급 행(``start``~``end``). 순매수 = 매수 − 매도, 둘 중 하나라도 비면 NULL."""
    out: dict[str, dict] = {}
    for r in rows:
        dt = str(r.get("Date", "")).strip()
        if len(dt) != 8 or not dt.isdigit():
            continue
        d = iso8(dt)
        if not (start <= d <= end) or d in out:
            continue
        close = opt_int(r.get("Prpr"))
        out[d] = sd_row(code, d, close=abs(close) if close is not None else None, flu_rt=None,
                        acc_trde_qty=opt_int(r.get("AcmlVol")),
                        actors={col: _net(r, keys) for col, keys in FIELDS.items()}, source=NAME)
    return [out[d] for d in sorted(out, reverse=True)]


class DbBroker:
    name = NAME
    capability = CAPABILITY
    list_label = LIST_TR
    flow_label = FLOW_TR
    lists_stocks = True

    def __init__(self, app_key: str, app_secret: str, http: HttpSession, *, wall=None):
        self._key, self._secret, self.http = app_key, app_secret, http
        self._wall = wall or time.time
        self._token: "str | None" = None
        self._expires: "float | None" = None

    def __repr__(self) -> str:
        return f"DbBroker(calls={self.total_calls})"

    @property
    def calls(self) -> Counter:
        return self.http.calls

    @property
    def retries(self) -> Counter:
        return self.http.retries

    @property
    def reissues(self) -> int:
        return self.http.reissues

    @property
    def total_calls(self) -> int:
        return self.http.total_calls

    def _issue(self) -> None:
        status, _, data = self.http.send(
            "POST", TOKEN_PATH, {"content-type": "application/x-www-form-urlencoded"},
            {"grant_type": "client_credentials", "appkey": self._key, "appsecretkey": self._secret, "scope": "oob"},
            what="token")
        token = data.get("access_token")
        if status != 200 or not token:
            raise BrokerError(NAME, "token", data.get("rsp_cd") or status,
                              self.http.masked(data.get("rsp_msg") or data.get("error_description") or "토큰 발급 실패"))
        self._token = token
        self.http.hide(token)
        try:
            self._expires = self._wall() + float(data["expires_in"])
        except (KeyError, TypeError, ValueError):
            self._expires = None

    def _post(self, path: str, tr: str, body: dict, cont: "str | None" = None) -> "tuple[dict, dict]":
        refreshed = False
        while True:
            if self._token is None or (self._expires is not None and self._wall() >= self._expires - 300):
                self._issue()
            headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {self._token}",
                       "cont_yn": "Y" if cont else "N", "cont_key": cont or ""}
            status, rh, data = self.http.send("POST", path, headers, body, what=tr,
                                              rate_limited=lambda st, d: st == 429)
            if status == 401 and not refreshed and self.http.reissues < 1:
                refreshed = True
                self.http.reissues += 1
                self._token = None
                continue
            if status != 200:
                raise BrokerError(NAME, tr, data.get("rsp_cd") or status, self.http.masked(data.get("rsp_msg", "")))
            return data, rh

    @staticmethod
    def _more(rh: dict) -> "str | None":
        if str(rh.get("cont_yn", "")).upper() != "Y":
            return None
        return rh.get("cont_key", "") or None

    def universe(self) -> list[dict]:
        rows: list[dict] = []
        cont = None
        for _ in range(20):
            data, rh = self._post(LIST_PATH, LIST_TR, {"In": {"InputCondMrktDivCode": MARKET_DIV}}, cont)
            rows.extend(data.get("Out") or [])
            cont = self._more(rh)
            if cont is None:
                break
        else:
            raise fetch.TruncatedResponse(f"{LIST_TR}: 20쪽에서 잘렸다({len(rows)}행)")
        out = []
        for r in rows:
            code, market = str(r.get("Iscd", "")).strip(), MARKETS.get(str(r.get("MrktClsCode", "")).strip())
            s = {"code": code, "name": str(r.get("KorIsnm", "")).strip(), "market": market or "", "sector": "",
                 "kind": "", "sector_source": None}
            if market and len(code) == 6 and code.isdigit() and fetch.is_common_stock(s):
                out.append(s)
        if not out:
            raise fetch.EmptyResponse(f"{LIST_TR}: 종목 0개")
        return out

    def investor_daily(self, code: str, start: str, end: str, *, days: "int | None" = None,
                       info: "dict | None" = None) -> list[dict]:
        def page(anchor: str, cont):
            body = {"In": {"InputCondMrktDivCode": MARKET_DIV, "InputIscd1": code,
                           "InputDate1": start.replace("-", ""), "InputDate2": anchor}}
            data, rh = self._post(FLOW_PATH, FLOW_TR, body, cont)
            rows = data.get("Out") or []
            rows = [rows] if isinstance(rows, dict) else rows
            return rows, [str(r.get("Date", "")) for r in rows], self._more(rh)

        rows = walk_back(page, start, end, days=days, rows_per_page=PAGE_ROWS_GUESS, info=info)
        return flow_rows(code, rows, start, end)


def make(s: Settings) -> DbBroker:
    key, secret = load_pair(s.env_file, KEY_ENV, SECRET_ENV)
    rate = DEFAULT_RATE if s.rate is None else min(s.rate, DEFAULT_RATE)
    http = HttpSession(NAME, PROD_URL, allowed=ALLOWED, limits=LIMITS, rate=rate, max_calls=s.max_calls,
                       guard=s.guard, notice=s.notice, transport=s.transport, secrets=(key, secret))
    return DbBroker(key, secret, http)
