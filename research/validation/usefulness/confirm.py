"""Confirmation stage (asof >= 2023-01-01, forward window inside data) + descriptive tables.
Reads fixed directions from discovery.json (written before this script was run)."""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm, t as tdist

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir  # noqa: E402
from iclib import (CELLS, CONF_START, DISC_END, SECTOR_IND, nw, sector_frame, sector_ic,  # noqa: E402
                   stock_frame, stock_ic)

WORK = out_dir(__file__)
COST = 0.30
disc = json.load(open(os.path.join(WORK, "discovery.json")))
S = pd.read_csv(os.path.join(WORK, "sector.csv.gz"))
K = pd.read_csv(os.path.join(WORK, "stock.csv.gz"), dtype={"code": str})
m = disc["m_passed"]
period = lambda x: np.where(pd.Index(x) <= DISC_END, "disc", "conf")  # noqa: E731

# full-sample per-date IC (전체), then split
ic_all = {}
for ind, h in CELLS:
    ic_all[f"{ind}_h{h}"] = sector_ic(S, ind, h) if ind in SECTOR_IND else stock_ic(K, ind, h)
icdf = pd.DataFrame(ic_all)
icdf.to_csv(os.path.join(WORK, "ic_series_all.csv"))
# guard: discovery part reproduces discovery.json exactly
for c, r in disc["cells"].items():
    v = icdf.loc[icdf.index <= DISC_END, c].dropna()
    assert abs(v.mean() - r["mean"]) < 1e-12, c

conf = {}
for ind, h in CELLS:
    c = f"{ind}_h{h}"
    v = icdf.loc[icdf.index >= CONF_START, c].dropna()
    st = nw(v, h)
    d = disc["cells"][c]
    rec = {**st, "first_date": v.index[0], "last_date": v.index[-1], "discovery_pass": d["pass"]}
    if d["pass"]:
        z = d["fixed_sign"] * st["t"]
        rec.update({"fixed_sign": d["fixed_sign"], "t_dir": z, "p_one_sided_normal": float(norm.sf(z)),
                    "p_one_sided_t": float(tdist.sf(z, st["n"] - 1)), "bonf_alpha": 0.05 / m,
                    "confirmed": bool(norm.sf(z) < 0.05 / m)})
    conf[c] = rec
json.dump({"m": m, "cells": conf}, open(os.path.join(WORK, "confirmation.json"), "w"), ensure_ascii=False, indent=1)

# ---- descriptive: spreads (all 12 cells, both periods) ----
def sector_spread(ind, h):
    s = sector_frame(S, ind, h)
    out = {}
    for d, g in s.groupby("asof"):
        if len(g) < 5:
            continue
        g = g.sort_values(ind)
        out[d] = g[f"f{h}"].iloc[-3:].mean() - g[f"f{h}"].iloc[:3].mean()
    return pd.Series(out, dtype=float).sort_index()


def stock_spread(ind, h):
    k = stock_frame(K, ind, h)
    k["pct"] = k.groupby(["asof", "sector"])[ind].rank(pct=True, method="average")
    top = k[k["pct"] > 0.8].groupby("asof")["resid"].mean()
    bot = k[k["pct"] <= 0.2].groupby("asof")["resid"].mean()
    return (top - bot).dropna().sort_index()


sprows = []
for ind, h in CELLS:
    c = f"{ind}_h{h}"
    sp = sector_spread(ind, h) if ind in SECTOR_IND else stock_spread(ind, h)
    d = disc["cells"][c]
    sgn = d["fixed_sign"] if d["pass"] else int(np.sign(d["mean"]))
    for per, x in (("disc", sp[sp.index <= DISC_END]), ("conf", sp[sp.index >= CONF_START])):
        st = nw(x, h)
        sprows.append({"cell": c, "period": per, "passed": d["pass"], "dir_sign": sgn, "n": st["n"],
                       "top_minus_bot_pct": st["mean"], "nw_t": st["t"],
                       "dir_spread_pct": sgn * st["mean"], "dir_net_pct": sgn * st["mean"] - COST})
sp_df = pd.DataFrame(sprows)
sp_df.to_csv(os.path.join(WORK, "spread.csv"), index=False)

# ---- descriptive: per-market, per-year IC ----
mk = []
for ind, h in CELLS:
    for mkt in ("거래소", "코스닥"):
        ic = sector_ic(S, ind, h, scope=mkt) if ind in SECTOR_IND else stock_ic(K, ind, h, market=mkt)
        for per in ("disc", "conf"):
            v = ic[period(ic.index) == per].dropna()
            st = nw(v, h)
            mk.append({"cell": f"{ind}_h{h}", "market": mkt, "period": per, "mean": st["mean"], "t": st["t"], "n": st["n"]})
mk = pd.DataFrame(mk)
mk.to_csv(os.path.join(WORK, "ic_by_market.csv"), index=False)
yr = icdf.groupby(icdf.index.str[:4]).mean().T
yr.to_csv(os.path.join(WORK, "ic_by_year.csv"))

pd.set_option("display.width", 200)
print("m =", m)
print(pd.DataFrame(conf).T[["mean", "t", "n", "discovery_pass", "t_dir", "p_one_sided_normal", "p_one_sided_t", "confirmed"]])
print(sp_df.round(4).to_string())
print(mk.pivot_table(index="cell", columns=["market", "period"], values=["mean", "t"]).round(3).to_string())
print(yr.round(3).to_string())
