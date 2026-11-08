"""Q3c·Q3d 긴 적재 — 고친 정의(PR #5, e8d8d12) — 정본.

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

OUT = out_dir(__file__)
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
    codes = sorted(df["code"].unique())
    # ret[%] = daily_bars_adjusted close / previous adjusted row of the code - 1 (report: adjusted_returns)
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
    print("adj ret", len(adj), "missing ret share", float(df["ret"].isna().mean()), f"{time.time()-t0:.0f}s", flush=True)

    # market cap panel = RAW close (supply_demand close, else daily_bars_raw) x shares as-of,
    # re-counted for split/merge/reduction (8|16|64) after the snapshot (storage.market_cap_asof_bulk)
    raw = []
    for y in years:
        raw.append(pd.read_sql_query(
            "SELECT code, date, close FROM daily_bars_raw WHERE date >= %s AND date <= %s",
            con, params=(f"{y}-01-01", f"{y}-12-31")))
    raw = pd.concat(raw, ignore_index=True)
    raw["date"] = raw["date"].astype(str)
    raw["close"] = raw["close"].astype(float).abs()
    raw = raw.drop_duplicates(["code", "date"]).set_index(["code", "date"])["close"]
    sdpx = df[["code", "date", "close"]].copy()
    sdpx["close"] = sdpx["close"].astype(float)
    sdpx = sdpx.sort_values("close", ascending=False).drop_duplicates(["code", "date"])
    bars = sdpx.rename(columns={"close": "sdc"})
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
    d2 = df.dropna(subset=["cap_lag", "ret"]).copy()
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
                     "sources": SOURCES, "defs": "adjusted-ret, raw-cap+events (PR #5)"}, f)
    print("done", N, C, markets, len(sectors), f"{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
