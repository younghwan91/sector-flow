"""LS증권 OPEN API 어댑터 — ``t1702`` 외인기관종목별동향. **미검증**. 약관 경고를 받아들여야(``--i-understand-ls-terms``) 돈다.

조사 ``docs/byo/brokers.md`` §2.3·§4. 응답 모양은 포털 API 메타의 요청·응답 예시와 블록 필드 목록(2차 미러)을 보고 손으로 쓴
최소 표본으로 시험했다(``tests/test_collect_ls.py``). 실제 키로 키움과 대조하기 전까지 ``verified=False``.

- **약관**: LS OPEN API 이용약관 제9조① 은 "고객이 3개월간 상품별 구분 없이 OPEN API를 통한 주문체결이 20회 이하인 경우" 를
  이용 중지·해지 사유로 든다 — 수집만 하는 사용자는 문구상 여기에 걸린다(시정 요구 뒤 해지, 실제 운용은 미확인). 그래서 시작할
  때 이 조항을 그대로 찍고, ``--i-understand-ls-terms`` 없이는 요청 전에 멈춘다.
- 토큰: ``POST /oauth2/token``(폼: ``grant_type=client_credentials``·``appkey``·``appsecretkey``·``scope=oob``) → ``access_token``.
- 수급: ``POST /stock/frgr-itt``, 머리 ``tr_cd: t1702``. 본문 ``t1702InBlock`` = 종목 ``shcode`` · ``fromdt``~``todt`` ·
  ``volvalgb=1``(수량) · ``msmdgb=0``(순매수) · ``gubun=0``(일간) · ``exchgubun=K``(KRX 단독). 응답 ``t1702OutBlock1`` 이 일별 행이다.
  한 번에 몇 행인지·연속조회 방식은 미확인이라 응답 머리 ``tr_cont: Y``(+``tr_cont_key``)면 그것으로, 아니면 ``todt`` 를 받은 가장
  오래된 날의 앞날로 옮겨 잇는다.
- 대응(§4, 1:1): 사모 ``tjj0000`` · 증권→금융투자 ``tjj0001`` · 보험 ``tjj0002`` · 투신 ``tjj0003`` · 은행 ``tjj0004`` ·
  기금→연기금 ``tjj0006`` · 기타법인 ``tjj0007`` · 개인 ``tjj0008`` · 국가외→국가 ``tjj0011`` · 기관 ``tjj0018`` ·
  외국인 ``tjj0016``(외인계 = 등록+미등록). 종금 ``tjj0005``(키움 기타금융)은 11칸에 없다.
- 종목 목록: ``POST /stock/etc``, ``tr_cd: t8436``(``gubun`` 1 코스피·2 코스닥) — 업종 칸이 없어 섹터는 저장된 것을 둔다
  (``write.resolve_sector``). ETF·ETN(``etfgubun`` ≠ 0)과 끝자리가 0 이 아닌 코드(우선주)는 뺀다.
- 상한: t1702 개인 초당 1건(포털 ``ThroughputQuotaRule``), t8436 초당 2건 — 둘을 합쳐 초당 1건으로 보낸다.
"""

from __future__ import annotations

import time
from collections import Counter

from .. import fetch
from ..auth import load_pair
from .base import VENUE_KRX, Capability, Settings, iso8, opt_int, sd_row, walk_back
from .http import BrokerError, HttpSession

NAME = "ls"
PROD_URL = "https://openapi.ls-sec.co.kr:8080"
TOKEN_PATH = "/oauth2/token"
FLOW_PATH, FLOW_TR = "/stock/frgr-itt", "t1702"
LIST_PATH, LIST_TR = "/stock/etc", "t8436"
ALLOWED = frozenset({("POST", TOKEN_PATH), ("POST", FLOW_PATH), ("POST", LIST_PATH)})
KEY_ENV, SECRET_ENV = "LS_APP_KEY", "LS_APP_SECRET"
#: KRX 단독(``N`` NXT · ``U`` 통합은 쓰지 않는다).
EXCHANGE = "K"
#: 개인 t1702 초당 1건 — 이것이 전체 속도다.
LIMITS: tuple[tuple[int, float], ...] = ((1, 1.0),)
DEFAULT_RATE = 1.0
PAGE_ROWS_GUESS = 20
#: 시장(``t8436`` ``gubun``) → ``stocks.market``(키움 ``marketName`` 과 같은 글자).
MARKETS = {"1": "거래소", "2": "코스닥"}

#: sector-flow 칸 ← LS ``t1702OutBlock1`` 키.
FIELDS: dict[str, str] = {
    "individual": "tjj0008",
    "foreign_": "tjj0016",
    "institution": "tjj0018",
    "fnnc_invt": "tjj0001",
    "insrnc": "tjj0002",
    "invtrt": "tjj0003",
    "bank": "tjj0004",
    "penfnd_etc": "tjj0006",
    "samo_fund": "tjj0000",
    "natn": "tjj0011",
    "etc_corp": "tjj0007",
}

#: 약관 원문(제9조①) — 시작할 때 그대로 찍는다.
TERMS_CLAUSE = "고객이 3개월간 상품별 구분 없이 OPEN API를 통한 주문체결이 20회 이하인 경우"
TERMS_WARNING = (f"⚠️ LS증권 OPEN API 이용약관 제9조①은 \"{TERMS_CLAUSE}\" 를 이용 중지·해지 사유로 든다. 이 도구는 주문을 하지 "
                 "않으므로 수집만 하면 이 조항에 걸릴 수 있다(시정 요구 뒤 해지 — 실제 운용은 확인하지 못했다).")
TERMS_FLAG = "--i-understand-ls-terms"

CAPABILITY = Capability(
    actors=frozenset(FIELDS),
    venue=VENUE_KRX,
    verified=False,
    terms_note=("LS증권: 데이터 양도·매도와 다른 서비스를 위한 시세 재사용 금지(약관 제9조①) · "
                "3개월 주문체결 20회 이하는 해지 사유 — 시작 때 경고, --i-understand-ls-terms 필요."),
)


class TermsNotAccepted(SystemExit):
    """LS 약관 경고를 받아들이지 않았다 — 요청을 보내지 않았다."""


def flow_rows(code: str, rows: list[dict], start: str, end: str) -> list[dict]:
    """``t1702OutBlock1`` 행 → 수급 행(``start``~``end``). 같은 날이 두 번 오면 처음 것."""
    out: dict[str, dict] = {}
    for r in rows:
        dt = str(r.get("date", "")).strip()
        if len(dt) != 8 or not dt.isdigit():
            continue
        d = iso8(dt)
        if not (start <= d <= end) or d in out:
            continue
        close = opt_int(r.get("close"))
        out[d] = sd_row(code, d, close=abs(close) if close is not None else None, flu_rt=None,
                        acc_trde_qty=opt_int(r.get("volume")),
                        actors={col: opt_int(r.get(key)) for col, key in FIELDS.items()}, source=NAME)
    return [out[d] for d in sorted(out, reverse=True)]


def list_rows(rows: list[dict]) -> list[dict]:
    """``t8436OutBlock`` → ``stocks`` 행(보통주만, 섹터 없음)."""
    out = []
    for r in rows:
        code, market = str(r.get("shcode", "")).strip(), MARKETS.get(str(r.get("gubun", "")).strip())
        if (market and len(code) == 6 and code.isdigit() and code.endswith("0")
                and str(r.get("etfgubun", "0")).strip() == "0"):
            out.append({"code": code, "name": str(r.get("hname", "")).strip(), "market": market, "sector": "",
                        "kind": "", "sector_source": None})
    return out


class LsBroker:
    """LS REST 세션 하나(토큰 하나)."""

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
        return f"LsBroker(calls={self.total_calls})"

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

    def _post(self, path: str, tr: str, body: dict, cont: "tuple[str, str] | None" = None) -> "tuple[dict, dict]":
        refreshed = False
        while True:
            if self._token is None or (self._expires is not None and self._wall() >= self._expires - 300):
                self._issue()
            headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {self._token}",
                       "tr_cd": tr, "tr_cont": cont[0] if cont else "N", "tr_cont_key": cont[1] if cont else ""}
            status, rh, data = self.http.send("POST", path, headers, body, what=tr,
                                              rate_limited=lambda st, d: st == 429)
            if status == 401 and not refreshed and self.http.reissues < 1:
                refreshed = True
                self.http.reissues += 1
                self._token = None
                continue
            if status != 200 or str(data.get("rsp_cd", "00000")) != "00000":
                raise BrokerError(NAME, tr, data.get("rsp_cd") or status, self.http.masked(data.get("rsp_msg", "")))
            return data, rh

    def universe(self) -> list[dict]:
        out: list[dict] = []
        for gubun in MARKETS:
            data, _ = self._post(LIST_PATH, LIST_TR, {"t8436InBlock": {"gubun": gubun}})
            got = list_rows(data.get("t8436OutBlock") or [])
            if not got:
                raise fetch.EmptyResponse(f"{LIST_TR} gubun={gubun}: 종목 0개")
            out.extend(got)
        return out

    def investor_daily(self, code: str, start: str, end: str, *, days: "int | None" = None,
                       info: "dict | None" = None) -> list[dict]:
        def fetch(anchor: str, cont):
            body = {"t1702InBlock": {"shcode": code, "fromdt": start.replace("-", ""), "todt": anchor,
                                     "volvalgb": "1", "msmdgb": "0", "gubun": "0", "exchgubun": EXCHANGE}}
            data, rh = self._post(FLOW_PATH, FLOW_TR, body, cont)
            rows = data.get("t1702OutBlock1") or []
            nxt = ("Y", rh.get("tr_cont_key", "")) if str(rh.get("tr_cont", "")).upper() == "Y" else None
            return rows, [str(r.get("date", "")) for r in rows], nxt

        rows = walk_back(fetch, start, end, days=days, rows_per_page=PAGE_ROWS_GUESS, info=info)
        return flow_rows(code, rows, start, end)


def make(s: Settings) -> LsBroker:
    if s.notice is not None:
        s.notice(TERMS_WARNING)
    if NAME not in s.accepted_terms:
        raise TermsNotAccepted(f"⏹ LS 약관 경고를 읽고 받아들이면 {TERMS_FLAG} 를 붙여 다시 친다 — 요청은 보내지 않았다")
    key, secret = load_pair(s.env_file, KEY_ENV, SECRET_ENV)
    rate = DEFAULT_RATE if s.rate is None else min(s.rate, DEFAULT_RATE)
    http = HttpSession(NAME, PROD_URL, allowed=ALLOWED, limits=LIMITS, rate=rate, max_calls=s.max_calls,
                       guard=s.guard, notice=s.notice, transport=s.transport, secrets=(key, secret))
    return LsBroker(key, secret, http)
