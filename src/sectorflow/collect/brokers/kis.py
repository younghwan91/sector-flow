"""한국투자증권 KIS Developers 어댑터 — ``FHPTJ04160001`` 종목별 투자자매매동향(일별). **미검증**.

조사 ``docs/byo/brokers.md`` §2.2·§4. 응답 모양은 공개 포털 메타와 공식 GitHub 예제의 필드 이름표를 보고 손으로 쓴 최소 표본으로
시험했다(``tests/test_collect_kis.py``). 실제 키로 키움과 대조(``sector-flow-collect equiv``)하기 전까지 ``verified=False``.

- 토큰: ``POST /oauth2/tokenP``(앱키·시크릿) → ``access_token``. 만료(``access_token_token_expired``, KST) 5분 전이나 무효 토큰
  응답(``EGW00121``·``EGW00123``)에 한 번 다시 받는다. 토큰 발급은 분당 1회로 알려져 있어 재발급 상한은 1이다.
- 수급: ``GET /uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily``, ``FID_COND_MRKT_DIV_CODE=J``(KRX 단독 —
  키움 접미사 없는 코드와 같은 기준). 응답 ``output2`` 가 일별 행이다. 한 번에 몇 행인지 미확인이라 두 길로 잇는다: 응답 헤더
  ``tr_cont`` 가 ``M``·``F`` 면 요청 헤더 ``tr_cont: N`` 으로 다음 쪽, 아니면 받은 가장 오래된 날 앞날로 ``FID_INPUT_DATE_1`` 를 옮긴다.
- 대응(§4): 개인 ``prsn_ntby_qty`` · 외국인 ``frgn_ntby_qty``(등록+비등록) · 기관계 ``orgn_ntby_qty`` · 증권→금융투자
  ``scrt_ntby_qty`` · 보험 ``insu_ntby_qty`` · 투자신탁 ``ivtr_ntby_qty`` · 은행 ``bank_ntby_qty`` · 기금→연기금 ``fund_ntby_qty`` ·
  사모 ``pe_fund_ntby_vol`` · 기타법인 ``etc_corp_ntby_vol``. **국가는 결측** — ``etc_ntby_qty``(기타)·``etc_orgt_ntby_vol``(기타단체)의
  뜻이 확인되지 않아 국가로 넣지 않는다. 종금(``mrbn``, 키움 기타금융)은 sector-flow 11칸에 없다.
- 빈 칸은 0 이 아니라 ``None``(모름). 수량(주) 단위다(응답에 대금도 있으나 키움과 같게 수량을 쓴다). 등락률은 단위가 키움 ``flu_rt``
  와 달라 싣지 않는다(``None``) — 지도는 쓰지 않는다.
- 상한: 블로그가 인용한 포털 공지는 실전 초당 18건(원문 미확인). 기본은 보수적으로 초당 5건(``--rate`` 로 바꾸되 18 을 넘지 않는다).
- 이 TR 은 **모의투자 미지원** — 실전 앱키 전용이다. 주문 경로는 허용 목록에 없다.
- 전 종목 목록 REST 가 없다(마스터 ``.mst`` 파일). 종목 목록은 저장된 ``stocks`` 를 쓴다(:attr:`KisBroker.lists_stocks` 거짓) —
  키움 키로 ``universe --broker kiwoom`` 을 받거나 ``sectors --csv … --add-missing`` 으로 넣는다.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from ..auth import load_pair
from .base import VENUE_KRX, Capability, NoListing, Settings, iso8, opt_int, sd_row, walk_back
from .http import BrokerError, HttpSession

NAME = "kis"
PROD_URL = "https://openapi.koreainvestment.com:9443"
TOKEN_PATH = "/oauth2/tokenP"
FLOW_PATH = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
FLOW_TR = "FHPTJ04160001"
ALLOWED = frozenset({("POST", TOKEN_PATH), ("GET", FLOW_PATH)})
KEY_ENV, SECRET_ENV = "KIS_APP_KEY", "KIS_APP_SECRET"
#: 키움 거래소와 같은 기준 — KRX 단독(``NX`` NXT · ``UN`` 통합은 쓰지 않는다).
MARKET_DIV = "J"
#: 포털 공지(블로그 인용) 실전 초당 18건 — 넘지 않는다. 기본 속도는 보수적으로 5.
LIMITS: tuple[tuple[int, float], ...] = ((18, 1.0),)
DEFAULT_RATE = 5.0
#: 무효·만료 토큰 / 초당 거래건수 초과.
TOKEN_ERRORS = frozenset({"EGW00121", "EGW00123"})
RATE_ERRORS = frozenset({"EGW00201"})
#: 한 응답의 행 수 — 미확인이라 작게 잡아 쪽 상한을 넉넉히 둔다.
PAGE_ROWS_GUESS = 30
EXPIRY_MARGIN_S = 300.0
KST = timezone(timedelta(hours=9))

#: sector-flow 칸 ← KIS 응답 키(``docs/byo/brokers.md`` §4). ``natn`` 은 없다.
FIELDS: dict[str, str] = {
    "individual": "prsn_ntby_qty",
    "foreign_": "frgn_ntby_qty",
    "institution": "orgn_ntby_qty",
    "fnnc_invt": "scrt_ntby_qty",
    "insrnc": "insu_ntby_qty",
    "invtrt": "ivtr_ntby_qty",
    "bank": "bank_ntby_qty",
    "penfnd_etc": "fund_ntby_qty",
    "samo_fund": "pe_fund_ntby_vol",
    "etc_corp": "etc_corp_ntby_vol",
}

CAPABILITY = Capability(
    actors=frozenset(FIELDS),
    venue=VENUE_KRX,
    verified=False,
    terms_note=("한국투자: 시세는 본인 업무에만, 제3자 제공 금지(약관 제5조③) · 1년 이용 실적이 없으면 해지 사유(제10조) · "
                "이 TR 은 모의투자 미지원(실전 키 전용)."),
)
LIVE_KEY_WARNING = ("⚠️ 한국투자 FHPTJ04160001 은 모의투자를 지원하지 않는다 — 실전 앱키가 필요하다. "
                    "이 도구는 시세·수급 조회만 부른다(주문 경로는 허용 목록에 없다).")


def flow_rows(code: str, rows: list[dict], start: str, end: str) -> list[dict]:
    """``output2`` 행 → 수급 행(``start``~``end``, YYYY-MM-DD). 같은 날이 두 번 오면(쪽이 겹치면) 처음 것."""
    out: dict[str, dict] = {}
    for r in rows:
        dt = str(r.get("stck_bsop_date", "")).strip()
        if len(dt) != 8 or not dt.isdigit():
            continue
        d = iso8(dt)
        if not (start <= d <= end) or d in out:
            continue
        close = opt_int(r.get("stck_clpr"))
        out[d] = sd_row(code, d, close=abs(close) if close is not None else None, flu_rt=None,
                        acc_trde_qty=opt_int(r.get("acml_vol")),
                        actors={col: opt_int(r.get(key)) for col, key in FIELDS.items()}, source=NAME)
    return [out[d] for d in sorted(out, reverse=True)]


def _expiry(data: dict, wall: float) -> "float | None":
    s = str(data.get("access_token_token_expired") or "").strip()
    if s:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST).timestamp()
        except ValueError:
            pass
    try:
        return wall + float(data["expires_in"])
    except (KeyError, TypeError, ValueError):
        return None


class KisBroker:
    """KIS REST 세션 하나(토큰 하나)."""

    name = NAME
    capability = CAPABILITY
    list_label = "-"
    flow_label = FLOW_TR
    #: 전 종목 목록 REST 가 없다 — ``run.py`` 가 저장된 ``stocks`` 를 쓴다.
    lists_stocks = False

    def __init__(self, app_key: str, app_secret: str, http: HttpSession, *, wall=None):
        self._key, self._secret, self.http = app_key, app_secret, http
        self._wall = wall or time.time
        self._token: "str | None" = None
        self._expires: "float | None" = None

    def __repr__(self) -> str:
        return f"KisBroker(calls={self.total_calls})"

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

    # ── 토큰 ──────────────────────────────────────────────────────────────
    def _issue(self) -> None:
        status, _, data = self.http.send("POST", TOKEN_PATH, {"content-type": "application/json; charset=utf-8"},
                                         {"grant_type": "client_credentials", "appkey": self._key,
                                          "appsecret": self._secret}, what="token")
        token = data.get("access_token")
        if status != 200 or not token:
            raise BrokerError(NAME, "token", data.get("error_code") or data.get("msg_cd") or status,
                              self.http.masked(data.get("error_description") or data.get("msg1") or "토큰 발급 실패"))
        self._token = token
        self.http.hide(token)
        self._expires = _expiry(data, self._wall())

    def _valid_token(self) -> str:
        if self._token is None or (self._expires is not None and self._wall() >= self._expires - EXPIRY_MARGIN_S):
            self._issue()
        assert self._token is not None
        return self._token

    def _get(self, params: dict, tr_cont: str) -> "tuple[dict, dict]":
        refreshed = False
        while True:
            headers = {"content-type": "application/json; charset=utf-8",
                       "authorization": f"Bearer {self._valid_token()}", "appkey": self._key,
                       "appsecret": self._secret, "tr_id": FLOW_TR, "tr_cont": tr_cont, "custtype": "P"}
            status, rh, data = self.http.send(
                "GET", FLOW_PATH, headers, params, what=FLOW_TR,
                rate_limited=lambda st, d: st == 429 or str(d.get("msg_cd", "")) in RATE_ERRORS)
            code = str(data.get("msg_cd", ""))
            if (status in (401, 403) or code in TOKEN_ERRORS) and not refreshed and self.http.reissues < 1:
                refreshed = True
                self.http.reissues += 1
                self._token = None
                continue
            if status != 200 or str(data.get("rt_cd", "")) != "0":
                raise BrokerError(NAME, FLOW_TR, code or status, self.http.masked(data.get("msg1", "")))
            return data, rh

    # ── 어댑터 ────────────────────────────────────────────────────────────
    def universe(self) -> list[dict]:
        raise NoListing("한국투자는 전 종목 목록 REST 가 없다 — 저장된 stocks 를 쓴다")

    def investor_daily(self, code: str, start: str, end: str, *, days: "int | None" = None,
                       info: "dict | None" = None) -> list[dict]:
        def fetch(anchor: str, cont):
            params = {"FID_COND_MRKT_DIV_CODE": MARKET_DIV, "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": anchor,
                      "FID_ORG_ADJ_PRC": "", "FID_ETC_CLS_CODE": ""}
            data, rh = self._get(params, "N" if cont else "")
            rows = data.get("output2") or []
            rows = [rows] if isinstance(rows, dict) else rows
            more = str(rh.get("tr_cont", "")).upper() in ("M", "F")    # 서버 연속조회 — 다음 요청 헤더 tr_cont: N
            return rows, [str(r.get("stck_bsop_date", "")) for r in rows], (True if more else None)

        rows = walk_back(fetch, start, end, days=days, rows_per_page=PAGE_ROWS_GUESS, info=info)
        return flow_rows(code, rows, start, end)


def make(s: Settings) -> KisBroker:
    key, secret = load_pair(s.env_file, KEY_ENV, SECRET_ENV)
    rate = DEFAULT_RATE if s.rate is None else min(s.rate, float(LIMITS[0][0]))
    if s.notice is not None:
        s.notice(LIVE_KEY_WARNING)
    http = HttpSession(NAME, PROD_URL, allowed=ALLOWED, limits=LIMITS, rate=rate, max_calls=s.max_calls,
                       guard=s.guard, notice=s.notice, transport=s.transport, secrets=(key, secret))
    return KisBroker(key, secret, http)
