"""Q3c(추정 안정성)·Q3d(앞날 순위 IC) — slice_build.py 산출물(rows.csv.gz·blocks.csv·fwd.csv)에서.

  python analyze.py [--variant fixed|v1] [--mkt 전체|거래소|코스닥]

Q3d 는 사전등록 그대로: 20일 블록, 얇지 않은 섹터, 날짜당 5섹터 이상, offset 0 이 1차, 널은 날짜 안
섹터 라벨 순열 1000회(seed 20261108), 전·후반 같은 부호. --mkt 는 보조(시장별) — 결과는 out/<시장>/.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--variant", choices=("fixed", "v1"), default="fixed")
ap.add_argument("--mkt", choices=("전체", "거래소", "코스닥"), default="전체")
args = ap.parse_args()
WORK = out_dir(__file__, *(("v1",) if args.variant == "v1" else ()))
OUT = WORK
rows = pd.read_csv(WORK / "rows.csv.gz")
blocks = pd.read_csv(WORK / "blocks.csv")
fwd = pd.read_csv(WORK / "fwd.csv")
STEP = 5
res = {"q3c": {}, "q3d": {}}


def sp(a, b):
    if len(a) < 3 or np.nanstd(a) == 0 or np.nanstd(b) == 0:
        return np.nan
    return spearmanr(a, b).statistic


# ---------------- Q3c ----------------
asofs = sorted(blocks["asof"].unique())
aidx = {d: i for i, d in enumerate(asofs)}  # grid index (step 5 trading days), last is off-grid
_ti = fwd.drop_duplicates("asof").set_index("asof")["ti"]
grid = [d for d in asofs if (_ti[d] - _ti.min()) % STEP == 0]
kt = []
for (w, m), g in blocks[blocks["asof"].isin(grid)].groupby(["win", "mkt"]):
    t = g["t"].to_numpy()
    sub = g.iloc[::max(1, w // STEP)]  # non-overlapping windows
    kt.append({"win": w, "mkt": m, "n_dates": len(t), "t_p5": np.percentile(t, 5), "t_p25": np.percentile(t, 25),
               "t_med": np.median(t), "t_p75": np.percentile(t, 75), "t_p95": np.percentile(t, 95),
               "share_abs_t_lt2": np.mean(np.abs(t) < 2), "share_t_neg": np.mean(t < 0),
               "k_med": g["k"].median(), "k_p5": g["k"].quantile(.05), "k_p95": g["k"].quantile(.95),
               "nonovl_n": len(sub), "nonovl_share_abs_t_lt2": np.mean(np.abs(sub["t"]) < 2)})
kt = pd.DataFrame(kt)
kt.to_csv(os.path.join(OUT, "q3c_k_t.csv"), index=False)

xr, red = [], []
for (d, w, m), g in rows[rows["asof"].isin(grid)].groupby(["asof", "win", "mkt"]):
    gx = g.dropna(subset=["x", "ret"])
    gn = gx[~gx["thin"]]
    xr.append({"asof": d, "win": w, "mkt": m,
               "rho_x_negret_all": sp(gx["x"], -gx["ret"]),
               "rho_x_negret_nonthin": sp(gn["x"], -gn["ret"]),
               "pearson_x_negret_all": np.corrcoef(gx["x"], -gx["ret"])[0, 1] if len(gx) > 2 else np.nan})
    if w == 5:
        continue
    gu = g[~g["thin"]].dropna(subset=["a", "x", "xddot"])  # ungated non-thin
    gg = g.dropna(subset=["G"])  # gated set (G defined)
    rec = {"asof": d, "win": w, "mkt": m, "n_ungated": len(gu), "n_gated": len(gg)}
    for lab, s in (("u", gu), ("g", gg)):
        ok = len(s) >= 5
        rec[f"{lab}_a_x"] = sp(s["a"], s["x"]) if ok else np.nan
        rec[f"{lab}_a_xdd"] = sp(s["a"], s["xddot"]) if ok else np.nan
        rec[f"{lab}_x_xdd"] = sp(s["x"], s["xddot"]) if ok else np.nan
    if len(gg) >= 5:
        rec["g_G_a"] = sp(gg["G"], gg["a"])
        rec["g_G_x"] = sp(gg["G"], gg["x"])
        rec["g_G_xdd"] = sp(gg["G"], gg["xddot"])
    red.append(rec)
xr = pd.DataFrame(xr)
red = pd.DataFrame(red)
xr.to_csv(os.path.join(OUT, "q3c_x_vs_negret_perdate.csv"), index=False)
red.to_csv(os.path.join(OUT, "q3c_G_redundancy_perdate.csv"), index=False)
xs = xr.groupby(["win", "mkt"]).agg(
    rho_all_med=("rho_x_negret_all", "median"), rho_all_p5=("rho_x_negret_all", lambda s: s.quantile(.05)),
    rho_all_min=("rho_x_negret_all", "min"), rho_nonthin_med=("rho_x_negret_nonthin", "median"),
    pearson_all_med=("pearson_x_negret_all", "median")).reset_index()
xs.to_csv(os.path.join(OUT, "q3c_x_vs_negret_summary.csv"), index=False)
rs = red.groupby(["win", "mkt"]).agg(
    n_dates=("asof", "size"), n_ungated_med=("n_ungated", "median"), n_gated_med=("n_gated", "median"),
    gated_ge5=("n_gated", lambda s: int((s >= 5).sum())),
    u_a_x=("u_a_x", "median"), u_a_xdd=("u_a_xdd", "median"), u_x_xdd=("u_x_xdd", "median"),
    g_a_x=("g_a_x", "median"), g_a_xdd=("g_a_xdd", "median"), g_x_xdd=("g_x_xdd", "median"),
    g_G_a=("g_G_a", "median"), g_G_x=("g_G_x", "median"), g_G_xdd=("g_G_xdd", "median")).reset_index()
rs.to_csv(os.path.join(OUT, "q3c_G_redundancy_summary.csv"), index=False)

# ---------------- Q3d ----------------
MKT = args.mkt
_f = fwd[["asof", "ti", "sector"]].copy()
for _h in (5, 20):
    _f[f"f{_h}"] = fwd[f"f{_h}"] if MKT == "전체" else fwd[f"f{_h}_{MKT}"]
R = rows[(rows["win"] == 20) & (rows["mkt"] == MKT) & (~rows["thin"])].merge(_f, on=["asof", "sector"], how="left")
OUT = os.path.join(OUT, "" if MKT == "전체" else MKT)
os.makedirs(OUT, exist_ok=True)
IND = {"accel20": "a", "x20": "x", "G20": "G", "impulse20": "inst"}
rng = np.random.default_rng(20261108)
NREP, MIN_N = 1000, 5
summ, perdate_all, nulls = [], [], {}
grid_t = sorted(fwd.drop_duplicates("asof")["ti"])
t0 = grid_t[0]


def ic_stats(ics):
    ics = np.asarray(ics)
    n = len(ics)
    m = ics.mean()
    sd = ics.std(ddof=1)
    return n, m, sd, m / (sd / np.sqrt(n))


for h in (5, 20):
    for offset in range(h // STEP):
        sel = [t for t in grid_t if (t - t0) % h == offset * STEP]
        Rh = R[R["ti"].isin(sel)].dropna(subset=[f"f{h}"])
        for name, col in IND.items():
            per = []
            groups = []
            for d, g in Rh.groupby("asof"):
                g = g.dropna(subset=[col])
                if len(g) < MIN_N:
                    continue
                x = rankdata(g[col].to_numpy())
                y = rankdata(g[f"f{h}"].to_numpy())
                if x.std() == 0 or y.std() == 0:
                    continue
                per.append({"asof": d, "n": len(g), "ic": np.corrcoef(x, y)[0, 1]})
                groups.append((x, y))
            per = pd.DataFrame(per).sort_values("asof")
            n, m, sd, t = ic_stats(per["ic"])
            # null: permute sector labels (forward returns) within each date
            null = np.empty(NREP)
            for k in range(NREP):
                s = 0.0
                for x, y in groups:
                    s += np.corrcoef(x, rng.permutation(y))[0, 1]
                null[k] = s / len(groups)
            p = (1 + np.sum(np.abs(null) >= abs(m))) / (1 + NREP)
            half = len(per) // 2
            h1, h2 = per["ic"].iloc[:half].mean(), per["ic"].iloc[half:].mean()
            mde = (1.96 + 0.84) * sd / np.sqrt(n)
            same = np.sign(h1) == np.sign(h2) == np.sign(m)
            rec = {"indicator": name, "h": h, "offset": offset, "primary": offset == 0, "N_dates": n,
                   "first": per["asof"].iloc[0], "last": per["asof"].iloc[-1], "n_sect_med": per["n"].median(),
                   "mean_ic": m, "sd_ic": sd, "t": t, "null_p_2s": p, "null_sd": null.std(),
                   "half1": h1, "half2": h2, "halves_same_sign": bool(same), "mde80": mde,
                   "bonf_pass": bool(p < 0.05 / 8),
                   "verdict": "관계 있음" if (p < 0.05 and same) else "증거 없음"}
            summ.append(rec)
            per = per.assign(indicator=name, h=h, offset=offset)
            perdate_all.append(per)
            nulls[f"{name}|h{h}|off{offset}"] = null.round(5).tolist()
summ = pd.DataFrame(summ)
summ.to_csv(os.path.join(OUT, "q3d_ic_summary.csv"), index=False)
pd.concat(perdate_all).to_csv(os.path.join(OUT, "q3d_ic_perdate.csv"), index=False)
with open(os.path.join(OUT, "q3d_null_dists.json"), "w") as f:
    json.dump(nulls, f)

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
print(kt.round(2).to_string(index=False))
print(xs.round(3).to_string(index=False))
print(rs.round(2).to_string(index=False))
print(summ.round(3).to_string(index=False))
