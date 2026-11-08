"""리뷰 재계산(quant-reviewer, Q3d 재검) — 위상과 무관한 합산 IC 와 HAC t.

결과 문서 Q3d 재검 "x20 h20 은 경계 신호다" 단락의 숫자: step 5 전부 합산 IC, NW lag 0·3·4·6·8·12,
Hansen-Hodrick(균등 커널) 3·4, 이동 블록 부트스트랩(블록 8 = 40거래일, 4000회, seed 1),
−ret20 만의 IC, x·−ret20 IC 시계열 상관, offset 별 −ret20, 연도별 x20 h20.
기술 통계이지 사전등록 판정이 아니다.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir  # noqa: E402

W = out_dir(__file__)
rows = pd.read_csv(W / "rows.csv.gz")
fwd = pd.read_csv(W / "fwd.csv")
R = rows[(rows.win == 20) & (rows.mkt == "전체") & (~rows.thin)].merge(
    fwd[["asof", "ti", "sector", "f5", "f20"]], on=["asof", "sector"])
t0 = fwd.ti.min()
R = R[(R.ti - t0) % 5 == 0].copy()
R["negret"] = -R["ret"]


def ics(col, h):
    out = []
    for d, g in R.dropna(subset=[f"f{h}", col]).groupby("asof"):
        if len(g) < 5:
            continue
        out.append((d, g.ti.iloc[0], np.corrcoef(rankdata(g[col]), rankdata(g[f"f{h}"]))[0, 1]))
    return pd.DataFrame(out, columns=["asof", "ti", "ic"])


def lrv(x, lag, kern):
    x = x - x.mean()
    n = len(x)
    v = x @ x / n
    for lg in range(1, lag + 1):
        w = 1 - lg / (lag + 1) if kern == "bart" else 1.0
        v += 2 * w * (x[lg:] @ x[:-lg]) / n
    return np.sqrt(max(v, 1e-12) / n)


rng = np.random.default_rng(1)
for col in ["x", "negret", "a", "inst"]:
    for h in (5, 20):
        p = ics(col, h)
        x = p.ic.to_numpy()
        m = x.mean()
        ac = [np.corrcoef(x[lg:], x[:-lg])[0, 1] for lg in range(1, 9)]
        res = {f"NW{L}": m / lrv(x, L, "bart") for L in (0, 3, 4, 6, 8, 12)}
        res["HH3"] = m / lrv(x, 3, "unif")
        res["HH4"] = m / lrv(x, 4, "unif")
        # moving block bootstrap, block 8 (=40 trading days)
        n = len(x)
        B = 8
        bs = []
        for _ in range(4000):
            st = rng.integers(0, n - B + 1, size=n // B + 1)
            idx = np.concatenate([np.arange(s, s + B) for s in st])[:n]
            bs.append(x[idx].mean())
        res["MBB_se_t"] = m / np.std(bs)
        print(col, h, "N", n, "mean", round(m, 4), {k: round(v, 2) for k, v in res.items()}, "acf", np.round(ac, 2))
# offsets for negret
for h in (20,):
    p = ics("negret", h)
    for off in range(4):
        q = p[(p.ti - t0) % 20 == off * 5]
        print("negret offset", off, round(q.ic.mean(), 3), round(q.ic.mean() / (q.ic.std() / np.sqrt(len(q))), 2))
# correlation of x and negret IC series
px = ics("x", 20)
pn = ics("negret", 20)
print("corr IC x vs negret h20", np.corrcoef(px.ic, pn.ic)[0, 1])
# yearly x20 h20 pooled
px["y"] = px["asof"].str[:4]
print(px.groupby("y").ic.agg(["mean", "count"]).round(3).T)
