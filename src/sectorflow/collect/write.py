"""sqlite 기록 — ``storage.SCHEMA`` 그대로 + BYO 원장 두 표.

리포트 코드는 스키마를 바꾸지 않고 이 파일을 읽는다(``storage.connect`` 가 경로가 DSN 이 아니면 sqlite 를 연다).
더하는 것은 리포트가 읽지 않는 표뿐이다:

- ``collect_runs`` — 실행 원장(종류·기준일·TR별 호출 수·행 수·실패). 확정 판정(설계 §2.4)의 근거가 된다.
- ``shares_provenance`` — 주식수 행이 어디서 왔나(``kiwoom`` 스냅숏 / ``seed`` 역산 / ``dart``).
  ``shares_outstanding_history`` 에는 source 열이 없어 옆 표로 둔다.
- ``dart_fetch`` — DART 주식 총수 조회 원장((종목, 사업연도, 보고서)마다 status·값·접수번호). 다시 돌리면 확정된
  칸은 건너뛴다(:mod:`.dart`).
- ``stocks.sector_source`` — 섹터가 어디서 왔나(``kiwoom`` 키움 업종명 · 증권사 이름 · ``csv:<이름>`` 사용자 표).
  리포트 공용 ``storage.SCHEMA`` 에는 없는 BYO 전용 열이다(kr_quant 는 키움 업종 하나라 필요 없다). 판 4 에서 더했고,
  그 전 행은 키움만 쓸 수 있었으므로 섹터가 있는 행을 ``kiwoom`` 으로 채운다. 쓰는 규칙은 :func:`upsert_stocks`.
- ``PRAGMA user_version`` — 스키마 판. 전진 마이그레이션만 한다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sectorflow.storage import INVESTOR_COLUMNS, connect

SCHEMA_VERSION = 4
#: 사용자 섹터 표(``sector-flow-collect sectors --csv``)의 출처 머리. 이 출처의 섹터는 증권사 목록이 덮지 않는다.
CSV_SOURCE = "csv:"

BYO_DDL = """
CREATE TABLE IF NOT EXISTS collect_runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    kind        TEXT NOT NULL,
    asof        TEXT,
    calls       TEXT,      -- JSON {api_id: n}
    rows        TEXT,      -- JSON {table: n}
    failed      TEXT,      -- JSON [[code, tr, 이유]]
    ok          INTEGER,
    notes       TEXT       -- JSON [[code, tr, 메모]] — 실패는 아닌 것(신규 상장의 짧은 이력, 운영 창 중단)
);
-- 기준일별로 끝난 종목 — backfill --resume 이 건너뛴다.
CREATE TABLE IF NOT EXISTS collect_done (
    code TEXT NOT NULL, asof TEXT NOT NULL, run_id INTEGER, done_at TEXT, PRIMARY KEY (code, asof)
);
CREATE TABLE IF NOT EXISTS dart_fetch (
    code TEXT NOT NULL, bsns_year INTEGER NOT NULL, reprt_code TEXT NOT NULL,
    period_end TEXT NOT NULL,  -- 물은 분기말(YYYY-MM-DD)
    status     TEXT NOT NULL,  -- DART status(000 값 있음 · 013 공시 없음 · no_common 보통주 행 없음 …)
    shares     INTEGER,        -- 보통주 istc_totqy
    stlm_dt    TEXT,           -- 응답의 기준일 = shares_outstanding_history.date
    rcept_no   TEXT,           -- 접수번호(앞 8자리가 공시 접수일)
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (code, bsns_year, reprt_code)
);
CREATE TABLE IF NOT EXISTS shares_provenance (
    code TEXT NOT NULL, date TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY (code, date)
);
"""

_SD_COLS = ("code", "date", "close", "flu_rt", "acc_trde_qty", *INVESTOR_COLUMNS, "source")
_BAR_COLS = ("code", "date", "open", "high", "low", "close", "volume", "trade_value", "source")
_IDX_COLS = ("code", "name", "date", "open", "high", "low", "close", "volume", "trade_value")


def open_db(path: "str | Path") -> sqlite3.Connection:
    p = str(path)
    if p.startswith(("postgres://", "postgresql://")):
        raise ValueError("BYO 수집기는 sqlite 에만 쓴다 — kr_quant 는 읽기 전용이다")
    con = connect(p)
    con.executescript(BYO_DDL)
    # storage.SCHEMA 의 조정 표는 종가만 있는데 verify_report 의 B 검사가 high·low 도 읽는다(kr_quant 표에는 있다).
    have = {r[1] for r in con.execute("PRAGMA table_info(daily_bars_adjusted)").fetchall()}
    for col in ("open", "high", "low"):
        if col not in have:
            con.execute(f"ALTER TABLE daily_bars_adjusted ADD COLUMN {col} REAL")
    if "notes" not in {r[1] for r in con.execute("PRAGMA table_info(collect_runs)").fetchall()}:
        con.execute("ALTER TABLE collect_runs ADD COLUMN notes TEXT")   # 판 1 → 2
    if "sector_source" not in {r[1] for r in con.execute("PRAGMA table_info(stocks)").fetchall()}:
        con.execute("ALTER TABLE stocks ADD COLUMN sector_source TEXT")  # 판 3 → 4
        # 판 4 전에는 키움(ka10099 upName)만 stocks 를 썼다.
        con.execute("UPDATE stocks SET sector_source = 'kiwoom' WHERE COALESCE(sector, '') != ''")
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    if ver < SCHEMA_VERSION:
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    con.commit()
    return con


def _upsert(con: sqlite3.Connection, table: str, cols: tuple[str, ...], rows: list[tuple]) -> int:
    if not rows:
        return 0
    ph = ",".join("?" * len(cols))
    con.executemany(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({ph})", rows)
    return len(rows)


def resolve_sector(old: "tuple[str | None, str | None] | None", sector: str,
                   source: "str | None") -> "tuple[str, str | None]":
    """저장된 ``(섹터, 출처)`` 와 들어온 섹터·출처 → 쓸 ``(섹터, 출처)``. 섹터는 한 벌로 지킨다(``docs/byo/brokers.md`` §1.3):

    1. 사용자 표(``csv:…``)에서 온 섹터는 덮지 않는다 — 사용자가 고른 분류다.
    2. 들어온 섹터가 있으면 그것(과 그 출처).
    3. 들어온 섹터가 없고 **다른 출처**가 준 섹터가 저장돼 있으면 그대로 — 섹터를 안 주는 증권사로 목록을 받아도 키움 키로
       받아 둔 업종이 지워지지 않는다.
    4. 같은 출처가 이제 섹터를 비웠으면 비운다(옛 동작).
    """
    sector = sector or ""
    if old is not None:
        o_sec, o_src = old[0] or "", old[1] or ""
        if o_src.startswith(CSV_SOURCE) or (not sector and o_sec and o_src != (source or "")):
            return o_sec, (o_src or None)
    return sector, (source if sector else None)


def stored_sectors(con, codes) -> dict[str, tuple]:
    """``{code: (sector, sector_source)}`` — ``codes`` 중 ``stocks`` 에 있는 것."""
    codes = list(codes)
    out: dict[str, tuple] = {}
    for k in range(0, len(codes), 500):
        part = codes[k:k + 500]
        q = f"SELECT code, sector, sector_source FROM stocks WHERE code IN ({','.join('?' * len(part))})"
        out.update({c: (sec, src) for c, sec, src in con.execute(q, part).fetchall()})
    return out


def with_stored_sectors(con, rows: list[dict]) -> list[dict]:
    """종목 목록 행의 섹터·출처를 :func:`resolve_sector` 로 고친 사본 — ``--sectors`` 조각을 고르기 전에 쓴다."""
    old = stored_sectors(con, [r["code"] for r in rows])
    out = []
    for r in rows:
        sec, src = resolve_sector(old.get(r["code"]), r["sector"], r.get("sector_source"))
        out.append({**r, "sector": sec, "sector_source": src})
    return out


def upsert_stocks(con, rows: list[dict]) -> int:
    """종목 목록 → ``stocks``. 이름·시장·종류는 늘 새 값, 섹터·출처는 :func:`resolve_sector`."""
    rows = with_stored_sectors(con, rows)
    return _upsert(con, "stocks", ("code", "name", "market", "sector", "kind", "sector_source"),
                   [(r["code"], r["name"], r["market"], r["sector"], r["kind"], r["sector_source"]) for r in rows])


def import_sectors(con, table: dict[str, str], label: str) -> dict:
    """사용자 섹터 표(종목코드 → 섹터) → ``stocks``. 출처 ``csv:<label>`` — 뒤의 증권사 목록이 덮지 않는다.

    ``stocks`` 에 있는 종목만 바꾼다(이름·시장은 종목 목록이 준다). ``{"updated": n, "unknown": [코드…]}``.
    """
    have = {r[0] for r in con.execute("SELECT code FROM stocks").fetchall()}
    hit = [(sec, CSV_SOURCE + label, code) for code, sec in table.items() if code in have and sec]
    con.executemany("UPDATE stocks SET sector = ?, sector_source = ? WHERE code = ?", hit)
    return {"updated": len(hit), "unknown": sorted(c for c in table if c not in have)}


def upsert_supply_demand(con, rows: list[tuple]) -> int:
    return _upsert(con, "supply_demand", _SD_COLS, rows)


def upsert_bars(con, rows: list[tuple]) -> int:
    return _upsert(con, "daily_bars", _BAR_COLS, rows)


def upsert_adjusted(con, rows: list[tuple]) -> int:
    """수정주가 한 응답의 종가 → ``daily_bars_adjusted``(설계 §2.3: 한 응답 안은 이음매가 없다)."""
    return _upsert(con, "daily_bars_adjusted", ("code", "date", "open", "high", "low", "close"),
                   [(r[0], r[1], float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows if r[5]])


def upsert_raw(con, rows: list[tuple]) -> int:
    return _upsert(con, "daily_bars_raw", ("code", "date", "close"),
                   [(r[0], r[1], r[5]) for r in rows if r[5]])


def replace_events(con, code: str, rows: list[tuple], lo: str, hi: str) -> int:
    """``corporate_actions`` 는 sqlite 에 PK 가 없다 — 응답이 덮은 ``[lo, hi]`` 의 그 종목 행만 지우고 다시 쓴다(멱등).

    범위 밖(이전 실행이 받은 더 오래된 이벤트)은 남긴다. 증분 실행의 짧은 응답이 과거 분할·병합을 지우면
    시드·시총 배율이 조용히 틀린다.
    """
    con.execute("DELETE FROM corporate_actions WHERE code = ? AND date >= ? AND date <= ?", (code, lo, hi))
    seen = {(r[0], r[1]): r for r in rows}  # 수정·무수정 응답이 같은 이벤트를 둘 다 싣는다
    return _upsert(con, "corporate_actions", ("code", "date", "upd_stkpc_tp", "upd_rt"), list(seen.values()))


def upsert_index(con, rows: list[tuple]) -> int:
    return _upsert(con, "sector_index", _IDX_COLS, rows)


def upsert_shares(con, rows: list[tuple[str, str, int]], source: str) -> int:
    n = _upsert(con, "shares_outstanding_history", ("code", "date", "shares_outstanding"), rows)
    _upsert(con, "shares_provenance", ("code", "date", "source"), [(c, d, source) for c, d, _ in rows])
    return n


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start_run(con, kind: str, asof: "str | None" = None) -> int:
    cur = con.execute("INSERT INTO collect_runs(started_at, kind, asof) VALUES (?,?,?)", (_now(), kind, asof))
    con.commit()
    return int(cur.lastrowid)


def finish_run(con, run_id: int, *, calls: dict, rows: dict, failed: list, ok: bool,
               asof: "str | None" = None, notes: "list | None" = None) -> None:
    con.execute("UPDATE collect_runs SET finished_at=?, calls=?, rows=?, failed=?, ok=?, "
                "asof=COALESCE(?, asof), notes=? WHERE run_id=?",
                (_now(), json.dumps(calls, sort_keys=True), json.dumps(rows, sort_keys=True),
                 json.dumps(failed, ensure_ascii=False), int(ok), asof,
                 json.dumps(notes or [], ensure_ascii=False), run_id))
    con.commit()


def mark_done(con, code: str, asof: str, run_id: int) -> None:
    con.execute("INSERT OR REPLACE INTO collect_done VALUES (?,?,?,?)", (code, asof, run_id, _now()))


def done_codes(con, asof: str) -> set[str]:
    return {r[0] for r in con.execute("SELECT code FROM collect_done WHERE asof = ?", (asof,)).fetchall()}
