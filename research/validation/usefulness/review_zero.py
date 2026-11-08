"""리뷰 재계산(quant-reviewer, 수급 쓸모) 3/4 — K1=0 동순위 몫, 섹터 안 K1·과거 20일 수익률 순위상관(+0.23),
K1·log 시총 순위상관, 확인 구간 K1 5분위별 p20·d1·r20·s20·시총. (review_features.py 먼저)"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir  # noqa: E402

K = pd.read_pickle(out_dir(__file__) / "review_K.pkl")
K["per"] = np.where(K["asof"] <= "2022-12-31", "disc", "conf")
K["z"] = K.K1 == 0
print(K.groupby("per")["z"].mean())
print("zero K1 & K2==0", (K.z & (K.K2 == 0)).mean(), " pres_t false share", (~K.pres_t).mean())
print(K.groupby(["per", "z"])["cap"].median())
print("median cap pct zero vs nonzero", K.groupby("z")["cap_pct"].median())


def g(df):
    if len(df) < 10:
        return np.nan
    return df["K1"].rank().corr(df["p20"].rank())


c = K.dropna(subset=["p20"]).groupby(["asof", "sector"]).apply(g)
print("mean within-sector rankcorr(K1,p20)", c.mean())
c2 = K.dropna(subset=["p20"]).groupby(["asof", "sector"]).apply(
    lambda d: d["K1"].rank().corr(np.log(d["cap"]).rank()) if len(d) >= 10 else np.nan)
print("mean within-sector rankcorr(K1,lcap)", c2.mean())
K["q"] = K.groupby(["asof", "sector"])["K1"].rank(pct=True)
K["qb"] = pd.cut(K.q, [0, .2, .4, .6, .8, 1.0], labels=False)
print(K[K.per == "conf"].groupby("qb")[["p20", "d1", "r20", "s20", "cap"]].median())
print(K[K.per == "conf"].groupby("qb")[["p20", "d1", "r20", "s20"]].mean())
