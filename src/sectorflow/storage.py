"""kr_quant 읽기 정문 — 일일 리포트가 쓰는 만큼만.

swing-it `src/swing_it/storage.py` 에서 2026-11-02 에 그대로 복사한 부분집합이다(공유 라이브러리를
만들지 않고 독립 사본을 둔다 — quant-airflow 의 `collectors/storage.py` 와 같은 선택). 원본이 고쳐져도
여기로 자동으로 오지 않는다. 시총·수급 정의를 바꿀 때는 양쪽을 같이 본다.

DB 는 읽기만 한다. 수집·스키마 변경은 quant-airflow 소관이고 아래 SCHEMA 는 테스트용 sqlite 뿐이다.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

_PG_PREFIXES = ("postgresql://", "postgres://")
#: daily_bars.date 를 문자열로 바꾼 모양 — market_cap_asof_bulk 의 정확 일치 조인 키.
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def db_default() -> str:
    """CLI ``--db`` flag default: ``KR_QUANT_DB`` env var if set, else local sqlite.

    Lets an analysis-only checkout point at the shared TimescaleDB once (via
    ``.env``/``export KR_QUANT_DB=postgresql://...``) instead of passing
    ``--db`` on every command. ``.env`` 을 스스로 읽지는 않는다.
    """
    return os.environ.get("KR_QUANT_DB") or str(default_db_path())


# ka10059 (투자자기관별종목별) net-buy fields → DB columns.
# Order matters: it defines the column order for ``supply_demand`` inserts.
INVESTOR_COLUMNS: dict[str, str] = {
    "individual": "ind_invsr",   # 개인
    "foreign_": "frgnr_invsr",   # 외국인
    "institution": "orgn",       # 기관계
    "fnnc_invt": "fnnc_invt",    # 금융투자
    "insrnc": "insrnc",          # 보험
    "invtrt": "invtrt",          # 투신
    "bank": "bank",              # 은행
    "penfnd_etc": "penfnd_etc",  # 연기금 등
    "samo_fund": "samo_fund",    # 사모펀드
    "natn": "natn",              # 국가
    "etc_corp": "etc_corp",      # 기타법인
}

_INVESTOR_COL_DDL = ",\n            ".join(f"{c} INTEGER" for c in INVESTOR_COLUMNS)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS stocks (
    code   TEXT PRIMARY KEY,
    name   TEXT,
    market TEXT,
    sector TEXT,
    kind   TEXT
);
CREATE TABLE IF NOT EXISTS supply_demand (
    code         TEXT NOT NULL,
    date         TEXT NOT NULL,
    close        INTEGER,
    flu_rt       REAL,
    acc_trde_qty INTEGER,
    {_INVESTOR_COL_DDL},
    -- kiwoom = 11개 분류 전체. naver = 폐지 종목 부분 백필(기관·외국인 순매매만,
    -- 나머지는 NULL — 0 은 '순매매 없음'이고 NULL 은 '모름'이라 구분해야 한다).
    -- 외국인 정의도 다르다: 네이버 값은 4개 분류 합이 0이 되도록 맞춘 수치다.
    source       TEXT NOT NULL DEFAULT 'kiwoom',
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_sd_date ON supply_demand(date);
-- source: 'kiwoom' = 상장 종목(ka10081, trade_value 는 보고된 거래대금),
--         'naver'  = 상장폐지 종목 백필(siseJson, trade_value 는 close*volume/1e6
--                    근사 — 실측 오차 중앙값 0.7%, p95 3.6%).
-- 폐지 종목이 이 테이블에 들어오는 이유는 생존편향 때문이다: 수집 소스가 현재 상장
-- 종목만 돌려주므로, 그냥 두면 백테스트가 살아남은 회사만 보고 성적을 잰다.
CREATE TABLE IF NOT EXISTS daily_bars (
    code        TEXT NOT NULL,
    date        TEXT NOT NULL,
    open        INTEGER,
    high        INTEGER,
    low         INTEGER,
    close       INTEGER,
    volume      INTEGER,
    trade_value INTEGER,
    source      TEXT NOT NULL DEFAULT 'kiwoom',
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_db_date ON daily_bars(date);
CREATE TABLE IF NOT EXISTS sector_index (
    code        TEXT NOT NULL,  -- 업종코드 (001=KOSPI 종합, 101=KOSDAQ 종합 등)
    name        TEXT,
    date        TEXT NOT NULL,
    open        INTEGER,
    high        INTEGER,
    low         INTEGER,
    close       INTEGER,
    volume      INTEGER,
    trade_value INTEGER,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_si_date ON sector_index(date);
CREATE TABLE IF NOT EXISTS shares_outstanding_history (
    code               TEXT NOT NULL,
    date               TEXT NOT NULL,
    shares_outstanding INTEGER,  -- sqlite INTEGER is dynamically 64-bit already;
    PRIMARY KEY (code, date)     -- Postgres side (init_timescale.sql) must use BIGINT, not INTEGER(32bit) — 삼성전자(58억주) overflows it
);
CREATE INDEX IF NOT EXISTS idx_sh_date ON shares_outstanding_history(date);
"""


def default_db_path() -> Path:
    """Default DB location: ``<repo>/data/sector_flow.db`` (gitignored)."""
    return Path(__file__).resolve().parents[2] / "data" / "sector_flow.db"


def connect(db_path: str | Path | None = None) -> Any:
    """Open a connection with row access.

    ``db_path`` starting with ``postgresql://``/``postgres://`` opens Postgres
    (e.g. TimescaleDB) via psycopg2. Anything else is treated as a sqlite file
    path (default: ``<repo>/data/sector_flow.db``, dirs created as needed).
    """
    if isinstance(db_path, str) and db_path.startswith(_PG_PREFIXES):
        import psycopg2  # noqa: PLC0415 — optional dep, only needed for this path

        con = psycopg2.connect(db_path)
        # Schema (tables, hypertables, compression policy) is provisioned by
        # quant-airflow/sql/init_timescale.sql, not here — init_db() only
        # applies to the sqlite path.
        return con

    path = Path(db_path) if db_path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    init_db(con)
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


def _is_pg(con: Any) -> bool:
    return not isinstance(con, sqlite3.Connection)


SUPPLY_TABLE = "supply_demand"
_SUPPLY_READ_COLS = ("code", "date", "individual", "foreign_", "institution")
#: 기관 세부 8종. 키움 소스에만 있고 네이버 폐지 백필에는 없다.
INSTITUTION_DETAIL_COLS = ("fnnc_invt", "insrnc", "invtrt", "bank",
                           "penfnd_etc", "samo_fund", "natn")


#: 네이버 폐지 백필이 채우는 컬럼. 나머지는 전부 NULL 이다.
NAVER_SUPPLY_COLS = ("code", "date", "close", "flu_rt", "acc_trde_qty",
                     "foreign_", "institution", "source")
#: 키움 소스에만 있는 컬럼 — 이걸 쓰면 유니버스가 현재 상장분으로 좁아진다.
KIWOOM_ONLY_SUPPLY_COLS = ("individual", "etc_corp") + INSTITUTION_DETAIL_COLS


def read_supply_demand(con: Any, *, cols: "tuple[str, ...] | None" = None,
                       start: "str | None" = None, end: "str | None" = None,
                       sources: "tuple[str, ...] | None" = None,
                       require_delisted: bool = True,
                       allow_individual_survivorship: bool = False):
    """수급 패널 정문 — 가격의 :func:`read_prices` 에 대응하는 자리.

    이 테이블에는 오랫동안 정문이 없었다. 그래서 소비자들이 ``supply_demand JOIN
    stocks`` 로 읽었는데 ``stocks`` 는 **현재 상장** 마스터라 WHERE 절이 없어도 폐지
    종목이 구조적으로 빠졌고, 검증 스택은 넘겨받은 표본만 보므로 모든 게이트가 초록인
    채로 성적만 부풀었다(GUARDRAILS §4 공백 2). 그 코드는 2026-08-16 에 삭제됐고,
    이 함수가 그 자리를 대신한다.

    **소스별로 컬럼이 반쪽이다.** 키움(현재 상장)은 11개 분류를 다 주지만, 네이버
    폐지 백필은 **외국인·기관 순매매량만** 준다 — ``individual`` 이 NULL 이다. 따라서
    ``individual`` 을 쓰면서 폐지 종목을 함께 담는 것은 **현재 데이터로 불가능**하고,
    ``individual IS NOT NULL`` 로 거르는 순간 조용히 생존편향이 들어온다. 그 선택을
    호출자가 **명시**하게 만드는 것이 이 함수의 존재 이유다.

    Args:
        con: 열린 연결.
        cols: 반환 컬럼(기본 :data:`_SUPPLY_READ_COLS`). 기관 세부는
            :data:`INSTITUTION_DETAIL_COLS` 를 붙여 요청한다.
        start: 조회 시작일(``YYYY-MM-DD``, 포함). ``None`` 이면 처음부터.
        end: 조회 종료일(포함). ``None`` 이면 끝까지.
        require_delisted: 조회 **구간 안에** 수급이 존재하는 폐지 종목이 결과에도
            담겼는지 검사한다. 구간을 좁히면 기대치도 그 구간으로 좁혀지므로,
            "오늘 하루" 같은 시점 조회에서 헛되이 터지지 않는다.
        allow_individual_survivorship: ``individual`` 을 요청하면서 폐지 종목 커버리지를
            포기한다고 **명시**할 때 ``True``. 명시 없이 둘을 함께 요구하면
            ``AssertionError`` 로 막는다 — 조용한 생존편향보다 시끄러운 실패가 낫다.

    Returns: pandas DataFrame.
    """
    import pandas as pd

    use = tuple(cols or _SUPPLY_READ_COLS)
    kiwoom_only = tuple(c for c in use if c in KIWOOM_ONLY_SUPPLY_COLS)
    if kiwoom_only and require_delisted and not allow_individual_survivorship:
        raise AssertionError(
            f"{', '.join(kiwoom_only)} 는 키움 소스에만 있고 네이버 폐지 백필에는 "
            "NULL 이라, 폐지 종목을 함께 담을 수 없다. 더 고약한 건 **지표마다 "
            "유니버스가 달라진다**는 점이다 — institution 으로 집계하면 폐지분이 들어오고 "
            "individual 로 집계하면 같은 종목이 NULL 로 빠져서, 두 막대를 나란히 그리면 "
            "분모가 다른 걸 비교하게 된다. 개인·기관세부가 꼭 필요하면 "
            "sources=('kiwoom',) 로 유니버스를 명시적으로 좁히고 "
            "allow_individual_survivorship=True 로 생존편향을 감수한다고 적어라."
        )

    ph = "%s" if _is_pg(con) else "?"
    where, params = [], []
    if start is not None:
        where.append(f"date >= {ph}")
        params.append(start)
    if end is not None:
        where.append(f"date <= {ph}")
        params.append(end)
    if sources is not None:
        where.append(f"source IN ({', '.join([ph] * len(sources))})")
        params.extend(sources)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sel = ", ".join(use)
    df = pd.read_sql_query(  # noqa: S608 — 컬럼은 모듈 상수, 날짜는 파라미터 바인딩
        f"SELECT {sel} FROM {SUPPLY_TABLE}{clause}", con, params=tuple(params) or None)
    if require_delisted:
        _assert_supply_has_delisted(con, df, start=start, end=end)
    return df


def _assert_supply_has_delisted(con: Any, df, *, start, end) -> None:
    """조회 구간에 수급이 있는 폐지 종목이 결과에도 담겼는지 확인."""
    ph = "%s" if _is_pg(con) else "?"
    where, params = ["source = " + ph], ["naver"]
    if start is not None:
        where.append(f"date >= {ph}")
        params.append(start)
    if end is not None:
        where.append(f"date <= {ph}")
        params.append(end)
    sql = (f"SELECT DISTINCT code FROM {SUPPLY_TABLE} "  # noqa: S608 — 모듈 상수
           f"WHERE {' AND '.join(where)}")
    if _is_pg(con):
        with con.cursor() as cur:
            cur.execute(sql, tuple(params))
            expected = {r[0] for r in cur.fetchall()}
    else:
        expected = {r[0] for r in con.execute(sql, tuple(params)).fetchall()}
    if not expected:
        return  # 그 구간에 폐지 종목 수급이 아예 없다 — 요구할 수 없다.
    if "code" not in getattr(df, "columns", []):
        return  # code 를 안 뽑았으면 검사할 수단이 없다.
    if not (expected & set(df["code"].astype(str))):
        raise AssertionError(
            f"{SUPPLY_TABLE} 조회 구간에 폐지 종목 수급 {len(expected)}개가 있는데 "
            f"결과엔 하나도 없다 — stocks(현재 상장 마스터) 조인이나 "
            f"individual IS NOT NULL 필터가 걸렸을 수 있다. 의도한 것이면 "
            f"require_delisted=False 로 명시하라."
        )


def market_cap_asof_bulk(con: Any, df: Any) -> Any:
    """Vectorized :func:`market_cap_asof` for many ``(code, date)`` rows at once.

    Byte-identical semantics to the per-row function — close comes from
    ``daily_bars`` on the exact date (NOT any close column the caller's
    frame may already carry, e.g. ``supply_demand.close``: that is a
    different, not-necessarily-equal price series in this DB — verified by
    spot-checking real rows, e.g. code 118000 on 2026-03-25 has
    ``daily_bars.close=2410`` vs ``supply_demand.close=241``). Shares
    outstanding is the most recent row with ``date <= date`` (lookahead-safe,
    same rule as the per-row SQL ``date<=? ORDER BY date DESC LIMIT 1``).

    This issues 2 bulk queries total instead of 2 queries per row —
    ``market_cap_asof`` called in a per-row Python loop over a multi-year
    dataset means millions of round trips to Postgres.

    Args:
        df: Must have ``code`` and ``date`` columns.

    Returns:
        A ``pandas.Series`` aligned with ``df.index``: ``market_cap`` per
        row, ``NaN`` where close or shares-outstanding is unavailable
        (mirrors ``market_cap_asof`` returning ``None``).
    """
    import pandas as pd  # noqa: PLC0415 — pandas is a strategy-layer dep, not storage's

    if df.empty:
        return pd.Series([], dtype=float, index=df.index)

    codes = df["code"].unique().tolist()
    ph = "%s" if _is_pg(con) else "?"
    placeholders = ",".join([ph] * len(codes))
    # 종가는 아래에서 (code, 'YYYY-MM-DD') **정확 일치**로만 붙는다 — 요청 날짜 밖의 행은 읽어도
    # 버려진다. 예전엔 호출마다 daily_bars 전체 이력(580만 행)을 읽어 sector_flow 한 번에 16회,
    # 166초를 썼다. 정확 일치할 수 있는 날짜(형식이 맞는 문자열)만 쿼리에 넣는다 — 형식이 다른
    # 문자열은 예전에도 어떤 행과도 일치하지 않았으므로 결과가 같다.
    if isinstance(df["date"].dtype, pd.DatetimeTZDtype):
        # 예전엔 merge_asof 가 tz-naive 주식수 날짜와 섞이며 MergeError 로 멈췄다 — 조용한 NaN 대신 계속 멈춘다.
        raise TypeError("market_cap_asof_bulk: tz-aware date column is not supported")
    date_strs = df["date"].astype(str)
    # 아래 merge 에서 하는 파싱을 쿼리 앞으로 당긴다 — 날짜가 아닌 문자열은 바로 예외.
    # (예전엔 bars·shares 가 비면 파싱 전에 NaN 을 돌려줬다. 그 경우만 예외가 앞당겨진다.)
    pd.to_datetime(date_strs)
    wanted = sorted({s for s in date_strs.unique() if isinstance(s, str) and _ISO_DAY.fullmatch(s)})
    if not wanted:
        return pd.Series([float("nan")] * len(df), index=df.index)
    date_ph = ",".join([ph] * len(wanted))
    bars = pd.read_sql_query(
        f"SELECT code, date, close FROM daily_bars WHERE code IN ({placeholders}) "
        f"AND date IN ({date_ph})",
        con,
        params=[*codes, *wanted],
    )
    bars["date"] = bars["date"].astype(str)

    shares = pd.read_sql_query(
        f"SELECT code, date, shares_outstanding FROM shares_outstanding_history "
        f"WHERE code IN ({placeholders})",
        con,
        params=codes,
    )
    if bars.empty or shares.empty:
        return pd.Series([float("nan")] * len(df), index=df.index)

    shares["date"] = pd.to_datetime(shares["date"].astype(str))
    # merge_asof with `by=` still requires the `on` column sorted globally
    # (not just within each `by` group) — sort by date first, code second.
    shares = shares.sort_values(["date", "code"])

    d = df[["code"]].reset_index(drop=True).copy()
    d["date_str"] = df["date"].astype(str).to_numpy()
    d["_row"] = d.index
    d = d.merge(bars, left_on=["code", "date_str"], right_on=["code", "date"], how="left")
    d["date"] = pd.to_datetime(d["date_str"])
    d_sorted = d.sort_values(["date", "code"])

    merged = pd.merge_asof(
        d_sorted,
        shares[["code", "date", "shares_outstanding"]],
        on="date",
        by="code",
        direction="backward",
    )
    merged = merged.sort_values("_row")
    # per-row 와 동일 semantics: 주식수가 0 이하면 결측으로 떨어뜨린다(더 오래된 유효
    # 행으로 대체하지 않는다 — 그러면 두 함수의 결과가 갈린다).
    merged.loc[merged["shares_outstanding"] <= 0, "shares_outstanding"] = float("nan")
    market_cap = (merged["close"] * merged["shares_outstanding"]).to_numpy()
    return pd.Series(market_cap, index=df.index)
