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
-- 무수정 일봉(quant-airflow 026). 시총의 가격 — daily_bars 는 수정주가라 과거 시총이 부푼다.
CREATE TABLE IF NOT EXISTS daily_bars_raw (
    code TEXT NOT NULL, date TEXT NOT NULL, close INTEGER, PRIMARY KEY (code, date)
);
-- 분할 백조정 일봉 — 섹터 수익률의 원천(같은 표 안의 종가 비라 세션 정의가 맞는다).
CREATE TABLE IF NOT EXISTS daily_bars_adjusted (
    code TEXT NOT NULL, date TEXT NOT NULL, close REAL, PRIMARY KEY (code, date)
);
-- 키움 수정주가 이벤트(027). 8 분할 · 16 병합 · 64 감자만 주식수를 다시 센다.
CREATE TABLE IF NOT EXISTS corporate_actions (
    code TEXT NOT NULL, date TEXT NOT NULL, upd_stkpc_tp TEXT, upd_rt REAL
);
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


def recent_dates(con: Any, n: int, asof: "str | None" = None) -> list[str]:
    """최근 ``n`` 거래일(오름차순). ``asof`` 를 주면 그날까지만 본다.

    **모든 소스**의 날짜에서 고른다. 소스를 좁히면 그 소스 행이 없는 날을 건너뛰어 직전 날을
    기준일로 착각한다.
    """
    ph = "%s" if _is_pg(con) else "?"
    where, params = "", [n]
    if asof is not None:
        where, params = f"WHERE date <= {ph} ", [asof, n]
    cur = con.cursor()
    cur.execute(f"SELECT DISTINCT date FROM {SUPPLY_TABLE} {where}"  # noqa: S608 — 모듈 상수
                f"ORDER BY date DESC LIMIT {ph}", tuple(params))
    return sorted(str(r[0]) for r in cur.fetchall())


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


#: 주식수를 다시 세는 수정주가 이벤트 — 분할(8)·병합(16)·감자(64). 가격이 (1+upd_rt/100) 배로 바뀐
#: 만큼 주식수는 그 역수로 바뀐다(078130 10:1 병합 upd_rt 900 → 주식수 ÷10). 권리락(256)은 유상·무상이
#: 섞여 있어 순수 배율이 아니므로 넣지 않는다 — 키움 주간 스냅샷이 1주 안에 따라잡는다.
SHARE_EVENTS = 8 | 16 | 64


def market_cap_asof_bulk(con: Any, df: Any) -> Any:
    """그날 시총(원) = 그날 **원 종가** × 그날의 상장주식수. ``(code, date)`` 여럿을 한 번에.

    가격은 ``supply_demand.close``(키움, 원 종가 — 기준일 저녁에도 있다), 없으면 ``daily_bars_raw``.
    **``daily_bars`` 를 쓰면 안 된다** — 키움 수정주가라 뒤에 병합이 있으면 과거 가격이 배수로 커진다
    (118000 2026-03-25: daily_bars 2,410 · 원 종가 241). 예전엔 이 함수가 바로 그 값을 썼다.

    주식수는 ``date`` 이하 최신 행(앞을 보지 않는다). 그 행 **뒤에** 분할·병합·감자가 있었으면 그만큼
    다시 센다 — 스냅샷이 늦게 오는 구간(078130: 2026-05-20 10:1 병합, 다음 주식수 행은 07-09)에서
    병합 전 주식수 × 병합 후 가격이 시총을 10배로 만들었다(2026-11-08 지표 검증).

    Returns:
        ``df.index`` 에 맞춘 ``pandas.Series``. 가격이나 주식수가 없으면 ``NaN``.
    """
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415 — pandas is a strategy-layer dep, not storage's

    if df.empty:
        return pd.Series([], dtype=float, index=df.index)
    if isinstance(df["date"].dtype, pd.DatetimeTZDtype):
        raise TypeError("market_cap_asof_bulk: tz-aware date column is not supported")

    codes = df["code"].unique().tolist()
    ph = "%s" if _is_pg(con) else "?"
    placeholders = ",".join([ph] * len(codes))
    date_strs = df["date"].astype(str)
    pd.to_datetime(date_strs)  # 날짜가 아닌 문자열은 바로 예외
    wanted = sorted({s for s in date_strs.unique() if isinstance(s, str) and _ISO_DAY.fullmatch(s)})
    if not wanted:
        return pd.Series([float("nan")] * len(df), index=df.index)
    date_ph = ",".join([ph] * len(wanted))

    def _prices(sql: str) -> Any:
        p = pd.read_sql_query(sql.format(c=placeholders, d=date_ph), con, params=[*codes, *wanted])
        p["date"] = p["date"].astype(str)
        p["close"] = p["close"].astype(float).abs()
        return p.drop_duplicates(["code", "date"]).set_index(["code", "date"])["close"]

    sd_px = _prices("SELECT code, date, close FROM supply_demand WHERE source='kiwoom' "
                    "AND code IN ({c}) AND date IN ({d})")
    raw_px = _prices("SELECT code, date, close FROM daily_bars_raw WHERE code IN ({c}) AND date IN ({d})")
    shares = pd.read_sql_query(
        f"SELECT code, date, shares_outstanding FROM shares_outstanding_history "
        f"WHERE code IN ({placeholders})", con, params=codes)
    events = pd.read_sql_query(
        f"SELECT code, date, upd_stkpc_tp, upd_rt FROM corporate_actions WHERE code IN ({placeholders})",
        con, params=codes)
    if shares.empty:
        return pd.Series([float("nan")] * len(df), index=df.index)

    d = pd.DataFrame({"code": df["code"].to_numpy(), "date_str": date_strs.to_numpy()})
    d["_row"] = np.arange(len(d))
    key = pd.MultiIndex.from_arrays([d["code"], d["date_str"]])
    px = sd_px.reindex(key).to_numpy()
    px = np.where(np.isnan(px) | (px <= 0), raw_px.reindex(key).to_numpy(), px)
    d["close"] = px
    d["date"] = pd.to_datetime(d["date_str"])

    shares["date"] = pd.to_datetime(shares["date"].astype(str))
    shares = shares.rename(columns={"date": "s_date"}).sort_values(["s_date", "code"])
    merged = pd.merge_asof(d.sort_values(["date", "code"]), shares, left_on="date",
                           right_on="s_date", by="code", direction="backward")

    # 스냅샷 뒤 이벤트의 배율. C(x) = Σ_{이벤트일 ≤ x} −ln(1+upd_rt/100) 로 두면
    # 주식수(t) = 주식수(s) × exp(C(t) − C(s)).
    ev = events.copy()
    ev["tp"] = pd.to_numeric(ev["upd_stkpc_tp"], errors="coerce").fillna(0).astype(int)
    ev["upd_rt"] = pd.to_numeric(ev["upd_rt"], errors="coerce")
    ev = ev[((ev["tp"] & SHARE_EVENTS) != 0) & ev["upd_rt"].notna() & (ev["upd_rt"] != 0)
            & (ev["upd_rt"] > -100)]
    if ev.empty:
        merged["factor"] = 1.0
    else:
        ev["e_date"] = pd.to_datetime(ev["date"].astype(str))
        ev = ev.sort_values(["code", "e_date"])
        ev["C"] = ev.groupby("code")["upd_rt"].transform(lambda r: (-np.log1p(r / 100.0)).cumsum())
        ev = ev[["code", "e_date", "C"]].sort_values(["e_date", "code"])

        def _c_at(col: str) -> Any:
            m = merged[["_row", "code", col]].dropna(subset=[col]).sort_values([col, "code"])
            m = pd.merge_asof(m, ev, left_on=col, right_on="e_date", by="code", direction="backward")
            return m.set_index("_row")["C"].fillna(0.0)

        c_t, c_s = _c_at("date"), _c_at("s_date")
        merged["factor"] = np.exp(merged["_row"].map(c_t).fillna(0.0)
                                  - merged["_row"].map(c_s).fillna(0.0))

    merged = merged.sort_values("_row")
    sh = merged["shares_outstanding"].astype(float)
    sh[sh <= 0] = float("nan")
    market_cap = (merged["close"] * sh * merged["factor"]).to_numpy()
    return pd.Series(market_cap, index=df.index)
