"""Q3a — 게시본(잠정 T) vs 오늘 DB(확정)로 같은 기준일을 다시 만든 것.

비교 쌍:
  main  = build_현재코드(게시 payload)  vs  build_현재코드(재구성 payload)   ← 데이터 차이만
  html  = 게시 numbers.html D          vs  build_현재코드(재구성 payload)   ← 사용자가 본 것(코드 판 차이 포함)

블록마다:
  flip    = 가속 부호가 바뀐 섹터 몫 (cap>0 인 전 섹터; 얇은 섹터 제외판도)
  rho_a   = 가속 순위 Spearman (같은 집합)
  rho_G   = G 순위 Spearman — 얇지 않은 섹터 전부, G 없음(관문 탈락)은 맨 아래 동률(화면 정렬과 같다)
  rho_Gi  = G 가 양쪽 다 있는 섹터끼리만 (n≥3)
  dG      = G 통과 집합 대칭차 섹터 수 (in / out)
합격(사전등록): 기준일의 90% 이상에서 Spearman ≥ 0.95 이고 G 통과 집합 변화 ≤ 1섹터.
  — 20일 가속이 명시된 대상이라 20일 블록(시장별)으로 판정한다. 가속·G 둘 다 ≥0.95, dG ≤1.
"""
import json
import os
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir, reports_dir  # noqa: E402

HERE = str(out_dir(__file__))
REPORTS = str(reports_dir())


def ld(d, a):
    with open(os.path.join(HERE, d, f"{a}.num.pkl"), "rb") as f:
        return pickle.load(f)


def rho(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3:
        return np.nan
    if np.array_equal(x, y):
        return 1.0
    if np.all(x == x[0]) or np.all(y == y[0]):
        return 0.0          # 한쪽만 상수 — 순위가 정보를 잃었다
    return float(spearmanr(x, y).correlation)


def sgn(v):
    return 0 if v is None or v == 0 else (1 if v > 0 else -1)


def cmp_block(A, B):
    ra = {r["sector"]: r for r in A["rows"]}
    rb = {r["sector"]: r for r in B["rows"]}
    secs = sorted(s for s in set(ra) & set(rb) if (ra[s]["cap"] or 0) > 0 and (rb[s]["cap"] or 0) > 0)
    thick = [s for s in secs if not ra[s]["thin"] and not rb[s]["thin"]]
    out = {"n": len(secs), "n_thick": len(thick),
           "missing": sorted(set(ra) ^ set(rb))}
    out["flip"] = np.mean([sgn(ra[s]["accel"]) != sgn(rb[s]["accel"]) for s in secs]) if secs else np.nan
    out["flip_thick"] = np.mean([sgn(ra[s]["accel"]) != sgn(rb[s]["accel"]) for s in thick]) if thick else np.nan
    out["flip_secs"] = [s for s in secs if sgn(ra[s]["accel"]) != sgn(rb[s]["accel"])]
    out["rho_a"] = rho([ra[s]["accel"] for s in secs], [rb[s]["accel"] for s in secs])
    out["rho_a_thick"] = rho([ra[s]["accel"] for s in thick], [rb[s]["accel"] for s in thick])
    g = lambda r: -1.0 if r["G"] is None else r["G"]  # noqa: E731
    out["rho_G"] = rho([g(ra[s]) for s in thick], [g(rb[s]) for s in thick])
    both = [s for s in thick if ra[s]["G"] is not None and rb[s]["G"] is not None]
    out["n_Gboth"] = len(both)
    out["rho_Gi"] = rho([ra[s]["G"] for s in both], [rb[s]["G"] for s in both])
    pa = {s for s in ra if ra[s]["G_pass"]}
    pb = {s for s in rb if rb[s]["G_pass"]}
    out["G_in"] = sorted(pb - pa)     # 확정에서 새로 통과
    out["G_out"] = sorted(pa - pb)    # 확정에서 탈락
    out["dG"] = len(pa ^ pb)
    out["n_pass_pub"], out["n_pass_new"] = len(pa), len(pb)
    # 가속 상대차(얇지 않은 섹터, |a|>0) — 크기 감
    rel = [abs(rb[s]["accel"] - ra[s]["accel"]) / abs(ra[s]["accel"]) for s in thick if ra[s]["accel"]]
    out["accel_relmed"] = float(np.median(rel)) if rel else np.nan
    return out


def cmp_comb(A, B):
    ra = {r["sector"]: r for r in A}
    rb = {r["sector"]: r for r in B}
    secs = sorted(s for s in set(ra) & set(rb) if not ra[s]["thin"] and not rb[s]["thin"])
    g = lambda r: -1.0 if r["G"] is None else r["G"]  # noqa: E731
    pa = {s for s in ra if ra[s]["G_pass"]}
    pb = {s for s in rb if rb[s]["G_pass"]}
    return {"rho_G": rho([g(ra[s]) for s in secs], [g(rb[s]) for s in secs]),
            "dG": len(pa ^ pb), "G_in": sorted(pb - pa), "G_out": sorted(pa - pb)}


def attribution(L):
    """게시 payload 의 기관 일별 흐름 vs 오늘 DB — 차이가 T 하루에 있나, 그 전 날들에도 있나."""
    df = L["df"]
    g = df.groupby(["market", "sector", "date"])["inst"].sum().round(2)
    rows = []
    stocks_now = df.drop_duplicates("code", keep="last").set_index("code")[["sector", "market"]]
    for a in L["asofs"]:
        with open(os.path.join(REPORTS, a, "payload.json"), encoding="utf-8") as f:
            P = json.load(f)
        dates = P["dates"]
        dT = dpre = aT = apre = 0.0
        for m in P["markets"]:
            for s in P["sectors"]:
                arr = P["flows"][m][s]["inst"]
                for i, d in enumerate(dates):
                    new = float(g.get((m, s, d), 0.0))
                    diff = abs(new - arr[i])
                    if i == len(dates) - 1:
                        dT += diff
                        aT += abs(arr[i])
                    else:
                        dpre += diff
                        apre += abs(arr[i])
        # 섹터·시장 배정이 바뀐 종목(게시 names vs 오늘 stocks)
        moved = sum(1 for c, r in P["names"].items()
                    if c in stocks_now.index and (stocks_now.at[c, "sector"] != r["sector"]
                                                  or stocks_now.at[c, "market"] != r["market"]))
        rows.append({"asof": a, "inst_absdiff_T_share": dT / aT if aT else np.nan,
                     "inst_absdiff_preT_share": dpre / apre if apre else np.nan,
                     "inst_absdiff_T_eok": dT, "inst_absdiff_preT_eok": dpre,
                     "names_moved_sector": moved, "n_names_pub": P["n_names"],
                     "mtime_payload": pd.Timestamp(os.path.getmtime(os.path.join(REPORTS, a, "payload.json")), unit="s", tz="UTC").tz_convert("Asia/Seoul").strftime("%m-%d %H:%M")})
    return pd.DataFrame(rows)


def main():
    asofs = sorted(f[:10] for f in os.listdir(os.path.join(HERE, "slices")) if f.endswith(".num.pkl"))
    recs, comb = [], []
    for pair, pubdir in (("main", "slices_pub"), ("html", "slices_html")):
        for a in asofs:
            new, pub = ld("slices", a), ld(pubdir, a)
            for bk in new["blocks"]:
                if bk not in pub["blocks"]:
                    continue
                r = cmp_block(pub["blocks"][bk], new["blocks"][bk])
                r.update(pair=pair, asof=a, block=bk, win=int(bk.split("|")[0]), mkt=bk.split("|")[1])
                recs.append(r)
            for mk in new["combined"]:
                if mk in pub["combined"]:
                    c = cmp_comb(pub["combined"][mk], new["combined"][mk])
                    c.update(pair=pair, asof=a, mkt=mk)
                    comb.append(c)
    R = pd.DataFrame(recs)
    C = pd.DataFrame(comb)
    R.to_pickle(os.path.join(HERE, "q3a_blocks.pkl"))
    C.to_pickle(os.path.join(HERE, "q3a_combined.pkl"))
    R.drop(columns=["flip_secs", "missing"]).to_csv(os.path.join(HERE, "q3a_blocks.csv"), index=False)
    C.to_csv(os.path.join(HERE, "q3a_combined.csv"), index=False)

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.max_rows", 400)
    for pair in ("main", "html"):
        S = R[R.pair == pair]
        print(f"\n===== pair={pair} — 블록별 분포 (중앙값 / 하위10% / 최악) =====")
        agg = S.groupby(["win", "mkt"]).agg(
            flip_med=("flip", "median"), flip_p90=("flip", lambda v: v.quantile(.9)), flip_max=("flip", "max"),
            rho_a_med=("rho_a", "median"), rho_a_p10=("rho_a", lambda v: v.quantile(.1)), rho_a_min=("rho_a", "min"),
            rho_G_med=("rho_G", "median"), rho_G_p10=("rho_G", lambda v: v.quantile(.1)), rho_G_min=("rho_G", "min"),
            dG_med=("dG", "median"), dG_p90=("dG", lambda v: v.quantile(.9)), dG_max=("dG", "max"),
            accrel_med=("accel_relmed", "median"))
        print(agg.round(3).to_string())
        # 판정: 20일 블록
        T = S[S.win == 20].copy()
        T["ok"] = (T.rho_a >= 0.95) & (T.rho_G >= 0.95) & (T.dG <= 1)
        T["ok_a"] = T.rho_a >= 0.95
        T["ok_G"] = T.rho_G >= 0.95
        T["ok_d"] = T.dG <= 1
        print(f"\n-- pair={pair} 20일 판정: 기준일 중 통과 몫 --")
        print(T.groupby("mkt")[["ok", "ok_a", "ok_G", "ok_d"]].mean().round(3).to_string())
        allm = T.groupby("asof")["ok"].all()
        print(f"세 시장 모두 통과한 기준일 몫: {allm.mean():.3f} ({allm.sum()}/{len(allm)})  → 합격선 0.90: "
              f"{'PASS' if allm.mean() >= 0.9 else 'FAIL'}")
        # 전 창 판정(참고)
        W = S[S.win.isin([20, 60, 120])].copy()
        W["ok"] = (W.rho_a >= 0.95) & (W.rho_G >= 0.95) & (W.dG <= 1)
        print("참고 — 창·시장별 통과 몫:")
        print(W.groupby(["win", "mkt"])["ok"].mean().round(3).to_string())
        print(f"\n-- pair={pair} 20일 최악 기준일(시장별 rho_a·rho_G·dG) --")
        bad = T[~T.ok].sort_values(["mkt", "rho_a"])
        print(bad[["asof", "mkt", "flip", "flip_secs", "rho_a", "rho_G", "rho_Gi", "n_Gboth", "dG", "G_in", "G_out",
                   "n_pass_pub", "n_pass_new"]].to_string())
    print("\n===== 종합 G =====")
    for pair in ("main", "html"):
        S = C[C.pair == pair]
        print(pair, S.groupby("mkt").agg(rho_med=("rho_G", "median"), rho_p10=("rho_G", lambda v: v.quantile(.1)),
                                         dG_med=("dG", "median"), dG_p90=("dG", lambda v: v.quantile(.9)),
                                         dG_max=("dG", "max"), ok=("dG", lambda v: (v <= 1).mean())).round(3).to_string())

    with open(os.path.join(HERE, "long.pkl"), "rb") as f:
        L = pickle.load(f)
    A = attribution(L)
    A.to_csv(os.path.join(HERE, "q3a_attribution.csv"), index=False)
    print("\n===== 귀속: 게시 payload 기관 일별 흐름 vs 오늘 DB (Σ|Δ| / Σ|게시|) =====")
    print(A.round(4).to_string())


if __name__ == "__main__":
    main()
