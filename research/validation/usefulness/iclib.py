"""수급 쓸모 — per-date IC and Newey-West statistics (prereg b702dc4). discovery·confirm·samples 가 같이 쓴다."""
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

SECTOR_IND = ("S1", "S2", "S3")
STOCK_IND = ("K1", "K2", "K3")
HS = (5, 20)
CELLS = [(i, h) for i in SECTOR_IND + STOCK_IND for h in HS]
DISC_END = "2022-12-31"
CONF_START = "2023-01-01"


def sp(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3 or np.all(x == x[0]) or np.all(y == y[0]):
        return np.nan
    return spearmanr(x, y).statistic


def sector_frame(S, ind, h, scope="전체"):
    s = S[(S["scope"] == scope) & (S["n20"] >= 10) & (S["cap"] > 0)]
    return s.dropna(subset=[ind, f"f{h}"])


def sector_ic(S, ind, h, scope="전체"):
    s = sector_frame(S, ind, h, scope)
    out = {}
    for d, g in s.groupby("asof"):
        out[d] = sp(g[ind], g[f"f{h}"]) if len(g) >= 5 else np.nan
    return pd.Series(out, dtype=float).sort_index()


def stock_frame(K, ind, h, market=None):
    k = K[K["elig"]].dropna(subset=[ind, f"r{h}"])
    if market is not None:
        k = k[k["market"] == market]
    k = k.copy()
    k["n_grp"] = k.groupby(["asof", "sector"])["code"].transform("size")
    k = k[k["n_grp"] >= 10].copy()
    k["resid"] = k[f"r{h}"] - k.groupby(["asof", "sector"])[f"r{h}"].transform("mean")
    return k


def stock_ic(K, ind, h, market=None):
    k = stock_frame(K, ind, h, market)
    out = {}
    for d, gd in k.groupby("asof"):
        ics, ns = [], []
        for _, g in gd.groupby("sector"):
            v = sp(g[ind], g["resid"])
            if v == v:
                ics.append(v)
                ns.append(len(g))
        out[d] = float(np.average(ics, weights=ns)) if ics else np.nan
    return pd.Series(out, dtype=float).sort_index()


def nw(x, h):
    x = np.asarray(pd.Series(x).dropna(), float)
    T = len(x)
    lag = max(0, h // 5 - 1)
    m = x.mean()
    e = x - m
    lrv = e @ e / T
    for lg in range(1, lag + 1):
        lrv += 2 * (1 - lg / (lag + 1)) * (e[lg:] @ e[:-lg]) / T
    se = np.sqrt(lrv / T)
    return {"mean": float(m), "t": float(m / se), "n": T, "lag": lag, "se": float(se)}
