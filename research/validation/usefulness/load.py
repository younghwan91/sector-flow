"""수급 쓸모 — 종목 × 거래일 패널 적재 (사전등록 b702dc4). 정본(고친 정의).
Definitions = ../q3cd/load_long.py (PR #5 corrected):
- daily ret[%] = daily_bars_adjusted close / previous adjusted row of the code - 1
- cap[억] = raw close (abs supply_demand.close, fallback daily_bars_raw) x shares as-of, re-counted by
  corporate_actions (upd_stkpc_tp & (8|16|64)) by 1/(1+upd_rt/100) after the snapshot date
- cap_lag = cap at the previous supply_demand row of the code
- net buy[억] = quantity x abs(close) / 1e8
Extra: adjP = daily_bars_adjusted close on trading dates (for stock forward returns).
"""
import os
import pickle
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import connect_ro, out_dir  # noqa: E402

warnings.filterwarnings("ignore", category=UserWarning)
OUT = out_dir(__file__)
EOK = 1e8
SOURCES = ("kiwoom", "kiwoom_backfill_2026-10-11", "kiwoom_backfill_2026-10-12")
START = "2017-01-02"


def main():
    t0 = time.time()
    con = connect_ro()
    dates = [str(d) for d, in pd.read_sql_query(
        "SELECT DISTINCT date FROM supply_demand WHERE date >= %s AND source IN %s ORDER BY date", con,
        params=(START, SOURCES)).itertuples(index=False)]
    years = sorted({d[:4] for d in dates})
    parts = []
    for y in years:
        parts.append(pd.read_sql_query(
            "SELECT code, date, close, institution, foreign_, invtrt, penfnd_etc, source FROM supply_demand "
            "WHERE date >= %s AND date <= %s AND source IN %s",
            con, params=(f"{y}-01-01", f"{y}-12-31", SOURCES)))
        print(y, len(parts[-1]), f"{time.time()-t0:.0f}s", flush=True)
    sd = pd.concat(parts, ignore_index=True)
    sd["date"] = sd["date"].astype(str)
    dup = sd.duplicated(["code", "date"]).sum()
    print("dup (code,date) across sources:", int(dup))
    assert dup == 0
    meta = pd.read_sql_query("SELECT code, name, sector, market FROM stocks WHERE market IN ('거래소','코스닥')", con)
    df = sd.merge(meta, on="code", how="inner")
    df["close"] = df["close"].astype(float).abs()
    df["sector"] = df["sector"].fillna("").replace("", "(미분류)")
    for col, src in (("f_tp", None), ("f_for", "foreign_"), ("f_inst", "institution")):
        if src:
            df[col] = df[src].astype(float) * df["close"] / EOK
    df["f_tp"] = (df["invtrt"].astype(float) + df["penfnd_etc"].astype(float)) * df["close"] / EOK
    codes = sorted(df["code"].unique())
    adj = []
    for y in years:
        a = pd.read_sql_query(
            "SELECT code, date, close FROM daily_bars_adjusted WHERE date >= %s AND date <= %s",
            con, params=(f"{int(y)-1}-12-01" if y == years[0] else f"{y}-01-01", f"{y}-12-31"))
        adj.append(a)
    adj = pd.concat(adj, ignore_index=True)
    adj["date"] = adj["date"].astype(str)
    adj = adj[adj["code"].isin(codes) & adj["close"].notna() & (adj["close"] > 0)]
    adj = adj.drop_duplicates(["code", "date"]).sort_values(["code", "date"])
    adj["ret"] = adj.groupby("code")["close"].pct_change() * 100.0
    df = df.merge(adj[["code", "date", "ret"]], on=["code", "date"], how="left")
    print("adj", len(adj), "missing ret share", float(df["ret"].isna().mean()), f"{time.time()-t0:.0f}s", flush=True)

    raw = []
    for y in years:
        raw.append(pd.read_sql_query(
            "SELECT code, date, close FROM daily_bars_raw WHERE date >= %s AND date <= %s",
            con, params=(f"{y}-01-01", f"{y}-12-31")))
    raw = pd.concat(raw, ignore_index=True)
    raw["date"] = raw["date"].astype(str)
    raw["close"] = raw["close"].astype(float).abs()
    raw = raw.drop_duplicates(["code", "date"]).set_index(["code", "date"])["close"]
    bars = df[["code", "date", "close"]].rename(columns={"close": "sdc"}).copy()
    key = pd.MultiIndex.from_arrays([bars["code"], bars["date"]])
    rpx = raw.reindex(key).to_numpy()
    px = bars["sdc"].to_numpy()
    bars["close"] = np.where(np.isnan(px) | (px <= 0), rpx, px)
    bars = bars[["code", "date", "close"]]
    shares = pd.read_sql_query("SELECT code, date, shares_outstanding FROM shares_outstanding_history", con)
    events = pd.read_sql_query("SELECT code, date, upd_stkpc_tp, upd_rt FROM corporate_actions", con)
    con.close()
    shares = shares[shares["code"].isin(codes)].copy()
    shares["s_date"] = pd.to_datetime(shares["date"].astype(str))
    shares = shares[["code", "s_date", "shares_outstanding"]].sort_values(["s_date", "code"])
    bars["dt"] = pd.to_datetime(bars["date"])
    m = pd.merge_asof(bars.sort_values(["dt", "code"]), shares, left_on="dt", right_on="s_date",
                      by="code", direction="backward")
    ev = events.copy()
    ev["tp"] = pd.to_numeric(ev["upd_stkpc_tp"], errors="coerce").fillna(0).astype(int)
    ev["upd_rt"] = pd.to_numeric(ev["upd_rt"], errors="coerce")
    ev = ev[((ev["tp"] & (8 | 16 | 64)) != 0) & ev["upd_rt"].notna() & (ev["upd_rt"] != 0) & (ev["upd_rt"] > -100)]
    ev["e_date"] = pd.to_datetime(ev["date"].astype(str))
    ev = ev.sort_values(["code", "e_date"])
    ev["C"] = ev.groupby("code")["upd_rt"].transform(lambda r: (-np.log1p(r / 100.0)).cumsum())
    ev = ev[["code", "e_date", "C"]].sort_values(["e_date", "code"])
    m["_row"] = np.arange(len(m))

    def c_at(col):
        x = m[["_row", "code", col]].dropna(subset=[col]).sort_values([col, "code"])
        x = pd.merge_asof(x, ev, left_on=col, right_on="e_date", by="code", direction="backward")
        return pd.Series(x["C"].fillna(0.0).to_numpy(), index=x["_row"].to_numpy()).reindex(m["_row"]).to_numpy()

    m["factor"] = np.exp(c_at("dt") - c_at("s_date"))
    m["shares_outstanding"] = m["shares_outstanding"].astype(float) * m["factor"]
    m.loc[m["shares_outstanding"] <= 0, "shares_outstanding"] = np.nan
    m["cap"] = m["close"].astype(float) * m["shares_outstanding"].astype(float) / EOK
    print("cap panel", len(m), f"{time.time()-t0:.0f}s", flush=True)

    di = {d: i for i, d in enumerate(dates)}
    ci = {c: i for i, c in enumerate(codes)}
    N, C = len(dates), len(codes)

    def panel(frame, col):
        P = np.full((C, N), np.nan)
        P[frame["code"].map(ci).to_numpy(), frame["date"].map(di).to_numpy()] = frame[col].to_numpy(dtype=float)
        return P

    capP = panel(m, "cap")
    df["cap"] = capP[df["code"].map(ci).to_numpy(), df["date"].map(di).to_numpy()]
    df = df.sort_values(["code", "date"])
    df["cap_lag"] = df.groupby("code")["cap"].shift(1)
    pres = ~np.isnan(panel(df.assign(one=1.0), "one"))
    adjT = adj[adj["date"].isin(di)]
    out = {"dates": dates, "codes": codes, "pres": pres, "capP": capP,
           "capLagP": panel(df, "cap_lag"), "retP": panel(df, "ret"),
           "tpP": panel(df, "f_tp"), "forP": panel(df, "f_for"), "instP": panel(df, "f_inst"),
           "adjP": panel(adjT, "close")}
    cm = df.drop_duplicates("code").set_index("code").loc[codes, ["sector", "market"]]
    out["code_sector"] = cm["sector"].to_numpy()
    out["code_market"] = cm["market"].to_numpy()
    for k in ("tpP", "forP", "instP"):
        out[k] = np.where(pres, out[k], 0.0)  # absent row -> no flow (report groupby-sum semantics)
    with open(os.path.join(OUT, "panels.pkl"), "wb") as f:
        pickle.dump(out, f, protocol=4)
    print("done N", N, "C", C, "last date", dates[-1], f"{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
