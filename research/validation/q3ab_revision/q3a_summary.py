"""Q3a 요약 — q3a_compare.py 산출물(out/q3a_blocks.pkl·q3a_attribution.csv)에서 판정 숫자."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir  # noqa: E402

W = out_dir(__file__)
R = pd.read_pickle(W / "q3a_blocks.pkl")
R = R[R.pair == "main"]
T = R[R.win == 20].copy()
T["ok_a"] = T.rho_a >= 0.95
T["ok_G"] = T.rho_G >= 0.95
T["ok_d"] = T.dG <= 1
T["ok"] = T.ok_a & T.ok_G & T.ok_d
by = T.groupby("asof")[["ok", "ok_a", "ok_G", "ok_d"]].all()
print("20일 세 시장 동시 통과 몫 (전 48):", by.mean().round(3).to_dict())
print("10-27(수집 미완·관문 이전) 제외:", by.drop("2026-10-27").mean().round(3).to_dict())
fail = by[~by.ok].index.tolist()
print("실패 기준일", len(fail), fail)
# 단일 통과 절벽: 한쪽 블록의 통과 수가 0 이고 다른 쪽이 2 이상
cl = R[((R.n_pass_pub == 0) & (R.n_pass_new >= 2)) | ((R.n_pass_new == 0) & (R.n_pass_pub >= 2))]
print("0↔≥2 절벽 블록:", len(cl), cl[["asof", "block", "n_pass_pub", "n_pass_new"]].values.tolist())
print(
    "20일 flip 분포(전 블록, cap>0 전 섹터): mean",
    T.flip.mean().round(4),
    "몫>0",
    (T.flip > 0).mean().round(3),
    "max",
    T.flip.max(),
)
print(
    "20일 rho_a 하위10%:",
    T.groupby("mkt").rho_a.quantile(0.1).round(4).to_dict(),
    "rho_G 하위10%",
    T.groupby("mkt").rho_G.quantile(0.1).round(3).to_dict(),
)
print(
    "20일 rho_Gi(교집합) 중앙/하위10%:",
    T.rho_Gi.median().round(3),
    T.rho_Gi.quantile(0.1).round(3),
    "n_Gboth 중앙",
    T.n_Gboth.median(),
)
A = pd.read_csv(W / "q3a_attribution.csv")
print(
    "T일 기관 Σ|Δ|/Σ|게시|: 중앙",
    A.inst_absdiff_T_share.median().round(4),
    "p90",
    A.inst_absdiff_T_share.quantile(0.9).round(3),
)
