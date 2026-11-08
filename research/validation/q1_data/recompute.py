"""Q1 데이터 — Independent recompute of sector-flow payload data layer from kr_quant (read-only).
Definitions used (README §7, CLAUDE.md 데이터 함정):
  flow[actor] = Σ_code supply_demand.<actor qty> × supply_demand.close / 1e8  (억원)
  tv          = Σ daily_bars.trade_value / 100                               (백만원→억원)
  cap         = Σ daily_bars.close × shares_asof / 1e8 at last date            (억원)
  cap_by_month= same at last window date of each month
  ret[t]      = Σ w_{t-1} r_t / Σ w_{t-1}, w = close×shares_asof (prev day), r from daily_bars_adjusted close ratio (%); ret[0]=0
  retw[t]     = Σ w_{t-1} (억원)
membership: stocks.market/sector (current snapshot), or --members payload.json (that payload's names map).
"""

import argparse
import json
import os

import numpy as np
import pandas as pd

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import connect_ro, out_dir  # noqa: E402

OUT = out_dir(__file__)

ap = argparse.ArgumentParser()
ap.add_argument("--asof", required=True)
ap.add_argument("--days", type=int, default=260)
ap.add_argument("--members", default=None, help="payload json whose names map defines membership")
ap.add_argument("--retsrc", default="daily_bars_adjusted")
ap.add_argument("--flowclose", default="sd", choices=["sd", "bars"])
ap.add_argument("--out", required=True)
ap.add_argument(
    "--unadjust", action="store_true", help="undo daily_bars back-adjustment from corporate events dated after asof"
)
ap.add_argument("--capclose", default="bars", choices=["bars", "raw"])
ap.add_argument(
    "--exclude-shares",
    default=None,
    help="json list [code,date,...] of shares rows inserted after asof (inputs/backfill_20261102_inserted_rows.json)",
)
ap.add_argument(
    "--add-legacy-shares",
    action="store_true",
    help="add back legacy.shares_outstanding_dart_unit_errors (removed 2026-10-12)",
)
ap.add_argument("--tv", default="bars", choices=["bars", "sd"])
ap.add_argument("--ret", default="adj", choices=["adj", "flu"])
a = ap.parse_args()
c = connect_ro()


def q(s, p=None):
    return pd.read_sql(s, c, params=p)


dates = (
    q(
        "select distinct date from supply_demand where date<=%s and source like 'kiwoom%%' order by date desc limit %s",
        (a.asof, a.days + 1),
    )["date"]
    .sort_values()
    .tolist()
)
prev_date, dates = dates[0], dates[1:]
d0, d1 = dates[0], dates[-1]
if a.members:
    nm = json.load(open(a.members, encoding="utf-8"))["names"]
    st = pd.DataFrame([(k, v["market"], v["sector"]) for k, v in nm.items()], columns=["code", "market", "sector"])
else:
    st = q("select code,market,coalesce(nullif(sector,''),'(미분류)') sector from stocks")
codes = tuple(st.code)

sd = q(
    """select code,date,close,acc_trde_qty,flu_rt,individual,foreign_,institution,etc_corp,fnnc_invt,insrnc,invtrt,bank,penfnd_etc,samo_fund
        from supply_demand where date between %s and %s and code in %s""",
    (d0, d1, codes),
)
bars = q(
    "select code,date,close,trade_value from daily_bars where date between %s and %s and code in %s",
    (prev_date, d1, codes),
)
adj = q(f"select code,date,close from {a.retsrc} where date between %s and %s and code in %s", (prev_date, d1, codes))
sh = q(
    "select code,date,shares_outstanding,source,coalesce(knowledge_date,date) kd from shares_outstanding_history where code in %s and coalesce(knowledge_date,date)<=%s",
    (codes, a.asof),
)
if a.exclude_shares:
    ex = pd.DataFrame([(r[0], r[1]) for r in json.load(open(a.exclude_shares))], columns=["code", "d"])
    ex["x"] = 1
    sh["d"] = sh.date.astype(str)
    sh = sh.merge(ex, on=["code", "d"], how="left")
    n0 = len(sh)
    sh = sh[sh.x.isna()].drop(columns=["d", "x"])
    print("excluded shares rows", n0 - len(sh))
if a.add_legacy_shares:
    lg = q(
        "select code,date,shares_outstanding,source,coalesce(knowledge_date,date) kd from legacy.shares_outstanding_dart_unit_errors where code in %s",
        (codes,),
    )
    sh = pd.concat([sh, lg])
    print("added legacy rows", len(lg))
sh = sh.sort_values(["code", "date", "source"]).drop_duplicates(["code", "date"], keep="last")  # tie: kiwoom > dart

ACT = {"inst": "institution", "forgn": "foreign_", "indiv": "individual", "etc": "etc_corp"}
DET = ["fnnc_invt", "insrnc", "invtrt", "bank", "penfnd_etc", "samo_fund"]
sd = sd.merge(st, on="code")
if a.flowclose == "bars":
    sd = sd.drop(columns="close").merge(bars[["code", "date", "close"]], on=["code", "date"], how="left")
didx = {d: i for i, d in enumerate(dates)}
markets = ["거래소", "코스닥"]
sectors = sorted(set(st.sector))
out = {
    "dates": [str(d) for d in dates],
    "flows": {},
    "detail": {},
    "cap": {},
    "cap_by_month": {},
    "ret": {},
    "retw": {},
}
for m in markets:
    out["flows"][m] = {}
    out["detail"][m] = {}
    for s in sectors:
        g = sd[(sd.market == m) & (sd.sector == s)]
        f = {}
        for k, col in list(ACT.items()) + [(x, x) for x in DET]:
            v = (g[col].astype(float) * g.close.astype(float)).groupby(g.date).sum() / 1e8
            arr = np.zeros(len(dates))
            for d, x in v.items():
                arr[didx[d]] = x
            f[k] = arr.tolist()
        out["flows"][m][s] = {k: f[k] for k in ACT}
        out["detail"][m][s] = {k: f[k] for k in DET}

# tv
b = bars.merge(st, on="code")
b = b[b.date >= d0]
for m in markets:
    for s in sectors:
        if a.tv == "sd":
            g = sd[(sd.market == m) & (sd.sector == s)]
            v = (g.acc_trde_qty.astype(float) * g.close.astype(float)).groupby(g.date).sum() / 1e8
        else:
            g = b[(b.market == m) & (b.sector == s)]
            v = g.trade_value.astype(float).groupby(g.date).sum() / 100
        arr = np.zeros(len(dates))
        for d, x in v.items():
            arr[didx[d]] = x
        out["flows"][m][s]["tv"] = arr.tolist()

if a.capclose == "raw":
    raw = q(
        "select code,date,close from daily_bars_raw where date between %s and %s and code in %s", (prev_date, d1, codes)
    )
    bars = bars.drop(columns="close").merge(raw, on=["code", "date"], how="left")
if a.unadjust:
    ev = q("select code,date,upd_rt from corporate_actions where date>%s and upd_rt<>0", (a.asof,))
    bars["f"] = 1.0
    for _, e in ev.iterrows():
        msk = (bars.code == e.code) & (bars.date < e.date)
        bars.loc[msk, "f"] *= 1 + e.upd_rt / 100
    bars["close"] = (bars.close.astype(float) / bars.f).round()
    print("unadjusted rows", (bars.f != 1).sum())
# shares as-of each date
allb = bars.merge(st, on="code").sort_values("date")
sh = sh.sort_values("date")
allb["date_dt"] = pd.to_datetime(allb.date)
sh["date_dt"] = pd.to_datetime(sh.date)
allb = pd.merge_asof(
    allb.sort_values("date_dt"),
    sh[["code", "date_dt", "shares_outstanding"]].sort_values("date_dt"),
    on="date_dt",
    by="code",
    direction="backward",
)
allb["mcap"] = allb.close.astype(float) * allb.shares_outstanding.astype(float) / 1e8
# cap & cap_by_month
month_last = {}
for d in dates:
    month_last[str(d)[:7]] = d
for m in markets:
    out["cap"][m] = {}
    out["cap_by_month"][m] = {}
    for s in sectors:
        g = allb[(allb.market == m) & (allb.sector == s)]
        if g.empty:
            continue
        bym = {mo: float(g[g.date == d].mcap.sum()) for mo, d in month_last.items()}
        out["cap_by_month"][m][s] = bym
        out["cap"][m][s] = bym[str(d1)[:7]]
# returns
adj = adj.sort_values(["code", "date"])
adj["pclose"] = adj.groupby("code").close.shift(1)
adj["pdate"] = adj.groupby("code").date.shift(1)
w = allb[["code", "date", "mcap"]].rename(columns={"date": "pdate", "mcap": "w"})
r = adj.merge(w, on=["code", "pdate"], how="left").merge(st, on="code")
r = r[(r.date >= d0)]
r["r"] = r.close / r.pclose - 1
if a.ret == "flu":
    fl = sd[["code", "date", "flu_rt"]]
    r = r.merge(fl, on=["code", "date"], how="left")
    r["r"] = r.flu_rt / 1e4
ok = r.w.notna() & r.r.notna() & (r.w > 0)
# require prev day = previous trading date
alld = [prev_date] + dates
prevmap = {alld[i + 1]: alld[i] for i in range(len(dates))}
ok &= r.pdate == r.date.map(prevmap)
r = r[ok]
for m in markets:
    out["ret"][m] = {}
    out["retw"][m] = {}
    for s in sectors:
        g = r[(r.market == m) & (r.sector == s)]
        ws = g.w.groupby(g.date).sum()
        wr = (g.w * g.r).groupby(g.date).sum()
        ret = np.zeros(len(dates))
        rw = np.zeros(len(dates))
        for d in ws.index:
            i = didx[d]
            if i == 0:
                continue
            rw[i] = ws[d]
            ret[i] = 100 * wr[d] / ws[d] if ws[d] > 0 else 0
        out["ret"][m][s] = ret.tolist()
        out["retw"][m][s] = rw.tolist()
outp = a.out if os.path.isabs(a.out) else os.path.join(OUT, a.out)
json.dump(out, open(outp, "w"), ensure_ascii=False)
print("wrote", outp, len(dates), d0, d1)
