"""Q1 데이터 — 과거 게시본 불일치 셀을 원인별로 나눈다(T 잠정치·재수집 창·주식수 정정·미해명). diff.py 결과(out/bad_*.json)를 읽는다."""

import collections
import json
import os

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir, reports_dir  # noqa: E402

OUT = out_dir(__file__)
R = reports_dir()
P = lambda f: os.path.join(OUT, f)  # noqa: E731
out = {}
for d in ["2026-09-15", "2026-10-15", "2026-11-02", "2026-11-06"]:
    D = json.load(open(R / d / "payload.json"))["dates"]
    if d == "2026-11-06":
        pm = json.load(open(P("bad_1106_v2.json")))
        px = pm
    else:
        pm = json.load(open(P(f"bad_{d}_pm.json")))
        px = json.load(open(P(f"bad_{d}_pmx.json")))

    def key(r):
        return (r[0], r[1], r[2], r[3], r[4])

    sx = {key(r): r for r in px}
    c = collections.Counter()
    ex = collections.defaultdict(list)
    for r in pm + [r for r in px if key(r) not in {key(x) for x in pm}]:
        k = key(r)
        dd = r[4] if r[0] != "cap_by_month" else max(x for x in D if x[:7] == r[3])
        if k not in sx:
            cat = "b1_shares_corrected_after_asof"
        elif r[4] == d or (r[0] == "cap_by_month" and r[3] == d[:7]):
            cat = "a1_dayT_provisional"
        elif dd in D and len(D) - 1 - D.index(dd) <= 15:
            cat = "a2_recollection_window"
        else:
            cat = "b2_residual"
        c[cat] += 1
        ex[cat].append(r)
    worst = {k: sorted(v, key=lambda r: -abs(r[7]) if r[0] != "ret" else -abs(r[7]) * 1e3)[:3] for k, v in ex.items()}
    print(d, dict(c))
    for k in ex:
        if k != "b1_shares_corrected_after_asof":
            print(
                "  ",
                k,
                collections.Counter(
                    r[0] + (":" + r[3] if r[0] in ("flows", "detail") else "") for r in ex[k]
                ).most_common(6),
            )
    out[d] = {"counts": dict(c), "worst": worst}
json.dump(out, open(P("classification.json"), "w"), ensure_ascii=False, indent=1, default=str)
