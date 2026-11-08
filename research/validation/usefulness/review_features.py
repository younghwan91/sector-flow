"""리뷰 재계산(quant-reviewer, 수급 쓸모) 1/4 — K1 행에 통제 변수를 붙여 out/review_K.pkl 로.

p20·p5 = 과거 20·5일 수익률(조정 종가), nret20 = −p20, s5·s20 = t+1 종가 진입 앞날 수익률,
d1 = t→t+1 수익률, lcap = log 시총, pres_t = 그날 supply_demand 행 유무. 대상: elig(시총 하위 20% 제외) 행.
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir  # noqa: E402

W = out_dir(__file__)
with open(W / "panels.pkl", "rb") as f:
    D = pickle.load(f)
codes = np.array(D["codes"])
ci = {c: i for i, c in enumerate(codes)}
adjP = D["adjP"]
pres = D["pres"]
N = adjP.shape[1]
K = pd.read_csv(W / "stock.csv.gz", dtype={"code": str})
K = K[K["elig"]].copy()
ii = K["code"].map(ci).to_numpy()
t = K["ti"].to_numpy()
a0 = adjP[ii, t]


def at(off):
    tt = t + off
    ok = (tt >= 0) & (tt < N)
    out = np.full(len(t), np.nan)
    out[ok] = adjP[ii[ok], tt[ok]]
    return out


K["p20"] = (a0 / at(-20) - 1) * 100
K["p5"] = (a0 / at(-5) - 1) * 100
K["nret20"] = -K["p20"]
for h in (5, 20):
    K[f"s{h}"] = (at(1 + h) / at(1) - 1) * 100   # entry close t+1
K["d1"] = (at(1) / a0 - 1) * 100
K["lcap"] = np.log(K["cap"])
K["pres_t"] = pres[ii, t]
K.to_pickle(W / "review_K.pkl")
print(len(K))
