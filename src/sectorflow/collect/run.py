"""``sector-flow-collect`` — 자기 증권사 키로 sqlite 를 채운다. 서브커맨드 ``smoke`` · ``universe`` · ``backfill`` · ``daily`` · ``sectors`` · ``reseed`` · ``dart-shares``.

    sector-flow-collect smoke --code 005930
    sector-flow-collect universe --db data/byo.db                # 코스피·코스닥 전 종목·업종(ka10099, 호출 2 + 쪽)
    sector-flow-collect backfill --db data/byo.db --sectors "운송/창고,오락/문화" --days 300
    sector-flow-collect backfill --db data/byo.db --map-only     # 지도 전용: TR 2개, 기본 120거래일
    sector-flow-collect daily --db data/byo.db --map-only        # 마지막 저장일부터 오늘까지(지도 전용: TR 2개)
    sector-flow-collect dart-shares --db data/byo.db          # 선택: DART 분기 주식수(본인 DART_API_KEY)
    sector-flow-collect sectors --db data/byo.db --csv my_sectors.csv   # 선택: 자기 섹터 표(code,sector)
    sector-flow-collect --busy-file deploy/kiwoom_busy.simnode.toml backfill ... --resume

같은 앱키를 다른 프로그램과 나눠 쓰면 ``--busy-file`` 로 그 프로그램의 운영 창을 준다(:mod:`.busy`). 요청 직전마다
창을 확인하고, 창에 들어서면 그 요청을 보내지 않고 멈춘다(종료코드 75). 끝난 종목은 커밋돼 있어 ``--resume`` 이 이어 받는다.

``daily`` 는 백필한 종목(과 같은 섹터의 신규 상장)을 종목마다 자기 마지막 저장일부터 오늘까지 받는다. 끝날은 KRX 휴장
달력 정본(``scripts/krx_calendar.py``)이 정하고, 애프터마켓이 끝난 20:00 KST 전이면 오늘은 받지 않는다. 마지막
저장일은 다시 받아 덮는다 — 그날 잠정치가 다음 실행에서 새 값이 된다.

증권사는 ``--broker``(:mod:`.brokers`, 기본 키움). 섹터는 증권사와 떼어 한 벌로 둔다(``stocks.sector_source``) — 키움 업종명,
그 증권사의 업종, 또는 ``sectors --csv`` 의 자기 표. 섹터를 안 주는 증권사로 목록을 받아도 저장된 섹터는 지워지지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import io
import functools
import importlib.util
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import dart, fetch, write
from .auth import MissingKeys, load_dart_key, mask
from . import busy
from .brokers import BROKERS, DEFAULT as DEFAULT_BROKER, Broker, Settings
from .brokers import kiwoom as kiwoom_broker
from .brokers.base import sd_tuple
from .kiwoom import BusyWindow, CallBudgetExceeded, KiwoomRest, TokenConflict
from .shares import reseed
from sectorflow.report_cli import script_path

KST = timezone(timedelta(hours=9))
BUSY_ENV = "SECTORFLOW_BUSY_FILE"
#: 운영 창에 걸려 멈췄다 — 다시 돌리면 된다(daily_report.sh 의 "수집 미완" 과 같은 값).
EXIT_BUSY = 75
#: 실패율이 이걸 넘으면 실행 실패(quant-airflow ``combined.py:210`` 과 같다).
MAX_FAIL_RATE = 0.20
#: 지도 전용 수집이 부르는 TR — 종목·업종 목록과 수급뿐(설계 §5). ``daily --map-only`` 와 ``backfill --map-only`` 가
#: 같이 쓴다. 클라이언트가 이 밖의 TR 을 요청 전에 막는다(``_client(args, only=map_only_trs)``).
map_only_trs = ("ka10099", "ka10059")
#: ``backfill --days`` 기본값 — 전체 수집 300거래일, 지도 전용 120거래일(설계 §9: 빠른 길을 기본으로).
BACKFILL_DAYS = 300
MAP_ONLY_DAYS = 120
#: 그날 수급·통합 종가가 다 찬 시각(KST 시) — 애프터마켓(16:00~20:00)이 끝난 뒤. 그 전이면 오늘은 받지 않는다.
DAY_DONE_HOUR_KST = 20


def _settings(args, windows: list) -> Settings:
    return Settings(env_file=args.env_file, rate=args.rate, max_calls=args.max_calls,
                    guard=busy.make_guard(windows), notice=lambda m: print(m, file=sys.stderr))


def _client(args, only: "tuple[str, ...] | None" = None) -> KiwoomRest:
    """키움 세션 — 전체 수집(일봉·주식수·업종지수)과 키움 어댑터가 같이 쓴다. 테스트가 이 자리에 가짜 전송을 끼운다."""
    windows = busy.load(args.busy_file)
    if not windows:
        print(busy.SHARED_KEY_WARNING, file=sys.stderr)
    return kiwoom_broker.rest(_settings(args, windows), only=only)


def _broker(args, only: "tuple[str, ...] | None" = None) -> Broker:
    """``--broker`` 의 어댑터. 키움은 :func:`_client` 세션을 감싼다(``only`` 는 키움 TR 제한)."""
    name = getattr(args, "broker", DEFAULT_BROKER)
    if name == "kiwoom":
        return kiwoom_broker.KiwoomBroker(_client(args, only=only) if only else _client(args))
    return BROKERS[name](_settings(args, busy.load(args.busy_file)))


@functools.lru_cache(maxsize=1)
def krx_calendar():
    """KRX 휴장 달력 정본 ``scripts/krx_calendar.py`` — 설치본은 wheel 사본(:func:`sectorflow.report_cli.script_path`)."""
    path = script_path("krx_calendar.py")
    spec = importlib.util.spec_from_file_location("sectorflow_krx_calendar", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _now_kst() -> datetime:
    return datetime.now(KST)


def target_day(now: datetime) -> str:
    """증분의 끝날(YYYY-MM-DD) — ``now`` 이하 마지막 거래일. 오늘이 거래일이라도 20시 전이면 앞 거래일이다."""
    cal = krx_calendar()
    now = now.astimezone(KST)
    today = now.date()
    known = cal.is_trading_day(today)
    if known is None:
        raise SystemExit(f"{today.year}년이 KRX 달력(scripts/krx_calendar.py)에 없다 — 거래일을 못 가른다")
    if known and now.hour >= DAY_DONE_HOUR_KST:
        return today.isoformat()
    prev = cal.prev_trading_day(today)
    if prev is None:
        raise SystemExit(f"{today} 앞 거래일이 KRX 달력(scripts/krx_calendar.py) 밖이다")
    return prev


def window_start(end: str, days: int) -> str:
    """``end`` 에서 끝나는 ``days`` 거래일 창의 첫날(YYYY-MM-DD) — KRX 휴장 달력 정본으로 센다.

    달력 밖 해의 평일은 거래일로 친다 — 창이 그만큼(휴장일 수) 앞으로 넓어질 뿐 모자라지 않는다(``_gap_days`` 와 같다).
    지도 전용 백필은 업종지수(ka20006)를 부르지 않으니 001 일봉 대신 이 달력이 하한을 정한다.
    """
    span = days * 2 + 14
    while True:
        start = (datetime.fromisoformat(end) - timedelta(days=span)).date()
        found, unknown = krx_calendar().trading_days(start, end)
        cal = sorted(found + unknown)
        if len(cal) >= days:
            return cal[-days]
        span *= 2


def _gap_days(since: str, end: str) -> int:
    """(since, end] 의 거래일 수 — 달력 밖 평일은 거래일로 친다(쪽 수를 넉넉히)."""
    start = (datetime.fromisoformat(since) + timedelta(days=1)).date()
    days, unknown = krx_calendar().trading_days(start, end)
    return len(days) + len(unknown)


def stored_listing(con, name: str) -> list[dict]:
    """전 종목 목록 API 가 없는 증권사(:attr:`Broker.lists_stocks` 거짓)의 목록 — 저장된 ``stocks``. 비었으면 갈 길을 적고 멈춘다."""
    rows = [dict(zip(("code", "name", "market", "sector", "kind", "sector_source"), r)) for r in con.execute(
        "SELECT code, name, market, COALESCE(sector, ''), COALESCE(kind, ''), sector_source FROM stocks ORDER BY code")]
    if not rows:
        raise SystemExit(f"⏹ --broker {name} 는 전 종목 목록 API 가 없다 — 먼저 종목 목록을 넣는다: 키움 키로 "
                         "universe --broker kiwoom, 또는 sectors --csv <표> --add-missing(docs/byo/brokers.md)")
    return rows


def listing(broker: Broker, con) -> list[dict]:
    """증권사 목록(없으면 저장된 ``stocks``) — 섹터는 저장된 한 벌을 따른다(:func:`write.with_stored_sectors`)."""
    rows = broker.universe() if broker.lists_stocks else stored_listing(con, broker.name)
    return write.with_stored_sectors(con, rows)


def _require_map_only(args, map_only: bool) -> None:
    """키움 밖의 증권사는 지도 전용 수집만 한다 — 일봉·주식수·업종지수 TR 이 어댑터에 없다."""
    name = getattr(args, "broker", DEFAULT_BROKER)
    if name != "kiwoom" and not map_only:
        raise SystemExit(f"⏹ --broker {name} 는 지도 전용 수집만 한다(수급·종목 목록) — --map-only 를 준다")


def collect_flows(broker: Broker, con, code: str, start: str, end: str, *,
                  days: int) -> "tuple[int, str, dict]":
    """종목 하나의 ``start``~``end``(YYYY-MM-DD, ``days`` 거래일) 수급 → ``supply_demand``. ``(행 수, 첫 날짜, 쪽 정보)``.

    지도 전용 수집의 종목 하나 — ``backfill``·``daily`` 가 같이 쓴다. 0행이면 :class:`fetch.EmptyResponse`.
    """
    info: dict = {}
    sd = [sd_tuple(r) for r in broker.investor_daily(code, start, end, days=days, info=info)]
    if not sd:
        raise fetch.EmptyResponse(f"{broker.name} 수급 0행")
    return write.upsert_supply_demand(con, sd), min(r[1] for r in sd), info


def _collect_prices(api: KiwoomRest, con, code: str, anchor: str, *, oldest: str, snap: str, no_raw: bool,
                    add, failed: list) -> None:
    """수정·무수정 일봉(ka10081) · 기업행동 · 주식수(ka10001) — 지도 전용 수집에는 없는 나머지."""
    adj_rows = fetch.daily_chart(api, code, anchor, adjusted=True)
    bars = fetch.bar_records(code, adj_rows, oldest=oldest)
    if not bars:
        raise fetch.EmptyResponse("ka10081 수정 0행")
    add("daily_bars", write.upsert_bars(con, bars))
    add("daily_bars_adjusted", write.upsert_adjusted(con, bars))
    events = fetch.event_records(code, adj_rows)
    span = [str(r["dt"]) for r in adj_rows if r.get("dt")]
    if not no_raw:
        raw_rows = fetch.daily_chart(api, code, anchor, adjusted=False)
        add("daily_bars_raw", write.upsert_raw(con, fetch.bar_records(code, raw_rows, oldest=oldest)))
        events += fetch.event_records(code, raw_rows)
        span += [str(r["dt"]) for r in raw_rows if r.get("dt")]
    # 수정·무수정 응답이 같은 이벤트를 둘 다 싣는다 — 날짜로 하나만 남긴다(두 번 세면 시드가 두 배로 틀린다).
    events = list({(r[0], r[1]): r for r in events}.values())
    # 응답이 덮은 날짜 범위만 바꾼다 — 그 밖의 이벤트(이전 실행분)는 지우지 않는다.
    add("corporate_actions", write.replace_events(con, code, events, fetch.iso(min(span)), fetch.iso(max(span))))
    cur = fetch.listed_shares(api, code)
    if cur is None:
        failed.append([code, "ka10001", "상장주식수 없음"])
    else:
        add("shares_outstanding_history", write.upsert_shares(con, [(code, snap, cur)], "kiwoom"))
        add("shares_outstanding_history", reseed(con, [code]))


def _collect_indexes(api: KiwoomRest, con, sectors: set, anchor: str, *, oldest: str, add, failed: list,
                     names: "dict | None" = None, cal_rows: "list | None" = None) -> None:
    """업종지수: 종합 2개 + 고른 섹터와 이름이 같은 업종 코드 전부(index_returns 가 이름으로 묶는다)."""
    if names is None:
        names = {i["code"]: i["name"] for i in fetch.industry_list(api)}
    want = {"001", "101"} | {c for c, n in names.items() if n in sectors}
    for code in sorted(want):
        r = cal_rows if code == "001" and cal_rows is not None else fetch.industry_daily(api, code, anchor)
        recs = fetch.index_records(code, names.get(code, ""), r, oldest=oldest)
        if not recs:
            failed.append([code, "ka20006", "0행"])
        add("sector_index", write.upsert_index(con, recs))


def _today() -> str:
    return datetime.now(KST).strftime("%Y%m%d")


def cmd_universe(args) -> int:
    """전 시장 종목·업종 목록 → ``stocks``. 키움은 시장마다 1회 + 연속조회 쪽(토큰 1)."""
    broker = _broker(args)
    if not broker.lists_stocks:
        raise SystemExit(f"⏹ --broker {broker.name} 는 전 종목 목록 API 가 없다 — 키움 키로 universe --broker kiwoom 을 받거나 "
                         "sectors --csv <표> --add-missing 으로 넣는다(docs/byo/brokers.md)")
    con = write.open_db(args.db)
    run_id = write.start_run(con, "universe")
    rows: dict = {}
    failed: list = []
    notes: list = []
    ok = False
    interrupted = ""
    try:
        stocks = write.with_stored_sectors(con, broker.universe())
        rows["stocks"] = write.upsert_stocks(con, stocks)
        con.commit()
        ok = True
        by_market = Counter(s["market"] for s in stocks)
        no_sector = [s["code"] for s in stocks if not s["sector"]]
        if no_sector:
            notes.append(["-", broker.list_label, f"업종 없음 {len(no_sector)}종목(앞 5: {','.join(no_sector[:5])})"])
        print(f"🧺 {' · '.join(f'{m} {n}' for m, n in sorted(by_market.items()))}종목, "
              f"업종 {len({s['sector'] for s in stocks if s['sector']})}개")
    except (fetch.EmptyResponse, fetch.TruncatedResponse) as e:
        con.rollback()
        failed.append(["-", broker.list_label, str(e)])
        print(f"⏹ {e}", file=sys.stderr)
    except BusyWindow as e:
        con.rollback()
        interrupted = str(e)
        notes.append(["-", "busy", interrupted])
        print(f"⏸ {interrupted}", file=sys.stderr)
    finally:
        write.finish_run(con, run_id, calls=dict(broker.calls), rows=rows, failed=failed, ok=ok, notes=notes)
        con.close()
        print(json.dumps({"ok": ok, "interrupted": bool(interrupted), "calls": dict(broker.calls), "rows": rows,
                          "failed": failed, "notes": notes}, ensure_ascii=False, indent=1))
    if interrupted:
        return EXIT_BUSY
    return 0 if ok else 1


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
        except (BusyWindow, TokenConflict, CallBudgetExceeded):
            raise
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
    """조각 백필. ``--map-only`` 면 종목 목록·수급(:data:`map_only_trs`)만 — 하한은 KRX 달력, 끝날은 :func:`target_day`."""
    map_only = args.map_only
    _require_map_only(args, map_only)
    if args.days is None:
        args.days = MAP_ONLY_DAYS if map_only else BACKFILL_DAYS
    broker = _broker(args, only=map_only_trs if map_only else None)
    con = write.open_db(args.db)
    run_id = write.start_run(con, "backfill-map" if map_only else "backfill")
    anchor = _today()
    rows: dict = {}
    failed: list = []
    notes: list = []

    def add(table, n):
        rows[table] = rows.get(table, 0) + n

    ok = False
    asof = None
    interrupted = ""
    try:
        # 1) 업종 목록과 거래일 달력(코스피 종합 001 일봉) — 백필 하한(oldest)을 정한다.
        #    지도 전용은 KRX 휴장 달력 정본으로 — 업종 TR 을 부르지 않는다.
        if map_only:
            asof = target_day(_now_kst())
            anchor = asof.replace("-", "")
            oldest = window_start(asof, args.days).replace("-", "")
        else:
            rest = broker.api
            inds = fetch.industry_list(rest)
            names = {i["code"]: i["name"] for i in inds}
            cal_rows = fetch.industry_daily(rest, "001", anchor)
            cal = sorted({str(r["dt"]) for r in cal_rows if r.get("dt")})
            if len(cal) < args.days:
                raise SystemExit(f"001 일봉이 {len(cal)}개뿐이다 — --days {args.days} 를 덮지 못한다")
            oldest = cal[-args.days]
            asof = fetch.iso(cal[-1])
        print(f"📅 {fetch.iso(oldest)} ~ {asof} ({args.days}거래일){' · 지도 전용' if map_only else ''}")

        # 2) 종목 마스터 → 조각
        # 섹터는 저장된 한 벌을 따른다(사용자 표·다른 키로 받은 업종) — 조각(--sectors)도 그 섹터로 고른다.
        stocks = listing(broker, con)
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

        # 3) 업종지수(지도 전용은 없다)
        if not map_only:
            _collect_indexes(rest, con, {s["sector"] for s in stocks}, anchor, oldest=oldest, add=add, failed=failed,
                             names=names, cal_rows=cal_rows)
            con.commit()

        # 4) 종목별: 수급(ka10059 여러 쪽) · 수정·무수정 일봉(ka10081) · 주식수(ka10001)
        snap = fetch.iso(anchor)
        done = write.done_codes(con, asof) if args.resume else set()
        if done:
            print(f"↪ 이어 받기: {len(done)}종목은 이미 끝났다({asof})")
        for i, s in enumerate(stocks, 1):
            code = s["code"]
            if code in done:
                continue
            try:
                n_sd, first, info = collect_flows(broker, con, code, fetch.iso(oldest), fetch.iso(anchor),
                                                  days=args.days)
                if first > fetch.iso(oldest):
                    if info.get("more"):
                        # 서버는 더 있다는데 쪽 상한에서 멈췄다 — 신규 상장과 구분해 실패로 센다.
                        failed.append([code, broker.flow_label,
                                       f"잘림: {info.get('pages')}쪽에서 멈춰 {first} 앞을 못 받았다"])
                    else:
                        notes.append([code, broker.flow_label, f"이력 시작 {first}(상장·이전상장 이후분)"])
                add("supply_demand", n_sd)
                if not map_only:
                    _collect_prices(rest, con, code, anchor, oldest=oldest, snap=snap, no_raw=args.no_raw, add=add,
                                    failed=failed)
                write.mark_done(con, code, asof, run_id)
                con.commit()
            except (CallBudgetExceeded, TokenConflict, BusyWindow):
                con.rollback()
                raise
            except Exception as e:  # noqa: BLE001 — 종목 단위로 격리(quant-airflow combined.py 와 같다)
                con.rollback()
                failed.append([code, "-", mask(f"{type(e).__name__}: {e}")[:200]])
            if i % 10 == 0 or i == len(stocks):
                print(f"  [{i}/{len(stocks)}] 호출 {broker.total_calls} · 실패 {len(failed)}")
        ok = len([f for f in failed if f[1] != "ka20006"]) <= MAX_FAIL_RATE * len(stocks)
    except BusyWindow as e:
        con.rollback()
        interrupted = str(e)
        print(f"⏸ {interrupted}", file=sys.stderr)
    finally:
        if interrupted:
            notes.append(["-", "busy", interrupted])
        write.finish_run(con, run_id, calls=dict(broker.calls), rows=rows, failed=failed, ok=ok, asof=asof,
                         notes=notes)
        con.close()
        print(json.dumps({"ok": ok, "interrupted": bool(interrupted), "calls": dict(broker.calls),
                          "retries": dict(broker.retries), "reissues": broker.reissues, "rows": rows,
                          "failed": failed[:20], "n_failed": len(failed), "notes": notes[:20]},
                         ensure_ascii=False, indent=1))
    if interrupted:
        return EXIT_BUSY
    return 0 if ok else 1


def cmd_daily(args) -> int:
    """마지막 저장일부터 끝날(:func:`target_day`)까지 — 종목마다 자기 마지막 저장일을 다시 받아 덮는다.

    대상은 수급이 저장된 종목 중 지금 상장 목록에 있는 것 + 그 섹터의 신규 상장이다(조각으로 백필했으면 조각 그대로).
    끝날에 이미 끝난 종목(``collect_done``)·끝날까지 찬 종목은 건너뛴다 — 다 찼으면 키움을 부르지 않는다.
    """
    _require_map_only(args, args.map_only)
    end = target_day(_now_kst())
    con = write.open_db(args.db)
    last_by_code = dict(con.execute("SELECT code, max(date) FROM supply_demand GROUP BY code").fetchall())
    if not last_by_code:
        con.close()
        raise SystemExit("저장된 수급이 없다 — 먼저 universe 로 목록을 받고 backfill 로 채운다")
    done = write.done_codes(con, end)
    stale = {c: d for c, d in last_by_code.items() if d < end and c not in done}
    if not stale:
        con.close()
        print(f"✅ 이미 최신이다({end}) — 키움을 부르지 않는다")
        return 0
    held = dict(con.execute("SELECT code, sector FROM stocks").fetchall())
    sectors = {held[c] for c in last_by_code if held.get(c)}
    newest = max(last_by_code.values())

    broker = _broker(args, only=map_only_trs if args.map_only else None)
    run_id = write.start_run(con, "daily-map" if args.map_only else "daily", end)
    anchor = end.replace("-", "")
    rows: dict = {}
    failed: list = []
    notes: list = []

    def add(table, n):
        rows[table] = rows.get(table, 0) + n

    ok = False
    interrupted = ""
    targets: list = []
    try:
        listed = {s["code"]: s for s in listing(broker, con)}
        for code in sorted(c for c in stale if c not in listed):
            notes.append([code, broker.list_label, "상장 목록에 없다(폐지·이전상장?) — 받지 않는다"])
            write.mark_done(con, code, end, run_id)
        new = sorted(c for c, s in listed.items() if c not in last_by_code and s["sector"] in sectors)
        targets = sorted(c for c in stale if c in listed) + new
        add("stocks", write.upsert_stocks(con, [listed[c] for c in targets]))
        if not args.map_only:
            _collect_indexes(broker.api, con, {listed[c]["sector"] for c in targets}, anchor,
                             oldest=newest.replace("-", ""), add=add, failed=failed)
        con.commit()
        print(f"📅 ~ {end} · {len(targets)}종목(신규 상장 {len(new)}){' · 지도 전용' if args.map_only else ''}")

        for i, code in enumerate(targets, 1):
            since = last_by_code.get(code, newest)
            try:
                n_sd, first, info = collect_flows(broker, con, code, since, end, days=_gap_days(since, end) + 1)
                add("supply_demand", n_sd)
                if first > since and info.get("more"):
                    failed.append([code, broker.flow_label, f"잘림: {info.get('pages')}쪽에서 멈춰 {first} 앞을 못 받았다"])
                if not args.map_only:
                    lo = con.execute("SELECT min(date) FROM daily_bars WHERE code = ?", (code,)).fetchone()[0]
                    # 저장된 구간 전체를 한 응답으로 다시 쓴다 — 새 기업행동이 옛 수정주가를 바꿔도 이음매가 없다.
                    _collect_prices(broker.api, con, code, anchor, oldest=(lo or since).replace("-", ""), snap=end,
                                    no_raw=args.no_raw, add=add, failed=failed)
                write.mark_done(con, code, end, run_id)
                con.commit()
            except (CallBudgetExceeded, TokenConflict, BusyWindow):
                con.rollback()
                raise
            except Exception as e:  # noqa: BLE001 — 종목 단위로 격리
                con.rollback()
                failed.append([code, "-", mask(f"{type(e).__name__}: {e}")[:200]])
            if i % 50 == 0 or i == len(targets):
                print(f"  [{i}/{len(targets)}] 호출 {broker.total_calls} · 실패 {len(failed)}")
        ok = len([f for f in failed if f[1] != "ka20006"]) <= MAX_FAIL_RATE * max(len(targets), 1)
    except BusyWindow as e:
        con.rollback()
        interrupted = str(e)
        notes.append(["-", "busy", interrupted])
        print(f"⏸ {interrupted}", file=sys.stderr)
    finally:
        write.finish_run(con, run_id, calls=dict(broker.calls), rows=rows, failed=failed, ok=ok, asof=end,
                         notes=notes)
        con.close()
        print(json.dumps({"ok": ok, "interrupted": bool(interrupted), "asof": end, "calls": dict(broker.calls),
                          "retries": dict(broker.retries), "rows": rows, "failed": failed[:20],
                          "n_failed": len(failed), "notes": notes[:20]}, ensure_ascii=False, indent=1))
    if interrupted:
        return EXIT_BUSY
    return 0 if ok else 1


#: 섹터 표 CSV 의 머리 이름 → 열. 영문(``code,sector[,name,market]``) 또는 KRX 정보데이터시스템 '업종분류 현황'
#: 내려받기의 한글 머리(``종목코드,종목명,시장구분,업종명``).
SECTOR_CSV_HEADERS = {"code": "code", "종목코드": "code", "sector": "sector", "업종명": "sector",
                      "name": "name", "종목명": "name", "market": "market", "시장구분": "market"}
#: CSV 시장 구분 → ``stocks.market``(키움 ``marketName`` 과 같은 글자). 밖(코넥스 등)은 목록에 넣지 않는다.
CSV_MARKETS = {"KOSPI": "거래소", "유가증권": "거래소", "거래소": "거래소", "KOSDAQ": "코스닥", "코스닥": "코스닥"}


def _read_text(path: str) -> str:
    """UTF-8(BOM 허용), 아니면 CP949 — KRX 내려받기 CSV 는 CP949 다."""
    raw = open(path, "rb").read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp949")


def read_stock_csv(path: str) -> list[dict]:
    """섹터 표 CSV → ``[{"code", "sector", "name", "market"}]``(이름·시장은 열이 없으면 빈 값). 머리는 :data:`SECTOR_CSV_HEADERS`.

    숫자 코드는 앞 0 을 채운다(스프레드시트가 ``000120`` 을 ``120`` 으로 만든다). 코드나 섹터가 빈 줄은 뺀다.
    """
    reader = csv.DictReader(io.StringIO(_read_text(path)))
    cols = {SECTOR_CSV_HEADERS[h.strip()]: h for h in (reader.fieldnames or []) if h and h.strip() in SECTOR_CSV_HEADERS}
    if not {"code", "sector"} <= set(cols):
        raise SystemExit(f"⏹ {path}: 머리줄에 code,sector(또는 종목코드,업종명)가 있어야 한다")
    out: list[dict] = []
    for row in reader:
        rec = {k: (row.get(cols[k]) or "").strip() if k in cols else "" for k in ("code", "sector", "name", "market")}
        rec["code"] = rec["code"].zfill(6) if rec["code"].isdigit() else rec["code"]
        if rec["code"] and rec["sector"]:
            out.append(rec)
    return out


def read_sector_csv(path: str) -> dict[str, str]:
    """섹터 표 CSV → ``{6자리 코드: 섹터}``(:func:`read_stock_csv`)."""
    return {r["code"]: r["sector"] for r in read_stock_csv(path)}


def csv_listing(rows: list[dict]) -> list[dict]:
    """CSV 행 중 ``stocks`` 에 새로 넣을 수 있는 보통주 — 이름·시장(코스피·코스닥)이 있고 코드가 6자리 숫자, 끝자리 0."""
    out = []
    for r in rows:
        market = CSV_MARKETS.get(r["market"].upper() if r["market"].isascii() else r["market"])
        s = {"code": r["code"], "name": r["name"], "market": market or "", "sector": "", "kind": "",
             "sector_source": None}
        if r["name"] and market and len(r["code"]) == 6 and r["code"].isdigit() and fetch.is_common_stock(s):
            out.append(s)
    return out


def cmd_sectors(args) -> int:
    """자기 섹터 표 → ``stocks.sector``(출처 ``csv:<이름>``). 증권사를 부르지 않는다.

    ``--add-missing`` 이면 표에 이름·시장이 있는 보통주 중 ``stocks`` 에 없는 것을 먼저 넣는다 — 전 종목 목록 API 가 없는
    증권사(한국투자·NH)로 수집할 때의 종목 목록이 된다.
    """
    rows = read_stock_csv(args.csv)
    label = args.label or os.path.splitext(os.path.basename(args.csv))[0]
    con = write.open_db(args.db)
    added = 0
    try:
        if args.add_missing:
            have = {r[0] for r in con.execute("SELECT code FROM stocks").fetchall()}
            added = write.upsert_stocks(con, [s for s in csv_listing(rows) if s["code"] not in have])
        got = write.import_sectors(con, {r["code"]: r["sector"] for r in rows}, label)
        con.commit()
    finally:
        con.close()
    print(json.dumps({"ok": got["updated"] > 0, "source": write.CSV_SOURCE + label, "rows": len(rows),
                      "added": added, "updated": got["updated"], "unknown": len(got["unknown"]),
                      "unknown_head": got["unknown"][:5]}, ensure_ascii=False, indent=1))
    if not got["updated"]:
        print("⏹ 바꾼 종목이 없다 — 먼저 universe 로 종목 목록을 받거나, 이름·시장 열이 있는 표에 --add-missing 을 준다",
              file=sys.stderr)
        return 1
    return 0


def cmd_reseed(args) -> int:
    con = write.open_db(args.db)
    try:
        print(f"시드 {reseed(con)}행")
    finally:
        con.close()
    return 0


def cmd_dart_shares(args) -> int:
    """DART 분기 발행주식총수 → ``shares_outstanding_history``(출처 dart) → 시드 다시 계산. 키움은 부르지 않는다."""
    key = load_dart_key(args.env_file)
    con = write.open_db(args.db)
    client = dart.DartClient(key, rate=args.dart_rate, max_calls=args.dart_max_calls)
    run_id = write.start_run(con, "dart-shares")
    codes = sorted({c.strip() for c in (args.codes or "").split(",") if c.strip()} or
                   {r[0] for r in con.execute("SELECT code FROM stocks").fetchall()})
    since = args.since or con.execute("SELECT min(date) FROM supply_demand").fetchone()[0]
    until = args.until or datetime.now(KST).strftime("%Y-%m-%d")
    plan = dart.periods(since, until) if since else []
    stats: dict = {}
    rows: dict = {}
    notes: list = []
    ok = False
    try:
        if not plan or not codes:
            raise SystemExit("물을 종목·분기가 없다 — 먼저 backfill 하거나 --codes/--since 를 준다")
        print(f"🗂 DART {len(codes)}종목 × {len(plan)}분기({plan[0][2]} ~ {plan[-1][2]})", file=sys.stderr)
        stats = dart.fetch_history(con, client, codes, plan,
                                   progress=lambda m: print(m, file=sys.stderr))
        ok = True
    except (CallBudgetExceeded, dart.DartError) as e:
        notes.append(["-", "dart", mask(str(e))])
        print(f"⏹ {mask(str(e))}", file=sys.stderr)
    finally:
        # 원장에 이미 받은 칸은 반영한다(멈춘 실행도 받은 만큼은 쓴다 — 다시 돌리면 남은 칸만 묻는다).
        applied = dart.apply_history(con, codes)
        rows = {"dart": applied["rows"], "dart_moved": applied["moved"], "seed": reseed(con, codes)}
        for code, d, n in applied["dropped"]:
            notes.append([code, "dart", f"단위 오류로 버림 {d} {n}"])
        for code, d in applied["unresolved"]:
            notes.append([code, "dart", f"{d} 값이 뒤 기업행동을 다시 세도 안 세도 다음 실측과 안 맞는다(병합·증자 겹침)"])
        failed = [[c, "dart", "corp_code 없음"] for c in stats.get("no_corp", [])]
        write.finish_run(con, run_id, calls={f"dart:{k}": v for k, v in client.calls.items()}, rows=rows,
                         failed=failed, ok=ok, notes=notes)
        con.close()
        print(json.dumps({"ok": ok, "calls": dict(client.calls), "total_calls": client.total_calls,
                          "asked": stats.get("asked", 0), "skipped": stats.get("skipped", 0),
                          "status": dict(stats.get("status", {})), "rows": rows, "failed": failed,
                          "notes": notes[:20]}, ensure_ascii=False, indent=1))
    return 0 if ok else 1


def _broker_arg(p) -> None:
    p.add_argument("--broker", choices=list(BROKERS), default=DEFAULT_BROKER,
                   help=f"수급을 받을 증권사 어댑터(기본 {DEFAULT_BROKER}). 키움 밖은 지도 전용(--map-only)만 — docs/byo/brokers.md")


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(prog="sector-flow-collect", description=__doc__.splitlines()[0])
    ap.add_argument("--env-file", default=os.environ.get("SECTORFLOW_ENV_FILE"),
                    help="KIWOOM_APP_KEY/SECRET 가 든 .env(환경변수가 먼저)")
    ap.add_argument("--rate", type=float, default=None,
                    help="요청 속도[req/s] — 기본은 증권사마다(키움 TR 하나 0.9, 한국투자 5). 증권사 공지 상한은 넘지 않는다")
    ap.add_argument("--max-calls", type=int, default=3000, help="이 실행의 총 호출 상한")
    ap.add_argument("--busy-file", default=os.environ.get(BUSY_ENV),
                    help="같은 앱키를 쓰는 다른 프로그램의 운영 창(TOML). 없으면 창 없음 + 경고")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sm = sub.add_parser("smoke", help="TR 6개를 한 번씩 불러 응답 모양만 찍는다")
    sm.add_argument("--code", default="005930")
    un = sub.add_parser("universe", help="코스피·코스닥 전 종목·업종 목록(키움 ka10099) → stocks")
    un.add_argument("--db", required=True, help="sqlite 경로")
    _broker_arg(un)
    bf = sub.add_parser("backfill", help="조각 백필(--sectors 를 안 주면 전 시장)")
    bf.add_argument("--db", required=True, help="sqlite 경로")
    _broker_arg(bf)
    bf.add_argument("--days", type=int, default=None,
                    help=f"거래일 수(기본 {BACKFILL_DAYS}, --map-only 면 {MAP_ONLY_DAYS})")
    bf.add_argument("--map-only", action="store_true",
                    help="지도 전용 — 종목 목록·수급(ka10099·ka10059)만 부른다(종목당 약 1~3회). 끝날은 KRX 달력")
    bf.add_argument("--sectors", default="", help="쉼표로 준 키움 업종명(stocks.sector)")
    bf.add_argument("--codes", default="", help="쉼표로 준 종목코드(섹터와 합집합)")
    bf.add_argument("--limit", type=int, default=0)
    bf.add_argument("--no-raw", action="store_true", help="무수정 일봉(daily_bars_raw)을 건너뛴다")
    bf.add_argument("--resume", action="store_true", help="같은 기준일에 이미 끝난 종목을 건너뛴다")
    dl = sub.add_parser("daily", help="마지막 저장일부터 오늘(KRX 거래일, 20시 KST 뒤)까지 증분 — 다시 돌리면 이어 받는다")
    dl.add_argument("--db", required=True, help="sqlite 경로(먼저 backfill)")
    _broker_arg(dl)
    dl.add_argument("--map-only", action="store_true",
                    help="지도 전용 — 종목 목록·수급(ka10099·ka10059)만 부른다(종목당 약 1회)")
    dl.add_argument("--no-raw", action="store_true", help="무수정 일봉(daily_bars_raw)을 건너뛴다")
    sc = sub.add_parser("sectors", help="자기 섹터 표(CSV: code,sector) → stocks.sector. 증권사 목록이 덮지 않는다(호출 없음)")
    sc.add_argument("--db", required=True, help="sqlite 경로(먼저 universe)")
    sc.add_argument("--csv", required=True, help="머리줄 code,sector(또는 KRX 내려받기의 종목코드,업종명)인 CSV")
    sc.add_argument("--label", help="섹터 출처 이름(지도에 '사용자 표(이름)'으로 나온다, 기본: 파일 이름)")
    sc.add_argument("--add-missing", action="store_true",
                    help="표에 이름·시장(name,market 또는 종목명,시장구분)이 있는 보통주 중 stocks 에 없는 것을 넣는다 — "
                         "목록 API 가 없는 증권사용")
    rs = sub.add_parser("reseed", help="저장된 스냅숏·기업행동으로 주식수 시드를 다시 만든다(호출 없음)")
    rs.add_argument("--db", required=True)
    ds = sub.add_parser("dart-shares", help="DART 분기 발행주식총수로 주식수 이력을 채운다(본인 DART_API_KEY, 선택)")
    ds.add_argument("--db", required=True)
    ds.add_argument("--codes", default="", help="쉼표로 준 종목코드(기본: stocks 전부)")
    ds.add_argument("--since", help="이 날을 덮는 분기말부터(기본: 첫 수급일)")
    ds.add_argument("--until", help="이 날 이하 마지막 분기말까지(기본: 오늘)")
    ds.add_argument("--dart-rate", type=float, default=1.0, help="DART 속도[req/s] (기본 1.0)")
    ds.add_argument("--dart-max-calls", type=int, default=1000, help="이 실행의 DART 호출 상한(기본 1000)")
    args = ap.parse_args(argv)
    try:
        return {"smoke": cmd_smoke, "universe": cmd_universe, "backfill": cmd_backfill, "daily": cmd_daily,
                "reseed": cmd_reseed, "dart-shares": cmd_dart_shares, "sectors": cmd_sectors}[args.cmd](args)
    except MissingKeys as e:
        # 처음 치는 사람이 가장 먼저 만나는 실패다 — 트레이스백 대신 갈 길 한 줄(docs/byo/quickstart.md §1).
        raise SystemExit(f"⏹ {e} --env-file 로 키 파일을 주거나 환경변수를 둔다(docs/byo/quickstart.md)") from None
    except CallBudgetExceeded as e:
        # 끝낸 종목은 이미 커밋됐다 — 같은 명령을 다시 치면 이어 받는다.
        print(f"⏹ {mask(str(e))} — --max-calls 를 올리고 같은 명령을 다시 친다(backfill 은 --resume)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
