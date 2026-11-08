"""Discovery stage (asof <= 2022-12-31 only). Writes discovery.json with verdicts and fixed signs."""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir  # noqa: E402
from iclib import CELLS, DISC_END, SECTOR_IND, nw, sector_ic, stock_ic  # noqa: E402

WORK = out_dir(__file__)
S = pd.read_csv(os.path.join(WORK, "sector.csv.gz"))
K = pd.read_csv(os.path.join(WORK, "stock.csv.gz"), dtype={"code": str})
S = S[S["asof"] <= DISC_END]  # blind: confirmation rows never enter this script
K = K[K["asof"] <= DISC_END]
res, series = {}, {}
for ind, h in CELLS:
    ic = sector_ic(S, ind, h) if ind in SECTOR_IND else stock_ic(K, ind, h)
    v = ic.dropna()
    half = len(v) // 2
    a, b = v.iloc[:half], v.iloc[half:]
    st = nw(v, h)
    sign = int(np.sign(st["mean"]))
    passed = bool(abs(st["t"]) >= 2.0 and np.sign(a.mean()) == np.sign(b.mean()) == sign)
    res[f"{ind}_h{h}"] = {**st, "n_dates_total": int(len(ic)), "first_date": v.index[0], "last_date": v.index[-1],
                          "half1_mean": float(a.mean()), "half1_n": len(a), "half1_end": a.index[-1],
                          "half2_mean": float(b.mean()), "half2_n": len(b),
                          "pass": passed, "fixed_sign": sign if passed else None}
    series[f"{ind}_h{h}"] = ic
    print(f"{ind} h{h}: IC {st['mean']:+.4f} t {st['t']:+.2f} n {st['n']} halves {a.mean():+.4f}/{b.mean():+.4f} pass {passed}")
m = sum(r["pass"] for r in res.values())
out = {"prereg": "b702dc4", "period": f"asof <= {DISC_END}", "m_passed": m, "cells": res}
with open(os.path.join(WORK, "discovery.json"), "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
pd.DataFrame(series).to_csv(os.path.join(WORK, "discovery_ic_series.csv"))
print("m =", m, "written discovery.json")
