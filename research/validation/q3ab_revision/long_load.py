"""Q3a/Q3b 공용: 한 번의 긴 적재(48 기준일 전체를 덮는 260+47 거래일) → pickle.

sector_flow.load()/attach_daily_cap() 를 그대로 쓰되 기간만 넓힌다. 종목-일 단위 값
(수량×종가, 시총)은 창과 무관하므로 긴 적재를 기준일별로 잘라도 같다 — 창에 의존하는
것은 cap_lag(종목의 창 안 첫 행은 NaN)와 기간말 섹터 시총뿐이고, slice_build.py 가
그것을 기준일마다 다시 계산한다.

Q3b 용으로 daily_bars_raw / daily_bars 의 거래대금·거래량, regular_close 도 같이 붙인다.
"""
import os
import pickle
import sys

import pandas as pd

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import FIRST_RUN_REV, connect_ro, out_dir, reports_dir, use_report_code  # noqa: E402

HERE = str(out_dir(__file__))
use_report_code(FIRST_RUN_REV, Path(HERE) / "report_code")  # 첫 검증의 리포트 코드(bd27017)
import sector_flow as sf  # noqa: E402
from sectorflow.storage import read_supply_demand  # noqa: E402

REPORTS = str(reports_dir())
# 첫 검증의 기준일 48개(2026-08-27~11-06). 그 뒤 게시본은 고친 코드로 만들어져 비교 대상이 아니다.
LAST_ASOF = "2026-11-06"
ASOFS = sorted(d for d in os.listdir(REPORTS) if d[:2] == "20" and len(d) == 10 and d <= LAST_ASOF)
con_ro = connect_ro


def main():
    con = con_ro()
    cur = con.cursor()
    cur.execute("SELECT DISTINCT date FROM supply_demand WHERE date <= %s ORDER BY date", (ASOFS[-1],))
    all_dates = [str(r[0]) for r in cur.fetchall()]
    i_first = all_dates.index(ASOFS[0])
    start = all_dates[i_first - 259]
    dates = [d for d in all_dates if d >= start]
    print("asofs", len(ASOFS), ASOFS[0], ASOFS[-1], "long window", start, "~", dates[-1], len(dates))

    # sector_flow.load 와 같은 본문, 기간만 넓힘
    sd = read_supply_demand(
        con,
        cols=("code", "date", "close", "flu_rt", "acc_trde_qty",
              "individual", "foreign_", "institution", "etc_corp")
             + tuple(c for c, _ in sf.INST_DETAIL),
        start=dates[0], end=dates[-1],
        sources=("kiwoom",), allow_individual_survivorship=True, require_delisted=False,
    )
    sd["date"] = sd["date"].astype(str)
    meta = pd.read_sql_query("SELECT code, name, sector, market FROM stocks", con)
    df = sd.merge(meta, on="code", how="inner")
    df["close"] = df["close"].abs()
    df["sector"] = df["sector"].fillna("").replace("", "(미분류)")
    for raw, short in sf.ACTORS:
        df[short] = df[raw].astype(float) * df["close"] / sf.EOK
    for raw, _ in sf.INST_DETAIL:
        df[raw] = df[raw].astype(float) * df["close"] / sf.EOK
    df["tv"] = df["acc_trde_qty"].astype(float) * df["close"] / sf.EOK
    df["ret"] = df["flu_rt"].astype(float) / 100.0
    df = sf.attach_daily_cap(con, df)   # cap 은 종목-일 정확일치 → 창 무관. cap_lag 는 slice 에서 다시
    print("rows", len(df))

    # Q3b 가격들
    raw = pd.read_sql_query(
        "SELECT code, date, close AS raw_close, volume AS raw_vol, trade_value AS raw_tv "
        "FROM daily_bars_raw WHERE date >= %s AND date <= %s", con, params=(dates[0], dates[-1]))
    bars = pd.read_sql_query(
        "SELECT code, date, close AS db_close, volume AS db_vol, trade_value AS db_tv "
        "FROM daily_bars WHERE date >= %s AND date <= %s", con, params=(dates[0], dates[-1]))
    reg = pd.read_sql_query(
        "SELECT code, date, close AS reg_close, src AS reg_src FROM regular_close "
        "WHERE date >= %s AND date <= %s", con, params=(dates[0], dates[-1]))
    for t in (raw, bars, reg):
        t["date"] = t["date"].astype(str)
    px = df[["code", "date"]].merge(raw, on=["code", "date"], how="left") \
        .merge(bars, on=["code", "date"], how="left").merge(reg, on=["code", "date"], how="left")
    con.close()
    with open(os.path.join(HERE, "long.pkl"), "wb") as f:
        pickle.dump({"df": df, "px": px, "all_dates": all_dates, "asofs": ASOFS}, f)
    print("wrote long.pkl")


if __name__ == "__main__":
    main()
