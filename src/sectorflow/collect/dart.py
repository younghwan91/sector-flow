"""DART 발행주식총수 이력 — 시드(대안 B)가 못 보는 유상증자·CB 전환·소각을 분기 시점값으로 메운다(설계 §3 대안 A).

키움 ka10001 은 **현재** 상장주식수만 준다. Phase 1 은 그 값에 분할·병합·감자를 거꾸로 적용한 시드 한 행으로
과거 시총을 만들었고, 그 사이 주식수 변화를 못 봐서 섹터 수익률이 60일 창에서 최대 2.95%p 틀렸다
(``docs/byo/phase1-equivalence.md``). 이 모듈은 사용자 **본인의 무료 OpenDART 키**로 정기보고서의
"주식의 총수 현황"(``stockTotqySttus``)을 분기마다 받아 ``shares_outstanding_history`` 에 넣는다.

규약은 quant-airflow ``collectors/dart_shares.py``·krx-fundamentals-client ``DartScraper`` 와 같다.

- 값은 ``se == "보통주"`` 행의 ``istc_totqy``(발행주식총수). ``합계`` 는 우선주가 섞이고, ``distb_stock_co``
  (유통주식수)는 자기주식을 빼서 시총이 작아진다.
- 날짜는 응답의 ``stlm_dt``(그 수치의 기준일)다. 공시 접수일(``rcept_no`` 앞 8자리)이 아니다 — 키움 행과 같은
  "그날의 주식수" 의미여야 ``market_cap_asof_bulk`` 의 as-of 조회가 섞이지 않는다. 접수일은 원장에 남긴다.
- 원천의 단위 오류(×1,000·×1,000,000)는 고치지 않고 버린다(:func:`drop_unit_errors`).

그 뒤의 분할·병합·감자는 ``market_cap_asof_bulk`` 가 그 행 뒤 이벤트로 다시 센다 — 시드·키움 스냅숏과 같은
의미라 따로 손대지 않는다. 시드는 DART 행이 덮지 못한 앞 구간에만 남는다(:func:`sectorflow.collect.shares.reseed`).

**네트워크.** 목적지는 ``opendart.fss.or.kr`` 하나, 경로는 :data:`PATHS` 둘뿐이다. 키는 URL 쿼리(``crtfc_key``)로
가므로 URL 을 로그·예외에 싣지 않는다. 속도는 기본 1 req/s, 호출 상한을 둔다. 받은 결과는 ``dart_fetch`` 원장에
남겨 다시 돌리면 이미 받은 (종목, 연도, 보고서)는 건너뛴다.
"""

from __future__ import annotations

import io
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from xml.etree import ElementTree as ET

from .auth import mask
from .kiwoom import CallBudgetExceeded

BASE_URL = "https://opendart.fss.or.kr/api"
#: 이 도구가 부르는 DART 경로 전부. 늘릴 때는 ``tests/test_collect_dart.py`` 의 허용 목록 검사를 같이 고친다.
PATHS = frozenset({"corpCode.xml", "stockTotqySttus.json"})
#: 분기말 월 → 보고서 코드(사업 11011 · 반기 11012 · 1분기 11013 · 3분기 11014).
QUARTER_REPORT = {3: "11013", 6: "11012", 9: "11014", 12: "11011"}
OK, NO_DATA, QUOTA = "000", "013", "020"
#: 000 인데 보통주 값이 없다 — 1분기·3분기 보고서는 주식 총수 표를 ``-`` 로 비워 내는 일이 많다(2026-11-08 실측
#: 96종목: 2025 3분기 34 · 2026 1분기 52). 라벨을 가릴 수 없는 응답도 여기다. 013 처럼 기한이 지나면 확정으로 친다.
NO_COMMON = "no_common"
#: 키 자체가 안 되는 응답(미등록·사용 불가·IP 미허용 등) — 다음 요청도 같으므로 바로 멈춘다.
AUTH_ERRORS = frozenset({"010", "011", "012", "901"})
#: 보고서 기한(사업 90일·분기 45일)에 여유를 더한 것. 기준일에서 이만큼 지나 받은 "공시 없음"(013)만 확정으로 친다 —
#: 그 전에 받은 013 은 아직 안 낸 것일 수 있어 다음 실행이 다시 묻는다.
SETTLE_DAYS = 100
#: 발행주식총수 상한(삼성전자 약 5.97e9) — 넘으면 단위 오류다(quant-airflow ``MAX_PLAUSIBLE_SHARES``).
MAX_PLAUSIBLE_SHARES = 10_000_000_000
#: 같은 종목 다른 값의 중앙값보다 이 배수 이상 크고 1,000 의 배수면 단위 오류로 본다(quant-airflow 와 같다).
UNIT_ERROR_RATIO = 500

#: ``transport(url, timeout) -> (HTTP status, 본문 바이트)``.
Transport = Callable[[str, float], "tuple[int, bytes]"]


class DartError(RuntimeError):
    """DART 가 실행을 멈춰야 하는 status 를 줬다(일한도 소진·키 오류). 메시지에 키는 없다."""

    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"DART status={status}: {message}")


def urllib_transport(url: str, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "sector-flow-collect"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 고정 https 호스트
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""


class DartClient:
    """OpenDART 읽기 전용 세션. 키는 객체 안에만 두고 ``repr``·예외에 싣지 않는다.

    Args:
        rate: 지속 속도[req/s]. 기본 1.0.
        max_calls: 이 세션의 총 호출 상한(재시도 포함). 넘으면 :class:`CallBudgetExceeded` — 요청을 보내지 않았다.
        retries: 통신 오류·HTTP 5xx 재시도 횟수.
    """

    def __init__(self, key: str, *, rate: float = 1.0, max_calls: "int | None" = 1000,
                 retries: int = 2, backoff: float = 2.0, timeout: float = 30.0,
                 transport: "Transport | None" = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._key = key
        self.min_interval = 1.0 / rate if rate else 0.0
        self.max_calls, self.retries, self.backoff, self.timeout = max_calls, retries, backoff, timeout
        self._transport = transport or urllib_transport
        self._clock, self._sleep = clock, sleep
        self._last: "float | None" = None
        self.calls: Counter = Counter()

    def __repr__(self) -> str:
        return f"DartClient(calls={self.total_calls})"

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())

    def _spend(self, path: str) -> None:
        if self.max_calls is not None and self.total_calls >= self.max_calls:
            raise CallBudgetExceeded(f"DART 호출 상한 {self.max_calls} 에 닿았다({dict(self.calls)})")
        if self._last is not None and self.min_interval:
            wait = self.min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()
        self.calls[path] += 1

    def get(self, path: str, params: "dict | None" = None) -> bytes:
        """``path`` 한 번(재시도 포함). 본문 바이트. 통신 오류가 끝까지 나면 :class:`DartError`("net")."""
        if path not in PATHS:
            raise ValueError(f"허용 목록(PATHS) 밖의 DART 경로: {path}")
        query = urllib.parse.urlencode({"crtfc_key": self._key, **(params or {})})
        url = f"{BASE_URL}/{path}?{query}"
        err = ""
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(self.backoff * attempt)
            self._spend(path)
            try:
                status, body = self._transport(url, self.timeout)
            except (OSError, urllib.error.URLError) as e:  # URL(키 포함)을 싣지 않는다
                err = type(e).__name__
                continue
            if status >= 500:
                err = f"HTTP {status}"
                continue
            if status != 200:
                raise DartError(f"HTTP {status}", mask(body[:120].decode("utf-8", "replace"), self._key))
            return body
        raise DartError("net", f"{path} 재시도 {self.retries}회 뒤에도 실패({err})")

    def get_json(self, path: str, params: dict) -> dict:
        body = self.get(path, params)
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"status": "parse", "message": "JSON 아님"}
        return check(data, self._key)


def check(data: dict, key: str = "") -> dict:
    """실행을 멈춰야 하는 status(일한도 020·키 오류)는 :class:`DartError`, 나머지는 그대로 돌려준다."""
    st = str(data.get("status", ""))
    if st == QUOTA or st in AUTH_ERRORS:
        raise DartError(st, mask(str(data.get("message", "")), key))
    return data


def parse_corp_codes(body: bytes) -> dict[str, str]:
    """``corpCode.xml`` ZIP → ``{종목코드: corp_code}``. ZIP 이 아니면(오류 JSON) :class:`DartError`."""
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            xml = zf.read(zf.namelist()[0])
    except zipfile.BadZipFile:
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = {"status": "parse", "message": "ZIP 도 JSON 도 아님"}
        check(data)
        raise DartError(str(data.get("status", "")), f"corpCode 가 ZIP 이 아니다: {data.get('message', '')}")
    out: dict[str, str] = {}
    for item in ET.fromstring(xml).iter("list"):
        stock = (item.findtext("stock_code") or "").strip()
        corp = (item.findtext("corp_code") or "").strip()
        if stock and corp:
            out[stock] = corp
    return out


def corp_codes(client: DartClient) -> dict[str, str]:
    return parse_corp_codes(client.get("corpCode.xml"))


def _iso(yyyymmdd: str) -> str:
    s = yyyymmdd.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _int(v: object) -> int:
    try:
        return int(str(v).replace(",", "").strip())
    except ValueError:
        return 0


#: ``se`` 가 이 말을 담으면(공백 제거 뒤) 보통주 후보가 아니다 — 먼저 본다. ``합계`` 는 우선주가 섞이고,
#: ``의결권없`` 은 ``의결권 없는 주식(우선주)`` 꼴이다(krx-fundamentals-client ``_SHARES_LABEL_EXCLUDE`` 와 같다).
LABEL_EXCLUDE = ("합계", "비고", "우선", "종류", "기타", "의결권없")
#: 공백을 지운 ``se`` 가 이 말을 담으면 보통주 행이다. 실측 라벨: ``보통주`` · ``보통주식`` ·
#: ``의결권 있는 주식\n(보통주)`` · ``의결권 있는 주식`` · ``의결권이 있는 주식``(토 ``이`` 는 지우고 본다).
LABEL_COMMON = ("보통주", "의결권있는주식")


def parse_shares(data: dict) -> "tuple[int, str, str] | None":
    """``stockTotqySttus`` 응답 → ``(보통주 발행주식총수, 기준일 YYYY-MM-DD, 접수번호)``. 못 찾으면 ``None``.

    ``se``(구분) 표기가 회사마다 다르다(2026-11-08 실측 96종목 중 5종목이 ``보통주`` 가 아니었다). 라벨 규칙은
    krx-fundamentals-client ``_parse_shares_outstanding`` 과 같다 — kr_quant 와 같은 값을 받으려면 같아야 한다.
    **추측하지 않는다**: 보통주 후보가 둘 이상이거나 어느 쪽도 아닌 라벨(``유통주식`` 등)이 있으면 버린다.
    기준일이 없는 값도 쓰지 않는다 — as-of 조회에 놓을 자리가 없다.
    """
    if str(data.get("status")) != OK:
        return None
    cands, unknown = [], []
    for row in data.get("list") or []:
        label = "".join((row.get("se") or "").split()).replace("의결권이", "의결권")
        if any(w in label for w in LABEL_EXCLUDE):
            continue
        (cands if any(w in label for w in LABEL_COMMON) else unknown).append(row)
    if unknown or len(cands) != 1:
        return None
    row = cands[0]
    n = _int(row.get("istc_totqy"))
    stlm = _iso(str(row.get("stlm_dt") or ""))
    if n > 0 and len(stlm) == 10:
        return n, stlm, str(row.get("rcept_no") or "").strip()
    return None


def _month_end(y: int, m: int) -> date:
    return date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)


def _shift_quarter(y: int, m: int, k: int) -> tuple[int, int]:
    n = y * 12 + (m - 1) + 3 * k
    return n // 12, n % 12 + 1


def quarter_end(d: date) -> date:
    """``d`` 이하의 가장 늦은 분기말."""
    y, m = d.year, (d.month // 3) * 3
    if m == 0:
        y, m = y - 1, 12
    if _month_end(y, m) > d:
        y, m = _shift_quarter(y, m, -1)
    return _month_end(y, m)


def periods(start: str, end: str) -> list[tuple[int, str, str]]:
    """``start`` 를 덮는 분기말(그 이하 가장 늦은 것)부터 ``end`` 이하 마지막 분기말까지 ``(사업연도, 보고서 코드, 분기말)``.

    12월 결산이 아닌 회사도 같은 코드로 묻는다 — 저장 날짜는 요청한 분기말이 아니라 응답의 ``stlm_dt`` 다.
    """
    q = quarter_end(date.fromisoformat(start))
    last = quarter_end(date.fromisoformat(end))
    out = []
    while q <= last:
        out.append((q.year, QUARTER_REPORT[q.month], q.isoformat()))
        q = _month_end(*_shift_quarter(q.year, q.month, 1))
    return out


def settled(period_end: str, fetched_at: str) -> bool:
    """이때 받은 "공시 없음"(013)을 확정으로 칠 수 있는가 — 보고서 기한이 지났는가."""
    return date.fromisoformat(fetched_at[:10]) >= date.fromisoformat(period_end) + timedelta(days=SETTLE_DAYS)


def drop_unit_errors(points: list[tuple[str, int]], reference: "list[int] | tuple[int, ...]" = ()
                     ) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """``(남길 점, 버린 점)`` — 회사가 주식 총수를 천 주·백만 주 단위로 잘못 낸 값을 뺀다(나눠서 고치지 않는다).

    ``points`` = ``[(기준일, 주식수)]``, ``reference`` = 그 종목의 다른 원천 값(키움 스냅숏) — 점이 하나뿐일 때 기준이 된다.
    """
    pool = sorted([n for _, n in points] + list(reference))
    median = pool[len(pool) // 2] if pool else 0
    keep, drop = [], []
    for p in points:
        n = p[1]
        bad = n > MAX_PLAUSIBLE_SHARES or bool(median and n % 1000 == 0 and n >= UNIT_ERROR_RATIO * median)
        (drop if bad else keep).append(p)
    return keep, drop


#: 두 주식수가 "같다"고 볼 로그 차(약 2%). 키움 상장주식수는 천 주 단위로 반올림돼 온다.
SAME_SHARES_LOG = 0.02


def align_to_listing(points: list[tuple[str, int]], kiwoom: list[tuple[str, int]],
                     events: list[tuple[str, str, "float | None"]],
                     unresolved: "list[str] | None" = None) -> list[tuple[str, int, str]]:
    """DART 점이 **이미 반영한** 분할·병합·감자를 ``market_cap_asof_bulk`` 가 한 번 더 세지 않게 한다.

    발행주식총수(DART)는 효력발생일에, 상장주식수와 가격 조정(키움 이벤트일)은 변경상장일에 바뀐다. 그 사이에
    분기말이 끼면 DART 점은 병합 **뒤** 주식수인데 as-of 조회는 그 뒤 이벤트로 다시 나눈다(2026-11-08 실측:
    033830 06-30 1,891만 = 07-03 5:1 병합 뒤 값 → 378만으로 ×5 틀림 · 210120 06-30 → 08-20 병합).

    판정: 점 ``p`` 와 다음 실측 ``q``(DART·키움) 사이에 주식수 이벤트가 있고, ``p`` 가 다시 세기 **전** 값으로 ``q`` 와
    같고(로그 차 ≤ :data:`SAME_SHARES_LOG`) 다시 센 값으로는 다르면 — ``p`` 를 그 구간 마지막 이벤트일로 옮긴다(출처
    ``dart_moved``). 그 날부터 가격도 조정돼 있으니 시총이 맞고, 그 전은 앞 점이 덮는다. 둘 다 아니면(사이에 증자까지
    겹쳐 어느 쪽과도 안 맞으면) 손대지 않는다 — 추측하지 않는다. 그런 점의 날짜는 ``unresolved`` 에 더한다(병합 뒤 값이면
    ×N 오류가 남을 수 있다 — 원장 메모로 드러낸다). 뒤 점부터 본다(옮긴 점이 앞 점의 ``q`` 가 된다).
    Returns: ``[(날짜, 주식수, "dart"|"dart_moved")]`` — 키움 날짜와 겹치게 옮겨질 점은 뺀다.
    """
    from .shares import _c_at, _share_event

    out = [(d, n, "dart") for d, n in points]
    taken = {d for d, _ in kiwoom}
    for i in range(len(out) - 1, -1, -1):
        d, n, _ = out[i]
        later = sorted([(dd, nn) for dd, nn, _ in out[i + 1:]] + [(dd, nn) for dd, nn in kiwoom if dd > d])
        if not later:
            continue
        qd, qn = later[0]
        between = sorted(e for e, tp, rt in events if d < e <= qd and _share_event(tp, rt))
        if not between:
            continue
        raw = abs(math.log(n / qn))
        recount = abs(math.log(n / qn) + _c_at(events, qd) - _c_at(events, d))
        if raw <= SAME_SHARES_LOG < recount:
            out[i] = (between[-1], n, "dart_moved")
        elif min(raw, recount) > SAME_SHARES_LOG and unresolved is not None:
            unresolved.append(d)
    seen: set[str] = set()
    kept = []
    for row in sorted(out):
        if row[0] in taken or row[0] in seen:
            continue
        seen.add(row[0])
        kept.append(row)
    return kept


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_history(con, client: DartClient, codes: list[str], plan: list[tuple[int, str, str]], *,
                  corp_map: "dict[str, str] | None" = None, now: Callable[[], str] = _now,
                  progress: "Callable[[str], None] | None" = None) -> dict:
    """``codes`` × ``plan`` 을 묻고 ``dart_fetch`` 원장에 쓴다. 이미 확정된 칸(000, 기한 지난 013·no_common)은 건너뛴다.

    corpCode 는 물을 칸이 하나라도 남았을 때만 받는다(할 일이 없는데 DART 가용성에 기대지 않는다).
    Returns: ``{"asked": n, "status": {status: n}, "no_corp": [code…], "skipped": n}``.
    """
    done = {(c, y, r) for c, y, r, st, at, pe in con.execute(
        "SELECT code, bsns_year, reprt_code, status, fetched_at, period_end FROM dart_fetch").fetchall()
        if st == OK or (st in (NO_DATA, NO_COMMON) and settled(pe, at))}
    todo = [(c, y, r, pe) for c in codes for y, r, pe in plan if (c, y, r) not in done]
    stats: dict = {"asked": 0, "status": Counter(), "no_corp": [], "skipped": len(codes) * len(plan) - len(todo)}
    if not todo:
        return stats
    cmap = corp_map if corp_map is not None else corp_codes(client)
    for i, (code, year, rep, pe) in enumerate(todo, 1):
        corp = cmap.get(code)
        if corp is None:
            if code not in stats["no_corp"]:
                stats["no_corp"].append(code)
            continue
        data = client.get_json("stockTotqySttus.json",
                               {"corp_code": corp, "bsns_year": str(year), "reprt_code": rep})
        st = str(data.get("status", ""))
        got = parse_shares(data)
        if st == OK and got is None:
            st = NO_COMMON
        shares, stlm, rcept = got or (None, None, None)
        con.execute("INSERT OR REPLACE INTO dart_fetch VALUES (?,?,?,?,?,?,?,?,?)",
                    (code, year, rep, pe, st, shares, stlm, rcept, now()))
        con.commit()
        stats["asked"] += 1
        stats["status"][st] += 1
        if progress is not None and (i % 50 == 0 or i == len(todo)):
            progress(f"  DART [{i}/{len(todo)}] 호출 {client.total_calls} · {dict(stats['status'])}")
    return stats


def apply_history(con, codes: "list[str] | None" = None) -> dict:
    """``dart_fetch`` 의 값 → ``shares_outstanding_history``(출처 ``dart``). 멱등 — 옛 ``dart`` 행은 지우고 다시 쓴다.

    - 같은 기준일 값이 여럿이면(사업보고서와 정정·다른 보고서) 접수번호가 가장 늦은 것.
    - 키움 스냅숏이 같은 날짜에 있으면 그 날짜는 키움 값을 둔다(상장주식수 실측이 먼저).
    - 단위 오류는 버리고 개수를 돌려준다.
    - 구간 안에서 상장한 종목의 상장 전 값(공모 신주가 빠진 주식수)은 쓰지 않는다.
    - 이미 반영한 병합·감자를 다시 세지 않게 변경상장일로 옮긴다(:func:`align_to_listing`).
    Returns: ``{"rows": n, "dropped": [(code, date, shares)…], "moved": n, "unresolved": [(code, date)…]}`` —
    ``unresolved`` 는 그 뒤 이벤트를 다시 세도 안 세도 다음 실측과 안 맞는 점(병합과 증자가 겹친 구간).
    """
    from . import write

    if codes is None:
        codes = [r[0] for r in con.execute("SELECT DISTINCT code FROM dart_fetch").fetchall()]
    start = con.execute("SELECT min(date) FROM supply_demand").fetchone()[0]
    rows, dropped, moved, unresolved = 0, [], 0, []
    for code in codes:
        old = [r[0] for r in con.execute(
            "SELECT date FROM shares_provenance WHERE code = ? AND source IN ('dart', 'dart_moved')",
            (code,)).fetchall()]
        for d in old:
            con.execute("DELETE FROM shares_outstanding_history WHERE code = ? AND date = ?", (code, d))
            con.execute("DELETE FROM shares_provenance WHERE code = ? AND date = ?", (code, d))
        best: dict[str, tuple[str, int]] = {}
        for stlm, n, rcept in con.execute(
                "SELECT stlm_dt, shares, rcept_no FROM dart_fetch WHERE code = ? AND status = ? AND shares > 0",
                (code, OK)).fetchall():
            if stlm not in best or (rcept or "") > best[stlm][0]:
                best[stlm] = (rcept or "", int(n))
        others = dict(con.execute(
            "SELECT h.date, h.shares_outstanding FROM shares_outstanding_history h JOIN shares_provenance p "
            "USING (code, date) WHERE code = ? AND p.source = 'kiwoom'", (code,)).fetchall())
        # 구간 안에서 상장한 종목(첫 수급일 > 구간 첫날)의 상장 전 값은 공모 신주가 빠진 비상장 주식수다 — 쓰지 않는다
        # (403850: 2024-12-31 1,235만 · 상장 2025-11-18 · 2025-12-31 1,435만). 상장일까지는 시드가 첫 실측에서 거꾸로 센다.
        first = con.execute("SELECT min(date) FROM supply_demand WHERE code = ?", (code,)).fetchone()[0]
        listed_from = first if first and start and first > start else ""
        points = [(d, n) for d, (_, n) in sorted(best.items()) if d >= listed_from]
        keep, drop = drop_unit_errors(points, list(others.values()))
        dropped += [(code, d, n) for d, n in drop]
        events = [(str(d), str(tp), rt) for d, tp, rt in con.execute(
            "SELECT date, upd_stkpc_tp, upd_rt FROM corporate_actions WHERE code = ?", (code,)).fetchall()]
        loose: list[str] = []
        aligned = align_to_listing(keep, sorted(others.items()), events, loose)
        unresolved += [(code, d) for d in loose]
        for d, n, src in aligned:
            rows += write.upsert_shares(con, [(code, d, n)], src)
            moved += src == "dart_moved"
    con.commit()
    return {"rows": rows, "dropped": dropped, "moved": moved, "unresolved": unresolved}
