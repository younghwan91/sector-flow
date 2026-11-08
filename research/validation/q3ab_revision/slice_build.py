"""긴 적재(long.pkl)를 기준일별로 잘라 payload → sector_numbers.build() 를 만든다.

기준일마다 다시 계산하는 것(창에 의존하는 것):
  * 날짜 목록 = 전 소스 supply_demand 날짜 중 기준일 이하 마지막 260개(storage.recent_dates 와 같은 규칙)
  * cap_lag = 창 안에서 종목별 shift(1)  (창 안 첫 행은 NaN — 새로 적재할 때와 같다)
  * 기간말 섹터 시총 = sector_flow.sector_cap(창 df, 기준일)
나머지(종목-일 금액·시총)는 창과 무관하다.

출력: slices/<asof>.payload.json (충실성 검사용, 두 날짜만), slices/<asof>.num.pkl (축약 build 결과)
"""
import json
import os
import pickle
import sys
from multiprocessing import Pool

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import FIRST_RUN_REV, out_dir, use_report_code  # noqa: E402

HERE = str(out_dir(__file__))
use_report_code(FIRST_RUN_REV, Path(HERE) / "report_code")  # 첫 검증의 리포트 코드(bd27017)
import sector_flow as sf  # noqa: E402
import sector_numbers as sn  # noqa: E402
from long_load import con_ro  # noqa: E402

OUT = os.path.join(HERE, "slices")
KEEP_PAYLOAD = {"2026-08-27", "2026-10-13"}
L = None


def slim(num: dict) -> dict:
    keys = ("sector", "inst", "cap", "accel", "a_idx", "x", "xddot", "G", "G_pass", "thin", "n_all", "ret")
    blocks = {bk: {"k": b["k"], "t": b["t"], "rows": [{k: r.get(k) for k in keys} for r in b["rows"]]}
              for bk, b in num["blocks"].items()}
    comb = {mk: [{k: r.get(k) for k in ("sector", "G", "G_pass", "thin", "n_all")} for r in c["rows"]]
            for mk, c in num["combined"].items()}
    return {"asof": num["asof"], "blocks": blocks, "combined": comb}


def payload_for(asof: str, price_col: "str | None" = None) -> dict:
    df, all_dates = L["df"], L["all_dates"]
    dates = [d for d in all_dates if d <= asof][-260:]
    sub = df[df["date"].isin(set(dates))].copy()
    sub = sub.sort_values(["code", "date"])
    sub["cap_lag"] = sub.groupby("code")["cap"].shift(1)
    con = con_ro()
    caps = sf.sector_cap(con, sub, dates[-1])
    con.close()
    return sf.build_payload(sub, dates, caps, {})


def one(asof: str):
    P = payload_for(asof)
    if asof in KEEP_PAYLOAD:
        with open(os.path.join(OUT, f"{asof}.payload.json"), "w", encoding="utf-8") as f:
            json.dump(P, f, ensure_ascii=False)
    num = sn.build(P)
    with open(os.path.join(OUT, f"{asof}.num.pkl"), "wb") as f:
        pickle.dump(slim(num), f)
    return asof


def init():
    global L
    with open(os.path.join(HERE, "long.pkl"), "rb") as f:
        L = pickle.load(f)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    init()
    asofs = sys.argv[1:] or L["asofs"]
    with Pool(8, initializer=init) as pool:
        for a in pool.imap_unordered(one, asofs):
            print("done", a, flush=True)
