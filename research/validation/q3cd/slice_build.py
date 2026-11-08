"""Q3c·Q3d — 긴 적재(long.pkl)를 기준일마다 [t-259, t] 로 잘라 리포트의 sector_numbers.build() 를 돌린다.

drivers·rolling_pct·spark_segs 는 k·x·ẍ·G 에 들어가지 않아 비워 둔다. 앞날 섹터 수익률은
sn.agg 로 긴 배열의 [t+1, t+h] 를 묶는다(전체 시장 + 시장별).

  --variant fixed (기본): out/long.pkl → out/. 리포트 코드는 이 작업 트리의 scripts/sector_numbers.py.
                          문서 숫자는 e8d8d12(PR #5) 판으로 냈다 — 판을 고정하려면 --code-rev e8d8d12.
  --variant v1          : out/v1/long.pkl → out/v1/. 리포트 코드는 bd27017 판(옛 정의 — 재현용).

출력: rows.csv.gz (기준일 × 창 × 시장 × 섹터), blocks.csv (블록별 k·b·t), fwd.csv (기준일 × 섹터 앞날 5·20일 수익률).
"""
import argparse
import os
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import FIRST_RUN_REV, out_dir, use_report_code  # noqa: E402

L = 260
WINS = (5, 20, 60, 120)
STEP = 5
HS = (5, 20)
sn = None


def load(work):
    with open(os.path.join(work, "long.pkl"), "rb") as f:
        return pickle.load(f)


def make_P(D, t):
    dates = D["dates"][t - L + 1: t + 1]
    pres = D["pres"][:, t - L + 1: t + 1]
    inwin = pres.any(axis=1)
    cs, cm = D["code_sector"], D["code_market"]
    sectors = sorted(set(cs[inwin]))
    markets = sorted(set(cm[inwin]))
    sl = slice(t - L + 1, t + 1)
    flows = {m: {s: {"inst": list(D["inst"][m][s][sl])} for s in sectors} for m in markets}
    ret = {m: {s: list(D["ret"][m][s][sl]) for s in sectors} for m in markets}
    retw = {m: {s: list(D["retw"][m][s][sl]) for s in sectors} for m in markets}
    capt = D["capP"][:, t]
    cap = {m: {} for m in markets}
    df = pd.DataFrame({"s": cs[inwin], "m": cm[inwin], "c": capt[inwin]}).dropna(subset=["c"])
    for (m, s), v in df.groupby(["m", "s"])["c"].sum().items():
        cap[m][s] = round(float(v), 1)
    names = {}
    idx = np.where(inwin)[0]
    for i in idx:
        win = {str(w): 1 for w in WINS if pres[i, L - w:].any()}
        names[D["codes"][i]] = {"sector": cs[i], "market": cm[i], "win": win}
    return {"dates": dates, "sectors": sectors, "markets": markets, "flows": flows, "ret": ret,
            "retw": retw, "cap": cap, "names": names, "finalized": True, "n_by_sector": {}}


def rows_of(out, asof):
    recs, blks = [], []
    for key, B in out["blocks"].items():
        w, mk = key.split("|")
        blks.append({"asof": asof, "win": int(w), "mkt": mk, "k": B["k"], "b": B["b"], "t": B["t"]})
        for r in B["rows"]:
            recs.append({"asof": asof, "win": int(w), "mkt": mk, "sector": r["sector"],
                         "n_all": r["n_all"], "thin": r["thin"], "inst": r["inst"],
                         "a": r.get("a_idx"), "ret": r["ret"], "x": r.get("x"),
                         "xdot": r.get("xdot"), "xddot": r.get("xddot"), "G": r.get("G")})
    return recs, blks


def main():
    global sn
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=("fixed", "v1"), default="fixed")
    ap.add_argument("--code-rev", default=None, help="리포트 코드 커밋(기본: fixed=작업 트리, v1=%s)" % FIRST_RUN_REV)
    a = ap.parse_args()
    work = out_dir(__file__, *(("v1",) if a.variant == "v1" else ()))
    rev = a.code_rev or (FIRST_RUN_REV if a.variant == "v1" else None)
    use_report_code(rev, work / "report_code")
    import sector_numbers  # noqa: E402  (판은 use_report_code 가 고른다)
    sn = sector_numbers
    sn.drivers = lambda *a, **k: {}
    sn.rolling_pct = lambda *a, **k: None
    sn.spark_segs = lambda *a, **k: []
    print("report code:", sn.__file__, flush=True)

    D = load(work)
    N = len(D["dates"])
    Pl = {"dates": D["dates"], "markets": D["markets"], "sectors": D["sectors"],
          "flows": {m: {s: {"inst": list(D["inst"][m][s])} for s in D["sectors"]} for m in D["markets"]},
          "ret": {m: {s: list(D["ret"][m][s]) for s in D["sectors"]} for m in D["markets"]},
          "retw": {m: {s: list(D["retw"][m][s]) for s in D["sectors"]} for m in D["markets"]},
          "cap": {}}
    ts = list(range(L - 1, N, STEP))
    if ts[-1] != N - 1:
        ts.append(N - 1)
    recs, blks, fwd = [], [], []
    t0 = time.time()
    for j, t in enumerate(ts):
        out = sn.build(make_P(D, t))
        r, b = rows_of(out, D["dates"][t])
        recs += r
        blks += b
        for s in D["sectors"]:
            rec = {"asof": D["dates"][t], "ti": t, "sector": s}
            for h in HS:
                if t + h <= N - 1:
                    rec[f"f{h}"] = sn.agg(Pl, D["markets"], s, t + 1, t + h)["ret"]
                    for mk in D["markets"]:
                        rec[f"f{h}_{mk}"] = sn.agg(Pl, [mk], s, t + 1, t + h)["ret"]
                else:
                    rec[f"f{h}"] = None
            fwd.append(rec)
        if j % 50 == 0:
            print(j, len(ts), D["dates"][t], f"{time.time()-t0:.0f}s", flush=True)
    pd.DataFrame(recs).to_csv(work / "rows.csv.gz", index=False)
    pd.DataFrame(blks).to_csv(work / "blocks.csv", index=False)
    pd.DataFrame(fwd).to_csv(work / "fwd.csv", index=False)
    print("done", len(ts), f"{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
