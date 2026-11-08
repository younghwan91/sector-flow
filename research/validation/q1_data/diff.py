"""Q1 데이터 — 게시 payload 와 recompute.py 결과를 셀마다 비교. usage: diff.py <게시 payload> <mine.json> <bad.json>
(상대 경로의 mine·bad 는 out/ 기준)"""

import collections
import json
import os

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir  # noqa: E402

OUT = out_dir(__file__)
rel = lambda p: p if os.path.isabs(p) else os.path.join(OUT, p)  # noqa: E731
pub = json.load(open(sys.argv[1]))
me = json.load(open(rel(sys.argv[2])))
TOL = {"amt": 0.1 + 1e-9, "ret": 0.001 + 1e-9}
assert pub["dates"] == me["dates"], "dates differ"
D = pub["dates"]
cells = []  # (field, m, s, k, day, pub, mine)


def add(field, m, s, k, i, pv, mv):
    cells.append((field, m, s, k, i, pv, mv))


for m in pub["markets"]:
    for s in pub["sectors"]:
        for fld in ["flows", "detail"]:
            P = pub[fld][m].get(s)
            M = me[fld][m].get(s)
            if P is None and M is None:
                continue
            keys = set((P or {}).keys()) | set((M or {}).keys())
            for k in keys:
                pa = (P or {}).get(k) or [0] * len(D)
                ma = (M or {}).get(k) or [0] * len(D)
                for i in range(len(D)):
                    add(fld, m, s, k, i, pa[i], ma[i])
        for fld in ["ret", "retw"]:
            pa = pub[fld][m].get(s)
            ma = me[fld][m].get(s)
            pa = pa or [0] * len(D)
            ma = ma or [0] * len(D)
            for i in range(len(D)):
                add(fld, m, s, "", i, pa[i], ma[i])
        pc = pub["cap"][m].get(s)
        mc = me["cap"][m].get(s)
        if pc is not None or mc is not None:
            add("cap", m, s, "", len(D) - 1, pc, mc)
        pm = pub["cap_by_month"][m].get(s, {})
        mm = me["cap_by_month"][m].get(s, {})
        for mo in sorted(set(pm) | set(mm)):
            add("cap_by_month", m, s, mo, -1, pm.get(mo), mm.get(mo))


def tol(f):
    return TOL["ret"] if f == "ret" else TOL["amt"]


stat = collections.defaultdict(lambda: [0, 0])
bad = []
for f, m, s, k, i, pv, mv in cells:
    key = f if f not in ("flows", "detail") else f + ":" + k
    pv0 = 0 if pv is None else pv
    mv0 = 0 if mv is None else mv
    okk = abs(pv0 - mv0) <= tol(f) and ((pv is None) == (mv is None) or abs(pv0) < 1e-12 and abs(mv0) < 1e-12)
    stat[key][0] += 1
    stat[key][1] += okk
    if not okk:
        bad.append((f, m, s, k, D[i] if i >= 0 else k, pv, mv, pv0 - mv0))
tot = sum(v[0] for v in stat.values())
ok = sum(v[1] for v in stat.values())
print(f"TOTAL {ok}/{tot} = {100 * ok / tot:.4f}%")
for k, (n, o) in sorted(stat.items()):
    print(f"  {k:16s} {o}/{n} {100 * o / n:.3f}%")
core = [k for k in stat if k in ("flows:inst", "flows:forgn", "flows:indiv", "flows:etc", "flows:tv", "cap", "ret")]
n = sum(stat[k][0] for k in core)
o = sum(stat[k][1] for k in core)
print(f"CORE(4actor+tv+cap+ret) {o}/{n} = {100 * o / n:.4f}%")
bydate = collections.Counter(b[4] for b in bad)
print("bad by date", bydate.most_common(12))
bad.sort(key=lambda b: -abs(b[7]))
for b in bad[:25]:
    print(b)
json.dump(bad, open(rel(sys.argv[3]), "w"), ensure_ascii=False, default=str)
