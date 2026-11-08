"""게시본 쪽: (1) 게시 payload.json 에 **현재** sector_numbers.build() 를 돌린 것(코드 판 차이를 지운 비교 기준),
(2) 게시 numbers.html 의 `const D = ` (사용자가 실제로 본 표).

2026-08-27 payload 는 옛 형식(names 가 일별 배열)이라 현재 build 가 종목 수를 0 으로 센다 → 창 합계로 'win' 을 합성한다
(그 창에 값이 하나라도 0 이 아닌 종목을 '거래된 종목' 으로 친다 — 근사).
"""
import json
import os
import pickle
import sys
from multiprocessing import Pool

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import FIRST_RUN_REV, out_dir, reports_dir, use_report_code  # noqa: E402

HERE = str(out_dir(__file__))
use_report_code(FIRST_RUN_REV, Path(HERE) / "report_code")  # 첫 검증의 리포트 코드(bd27017)
import sector_numbers as sn  # noqa: E402
from slice_build import slim  # noqa: E402

REPORTS = str(reports_dir())
LAST_ASOF = "2026-11-06"


def fix_old_names(P):
    n = len(P["dates"])
    for code, r in P["names"].items():
        if "win" in r:
            continue
        r["win"] = {}
        for w in sn.WINDOWS:
            win = w[0]
            if win > n:
                continue
            sl = slice(n - win, n)
            vals = {k: sum((r.get(k) or [0.0] * n)[sl]) for k in ("inst", "forgn", "indiv", "etc", "tv")}
            if any(any((r.get(k) or [0.0] * n)[sl]) for k in ("inst", "forgn", "indiv", "etc", "tv")):
                r["win"][str(win)] = {k: round(v, 1) for k, v in vals.items()}
    return P


def html_D(asof):
    s = open(os.path.join(REPORTS, asof, "numbers.html"), encoding="utf-8").read()
    i = s.index("const D = ") + len("const D = ")
    D, _ = json.JSONDecoder().raw_decode(s[i:])
    return D


def one(asof):
    with open(os.path.join(REPORTS, asof, "payload.json"), encoding="utf-8") as f:
        P = json.load(f)
    P = fix_old_names(P)
    num = sn.build(P)
    with open(os.path.join(HERE, "slices_pub", f"{asof}.num.pkl"), "wb") as f:
        pickle.dump(slim(num), f)
    with open(os.path.join(HERE, "slices_html", f"{asof}.num.pkl"), "wb") as f:
        pickle.dump(slim(html_D(asof)), f)
    return asof


if __name__ == "__main__":
    for d in ("slices_pub", "slices_html"):
        os.makedirs(os.path.join(HERE, d), exist_ok=True)
    asofs = sorted(d for d in os.listdir(REPORTS) if d[:2] == "20" and len(d) == 10 and d <= LAST_ASOF)
    with Pool(8) as pool:
        for a in pool.imap_unordered(one, asofs):
            print("done", a, flush=True)
