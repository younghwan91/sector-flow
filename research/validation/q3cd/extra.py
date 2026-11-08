"""Descriptive sensitivities (NOT prereg verdicts):
(1) all step-5 asofs pooled for h=20 (overlapping) with Newey-West t (lag 3);
(2) ungated G (rank-mean of a, x, xddot over all non-thin sectors) — the report's G is gated;
(3) era split 2018-2023 (backfill universe) vs 2024+ (report's own kiwoom source)."""
import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--variant", choices=("fixed", "v1"), default="fixed")
WORK = out_dir(__file__, *(("v1",) if ap.parse_args().variant == "v1" else ()))
OUT = WORK
rows = pd.read_csv(WORK / "rows.csv.gz")
fwd = pd.read_csv(WORK / "fwd.csv")
R = rows[(rows["win"] == 20) & (rows["mkt"] == "전체") & (~rows["thin"])].merge(
    fwd[["asof", "ti", "sector", "f5", "f20"]], on=["asof", "sector"], how="left")
t0 = fwd["ti"].min()
R = R[(R["ti"] - t0) % 5 == 0]


def gu(g):
    g = g.dropna(subset=["a", "x", "xddot"]).copy()
    n = len(g)
    if n < 2:
        g["Gu"] = np.nan
        return g
    g["Gu"] = sum((rankdata(g[c]) - 1) / (n - 1) for c in ("a", "x", "xddot")) / 3
    return g


R = pd.concat([gu(g) for _, g in R.groupby("asof")])


def nw_se(x, lag):
    x = np.asarray(x) - np.mean(x)
    n = len(x)
    v = x @ x / n
    for lg in range(1, lag + 1):
        v += 2 * (1 - lg / (lag + 1)) * (x[lg:] @ x[:-lg]) / n
    return np.sqrt(v / n)


out = []
for h, step in ((5, 5), (20, 5), (20, 20)):
    for name, col in (("accel20", "a"), ("x20", "x"), ("impulse20", "inst"), ("G20_ungated", "Gu")):
        per = []
        for d, g in R.dropna(subset=[f"f{h}", col]).groupby("asof"):
            if len(g) < 5:
                continue
            per.append((d, g["ti"].iloc[0], np.corrcoef(rankdata(g[col]), rankdata(g[f"f{h}"]))[0, 1]))
        per = pd.DataFrame(per, columns=["asof", "ti", "ic"])
        if step == 20:
            per = per[(per["ti"] - t0) % 20 == 0]
        ics = per["ic"].to_numpy()
        lag = max(0, h // 5 - 1) if step == 5 else 0
        se = nw_se(ics, lag)
        pre = per[per["asof"] < "2024-01-01"]["ic"].mean()
        post = per[per["asof"] >= "2024-01-01"]["ic"].mean()
        out.append({"indicator": name, "h": h, "step": step, "N": len(ics), "mean_ic": ics.mean(),
                    "t_nw": ics.mean() / se, "nw_lag": lag, "ic_2018_2023": pre, "ic_2024plus": post,
                    "N_2024plus": int((per["asof"] >= "2024-01-01").sum())})
out = pd.DataFrame(out)
out.to_csv(os.path.join(OUT, "q3d_sensitivity_descriptive.csv"), index=False)
print(out.round(3).to_string(index=False))
