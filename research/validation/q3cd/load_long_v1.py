"""Q3c·Q3d 긴 적재 — **옛 정의(결함 2·3 포함) — 재현용.** 첫 Q3d(결과 문서 Q3d 재검 표의 'IC(첫 실행)' 열)만 다시 만든다.
결함 2: 수익률 = supply_demand.flu_rt (09-14 이후 애프터마켓 종가 ÷ 정규장 기준가라 이어 곱할 수 없다).
결함 3: 시총 = daily_bars(수정주가) 종가 × as-of 주식수 (병합·분할 뒤 스냅샷 지연을 다시 세지 않는다).
짝이 되는 리포트 코드는 사전등록 커밋 bd27017 판(`slice_build.py --variant v1` 이 꺼내 쓴다).

Load full history once and build per-(market,sector,date) arrays identical in definition to
sector_flow.build_payload (inst flow, own-basket cap_lag-weighted ret, retw), plus code-level
presence and market-cap panels needed to reproduce per-asof `cap` and `names_count`.

Deviation from report code (documented): sources = kiwoom + kiwoom_backfill_* (report uses
('kiwoom',) only, which before 2024 covers ~350-450 codes; the backfill rows are the same vendor
fields for the rest of the currently-listed universe). Report only ever runs on the last 260 days,
where kiwoom alone covers the universe, so this reproduces the report's universe on history.
"""
import os
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import connect_ro, out_dir  # noqa: E402

OUT = out_dir(__file__, 'v1')
EOK = 1e8
SOURCES = ("kiwoom", "kiwoom_backfill_2026-10-11", "kiwoom_backfill_2026-10-12")
START = "2017-01-02"


def main():
    t0 = time.time()
    con = connect_ro()
    dates = [str(d) for d, in pd.read_sql_query(
        "SELECT DISTINCT date FROM supply_demand WHERE date >= %s ORDER BY date", con,
        params=(START,)).itertuples(index=False)]
    years = sorted({d[:4] for d in dates})
    parts = []
    for y in years:
        parts.append(pd.read_sql_query(
            "SELECT code, date, close, flu_rt, acc_trde_qty, institution, source FROM supply_demand "
            "WHERE date >= %s AND date <= %s AND source IN %s",
            con, params=(f"{y}-01-01", f"{y}-12-31", SOURCES)))
        print(y, len(parts[-1]), f"{time.time()-t0:.0f}s", flush=True)
    sd = pd.concat(parts, ignore_index=True)
    sd["date"] = sd["date"].astype(str)
    meta = pd.read_sql_query("SELECT code, name, sector, market FROM stocks", con)
    df = sd.merge(meta, on="code", how="inner")
    df["close"] = df["close"].abs()
    df["sector"] = df["sector"].fillna("").replace("", "(미분류)")
    df["inst"] = df["institution"].astype(float) * df["close"] / EOK
    df["ret"] = df["flu_rt"].astype(float) / 100.0
    codes = sorted(df["code"].unique())

    # market cap panel = daily_bars close (exact date) x shares as-of (<= date), shares<=0 -> NaN
    bars = []
    for y in years:
        b = pd.read_sql_query(
            "SELECT code, date, close FROM daily_bars WHERE date >= %s AND date <= %s",
            con, params=(f"{y}-01-01", f"{y}-12-31"))
        bars.append(b)
    bars = pd.concat(bars, ignore_index=True)
    bars["date"] = bars["date"].astype(str)
    bars = bars[bars["code"].isin(codes) & bars["date"].isin(set(dates))]
    shares = pd.read_sql_query("SELECT code, date, shares_outstanding FROM shares_outstanding_history", con)
    con.close()
    shares = shares[shares["code"].isin(codes)].copy()
    shares["date"] = pd.to_datetime(shares["date"].astype(str))
    shares = shares.sort_values(["date", "code"])
    bars["dt"] = pd.to_datetime(bars["date"])
    m = pd.merge_asof(bars.sort_values(["dt", "code"]), shares[["code", "date", "shares_outstanding"]]
                      .rename(columns={"date": "dt"}), on="dt", by="code", direction="backward")
    m.loc[m["shares_outstanding"] <= 0, "shares_outstanding"] = np.nan
    m["cap"] = m["close"].astype(float) * m["shares_outstanding"].astype(float) / EOK
    print("cap panel", len(m), f"{time.time()-t0:.0f}s", flush=True)

    di = {d: i for i, d in enumerate(dates)}
    ci = {c: i for i, c in enumerate(codes)}
    N, C = len(dates), len(codes)
    capP = np.full((C, N), np.nan)
    capP[m["code"].map(ci).to_numpy(), m["date"].map(di).to_numpy()] = m["cap"].to_numpy()

    # attach_daily_cap semantics: cap at (code,date) of df rows; cap_lag = previous df row of the code
    df["ci"] = df["code"].map(ci)
    df["di"] = df["date"].map(di)
    df["cap"] = capP[df["ci"].to_numpy(), df["di"].to_numpy()]
    df = df.sort_values(["code", "date"])
    df["cap_lag"] = df.groupby("code")["cap"].shift(1)
    pres = np.zeros((C, N), dtype=bool)
    pres[df["ci"].to_numpy(), df["di"].to_numpy()] = True

    markets = sorted(df["market"].dropna().unique())
    sectors = sorted(df["sector"].unique())
    # flows inst (rounded 2 like payload), ret (3), retw (1)
    inst = {mk: {s: np.zeros(N) for s in sectors} for mk in markets}
    ret = {mk: {s: np.zeros(N) for s in sectors} for mk in markets}
    retw = {mk: {s: np.zeros(N) for s in sectors} for mk in markets}
    g = df.groupby(["market", "sector", "di"])["inst"].sum()
    for (mk, s, i), v in g.items():
        inst[mk][s][i] = round(float(v), 2)
    d2 = df.dropna(subset=["cap_lag"]).copy()
    d2["rw"] = d2["ret"] * d2["cap_lag"]
    gr = d2.groupby(["market", "sector", "di"])[["rw", "cap_lag"]].sum(min_count=1)
    for (mk, s, i), row in gr.iterrows():
        w = float(row["cap_lag"]) if row["cap_lag"] == row["cap_lag"] else 0.0
        num = float(row["rw"]) if row["rw"] == row["rw"] else 0.0
        ret[mk][s][i] = round(num / w if w else 0.0, 3)
        retw[mk][s][i] = round(w, 1)
    code_meta = df.drop_duplicates("code").set_index("code").loc[codes, ["sector", "market"]]
    with open(os.path.join(OUT, "long.pkl"), "wb") as f:
        pickle.dump({"dates": dates, "codes": codes, "markets": markets, "sectors": sectors,
                     "inst": inst, "ret": ret, "retw": retw, "pres": pres, "capP": capP,
                     "code_sector": code_meta["sector"].to_numpy(),
                     "code_market": code_meta["market"].to_numpy(),
                     "sources": SOURCES}, f)
    print("done", N, C, markets, len(sectors), f"{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
