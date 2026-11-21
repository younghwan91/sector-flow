"""키움 REST 읽기 전용 클라이언트 — 표준 라이브러리만(``urllib``).

quant-airflow 는 ``kiwoom-client`` 라이브러리를 쓰지만 BYO 도구는 그것을 import 하지 않는다. 그 라이브러리에는
주문 API 가 있고(설계 §4.2 "주문 연동 금지"), 연속조회 키를 응답 **헤더**에서 읽지 못하던 판도 있었다
(quant-airflow ``collectors/supply_demand.py`` docstring). 여기서는 필요한 것만 직접 한다.

- **허용 TR 목록**(:data:`TR_PATHS`) 밖의 ``api-id`` 는 요청 전에 :class:`ForbiddenTR` 로 막는다.
- **토큰 1개.** 같은 앱키로 따로 로그인하면 앞 토큰이 무효가 된다(``8005``, quant-airflow ``CLAUDE.md`` §4).
  키움은 무효 토큰에 401 이 아니라 ``HTTP 200 {"return_code": 3}`` 을 준다(kiwoom-client ``base.py`` 실측 주석).
  그때 한 번 다시 발급하고, 같은 요청에서 또 나면 :class:`TokenConflict` — 같은 키를 쓰는 다른 프로그램이 있다.
- **TR별 속도 제한.** 키움 제한은 TR(api-id)마다 따로다(지속 약 1 req/s, 버스트 2). 기본 0.9 req/s.
- **429 / return_code 5** 는 선형 백오프로 재시도한다(quant-airflow 와 같은 ``max_retries=5``).
- **연속조회**는 응답 헤더 ``cont-yn``·``next-key`` 로 한다.
- **호출 상한**(``max_calls``) — 넘으면 :class:`CallBudgetExceeded`. 토큰 발급도 센다.
- **분·시간 창**(:data:`KIWOOM_LIMITS`, 앱키 하나에서 모든 TR 합: 초 5 · 분 100 · 시간 1,000). 창이 차면 가장 오래된
  전송이 창을 벗어날 때까지 기다린다(멈추지 않는다 — 전 시장 증분은 몇 시간 걸린다). 기다린 뒤에도 운영 창 검사는 한다.
- **TR 제한**(``only``) — 지도 전용 수집은 ka10099·ka10059 만. 그 밖은 요청 전에 :class:`ForbiddenTR`.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .auth import mask

PROD_URL = "https://api.kiwoom.com"

#: 이 도구가 부르는 TR 전부와 그 경로. **주문·계좌 경로(/ordr·/acnt·/crdordr)는 없다.** 늘릴 때는 읽기 전용
#: 시세·수급 TR 인지 확인하고 ``tests/test_collect_kiwoom.py`` 의 허용 목록 검사를 같이 고친다.
TR_PATHS: dict[str, str] = {
    "ka10099": "/api/dostk/stkinfo",  # 종목정보 리스트 → stocks
    "ka10059": "/api/dostk/stkinfo",  # 투자자기관별종목별 → supply_demand
    "ka10001": "/api/dostk/stkinfo",  # 종목기본정보(상장주식수) → shares_outstanding_history
    "ka10081": "/api/dostk/chart",    # 주식일봉차트 → daily_bars·_adjusted·_raw·corporate_actions
    "ka20006": "/api/dostk/chart",    # 업종일봉차트 → sector_index
    "ka20003": "/api/dostk/sect",     # 전업종지수(업종 코드·이름)
}
TOKEN_PATH = "/oauth2/token"
#: 무효·만료 토큰. HTTP 200 본문에 실려 온다.
TOKEN_ERROR_CODES = frozenset({3})
#: "허용된 요청 개수를 초과" — 429 와 같이 다룬다.
RATE_ERROR_CODES = frozenset({5})
#: 만료 이만큼 전에 새로 발급한다[초].
EXPIRY_MARGIN_S = 300.0
#: 한 실행에서 허용하는 토큰 재발급 횟수. 같은 키로 다른 프로그램이 로그인하면 서로의 토큰을 계속 밀어낸다
#: (요청마다 1회씩 허용하면 둘이 무한히 핑퐁한다). 넘으면 :class:`TokenConflict`.
MAX_REISSUES = 2
#: 키움 ``expires_dt`` 는 KST 현지 시각이다 — 호스트 시간대와 무관하게 읽는다.
KST = timezone(timedelta(hours=9))

#: 키움 REST 호출 상한 — (횟수, 창[초]). 앱키 하나의 모든 요청(토큰 발급 포함) 합으로 센다(설계 §5).
KIWOOM_LIMITS: tuple[tuple[int, float], ...] = ((5, 1.0), (100, 60.0), (1000, 3600.0))
#: 이보다 오래 기다리면 ``notice`` 로 알린다[초] — 시간 창에 걸린 실행이 멈춘 것처럼 보이지 않게.
NOTICE_WAIT_S = 30.0

#: ``transport(url, headers, body, timeout) -> (status, response_headers, json_body)``.
Transport = Callable[[str, dict, dict, float], "tuple[int, dict[str, str], dict]"]


class KiwoomError(RuntimeError):
    def __init__(self, api_id: str, code: Any, message: str):
        self.api_id, self.code = api_id, code
        super().__init__(f"{api_id} return_code={code}: {message}")


class ForbiddenTR(KiwoomError):
    """허용 목록 밖의 TR — 요청을 보내지 않았다."""


class TokenConflict(KiwoomError):
    """새 토큰으로도 무효 — 같은 앱키로 다른 프로그램이 로그인해 이 토큰을 밀어냈다."""


class CallBudgetExceeded(RuntimeError):
    """설정한 호출 상한을 넘었다 — 요청을 보내지 않았다."""


class BusyWindow(RuntimeError):
    """같은 앱키를 쓰는 다른 프로그램의 운영 창에 들어섰다 — 요청을 보내지 않았다(다시 돌리면 이어 받는다)."""


def urllib_transport(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict, dict]:
    """기본 전송 — POST JSON. 4xx/5xx 도 예외 대신 ``(status, headers, body)`` 로 돌려준다."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 고정 https 호스트
            raw, status, hdrs = resp.read(), resp.status, dict(resp.headers.items())
    except urllib.error.HTTPError as e:
        raw, status, hdrs = e.read() or b"", e.code, dict(e.headers.items()) if e.headers else {}
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = {"_raw": raw[:200].decode("utf-8", "replace")}
    return status, {k.lower(): v for k, v in hdrs.items()}, parsed


class KiwoomRest:
    """읽기 전용 키움 REST 세션 하나(토큰 하나).

    Args:
        app_key, app_secret: 앱키. 객체 안에만 두고 ``repr``·로그에 찍지 않는다.
        rate: TR 하나의 지속 속도[req/s]. 기본 0.9(quant-airflow ``--rate`` 와 같다).
        max_retries: 429·return_code 5 재시도 횟수.
        backoff: 재시도 대기 기본값[초] — ``backoff × 시도 번호``.
        max_calls: 이 세션의 총 호출 상한(토큰 발급 포함). ``None`` 이면 무제한.
        max_reissues: 이 세션의 토큰 재발급 상한(만료 전 정기 갱신은 세지 않는다).
        guard: 요청(토큰 발급 포함) **직전마다** 부른다. 운영 창이면 :class:`BusyWindow` 를 던진다.
        limits: 모든 요청 합의 (횟수, 창[초]) 상한. 기본 :data:`KIWOOM_LIMITS`.
        only: 주면 이 TR 만 부른다(허용 목록 안에서 더 좁힌다) — 밖은 요청 전에 :class:`ForbiddenTR`.
        notice: 긴 대기(:data:`NOTICE_WAIT_S` 초 이상)를 알릴 곳.
        transport, clock, sleep: 테스트가 바꿔 끼운다.
    """

    def __init__(self, app_key: str, app_secret: str, *, base_url: str = PROD_URL,
                 rate: float = 0.9, max_retries: int = 5, backoff: float = 1.1,
                 max_calls: "int | None" = None, timeout: float = 30.0,
                 max_reissues: int = MAX_REISSUES,
                 guard: "Callable[[], None] | None" = None,
                 limits: "tuple[tuple[int, float], ...]" = KIWOOM_LIMITS,
                 only: "tuple[str, ...] | None" = None,
                 notice: "Callable[[str], None] | None" = None,
                 transport: "Transport | None" = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 wall: Callable[[], float] = time.time):
        self._key, self._secret = app_key, app_secret
        self.base_url = base_url.rstrip("/")
        self.min_interval = 1.0 / rate if rate else 0.0
        self.max_retries, self.backoff = max_retries, backoff
        self.max_calls, self.timeout = max_calls, timeout
        self._transport = transport or urllib_transport
        self._clock, self._sleep, self._wall = clock, sleep, wall
        self._token: "str | None" = None
        self._expires_at: "float | None" = None
        self._last: dict[str, float] = {}
        self.max_reissues, self.reissues = max_reissues, 0
        self._guard = guard
        self.limits, self.only, self._notice = tuple(limits), only, notice
        #: 최근 전송 시각(``clock``) — 가장 큰 창의 횟수만큼만 둔다.
        self._sent: deque = deque(maxlen=max((n for n, _ in self.limits), default=1))
        self.calls: Counter = Counter()
        self.retries: Counter = Counter()

    def __repr__(self) -> str:  # 키·토큰을 싣지 않는다
        return f"KiwoomRest(base_url={self.base_url!r}, calls={sum(self.calls.values())})"

    def _secrets(self) -> tuple[str, ...]:
        return (self._key, self._secret, self._token or "")

    # ── 호출 회계 ──────────────────────────────────────────────────────────
    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())

    def _spend(self, what: str) -> None:
        if self._guard is not None:
            self._guard()
        if self.max_calls is not None and self.total_calls >= self.max_calls:
            raise CallBudgetExceeded(f"호출 상한 {self.max_calls} 에 닿았다({dict(self.calls)})")
        self.calls[what] += 1
        self._sent.append(self._clock())

    def _throttle(self, key: str) -> None:
        """TR별 간격과 합계 창 중 가장 긴 대기를 한 번 잔다 — 창의 n번째 최근 전송이 창을 벗어날 때까지."""
        now = self._clock()
        wait = 0.0
        last = self._last.get(key)
        if last is not None and self.min_interval:
            wait = self.min_interval - (now - last)
        for n, span in self.limits:
            if len(self._sent) >= n:
                wait = max(wait, self._sent[-n] + span - now)
        if wait > 0:
            if self._notice is not None and wait >= NOTICE_WAIT_S:
                self._notice(f"⏳ 호출 상한(초 5·분 100·시간 1,000) — {wait:.0f}초 기다린다")
            self._sleep(wait)
        self._last[key] = self._clock()

    # ── 토큰 ──────────────────────────────────────────────────────────────
    def issue_token(self) -> None:
        self._throttle(TOKEN_PATH)
        self._spend(TOKEN_PATH)
        status, _, data = self._transport(
            self.base_url + TOKEN_PATH, {"Content-Type": "application/json;charset=UTF-8"},
            {"grant_type": "client_credentials", "appkey": self._key, "secretkey": self._secret},
            self.timeout)
        token = data.get("token") or data.get("access_token")
        if status != 200 or not token:
            raise KiwoomError("oauth2/token", data.get("return_code", status),
                              mask(data.get("return_msg", "토큰 발급 실패"), self._key, self._secret))
        self._token = token
        self._expires_at = _parse_expiry(data, self._wall)

    def _valid_token(self) -> str:
        if (self._token is None or
                (self._expires_at is not None and self._wall() >= self._expires_at - EXPIRY_MARGIN_S)):
            self.issue_token()
        assert self._token is not None
        return self._token

    # ── 요청 ──────────────────────────────────────────────────────────────
    def request(self, api_id: str, body: dict, *, cont_yn: str = "N",
                next_key: str = "") -> tuple[dict, dict[str, str]]:
        """한 페이지. ``(본문, 응답 헤더)`` — 연속조회 키가 헤더에 있다."""
        path = TR_PATHS.get(api_id)
        if path is None:
            raise ForbiddenTR(api_id, "-", "허용 목록(TR_PATHS) 밖의 TR — 읽기 전용 시세·수급 TR 만 부른다")
        if self.only is not None and api_id not in self.only:
            raise ForbiddenTR(api_id, "-", f"이 실행은 {', '.join(self.only)} 만 부른다(지도 전용 수집)")
        refreshed = False
        attempt = 0
        while True:
            headers = {"Content-Type": "application/json;charset=UTF-8", "api-id": api_id,
                       "cont-yn": cont_yn, "next-key": next_key,
                       "authorization": f"Bearer {self._valid_token()}"}
            self._throttle(api_id)
            self._spend(api_id)
            status, rh, data = self._transport(self.base_url + path, headers, body, self.timeout)
            rc = data.get("return_code", 0) if isinstance(data, dict) else 0
            if (status == 429 or rc in RATE_ERROR_CODES) and attempt < self.max_retries:
                attempt += 1
                self.retries[api_id] += 1
                self._sleep(self.backoff * attempt)
                continue
            if status == 401 or rc in TOKEN_ERROR_CODES:
                if not refreshed and self.reissues < self.max_reissues:
                    refreshed = True
                    self.reissues += 1
                    self.retries[api_id] += 1
                    self.issue_token()
                    continue
                raise TokenConflict(api_id, rc or status,
                                    f"토큰이 무효다(이번 실행 재발급 {self.reissues}/{self.max_reissues}회) — "
                                    "같은 앱키로 로그인한 다른 프로그램이 있는지 확인하라")
            if status != 200:
                raise KiwoomError(api_id, status, mask(data, *self._secrets())[:200])
            if rc not in (0, None, "0"):
                raise KiwoomError(api_id, rc, mask(data.get("return_msg", ""), *self._secrets()))
            return data, rh

    def request_pages(self, api_id: str, body: dict, data_key: str, *,
                      max_pages: int = 1,
                      stop: "Callable[[list[dict]], bool] | None" = None,
                      info: "dict | None" = None) -> list[dict]:
        """``data_key`` 목록을 헤더 연속조회로 ``max_pages`` 까지 이어 붙인다.

        ``stop(page_rows)`` 가 참이면 다음 페이지를 받지 않는다(예: 원하는 날짜보다 오래된 행에 닿았다).
        ``info`` 를 주면 ``{"pages": n, "more": bool, "stopped": bool}`` 를 채운다 — ``more`` 는 서버가 다음 쪽이
        있다고 했는데 ``max_pages`` 에서 멈췄다는 뜻(잘림)이고, 끝까지 받았으면 거짓이다.
        """
        rows: list[dict] = []
        cont, nxt = "N", ""
        more = stopped = False
        page = 0
        for page in range(max_pages):
            data, rh = self.request(api_id, body, cont_yn=cont, next_key=nxt)
            got = data.get(data_key) or []
            rows.extend(got)
            if stop is not None and stop(got):
                stopped = True
                break
            cont_h, nxt_h = rh.get("cont-yn", "N"), rh.get("next-key", "")
            if cont_h != "Y" or not nxt_h:
                break
            if page == max_pages - 1:
                more = True
                break
            cont, nxt = "Y", nxt_h
        if info is not None:
            info.update(pages=page + 1, more=more, stopped=stopped)
        return rows


def _parse_expiry(data: dict, wall: Callable[[], float]) -> "float | None":
    """``expires_dt``(KST ``yyyyMMddHHmmss``) 또는 ``expires_in``[초] → 유닉스 시각."""
    if data.get("expires_dt"):
        try:
            return datetime.strptime(str(data["expires_dt"]), "%Y%m%d%H%M%S").replace(tzinfo=KST).timestamp()
        except ValueError:
            pass
    if data.get("expires_in") is not None:
        try:
            return wall() + float(data["expires_in"])
        except (TypeError, ValueError):
            pass
    return None
