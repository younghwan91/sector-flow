"""Q1 표본 — 게시 2026-11-06 payload 에서 손검산할 15셀과 원자료를 뽑는다(haiku-verifier 입력).
usage: make_sample.py [게시 payload 경로]  — 기본 <reports>/2026-11-06/payload.json. 문서 숫자는 18:10 잠정 게시본으로 냈다."""

import json
import os

import pandas as pd

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import connect_ro, out_dir, reports_dir  # noqa: E402

OUT = out_dir(__file__)
SRC = sys.argv[1] if len(sys.argv) > 1 else str(reports_dir() / "2026-11-06" / "payload.json")
c = connect_ro()


def q(s, p=None):
    return pd.read_sql(s, c, params=p)


P = json.load(open(SRC))
D = P["dates"]
SECT = "coalesce(nullif(s.sector,''),'(미분류)')"
ACT = {
    "inst": "institution",
    "forgn": "foreign_",
    "indiv": "individual",
    "etc": "etc_corp",
    "invtrt": "invtrt",
    "penfnd_etc": "penfnd_etc",
}
cells = [  # (kind, market, sector, field, date)
    ("flow", "거래소", "부동산", "inst", "2026-10-20"),
    ("flow", "코스닥", "출판/매체복제", "forgn", "2026-09-03"),
    ("flow", "거래소", "통신", "indiv", "2026-07-15"),
    ("flow", "코스닥", "운송/창고", "etc", "2026-03-12"),
    ("flow", "거래소", "제조", "inst", "2026-11-05"),
    ("flow", "거래소", "의료/정밀기기", "invtrt", "2026-10-29"),
    ("flow", "거래소", "전기/가스", "penfnd_etc", "2026-02-24"),
    ("tv", "거래소", "전기/가스", "tv", "2026-08-11"),
    ("tv", "코스닥", "종이/목재", "tv", "2025-12-04"),
    ("ret", "거래소", "부동산", "ret", "2026-10-21"),
    ("ret", "코스닥", "운송/창고", "ret", "2026-06-10"),
    ("ret", "거래소", "통신", "ret", "2026-09-22"),
    ("ret", "코스닥", "출판/매체복제", "ret", "2026-11-04"),
    ("cap", "거래소", "의료/정밀기기", "2026-10", None),
    ("cap", "코스닥", "통신", "2026-09", None),
]
out = {
    "source_payload": SRC,
    "definitions": {
        "flow": "Σ_codes supply_demand.<qty col> × supply_demand.close / 1e8 (억원). qty col: inst=institution, forgn=foreign_, indiv=individual, etc=etc_corp, invtrt/penfnd_etc same name (payload detail[...]). members = stocks with stocks.market=M and coalesce(nullif(sector,''),'(미분류)')=S, source kiwoom rows on that date. Payload rounds to 0.01.",
        "tv": "Σ supply_demand.acc_trde_qty × supply_demand.close / 1e8 (억원) — NOT daily_bars.trade_value",
        "ret": "Σ w_i·flu_rt_i/100 / Σ w_i (%), w_i = daily_bars.close(prev trading date) × shares_asof(prev date) / 1e8; shares_asof = latest shares_outstanding_history row with date<=prev date; flu_rt from supply_demand on the date (bp). payload rounds to 0.001; retw = Σ w_i rounded to 0.1",
        "cap": "Σ daily_bars.close(last trading date of month within window) × shares_asof(that date) / 1e8 (억원), payload cap_by_month rounded 0.1",
    },
    "tolerance": {"amount_eok": 0.1, "ret_pct": 0.001},
    "cells": [],
}
for kind, m, s, f, d in cells:
    cell = {"kind": kind, "market": m, "sector": s, "field": f, "date": d}
    if kind in ("flow", "tv"):
        col = "acc_trde_qty" if kind == "tv" else ACT[f]
        x = q(
            f"""select sd.code,s.name,sd.{col} qty,sd.close from supply_demand sd join stocks s using(code)
               where sd.date=%s and s.market=%s and {SECT}=%s and sd.source like 'kiwoom%%' order by sd.code""",
            (d, m, s),
        )
        val = float((x.qty.astype(float) * x.close).sum() / 1e8)
        i = D.index(d)
        pub = (
            P["flows"][m][s]["tv"][i]
            if kind == "tv"
            else (P["flows"][m][s][f][i] if f in P["flows"][m][s] else P["detail"][m][s][f][i])
        )
        cell.update(qty_column=col, rows=x.to_dict("records"))
    elif kind == "ret":
        i = D.index(d)
        pdte = D[i - 1]
        x = q(
            f"""select sd.code,s.name,sd.flu_rt,b.close prev_close,
               (select shares_outstanding from shares_outstanding_history h where h.code=sd.code and h.date<=%(p)s order by date desc, source desc limit 1) shares_asof,
               (select max(date)::text from shares_outstanding_history h where h.code=sd.code and h.date<=%(p)s) shares_date
             from supply_demand sd join stocks s using(code) left join daily_bars b on b.code=sd.code and b.date=%(p)s
             where sd.date=%(d)s and s.market=%(m)s and {SECT}=%(s)s and sd.source like 'kiwoom%%' order by sd.code""",
            dict(d=d, p=pdte, m=m, s=s),
        )
        x["w"] = x.prev_close * x.shares_asof / 1e8
        ok = x.w.notna()
        val = float((x.w[ok] * x.flu_rt[ok] / 100).sum() / x.w[ok].sum())
        pub = P["ret"][m][s][i]
        cell.update(
            prev_date=pdte,
            payload_retw=P["retw"][m][s][i],
            my_retw=round(float(x.w[ok].sum()), 2),
            rows=x.to_dict("records"),
        )
    else:
        md = max(t for t in D if t[:7] == f)
        x = q(
            f"""select b.code,s.name,b.close,
               (select shares_outstanding from shares_outstanding_history h where h.code=b.code and h.date<=%(d)s order by date desc, source desc limit 1) shares_asof,
               (select max(date)::text from shares_outstanding_history h where h.code=b.code and h.date<=%(d)s) shares_date
             from daily_bars b join stocks s using(code) where b.date=%(d)s and s.market=%(m)s and {SECT}=%(s)s order by b.code""",
            dict(d=md, m=m, s=s),
        )
        val = float((x.close.astype(float) * x.shares_asof / 1e8).sum())
        pub = P["cap_by_month"][m][s][f]
        cell.update(date=md, month=f, rows=x.to_dict("records"))
    cell.update(payload_value=pub, my_value=round(val, 4), abs_diff=round(abs(val - pub), 4))
    out["cells"].append(cell)
    print(kind, m, s, f, d, "pub", pub, "mine", round(val, 4), "n", len(cell["rows"]))
json.dump(out, open(os.path.join(OUT, "sample_15cells.json"), "w"), ensure_ascii=False, indent=1, default=str)
