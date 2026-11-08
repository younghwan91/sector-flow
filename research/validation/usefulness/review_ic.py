"""리뷰 재계산(quant-reviewer, 수급 쓸모) 2/4 — K1 IC 의 통제·부분집합·진입 시점 민감도.

  python review_ic.py base|nz|skip|neg     (review_features.py 먼저)

base: 과거 20일 수익률·log 시총 통제(순위 잔차) — 결과 문서 "확인의 뜻" 2.
nz  : K1≠0, K1>0 만 — 문서 1 "K1>0 종목만 보면 h20 −0.004".
skip: t+1 종가 진입 — 문서 2 "h5 −0.021, h20 −0.028".
neg : K1<0 만.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir  # noqa: E402

K = None


def load():
    global K
    if K is None:
        K = pd.read_pickle(out_dir(__file__) / "review_K.pkl")
    return K


def nw(x, h):
    x = np.asarray(pd.Series(x).dropna(), float)
    T = len(x)
    lag = max(0, h // 5 - 1)
    m = x.mean()
    e = x - m
    l0 = e @ e / T
    for lg in range(1, lag + 1):
        l0 += 2 * (1 - lg / (lag + 1)) * (e[lg:] @ e[:-lg]) / T
    return m, m / np.sqrt(l0 / T), T


def frame(ycol, h, extra=(), sub=None):
    k = load().dropna(subset=["K1", ycol] + list(extra)).copy()
    if sub is not None:
        k = k[sub(k)]
    k["n_grp"] = k.groupby(["asof", "sector"])["code"].transform("size")
    k = k[k["n_grp"] >= 10].copy()
    k["resid"] = k[ycol] - k.groupby(["asof", "sector"])[ycol].transform("mean")
    return k


def ic(k, x, ctrl=()):
    out = {}
    for d, gd in k.groupby("asof"):
        ics, ns = [], []
        for _, g in gd.groupby("sector"):
            rx = g[x].rank().to_numpy(float)
            ry = g["resid"].rank().to_numpy(float)
            if ctrl:
                Z = np.column_stack([np.ones(len(g))] + [g[c].rank().to_numpy(float) for c in ctrl])
                rx = rx - Z @ np.linalg.lstsq(Z, rx, rcond=None)[0]
                ry = ry - Z @ np.linalg.lstsq(Z, ry, rcond=None)[0]
            sx, sy = rx.std(), ry.std()
            if sx < 1e-12 or sy < 1e-12:
                continue
            ics.append(np.corrcoef(rx, ry)[0, 1])
            ns.append(len(g))
        if ics:
            out[d] = np.average(ics, weights=ns)
    return pd.Series(out).sort_index()


def rep(name, s, h):
    for per, v in (("disc", s[s.index <= "2022-12-31"]), ("conf", s[s.index >= "2023-01-01"])):
        m, t, n = nw(v, h)
        print(f"{name:45s} h{h} {per}: IC {m:+.4f} NWt {t:+.2f} n {n}", flush=True)


def main(which):
    for h in (5, 20):
        y = f"r{h}"
        if which == "base":
            k = frame(y, h, extra=("p20", "lcap"))
            rep("K1 (base, p20 avail)", ic(k, "K1"), h)
            rep("-ret20 (rev)", ic(k, "nret20"), h)
            rep("K1 | rank p20", ic(k, "K1", ("p20",)), h)
            rep("K1 | rank p20, lcap", ic(k, "K1", ("p20", "lcap")), h)
            rep("K1 | rank p20, p5, lcap", ic(k.dropna(subset=["p5"]), "K1", ("p20", "p5", "lcap")), h)
            rep("-ret20 | K1", ic(k, "nret20", ("K1",)), h)
        elif which == "nz":
            k = frame(y, h, extra=("p20", "lcap"), sub=lambda k: k["K1"] != 0)
            rep("K1 nonzero only", ic(k, "K1"), h)
            rep("K1 nonzero | p20,lcap", ic(k, "K1", ("p20", "lcap")), h)
            k = frame(y, h, extra=("p20",), sub=lambda k: k["K1"] > 0)
            rep("K1>0 only", ic(k, "K1"), h)
        elif which == "skip":
            k = frame(f"s{h}", h, extra=("p20", "lcap"))
            rep("K1 entry close t+1", ic(k, "K1"), h)
            rep("K1 entry t+1 | p20,lcap", ic(k, "K1", ("p20", "lcap")), h)
            rep("-ret20 entry t+1", ic(k, "nret20"), h)
        elif which == "neg":
            k = frame(y, h, extra=("p20",), sub=lambda k: k["K1"] < 0)
            rep("K1<0 only", ic(k, "K1"), h)
            rep("K1<0 only | p20", ic(k, "K1", ("p20",)), h)
        else:
            sys.exit("base|nz|skip|neg")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "base")
