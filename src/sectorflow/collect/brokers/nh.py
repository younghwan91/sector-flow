"""NH투자증권 PLUG 어댑터 — ``currentInvestor`` 주식현재가 투자자. **미검증, 기관계만.**

조사 ``docs/byo/brokers.md`` §2.6·§4. 응답 모양은 공개 OpenAPI 문서(``www.nhplug.com/openapi-docs/krstock/openapi.json``)와
``llms.txt`` 를 보고 손으로 쓴 최소 표본으로 시험했다(``tests/test_collect_db_nh.py``). 실제 키로 대조하기 전까지 ``verified=False``.

- **주체가 셋뿐이고 순매수 수량만**: 개인 ``person`` · 외국인 ``frgn_ntby_qty``(외국인순매수량 — 같은 행의 ``invest``
  외국인투자자순매수량과의 차이는 미확인) · 기관계 ``gigwan``. 기관 세부·기타법인·국가는 **NULL** — 기타법인을 잔여로 메우지 않는다.
- 토큰: ``POST /oauth2/token``(폼: ``appkey``·``appsecretkey``·``grant_type=client_credentials``·``scope=oob``). 문서는 "재발급은
  보안 알림 유발" 이라 토큰을 캐시하라고 하지만 이 도구는 토큰을 디스크에 두지 않는다 — **실행마다 한 번** 받는다(시작 때 알린다).
- 수급: ``POST /krstock/quote/v1/currentInvestor``, 본문 ``Input_0`` = ``market_cd=KRX``(KRX 단독)·``iem_cd``·``array_cnt``.
  **기준일을 줄 수 없다** — 최신일부터 ``array_cnt`` 행(``Output_0``)이고, 더 과거는 응답 *머리* ``cts``·``cts_flag``(Y)를 다음 요청 머리에
  그대로 실어 잇는다. ``cts`` 가 직전과 같으면 멈춘다. 블록이 없으면(데이터 없음·실패) ``rsp_msg`` 를 싣고 실패로 센다.
- 종목 목록 REST 가 없다(마스터 ``.mst`` 파일) — 저장된 ``stocks`` 를 쓴다(한국투자와 같다).
- 상한: REST 초당 5건, "초과 시 재시도 대신 간격을 늘리라" — 기본 초당 4건.
"""

from __future__ import annotations

import time
from collections import Counter

from ..auth import load_pair
from .base import VENUE_KRX, Capability, NoListing, Settings, iso8, opt_int, sd_row
from .http import BrokerError, HttpSession

NAME = "nh"
PROD_URL = "https://api.nhplug.com:8443"
TOKEN_PATH = "/oauth2/token"
FLOW_PATH, FLOW_TR = "/krstock/quote/v1/currentInvestor", "currentInvestor"
ALLOWED = frozenset({("POST", TOKEN_PATH), ("POST", FLOW_PATH)})
KEY_ENV, SECRET_ENV = "NH_APP_KEY", "NH_APP_SECRET"
MARKET = "KRX"
LIMITS: tuple[tuple[int, float], ...] = ((5, 1.0),)
DEFAULT_RATE = 4.0
ARRAY_CNT = 100

FIELDS: dict[str, str] = {"individual": "person", "foreign_": "frgn_ntby_qty", "institution": "gigwan"}

CAPABILITY = Capability(
    actors=frozenset(FIELDS),
    venue=VENUE_KRX,
    verified=False,
    terms_note="NH투자증권: 약관을 확인하지 못했다 — 받은 시세는 본인만 쓴다 · 주체는 개인·외국인·기관계 셋뿐(순매수 수량).",
)
TOKEN_NOTICE = "ℹ️ NH PLUG 토큰을 이번 실행에 한 번 받는다 — NH 는 재발급마다 보안 알림을 보낼 수 있다(토큰은 디스크에 두지 않는다)."


def flow_rows(code: str, rows: list[dict], start: str, end: str) -> list[dict]:
    out: dict[str, dict] = {}
    for r in rows:
        dt = str(r.get("bsop_date1", "")).strip()
        if len(dt) != 8 or not dt.isdigit():
            continue
        d = iso8(dt)
        if not (start <= d <= end) or d in out:
            continue
        close = opt_int(r.get("stck_prpr"))
        out[d] = sd_row(code, d, close=abs(close) if close is not None else None, flu_rt=None,
                        acc_trde_qty=opt_int(r.get("acml_vol")),
                        actors={col: opt_int(r.get(key)) for col, key in FIELDS.items()}, source=NAME)
    return [out[d] for d in sorted(out, reverse=True)]


class NhBroker:
    name = NAME
    capability = CAPABILITY
    list_label = "-"
    flow_label = FLOW_TR
    lists_stocks = False

    def __init__(self, app_key: str, app_secret: str, http: HttpSession, *, wall=None):
        self._key, self._secret, self.http = app_key, app_secret, http
        self._wall = wall or time.time
        self._token: "str | None" = None
        self._expires: "float | None" = None

    def __repr__(self) -> str:
        return f"NhBroker(calls={self.total_calls})"

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

    def _post(self, body: dict, cts: "tuple[str, str] | None") -> "tuple[dict, dict]":
        refreshed = False
        while True:
            if self._token is None or (self._expires is not None and self._wall() >= self._expires - 300):
                self._issue()
            headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {self._token}"}
            if cts:
                headers.update(cts=cts[0], cts_flag=cts[1])
            status, rh, data = self.http.send("POST", FLOW_PATH, headers, body, what=FLOW_TR,
                                              rate_limited=lambda st, d: st == 429)
            if status == 401 and not refreshed and self.http.reissues < 1:   # 재발급은 401 일 때만(문서)
                refreshed = True
                self.http.reissues += 1
                self._token = None
                continue
            if status != 200:
                raise BrokerError(NAME, FLOW_TR, data.get("rsp_cd") or status, self.http.masked(data.get("rsp_msg", "")))
            return data, rh

    def universe(self) -> list[dict]:
        raise NoListing("NH 는 전 종목 목록 REST 가 없다 — 저장된 stocks 를 쓴다")

    def investor_daily(self, code: str, start: str, end: str, *, days: "int | None" = None,
                       info: "dict | None" = None) -> list[dict]:
        body = {"Input_0": {"market_cd": MARKET, "iem_cd": code, "array_cnt": str(ARRAY_CNT)}}
        need = days if days is not None else 250
        max_pages = need // ARRAY_CNT + 3            # 기준일을 못 줘 끝날 뒤(오늘 쪽) 행도 받는다 — 한 쪽 더
        rows: list[dict] = []
        cts, last, more, page = None, None, False, 0
        for page in range(1, max_pages + 1):
            data, rh = self._post(body, cts)
            got = data.get("Output_0")
            if got is None:
                if rows:
                    break
                raise BrokerError(NAME, FLOW_TR, data.get("rsp_cd", "-"), self.http.masked(data.get("rsp_msg", "")))
            got = [got] if isinstance(got, dict) else got
            rows.extend(got)
            oldest = min((str(r.get("bsop_date1", "")) for r in got), default="")
            nxt = (str(rh.get("cts", "")), str(rh.get("cts_flag", ""))) if str(rh.get("cts_flag", "")).upper() == "Y" \
                else None
            if not got or oldest <= start.replace("-", "") or nxt is None or nxt[0] == last:
                break
            if page == max_pages:
                more = True
                break
            cts, last = nxt, nxt[0]
        if info is not None:
            info.update(pages=page, more=more)
        return flow_rows(code, rows, start, end)


def make(s: Settings) -> NhBroker:
    key, secret = load_pair(s.env_file, KEY_ENV, SECRET_ENV)
    if s.notice is not None:
        s.notice(TOKEN_NOTICE)
    rate = DEFAULT_RATE if s.rate is None else min(s.rate, float(LIMITS[0][0]))
    http = HttpSession(NAME, PROD_URL, allowed=ALLOWED, limits=LIMITS, rate=rate, max_calls=s.max_calls,
                       guard=s.guard, notice=s.notice, transport=s.transport, secrets=(key, secret))
    return NhBroker(key, secret, http)
