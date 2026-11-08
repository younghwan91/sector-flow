"""``sector-flow-collect`` — 자기 키움 키로 sqlite 를 채운다. 서브커맨드 ``smoke`` · ``backfill``.

    sector-flow-collect smoke --code 005930
    sector-flow-collect backfill --db data/byo.db --sectors "운송/창고,오락/문화" --days 300

Phase 1 은 작은 조각(섹터 몇 개)만 받는다. 전 유니버스 증분·확정·밀린 날(설계 §2.4)은 다음 단계다.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

from . import fetch, write
from .auth import load_keys, mask
from .kiwoom import CallBudgetExceeded, KiwoomRest, TokenConflict
from .shares import reseed

KST = timezone(timedelta(hours=9))
#: 같은 앱키를 쓰는 운영 수집기(quant-airflow·scalp-it)가 도는 평일 창[KST, (시작, 끝) 분]. 이 창에 로그인하면
#: 그쪽 토큰을 무효화한다(8005). 사용자 PC 의 BYO 실행에는 해당 없지만, 이 호스트의 Phase 1 시험에서는 막는다.
BUSY_WINDOWS = ((6 * 60 + 30, 7 * 60 + 30), (15 * 60 + 30, 19 * 60 + 30))
ANYTIME_ENV = "SECTORFLOW_COLLECT_ANYTIME"
#: 실패율이 이걸 넘으면 실행 실패(quant-airflow ``combined.py:210`` 과 같다).
MAX_FAIL_RATE = 0.20
#: ka10059 한 쪽의 거래일 수(실측 약 100).
SD_PAGE_DAYS = 100


def busy_window(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    m = now.hour * 60 + now.minute
    return any(a <= m < b for a, b in BUSY_WINDOWS)


def _client(args) -> KiwoomRest:
    now = datetime.now(KST)
    if busy_window(now) and not os.environ.get(ANYTIME_ENV):
        raise SystemExit(f"지금({now:%a %H:%M} KST)은 같은 키를 쓰는 운영 수집기 창이다 — 나중에 돌려라 "
                         f"(이 호스트가 아니면 {ANYTIME_ENV}=1).")
    key, secret = load_keys(args.env_file)
    return KiwoomRest(key, secret, rate=args.rate, max_calls=args.max_calls)


def _today() -> str:
    return datetime.now(KST).strftime("%Y%m%d")


def cmd_smoke(args) -> int:
    """TR 6개를 한 번씩 — 응답의 **모양**만 찍는다(값·토큰 없음)."""
    api = _client(args)
    anchor, code = _today(), args.code
    out: dict = {}

    def probe(name, fn):
        try:
            data, rh = fn()
            out[name] = {"shape": fetch.shape(data), "cont-yn": rh.get("cont-yn"),
                         "has_next_key": bool(rh.get("next-key"))}
            for k in (fetch.SD_KEY, fetch.CHART_KEY, fetch.INDEX_CHART_KEY, "list", fetch.INDEX_LIST_KEY):
                rows = data.get(k)
                if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "dt" in rows[0]:
                    ds = [str(r.get("dt", "")) for r in rows if r.get("dt")]
                    out[name]["dt_range"] = [min(ds), max(ds)]
                    out[name]["upd_stkpc_tp_values"] = sorted({str(r.get("upd_stkpc_tp", ""))
                                                               for r in rows})[:10]
        except Exception as e:  # noqa: BLE001 — 한 TR 이 죽어도 나머지 모양은 본다
            out[name] = {"error": mask(f"{type(e).__name__}: {e}")[:300]}

    probe("ka10099", lambda: api.request("ka10099", {"mrkt_tp": "0"}))
    probe("ka10059", lambda: api.request("ka10059", {"dt": anchor, "stk_cd": code, "amt_qty_tp": "2",
                                                     "trde_tp": "0", "unit_tp": "1"}))
    probe("ka10081", lambda: api.request("ka10081", {"stk_cd": code, "base_dt": anchor, "upd_stkpc_tp": "1"}))
    probe("ka10001", lambda: api.request("ka10001", {"stk_cd": code}))
    probe("ka20003", lambda: api.request("ka20003", {"inds_cd": "001"}))
    probe("ka20006", lambda: api.request("ka20006", {"inds_cd": "001", "base_dt": anchor}))
    out["_calls"] = dict(api.calls)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not any("error" in v for k, v in out.items() if not k.startswith("_")) else 1


def cmd_backfill(args) -> int:
    api = _client(args)
    con = write.open_db(args.db)
    run_id = write.start_run(con, "backfill")
    anchor = _today()
    rows: dict = {}
    failed: list = []

    def add(table, n):
        rows[table] = rows.get(table, 0) + n

    ok = False
    asof = None
    try:
        # 1) 업종 목록과 거래일 달력(코스피 종합 001 일봉) — 백필 하한(oldest)을 정한다.
        inds = fetch.industry_list(api)
        names = {i["code"]: i["name"] for i in inds}
        cal_rows = fetch.industry_daily(api, "001", anchor)
        cal = sorted({str(r["dt"]) for r in cal_rows if r.get("dt")})
        if len(cal) < args.days:
            raise SystemExit(f"001 일봉이 {len(cal)}개뿐이다 — --days {args.days} 를 덮지 못한다")
        oldest = cal[-args.days]
        asof = fetch.iso(cal[-1])
        print(f"📅 {fetch.iso(oldest)} ~ {asof} ({args.days}거래일)")

        # 2) 종목 마스터 → 조각
        stocks = [s for m in ("kospi", "kosdaq") for s in fetch.stock_list(api, m)]
        stocks = [s for s in stocks if fetch.is_common_stock(s)]
        sectors = {s.strip() for s in (args.sectors or "").split(",") if s.strip()}
        codes = {c.strip() for c in (args.codes or "").split(",") if c.strip()}
        if sectors or codes:
            stocks = [s for s in stocks if s["sector"] in sectors or s["code"] in codes]
        if args.limit:
            stocks = stocks[: args.limit]
        if not stocks:
            raise SystemExit("고른 종목이 없다 — --sectors/--codes 를 확인하라")
        add("stocks", write.upsert_stocks(con, stocks))
        con.commit()
        print(f"🧺 {len(stocks)}종목 ({', '.join(sorted(sectors)) or '전체'})")

        # 3) 업종지수: 종합 2개 + 고른 섹터와 이름이 같은 업종 코드 전부(index_returns 가 이름으로 묶는다).
        want = {"001", "101"} | {c for c, n in names.items() if n in {s["sector"] for s in stocks}}
        for code in sorted(want):
            r = cal_rows if code == "001" else fetch.industry_daily(api, code, anchor)
            recs = fetch.index_records(code, names.get(code, ""), r, oldest=oldest)
            if not recs:
                failed.append([code, "ka20006", "0행"])
            add("sector_index", write.upsert_index(con, recs))
        con.commit()

        # 4) 종목별: 수급(ka10059 여러 쪽) · 수정·무수정 일봉(ka10081) · 주식수(ka10001)
        pages = math.ceil(args.days / SD_PAGE_DAYS) + 1
        snap = fetch.iso(anchor)
        for i, s in enumerate(stocks, 1):
            code = s["code"]
            try:
                sd = fetch.sd_records(code, fetch.investor_flow(api, code, anchor, max_pages=pages,
                                                                oldest=oldest), oldest=oldest)
                if not sd:
                    raise fetch.EmptyResponse("ka10059 0행")
                add("supply_demand", write.upsert_supply_demand(con, sd))
                adj_rows = fetch.daily_chart(api, code, anchor, adjusted=True)
                bars = fetch.bar_records(code, adj_rows, oldest=oldest)
                if not bars:
                    raise fetch.EmptyResponse("ka10081 수정 0행")
                add("daily_bars", write.upsert_bars(con, bars))
                add("daily_bars_adjusted", write.upsert_adjusted(con, bars))
                events = fetch.event_records(code, adj_rows)
                if not args.no_raw:
                    raw_rows = fetch.daily_chart(api, code, anchor, adjusted=False)
                    add("daily_bars_raw", write.upsert_raw(con, fetch.bar_records(code, raw_rows,
                                                                                   oldest=oldest)))
                    events += fetch.event_records(code, raw_rows)
                # 수정·무수정 응답이 같은 이벤트를 둘 다 싣는다 — 날짜로 하나만 남긴다(두 번 세면 시드가 두 배로 틀린다).
                events = list({(r[0], r[1]): r for r in events}.values())
                add("corporate_actions", write.replace_events(con, code, events))
                cur = fetch.listed_shares(api, code)
                if cur is None:
                    failed.append([code, "ka10001", "상장주식수 없음"])
                else:
                    add("shares_outstanding_history", write.upsert_shares(con, [(code, snap, cur)], "kiwoom"))
                    add("shares_outstanding_history", reseed(con, [code]))
                con.commit()
            except (CallBudgetExceeded, TokenConflict):
                raise
            except Exception as e:  # noqa: BLE001 — 종목 단위로 격리(quant-airflow combined.py 와 같다)
                con.rollback()
                failed.append([code, "-", mask(f"{type(e).__name__}: {e}")[:200]])
            if i % 10 == 0 or i == len(stocks):
                print(f"  [{i}/{len(stocks)}] 호출 {api.total_calls} · 실패 {len(failed)}")
        ok = len([f for f in failed if f[1] != "ka20006"]) <= MAX_FAIL_RATE * len(stocks)
    finally:
        write.finish_run(con, run_id, calls=dict(api.calls), rows=rows, failed=failed, ok=ok, asof=asof)
        con.close()
        print(json.dumps({"ok": ok, "calls": dict(api.calls), "retries": dict(api.retries),
                          "rows": rows, "failed": failed[:20], "n_failed": len(failed)},
                         ensure_ascii=False, indent=1))
    return 0 if ok else 1


def cmd_reseed(args) -> int:
    con = write.open_db(args.db)
    try:
        print(f"시드 {reseed(con)}행")
    finally:
        con.close()
    return 0


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(prog="sector-flow-collect", description=__doc__.splitlines()[0])
    ap.add_argument("--env-file", default=os.environ.get("SECTORFLOW_ENV_FILE"),
                    help="KIWOOM_APP_KEY/SECRET 가 든 .env(환경변수가 먼저)")
    ap.add_argument("--rate", type=float, default=0.9, help="TR 하나의 속도[req/s] (기본 0.9)")
    ap.add_argument("--max-calls", type=int, default=3000, help="이 실행의 총 호출 상한")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sm = sub.add_parser("smoke", help="TR 6개를 한 번씩 불러 응답 모양만 찍는다")
    sm.add_argument("--code", default="005930")
    bf = sub.add_parser("backfill", help="조각 백필")
    bf.add_argument("--db", required=True, help="sqlite 경로")
    bf.add_argument("--days", type=int, default=300)
    bf.add_argument("--sectors", default="", help="쉼표로 준 키움 업종명(stocks.sector)")
    bf.add_argument("--codes", default="", help="쉼표로 준 종목코드(섹터와 합집합)")
    bf.add_argument("--limit", type=int, default=0)
    bf.add_argument("--no-raw", action="store_true", help="무수정 일봉(daily_bars_raw)을 건너뛴다")
    rs = sub.add_parser("reseed", help="저장된 스냅숏·기업행동으로 주식수 시드를 다시 만든다(호출 없음)")
    rs.add_argument("--db", required=True)
    args = ap.parse_args(argv)
    return {"smoke": cmd_smoke, "backfill": cmd_backfill, "reseed": cmd_reseed}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
