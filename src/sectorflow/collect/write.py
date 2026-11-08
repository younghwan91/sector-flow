"""sqlite 기록 — ``storage.SCHEMA`` 그대로 + BYO 원장 두 표.

리포트 코드는 스키마를 바꾸지 않고 이 파일을 읽는다(``storage.connect`` 가 경로가 DSN 이 아니면 sqlite 를 연다).
더하는 것은 리포트가 읽지 않는 표뿐이다:

- ``collect_runs`` — 실행 원장(종류·기준일·TR별 호출 수·행 수·실패). 확정 판정(설계 §2.4)의 근거가 된다.
- ``shares_provenance`` — 주식수 행이 어디서 왔나(``kiwoom`` 스냅숏 / ``seed`` 역산 / ``dart``).
  ``shares_outstanding_history`` 에는 source 열이 없어 옆 표로 둔다.
- ``PRAGMA user_version`` — 스키마 판. 전진 마이그레이션만 한다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sectorflow.storage import INVESTOR_COLUMNS, connect

SCHEMA_VERSION = 2

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


def upsert_stocks(con, rows: list[dict]) -> int:
    return _upsert(con, "stocks", ("code", "name", "market", "sector", "kind"),
                   [(r["code"], r["name"], r["market"], r["sector"], r["kind"]) for r in rows])


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
