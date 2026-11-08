"""Diagnostics + verifier samples."""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir  # noqa: E402
from iclib import stock_frame  # noqa: E402

WORK = out_dir(__file__)
S = pd.read_csv(os.path.join(WORK, "sector.csv.gz"))
K = pd.read_csv(os.path.join(WORK, "stock.csv.gz"), dtype={"code": str})
c = json.load(open(os.path.join(WORK, "confirmation.json")))
for k, v in c["cells"].items():
    if v.get("discovery_pass"):
        print(k, "t_dir", round(v["t_dir"], 3), "p_norm", f"{v['p_one_sided_normal']:.3g}", "p_t", f"{v['p_one_sided_t']:.3g}")
k = stock_frame(K, "K1", 20)
g = k.groupby("asof").agg(n=("code", "size"), ns=("sector", "nunique"))
print("stock K1 h20 per date: stocks", g["n"].describe()[["min", "50%", "max"]].to_dict(), "sectors", g["ns"].describe()[["min", "50%", "max"]].to_dict())
print("share K1==0 among used", float((k["K1"] == 0).mean()), "K2==0", float((stock_frame(K, "K2", 20)["K2"] == 0).mean()))
s = S[(S.scope == "전체") & (S.n20 >= 10)]
print("sectors per date", s.groupby("asof").size().describe()[["min", "50%", "max"]].to_dict(), "(미분류) used", int((s.sector == "(미분류)").sum()))
rng = np.random.default_rng(20261108)
ss = s.dropna(subset=["f20"]).iloc[rng.choice(len(s.dropna(subset=["f20"])), 10, replace=False)].copy()
for i in ("S1", "S2"):
    ss[f"{i}_num_eok"] = ss[i] * ss["cap"]
ss["S3_num_eok"] = ss["S3"] * ss["cap"]
ss = ss[["asof", "sector", "cap", "S1", "S1_num_eok", "S2", "S2_num_eok", "S3", "S3_num_eok", "f5", "f20"]].sort_values("asof")
ss.to_csv(os.path.join(WORK, "sample_S.csv"), index=False)
kk = K[K.elig].dropna(subset=["r20"])
kk = kk.iloc[rng.choice(len(kk), 10, replace=False)].copy()
for i in ("K1", "K2", "K3"):
    kk[f"{i}_num_eok"] = kk[i] * kk["cap"]
kk = kk[["asof", "code", "sector", "market", "cap", "cap_pct", "K1", "K1_num_eok", "K2", "K2_num_eok", "K3", "K3_num_eok", "r5", "r20"]].sort_values("asof")
kk.to_csv(os.path.join(WORK, "sample_K.csv"), index=False)
pd.set_option("display.width", 250)
print(ss.to_string(index=False))
print(kk.to_string(index=False))
