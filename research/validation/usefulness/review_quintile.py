"""리뷰 재계산(quant-reviewer, 수급 쓸모) 4/4 — 섹터 대비 초과수익의 K1 5분위 표(결과 문서 "확인의 뜻" 1),
순매수 상위(5분위) 회피 효과(문서 4 "20일당 −0.06%"). (review_features.py 먼저)"""
import numpy as np
import pandas as pd

from review_ic import frame, nw

for y in ("r20", "s20", "r5", "s5"):
    h = int(y[1:])
    k = frame(y, h)
    k["pct"] = k.groupby(["asof", "sector"])["K1"].rank(pct=True)
    k["qb"] = pd.cut(k.pct, [0, .2, .4, .6, .8, 1.0], labels=False)
    k["per"] = np.where(k["asof"] <= "2022-12-31", "disc", "conf")
    t = k.groupby(["per", "asof", "qb"])["resid"].mean().groupby(["per", "qb"]).mean().unstack()
    print(y, "\n", t.round(3))
    top = k[k.qb == 4].groupby("asof")["resid"].mean()
    rest = k[k.qb < 4].groupby("asof")["resid"].mean()
    for per in ("disc", "conf"):
        sel = (top.index <= "2022-12-31") if per == "disc" else (top.index >= "2023-01-01")
        m, tt, n = nw((rest - top)[sel], h)
        print(f"  {y} {per} rest-minus-top {m:+.3f}% t {tt:+.2f}; "
              f"universe-minus-top*? excl-top vs all = {((rest - top)[sel].mean() * 0.2):+.3f}%")
