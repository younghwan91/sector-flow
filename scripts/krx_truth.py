#!/usr/bin/env python3
"""공표 시장값 대조 — 리포트의 시장 합계 순매수 금액을 공표치에 댄다. **내부 대조 전용** (D1-m2, 4회차 D1).

리포트의 순매수 금액은 순매매 **수량 × 그날 종가** 근사고, 시장 합계는 ``supply_demand`` 에 있는 종목만의
합이다. 종가 환산 vs VWAP 환산 차이(``approx_gap``)는 내부 자료끼리의 대조라 실제 오차를 못 본다 — 48일
실측에서 ``approx_gap`` 칩은 0일 떴고(최대 3.12%) 공표치 기준 거래소 오차는 28일이 5% 를 넘었다
(``docs/validation/2026-11-09-market-total-vs-published.md``). 이 모듈은 기준일 공표치를 받아 시장×주체 상대
오차를 ``today.krx_truth`` 에 적고, 화면·검증기는 그 값으로 판정한다.

**소스.** 기본은 네이버 시장값(``source=naver``) — 개인·외국인·기관 셋만 있고(억, 기타법인 없음) KRX 원표와
같은지 **확인하지 못했다**. KRX 표(``source=krx``, ``KR_QUANT_KRX_TRUTH_SOURCE=krx``)는 받는 코드만 남아 있다.
2026-11-09 익명 POST 는 400 ``LOGOUT`` 이었다(로그인 장벽). 그래서 파일·블록 이름은 옛 ``krx_truth`` 그대로다.

**이용 범위(법적 가드).** 네이버·KRX 약관을 확인하지 못했다. 공표 수치는 **내부 대조용으로만** 받아 리포트 폴더
(``<리포트>/krx_truth.json`` — ``serve_reports.py`` 목록 밖)에만 재운다. **페이로드와 화면에는 오차 % 와 부호
반대 표지만 싣는다** — 공표 수치·절대차를 ``today.krx_truth`` 에 넣지 않는다(검증기가 본다). 빈도는 기준일당
시장별 최대 2회(저녁 게시·다음날 확정)다.

**엔드포인트.** 네이버: ``GET m.stock.naver.com/api/index/{KOSPI,KOSDAQ}/trend?bizdate=YYYYMMDD`` →
``personalValue``·``foreignValue``·``institutionalValue``(억, 부호·쉼표). 공표 전·휴장일은 값이 전부 0 이다.
KRX: ``POST data.krx.co.kr/comm/bldAttendant/getJsonData.cmd``, ``bld=dbms/MDC/STAT/standard/MDCSTAT02201``,
``mktId=STK|KSQ``, ``strtDd=endDd=YYYYMMDD``, ``money=1`` → ``output[]`` 의 ``INVST_TP_NM``·``NETBID_TRDVAL``(원).

**실패는 리포트를 깨지 않는다.** 망·로그인 장벽·형식 변화·공표 전 어떤 실패도 ``{"status": "skip", "reason": …}``
으로 남고, 검증기가 연속 SKIP 을 세어 3회째부터 경고한다(``verify_report._vt_krx_truth``).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

EOK = 1e8                        # 원 → 억원 (sector_flow.EOK 와 같은 정의 — 독립 사본)

KRX_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_BLD = "dbms/MDC/STAT/standard/MDCSTAT02201"
NAVER_URL = "https://m.stock.naver.com/api/index/{index}/trend?bizdate={date}"
#: 신원을 밝힌다 — 내부 대조용 일 단위 조회임을 UA 로 적는다. 개인 연락처를 넣지 않는다.
USER_AGENT = "sector-flow-internal-verification"
REFERER = "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020403"
#: 시장별 요청 사이 간격[초] — 예의상 최소 2초(탐사 때와 같은 규율).
REQUEST_PAUSE_S = 2.0
#: 응답 크기 상한 — 이 통계는 수 KB 다. 비정상으로 큰 응답은 파싱하지 않는다.
MAX_BYTES = 2_000_000

#: 크론 부담 상한 — 시장마다 한 번 받고 망 오류만 한 번 더 시도한다.
NAVER_TIMEOUT_S = 10.0
NAVER_RETRY_PAUSE_S = 1.0
NAVER_PAUSE_S = 0.5

#: 페이로드 시장 이름 → KRX mktId.
MKT_IDS = {"거래소": "STK", "코스닥": "KSQ"}
#: 페이로드 시장 이름 → 네이버 지수 코드.
NAVER_INDEX = {"거래소": "KOSPI", "코스닥": "KOSDAQ"}
#: 네이버 응답 키 → 공표 행 이름(KRX 표와 같은 이름으로 재운다 — 검증기·주체 매핑이 한 벌이다).
NAVER_KEYS = {"personalValue": "개인", "foreignValue": "외국인", "institutionalValue": "기관합계"}
#: 소스별 화면 꼬리표 — 어느 공표치에 댄 값인지 숨기지 않는다.
SOURCE_LABEL = {"naver": "네이버 시장값, KRX 원표 미대조, 내부 대조용", "krx": "KRX 공표치, 내부 대조용"}
#: 페이로드 주체 → KRX 투자자구분 행(합산). 외국인은 공표 행이 외국인·기타외국인로 갈린다 —
#: ``supply_demand`` 쪽 정의가 어느 쪽인지는 바로 이 대조가 재는 것이라 합계로 맞춘다.
ACTOR_ROWS = {"inst": ("기관합계",), "forgn": ("외국인", "기타외국인"),
              "indiv": ("개인",), "etc": ("기타법인",)}
ACTOR_KO = {"inst": "기관", "forgn": "외국인", "indiv": "개인", "etc": "기타법인"}
#: ``기관합계`` 행이 없으면 이 행들을 합쳐 만든다(공표 표의 기관 소분류).
INST_SUB = ("금융투자", "보험", "투신", "사모", "은행", "기타금융", "연기금 등")
#: 분모 게이트 — |공표 순매수| 가 그 시장 거래대금의 이 몫 미만이면 % 를 내지 않는다.
#: 0 근처 순매수의 상대 오차는 숫자가 아니라 잡음이다(CLAUDE.md 비율 열 규칙). 그런 날 큰 오차는
#: 주체 칸이 아니라 시장 합계(``total_pct`` = Σ|오차| ÷ Σ|공표|)가 잡는다 — 10-29 개인이 그 예다.
GAP_MIN_TV_SHARE = 0.002
#: 시장 합계 오차가 이 % 를 넘으면 화면 칩과 운영자 경고 한 줄(⚠️).
KRX_GAP_WARN = 5.0


class KrxFetchError(RuntimeError):
    """공표치를 못 받았다 — HTTP 오류·LOGOUT(로그인 장벽)·형식 변화·공표 전. 호출 쪽에서 SKIP 으로 받는다."""


def _now_kst() -> str:
    """지금 시각(KST, ISO) — ``time.localtime`` 류를 쓰지 않는다(머신 시간대 무관)."""
    kst = dt.timezone(dt.timedelta(hours=9), "KST")
    return dt.datetime.now(dt.timezone.utc).astimezone(kst).isoformat(timespec="seconds")


def _won(s) -> float:
    """공표 금액 문자열(쉼표 구분, 원) → float[원]. 빈 값·``-`` 는 0."""
    t = str(s).replace(",", "").strip()
    return 0.0 if t in ("", "-") else float(t)


def fetch_market(mkt_id: str, date: str, *, timeout: float = 30.0) -> dict[str, float]:
    """한 시장·하루의 ``{투자자구분: 순매수 거래대금[원]}`` — 실패는 전부 :class:`KrxFetchError`."""
    form = urllib.parse.urlencode({
        "bld": KRX_BLD, "locale": "ko_KR", "mktId": mkt_id,
        "strtDd": date.replace("-", ""), "endDd": date.replace("-", ""),
        "etf": "", "etn": "", "elw": "", "share": "1", "money": "1", "csvxls_isNo": "false",
    }).encode()
    req = urllib.request.Request(KRX_URL, data=form, headers={
        "User-Agent": USER_AGENT, "Referer": REFERER,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        tail = (e.read(64) or b"").decode("utf-8", "replace").strip()
        hint = " — 로그인 장벽(익명 조회 거부). 접근이 풀릴 때까지 SKIP 이다" if "LOGOUT" in tail else ""
        raise KrxFetchError(f"HTTP {e.code} {tail[:40]!r}{hint}") from e
    except OSError as e:                         # URLError·timeout·DNS
        raise KrxFetchError(f"접속 실패: {e}") from e
    return _parse_body(body)


def _parse_body(body: bytes) -> dict[str, float]:
    """응답 바디 → ``{투자자구분: 순매수 거래대금[원]}``. LOGOUT·형식 변화·빈 표는 :class:`KrxFetchError`."""
    if len(body) > MAX_BYTES:
        raise KrxFetchError(f"응답이 {MAX_BYTES}B 를 넘는다 — 형식이 바뀐 듯하다")
    text = body.decode("utf-8", "replace")
    if "LOGOUT" in text[:200]:
        raise KrxFetchError("LOGOUT — 로그인 장벽(익명 조회 거부). 접근이 풀릴 때까지 SKIP 이다")
    try:
        rows = json.loads(text)["output"]
        out = {str(r["INVST_TP_NM"]).strip(): _won(r["NETBID_TRDVAL"]) for r in rows}
    except (ValueError, KeyError, TypeError) as e:
        raise KrxFetchError(f"응답 형식이 다르다({e!r}) — bld·열 이름이 바뀐 듯하다") from e
    if not out:
        raise KrxFetchError("output 이 비었다 — 휴장일이거나 아직 공표 전이다")
    return out


def _signed(s) -> float:
    """네이버 금액 문자열(``+1,221``·``-429``) → float."""
    return float(str(s).replace(",", "").replace("+", "").strip())


def _parse_naver(body: bytes, date: str) -> dict[str, float]:
    """네이버 응답 → ``{공표 행: 순매수[원]}``. 다른 날·공표 전(전부 0)·형식 변화는 :class:`KrxFetchError`."""
    try:
        d = json.loads(body.decode("utf-8", "replace"))
        rows = {name: _signed(d[k]) * EOK for k, name in NAVER_KEYS.items()}
        biz = str(d.get("bizdate"))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise KrxFetchError(f"네이버 응답 형식이 다르다({e!r})") from e
    if biz != date.replace("-", ""):
        raise KrxFetchError(f"네이버가 다른 날을 줬다({biz} ≠ {date})")
    if not any(rows.values()):
        raise KrxFetchError("네이버 값이 전부 0 — 휴장일이거나 아직 공표 전이다")
    return rows


def fetch_naver(index: str, date: str, *, timeout: float = NAVER_TIMEOUT_S,
                opener=None, retry_pause: float = NAVER_RETRY_PAUSE_S) -> dict[str, float]:
    """네이버 시장값 한 시장·하루 — 망 오류만 한 번 더 시도한다(크론 부담 상한). 4xx 는 다시 묻지 않는다."""
    opener = urllib.request.urlopen if opener is None else opener
    url = NAVER_URL.format(index=index, date=date.replace("-", ""))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last: "Exception | None" = None
    for attempt in range(2):
        if attempt and retry_pause > 0:
            time.sleep(retry_pause)
        try:
            with opener(req, timeout=timeout) as r:
                body = r.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise KrxFetchError(f"응답이 {MAX_BYTES}B 를 넘는다 — 형식이 바뀐 듯하다")
            return _parse_naver(body, date)
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise KrxFetchError(f"네이버 HTTP {e.code}") from e
            last = e
        except OSError as e:                     # URLError·timeout·DNS
            last = e
    raise KrxFetchError(f"네이버 접속 실패: {last}") from last


#: 소스 이름 → (받는 함수, 시장 코드 표, 요청 간격[초], 있는 주체).
SOURCES = {
    "naver": (fetch_naver, NAVER_INDEX, NAVER_PAUSE_S, ("inst", "forgn", "indiv")),
    "krx": (fetch_market, MKT_IDS, REQUEST_PAUSE_S, tuple(ACTOR_ROWS)),
}


def _actor_won(rows: dict[str, float], actor: str) -> float:
    """공표 행에서 한 주체의 순매수[원] — 기관합계가 없으면 소분류 합으로 만든다."""
    if actor == "inst" and "기관합계" not in rows:
        sub = [k for k in INST_SUB if k in rows]
        if not sub:
            raise KrxFetchError("기관 행(기관합계·소분류)이 없다 — 투자자구분 이름이 바뀐 듯하다")
        return sum(rows[k] for k in sub)
    names = [n for n in ACTOR_ROWS[actor] if n in rows]
    if not names:
        raise KrxFetchError(f"{ACTOR_KO[actor]} 행({'·'.join(ACTOR_ROWS[actor])})이 없다")
    return sum(rows[n] for n in names)


def fetch_official(date: str, markets: list[str], *, source: str = "naver", fetch=None,
                   pause: "float | None" = None) -> dict:
    """두 시장의 공표 순매수[억] — ``{"date", "fetched_at", "source", "markets": {시장: {주체: 억}}, "rows"}``.

    ``rows`` 는 공표 행(원)이다 — 검증기가 이것으로 오차를 다시 센다. 이 값은 사이드카(``krx_truth.json``)에만
    머문다. 페이로드에는 :func:`compare` 의 오차만 간다.
    """
    if source not in SOURCES:
        raise KrxFetchError(f"모르는 소스다({source}) — naver·krx 만 있다")
    default_fetch, codes, default_pause, actors = SOURCES[source]
    fetch = default_fetch if fetch is None else fetch
    pause = default_pause if pause is None else pause
    out: dict = {"date": date, "fetched_at": _now_kst(), "source": source,
                 "use": "내부 대조 전용 — 약관 미확인, 비공개 리포트 밖 재배포 금지", "markets": {}, "rows": {}}
    first = True
    for m in markets:
        if m not in codes:
            continue
        if not first and pause > 0:
            time.sleep(pause)
        first = False
        rows = fetch(codes[m], date)
        out["rows"][m] = rows
        out["markets"][m] = {a: round(_actor_won(rows, a) / EOK, 2) for a in actors}
    if not out["markets"]:
        raise KrxFetchError(f"아는 시장이 없다({markets})")
    return out


def compare(today: dict, official: dict) -> dict:
    """페이로드 ``today.market`` (수량×종가 근사) ↔ 공표치 — 시장×주체 상대 오차 블록. **공표 수치를 싣지 않는다.**

    주체 칸 ``gap_pct = |근사 − 공표| / |공표| × 100`` (|공표| 이 거래대금의 :data:`GAP_MIN_TV_SHARE` 미만이면
    ``None``), ``flip`` = 부호가 반대이고 둘 중 큰 쪽이 그 게이트 이상. 시장 ``total_pct`` = Σ|오차| ÷ Σ|공표|
    (같은 게이트를 Σ|공표| 에), ``hot`` = ``total_pct`` 가 :data:`KRX_GAP_WARN` 초과 — 칩은 이것만 본다.
    """
    markets: dict = {}
    for m, off in (official.get("markets") or {}).items():
        M = (today.get("market") or {}).get(m) or {}
        net = M.get("net") or {}
        tv = M.get("tv")                          # 그날 거래대금[억] — 분모 게이트의 기준
        gate = None if tv is None else GAP_MIN_TV_SHARE * tv
        row: dict = {}
        err = den = 0.0
        for a in ACTOR_ROWS:
            o = off.get(a)
            v = (net.get(a) or {}).get("v")
            if o is None or v is None:
                row[a] = None
                continue
            err += abs(v - o)
            den += abs(o)
            big = gate is not None and abs(o) >= gate and o != 0
            row[a] = {"gap_pct": round(abs(v - o) / abs(o) * 100.0, 2) if big else None,
                      "flip": bool(gate is not None and o * v < 0 and max(abs(o), abs(v)) >= gate)}
        total = round(err / den * 100.0, 2) if gate is not None and den and den >= gate else None
        row["total_pct"] = total
        row["hot"] = bool(total is not None and total > KRX_GAP_WARN)
        markets[m] = row
    source = official.get("source")
    source = source if source in SOURCE_LABEL else "krx"     # 옛 사이드카의 source 는 {url, bld} 였다
    return {"status": "ok", "source": source, "label": SOURCE_LABEL[source], "date": official.get("date"),
            "fetched_at": official.get("fetched_at"), "min_tv_share": GAP_MIN_TV_SHARE, "warn": KRX_GAP_WARN,
            "markets": markets}


def _skip(reason: str) -> dict:
    return {"status": "skip", "reason": reason, "checked_at": _now_kst()}


def _say(block: dict) -> None:
    """빌드 로그 한 줄 — 운영자 문구는 README §3.1 런북과 짝이다(tests/test_runbook.py). 오차만 적는다."""
    if block.get("status") != "ok":
        print(f"⚠️ [공표치 대조] SKIP — {block.get('reason')}. 리포트는 그대로 만든다", file=sys.stderr)
        return
    for m, row in (block.get("markets") or {}).items():
        parts = []
        for a in ACTOR_ROWS:
            r = row.get(a)
            if r is None:
                continue
            g = r.get("gap_pct")
            parts.append(f"{ACTOR_KO[a]} {'—' if g is None else f'{g:.2f}%'}" + (" 부호 반대" if r.get("flip") else ""))
        t = row.get("total_pct")
        print(f"[공표치 대조] {m} 오차 합계 {'—' if t is None else f'{t:.2f}%'}: " + " · ".join(parts))
        if row.get("hot"):
            print(f"⚠️ [공표치 대조] 오차 큼 — {m} {t:.2f}% (문턱 {KRX_GAP_WARN}%, {block.get('label')}). "
                  "시장 합계가 공표치와 이만큼 다르다 — 금액 해석에 반영하라", file=sys.stderr)


def attach(payload: dict, build_dir: "str | None", root: "str | None", *,
           env=None, fetch=None, pause: "float | None" = None, now: "str | None" = None) -> "dict | None":
    """켜져 있으면 ``payload["today"]["krx_truth"]`` 를 채운다 — 꺼져 있으면 아무것도 안 한다.

    켜짐 = 같은 기준일 폴더(``<root>/<기준일>/krx_truth.json``)에 캐시가 있거나, 빌드 환경에
    ``KR_QUANT_KRX_TRUTH=1``. 캐시가 있으면 다시 받지 않는다 — 단 켜져 있고 그 캐시를 **기준일 당일 이전**에
    받았는데 지금이 기준일 뒤면 한 번 다시 받는다(확정 빌드, 기준일당 최대 2회). 못 받으면 캐시를 쓴다.
    받았거나 캐시를 썼으면 ``build_dir/krx_truth.json`` 에 재워 리포트 폴더와 같이 움직인다.
    어떤 실패도 예외가 아니라 skip 블록이다 — 리포트를 깨지 않는다(이 모듈의 버그까지 포함).
    """
    try:
        return _attach(payload, build_dir, root, env=env, fetch=fetch, pause=pause, now=now)
    except Exception as e:  # noqa: BLE001 — 선택 기능의 버그가 본 리포트를 죽이면 안 된다
        block = _skip(f"내부 오류: {e!r}")
        t = payload.get("today")
        if isinstance(t, dict):
            t["krx_truth"] = block
        print(f"⚠️ [공표치 대조] 내부 오류 — {e!r}. 리포트는 그대로 만든다", file=sys.stderr)
        return block


def _stale(cached: dict, date: str, now: str) -> bool:
    """캐시를 기준일 당일(또는 그 전)에 받았고 지금은 기준일 뒤다 — 확정 빌드가 한 번 다시 받을 때."""
    return str(cached.get("fetched_at") or "")[:10] <= date < now[:10]


def _attach(payload: dict, build_dir: "str | None", root: "str | None", *,
            env=None, fetch=None, pause: "float | None" = None, now: "str | None" = None) -> "dict | None":
    env = os.environ if env is None else env
    today = payload.get("today") or {}
    date = today.get("date")
    cache = os.path.join(root, date, "krx_truth.json") if root and date else None
    want_fetch = bool(env.get("KR_QUANT_KRX_TRUTH"))
    if not want_fetch and not (cache and os.path.isfile(cache)):
        return None                              # 켜지 않았으면 흔적도 없다
    official = None
    reason = "기준일이 없다(옛 페이로드)" if not date else None
    if date and cache and os.path.isfile(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                c = json.load(f)
            if c.get("date") == date and c.get("markets"):
                official = c
            else:
                reason = f"캐시 기준일이 다르다({c.get('date')} ≠ {date})"
        except (OSError, ValueError) as e:
            reason = f"캐시를 못 읽었다: {e}"
    refresh = official is not None and want_fetch and _stale(official, date, now or _now_kst())
    if (official is None or refresh) and date and want_fetch:
        try:
            official = fetch_official(date, list(payload.get("markets") or MKT_IDS), fetch=fetch, pause=pause,
                                      source=env.get("KR_QUANT_KRX_TRUTH_SOURCE") or "naver")
            reason = None
        except (KrxFetchError, OSError) as e:
            reason = str(e)                      # 다시 받기 실패면 official 은 캐시 그대로다
    if official is not None:
        block = compare(today, official)
        if build_dir:
            try:
                with open(os.path.join(build_dir, "krx_truth.json"), "w", encoding="utf-8") as f:
                    json.dump(official, f, ensure_ascii=False, indent=1)
            except OSError as e:
                print(f"⚠️ [공표치 대조] krx_truth.json 을 못 재웠다({e}) — 다음 빌드가 다시 받는다",
                      file=sys.stderr)
    else:
        block = _skip(reason or "캐시도 KR_QUANT_KRX_TRUTH 도 없다")
    today["krx_truth"] = block
    _say(block)
    return block


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="공표 시장값 ↔ 리포트 시장 합계 금액 대조(내부 대조 전용)")
    ap.add_argument("--payload", required=True, help="리포트 payload.json — today.krx_truth 를 제자리에 채운다")
    ap.add_argument("--fetch", action="store_true", help="캐시가 없으면 공표치를 받는다(기본 소스 네이버)")
    ap.add_argument("--out", help="결과를 이 폴더에 쓴다(payload.json·krx_truth.json) — 게시된 리포트를 볼 때는 이것을 쓴다")
    a = ap.parse_args(argv)
    path = os.path.abspath(a.payload)
    source_dir = os.path.dirname(os.path.realpath(path))
    # 게시된 폴더는 고쳐 쓰지 않는다 — numbers.html 과 payload 가 갈리고, 재현 명령 하나가 게시본을 바꾼다.
    if not a.out and os.path.exists(os.path.join(source_dir, "numbers.html")):
        print(f"게시된 리포트({source_dir})는 고쳐 쓰지 않는다 — 재현은 --out <임시 폴더> 를 붙여라. 게시본에 반영하려면 "
              "KR_QUANT_KRX_TRUTH=1 KR_QUANT_FORCE=1 scripts/daily_report.sh", file=sys.stderr)
        return 2
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    build_dir = os.path.abspath(a.out) if a.out else os.path.dirname(path)
    if a.out:
        os.makedirs(build_dir, exist_ok=True)
        path = os.path.join(build_dir, "payload.json")
    env = dict(os.environ)
    if a.fetch:
        env["KR_QUANT_KRX_TRUTH"] = "1"
    block = attach(payload, build_dir, _root_of(os.path.abspath(a.payload)), env=env)
    if block is None:
        print("켜져 있지 않다 — 캐시(krx_truth.json)도 --fetch 도 없다. 아무것도 안 바꿨다")
        return 0
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)
    print(f"payload 갱신: {path} — 이미 만든 HTML 은 다시 만들어야 반영된다(daily_report.sh KR_QUANT_FORCE=1)")
    return 0


def _root_of(payload_path: str) -> "str | None":
    """``<root>/<기준일>[.building.N]/payload.json`` 꼴이면 root — sector_flow.reports_root 와 같은 규칙."""
    import re
    d = os.path.dirname(payload_path)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(\.building\.\d+)?", os.path.basename(d)):
        return os.path.dirname(d)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
