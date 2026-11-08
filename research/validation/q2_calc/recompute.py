"""Q2: payload.json 에서 README §7 · docs/kqflow-formulas.pdf 정의만으로 numbers.html 표를 다시 계산해 비교.

sector_numbers.py 를 보지 않고 쓴 독립 구현. 문서가 정하지 않은 자리는 VARIANT 로 갈라 둔다.
usage: python recompute.py [key=value ...]      (기본 변형 = 문서 문언. S_mode=pass 등은 아래 V)
       python recompute.py fragility            (얇은 섹터 포함/제외 k, 블록별 G 통과 수)
입력: <reports>/<기준일>/payload.json · numbers.html (KR_QUANT_REPORTS). 문서 숫자는 2026-11-08 새벽 당시 게시본
(11-06 은 18:10 잠정 게시본 — 그 뒤 확정 모드로 다시 만들어 지금 reports 의 11-06 은 다른 판이다).
"""
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import out_dir, reports_dir  # noqa: E402

REPORTS = str(reports_dir())
DATES = ["2026-11-06", "2026-10-15", "2026-09-15"]
WINDOWS = [5, 20, 60, 120]
ACTORS = ["inst", "forgn", "indiv", "etc"]
MIN_NAMES = 10

# 문서가 비워 둔 자리의 선택지
V = dict(
    ols_thin=True,          # (17) U 에 thin 섹터를 넣는가 — 문서: 조건에 없음 → 넣는다
    piece_cap="block",      # (23)(25) 조각 α 의 분모: block C_s (문서 §1.4 "기간말") | month (cap_by_month 조각말 월)
    piece_split="last_rest",  # (23) 셋째 조각: i1 까지(last_rest) | 정확히 Δ(exact)
    rank_ties="average",    # (28) 동률 처리
    S_mode="doc",           # (27) 순위 모집단: doc(ẍ·α 있음·비thin) | pass(통과 섹터만 — 게시값 역추정 가설)
)
for a in sys.argv[1:]:
    if "=" not in a:
        continue
    k, v = a.split("=")
    V[k] = {"True": True, "False": False}.get(v, v)


def load(d):
    p = json.load(open(f"{REPORTS}/{d}/payload.json"))
    h = open(f"{REPORTS}/{d}/numbers.html").read()
    D = json.loads(re.search(r"const D = (\{.*?\});\n", h, re.S).group(1))
    return p, D


def geo(rs):
    """(16) 기하누적. rs: None 이면 기여 0. 전부 None 이면 None."""
    if all(r is None for r in rs):
        return None
    prod = 1.0
    for r in rs:
        if r is not None:
            prod *= 1 + r / 100
    return (prod - 1) * 100


class Calc:
    def __init__(self, p):
        self.p = p
        self.N = len(p["dates"])
        self.markets = p["markets"]
        self.sectors = p["sectors"]

    def mk(self, M):
        return self.markets if M == "전체" else [M]

    def F(self, M, s, A, i0, i1):
        tot, seen = 0.0, False
        for m in self.mk(M):
            arr = self.p["flows"].get(m, {}).get(s, {}).get(A)
            if arr is None:
                continue
            seen = True
            tot += sum(v for v in arr[i0:i1 + 1] if v is not None)
        return tot if seen else 0.0

    def cap(self, M, s):
        vals = [self.p["cap"][m][s] for m in self.mk(M) if s in self.p["cap"].get(m, {})]
        return sum(vals) if vals else None

    def cap_month(self, M, s, i):
        mon = self.p["dates"][i][:7]
        vals = [self.p["cap_by_month"][m][s].get(mon) for m in self.mk(M)
                if s in self.p["cap_by_month"].get(m, {})]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else None

    def rbar(self, M, s, i):
        num = den = 0.0
        for m in self.mk(M):
            r = self.p["ret"].get(m, {}).get(s)
            w = self.p["retw"].get(m, {}).get(s)
            if r is None or w is None or r[i] is None or w[i] is None:
                continue
            num += r[i] * w[i]
            den += w[i]
        return num / den if den > 0 else None

    def dv(self, M, s, i0, i1):
        return geo([self.rbar(M, s, i) for i in range(i0, i1 + 1)])

    def alpha(self, M, s, i0, i1, C):
        if C is None or C == 0:
            return None
        return self.F(M, s, "inst", i0, i1) / C * 100

    def n_names(self, M, s, W):
        mk = set(self.mk(M))
        return sum(1 for v in self.p["names"].values()
                   if v["sector"] == s and v["market"] in mk and v["win"].get(str(W)) is not None)

    def piece_C(self, M, s, i_end, Cblk):
        return Cblk if V["piece_cap"] == "block" else self.cap_month(M, s, i_end)

    def block(self, W, M):
        N = self.N
        i0, i1 = N - W, N - 1
        rows = {}
        for s in self.sectors:
            r = {"sector": s}
            for A in ACTORS + ["tv"]:
                r[A] = self.F(M, s, A, i0, i1)
            C = self.cap(M, s)
            r["cap"] = C
            r["accel"] = self.alpha(M, s, i0, i1, C)
            r["ret"] = self.dv(M, s, i0, i1)
            r["n_all"] = self.n_names(M, s, W)
            r["thin"] = r["n_all"] < MIN_NAMES
            rows[s] = r
        U = [s for s, r in rows.items() if r["ret"] is not None and r["cap"] not in (None, 0)
             and (V["ols_thin"] or not r["thin"])]
        a = np.array([rows[s]["accel"] for s in U])
        y = np.array([rows[s]["ret"] for s in U])
        n = len(U)
        if n <= 2:
            k = b = t = 0.0
        else:
            am, ym = a.mean(), y.mean()
            sxx = ((a - am) ** 2).sum()
            k = ((a - am) * (y - ym)).sum() / sxx
            b = ym - k * am
            sse = ((y - (k * a + b)) ** 2).sum()
            se = math.sqrt(sse / ((n - 2) * sxx))
            t = k / se if se > 0 else 0.0
        mid = i0 + (i1 - i0) // 2
        nn = i1 - i0 + 1
        Dl = nn // 3
        for s, r in rows.items():
            ok = r["ret"] is not None and r["cap"] not in (None, 0)
            r["exp"] = k * r["accel"] + b if ok else None
            r["x"] = r["exp"] - r["ret"] if ok else None
            r["U"] = 0.5 * k * r["x"] ** 2 if (ok and k > 0) else None
            # (25)(26) dW/dt
            r["P"] = None
            if mid > i0 and r["cap"] not in (None, 0):
                a1 = self.alpha(M, s, i0, mid, self.piece_C(M, s, mid, r["cap"]))
                a2 = self.alpha(M, s, mid + 1, i1, self.piece_C(M, s, i1, r["cap"]))
                v1, v2 = self.dv(M, s, i0, mid), self.dv(M, s, mid + 1, i1)
                if None not in (a1, a2, v1, v2):
                    r["P"] = (a2 * v2 - a1 * v1) / max(1, i1 - mid)
            # (23)(24)
            r["xdot"] = r["xddot"] = None
            if nn >= 9 and k != 0 and ok:
                xs = []
                for pidx in range(3):
                    s0 = i0 + pidx * Dl
                    s1 = (i1 if (pidx == 2 and V["piece_split"] == "last_rest") else s0 + Dl - 1)
                    ap = self.alpha(M, s, s0, s1, self.piece_C(M, s, s1, r["cap"]))
                    vp = self.dv(M, s, s0, s1)
                    if ap is None or vp is None:
                        xs = None
                        break
                    xs.append(k * ap + b - vp)
                if xs:
                    r["xdot"] = -(xs[2] - xs[0]) / (2 * Dl)
                    r["xddot"] = -(xs[2] - 2 * xs[1] + xs[0]) / Dl ** 2
        # (27)(28)(29)
        S = [s for s, r in rows.items() if r["xddot"] is not None and r["accel"] is not None and not r["thin"]]
        for r in rows.values():
            r["G"], r["G_pass"] = None, False
        if V["S_mode"] == "pass":
            S = [s for s in S if rows[s]["accel"] > 0 and rows[s]["x"] > 0 and rows[s]["xddot"] > 0]
        if len(S) > 1:
            import pandas as pd
            ranks = {}
            for key in ("accel", "x", "xddot"):
                ser = pd.Series({s: rows[s][key] for s in S})
                ranks[key] = (ser.rank(method=V["rank_ties"]) - 1) / (len(S) - 1)
            for s in S:
                r = rows[s]
                r["G"] = float(sum(ranks[kk][s] for kk in ranks) / 3)
                r["G_pass"] = bool(r["accel"] > 0 and r["x"] > 0 and r["xddot"] > 0)
        return dict(k=k, b=b, t=t, n_ols=n, rows=rows,
                    **{"from": self.p["dates"][i0], "to": self.p["dates"][i1]})

    def combined(self, blocks, M):
        Ws = [W for W in WINDOWS if f"{W}|{M}" in blocks
              and any(r["G"] is not None for r in blocks[f"{W}|{M}"]["rows"].values())]
        if len(Ws) < 2:
            return None
        out = {}
        for s in self.sectors:
            per = {W: blocks[f"{W}|{M}"]["rows"][s]["G"] for W in Ws}
            per = {W: g for W, g in per.items() if g is not None}
            out[s] = dict(per=per, G=(sum(per.values()) / len(per) if per else None),
                          pass_n=sum(blocks[f"{W}|{M}"]["rows"][s]["G_pass"] for W in per))
        return dict(windows=Ws, rows=out)


# 표시 자릿수(numbers.html 의 렌더: A=정수, P=2, U 2, P/xdot/xddot 3, G 2, tv/cap 정수)
DEC = dict(inst=0, forgn=0, indiv=0, etc=0, tv=0, cap=0, accel=2, ret=2, exp=2, x=2, U=2,
           P=3, xdot=3, xddot=3, G=2, n_all=0)


def cmp(mine, pub, dec):
    if mine is None or pub is None:
        return mine is None and pub is None, None
    d = abs(mine - pub)
    return d <= 0.5 * 10 ** -dec + 1e-9, d


def main():
    tag = ",".join(f"{k}={v}" for k, v in V.items())
    print("VARIANT", tag)
    allstats = defaultdict(lambda: [0, 0])
    mism = []
    kbt = []
    for d in DATES:
        p, D = load(d)
        c = Calc(p)
        blocks = {}
        for W in WINDOWS:
            for M in ["전체"] + c.markets:
                blocks[f"{W}|{M}"] = c.block(W, M)
        for key, pub in D["blocks"].items():
            mine = blocks[key]
            for f in ("k", "b", "t"):
                ok = abs(mine[f] - pub[f]) <= 1e-3 + 0.5 * 10 ** -(2 if f == "t" else 3)
                # 게시값은 k·b 3자리, t 2자리 반올림 → 반올림 폭 + 1e-3
                kbt.append((d, key, f, mine[f], pub[f], ok, mine["n_ols"]))
                allstats[f][0] += ok
                allstats[f][1] += 1
                if not ok:
                    mism.append((d, key, "-", f, mine[f], pub[f]))
            for f in ("from", "to"):
                ok = mine[f] == pub[f]
                allstats[f][0] += ok
                allstats[f][1] += 1
            for pr in pub["rows"]:
                mr = mine["rows"][pr["sector"]]
                for f, dec in DEC.items():
                    pv = pr.get(f)
                    mv = mr.get(f)
                    if f == "cap" and mv is None:
                        mv = 0.0 if pv == 0 else None
                    ok, dd = cmp(mv, pv, dec)
                    allstats[f][0] += ok
                    allstats[f][1] += 1
                    if not ok:
                        mism.append((d, key, pr["sector"], f, mv, pv))
                for f in ("thin", "G_pass"):
                    ok = bool(mr[f]) == bool(pr.get(f))
                    allstats[f][0] += ok
                    allstats[f][1] += 1
                    if not ok:
                        mism.append((d, key, pr["sector"], f, mr[f], pr.get(f)))
        for M in ["전체"] + c.markets:
            mc = c.combined(blocks, M)
            pc = (D.get("combined") or {}).get(M)
            ok = (mc is None) == (pc is None) and (mc is None or mc["windows"] == pc["windows"])
            allstats["comb.windows"][0] += ok
            allstats["comb.windows"][1] += 1
            if not ok:
                mism.append((d, "comb|" + M, "-", "windows", mc and mc["windows"], pc and pc["windows"]))
            if mc is None or pc is None:
                continue
            for pr in pc["rows"]:
                mr = mc["rows"][pr["sector"]]
                ok, _ = cmp(mr["G"], pr["G"], 2)
                allstats["comb.G"][0] += ok
                allstats["comb.G"][1] += 1
                if not ok:
                    mism.append((d, "comb|" + M, pr["sector"], "G", mr["G"], pr["G"]))
                okp = mr["pass_n"] == pr["pass_n"]
                allstats["comb.pass_n"][0] += okp
                allstats["comb.pass_n"][1] += 1
                if not okp:
                    mism.append((d, "comb|" + M, pr["sector"], "pass_n", mr["pass_n"], pr["pass_n"]))
                pp = {int(k): v for k, v in pr["per"].items()}
                for W in set(pp) | set(mr["per"]):
                    ok, _ = cmp(mr["per"].get(W), pp.get(W), 2)
                    allstats["comb.per"][0] += ok
                    allstats["comb.per"][1] += 1
                    if not ok:
                        mism.append((d, "comb|" + M, pr["sector"], f"per{W}", mr["per"].get(W), pp.get(W)))
    print("\n== match rate ==")
    tot_ok = tot_n = 0
    for f, (ok, n) in allstats.items():
        tot_ok += ok
        tot_n += n
        print(f"{f:12s} {ok:5d}/{n:5d} {100 * ok / n:6.2f}%")
    print(f"{'ALL':12s} {tot_ok:5d}/{tot_n:5d} {100 * tot_ok / tot_n:6.2f}%")
    print("\n== k/b/t ==")
    for row in kbt:
        if not row[5]:
            print("  MISS", row)
    json.dump([list(map(str, m)) for m in mism], open(OUT + f"/mism_{V['S_mode']}_{V['rank_ties']}.json", "w"),
              ensure_ascii=False, indent=0)
    print(f"\n== mismatches: {len(mism)} ==")
    by = defaultdict(int)
    for m in mism:
        by[(m[0], m[3])] += 1
    for k_, v in sorted(by.items()):
        print("  ", k_, v)
    return mism


OUT = str(out_dir(__file__))


def fragility():
    """비교용 기술 통계: thin 포함/제외 k, 블록별 통과 수(G 모집단 크기)."""
    for d in DATES:
        p, _ = load(d)
        c = Calc(p)
        for W in WINDOWS:
            for M in ["전체"] + c.markets:
                V["ols_thin"] = True
                b1 = c.block(W, M)
                V["ols_thin"] = False
                b2 = c.block(W, M)
                V["ols_thin"] = True
                npass = sum(r["G"] is not None for r in b1["rows"].values())
                print(f"{d} {W:>3}|{M}: k={b1['k']:+.3f} t={b1['t']:+.2f} n={b1['n_ols']} | k(no thin)={b2['k']:+.3f} t={b2['t']:+.2f} n={b2['n_ols']} | G scored={npass}")


if __name__ == "__main__":
    if "fragility" in sys.argv[1:]:
        fragility()
    else:
        mism = main()
        for m in mism[:60]:
            print(m)
