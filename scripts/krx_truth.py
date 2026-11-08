#!/usr/bin/env python3
"""KRX 공식 투자자별 거래대금 대조 — 선택 실행, **내부 검증 전용** (D1-m2).

리포트의 순매수 금액은 순매매 **수량 × 그날 종가** 근사다(참값은 체결가 합 —
``docs/formulas.md``). 종가 환산 vs VWAP 환산 차이(``approx_gap``)는 내부 자료끼리의
대조라 외부 참값이 없었다. 이 모듈은 KRX 정보데이터시스템(data.krx.co.kr)이 공표하는
**투자자별 거래실적 — 거래대금(순매수, 원)** 을 기준일 하루치 받아, 페이로드의 시장
1일 순매수 금액(기관·외국인·개인·기타법인)과의 상대 오차를 ``today.krx_truth`` 에
적는다 — 근사 오차를 공식 공표치에 대고 날마다 수치화한다.

**이용 범위(법적 가드).** 이 데이터는 **내부 검증용으로만** 받는다. 기준일마다 한 번
받아 리포트 폴더에 재우고(``<리포트>/krx_truth.json``), 이 레포의 비공개 리포트 밖으로
재배포하지 않는다(이 레포는 PRIVATE 다 — CLAUDE.md 운영 절). 이 용도는 KRX 시장데이터
이용 범위 질의(strategy-a-plus ``docs/strategy/inquiries/krx_market_data.md``)에 포함해
확인한다. 자동 수집이 아니라 리포트 빌드당 시장별 1회(하루 최대 수 회)의 조회다.

**엔드포인트(2026-11-09 조사).** ``POST https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd``,
폼 파라미터 ``bld=dbms/MDC/STAT/standard/MDCSTAT02201``(투자자별 거래실적),
``mktId=STK|KSQ``, ``strtDd=endDd=YYYYMMDD``, ``money=1``. 응답 ``output[]`` 행:
``INVST_TP_NM``(투자자구분 — 금융투자·보험·…·기관합계·기타법인·개인·외국인·기타외국인·전체),
``ASK_TRDVAL``/``BID_TRDVAL``/``NETBID_TRDVAL``(거래대금, **원**, 쉼표 구분 문자열).
같은 날 실측: 익명 POST 는 400 ``LOGOUT`` 으로 거부됐다(해당 통계 메뉴가 로그인 장벽
뒤에 있다) — 그것도 실패가 아니라 SKIP 이다. 접근 방식이 바뀌면 받는 쪽만 여기서 고친다.

**실패는 리포트를 깨지 않는다.** 망·로그인 장벽·형식 변화·휴장 등 어떤 실패도
``{"status": "skip", "reason": …}`` 으로만 남는다(``attach`` 는 예외를 안 올린다).
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
#: 신원을 밝힌다 — 내부 검증용 수동 빈도 조회임을 UA 로 적는다.
USER_AGENT = "sector-flow-internal-verification (daily cross-check; contact: repo owner)"
REFERER = "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020403"
#: 시장별 요청 사이 간격[초] — 예의상 최소 2초(탐사 때와 같은 규율).
REQUEST_PAUSE_S = 2.0
#: 응답 크기 상한 — 이 통계는 수 KB 다. 비정상으로 큰 응답은 파싱하지 않는다.
MAX_BYTES = 2_000_000

#: 페이로드 시장 이름 → KRX mktId.
MKT_IDS = {"거래소": "STK", "코스닥": "KSQ"}
#: 페이로드 주체 → KRX 투자자구분 행(합산). 외국인은 공표 행이 외국인·기타외국인로 갈린다 —
#: ``supply_demand`` 쪽 정의가 어느 쪽인지는 바로 이 대조가 재는 것이라 합계로 맞춘다.
ACTOR_ROWS = {"inst": ("기관합계",), "forgn": ("외국인", "기타외국인"),
              "indiv": ("개인",), "etc": ("기타법인",)}
ACTOR_KO = {"inst": "기관", "forgn": "외국인", "indiv": "개인", "etc": "기타법인"}
#: ``기관합계`` 행이 없으면 이 행들을 합쳐 만든다(공표 표의 기관 소분류).
INST_SUB = ("금융투자", "보험", "투신", "사모", "은행", "기타금융", "연기금 등")
#: 분모 게이트 — |공식 순매수| 가 그 시장 거래대금의 이 몫 미만이면 % 를 내지 않는다
#: (절대차[억]만 싣는다). 0 근처 순매수의 상대 오차는 숫자가 아니라 잡음이다(CLAUDE.md 비율 열 규칙).
GAP_MIN_TV_SHARE = 0.002
#: 이 % 를 넘는 주체가 있으면 운영자 경고 한 줄(⚠️)을 남긴다 — ``approx_gap`` 칩과 같은 문턱.
KRX_GAP_WARN = 5.0


class KrxFetchError(RuntimeError):
    """KRX 가 값을 안 줬다 — HTTP 오류·LOGOUT(로그인 장벽)·형식 변화. 호출 쪽에서 SKIP 으로 받는다."""


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


def fetch_official(date: str, markets: list[str], *, fetch=None,
                   pause: float = REQUEST_PAUSE_S) -> dict:
    """두 시장의 공식 순매수 거래대금[억] — ``{"date", "fetched_at", "markets": {시장: {주체: 억}}, "rows"}``.

    ``rows`` 는 공표 행 원본(원)이다 — 나중에 매핑(외국인 합산 등)을 다시 따질 수 있게 같이 재운다.
    """
    fetch = fetch_market if fetch is None else fetch
    out: dict = {"date": date, "fetched_at": _now_kst(), "source": {"url": KRX_URL, "bld": KRX_BLD},
                 "use": "내부 검증용 — 비공개 리포트 밖 재배포 금지", "markets": {}, "rows": {}}
    first = True
    for m in markets:
        if m not in MKT_IDS:
            continue
        if not first and pause > 0:
            time.sleep(pause)
        first = False
        rows = fetch(MKT_IDS[m], date)
        out["rows"][m] = rows
        out["markets"][m] = {a: round(_actor_won(rows, a) / EOK, 2) for a in ACTOR_ROWS}
    if not out["markets"]:
        raise KrxFetchError(f"아는 시장이 없다({markets})")
    return out


def compare(today: dict, official: dict) -> dict:
    """페이로드 ``today.market`` (수량×종가 근사) ↔ 공식 거래대금 — 시장×주체 상대 오차 블록.

    ``gap_pct = |근사 − 공식| / |공식| × 100``. |공식| 이 그 시장 거래대금의
    :data:`GAP_MIN_TV_SHARE` 미만이면 % 대신 ``None`` (절대차 ``diff`` [억]는 늘 싣는다).
    """
    markets: dict = {}
    for m, off in (official.get("markets") or {}).items():
        M = (today.get("market") or {}).get(m) or {}
        net = M.get("net") or {}
        tv = M.get("tv")                          # 그날 거래대금[억] — 분모 게이트의 기준
        row: dict = {}
        for a in ACTOR_ROWS:
            o = off.get(a)
            v = (net.get(a) or {}).get("v")
            if o is None or v is None:
                row[a] = None
                continue
            gated = tv is not None and abs(o) >= GAP_MIN_TV_SHARE * tv and o != 0
            row[a] = {"official": o, "approx": v, "diff": round(v - o, 2),
                      "gap_pct": round(abs(v - o) / abs(o) * 100.0, 2) if gated else None}
        markets[m] = row
    return {"status": "ok", "date": official.get("date"), "fetched_at": official.get("fetched_at"),
            "min_tv_share": GAP_MIN_TV_SHARE, "warn": KRX_GAP_WARN, "markets": markets}


def _skip(reason: str) -> dict:
    return {"status": "skip", "reason": reason, "checked_at": _now_kst()}


def _say(block: dict) -> None:
    """빌드 로그 한 줄 — 운영자 문구는 README §3.1 런북과 짝이다(tests/test_runbook.py)."""
    if block.get("status") != "ok":
        print(f"⚠️ [KRX 공식 대조] SKIP — {block.get('reason')}. 리포트는 그대로 만든다", file=sys.stderr)
        return
    for m, row in (block.get("markets") or {}).items():
        parts = []
        hot = []
        for a in ACTOR_ROWS:
            r = row.get(a)
            g = r and r.get("gap_pct")
            parts.append(f"{ACTOR_KO[a]} {'—' if g is None else f'{g:.2f}%'}")
            if g is not None and g > KRX_GAP_WARN:
                hot.append(f"{ACTOR_KO[a]} {g:.2f}%")
        print(f"[KRX 공식 대조] {m} 근사 오차: " + " · ".join(parts))
        if hot:
            print(f"⚠️ [KRX 공식 대조] 근사 오차 큼 — {m} {', '.join(hot)} (문턱 {KRX_GAP_WARN}%). "
                  "수량×종가 근사가 공식 거래대금과 이만큼 다르다 — 금액 해석에 반영하라", file=sys.stderr)


def attach(payload: dict, build_dir: "str | None", root: "str | None", *,
           env=None, fetch=None, pause: float = REQUEST_PAUSE_S) -> "dict | None":
    """켜져 있으면 ``payload["today"]["krx_truth"]`` 를 채운다 — 꺼져 있으면 아무것도 안 한다.

    켜짐 = 같은 기준일 폴더(``<root>/<기준일>/krx_truth.json``)에 캐시가 있거나, 빌드 환경에
    ``KR_QUANT_KRX_TRUTH=1``. 캐시가 있으면 **다시 받지 않고** 그대로 쓴다(기준일당 한 번).
    받았거나 캐시를 썼으면 ``build_dir/krx_truth.json`` 에 재워 리포트 폴더와 같이 움직인다.
    어떤 실패도 예외가 아니라 skip 블록이다 — 리포트를 깨지 않는다(이 모듈의 버그까지 포함).
    """
    try:
        return _attach(payload, build_dir, root, env=env, fetch=fetch, pause=pause)
    except Exception as e:  # noqa: BLE001 — 선택 기능의 버그가 본 리포트를 죽이면 안 된다
        block = _skip(f"내부 오류: {e!r}")
        t = payload.get("today")
        if isinstance(t, dict):
            t["krx_truth"] = block
        print(f"⚠️ [KRX 공식 대조] 내부 오류 — {e!r}. 리포트는 그대로 만든다", file=sys.stderr)
        return block


def _attach(payload: dict, build_dir: "str | None", root: "str | None", *,
            env=None, fetch=None, pause: float = REQUEST_PAUSE_S) -> "dict | None":
    env = os.environ if env is None else env
    today = payload.get("today") or {}
    date = today.get("date")
    cache = os.path.join(root, date, "krx_truth.json") if root and date else None
    want_fetch = bool(env.get("KR_QUANT_KRX_TRUTH"))
    if not want_fetch and not (cache and os.path.isfile(cache)):
        return None                              # 선택 기능 — 안 켰으면 흔적도 없다
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
    if official is None and date and want_fetch:
        try:
            official = fetch_official(date, list(payload.get("markets") or MKT_IDS),
                                      fetch=fetch, pause=pause)
            reason = None
        except (KrxFetchError, OSError) as e:
            reason = str(e)
    if official is not None:
        block = compare(today, official)
        if build_dir:
            try:
                with open(os.path.join(build_dir, "krx_truth.json"), "w", encoding="utf-8") as f:
                    json.dump(official, f, ensure_ascii=False, indent=1)
            except OSError as e:
                print(f"⚠️ [KRX 공식 대조] krx_truth.json 을 못 재웠다({e}) — 다음 빌드가 다시 받는다",
                      file=sys.stderr)
    else:
        block = _skip(reason or "캐시도 KR_QUANT_KRX_TRUTH 도 없다")
    today["krx_truth"] = block
    _say(block)
    return block


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="KRX 공식 투자자별 거래대금 ↔ 리포트 근사 금액 대조(내부 검증용)")
    ap.add_argument("--payload", required=True, help="리포트 payload.json — today.krx_truth 를 제자리에 채운다")
    ap.add_argument("--fetch", action="store_true", help="캐시가 없으면 data.krx.co.kr 에서 받는다")
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
