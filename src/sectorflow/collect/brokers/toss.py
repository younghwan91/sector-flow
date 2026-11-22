"""토스증권 Open API 어댑터 — 투자자별 매매동향(``/api/v1/stocks/{symbol}/investor-trading``). **미검증, KRX+NXT 통합.**

조사 ``docs/byo/brokers.md`` §2.4·§4. 응답 모양은 공개 OpenAPI 문서(``openapi.tossinvest.com/openapi-docs``)의 스키마·예시를 보고
손으로 쓴 최소 표본으로 시험했다(``tests/test_collect_toss.py``). 실제 키로 대조하기 전까지 ``verified=False``.

- **거래소 기준이 다르다.** 토스의 투자자별 거래량은 "KRX·NXT 통합 기준" 이고 거래소를 고를 수 없다. sector-flow 의 다른 어댑터·
  리포트는 KRX 단독이라 같은 날 숫자가 다르다 → ``venue="KRX+NXT"``, 페이로드·지도 표지는 ``basis.DATA_BASIS_INTEGRATED``,
  그리고 KRX 단독 행이 든 sqlite 에 섞어 넣지 않는다(``run._guard_venue`` 가 수집 전에 거부).
- **외국인은 등록외국인만**(미등록 미포함) — 키움 외국인과 기준이 다르다. 국가 칸이 없다(결측).
- 토큰: ``POST /oauth2/token``(폼: ``grant_type=client_credentials``·``client_id``·``client_secret``) → ``access_token``.
  client 당 유효 토큰이 하나라 재발급하면 앞 토큰이 무효가 된다 — 재발급 상한 1.
- 수급: ``count=100``, ``until``(YYYY-MM-DD, 포함) → ``result.records``(최신순)·``result.nextUntil``. 값은 ``netBuyVolume``(주) 문자열.
  당일 잠정 기록의 개인·기관 세부·기타법인은 ``null`` — 그대로 NULL(모름)이다.
- 대응(§4): 개인 ``individual`` · 외국인 ``foreigner`` · 기관 ``institution`` · 세부 ``breakdown``: 금융투자 ``financialInvestment`` ·
  보험 ``insurance`` · 투신 ``trust`` · 사모 ``privateEquityFund`` · 은행 ``bank`` · 연기금 ``pensionFund`` · 기타법인
  ``otherCorporation``. ``otherFinancialInstitution``(기타금융)은 11칸에 없다.
- **종가가 매매동향에 없다.** 수량 × 종가로 금액을 복원하므로 일봉(``/api/v1/candles``, ``interval=1d``, ``adjusted=false`` — 원 종가,
  키움 ``supply_demand.close`` 와 같은 뜻)을 따로 받아 날짜로 붙인다. 일봉이 없는 날은 종가 NULL. 일봉의 거래소 기준은 미확인.
- 종목 목록: ``/api/v1/stocks/all?market=KOSPI|KOSDAQ&securityType=STOCK&commonShare=true``. **토스 섹터는 쓰지 않는다** — 자체
  분류이고 한 종목이 여러 섹터에 든다(지금의 "한 종목 한 섹터" 합계와 맞지 않는다). 섹터는 저장된 것을 둔다.
- 상한: 그룹별 초당 — 매매동향 10, 일봉 20, 전 종목 1, 토큰 5. 기본은 보수적으로 합쳐 초당 5건. 허용 IP 등록이 필요하다(아니면 403).
"""

from __future__ import annotations

import math
import time
from collections import Counter

from .. import fetch
from ..auth import load_pair
from .base import VENUE_KRX_NXT, Capability, Settings, opt_int, sd_row
from .http import BrokerError, HttpSession

NAME = "toss"
PROD_URL = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"
FLOW_PATH = "/api/v1/stocks/{symbol}/investor-trading"
CANDLE_PATH = "/api/v1/candles"
LIST_PATH = "/api/v1/stocks/all"
ALLOWED = frozenset({("POST", TOKEN_PATH), ("GET", FLOW_PATH), ("GET", CANDLE_PATH), ("GET", LIST_PATH)})
KEY_ENV, SECRET_ENV = "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"
#: 공지된 그룹 상한 중 가장 낮은 수집 그룹(전 종목 1)은 두 번만 부른다 — 합계는 매매동향(10) 아래로 잡는다.
LIMITS: tuple[tuple[int, float], ...] = ((10, 1.0),)
DEFAULT_RATE = 5.0
FLOW_COUNT, CANDLE_COUNT = 100, 200
PAGE_ROWS_FLOOR = 20
MARKETS = {"KOSPI": "거래소", "KOSDAQ": "코스닥"}

#: sector-flow 칸 ← 토스 기록의 경로(투자자 키, 기관 세부 키).
FIELDS: dict[str, tuple[str, ...]] = {
    "individual": ("individual",),
    "foreign_": ("foreigner",),
    "institution": ("institution",),
    "fnnc_invt": ("institution", "breakdown", "financialInvestment"),
    "insrnc": ("institution", "breakdown", "insurance"),
    "invtrt": ("institution", "breakdown", "trust"),
    "bank": ("institution", "breakdown", "bank"),
    "penfnd_etc": ("institution", "breakdown", "pensionFund"),
    "samo_fund": ("institution", "breakdown", "privateEquityFund"),
    "etc_corp": ("otherCorporation",),
}

CAPABILITY = Capability(
    actors=frozenset(FIELDS),
    venue=VENUE_KRX_NXT,
    verified=False,
    terms_note=("토스증권: 받은 정보는 본인의 매매 목적에만, 비상업적이어도 제3자 배포 금지(FAQ 데이터 이용 정책) · "
                "수급은 KRX+NXT 통합, 외국인은 등록외국인만."),
)


def net(rec: dict, path: tuple[str, ...]) -> "int | None":
    """기록에서 ``path`` 의 ``netBuyVolume`` — 중간이 ``null`` 이면 ``None``(모름)."""
    cur = rec
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return opt_int(cur.get("netBuyVolume")) if isinstance(cur, dict) else None


def flow_rows(code: str, records: list[dict], candles: dict[str, tuple], start: str, end: str) -> list[dict]:
    """매매동향 기록 + 날짜별 ``(원 종가, 거래량)`` → 수급 행(``start``~``end``)."""
    out: dict[str, dict] = {}
    for rec in records:
        d = str(rec.get("date", "")).strip()[:10]
        if not (start <= d <= end) or d in out:
            continue
        close, vol = candles.get(d, (None, None))
        out[d] = sd_row(code, d, close=abs(close) if close is not None else None, flu_rt=None, acc_trde_qty=vol,
                        actors={col: net(rec, path) for col, path in FIELDS.items()}, source=NAME)
    return [out[d] for d in sorted(out, reverse=True)]


class TossBroker:
    """토스 REST 세션 하나(토큰 하나)."""

    name = NAME
    capability = CAPABILITY
    list_label = "stocks/all"
    flow_label = "investor-trading"
    lists_stocks = True

    def __init__(self, client_id: str, client_secret: str, http: HttpSession, *, wall=None):
        self._id, self._secret, self.http = client_id, client_secret, http
        self._wall = wall or time.time
        self._token: "str | None" = None
        self._expires: "float | None" = None

    def __repr__(self) -> str:
        return f"TossBroker(calls={self.total_calls})"

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
            {"grant_type": "client_credentials", "client_id": self._id, "client_secret": self._secret}, what="token")
        token = data.get("access_token")
        if status != 200 or not token:
            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            raise BrokerError(NAME, "token", err.get("code") or data.get("error") or status,
                              self.http.masked(err.get("message") or data.get("error_description") or "토큰 발급 실패"))
        self._token = token
        self.http.hide(token)
        try:
            self._expires = self._wall() + float(data["expires_in"])
        except (KeyError, TypeError, ValueError):
            self._expires = None

    def _get(self, path: str, params: dict, what: str) -> dict:
        refreshed = False
        while True:
            if self._token is None or (self._expires is not None and self._wall() >= self._expires - 300):
                self._issue()
            status, _, data = self.http.send("GET", path, {"authorization": f"Bearer {self._token}"}, params,
                                             what=what, rate_limited=lambda st, d: st == 429)
            if status == 401 and not refreshed and self.http.reissues < 1:
                refreshed = True
                self.http.reissues += 1
                self._token = None
                continue
            if status != 200 or "result" not in data:
                err = data.get("error") if isinstance(data.get("error"), dict) else {}
                raise BrokerError(NAME, what, err.get("code") or status, self.http.masked(err.get("message", "")))
            return data["result"]

    def universe(self) -> list[dict]:
        out: list[dict] = []
        for market, label in MARKETS.items():
            got = self._get(LIST_PATH, {"market": market, "status": "ACTIVE", "securityType": "STOCK",
                                        "commonShare": "true"}, self.list_label) or []
            rows = [{"code": str(r.get("symbol", "")).strip(), "name": str(r.get("name", "")).strip(), "market": label,
                     "sector": "", "kind": "", "sector_source": None} for r in got
                    if r.get("securityType") == "STOCK" and r.get("isCommonShare") is True]
            rows = [r for r in rows if fetch.is_common_stock(r)]
            if not rows:
                raise fetch.EmptyResponse(f"stocks/all {market}: 종목 0개")
            out.extend(rows)
        return out

    def _candles(self, code: str, start: str, end: str) -> dict[str, tuple]:
        """``start``~``end`` 의 원 종가 일봉 → ``{날짜: (종가, 거래량)}``. ``nextBefore`` 로 과거로 잇는다."""
        out: dict[str, tuple] = {}
        before = f"{end}T23:59:59+09:00"
        for _ in range(50):
            got = self._get(CANDLE_PATH, {"symbol": code, "interval": "1d", "count": CANDLE_COUNT, "adjusted": "false",
                                          "before": before}, "candles")
            candles = got.get("candles") or []
            for c in candles:
                d = str(c.get("timestamp", ""))[:10]
                if start <= d <= end and d not in out:
                    out[d] = (opt_int(c.get("closePrice")), opt_int(c.get("volume")))
            oldest = min((str(c.get("timestamp", ""))[:10] for c in candles), default="")
            nxt = got.get("nextBefore")
            if not candles or not nxt or oldest <= start:
                break
            before = nxt
        return out

    def investor_daily(self, code: str, start: str, end: str, *, days: "int | None" = None,
                       info: "dict | None" = None) -> list[dict]:
        records: list[dict] = []
        until, page, more = end, 0, False
        need = days if days is not None else 400
        # 한 쪽은 문서상 count(100)까지다 — 덜 오는 경우에 대비해 쪽 상한은 넉넉히(20행 가정) 둔다.
        max_pages = math.ceil(need / PAGE_ROWS_FLOOR) + 2
        for page in range(1, max_pages + 1):
            got = self._get(FLOW_PATH.replace("{symbol}", code), {"count": FLOW_COUNT, "until": until}, self.flow_label)
            recs = got.get("records") or []
            records.extend(recs)
            oldest = min((str(r.get("date", ""))[:10] for r in recs), default="")
            nxt = got.get("nextUntil")
            if not recs or not nxt or oldest <= start:
                break
            if page == max_pages:
                more = True
                break
            until = str(nxt)[:10]
        if info is not None:
            info.update(pages=page, more=more)
        dates = sorted({str(r.get("date", ""))[:10] for r in records if start <= str(r.get("date", ""))[:10] <= end})
        candles = self._candles(code, dates[0], dates[-1]) if dates else {}
        return flow_rows(code, records, candles, start, end)


def make(s: Settings) -> TossBroker:
    cid, secret = load_pair(s.env_file, KEY_ENV, SECRET_ENV)
    rate = DEFAULT_RATE if s.rate is None else min(s.rate, float(LIMITS[0][0]))
    http = HttpSession(NAME, PROD_URL, allowed=ALLOWED, limits=LIMITS, rate=rate, max_calls=s.max_calls,
                       guard=s.guard, notice=s.notice, transport=s.transport, secrets=(cid, secret))
    return TossBroker(cid, secret, http)

