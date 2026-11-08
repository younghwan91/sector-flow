"""수급 쓸모 — Per-asof indicators and forward returns (prereg b702dc4). Computes values only; no statistics.
Outputs: sector.csv.gz (asof x scope x sector), stock.csv.gz (asof x code)."""
import os
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir  # noqa: E402

WORK = out_dir(__file__)
L, STEP, HS = 260, 5, (5, 20)
SCOPES = ("전체", "거래소", "코스닥")

with open(os.path.join(WORK, "panels.pkl"), "rb") as f:
    D = pickle.load(f)
dates, codes = D["dates"], np.array(D["codes"])
N, C = len(dates), len(codes)
cs, cm = D["code_sector"], D["code_market"]
pres, capP, capLag, retP = D["pres"], D["capP"], D["capLagP"], D["retP"]
tpP, forP, instP, adjP = D["tpP"], D["forP"], D["instP"], D["adjP"]
cum = {k: np.concatenate([np.zeros((C, 1)), np.cumsum(P, axis=1)], axis=1)
       for k, P in (("tp", tpP), ("for", forP), ("inst", instP))}


def wsum(k, t, w):  # sum over [t-w+1, t]
    return cum[k][:, t + 1] - cum[k][:, t + 1 - w]


# sector daily own-basket return R[scope][sector][day] (prev-row cap weighted; no stock -> 0, flag any)
ok = ~np.isnan(capLag) & ~np.isnan(retP)
rw = np.where(ok, retP * np.nan_to_num(capLag), 0.0)
ww = np.where(ok, np.nan_to_num(capLag), 0.0)
sectors = sorted(set(cs))
R, ANY = {}, {}
for sc in SCOPES:
    R[sc], ANY[sc] = {}, {}
    for s in sectors:
        msk = (cs == s) & ((cm == sc) if sc != "전체" else True)
        if not msk.any():
            continue
        num, den = rw[msk].sum(0), ww[msk].sum(0)
        R[sc][s] = np.where(den > 0, num / np.where(den > 0, den, 1), 0.0)
        ANY[sc][s] = den > 0

ts = list(range(L - 1, N, STEP))
srec, krec = [], []
t0 = time.time()
for j, t in enumerate(ts):
    asof = dates[t]
    pres20 = pres[:, t - 19: t + 1].any(1)
    S1, S2, S3 = wsum("tp", t, 20), wsum("for", t, 20), wsum("inst", t, 60)
    capt = capP[:, t]
    for sc in SCOPES:
        for s in R[sc]:
            msk = (cs == s) & ((cm == sc) if sc != "전체" else True)
            n20 = int((msk & pres20).sum())
            cap = float(np.nansum(capt[msk]))
            rec = {"asof": asof, "ti": t, "scope": sc, "sector": s, "n20": n20, "cap": cap,
                   "S1": S1[msk].sum() / cap if cap > 0 else np.nan,
                   "S2": S2[msk].sum() / cap if cap > 0 else np.nan,
                   "S3": S3[msk].sum() / cap if cap > 0 else np.nan}
            for h in HS:
                if t + h <= N - 1:
                    r = R[sc][s][t + 1: t + h + 1]
                    rec[f"f{h}"] = (np.prod(1 + r / 100.0) - 1) * 100 if ANY[sc][s][t + 1: t + h + 1].any() else np.nan
                else:
                    rec[f"f{h}"] = np.nan
            srec.append(rec)
    # stock level
    have = ~np.isnan(capt) & (capt > 0)
    idx = np.where(have)[0]
    capv = capt[idx]
    pct = pd.Series(capv).rank(pct=True).to_numpy()
    K1, K2, K3 = wsum("tp", t, 20)[idx] / capv, wsum("inst", t, 20)[idx] / capv, wsum("for", t, 20)[idx] / capv
    rec = {"asof": np.repeat(asof, len(idx)), "ti": t, "code": codes[idx], "sector": cs[idx], "market": cm[idx],
           "cap": capv, "cap_pct": pct, "elig": pct > 0.2, "K1": K1, "K2": K2, "K3": K3}
    a0 = adjP[idx, t]
    for h in HS:
        rec[f"r{h}"] = ((adjP[idx, t + h] / a0 - 1) * 100) if t + h <= N - 1 else np.full(len(idx), np.nan)
    krec.append(pd.DataFrame(rec))
    if j % 100 == 0:
        print(j, len(ts), asof, f"{time.time()-t0:.0f}s", flush=True)
pd.DataFrame(srec).to_csv(os.path.join(WORK, "sector.csv.gz"), index=False)
pd.concat(krec, ignore_index=True).to_csv(os.path.join(WORK, "stock.csv.gz"), index=False)
print("done", len(ts), ts[0], dates[ts[0]], dates[ts[-1]], f"{time.time()-t0:.0f}s")
