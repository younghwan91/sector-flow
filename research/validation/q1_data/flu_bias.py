"""결함 2(섹터 수익률 = flu_rt) 크기 — flu_rt-based sector return vs close-to-close (integrated) vs regular-close returns, 20d windows."""

import numpy as np
import pandas as pd

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import connect_ro  # noqa: E402

c = connect_ro()


def q(s, p=None):
    return pd.read_sql(s, c, params=p)


for end, label in [("2026-09-11", "pre-aftermarket 20d to 09-11"), ("2026-11-05", "post-aftermarket 20d to 11-05")]:
    ds = (
        q("select distinct date from supply_demand where date<=%s order by date desc limit 21", (end,))
        .date.sort_values()
        .tolist()
    )
    x = q(
        """select sd.code,sd.date,sd.flu_rt,b.close bc,rc.close rc,s.market,coalesce(nullif(s.sector,''),'(미분류)') sector,
           (select shares_outstanding from shares_outstanding_history h where h.code=sd.code and h.date<=sd.date order by date desc limit 1) sh
        from supply_demand sd join stocks s using(code) join daily_bars b on b.code=sd.code and b.date=sd.date
        left join regular_close rc on rc.code=sd.code and rc.date=sd.date
        where sd.date between %s and %s and sd.source like 'kiwoom%%'""",
        (ds[0], ds[-1]),
    )
    x = x.sort_values(["code", "date"])
    for k in ["bc", "rc"]:
        x["p" + k] = x.groupby("code")[k].shift(1)
    x["w"] = x.groupby("code").bc.shift(1) * x.groupby("code").sh.shift(1)
    x = x[x.date > ds[0]].dropna(subset=["w"])
    x["r_flu"] = x.flu_rt / 1e4
    x["r_cc"] = x.bc / x.pbc - 1
    x["r_rr"] = x.rc / x.prc - 1
    rows = []
    for (m, s), g in x.groupby(["market", "sector"]):
        out = {"m": m, "s": s}
        for k in ["r_flu", "r_cc", "r_rr"]:
            gg = g.dropna(subset=[k])
            daily = (gg.w * gg[k]).groupby(gg.date).sum() / gg.w.groupby(gg.date).sum()
            out[k] = 100 * (np.prod(1 + daily) - 1)
        rows.append(out)
    r = pd.DataFrame(rows)
    r["flu-cc"] = r.r_flu - r.r_cc
    r["flu-rr"] = r.r_flu - r.r_rr
    print(label)
    print(r[["flu-cc", "flu-rr"]].describe().round(3).T.to_string())
    print("median|flu-cc|", r["flu-cc"].abs().median().round(3), "median|flu-rr|", r["flu-rr"].abs().median().round(3))
    print(
        "spearman flu vs cc",
        r.r_flu.rank().corr(r.r_cc.rank()).round(4),
        " flu vs rr",
        r.r_flu.rank().corr(r.r_rr.rank()).round(4),
    )
    print(r.reindex(r["flu-cc"].abs().sort_values(ascending=False).index).head(5).round(2).to_string())
