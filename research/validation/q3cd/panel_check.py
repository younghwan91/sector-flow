"""리뷰 재계산(quant-reviewer, Q3d 재검) — 재구성 패널이 리포트와 같은지.

게시본 reports/2026-11-06/payload.json(확정 모드로 다시 만든 판)에 리포트의 sector_numbers.build() 를
돌린 20|전체 블록과, slice_build.py 의 같은 날 행(rows.csv.gz)을 섹터별로 맞춘다.
문서 숫자: a·x·inst·ret·G 최대차 4e-12, k 14.927·t 3.86 동일.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir, reports_dir, use_report_code  # noqa: E402

W = out_dir(__file__)
use_report_code(None, W / "report_code")
import sector_numbers as sn  # noqa: E402

sn.drivers = lambda *a, **k: {}
sn.rolling_pct = lambda *a, **k: None
sn.spark_segs = lambda *a, **k: []
ASOF = sys.argv[1] if len(sys.argv) > 1 else "2026-11-06"
P = json.load(open(reports_dir() / ASOF / "payload.json", encoding="utf-8"))
B = sn.build(P)["blocks"]["20|전체"]
rep = pd.DataFrame([{"sector": r["sector"], "a": r.get("a_idx"), "x": r.get("x"), "inst": r["inst"],
                     "G": r.get("G"), "ret": r["ret"]} for r in B["rows"]])
rows = pd.read_csv(W / "rows.csv.gz")
mine = rows[(rows["asof"] == ASOF) & (rows.win == 20) & (rows.mkt == "전체")][["sector", "a", "x", "inst", "G", "ret"]]
m = rep.merge(mine, on="sector", suffixes=("_rep", "_val"))
print("sectors report", len(rep), "rebuilt", len(mine), "matched", len(m))
for c in ["a", "x", "inst", "ret", "G"]:
    d = (m[c + "_rep"] - m[c + "_val"]).abs()
    print(c, "maxabs diff", d.max())
blk = pd.read_csv(W / "blocks.csv").query("asof == @ASOF and win == 20 and mkt == '전체'")
print("k report", B["k"], "t report", B["t"], "| rebuilt k,t", blk[["k", "t"]].values.tolist())
