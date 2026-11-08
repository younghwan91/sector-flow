"""Q3b — 금액 근사: 기관 순매매 수량 × supply_demand.close (리포트) vs × VWAP, 그리고 × 정규장 종가.

VWAP = daily_bars_raw.trade_value(백만원) × 1e6 / daily_bars_raw.volume  (원/주, 무수정 — 수량이 무수정이라)
       raw 가 없거나 거래량 0 이면 daily_bars 의 같은 비율, 그것도 없으면 종가(폴백 몫을 센다).
       2026-09-14~ 거래대금·거래량은 애프터마켓 포함 통합값 → 통합 VWAP.
REG  = regular_close 뷰(2026-09-14 전 = sd.close, 후 = 기준가 항등식), NULL(pending 등)이면 종가 폴백.

기준일 48개 전부 × 블록(전체·거래소·코스닥) × 20일 창. 섹터 J = Σ 수량 × 가격 / 1e8 (억).
지표: 섹터별 |J_v − J_c| / |J_c| 의 중앙값(J_c ≠ 0 인 섹터), J 순위 Spearman, 가속(J/시총) 순위 Spearman.
합격(사전등록): |상대차| 중앙값 ≤ 5% 이고 순위 Spearman ≥ 0.95.
"""
import os
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir  # noqa: E402

HERE = str(out_dir(__file__))
EOK = 1e8


def main():
    L = pickle.load(open(os.path.join(HERE, "long.pkl"), "rb"))
    df, px, ad, asofs = L["df"], L["px"], L["all_dates"], L["asofs"]
    df = df.reset_index(drop=True)
    px = px.drop_duplicates(["code", "date"])
    d = df[["code", "date", "market", "sector", "close", "institution", "acc_trde_qty", "cap"]].merge(
        px, on=["code", "date"], how="left")
    assert len(d) == len(df)
    c = d["close"].astype(float)
    v_raw = np.where((d.raw_vol > 0) & (d.raw_tv > 0), d.raw_tv * 1e6 / d.raw_vol.where(d.raw_vol > 0), np.nan)
    v_db = np.where((d.db_vol > 0) & (d.db_tv > 0), d.db_tv * 1e6 / d.db_vol.where(d.db_vol > 0), np.nan)
    vw = pd.Series(v_raw, index=d.index)
    src = pd.Series(np.where(~np.isnan(v_raw), "raw", ""), index=d.index)
    m2 = vw.isna() & ~np.isnan(v_db)
    vw[m2] = v_db[m2]
    src[m2] = "daily_bars"
    m3 = vw.isna()
    vw[m3] = c[m3]
    src[m3] = "close_fallback"
    d["vwap"], d["vwap_src"] = vw, src
    d["reg"] = d["reg_close"].astype(float).where(d["reg_close"].notna(), c)
    d["reg_fb"] = d["reg_close"].isna()

    # 진단: 가격 비율
    ratio = d["vwap"] / c
    lr = np.log(ratio.where(ratio > 0))
    q = d.institution.abs() * c / EOK   # 기관 금액 가중
    post = d.date >= "2026-09-14"
    print("VWAP 출처 몫:", d.vwap_src.value_counts(normalize=True).round(4).to_dict())
    print("VWAP 폴백 행의 기관 |금액| 몫:", round(q[d.vwap_src == "close_fallback"].sum() / q.sum(), 5))
    for nm, mk in (("09-14 전", ~post), ("09-14~", post)):
        r = lr[mk].dropna()
        print(f"log(VWAP/종가) {nm}: 중앙 {r.median():+.4f} · |.| 중앙 {r.abs().median():.4f} · |.| p90 {r.abs().quantile(.9):.4f}"
              f" · |.|>0.3 행 {int((r.abs() > 0.3).sum())}")
        r2 = np.log(d.reg[mk] / c[mk]).dropna()
        print(f"log(정규장/통합종가) {nm}: |.| 중앙 {r2.abs().median():.4f} · |.| p90 {r2.abs().quantile(.9):.4f} · 다른 행 몫 {(r2 != 0).mean():.3f}"
              f" · 정규장 폴백 몫 {d.reg_fb[mk].mean():.4f}")
    big = d[lr.abs() > 0.3]
    print("|log(VWAP/종가)|>0.3 행 기관 |금액| 몫:", round(q[lr.abs() > 0.3].sum() / q.sum(), 5))
    print(big.assign(ratio=ratio[big.index]).sort_values("ratio")[["code", "date", "close", "raw_close", "vwap", "vwap_src", "ratio"]]
          .head(8).to_string(index=False))

    for k, p in (("J_c", c), ("J_v", d.vwap), ("J_r", d.reg)):
        d[k] = d.institution.astype(float) * p / EOK

    recs, sect = [], []
    for a in asofs:
        win = [x for x in ad if x <= a][-20:]
        sub = d[d.date.isin(set(win))]
        capT = d[d.date == a]
        for mk, mks in (("전체", ("거래소", "코스닥")), ("거래소", ("거래소",)), ("코스닥", ("코스닥",))):
            s = sub[sub.market.isin(mks)]
            g = s.groupby("sector")[["J_c", "J_v", "J_r"]].sum()
            n = s.groupby("sector")["code"].nunique()
            cap = capT[capT.market.isin(mks)].groupby("sector")["cap"].sum() / EOK
            g["n"] = n
            g["cap"] = cap
            g = g[g.J_c != 0]
            for alt in ("J_v", "J_r"):
                for scope, G in (("all", g), ("n>=10", g[g.n >= 10])):
                    rel = (G[alt] - G.J_c).abs() / G.J_c.abs()
                    acc = G[G.cap > 0]
                    recs.append({"asof": a, "mkt": mk, "alt": alt, "scope": scope, "n": len(G),
                                 "relmed": rel.median(), "relp90": rel.quantile(.9),
                                 "worst_sector": rel.idxmax(), "worst_rel": rel.max(),
                                 "rho_J": spearmanr(G.J_c, G[alt]).correlation,
                                 "rho_acc": spearmanr(acc.J_c / acc.cap, acc[alt] / acc.cap).correlation,
                                 "flip": float((np.sign(G.J_c) != np.sign(G[alt])).mean()),
                                 "flip_secs": list(G.index[np.sign(G.J_c) != np.sign(G[alt])])})
            if mk == "전체" and a == asofs[-1]:
                sect.append(g.assign(rel_v=(g.J_v - g.J_c) / g.J_c.abs(), rel_r=(g.J_r - g.J_c) / g.J_c.abs()))
    R = pd.DataFrame(recs)
    R.to_csv(os.path.join(HERE, "q3b_blocks.csv"), index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print("\n===== 최신 기준일", asofs[-1], "=====")
    print(R[R["asof"] == asofs[-1]].drop(columns=["asof"]).round(4).to_string(index=False))
    print("\n전체 시장 섹터별(최신, J 억):")
    print(sect[0].sort_values("J_c").round(3).to_string())
    print("\n===== 48 기준일 분포 =====")
    agg = R.groupby(["alt", "scope", "mkt"]).agg(
        relmed_med=("relmed", "median"), relmed_max=("relmed", "max"), relmed_p90=("relmed", lambda v: v.quantile(.9)),
        rho_J_med=("rho_J", "median"), rho_J_p10=("rho_J", lambda v: v.quantile(.1)), rho_J_min=("rho_J", "min"),
        rho_acc_min=("rho_acc", "min"), flip_max=("flip", "max"),
        ok=("relmed", lambda v: 0))
    R["ok"] = (R.relmed <= 0.05) & (R.rho_J >= 0.95)
    agg["ok"] = R.groupby(["alt", "scope", "mkt"])["ok"].mean()
    print(agg.round(4).to_string())
    print("\n최악 기준일(J_v, all):")
    W = R[(R.alt == "J_v") & (R.scope == "all")].sort_values("relmed", ascending=False).head(6)
    print(W[["asof", "mkt", "n", "relmed", "relp90", "worst_sector", "worst_rel", "rho_J", "flip_secs"]].round(4).to_string(index=False))
    W = R[(R.alt == "J_v") & (R.scope == "all")].sort_values("rho_J").head(6)
    print(W[["asof", "mkt", "n", "relmed", "rho_J", "rho_acc", "flip_secs"]].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
