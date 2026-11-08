"""TR 호출과 응답 → ``storage.SCHEMA`` 행. 파싱 규칙은 quant-airflow 수집기와 같게 둔다.

동등성 시험(Phase 1)이 kr_quant 와 숫자를 대조하므로, 부호·단위·빈 값 처리를 quant-airflow 와 한 글자씩
맞춘다 — ``to_int`` 는 빈 값을 0 으로(``collectors/storage.py:775``), 가격은 절댓값(부호는 등락 방향),
``trade_value`` 는 키움 단위 그대로(백만원), 상장주식수 ``flo_stk`` 는 천 주 단위(``listed_shares.py:47``).
"""

from __future__ import annotations

from typing import Any

from sectorflow.storage import INVESTOR_COLUMNS

from .kiwoom import KiwoomRest

#: ka10099 시장 구분.
MARKETS = {"kospi": "0", "kosdaq": "10"}
SD_KEY = "stk_invsr_orgn"            # ka10059
CHART_KEY = "stk_dt_pole_chart_qry"  # ka10081
INDEX_CHART_KEY = "inds_dt_pole_qry"  # ka20006
INDEX_LIST_KEY = "all_inds_idex"     # ka20003
#: ka20003 범위 — 둘이 키움이 주는 업종 코드 전부를 덮는다(quant-airflow ``sector_index.py:30``).
INDEX_SCOPES = ("001", "101")
SHARES_UNIT = 1_000


class EmptyResponse(RuntimeError):
    """행이 0개 — 성공으로 넘기지 않는다(quant-airflow ``CLAUDE.md`` §5 "rows=0 무실패")."""


def to_int(s: object) -> int:
    """키움 숫자 문자열(``'+322500'``·``'-1979879'``·``''``) → int. 못 읽으면 0(quant-airflow 와 같다)."""
    text = str(s or "").replace("+", "").strip()
    try:
        return int(text)
    except ValueError:
        return 0


def to_float(s: object) -> float:
    text = str(s or "").replace("+", "").strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def iso(dt: str) -> str:
    """``YYYYMMDD`` → ``YYYY-MM-DD``(리포트 코드의 날짜 모양)."""
    dt = str(dt).strip()
    if len(dt) == 8 and dt.isdigit():
        return f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"
    raise ValueError(f"날짜 모양이 아니다: {dt!r}")


# ── stocks ─────────────────────────────────────────────────────────────────
def is_common_stock(row: dict) -> bool:
    """보통주만 — quant-airflow ``supply_demand.is_common_stock`` 과 같은 규칙."""
    return row["market"] in ("거래소", "코스닥") and row["code"].endswith("0")


def stock_list(api: KiwoomRest, market: str, *, max_pages: int = 5) -> list[dict]:
    rows = api.request_pages("ka10099", {"mrkt_tp": MARKETS[market]}, "list", max_pages=max_pages)
    if not rows:
        raise EmptyResponse(f"ka10099 {market}: 종목 0개")
    return [{"code": str(r.get("code", "")).strip(), "name": str(r.get("name", "")).strip(),
             "market": str(r.get("marketName", "")).strip(),
             "sector": str(r.get("upName", "")).strip(), "kind": str(r.get("kind", "")).strip()}
            for r in rows]


# ── supply_demand (ka10059) ────────────────────────────────────────────────
def investor_flow(api: KiwoomRest, code: str, anchor: str, *, max_pages: int = 3,
                  oldest: str = "", info: "dict | None" = None) -> list[dict]:
    """``anchor``(YYYYMMDD)부터 과거로 ``max_pages`` 쪽(한 쪽 ≈ 100거래일). ``oldest`` 에 닿으면 멈춘다.

    ``amt_qty_tp=2`` = 수량(주). 리포트는 수량 × 종가로 금액을 복원한다.
    """
    def _stop(page: list[dict]) -> bool:
        return bool(oldest and page and min(str(r.get("dt", "")) for r in page) <= oldest)

    return api.request_pages(
        "ka10059",
        {"dt": anchor, "stk_cd": code, "amt_qty_tp": "2", "trde_tp": "0", "unit_tp": "1"},
        SD_KEY, max_pages=max_pages, stop=_stop, info=info)


def sd_records(code: str, rows: list[dict], *, oldest: str = "") -> list[tuple]:
    """ka10059 행 → ``supply_demand`` 튜플 (code, date, close, flu_rt, acc_trde_qty, *11주체, source)."""
    out = []
    for r in rows:
        dt = str(r.get("dt", "")).strip()
        if not dt or (oldest and dt < oldest):
            continue
        out.append((code, iso(dt), abs(to_int(r.get("cur_prc"))), to_float(r.get("flu_rt")),
                    to_int(r.get("acc_trde_qty")),
                    *[to_int(r.get(src)) for src in INVESTOR_COLUMNS.values()], "kiwoom"))
    return out


# ── daily_bars (ka10081) ───────────────────────────────────────────────────
def daily_chart(api: KiwoomRest, code: str, anchor: str, *, adjusted: bool) -> list[dict]:
    """한 쪽(약 600봉). ``adjusted`` 면 수정주가(``upd_stkpc_tp=1``) — **한 응답 안은 같은 수정 기준**이다."""
    return api.request_pages(
        "ka10081", {"stk_cd": code, "base_dt": anchor, "upd_stkpc_tp": "1" if adjusted else "0"},
        CHART_KEY, max_pages=1)


def bar_records(code: str, rows: list[dict], *, oldest: str = "") -> list[tuple]:
    """(code, date, open, high, low, close, volume, trade_value[백만원], source)."""
    out = []
    for r in rows:
        dt = str(r.get("dt", "")).strip()
        if not dt or (oldest and dt < oldest):
            continue
        out.append((code, iso(dt), abs(to_int(r.get("open_pric"))), abs(to_int(r.get("high_pric"))),
                    abs(to_int(r.get("low_pric"))), abs(to_int(r.get("cur_prc"))),
                    to_int(r.get("trde_qty")), to_int(r.get("trde_prica")), "kiwoom"))
    return out


def event_records(code: str, rows: list[dict]) -> list[tuple]:
    """수정주가 이벤트가 찍힌 행 → (code, date, upd_stkpc_tp, upd_rt). quant-airflow ``_row_to_event`` 와 같다."""
    out = []
    for r in rows:
        tp = str(r.get("upd_stkpc_tp") or "").strip()
        dt = str(r.get("dt", "")).strip()
        if not tp or not dt:
            continue
        rt = str(r.get("upd_rt") or "").strip()
        try:
            upd_rt = float(rt) if rt else None
        except ValueError:
            upd_rt = None
        out.append((code, iso(dt), tp, upd_rt))
    return out


# ── shares (ka10001) ───────────────────────────────────────────────────────
def listed_shares(api: KiwoomRest, code: str) -> "int | None":
    """현재 상장주식수(주). 없거나 0 이면 ``None`` — 0 을 적재하면 시총이 0 으로 고정된다(listed_shares.py:80)."""
    data, _ = api.request("ka10001", {"stk_cd": code})
    n = to_int(data.get("flo_stk")) * SHARES_UNIT
    return n if n > 0 else None


# ── sector_index (ka20003 · ka20006) ───────────────────────────────────────
def industry_list(api: KiwoomRest) -> list[dict]:
    seen: dict[str, str] = {}
    for scope in INDEX_SCOPES:
        data, _ = api.request("ka20003", {"inds_cd": scope})
        for r in data.get(INDEX_LIST_KEY) or []:
            seen[str(r["stk_cd"]).strip()] = str(r["stk_nm"]).strip()
    if not seen:
        raise EmptyResponse("ka20003: 업종 0개")
    return [{"code": c, "name": n} for c, n in seen.items()]


def industry_daily(api: KiwoomRest, code: str, anchor: str) -> list[dict]:
    return api.request_pages("ka20006", {"inds_cd": code, "base_dt": anchor}, INDEX_CHART_KEY,
                             max_pages=1)


def index_records(code: str, name: str, rows: list[dict], *, oldest: str = "") -> list[tuple]:
    """(code, name, date, open, high, low, close, volume, trade_value) — quant-airflow ``sector_index._row_to_record``."""
    out = []
    for r in rows:
        dt = str(r.get("dt", "")).strip()
        if not dt or (oldest and dt < oldest):
            continue
        out.append((code, name, iso(dt), abs(to_int(r.get("open_pric"))), abs(to_int(r.get("high_pric"))),
                    abs(to_int(r.get("low_pric"))), abs(to_int(r.get("cur_prc"))),
                    to_int(r.get("trde_qty")), to_int(r.get("trde_prica"))))
    return out


def shape(obj: Any, depth: int = 0) -> Any:
    """응답의 **모양**만 — 키 목록·행 수·첫 행의 키. 값(시세·토큰)은 싣지 않는다(smoke 로그용)."""
    if isinstance(obj, dict):
        return {k: shape(v, depth + 1) if isinstance(v, (dict, list)) else type(v).__name__
                for k, v in obj.items()}
    if isinstance(obj, list):
        first = obj[0] if obj else None
        return {"len": len(obj), "row_keys": sorted(first) if isinstance(first, dict) else None}
    return type(obj).__name__
