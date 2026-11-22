"""키움 밖 어댑터의 공통 HTTP 세션 — 표준 라이브러리만(``urllib``). 허용 경로·속도·호출 상한·운영 창·재시도.

키움 클라이언트(:mod:`..kiwoom`)와 같은 원칙을 증권사마다 다시 쓰지 않으려고 한 곳에 둔다:

- **허용 경로**(``allowed``) 밖의 (메서드, 경로)는 요청 전에 :class:`ForbiddenPath` — 주문·계좌 경로는 어느 어댑터 목록에도 없다
  (``tests/test_collect_broker_http.py`` 가 정적으로도 본다).
- **속도**: 요청 사이 최소 간격(``rate`` req/s)과 모든 요청 합의 (횟수, 창[초]) 상한(``limits``). 창이 차면 가장 오래된
  전송이 창을 벗어날 때까지 기다린다.
- **호출 상한**(``max_calls``) — 넘으면 :class:`..kiwoom.CallBudgetExceeded`. 토큰 발급도 센다.
- **운영 창**(``guard``) — 요청 직전마다 부른다(:mod:`..busy`).
- **재시도**: 어댑터가 준 ``rate_limited(status, body)`` 가 참이면 선형 백오프로 ``max_retries`` 번.

토큰 발급·갱신과 응답 해석은 증권사마다 달라 어댑터가 한다. 키·토큰은 :func:`..auth.mask` 로 가린 뒤에만 메시지에 싣는다.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, deque
from typing import Callable

from ..auth import mask
from ..kiwoom import CallBudgetExceeded

#: ``transport(method, url, headers, body, timeout) -> (status, response_headers, json_body)``.
#: GET 이면 ``body`` 를 쿼리 문자열로, POST 면 JSON 본문으로 보낸다.
HttpTransport = Callable[[str, str, dict, dict, float], "tuple[int, dict[str, str], dict]"]


class BrokerError(RuntimeError):
    def __init__(self, broker: str, what: str, code: object, message: str):
        self.broker, self.what, self.code = broker, what, code
        super().__init__(f"{broker} {what} {code}: {message}")


class ForbiddenPath(BrokerError):
    """허용 목록 밖의 경로 — 요청을 보내지 않았다."""


def urllib_transport(method: str, url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict, dict]:
    """기본 전송. 4xx/5xx 도 예외 대신 ``(status, headers, body)`` 로 돌려준다."""
    data = None
    if method == "GET":
        if body:
            url = url + "?" + urllib.parse.urlencode(body)
    else:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 어댑터의 고정 https 호스트
            raw, status, hdrs = resp.read(), resp.status, dict(resp.headers.items())
    except urllib.error.HTTPError as e:
        raw, status, hdrs = e.read() or b"", e.code, dict(e.headers.items()) if e.headers else {}
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = {"_raw": raw[:200].decode("utf-8", "replace")}
    return status, {k.lower(): v for k, v in hdrs.items()}, parsed if isinstance(parsed, dict) else {"_list": parsed}


class HttpSession:
    """증권사 REST 세션 하나의 전송 회계.

    Args:
        broker: 어댑터 이름(메시지용).
        base_url: ``https://…`` 호스트.
        allowed: 부를 수 있는 ``(메서드, 경로)`` 전부.
        rate: 요청 사이 최소 간격의 역수[req/s] — 어댑터의 보수적 기본값. ``None`` 이면 간격 없이 ``limits`` 만.
        limits: 모든 요청 합의 (횟수, 창[초]) 상한 — 증권사가 공지한 값.
        max_calls: 이 세션의 총 호출 상한(토큰 발급 포함). ``None`` 이면 무제한.
        secrets: 메시지에서 가릴 값(앱키·시크릿) — 토큰은 :meth:`hide` 로 더한다.
    """

    def __init__(self, broker: str, base_url: str, *, allowed: "frozenset[tuple[str, str]]",
                 limits: "tuple[tuple[int, float], ...]", rate: "float | None" = None, max_calls: "int | None" = None,
                 guard: "Callable[[], None] | None" = None, notice: "Callable[[str], None] | None" = None,
                 transport: "HttpTransport | None" = None, max_retries: int = 5, backoff: float = 1.1,
                 timeout: float = 30.0, secrets: tuple[str, ...] = (),
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.broker, self.base_url = broker, base_url.rstrip("/")
        self.allowed, self.limits = frozenset(allowed), tuple(limits)
        self.min_interval = 1.0 / rate if rate else 0.0
        self.max_calls, self.max_retries, self.backoff, self.timeout = max_calls, max_retries, backoff, timeout
        self._guard, self._notice = guard, notice
        self._transport = transport or urllib_transport
        self._clock, self._sleep = clock, sleep
        self._secrets = [s for s in secrets if s]
        self._sent: deque = deque(maxlen=max((n for n, _ in self.limits), default=1))
        self.calls: Counter = Counter()
        self.retries: Counter = Counter()
        self.reissues = 0

    def __repr__(self) -> str:  # 키·토큰을 싣지 않는다
        return f"HttpSession({self.broker}, calls={self.total_calls})"

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())

    def hide(self, secret: str) -> None:
        """메시지에서 가릴 값을 더한다(새 토큰)."""
        if secret:
            self._secrets.append(secret)

    def masked(self, text: object) -> str:
        return mask(text, *self._secrets)[:300]

    def _throttle(self) -> None:
        now = self._clock()
        wait = self.min_interval - (now - self._sent[-1]) if self._sent and self.min_interval else 0.0
        for n, span in self.limits:
            if len(self._sent) >= n:
                wait = max(wait, self._sent[-n] + span - now)
        if wait > 0:
            if self._notice is not None and wait >= 30.0:
                self._notice(f"⏳ {self.broker} 호출 상한 — {wait:.0f}초 기다린다")
            self._sleep(wait)

    def _spend(self, what: str) -> None:
        if self._guard is not None:
            self._guard()
        if self.max_calls is not None and self.total_calls >= self.max_calls:
            raise CallBudgetExceeded(f"호출 상한 {self.max_calls} 에 닿았다({dict(self.calls)})")
        self.calls[what] += 1
        self._sent.append(self._clock())

    def send(self, method: str, path: str, headers: dict, body: dict, *, what: str,
             rate_limited: "Callable[[int, dict], bool] | None" = None) -> "tuple[int, dict[str, str], dict]":
        """요청 하나 — 허용 경로 확인 → (속도 제한에 걸리면 재시도하며) 보낸다. ``what`` 은 원장의 호출 칸 이름."""
        if (method, path) not in self.allowed:
            raise ForbiddenPath(self.broker, what, "-", f"허용 목록 밖의 경로 {method} {path} — 시세·수급 조회만 부른다")
        attempt = 0
        while True:
            self._throttle()
            self._spend(what)
            status, rh, data = self._transport(method, self.base_url + path, headers, body, self.timeout)
            if rate_limited is not None and rate_limited(status, data) and attempt < self.max_retries:
                attempt += 1
                self.retries[what] += 1
                self._sleep(self.backoff * attempt)
                continue
            return status, rh, data
