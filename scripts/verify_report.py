#!/usr/bin/env python
"""일일 리포트 검증 — 수식·데이터·계산을 한 번에 점검한다.

층을 나눠서 본다. 층마다 실패 원인이 다르고 대응도 다르기 때문이다.

  A. 수식   — 표에 실린 값들이 정의된 항등식을 만족하는가(전정밀도 재적합 기준)
  A3. 파생  — 구간 수익률·ẋ·ẍ·1년%ile·dW/dt 를 **페이로드 일별 배열에서 따로** 다시 셈하는가
  B. 데이터 — DB 에서 들어온 것이 온전한가(기준일·커버리지·항등식·중복·단위)
  C. 계산   — 표의 구조(열 정의 = 셀 개수)와 값이 정합한가
  D. 부류   — 계산에 들어가는 두 값이 같은 집합·시점·파라미터에서 나오는가
  E. 화면   — **그려진 글자**가 열 정의가 내는 값과 같은가(폭 80·120·200)
  F. 독립   — **DB 원자료에서 처음부터 다시 만든 값**과 페이로드가 같은가
  G. 원장   — ``sw-ledger`` 화면의 값·그림, 그리고 flow 와 같은 수를 말하는가
  H. 화면·키 — **듣는 키와 화면에 적힌 키**가 같은가(값이 아니라 조작을 본다)
  T. 오늘   — 1일 값·1년 백분위·시장 합계·직전 리포트 대비를 일별 배열에서 다시 센다
  W. 웹     — numbers.html·viewer.html 을 node 로 **그려** 칸의 글자를 페이로드·TUI 와 맞춘다

A~D 는 전부 *파일 안의 값끼리* 정합한지만 본다. 그래서 producer 가 임펄스를
통째로 잘못 계산해도(수량을 금액으로 착각, `flu_rt` 의 bp 단위를 놓침 — 둘 다
이 저장소에서 실제로 사고를 냈다) 전부 초록이다. 파생층이 그 틀린 값 위에서
일관될 뿐이기 때문이다. **F 가 그 구멍을 막는다** — `scripts/` 의 함수를 하나도
부르지 않고 SQL 로 다시 집계한다. 같은 버그를 두 번 통과시키지 않으려면 경로가
달라야 한다. (E·F 는 두 번째 검증기를 만들지 않고 여기에 합쳤다 — 검증기가
둘이면 배치가 하나만 돌리고 나머지는 조용히 썩는다. F 만 DB 를 요구한다.)

⚠️ **반올림된 값으로 검산하지 않는다.** payload 의 k 는 소수 3자리라, 그걸로
U=½kx² 를 검산하면 1e-3 오차가 나서 멀쩡한 수식이 FAIL 로 뜬다(실제로 한 번
밟았다). 원자료에서 전정밀도로 다시 적합해 비교한다.

⚠️ **상대오차로 비교하지 않는다.** 섹터 합계가 0 근처인 칸(섬유/의류 20일
+2.0억)에서 상대오차는 무의미하게 커진다. 허용오차는 페이로드가 반올림하는
자릿수에서 유도한 **절대값**이다(`TOL_FLOW`·`TOL_CAP`·`TOL_RET`).

**수치 무결성과 문서 주장을 가른다.** 값이 틀린 것(FAIL)은 리포트를 VERIFY_FAILED 로 만든다.
도움말·한계 절이 인용하는 **실측 주장**(예: "개인·외국인·기관은 널보다 음수쌍이 드물다")이
오늘 데이터에서 어긋난 것은 CLAIM 이다 — 숫자에는 결함이 없고 글이 낡았다는 뜻이라 같은 폴더의
``CLAIMS.txt`` 에만 적는다. 예전엔 둘이 한 목록이라 10-13·10-29 리포트가 수치 결함 없이 빨갰다.

Run:  python scripts/verify_report.py --dir reports/2026-08-27
      python scripts/verify_report.py --dir <폴더> --db-check     # B·F 층까지
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import traceback
import warnings
from datetime import date

# pandas 는 psycopg2 연결을 "검증 안 된 DBAPI" 라며 read_sql 마다 UserWarning 을 낸다. 동작엔 문제가 없는데
# 크론 로그를 덮어 진짜 경고를 묻는다(2026-11 cron.log 에 1,491줄). 이 메시지만 끈다 — 크론의 전역 -W 는 다른 경고까지 끈다.
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy", category=UserWarning)

TOL_EXACT = 1e-9      # 항등식 — 기계정밀도
TOL_FIT = 1e-8        # 재적합 경유 — 누적 부동소수 오차 여유
FAILS: list[str] = []
CHECKS = 0


def chk(layer: str, name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] {layer} · {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILS.append(f"{layer}/{name}")


#: 문서 주장 어긋남 — 실패가 아니다(:func:`claim`).
CLAIMS: list[str] = []


def claim(layer: str, name: str, ok: bool, detail: str = "") -> None:
    """도움말·한계가 인용하는 **실측 주장**이 오늘 데이터에서도 참인가.

    값의 무결성이 아니라 글의 낡음을 본다. 어긋나도 ``FAILS`` 에 넣지 않고 ``CLAIMS`` 에
    적는다 — 그래야 VERIFY_FAILED 가 "숫자가 틀렸다" 하나만 뜻한다.
    """
    global CHECKS
    CHECKS += 1
    mark = "OK   " if ok else "CLAIM"
    print(f"  [{mark}] {layer} · {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        CLAIMS.append(f"{layer}/{name}" + (f"   {detail}" if detail else ""))


#: 경고 — 실패도 문서 주장도 아니다(:func:`warn`). VERIFY_FAILED 를 만들지 않는다.
WARNS: list[str] = []


def warn(layer: str, name: str, detail: str = "") -> None:
    """값은 맞지만 사람이 봐야 할 상태 — 예: 외부 참값 대조가 며칠째 SKIP 했다(:func:`_external_skip`).

    검사 수에 넣지 않고 종료코드를 바꾸지 않는다. VERIFY.txt 에 ``[WARN]`` 줄과 끝의 경고 요약으로 남고,
    ``freshness_check.py`` 가 전 거래일 VERIFY.txt 에서 그 줄을 찾아 알린다.
    """
    print(f"  [WARN] {layer} · {name}" + (f"   {detail}" if detail else ""))
    WARNS.append(f"{layer}/{name}")


def ols(xs, ys):
    """절편 포함 단순회귀 — (기울기, 절편, t, R²)."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((v - mx) ** 2 for v in xs)
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    k = sxy / sxx if sxx else 0.0
    b = my - k * mx
    res = [y - (k * x + b) for x, y in zip(xs, ys)]
    sse = sum(r * r for r in res)
    sst = sum((y - my) ** 2 for y in ys)
    se = math.sqrt(sse / (n - 2) / sxx) if sxx and n > 2 else float("nan")
    return k, b, (k / se if se else float("nan")), (1 - sse / sst if sst else float("nan"))


# ─────────────────────────────────────────────── A. 수식

def layer_a(D: dict) -> None:
    print("\nA. 수식 — 표의 값이 정의된 항등식을 만족하는가")
    worst = {}

    def upd(key, v):
        worst[key] = max(worst.get(key, 0.0), abs(v))

    blocks = 0
    for key, B in D["blocks"].items():
        rows = [r for r in B["rows"] if r.get("exp") is not None]
        if len(rows) < 8:
            continue
        blocks += 1
        # 적합은 얇은 섹터를 뺀 표본으로 한다(`sector_numbers` 와 같은 규칙). 항등식은 전 행에서 본다.
        fit = [r for r in rows if not r.get("thin")]
        if len(fit) < 3:
            continue
        xs = [r["a_idx"] for r in fit]
        ys = [r["ret"] for r in fit]
        k, b, t_fit, _r2 = ols(xs, ys)              # 전정밀도 재적합

        upd("a = 임펄스/시총×100", max(r["a_idx"] - r["inst"] / r["cap_idx"] * 100 for r in rows))
        upd("예상Δv = k·a + b", max(r["exp"] - (k * r["a_idx"] + b) for r in rows))
        upd("x = 예상 − 실제", max(r["x"] - (r["exp"] - r["ret"]) for r in rows))
        upd("x = −(OLS 잔차)", max(r["x"] + (r["ret"] - (k * r["a_idx"] + b)) for r in rows))
        # U 는 **k>0 인 블록에만** 실린다(k≤0 이면 |x| 의 순감소 변환이라 뜻을
        # 잃는다 — `layer_a2` 가 그 규약을 따로 검사한다). 여기서는 실린 것만 본다.
        if all(r["U"] is not None for r in rows):
            upd("U = ½·k·x²", max(r["U"] - 0.5 * k * r["x"] ** 2 for r in rows))
        res = [y - (k * x + b) for x, y in zip(xs, ys)]
        upd("OLS: Σ잔차 = 0", sum(res) / len(res))
        upd("OLS: 잔차 ⊥ a", sum(r * x for r, x in zip(res, xs)) / max(1.0, sum(abs(v) for v in xs)))
        upd("k 반올림 편차(표시용)", k - B["k"])
        upd("b 반올림 편차(표시용)", b - B["b"])
        # t 는 화면 머리에 `k=… (t=…)` 로 실려 k 를 얼마나 믿을지 말한다. 예전엔 재적합의 t 를
        # 버리고(`_t`) 표의 t 를 안 봐서, t 가 틀려도 아무 검사도 안 빨개졌다.
        tb = B.get("t")
        if isinstance(tb, (int, float)) and math.isfinite(tb) and math.isfinite(t_fit):
            upd("t 반올림 편차(표시용)", t_fit - tb)
        elif (isinstance(tb, (int, float)) and math.isfinite(tb)) != math.isfinite(t_fit):
            chk("A", f"{key} t 결측 불일치", False, f"재적합 {t_fit} vs 표 {tb}")

        # U 의 부호 규약: k>0 이면 U≥0
        if k > 0 and any(r["U"] is not None and r["U"] < -TOL_EXACT for r in rows):
            chk("A", f"{key} k>0 인데 U<0", False)

        # G 는 0~1 순위평균, 통과는 세 부호의 논리곱
        for r in rows:
            if r.get("G") is not None and not (-TOL_EXACT <= r["G"] <= 1 + TOL_EXACT):
                chk("A", f"{key} G 범위 이탈", False, f"{r['sector']} G={r['G']}")
            if r.get("G") is not None:
                want = bool(r["a_idx"] > 0 and r["x"] > 0 and r["xddot"] > 0) if r.get("xddot") is not None else False
                if bool(r.get("G_pass")) != want:
                    chk("A", f"{key} G_pass 불일치", False, r["sector"])
        # 얇은 섹터는 G 를 받지 않아야
        for r in B["rows"]:
            if r.get("thin") and r.get("G") is not None:
                chk("A", f"{key} 얇은 섹터에 G 부여", False, r["sector"])

    for name, v in worst.items():
        # 반올림 폭의 절반 — k·b 는 소수 3자리, t 는 2자리로 싣는다.
        tol = (5e-3 + 1e-9 if name.startswith("t ") else 5e-4 + 1e-9 if "반올림" in name
               else TOL_FIT)
        chk("A", name, v < tol, f"최대오차 {v:.2e}")
    chk("A", "검사한 블록 수", blocks >= 8, f"{blocks}개")

    # 중앙차분 공식이 해석적으로 맞는가 — x=t² 에서 ẋ=2t, ẍ=2
    f = [0.0, 1.0, 4.0]
    chk("A", "1차 중앙차분 (x=t², t=1 → 2)", abs((f[2] - f[0]) / 2 - 2) < TOL_EXACT)
    chk("A", "2차 중앙차분 (x=t² → 2)", abs((f[2] - 2 * f[1] + f[0]) - 2) < TOL_EXACT)

    layer_a2(D)


def _spearman(a: list, b: list) -> float:
    def rk(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for t in range(i, j + 1):
                r[order[t]] = (i + j) / 2.0
            i = j + 1
        return r
    ra, rb = rk(a), rk(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db_ = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return num / (da * db_) if da and db_ else float("nan")


def layer_a2(D: dict) -> None:
    """A 층 보강 — **원 검사가 안 보던 열들.**

    A 층은 오래도록 `a·exp·x·U` 네 개의 항등식만 봤다. 그건 회귀 한 줄에서
    파생되는 값들이라 서로 맞을 수밖에 없고, 실제로 화면에서 가장 많은 칸을
    차지하는 `1년[%ile]`·`추이[8]`·`풀림`·`G`·`종합 G` 는 **한 번도 재계산되지
    않았다.** 여기서 그 빈 곳을 메운다.
    """
    worst = {}

    def upd(key, v):
        worst[key] = max(worst.get(key, 0.0), abs(v))

    # ① 포텐셜의 **부호 규약** — k ≤ 0 인 블록에서는 U 가 없어야 한다.
    #
    # U = ½kx² 의 뜻은 "|미실현| 이 클수록 크다" 하나인데, 그건 k>0 에서만
    # 성립한다. k<0 이면 |x| 의 순**감소** 변환이라 화면이 "포텐셜 큰 순" 이라
    # 말하면서 정확히 반대로 줄세운다(실측: 5일|코스닥 k=−0.587, 22개 섹터
    # 전부 U<0, ρ(|x|,U) = −1.0000). 도움말이 "4개 구간 전부 Spearman 1.0000"
    # 이라 적었던 것은 **시장=전체에서만 잰 값**이었다 — 실측 주장이 실측
    # 범위를 넘어 일반화된 자리다.
    bad_sign, bad_rho = [], []
    for key, B in D["blocks"].items():
        rows = [r for r in B["rows"] if r.get("x") is not None]
        if len(rows) < 3:
            continue
        us = [r.get("U") for r in rows]
        # k 는 표시용 반올림값이므로 부호 판정에만 쓴다(0 근처는 아래 ρ 로 잡힌다).
        if B["k"] <= 0:
            if any(u is not None for u in us):
                bad_sign.append(f"{key} k={B['k']} 인데 U 가 있다")
            continue
        if any(u is None for u in us):
            bad_sign.append(f"{key} k={B['k']}>0 인데 U 가 없다")
            continue
        if any(u < -TOL_EXACT for u in us):
            bad_sign.append(f"{key} k>0 인데 U<0")
        rho = _spearman([abs(r["x"]) for r in rows], us)
        if not (rho > 0.9999):
            bad_rho.append(f"{key} ρ(|x|,U)={rho:+.4f}")
    chk("A", "포텐셜: k≤0 인 블록에는 U 가 없다", not bad_sign, "; ".join(bad_sign[:3]))
    chk("A", "포텐셜: U 가 있는 블록은 |미실현| 과 Spearman +1",
        not bad_rho, "; ".join(bad_rho[:3]))

    # ② Σ미실현 = 0 — OLS 잔차의 부호 반전이므로 **적합에 들어간 행**(얇은 섹터 제외)에서
    #    블록마다 정확히 0 이어야 한다. 얇은 섹터는 직선으로 x 를 받지만 적합에는 안 들어간다.
    bad = []
    for key, B in D["blocks"].items():
        xs = [r["x"] for r in B["rows"] if r.get("x") is not None and not r.get("thin")]
        if not xs:
            continue
        scale = max(abs(v) for v in xs) * len(xs)
        upd("Σ미실현 = 0", sum(xs) / max(scale, 1e-12))
        if abs(sum(xs)) > 1e-9 * max(scale, 1.0):
            bad.append(f"{key} Σx={sum(xs):.3e}")
    chk("A", "Σ미실현 = 0 (전 블록, 적합 표본)", not bad, "; ".join(bad[:3]))

    # ③ 1년 백분위 — 정의역 [0,100] 이고 **주체마다 다른 값**이어야 한다.
    #    (한 행에 두 주체의 숫자가 섞이는 사고를 이 저장소가 이미 냈다.)
    bad, same = [], 0
    for key, B in D["blocks"].items():
        for r in B["rows"]:
            p = r.get("pct1y") or {}
            for a, v in p.items():
                if not (0.0 <= v <= 100.0):
                    bad.append(f"{key}/{r['sector']}/{a}={v}")
            if len(set(p.values())) == 1 and len(p) > 1:
                same += 1
    chk("A", "1년 백분위 ∈ [0,100]", not bad, "; ".join(bad[:3]))
    chk("A", "1년 백분위가 주체마다 갈리는가", same < 0.5 * 27 * len(D["blocks"]),
        f"4주체가 모두 같은 행 {same}개")

    # ④ 추이[8] — **조각 합이 그 주체의 임펄스와 같다**는 약속. 스파크라인의
    #    오른쪽 끝이 임펄스 열의 숫자로 이어진다는 말이 여기서 나온다.
    bad = []
    for key, B in D["blocks"].items():
        win = int(key.split("|")[0])
        segs = min(8, win)
        for r in B["rows"]:
            for a, arr in (r.get("spark") or {}).items():
                if len(arr) != segs:
                    bad.append(f"{key}/{r['sector']}/{a} 조각 {len(arr)} != {segs}")
                    continue
                # 조각은 0.1억으로 반올림된다 — 허용오차는 거기서 나온다.
                d = abs(sum(arr) - (r.get(a) or 0.0))
                upd("추이 Σ조각 − 임펄스 [억]", d)
                if d > 0.05 * segs + 1e-6:
                    bad.append(f"{key}/{r['sector']}/{a} Σ={sum(arr):.2f} vs {r.get(a)}")
    chk("A", "추이[8]: 조각 수 = min(8,창) · Σ조각 = 임펄스", not bad, "; ".join(bad[:3]))

    # ⑤ G — 세 순위의 평균이고, 각 순위는 pos/(N−1) 이다. 값이 [0,1] 인지만
    #    보던 것을 **순위 자체를 다시 매겨** 대조한다.
    bad, gate_bad = [], []
    for key, B in D["blocks"].items():
        scored = [r for r in B["rows"] if r.get("G") is not None]
        # 관문을 다 통과한 섹터 = G 가 있는 섹터. 통과가 하나뿐이면 G 를 안 줘서 ● 가 사라지던
        # 부류를 잡는다(2026-10-15 60|전체 의료/정밀기기).
        passed = {r["sector"] for r in B["rows"]
                  if not r.get("thin") and r.get("xddot") is not None and r.get("a_idx") is not None
                  and r["a_idx"] > 0 and (r.get("x") or 0) > 0 and r["xddot"] > 0}
        if passed != {r["sector"] for r in scored}:
            gate_bad.append(f"{key} 통과 {sorted(passed)} vs G {sorted(r['sector'] for r in scored)}")
        if len(scored) == 1:
            if abs(scored[0]["G"] - 0.5) > TOL_FIT:
                bad.append(f"{key}/{scored[0]['sector']} 단독 통과 G={scored[0]['G']} (0.5 여야)")
            continue
        if len(scored) < 2:
            continue
        def _rank(k2):
            """pos/(N−1) — **동률은 중간순위**(producer 와 같은 규약). 위치 순위로 두면 값이 같은
            두 섹터가 입력 순서에 따라 다른 G 를 받는데도 검사가 그걸 정답으로 삼는다."""
            vals = [r[k2] for r in scored]
            o = [0.0] * len(vals)
            for i, v in enumerate(vals):
                below = sum(1 for u in vals if u < v)
                ties = sum(1 for u in vals if u == v)
                o[i] = (below + (ties - 1) / 2.0) / (len(vals) - 1)
            return o
        ra, rx, rd = _rank("a_idx"), _rank("x"), _rank("xddot")
        for r, A, X, Dd in zip(scored, ra, rx, rd):
            d = abs(r["G"] - (A + X + Dd) / 3.0)
            upd("G = 세 순위 평균", d)
            if d > TOL_FIT:
                bad.append(f"{key}/{r['sector']} G={r['G']} vs {(A+X+Dd)/3:.6f}")
    chk("A", "G = rank(a)·rank(x)·rank(ẍ) 의 평균", not bad, "; ".join(bad[:3]))
    chk("A", "세 관문을 다 통과한 섹터 = G 가 있는 섹터", not gate_bad, "; ".join(gate_bad[:3]))

    # ⑥ 종합 G = 창별 G 의 **산술평균**, 통과[구간] = pass_n/seen.
    bad = []
    for mkey, C in (D.get("combined") or {}).items():
        wins = C.get("windows") or []
        for r in C["rows"]:
            per = {str(k2): v for k2, v in (r.get("per") or {}).items()}
            got = {}
            for w in wins:
                b = D["blocks"].get(f"{w}|{mkey}")
                row = next((q for q in (b or {}).get("rows", [])
                            if q["sector"] == r["sector"]), None)
                if row and row.get("G") is not None:
                    got[str(w)] = round(row["G"], 3)
            if per != got:
                bad.append(f"{mkey}/{r['sector']} per {per} vs {got}")
                continue
            want = sum(got.values()) / len(got) if got else None
            if (r.get("G") is None) != (want is None):
                bad.append(f"{mkey}/{r['sector']} G {r.get('G')} vs {want}")
            elif want is not None:
                upd("종합 G = 창별 G 평균", r["G"] - want)
                if abs(r["G"] - want) > TOL_FIT:
                    bad.append(f"{mkey}/{r['sector']} G {r['G']} vs {want}")
    chk("A", "종합 G = G 가 나온 창의 등가중 평균", not bad, "; ".join(bad[:3]))

    # ⑦ 종합 `*` = **판정된 모든 창에서 통과.** 판정된 창 = 그 창에서 얇지 않고 a·x·ẍ 가 다 있는
    #    창(seen), 통과 = G 가 나온 창(pass_n). 예전엔 seen 도 "G 가 나온 창" 을 세서 pass_n 과
    #    언제나 같았고, `*` 가 "한 창이라도 통과" 로 붙었다 — 도움말·README 의 뜻과 반대였다.
    bad, n_rows = [], 0
    for mkey, C in (D.get("combined") or {}).items():
        for r in C["rows"]:
            n_rows += 1
            seen = passed = 0
            for w in C.get("windows") or []:
                row = next((q for q in (D["blocks"].get(f"{w}|{mkey}") or {}).get("rows", [])
                            if q["sector"] == r["sector"]), None)
                if row is None:
                    continue
                if (not row.get("thin") and row.get("a_idx") is not None
                        and row.get("x") is not None and row.get("xddot") is not None):
                    seen += 1
                if row.get("G") is not None:
                    passed += 1
            want = seen > 0 and passed == seen
            if (r.get("seen"), r.get("pass_n"), bool(r.get("G_pass"))) != (seen, passed, want):
                bad.append(f"{mkey}/{r['sector']} seen·pass_n·* = {r.get('seen')}·"
                           f"{r.get('pass_n')}·{r.get('G_pass')} vs {seen}·{passed}·{want}")
    chk("A", "종합 * = 판정된 모든 창에서 통과 (seen = 판정 창, pass_n = G 창)", not bad,
        f"{n_rows}줄 · {len(bad)}건  " + "; ".join(bad[:3]))

    for name, v in worst.items():
        print(f"       · {name} 최대오차 {v:.2e}")


# ─────────────────────────────────────────────── A3. 파생 열 독립 재계산

#: ``sector_numbers.MIN_NAMES`` 와 같은 수 — 얇은 섹터(이 미만)는 k 적합에서 빠진다. import 하지
#: 않는다(그 모듈을 부르면 같은 버그를 두 번 통과시킨다). 값이 갈리면 아래 `얇은 섹터` 검사가 빨개진다.
A3_MIN_NAMES = 10
#: ``sector_numbers.PCT_LOOKBACK`` — 백분위의 되돌아보기 길이(거래일).
A3_PCT_LOOKBACK = 260
#: 같은 페이로드 입력에서 같은 정의로 다시 셈한 값의 차이 — 덧셈 순서만 다르다.
#: 값의 크기에 비례한 부동소수 오차라 ``A3_TOL_REL × (1 + |값|)`` 로 본다.
A3_TOL_REL = 1e-9


def layer_a3(D: dict, P: dict) -> None:
    """A3. **파생 열을 페이로드의 일별 배열에서 따로 다시 셈한다.**

    A 층은 회귀 한 줄의 항등식(a·exp·x·U)을, A2 는 G·종합 G·추이 조각의 합을 본다. 그런데
    화면이 가장 많이 쓰는 파생 값 — 구간 수익률(Δv), ẋ·ẍ, 1년[%ile], dW/dt — 은 **producer 가 낸
    값을 그대로 믿고** 그 위에서 정합만 봤다. ẍ 의 부호를 뒤집거나 수익률을 기하가 아니라 산술로
    누적해도 A·A2 는 초록이다(그 틀린 값 위에서 일관될 뿐이다).

    여기서는 ``sector_numbers`` 를 부르지 않고 ``payload.json`` 의 일별 배열(``flows``·``ret``·
    ``retw``·``cap``)에서 정의대로 다시 만든다. 허용오차는 반올림에서 나온다 — 행 값은 전정밀도라
    덧셈 순서 오차(:data:`A3_TOL_REL`)만, ``1년[%ile]`` 은 소수 1자리 반올림의 절반을 준다.
    F 층(DB → 페이로드)과 이 층(페이로드 → 표)을 이으면 DB 에서 표까지 한 줄로 검산된다.
    """
    print("\nA3. 파생 열 — 페이로드 일별 배열에서 다시 셈한 값과 같은가")
    dates = P.get("dates") or []
    N = len(dates)
    mk_all = list(P.get("markets") or [])
    acts = ("inst", "forgn", "indiv", "etc")

    def mkts_of(mk: str) -> list:
        return mk_all if mk == "전체" else [mk]

    def series(mkts, sec, key) -> list:
        """시장을 합친 일별 계열. 시장 순서대로 0.0 에 더한다(백분위의 동률이 비트 단위로 같아야)."""
        out = [0.0] * N
        for m in mkts:
            a = ((P["flows"].get(m) or {}).get(sec) or {}).get(key)
            if not a:
                continue
            for i in range(min(N, len(a))):
                out[i] += a[i] or 0.0
        return out

    def cap_of(mkts, sec) -> float:
        return sum((P["cap"].get(m) or {}).get(sec) or 0.0 for m in mkts)

    def comp_ret(mkts, sec, a, b):
        """[a,b] 의 섹터 수익률(%) — 그날 전일 시총 가중 평균을 **기하**로 잇는다. 하루도 없으면 None."""
        acc, seen = 1.0, False
        for i in range(a, b + 1):
            num = den = 0.0
            for m in mkts:
                rs = (P["ret"].get(m) or {}).get(sec)
                ws = (P["retw"].get(m) or {}).get(sec)
                if not rs or not ws:
                    continue
                r, w = rs[i], ws[i]
                if r is None or not w:
                    continue
                num += r * w
                den += w
                seen = True
            day = num / den if den else 0.0
            acc *= 1.0 + day / 100.0
        return (acc - 1.0) * 100.0 if seen else None

    def pct_now(ser, win):
        """롤링 ``win`` 일 합의 1년 분포에서 오늘 값의 백분위 — 동률은 중간순위."""
        n = min(len(ser), A3_PCT_LOOKBACK)
        if win > n:
            return None
        tail = ser[len(ser) - n:]
        pre = [0.0]
        for v in tail:
            pre.append(pre[-1] + v)
        sums = [pre[j + win] - pre[j] for j in range(n - win + 1)]
        if len(sums) < 2:
            return None
        cur = sums[-1]
        lt = sum(1 for v in sums if v < cur)
        eq = sum(1 for v in sums if v == cur)
        return 100.0 * (lt + (eq - 1) / 2.0) / (len(sums) - 1)

    names = D.get("names") or {}
    stats: dict = {}          # 검사 이름 → [비교 수, 최대오차, 불일치 목록]

    def cmp(name, label, got, want, tol):
        st = stats.setdefault(name, [0, 0.0, []])
        if (got is None) != (want is None):
            st[2].append(f"{label} 표 {got} vs 재계산 {want}")
            return
        if got is None:
            return
        st[0] += 1
        d = abs(got - want)
        if not math.isfinite(d):
            st[2].append(f"{label} 표 {got} vs 재계산 {want}")
            return
        st[1] = max(st[1], d)
        if d > tol(want):
            st[2].append(f"{label} 표 {got} vs 재계산 {want}")

    rel = lambda w: A3_TOL_REL * (1.0 + abs(w))          # noqa: E731
    half01 = lambda _w: 0.05 + 1e-9                      # noqa: E731 — 소수 1자리 반올림

    n_blocks = 0
    for key, B in (D.get("blocks") or {}).items():
        try:
            win, mk = key.split("|")
            win = int(win)
        except ValueError:
            continue
        if win > N or mk not in (["전체"] + mk_all):
            continue
        n_blocks += 1
        mkts = mkts_of(mk)
        i0, i1 = N - win, N - 1
        rows = B.get("rows") or []
        # ── 행 단위: 임펄스·시총·가속도·구간 수익률·백분위
        mine = {}
        for r in rows:
            sec = r["sector"]
            tag = f"{key}/{sec}"
            for a in acts + ("tv",):
                ser = series(mkts, sec, a)
                cmp("임펄스·거래대금 = 일별 합 (4주체·tv)", f"{tag}/{a}", r.get(a),
                    sum(ser[i0:i1 + 1]), rel)
            cap = cap_of(mkts, sec)
            cmp("시총 = 시장별 기간말 시총의 합", tag, r.get("cap"), cap, rel)
            inst = sum(series(mkts, sec, "inst")[i0:i1 + 1])
            cmp("가속도 = 임펄스/시총×100 (시총 없으면 결측)", tag, r.get("accel"),
                inst / cap * 100.0 if cap else None, rel)
            ret = comp_ret(mkts, sec, i0, i1)
            cmp("구간 수익률 = 일별 시총가중 수익률의 기하누적", tag, r.get("ret"), ret, rel)
            pcts = r.get("pct1y") or {}
            for a in acts:
                want = pct_now(series(mkts, sec, a), win)
                got = pcts.get(a)
                cmp("1년[%ile] = 롤링 합의 1년 분포에서 오늘의 중간순위", f"{tag}/{a}", got,
                    want, half01)
            # 얇은 섹터 판정 — 적합 표본을 정한다.
            n_all = sum(1 for nm in names.values()
                        if nm.get("sector") == sec and nm.get("market") in mkts
                        and (nm.get("win") or {}).get(str(win)))
            if bool(r.get("thin")) != (n_all < A3_MIN_NAMES):
                stats.setdefault("얇은 섹터 = 구간 거래 종목 10 미만", [0, 0.0, []])[2].append(
                    f"{tag} thin={r.get('thin')} 종목 {n_all}")
            stats.setdefault("얇은 섹터 = 구간 거래 종목 10 미만", [0, 0.0, []])[0] += 1
            mine[sec] = {"inst": inst, "cap": cap, "ret": ret, "thin": n_all < A3_MIN_NAMES}

        # ── 블록 적합(얇은 섹터 제외, 절편 포함 OLS)과 t
        fit = [v for v in mine.values() if v["ret"] is not None and v["cap"] and not v["thin"]]
        if len(fit) > 2:
            xs = [v["inst"] / v["cap"] * 100.0 for v in fit]
            ys = [v["ret"] for v in fit]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            sxx = sum((x - mx) ** 2 for x in xs)
            sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            k = sxy / sxx if sxx else 0.0
            b = my - k * mx
            sse = sum(((y - my) - k * (x - mx)) ** 2 for x, y in zip(xs, ys))
            se = math.sqrt(sse / (len(xs) - 2) / sxx) if sxx else float("nan")
            t = k / se if se else float("nan")
        else:
            k = b = t = 0.0
        tb = B.get("t")
        if isinstance(tb, (int, float)) and math.isfinite(tb) and math.isfinite(t):
            cmp("t = 블록 적합 기울기의 t (소수 2자리)", key, tb, t, lambda _w: 5e-3 + 1e-9)
        elif (isinstance(tb, (int, float)) and math.isfinite(tb)) != math.isfinite(t):
            stats.setdefault("t = 블록 적합 기울기의 t (소수 2자리)", [0, 0.0, []])[2].append(
                f"{key} 표 {tb} vs 재계산 {t}")

        # ── 행 단위: dW/dt · ẋ · ẍ (블록 k·절편을 조각에 적용)
        for r in rows:
            sec = r["sector"]
            tag = f"{key}/{sec}"
            cap = mine[sec]["cap"]
            ser = series(mkts, sec, "inst")

            def piece(a, b_, _ser=ser, _sec=sec, _cap=cap):
                """조각 [a,b] 의 (가속도, 수익률). 수익률이나 시총이 없으면 None."""
                rr = comp_ret(mkts, _sec, a, b_)
                if rr is None or not _cap:
                    return None
                return sum(_ser[a:b_ + 1]) / _cap * 100.0, rr

            # dW/dt — 구간을 반으로: 앞 [i0, mid], 뒤 [mid+1, i1]. W = 가속도 × 수익률.
            mid = i0 + (i1 - i0) // 2
            want_p = None
            if i0 < mid < i1:
                h1, h2 = piece(i0, mid), piece(mid + 1, i1)
                if h1 is not None and h2 is not None:
                    want_p = (h2[0] * h2[1] - h1[0] * h1[1]) / max(1, i1 - mid)
            cmp("dW/dt = (W_뒤 − W_앞)/(뒤 반의 거래일수)", tag, r.get("P"), want_p, rel)

            # ẋ·ẍ — 길이 Δ 가 **같은** 세 조각(가장 오래된 나머지 날은 버린다), 부호 반전.
            want_v = want_a = None
            if win >= 9 and k:
                dl = win // 3
                s0 = i1 - 3 * dl + 1
                ps = [piece(s0 + j * dl, s0 + (j + 1) * dl - 1) for j in range(3)]
                if all(p is not None for p in ps):
                    x1, x2, x3 = ((k * a + b) - dv for a, dv in ps)
                    want_v = -(x3 - x1) / (2.0 * dl)
                    want_a = -(x3 - 2.0 * x2 + x1) / (dl * dl)
            cmp("ẋ = −(x₃ − x₁)/2Δ (같은 길이 세 조각)", tag, r.get("xdot"), want_v, rel)
            cmp("ẍ = −(x₃ − 2x₂ + x₁)/Δ² (같은 길이 세 조각)", tag, r.get("xddot"), want_a, rel)

    # 비교가 0 건이면 **실패**다 — 본 것이 없는데 초록이면 이 층이 있는 줄 알고 믿는다.
    for name in ("임펄스·거래대금 = 일별 합 (4주체·tv)", "시총 = 시장별 기간말 시총의 합",
                 "가속도 = 임펄스/시총×100 (시총 없으면 결측)",
                 "구간 수익률 = 일별 시총가중 수익률의 기하누적",
                 "1년[%ile] = 롤링 합의 1년 분포에서 오늘의 중간순위",
                 "얇은 섹터 = 구간 거래 종목 10 미만",
                 "t = 블록 적합 기울기의 t (소수 2자리)",
                 "dW/dt = (W_뒤 − W_앞)/(뒤 반의 거래일수)",
                 "ẋ = −(x₃ − x₁)/2Δ (같은 길이 세 조각)",
                 "ẍ = −(x₃ − 2x₂ + x₁)/Δ² (같은 길이 세 조각)"):
        n_cmp, worst, bad = stats.get(name, [0, 0.0, []])
        chk("A3", name, not bad and n_cmp > 0,
            f"{n_cmp}칸 · {len(bad)}건 · 최대오차 {worst:.2e}"
            + ("  " + "; ".join(bad[:2]) if bad else ""))
    chk("A3", "검사한 블록 수", n_blocks > 0, f"{n_blocks}개")


# ─────────────────────────────────────────────── B. 데이터

#: 기준일 시장 거래대금이 직전 5일 중앙값의 이 몫 아래면 빈 시장으로 본다. 260거래일 실측 최저는
#: 0.60배(코스닥 2026-02-04)이고 2026-10-27 부분 수집 코스닥은 0.02배 수준이었다. 관문
#: (``completeness_gate.py``)이 종목수로 먼저 막는다 — 이건 관문을 지난 뒤의 두 번째 그물이다.
MIN_LAST_DAY_TV_RATIO = 0.5


def check_market_presence(payload: dict) -> None:
    """시장이 통째로 비었으면 **실패**다 — 없는 값을 건너뛰면 "검사 통과" 가 거짓이 된다."""
    secs, tv_days = payload["sectors"], len(payload["dates"])
    for m in payload["markets"]:
        cap = sum(payload["cap"].get(m, {}).get(s) or 0 for s in secs)
        chk("B", f"{m} 기준일 시총이 비어 있지 않은가", cap > 0, f"합 {cap:,.0f}억")
        tv = [sum(payload["flows"][m][s]["tv"][i] or 0 for s in secs) for i in range(tv_days)]
        base = statistics.median(tv[-6:-1]) if tv_days > 1 else 0
        ok = base > 0 and tv[-1] >= base * MIN_LAST_DAY_TV_RATIO
        chk("B", f"{m} 기준일 거래대금이 비어 있지 않은가", ok,
            f"{tv[-1]:,.0f}억 / 직전 5일 중앙값 {base:,.0f}억")


def layer_b(payload: dict, db: str | None, asof: "str | None" = None) -> None:
    print("\nB. 데이터 — DB 에서 들어온 것이 온전한가")
    dates = payload["dates"]
    chk("B", "거래일 수", len(dates) >= 60, f"{len(dates)}일")
    chk("B", "날짜 오름차순·중복 없음", dates == sorted(set(dates)))
    chk("B", "기준일 마감 플래그 존재", isinstance(payload.get("finalized"), bool),
        f"finalized={payload.get('finalized')}")

    n = len(dates)
    secs, mkts = payload["sectors"], payload["markets"]
    lens = {len(payload["flows"][m][s][k])
            for m in mkts for s in secs for k in ("inst", "forgn", "indiv", "etc", "tv")}
    chk("B", "모든 유량 배열 길이 = 거래일 수", lens == {n}, f"{lens}")
    check_market_presence(payload)

    # 4주체 항등식 — 합이 0 에 가까워야(기타법인 포함)
    tot = {k: sum(sum(payload["flows"][m][s][k]) for m in mkts for s in secs)
           for k in ("inst", "forgn", "indiv", "etc")}
    tv = sum(sum(payload["flows"][m][s]["tv"]) for m in mkts for s in secs)
    imb = sum(tot.values())
    chk("B", "4주체 순매수 합 ≈ 0", abs(imb) < 0.01 * tv,
        f"합 {imb:+,.0f}억 / 거래대금 {tv:,.0f}억 = {imb/tv*100:+.3f}%")

    # 시총·업종지수 커버리지
    cap_n = sum(1 for m in mkts for s in secs if payload["cap"].get(m, {}).get(s))
    idx_n = sum(1 for m in mkts for s in secs if payload["iret"].get(m, {}).get(s))
    chk("B", "섹터 시총 커버리지", cap_n >= len(secs), f"{cap_n}/{len(secs)*len(mkts)}")
    chk("B", "업종지수 커버리지", idx_n >= len(secs) * 0.6, f"{idx_n}/{len(secs)*len(mkts)}")

    # 종목
    nm = payload["names"]
    covered = {r["sector"] for r in nm.values()}
    chk("B", "종목이 전 섹터를 덮는가", covered >= set(secs) - {"(미분류)"},
        f"{len(covered)}/{len(secs)} 섹터, {len(nm)}종목")
    # 종목은 일별 배열이 아니라 **구간별 집계**를 싣는다(전 종목을 실으면서 바뀜).
    wins = {w for r in nm.values() for w in (r.get("win") or {})}
    chk("B", "종목 구간 집계 존재", bool(wins), f"구간 {sorted(wins, key=int)}")
    # 화면이 그리는 종목 값 — **새 열을 추가하면 여기에 이름을 적어라.**
    # producer 가 조용히 빠뜨리면 화면은 `—` 로 예쁘게 뜨고 아무도 모른다.
    # `invtrt`·`penfnd_etc` 는 기관 세부(투신·연기금), `ret` 은 구간 수익률로
    # 상대수익 열의 분자다.
    NAME_WIN_KEYS = {"inst", "forgn", "indiv", "etc", "tv",
                     "invtrt", "penfnd_etc", "ret"}
    lack = sorted({k for r in nm.values() for v in (r.get("win") or {}).values()
                   for k in NAME_WIN_KEYS - set(v)})
    chk("B", "종목 집계에 화면이 그리는 값이 다 있는가", not lack, f"없는 키 {lack}")
    # 투신·연기금이 **실제로 값을 갖는가.** 키만 있고 전부 0 이면 열이 두 줄
    # 늘고 정보는 0 이다(`natn` 이 정확히 그 모양이라 INST_DETAIL 에서 뺐다).
    for k in ("invtrt", "penfnd_etc"):
        live = sum(1 for r in nm.values()
                   if any((v.get(k) or 0) for v in (r.get("win") or {}).values()))
        chk("B", f"{k} 가 0 이 아닌 종목 수", live >= len(nm) * 0.2,
            f"{live}/{len(nm)}종목")
    # 종목 수익률은 등락률의 기하누적이라 −100% 아래로 못 내려간다.
    bad_ret = [f"{c}/{w}={(v or {}).get('ret'):.1f}"
               for c, r in nm.items() for w, v in (r.get("win") or {}).items()
               if (v or {}).get("ret") is not None
               and not (-100.0 <= (v or {}).get("ret") < 1e6)]
    chk("B", "종목 구간수익률이 −100% 아래로 안 내려가는가", not bad_ret,
        "; ".join(bad_ret[:3]))
    # 섹터 종목 수가 n_by_sector 와 맞는가 — 전 종목을 싣는다는 주장의 검사다.
    from collections import Counter
    cnt = Counter((r["market"], r["sector"]) for r in nm.values())
    bad = [f"{m}/{s}: names {cnt.get((m,s),0)} vs n_by_sector {v}"
           for m, d2 in payload.get("n_by_sector", {}).items()
           for s, v in d2.items() if cnt.get((m, s), 0) != v]
    chk("B", "섹터별 종목 수 = n_by_sector", not bad, "; ".join(bad[:2]))

    if not db:
        print("       (--db-check 없음 — DB 대조는 건너뛴다)")
        return
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
    from sectorflow.storage import connect
    con = connect(db)
    cur = con.cursor()
    ph = "?" if con.__class__.__module__.startswith("sqlite3") else "%s"
    if asof:
        # 재구성(--asof): 기준일은 DB 최신일이 아니라 지정한 날이다. 그날이 거래일이고 페이로드가 거기서 끝나야 한다.
        cur.execute(f"SELECT max(date) FROM supply_demand WHERE date <= {ph}", (asof,))
        db_last = str(cur.fetchone()[0])
        chk("B", "페이로드 기준일 = 재구성 기준일 = 그 이하 DB 마지막 거래일",
            db_last == dates[-1] == asof, f"지정 {asof} / DB {db_last} / 리포트 {dates[-1]}")
    else:
        cur.execute("SELECT max(date) FROM supply_demand")
        db_last = str(cur.fetchone()[0])
        chk("B", "페이로드 기준일 = DB 최신일", db_last == dates[-1], f"DB {db_last} / 리포트 {dates[-1]}")
    # 재구성(--asof)이면 표 전체 검사도 기준일 이하만 본다 — 리포트가 읽지 않은 그 뒤 행의 결함으로
    # 지난 기준일 리포트를 빨갛게 만들지 않고, "모든 읽기는 기준일 이하" 라는 REBUILT.txt 의 말을 지킨다.
    upto, upto_args = (f" AND date <= {ph}", (asof,)) if asof else ("", ())
    for tbl in ("supply_demand", "daily_bars_adjusted"):
        cur.execute(f"SELECT count(*) FROM (SELECT code,date FROM {tbl} WHERE 1=1{upto} "
                    f"GROUP BY code,date HAVING count(*)>1) q", upto_args)
        chk("B", f"{tbl} (code,date) 중복", cur.fetchone()[0] == 0)
    cur.execute(f"SELECT count(*) FROM shares_outstanding_history WHERE shares_outstanding<=0{upto}", upto_args)
    chk("B", "주식수 ≤ 0 행", cur.fetchone()[0] == 0)
    cur.execute(f"SELECT count(*) FROM daily_bars_adjusted WHERE (close<=0 OR low>close OR high<close){upto}",
                upto_args)
    chk("B", "OHLC 불변식 위반", cur.fetchone()[0] == 0)
    check_trade_value_units(cur, dates[-1], ph)
    con.close()


#: Σ(수량×종가)/Σ거래대금 의 허용 띠. 단위 착오(주↔천주, 원↔백만원)는 10³ 배라 넓은 띠로 충분하다.
TV_UNIT_BAND = (0.8, 1.25)


def check_trade_value_units(cur, day: "str | None" = None, ph: str = "%s") -> None:
    """단위 sanity — 페이로드의 거래대금(`acc_trde_qty × close`)이 보고된 거래대금과 **같은 단위**인가.

    예전엔 종목마다 ``|수량×종가 ÷ 거래대금 − 1| < 5%`` 인 비율이 90% 를 넘는지 봤다. 그건 **정의가
    다른 둘**을 맞댄 것이다 — 거래대금은 체결가 합(VWAP × 수량)이고 수량×종가는 종가 하나로 환산한
    근사라, 장중 변동이 큰 종목일수록 5% 를 넘는다. 2026-09-14 애프터마켓 통합 뒤로는 종가가
    20:00 체결가라 그 차이가 더 벌어졌다(kr_quant 실측, 키움 수급 ⋈ 일봉: 그 비율이 09-14 전
    230일 평균 97.0% → 뒤 36일 평균 94.6%, 최저 90.6%(11-04) — 문턱 90% 에 바짝 붙어 단위와
    무관한 날에 빨개질 참이었다).

    단위가 묻는 것은 개별 종목의 근사 오차가 아니라 **배율**이다. 그래서 같은 행들의 합끼리
    비를 본다 — Σ(수량×종가)/1e6 ÷ Σ거래대금(백만원). 같은 실측에서 266일 전부 0.940~1.040
    (09-14 뒤 36일 0.969~1.032)이고, 단위 착오는 10³ 배다.
    """
    # ``day`` 는 페이로드 기준일 — 재구성(--asof)에선 DB 최신일이 아니다. 없으면 DB 최신일.
    on = f"s.date={ph}" if day else "s.date=(SELECT max(date) FROM supply_demand)"
    cur.execute(f"""SELECT sum(s.acc_trde_qty * 1.0 * abs(s.close)) / 1e6, sum(b.trade_value * 1.0),
                          count(*)
        FROM supply_demand s JOIN daily_bars b ON b.code=s.code AND b.date=s.date
        WHERE s.source='kiwoom' AND {on}
          AND b.trade_value>0""", (day,) if day else ())
    qc, tv, total = cur.fetchone()
    ratio = float(qc) / float(tv) if total and qc is not None and tv else None
    lo, hi = TV_UNIT_BAND
    chk("B", f"금액 단위: Σ(수량×종가)/Σ거래대금 ∈ [{lo}, {hi}]",
        ratio is not None and lo <= ratio <= hi,
        f"{ratio:.4f} ({total}종목)" if ratio is not None else "표본 없음")


# ─────────────────────────────────────────────── C. 계산

def layer_d(D: dict, P: dict, html: str) -> None:
    """D. **부류 검사** — 오늘 나온 버그들이 전부 같은 모양이었다:
    *계산에 들어가는 두 값이 서로 다른 집합·시점·파라미터에서 나오는데 아무도
    검사하지 않는다.* 개별 증상이 아니라 그 부류를 막는다.

    실제 사례(전부 실측으로 확인됨):
      · 분모 시점 불일치 — 구간말이 1년 전인데 오늘 시총으로 나눔(중앙값 1.33배)
      · 분모 집합 불일치 — 수익률은 지수 있는 시장만, 시총은 모든 시장(겹침 0인 섹터 2개)
      · 표 헤더 ≠ 셀 개수 — 같은 실수가 두 번(열이 한 칸씩 밀림)
      · 라벨 ≠ 실제 계산 — 민감도 config 는 바뀌는데 시뮬레이션은 동일
    """
    print("\nD. 부류 — 두 값이 같은 집합·시점에서 나오는가")

    # ⓪ 화면이 그리는 키를 producer 가 실제로 싣는가.
    #
    # 이 저장소의 서명 같은 실패 모드다: producer 가 새 키를 조용히 빠뜨려도
    # 화면은 `—` 로 예쁘게 뜨고 아무도 모른다. 열이 있는데 전부 결측인 것과
    # 열이 아예 없는 것은 화면에서 구분되지 않는다.
    #
    # 새 열을 추가하면 **여기에 키를 등록하라.** 등록을 잊으면 그 열은
    # 조용히 빈 채로 매일 나온다.
    RENDERED_KEYS = ("inst", "forgn", "indiv", "etc",     # 주체 토글이 쓰는 넷
                     "accel", "pct1y", "spark", "x", "U", "P", "xddot",
                     "G", "ret", "cap", "n_all", "top")
    for bk, b in (D.get("blocks") or {}).items():
        rows = b.get("rows") or []
        if not rows:
            continue
        missing = [k for k in RENDERED_KEYS
                   if not any(k in r for r in rows)]
        chk("D", f"{bk} 화면이 그리는 키가 페이로드에 있는가",
            not missing, f"없는 키 {missing}" if missing else "")
        break        # 블록 구조는 동일하다 — 하나만 봐도 producer 누락은 잡힌다

    # ① 분모의 **시점**: 월별 시총이 구간말 월을 덮는가
    cbm = P.get("cap_by_month", {})
    if cbm:
        months = {m for mk in cbm.values() for sec in mk.values() for m in sec}
        want = {d[:7] for d in P["dates"]}
        chk("D", "월별 시총이 페이로드 전 구간의 월을 덮는가", want <= months,
            f"부족한 월 {sorted(want - months)[:3]}")
    else:
        chk("D", "월별 시총 계열 존재", False, "cap_by_month 없음 — 임의 구간 분모가 틀어진다")

    # ② 분모의 **집합**: 지수가 있는 시장과 시총 집계 시장이 같은가
    bad = []
    for m in P["markets"]:
        for sec in P["sectors"]:
            has_idx = bool(P["iret"].get(m, {}).get(sec))
            has_cap = bool(P["cap"].get(m, {}).get(sec))
            if has_idx and not has_cap:
                bad.append(f"{m}/{sec}")
    chk("D", "지수 있는 (시장,섹터)에 시총도 있는가", not bad, ", ".join(bad[:3]))

    # ③ **라벨 ≠ 계산**: 표에 실린 a 가 그 행의 임펄스/시총과 실제로 일치하는가
    #    (라벨만 바뀌고 값이 안 바뀌는 부류를 잡는다)
    mismatch = 0
    for B in D["blocks"].values():
        for r in B["rows"]:
            if r.get("a_idx") is None or not r.get("cap_idx"):
                continue
            if abs(r["a_idx"] - r["inst"] / r["cap_idx"] * 100) > TOL_EXACT:
                mismatch += 1
    chk("D", "표의 가속도 = 그 행의 임펄스/시총", mismatch == 0, f"{mismatch}건")

    # ④ **블록 간 값이 실제로 달라야 한다** — 창을 바꿨는데 값이 완전히 같으면
    #    파라미터가 계산에 도달하지 않는다는 신호다(민감도 격자가 그 모양이었다).
    keys = sorted(D["blocks"])
    same = []
    for i in range(len(keys) - 1):
        a, b = D["blocks"][keys[i]], D["blocks"][keys[i + 1]]
        if keys[i].split("|")[1] != keys[i + 1].split("|")[1]:
            continue
        va = [r.get("inst") for r in a["rows"]]
        vb = [r.get("inst") for r in b["rows"]]
        if va == vb:
            same.append(f"{keys[i]} == {keys[i+1]}")
    chk("D", "창이 다르면 값도 다른가(파라미터가 계산에 도달하는가)", not same,
        ", ".join(same[:2]))

    # ⑤ **개수의 집합**: 표가 적는 종목 수 = 그 줄에서 Enter 를 눌렀을 때 나오는
    #    목록의 길이. 이 층은 "분자와 분모가 같은 집합인가" 를 오래 봤는데
    #    **개수는 안 봤다.** 실측(수정 전): 표가 "전기/전자 387" 이라 적고 목록은
    #    386 개였다 — 두 달 전 수급이 끊긴 종목이 `stocks` 에 남아 있어서다.
    #    더 나쁜 건 `MIN_NAMES` 판정이 이 수로 이뤄져 **죽은 이름 하나가 얇은
    #    섹터 경고를 지운다**는 것이다(코스닥/종이/목재 10 vs 실제 9).
    names = D.get("names") or {}
    mkts_all = P["markets"]
    bad, flips = [], []
    for bk, B in (D.get("blocks") or {}).items():
        win, mk = bk.split("|")
        sel = mkts_all if mk == "전체" else [mk]
        listed: dict = {}
        for nm in names.values():
            if nm.get("market") in sel and (nm.get("win") or {}).get(win):
                listed[nm.get("sector")] = listed.get(nm.get("sector"), 0) + 1
        for r in B["rows"]:
            got = listed.get(r["sector"], 0)
            if got != r.get("n_all"):
                bad.append(f"{bk}/{r['sector']} 표 {r.get('n_all')} vs 목록 {got}")
                if (r.get("n_all", 0) < 10) != (got < 10):
                    flips.append(f"{bk}/{r['sector']}")
    chk("D", "표의 종목[수] = 드릴다운 목록 길이", not bad,
        f"{len(bad)}건 · ~마커 뒤집힘 {len(flips)}건  " + "; ".join(bad[:2]))

    # ⑥ **분자의 집합**: 구간말 시총이 없는(=상장이 끊긴) 종목의 순매수가
    #    임펄스에 얼마나 섞였나. 분모(섹터 시총)에는 그들이 없으므로 가속도가
    #    그만큼 오염된다. 실측(2026-08-28): 5·20일 창은 0.000%p, 60일 최대
    #    0.029%p, 120일 최대 0.142%p — 작지만 0 은 아니다. 임펄스는 "실제로
    #    흘러간 돈" 이라 빼지 않고, 대신 **오염 크기를 검사로 묶어둔다.**
    dead = {c for c, nm in names.items() if nm.get("cap") is None}
    worst_pol, where = 0.0, ""
    for bk, B in (D.get("blocks") or {}).items():
        win, mk = bk.split("|")
        sel = mkts_all if mk == "전체" else [mk]
        for r in B["rows"]:
            if not r.get("cap_idx"):
                continue
            s = 0.0
            for c in dead:
                nm = names[c]
                if nm.get("sector") != r["sector"] or nm.get("market") not in sel:
                    continue
                w = (nm.get("win") or {}).get(win)
                if w:
                    s += w.get("inst") or 0.0
            pol = abs(s / r["cap_idx"] * 100)
            if pol > worst_pol:
                worst_pol, where = pol, f"{bk}/{r['sector']}"
    chk("D", "상장이 끊긴 종목이 가속도를 오염시키는 폭 < 1%p", worst_pol < 1.0,
        f"최대 {worst_pol:.5f}%p ({where}) · 해당 종목 {len(dead)}개")


# ─────────────────────────────────────────────── E. 화면

def layer_e(D: dict) -> None:
    """E. **화면** — 페이로드가 맞아도 렌더가 틀릴 수 있다.

    A~D 는 전부 파일 안의 숫자만 본다. 그런데 이 저장소가 실제로 낸 사고 중
    여럿은 **그 숫자가 화면에 도달하는 길**에서 났다: 열 정의와 셀 개수가
    어긋나 한 칸씩 밀리고, 한글이 두 칸인 걸 잊어 줄이 접히고, 검사는 폭 80
    으로 묻는데 화면은 79 로 그렸다(`view_width` 가 그래서 생겼다).

    그래서 렌더가 낸 **문자열을 다시 파싱해** 열 정의가 내는 값과 대조한다.
    DB 가 없어도 돌고, 리포트 파일 하나만 있으면 된다.

    ⚠️ 종목명·섹터명은 폭에 맞춰 `pad` 가 자른다(설계다). **숫자 열은 하나도
    잘리면 안 된다** — `-1,360` 이 `-1` 로 보이는 것이 이 검사의 표적이다.
    """
    print("\nE. 화면 — 그려진 글자가 값과 같은가")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "src"))
    try:
        from sectorflow.tui import flow_view as FV
    except ImportError as e:                      # pragma: no cover
        chk("E", "flow_view 를 읽을 수 있는가", False, str(e))
        return

    TEXT_COLS = {"섹터", "종목", "순매수상위[억]", "순매도상위[억]", ""}
    bad_w, bad_v, seen = [], [], 0

    def cells(line: str, widths: list[int], i: int) -> str:
        a, w = FV.span_at(widths, i)
        cur, buf = 0, []
        for ch in line:
            if a <= cur < a + w:
                buf.append(ch)
            cur += FV.cell_width(ch)
        return "".join(buf).strip()

    for term in (80, 120, 200):
        width = FV.view_width(term)
        for wi in range(len(FV.WINDOWS)):
            for mi in range(3):
                st = FV.State(D)
                st.wi = wi
                if mi >= len(st.markets):
                    continue
                st.mi = mi
                seen += 1
                lines, _thin, _nh = FV.table_lines(st, width, 50)
                for ln in lines:
                    if FV.cell_len(ln) != width:
                        bad_w.append(f"표 {term}/{FV.WINDOWS[wi]}/{st.market} "
                                     f"{FV.cell_len(ln)} != {width}")
                cols = FV._fit(FV.table_cols(st, width), width)
                widths = [c.width for c in cols]
                for ln, r in zip(lines[1:], st.rows()):
                    for i, c in enumerate(cols):
                        want = c.fn(r, st).strip()
                        if c.header in TEXT_COLS:
                            continue
                        if cells(ln, widths, i) != want:
                            bad_v.append(f"{term}/{FV.WINDOWS[wi]}/{st.market} "
                                         f"{c.header!r} {cells(ln, widths, i)!r} != {want!r}")
                rows = st.rows()
                for ri in ({0, len(rows) // 2, len(rows) - 1} if rows else set()):
                    st.row = ri
                    nl, nhead = FV.names_lines(st, width)
                    for ln in nl:
                        if FV.cell_len(ln) != width:
                            bad_w.append(f"종목 {term}/{FV.WINDOWS[wi]}/{st.market} "
                                         f"{FV.cell_len(ln)} != {width}")
                    # 실제 렌더 경로를 쓴다 — `_fit` 만으로는 `COL_PAIRS`
                    # (참여율·거래대금은 통째로 남거나 통째로 빠진다)를 모른다.
                    # 검사와 화면이 갈리면 화면이 맞는데 검사가 빨개진다.
                    ncols = FV.fit_names(width)
                    nwid = [c.width for c in ncols]
                    for ln, t in zip(nl[nhead:], st.names()):
                        for i, c in enumerate(ncols):
                            if c.header in TEXT_COLS:
                                continue
                            want = c.fn(t, st).strip()
                            if cells(ln, nwid, i) != want:
                                bad_v.append(f"{term}/종목 {c.header!r} "
                                             f"{cells(ln, nwid, i)!r} != {want!r}")
                for ln in FV.header_lines(st, width) + FV.detail_lines(st, width):
                    if FV.cell_len(ln) != width:
                        bad_w.append(f"헤더/상세 {term} {FV.cell_len(ln)} != {width}")
    chk("E", "모든 줄의 표시 폭 = view_width(터미널−1)", not bad_w,
        f"{len(bad_w)}건  " + "; ".join(bad_w[:2]))
    chk("E", "숫자 열: 그려진 글자 = 열 정의가 내는 값 (폭 80·120·200)", not bad_v,
        f"{len(bad_v)}건  " + "; ".join(bad_v[:2]))
    chk("E", "검사한 (폭,창,시장) 조합", seen >= 30, f"{seen}개")

    # 종목 목록의 누적[%] — 단조증가하고 100 에서 끝난다.
    bad = []
    for wi in range(len(FV.WINDOWS)):
        st = FV.State(D)
        st.wi = wi
        for ri in range(len(st.rows())):
            st.row = ri
            arr = [t["cum"] for t in st.names() if t.get("cum") is not None]
            if not arr:
                continue
            if any(b < a - 1e-9 for a, b in zip(arr, arr[1:])):
                bad.append(f"{FV.WINDOWS[wi]}/{ri} 단조증가 아님")
            if abs(arr[-1] - 100.0) > 1e-6:
                bad.append(f"{FV.WINDOWS[wi]}/{ri} 끝값 {arr[-1]}")
    chk("E", "종목 누적[%] 이 단조증가하고 100 에서 끝나는가", not bad,
        "; ".join(bad[:3]))

    # 상대수익의 기준선이 **섹터 표의 그 수익률**과 같은 값인가.
    bad = []
    for wi, w in enumerate(FV.WINDOWS):
        if w == "종합":
            continue
        st = FV.State(D)
        st.wi = wi
        for ri, r in enumerate(st.rows()):
            st.row = ri
            sret = r.get("ret")
            for t in st.names()[:5]:
                if t.get("rrel") is None or t.get("ret") is None:
                    continue
                if abs((t["ret"] - sret) - t["rrel"]) > TOL_EXACT:
                    bad.append(f"{w}/{r['sector']}/{t['code']}")
    chk("E", "상대수익 = 종목 수익률 − 섹터 표의 수익률", not bad, "; ".join(bad[:3]))

    # 시총대비 — **페이로드의 그 종목 칸**에서 다시 나눈다. 위 대조는 열 함수끼리라 행의 값 자체가 틀리면 같이 틀린다
    # (리뷰 4차 D1: ×0.1 변이가 pytest·실DB 를 다 지났다). 표지도 같이 본다 — 시총이 낡은 종목의 칸은 글자에 `!` 가 있어야 한다.
    bad, bad_mark, n = [], [], 0
    names_raw = D.get("names") or {}
    ncols = {c.header: c for c in FV.names_cols(True)}
    for wi, w in enumerate(FV.WINDOWS):
        if w == "종합":
            continue
        for ai, (actor, _ko) in enumerate(FV.ACTORS):
            st = FV.State(D)
            st.wi, st.ai = wi, ai
            for ri in range(len(st.rows())):
                st.row = ri
                for t in st.names():
                    raw = names_raw[t["code"]]
                    v, cap = ((raw.get("win") or {}).get(w) or {}).get(actor), raw.get("cap")
                    want = ((v or 0.0) / cap * 100.0) if cap else None
                    n += 1
                    if (want is None) != (t.get("a") is None) or (want is not None and abs(t["a"] - want) > 1e-9):
                        bad.append(f"{w}/{actor}/{t['code']} {t.get('a')} vs {want}")
                    if raw.get("cap_pending") and cap and not all(
                            ncols[h].fn(t, st).strip().endswith(FV.CAP_PENDING_MARK) for h in ("시총대비[%p]", "시총[억]")):
                        bad_mark.append(f"{w}/{actor}/{t['code']}")
    chk("E", "종목 시총대비 = 페이로드 순매수 ÷ 시총 × 100 (창×주체×섹터)", not bad and n > 0,
        f"{n}칸  " + "; ".join(bad[:3]))
    chk("E", "권리락 신주 미반영 종목의 시총 칸에 표지가 있는가", not bad_mark, "; ".join(bad_mark[:3]))

    # ── 전 종목 화면(`t`) — 두 층을 곱한 목록 ────────────────────────────
    #
    # 이 화면은 섹터 표에도 드릴다운에도 없는 값을 만든다(`곱`). 그런데 그 값은
    # **다른 두 화면에서 이미 보이는 값의 곱**이라, 세 화면이 서로 어긋날 수 있다.
    # 어긋나면 사용자는 같은 종목을 세 자리에서 세 가지로 읽는다.
    bad_mul, bad_ord, bad_g, bad_p, bad_dead = [], [], [], [], []
    combos = 0
    for wi in range(len(FV.WINDOWS)):
        for mi in range(3):
            for ai in range(len(FV.ACTORS)):
                st = FV.State(D)
                st.wi, st.mi, st.ai = wi, mi, ai
                rows = st.all_picks()
                tag = f"{st.window}/{st.market}/{st.actor}"
                combos += 1
                gmap = {r["sector"]: r.get("G") for r in st.rows()}
                for t in rows:
                    if abs(t["both"] - t["gsec"] * t["pick"]) > 1e-12:
                        bad_mul.append(f"{tag}/{t['code']}")
                    if t["gsec"] != gmap.get(t["sector"]):
                        bad_g.append(f"{tag}/{t['code']}")
                    if gmap.get(t["sector"]) is None:
                        bad_dead.append(f"{tag}/{t['code']}")
                vals = [t["both"] for t in rows]
                if vals != sorted(vals, reverse=True):
                    bad_ord.append(tag)
                # 종목 점수는 그 섹터 드릴다운이 내는 값과 **같은 수**여야 한다.
                for ri, r in enumerate(st.rows()):
                    if r.get("G") is None:
                        continue
                    st.row = ri
                    pm = {t["code"]: t.get("pick") for t in st.names()}
                    for t in rows:
                        if t["sector"] == r["sector"] and pm.get(t["code"]) != t["pick"]:
                            bad_p.append(f"{tag}/{t['code']}")
    chk("E", "전 종목: 곱 = 섹터G × 종목선정", not bad_mul, "; ".join(bad_mul[:3]))
    chk("E", "전 종목: 곱 내림차순 고정", not bad_ord, "; ".join(bad_ord[:3]))
    chk("E", "전 종목: 섹터G = 섹터 표의 그 값", not bad_g, "; ".join(bad_g[:3]))
    chk("E", "전 종목: 종목선정 = 드릴다운의 그 값", not bad_p, "; ".join(bad_p[:3]))
    chk("E", "전 종목: 관문 탈락 섹터의 종목은 안 나온다", not bad_dead,
        "; ".join(bad_dead[:3]))
    chk("E", "전 종목: 검사한 (창,시장,주체) 조합", combos >= 60, f"{combos}개")



# ─────────────────────────────────────────────── G. 원장 화면

#: 원장 화면을 훑을 폭. 좁은 경계(48·51·71)를 일부러 넣는다 — 스파크라인의
#: 마지막 점이 밀리던 자리가 폭 51 이었고, 열이 떨어지는 경계가 71 이다.
LEDGER_WIDTHS = (48, 51, 71, 79, 80, 119, 120, 199)


def comove_table(mo, windows, min_n: int, actors) -> tuple[dict, list[str]]:
    """시장×구간×주체의 (관측, 널) 음수쌍 비율[%]과 **못 잰 조합**.

    못 재는 것은 시장이 비어 상관을 낼 섹터가 2개 미만일 때다. 예전엔 ``None * 100`` 에서 검증 전체가
    트레이스백으로 죽었다. 건너뛰지 않고 호출자가 실패로 적게 돌려준다.
    """
    obs: dict = {}
    empty: list[str] = []
    for mi in range(len(mo.markets)):
        for wi, W in enumerate(windows):
            if W < min_n:
                continue
            for ai, (_k, ko) in enumerate(actors):
                mo.mi, mo.wi, mo.ai, mo.detrend = mi, wi, ai, False
                o, nul = mo.obs_neg_frac(), mo.null_neg_frac()
                if o is None or nul is None:
                    empty.append(f"{mo.market}/{W}/{ko}")
                    continue
                obs.setdefault(ko, []).append((o * 100, nul * 100, mo.market, W))
    return obs, empty


def layer_g(P: dict, D: dict) -> None:
    """G. **자금 원장 화면**(``sw-ledger``) — 값·그림·두 앱의 대조.

    A~F 는 ``sw-flow`` 의 표(``numbers.html`` 의 ``D``)만 본다. 원장은 같은
    페이로드를 **다른 함수로** 읽어 다른 화면을 그리므로, flow 가 초록이어도
    원장이 틀릴 수 있다. 실제로 그랬다 — ``downsample`` 이 부동소수 반올림으로
    구간의 마지막 날을 버렸고(폭 51·60일), ``종목[수]`` 가 flow 와 다른 수를
    말했다(324칸 중 49칸).

    ⚠️ **``Model`` 의 값을 ``Model`` 로 검산하지 않는다.** 기대값은 페이로드의
    ``flows`` 를 여기서 직접 더해 만들고(F 층과 같은 규율), 화면 값은 **그려진
    문자열을 다시 파싱해** 꺼낸다. 같은 함수를 두 번 부르면 같은 버그를 두 번
    통과시킨다.
    """
    print("\nG. 원장 화면 — 값·그림·sw-flow 와의 대조")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "src"))
    try:
        from sectorflow.tui import flow_view as FV
        from sectorflow.tui import ledger_view as LV
    except ImportError as e:                      # pragma: no cover
        chk("G", "ledger_view 를 읽을 수 있는가", False, str(e))
        return

    AK = LV.ACTOR_KEYS
    mo = LV.Model(P)
    dates, secs = P["dates"], P["sectors"]

    def span(line: str, a: int, w: int) -> str:
        """줄에서 (시작 표시칸, 폭) 구간의 글자 — 한글 2칸을 센다."""
        cur, buf = 0, []
        for ch in line:
            if a <= cur < a + w:
                buf.append(ch)
            cur += FV.cell_width(ch)
        return "".join(buf).strip()

    def cells(line: str, widths: list[int], i: int) -> str:
        return span(line, *FV.span_at(widths, i))

    def series(mkts, sector, key, w):
        """(시장들, 섹터)의 일별 계열 — **페이로드에서 직접** 더한다."""
        arr = [0.0] * len(dates)
        for m in mkts:
            c = P["flows"][m].get(sector)
            if c is None:
                continue
            for i, v in enumerate(c[key]):
                arr[i] += v
        return arr[len(arr) - w:]

    # ── ① 표의 값: 페이로드 직접 재집계 ──
    pairs, bad_gate = [], []
    for mi, mk in enumerate(mo.markets):
        mkts = P["markets"] if mk == "전체" else [mk]
        for wi, W in enumerate(LV.WINDOWS):
            w = min(W, len(dates))
            for ai in range(len(LV.ACTORS)):
                mo.mi, mo.wi, mo.ai = mi, wi, ai
                rows = {r["sector"]: r for r in mo.rows()}
                for s in secs:
                    d4 = [series(mkts, s, k, w) for k in AK]
                    exp = {k: sum(a) for k, a in zip(AK, d4)}
                    gross = sum(abs(x) for a in d4 for x in a)
                    rabs = sum(abs(sum(a[i] for a in d4)) for i in range(w))
                    ag = sum(abs(x) for x in d4[ai])
                    tag = f"{mk}/{W}/{LV.ACTORS[ai][0]}/{s}"
                    # 이 시장에 종목이 없는 섹터는 행이 아니다(`+0` 행으로 뜨던 자리) — 종목이 있는데 행이 없으면 틀렸다.
                    r = rows.get(s)
                    if r is None:
                        pairs.append((f"{tag}/행이 있어야 하는데 없다", float(sum(int((P.get("n_by_sector") or {}).get(m, {}).get(s) or 0) for m in mkts)), 0.0))
                        continue
                    for k in AK:
                        pairs.append((f"{tag}/{k}", r[k], exp[k]))
                    pairs.append((f"{tag}/잔여", r["resid"], -sum(exp.values())))
                    pairs.append((f"{tag}/절대크기", r["abs"],
                                  sum(abs(v) for v in exp.values())))
                    pairs.append((f"{tag}/잔여몫", r["resid_pct"],
                                  rabs / gross * 100
                                  if gross >= LV._MIN_RATIO_GROSS else None))
                    pairs.append((f"{tag}/최대일몫", r["spike"],
                                  max(abs(x) for x in d4[ai]) / ag * 100
                                  if ag >= LV._MIN_RATIO_GROSS else None))
                    # 비율은 **분모가 클 때만** 뜻이 있다 — 값이 있으면 분모도 있어야.
                    if r["resid_pct"] is not None and gross < LV._MIN_RATIO_GROSS:
                        bad_gate.append(f"{tag} 잔여몫 분모 {gross:.3f}억")
                    if r["spike"] is not None and ag < LV._MIN_RATIO_GROSS:
                        bad_gate.append(f"{tag} 최대일몫 분모 {ag:.3f}억")
    bad, worst = [], 0.0
    for label, x, y in pairs:
        if (x is None) != (y is None):
            bad.append(f"{label} {x} vs {y}")
        elif x is not None:
            worst = max(worst, abs(x - y))
            if abs(x - y) > TOL_EXACT:
                bad.append(f"{label} {x} vs {y}")
    chk("G", "원장 표의 값 = 페이로드 직접 재집계 (시장×구간×주체×섹터×8값)",
        not bad and bool(pairs), f"{len(pairs)}칸 · 최대오차 {worst:.3e}  " + "; ".join(bad[:2]))
    chk("G", "비율 열은 분모가 1억 이상일 때만 값을 낸다", not bad_gate,
        f"{len(bad_gate)}건  " + "; ".join(bad_gate[:2]))

    # ── ② 그려진 글자 = 열 정의 · 스파크라인 역산 ──
    lvl = {g: k for k, g in LV.SPARK_SIGNED.items()}
    bad_w, bad_v, bad_sp, bad_sc, seen = [], [], [], [], 0
    for width in LEDGER_WIDTHS:
        for mi in range(len(mo.markets)):
            for wi in range(len(LV.WINDOWS)):
                for ai in range(len(LV.ACTORS)):
                    mo.mi, mo.wi, mo.ai = mi, wi, ai
                    seen += 1
                    tag = f"{width}/{mo.market}/{mo.window}/{mo.actor}"
                    rows = mo.rows()
                    unif = mo.uniform_spike()
                    lines, _t, _nh = LV.ledger_lines(mo, width)
                    tl, _t2, tnh = LV.timeline_lines(mo, width)
                    cm, marks, cnh = LV.comove_lines(mo, width)
                    for ln in lines + tl + cm + LV.header_lines(mo, width):
                        if FV.cell_len(ln) != width:
                            bad_w.append(f"{tag} {FV.cell_len(ln)}!={width}")
                    if width >= LV.LEDGER_MIN_W:
                        cols = LV._fit(LV.ledger_cols(), width)
                        ws = [c[1] for c in cols]
                        for ln, r in zip(lines[1:], rows):
                            sp, rp = r["spike"], r["resid_pct"]
                            mark = ("!" if sp is not None
                                    and sp >= LV.SPIKE_MARK_MULT * unif else "")
                            want = (["", "~" if r["n"] < LV.THIN_N else ""]
                                    + [FV.fmt_amt(r[k]) for k in AK]
                                    + [FV.fmt_amt(r["resid"]),
                                       f"{rp:.1f}" if rp is not None else "—",
                                       f"{sp:.1f}{mark}" if sp is not None else "—",
                                       str(r["n"])])
                            for i, c in enumerate(cols):
                                if c[0] in ("섹터", ""):
                                    continue
                                got = cells(ln, ws, i)
                                if got != want[i].strip():
                                    bad_v.append(f"원장 {tag} {c[0]} "
                                                 f"{got!r}!={want[i]!r}")
                    if width >= LV.TIMELINE_MIN_W:
                        cols = LV._fit(LV.timeline_cols(), width)
                        ws = [c[1] for c in cols]
                        sw = min(mo.window, width - sum(c[1] + 1 for c in cols))
                        for ln, r in zip(tl[tnh:], rows):
                            v = series(P["markets"] if mo.market == "전체"
                                       else [mo.market], r["sector"], mo.actor,
                                       mo.window)
                            cum, acc = [], 0.0
                            for x in v:
                                acc += x
                                cum.append(acc)
                            show = LV.downsample(cum, sw)
                            # 그림의 오른쪽 끝은 **구간의 마지막 날**이다.
                            if show and show[-1] != cum[-1]:
                                bad_sp.append(f"{tag} 끝점이 구간 끝이 아니다")
                            want = ["", "~" if r["n"] < LV.THIN_N else "",
                                    FV.fmt_amt(acc), f"{max(abs(x) for x in show):,.0f}"
                                    if show else "0"]
                            for i, c in enumerate(cols):
                                if c[0] in ("섹터", ""):
                                    continue
                                got = cells(ln, ws, i)
                                if got != want[i].strip():
                                    bad_v.append(f"전개 {tag} {c[0]} "
                                                 f"{got!r}!={want[i]!r}")
                            if len(cols) > 3:
                                sc = float(cells(ln, ws, 3).replace(",", ""))
                                if sc + 1 < abs(acc):
                                    bad_sc.append(f"{tag} 눈금 {sc} < |누적| {acc}")
                            drawn = [ch for ch in ln if ch in LV.SPARK]
                            if len(drawn) != len(show):
                                bad_sp.append(f"{tag} 점 {len(drawn)}!={len(show)}")
                                continue
                            peak = max((abs(x) for x in show), default=0.0)
                            for ch, val in zip(drawn, show):
                                if peak <= 0 or val == 0:
                                    exp = 0
                                else:
                                    rr = val / peak
                                    exp = (2 if rr > 0.5 else 1 if rr > 0
                                           else -1 if rr >= -0.5 else -2)
                                if lvl[ch] != exp:
                                    bad_sp.append(f"{tag} 레벨 {lvl[ch]}!={exp}")
                                # 중앙선 위/아래가 부호와 같은가.
                                if (val > 0) != (lvl[ch] > 0) and val != 0:
                                    bad_sp.append(f"{tag} 부호 {val} {ch}")
                    # 동시성 — 그려진 칸이 상관행렬과 같은가, 색 지시가 부호 위인가.
                    if mo.window >= LV._MIN_CORR_N:
                        keys, m = mo.corr_matrix()
                        mean = mo.corr_means()
                        x0 = 13 + 1 + 4 + 1
                        ncol = max(0, min(len(keys), (width - x0) // 2))
                        for i, a in enumerate(keys):
                            ln = cm[cnh + i]
                            got = span(ln, 14, 4)
                            wnt = f"{mean[a] * 100:+.0f}" if mean[a] == mean[a] else "?"
                            if got != wnt:
                                bad_v.append(f"동시성 {tag} 평균 {got!r}!={wnt!r}")
                            cur, buf = 0, []
                            for ch in ln:
                                if cur >= x0:
                                    buf.append(ch)
                                cur += FV.cell_width(ch)
                            s = "".join(buf)
                            for j in range(ncol):
                                wnt = (LV.heat_diag() if i == j
                                       else LV.heat_cell(m[i][j]))
                                if s[2 * j:2 * j + 2] != wnt:
                                    bad_v.append(f"동시성 {tag} 칸 {i},{j}")
                                    break
                        for y, x, wd, lv in marks:
                            t = span(cm[y], x, wd)
                            if not t or t[0] not in "+-" or lv == 0:
                                bad_v.append(f"동시성 {tag} 색 지시 {y},{x} {t!r}")
    chk("G", "모든 줄의 표시 폭 = 요청한 폭", not bad_w,
        f"{len(bad_w)}건  " + "; ".join(bad_w[:2]))
    chk("G", f"그려진 글자 = 열 정의 (폭 {len(LEDGER_WIDTHS)}종 × 시장×구간×주체)",
        not bad_v, f"{len(bad_v)}건  " + "; ".join(bad_v[:2]))
    chk("G", "스파크라인: 레벨·부호·점 개수·끝점이 구간 끝", not bad_sp,
        f"{len(bad_sp)}건  " + "; ".join(bad_sp[:2]))
    chk("G", "눈금[억] ≥ |누적[억]| (두 열이 서로를 부정하지 않는가)", not bad_sc,
        f"{len(bad_sc)}건  " + "; ".join(bad_sc[:2]))
    chk("G", "검사한 (폭,시장,구간,주체) 조합", seen >= 200, f"{seen}개")

    # ── ③ 원장 ↔ sw-flow: 같은 것을 같은 수로 말하는가 ──
    npair, nbad, fbad, fworst = 0, [], [], 0.0
    for key, B in D["blocks"].items():
        win, mk = key.split("|")
        if not win.isdigit() or int(win) not in LV.WINDOWS or mk not in mo.markets:
            continue
        mo.mi, mo.wi = mo.markets.index(mk), LV.WINDOWS.index(int(win))
        rows = {r["sector"]: r for r in mo.rows()}
        for r in B["rows"]:
            g = rows.get(r["sector"])
            if g is None:
                continue
            npair += 1
            if g["n"] != r.get("n_all"):
                nbad.append(f"{key}/{r['sector']} 원장 {g['n']} vs flow {r.get('n_all')}")
            for k in AK:
                fworst = max(fworst, abs(g[k] - r[k]))
                if abs(g[k] - r[k]) > TOL_FLOW:
                    fbad.append(f"{key}/{r['sector']}/{k} {g[k]} vs {r[k]}")
    chk("G", "원장·flow 의 순매수가 같은가 (시장×구간×섹터×주체)", not fbad and npair > 0,
        f"{npair * len(AK)}칸 · 최대오차 {fworst:.3e}  " + "; ".join(fbad[:2]))
    chk("G", "원장·flow 의 종목[수] 가 같은가", not nbad,
        f"{npair}칸 · {len(nbad)}건  " + "; ".join(nbad[:2]))

    # ── ④ 화면이 인용하는 실측 주장이 오늘 데이터 안에 있는가 ──
    #
    # ``sw-flow`` 가 밟은 자리다 — "4개 구간 전부 Spearman 1.0000" 이 시장=전체
    # 에서만 잰 값이었다. 원장의 도움말·한계도 범위를 적어 두고 있으므로,
    # 그 범위를 **오늘 데이터로 다시 재서** 벗어나면 말한다.
    obs, empty = comove_table(mo, LV.WINDOWS, LV._MIN_CORR_N, LV.ACTORS)
    chk("G", "모든 시장·구간에서 동시성을 잴 수 있는가(빈 시장 없음)", not empty,
        f"{len(empty)}조합 못 잼: " + " ".join(empty[:4]))
    three = [v for ko in ("개인", "외국인", "기관") for v, *_ in obs.get(ko, [])]
    etc = [v for v, *_ in obs.get("기타법인", [])]
    if not three or not etc:
        chk("G", "동시성 범위를 잴 조합이 남아 있는가", False, "전 조합이 비었다")
        three, etc = three or [0.0], etc or [0.0]
    lo3, hi3 = min(three), max(three)
    # **그려진 글**을 본다(소스 상수가 아니라). 한계 §6·도움말 `널` 의 범위는
    # 이제 연 리포트에서 재서 끼운다(`ledger_view.comove_claims`) — 예전엔
    # "2~43%(09-04)" 를 박아 둬서 27일 중 25일 이 검사가 빨갰다. 상수를 보면
    # 자리표(`{C3}`)만 보이고, 끼우는 배선이 끊겨도 모른다. 폭은 넉넉히 —
    # 줄바꿈이 범위 문자열을 가르면 있는 것을 없다고 한다.
    txt = LV.render_text(LV.Model(P), 400)
    left = [t for t in LV.COMOVE_TOKENS if t in txt]
    chk("G", "도움말·한계에 끼우지 못한 자리표가 없는가", not left, " ".join(left))
    if not left:  # 자리표가 남았으면 범위 글도 없다 — 같은 원인으로 두 번 빨개지지 않는다.
        chk("G", "도움말·한계의 동시성 범위가 오늘 실측을 덮는가(β제거 끈 상태)",
            f"{lo3:.0f}~{hi3:.0f}%" in txt,
            f"실측 개인·외국인·기관 {lo3:.0f}~{hi3:.0f}% · "
            f"기타법인 {min(etc):.0f}~{max(etc):.0f}%")
    # 그리고 판정 자체("셋은 널보다 드물다")가 12조합 전부에서 유지되는가.
    # 이건 **도움말이 인용하는 실측 주장**이다 — 값은 위 검사들이 이미 맞다고 했고, 어긋나면 글이
    # 낡은 것이라 CLAIM 이다(10-13·10-29 가 이것 하나로 VERIFY_FAILED 였다).
    bad = [f"{ko}/{m}/{W} 관측 {v:.0f}% ≥ 널 {n:.0f}%"
           for ko in ("개인", "외국인", "기관") for v, n, m, W in obs.get(ko, [])
           if v >= n - 3]
    claim("G", "개인·외국인·기관은 어느 조합에서도 널보다 음수쌍이 드문가(β제거 끈 상태)",
        not bad,
        f"{len(three)}조합  " + "; ".join(bad[:2]))

    # 잔여의 "최악의 한 칸" — 한계 §5 는 gross 하한을 밝히고 적는다.
    worst_lo, worst_hi = (0.0, None), (0.0, None)
    for i in range(len(dates)):
        for m in P["markets"]:
            for s in secs:
                c = P["flows"][m][s]
                cr = sum(c[k][i] for k in AK)
                cg = sum(abs(c[k][i]) for k in AK)
                if cg <= 0:
                    continue
                hit = (abs(cr) / cg * 100, f"{m}/{s}/{dates[i]} "
                       f"잔여 {-cr:+,.0f}억 / gross {cg:,.0f}억")
                worst_lo = max(worst_lo, hit)
                if cg >= 100:
                    worst_hi = max(worst_hi, hit)
    # 한계 §5 의 숫자는 글에 박힌 실측값이다 — 오늘 최악이 바뀌면 글이 낡은 것(CLAIM).
    claim("G", "한계 §5 의 '최악의 한 칸' 이 하한과 함께 적혀 있는가",
        f"{worst_hi[0]:.1f}%" in txt and "gross 100억" in txt,
        f"gross≥100억 {worst_hi[0]:.1f}% ({worst_hi[1]}) · "
        f"하한 없으면 {worst_lo[0]:.1f}%")


def layer_c(D: dict, html: str) -> None:
    print("\nC. 계산 — 표의 구조와 값이 정합한가")
    # ⚠️ 표가 둘 이상이므로 `rows.forEach` 로 찾으면 안 된다 — 종합 표(ctbl)가
    # 먼저 잡혀 본표(tbl)의 열 수와 어긋난다(실제로 한 번 오탐이 났다).
    # 각 표의 getElementById(...) 부터 t.append(tb) 까지를 구간으로 잡는다.
    def _cells(table_id: str) -> int:
        m = re.search(
            r'getElementById\("' + table_id + r'"\)(.*?)\n\s*t\.append\(tb\)',
            html, re.S)
        return len(re.findall(r"x\.append\(", m.group(1))) if m else -1

    # 본표는 헤더와 셀을 **같은 목록**(`shown`)에서 만든다 — 열 키마다 셀 함수(`cell={k:()=>…}`)가 있어야 한다.
    # 하나라도 빠지면 그 열에서 TypeError 로 표가 반쯤 그려진다. 그려진 DOM 은 W 층이 본다.
    cols = re.search(r"const COLS=\[(.*?)\];", html, re.S)
    col_keys = re.findall(r'^\s*\["(\w+)"', cols.group(1), re.M) if cols else []
    body = re.search(r'getElementById\("tbl"\)(.*?)\n\s*t\.append\(tb\)', html, re.S)
    cell_keys = re.findall(r"^\s*(\w+):\(\)=>", body.group(1), re.M) if body else []
    chk("C", "본표: 열 정의 키 = 셀 함수 키", bool(col_keys) and sorted(col_keys) == sorted(cell_keys),
        f"COLS {len(col_keys)} vs 셀 {len(cell_keys)}  빠짐 {sorted(set(col_keys) - set(cell_keys))}"
        f" 남음 {sorted(set(cell_keys) - set(col_keys))}")

    # 종합 표는 열 수가 창 수만큼 가변이다. 고정 셀과 루프 셀을 나눠 센 뒤,
    # 헤더 배열의 고정 항목 수(섹터·종목·종합G + 통과창)와 맞는지 본다.
    m = re.search(r'getElementById\("ctbl"\)(.*?)\n\s*t\.append\(tb\)', html, re.S)
    if m:
        body_c = m.group(1)
        loop = re.findall(r"windows\.forEach\([^)]*=>\s*x\.append\(", body_c)
        fixed = len(re.findall(r"^\s*x\.append\(", body_c, re.M))
        hdr = re.search(r'\[("섹터".*?)\]\.forEach', body_c, re.S)
        # 헤더 배열 안의 `...C.windows.map(w=>w+"일 G")` 는 **루프 라벨**이므로
        # 고정 항목에서 뺀다 — 안 빼면 그 안의 따옴표 문자열까지 세어 1 이 더 나온다.
        hdr_txt = re.sub(r"\.\.\.[^,]*?\.map\([^)]*\)", "", hdr.group(1)) if hdr else ""
        n_hdr_fixed = len(re.findall(r'"[^"]+"', hdr_txt)) if hdr else -1
        wins = len(next(iter(D.get("combined", {}).values()), {}).get("windows", []))
        chk("C", "종합표: 고정 헤더 = 고정 셀", n_hdr_fixed == fixed,
            f"헤더 고정 {n_hdr_fixed} · 셀 고정 {fixed}")
        chk("C", "종합표: 창 루프 정확히 1개", len(loop) == 1, f"창 {wins}개")
    chk("C", "템플릿 자리표시자 없음", "__DATA__" not in html and "__BASIS__" not in html)

    for key, B in D["blocks"].items():
        rows = B["rows"]
        chk("C", f"{key} 섹터 중복 없음",
            len({r["sector"] for r in rows}) == len(rows))
        bad = [r["sector"] for r in rows
               if any(isinstance(r.get(k), float) and not math.isfinite(r[k])
                      for k in ("inst", "accel", "ret", "exp", "x", "U", "P", "xdot", "xddot", "G"))]
        chk("C", f"{key} Inf/NaN 없음", not bad, ", ".join(bad[:3]))
    # 전 블록 Inf/NaN 합계(위는 블록마다 한 줄씩)
    allbad = [(key, r["sector"]) for key, B in D["blocks"].items() for r in B["rows"]
              if any(isinstance(r.get(k), float) and not math.isfinite(r[k])
                     for k in ("inst", "accel", "ret", "exp", "x", "U", "P", "xdot", "xddot", "G"))]
    chk("C", "전 블록 Inf/NaN 없음", not allbad, f"{len(allbad)}건")


# ─────────────────────────────────────────────── F. 독립 재계산

#: 페이로드가 반올림하는 자릿수에서 나오는 허용오차.
#: 유량·거래대금은 0.01억(round(...,2)), 시총·가중은 0.1억, 수익률은 0.001%.
#: **상대오차를 쓰지 않는다** — 섹터 합계가 0 근처인 칸(섬유/의류 +2.0억)에서
#: 상대오차는 무의미하게 커진다. 반올림 폭에서 유도한 절대오차가 맞는 축이다.
TOL_FLOW = 0.011
TOL_CAP = 0.11
TOL_RET = 0.0011


def layer_f(P: dict, db: str | None) -> None:
    """F. **독립 재계산** — DB 원자료에서 페이로드를 처음부터 다시 만든다.

    A~E 는 전부 *페이로드 안의 값끼리* 정합한지를 본다. 그래서 `sector_flow.py`
    가 임펄스를 통째로 잘못 계산해도(예: 수량을 금액으로 착각, `flu_rt` 의 bp
    단위를 놓침 — 둘 다 이 저장소에서 실제로 사고를 낸 함정이다) A~E 는 전부
    초록이다. 파생층이 그 틀린 값 위에서 일관될 뿐이기 때문이다.

    그래서 여기서는 **`scripts/` 의 함수를 하나도 부르지 않고** SQL 로 다시
    집계해 대조한다. 같은 버그를 두 번 통과시키지 않으려면 경로가 달라야 한다.

    ⚠️ 함정을 재현 쪽에서도 명시한다.
      · `supply_demand` 의 순매매는 **수량**이다 — 금액은 그날 종가를 곱해 만든다.
      · 수익률은 `flu_rt` 가 아니라 조정 종가 비다. 시총은 수정주가가 아니라 원 종가 × 주식수다.
    """
    print("\nF. 독립 재계산 — DB 원자료에서 다시 만든 값과 같은가")
    if not db:
        print("       (--db-check 없음 — 건너뛴다)")
        return
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "src"))
    from sectorflow.storage import connect
    con = connect(db)
    pg = con.__class__.__module__.startswith("psycopg2")
    ph = "%s" if pg else "?"
    cur = con.cursor()
    dates = P["dates"]
    d0, d1 = dates[0], dates[-1]
    n = len(dates)
    # 섹터 덮어쓰기는 producer 와 **같은 표**다(sectorflow/sector_override.py) — 표는 사실이지 계산이 아니다.
    from sectorflow.sector_override import sector_sql
    SEC = sector_sql("st.code", "st.sector")
    SRC = f"s.source={ph} AND s.date BETWEEN {ph} AND {ph}"
    args = ("kiwoom", d0, d1)

    def cmp_abs(name, pairs, tol, min_n=1):
        """(라벨, 페이로드값, 재계산값) 을 절대오차로 본다.

        **비교한 것이 없으면 실패다.** 예전엔 쌍이 0개여도 ``0/0 불일치`` 로 초록이었다 —
        페이로드가 비거나 SQL 이 아무것도 못 찾으면 이 층이 본 것이 없는데 통과로 읽힌다.
        ``min_n`` 은 양쪽 다 값이 있고 **둘 중 하나는 0 이 아닌** 쌍의 최소 개수다(전부 0 끼리의
        비교는 "DB 도 비었고 페이로드도 비었다" 와 구분되지 않는다).
        """
        bad, worst, live = [], 0.0, 0
        for label, x, y in pairs:
            if x is None or y is None:
                if (x is None) != (y is None):
                    bad.append(f"{label} {x} vs {y}")
                continue
            if x or y:
                live += 1
            worst = max(worst, abs(x - y))
            if abs(x - y) > tol:
                bad.append(f"{label} {x} vs {y}")
        chk("F", name, not bad and live >= max(1, min_n),
            f"{len(bad)}/{len(pairs)} 불일치 · 0 아닌 비교 {live}쌍(최소 {max(1, min_n)}) · "
            f"최대오차 {worst:.3e}" + ("  " + "; ".join(bad[:2]) if bad else ""))

    # ── ① 유량 (시장,섹터,날짜) × 5값 ──
    ACT = (("indiv", "individual"), ("forgn", "foreign_"),
           ("inst", "institution"), ("etc", "etc_corp"))
    sel = ", ".join([f"sum(s.{c} * 1.0 * abs(s.close))" for _k, c in ACT]
                    + ["sum(s.acc_trde_qty * 1.0 * abs(s.close))"])
    cur.execute(f"SELECT st.market, {SEC}, s.date, {sel} "
                f"FROM supply_demand s JOIN stocks st ON st.code=s.code "
                f"WHERE {SRC} GROUP BY 1,2,3", args)
    keys = [k for k, _ in ACT] + ["tv"]
    got = {}
    for row in cur.fetchall():
        for j, k in enumerate(keys):
            got[(row[0], row[1], str(row[2]), k)] = float(row[3 + j]) / 1e8
    pairs = []
    for m in P["markets"]:
        for s in P["sectors"]:
            for k in keys:
                arr = P["flows"][m][s][k]
                for i, v in enumerate(arr):
                    pairs.append((f"{m}/{s}/{k}/{dates[i]}", v,
                                  got.get((m, s, dates[i], k), 0.0)))
    # 거래대금(tv)은 거래가 있는 날이면 0 이 아니다 — 시장마다 날마다 적어도 한 칸.
    cmp_abs("유량 일별 (시장×섹터×5값×거래일)", pairs, TOL_FLOW, min_n=len(P["markets"]) * n)

    # ── ② 기관 세부 ──
    det = sorted({k for m in P["detail"] for s in P["detail"][m]
                  for k in P["detail"][m][s]})
    if det:
        sel = ", ".join(f"sum(s.{c} * 1.0 * abs(s.close))" for c in det)
        cur.execute(f"SELECT st.market, {SEC}, s.date, {sel} "
                    f"FROM supply_demand s JOIN stocks st ON st.code=s.code "
                    f"WHERE {SRC} GROUP BY 1,2,3", args)
        got = {}
        for row in cur.fetchall():
            for j, k in enumerate(det):
                got[(row[0], row[1], str(row[2]), k)] = float(row[3 + j]) / 1e8
        pairs = []
        for m in P["markets"]:
            for s in P["sectors"]:
                for k in det:
                    for i, v in enumerate(P["detail"][m][s][k]):
                        pairs.append((f"{m}/{s}/{k}/{dates[i]}", v,
                                      got.get((m, s, dates[i], k), 0.0)))
        cmp_abs("기관 세부 일별", pairs, TOL_FLOW, min_n=n)

    # ── ③·④ 시총과 수익률의 재료 — 리포트 코드(`storage.market_cap_asof_bulk`·`adjusted_returns`)를
    #    부르지 않고 원자료에서 따로 만든다.
    #    시총 = 그날 **원 종가**(supply_demand.close, 없으면 daily_bars_raw) × 그날 주식수.
    #    ⚠️ daily_bars 가 아니다 — 키움 수정주가라 뒤에 병합이 있으면 과거 시총이 배수로 커진다.
    #    주식수는 date 이하 최신 행, 그 뒤 분할(8)·병합(16)·감자(64)는 1/(1+upd_rt/100) 배로 다시 센다.
    #    수익률 = daily_bars_adjusted 종가 ÷ 그 종목 직전 행 종가 − 1. ⚠️ flu_rt 가 아니다
    #    (09-14 부터 분자는 통합 종가·기준가는 정규장 종가라 이어 곱할 수 없다).
    import bisect
    cur.execute(f"SELECT s.code, s.date, abs(s.close) FROM supply_demand s "
                f"JOIN stocks st ON st.code=s.code WHERE {SRC}", args)
    sd_rows = sorted((c, str(dt), float(v) if v is not None else None) for c, dt, v in cur.fetchall())
    px = {(c, d): v for c, d, v in sd_rows}
    codes_win = sorted({c for c, _d, _v in sd_rows})
    cur.execute(f"SELECT code, close FROM daily_bars_raw WHERE date={ph}", (d1,))
    raw_d1 = {c: float(v) for c, v in cur.fetchall() if v is not None}
    cur.execute("SELECT code, date, shares_outstanding FROM shares_outstanding_history")
    sh_hist: dict = {}
    for c, dt, v in cur.fetchall():
        sh_hist.setdefault(c, []).append((str(dt), v))
    for v in sh_hist.values():
        v.sort()
    cur.execute("SELECT code, date, upd_stkpc_tp, upd_rt FROM corporate_actions")
    evs: dict = {}
    rights_ex: dict = {}
    for c, dt, tp, rt in cur.fetchall():
        try:
            tpi, r = int(tp), float(rt)
        except (TypeError, ValueError):
            continue
        if tpi & (8 | 16 | 64) and r != 0 and r > -100:
            evs.setdefault(c, []).append((str(dt), r))
        if tpi & 256:
            rights_ex.setdefault(c, []).append((str(dt), r))

    def shares_at(c: str, d: str):
        hist = sh_hist.get(c) or []
        k = bisect.bisect_right(hist, (d, float("inf"))) - 1
        if k < 0 or not hist[k][1] or hist[k][1] <= 0:
            return None
        s_date, v = hist[k][0], float(hist[k][1])
        for e_date, r in evs.get(c, []):
            if s_date < e_date <= d:
                v /= 1.0 + r / 100.0
        return v

    def cap_at(c: str, d: str, price):
        sh = shares_at(c, d)
        return None if price is None or sh is None else price * sh

    meta = {}
    cur.execute(f"SELECT code, market, {SEC.replace('st.', '')} FROM stocks")
    for c, m, sec in cur.fetchall():
        meta[c] = (m, sec)

    # ── ③ 섹터 시총(구간말) ──
    got: dict = {}
    for c in codes_win:
        v = cap_at(c, d1, px.get((c, d1)) or raw_d1.get(c))
        if v is not None and c in meta:
            got[meta[c]] = got.get(meta[c], 0.0) + v / 1e8
    pairs = [(f"{m}/{s}", P["cap"].get(m, {}).get(s), got.get((m, s)))
             for m in P["markets"] for s in P["sectors"]
             if P["cap"].get(m, {}).get(s) is not None or (m, s) in got]
    cmp_abs("섹터 시총 (구간말, 원 종가 × 주식수)", pairs, TOL_CAP, min_n=len(P["markets"]))

    # ── ③-2 종목 시총(구간말) — 시총대비의 분모. 섹터 합이 맞아도 종목 칸은 따로 틀릴 수 있다(리뷰 4차 D1: ×1.1 변이가 통과). ──
    pairs = []
    for c, r in P["names"].items():
        v = cap_at(c, d1, px.get((c, d1)) or raw_d1.get(c))
        pairs.append((c, r.get("cap"), None if v is None else v / 1e8))
    cmp_abs("종목 시총 (구간말, 원 종가 × 주식수)", pairs, TOL_CAP, min_n=max(1, len(P["names"]) // 2))

    # ── ③-3 월별 섹터 시총 — 웹 흐름 페이지가 임의 구간 가속의 분모로 쓴다. 각 월의 마지막 거래일 값의 **합**이다. ──
    #    W 층(`_w_viewer_expect`)은 이 값을 읽어 비교하므로 독립이 아니다 — 평균·월초 값이어도 초록이었다(11월 금융 78배).
    month_end: dict = {}
    for d in dates:
        month_end[d[:7]] = d
    ends = sorted(set(month_end.values()))
    cur.execute(f"SELECT code, date, close FROM daily_bars_raw WHERE date IN ({','.join([ph] * len(ends))})", ends)
    raw_ends = {(c, str(dt)): float(v) for c, dt, v in cur.fetchall() if v is not None}
    got = {}
    for c in codes_win:
        if c not in meta:
            continue
        for ym, d in month_end.items():
            v = cap_at(c, d, px.get((c, d)) or raw_ends.get((c, d)))
            if v is not None:
                key = (*meta[c], ym)
                got[key] = got.get(key, 0.0) + v / 1e8
    cbm = P.get("cap_by_month") or {}
    keys = {(m, s, ym) for m, ss in cbm.items() for s, mm in ss.items() for ym in mm} | set(got)
    cmp_abs("월별 섹터 시총 (각 월 마지막 거래일의 합)",
            [(f"{m}/{s}/{ym}", ((cbm.get(m) or {}).get(s) or {}).get(ym), got.get((m, s, ym))) for m, s, ym in sorted(keys)],
            TOL_CAP, min_n=len(P["markets"]) * len(month_end))
    # 마지막 달의 마지막 거래일은 곧 기준일이다 — 그 칸과 구간말 시총은 같은 수여야 한다. 표(가속)와 흐름 페이지가 같은 분모를 쓴다는 약속.
    ym = d1[:7]
    bad = [f"{m}/{s} 월별 {((cbm.get(m) or {}).get(s) or {}).get(ym)} vs 구간말 {v}"
           for m, ss in (P.get("cap") or {}).items() for s, v in ss.items()
           if abs((((cbm.get(m) or {}).get(s) or {}).get(ym) or 0.0) - v) > TOL_CAP]
    chk("F", "월별 시총의 마지막 달 = 구간말 시총", not bad and bool(P.get("cap")),
        f"{len(bad)}건  " + "; ".join(bad[:2]))

    # ── ③-4 권리락 신주 미반영 표지 — 값이 아니라 **표지**의 대조. 규칙은 producer 상수를 읽지 않고 여기 적는다. ──
    want_p: dict = {}
    d1_day = date.fromisoformat(d1)
    for c in P["names"]:
        for e_date, r in sorted(rights_ex.get(c, [])):
            if e_date > d1 or r > VF_RIGHTS_RT or (d1_day - date.fromisoformat(e_date)).days > VF_RIGHTS_DAYS:
                continue
            hist = [x for x in sh_hist.get(c, []) if x[0] <= d1 and x[1] and x[1] > 0]
            before = [v for dt, v in hist if dt < e_date]
            after = [v for dt, v in hist if dt >= e_date]
            if before and (not after or after[-1] < before[-1] * VF_RIGHTS_CAUGHT_UP):
                want_p[c] = (e_date, r)
    got_p = {c: (r["cap_pending"].get("date"), r["cap_pending"].get("rt")) for c, r in P["names"].items() if r.get("cap_pending")}
    bad = [f"{c} 페이로드 {got_p.get(c)} vs DB {want_p.get(c)}" for c in sorted(set(want_p) | set(got_p))
           if (c in want_p) != (c in got_p) or got_p[c][0] != want_p[c][0] or abs(got_p[c][1] - want_p[c][1]) > 1e-6]
    chk("F", "권리락 신주 미반영 표지 = DB 재계산(종목·날짜·배율)", not bad,
        f"{len(want_p)}종목" + ("  " + "; ".join(bad[:3]) if bad else ""))

    # ── ④ 섹터 일별 수익률 — **전일** 시총 가중, 조정 종가 비 ──
    cur.execute(f"SELECT DISTINCT date FROM supply_demand WHERE date < {ph} "
                f"ORDER BY date DESC LIMIT 1", (d0,))
    prev = cur.fetchone()
    a0 = str(prev[0]) if prev else d0
    cur.execute(f"SELECT code, date, close FROM daily_bars_adjusted "
                f"WHERE date BETWEEN {ph} AND {ph} AND close > 0", (a0, d1))
    adj_rows: dict = {}
    for c, dt, v in cur.fetchall():
        adj_rows.setdefault(c, []).append((str(dt), float(v)))
    aret: dict = {}
    for c, rows_c in adj_rows.items():
        rows_c.sort()
        for (_d_prev, p0), (d_cur, p1) in zip(rows_c, rows_c[1:]):
            aret[(c, d_cur)] = (p1 / p0 - 1.0) * 100.0
    num: dict = {}
    den: dict = {}
    last_cap: dict = {}
    mcap: dict = {}                                 # (시장, 날짜) → 시총 합[억] — 시총/지수 비 점검에 쓴다
    for c, d, price in sd_rows:                     # 코드·날짜 순 — 직전 행이 곧 전일
        cap_lag = last_cap.get(c)
        last_cap[c] = cap_at(c, d, price)
        if last_cap[c] is not None and c in meta:
            mk = (meta[c][0], d)
            mcap[mk] = mcap.get(mk, 0.0) + last_cap[c] / 1e8
        r = aret.get((c, d))
        if cap_lag is None or r is None or c not in meta:
            continue
        key = (*meta[c], d)
        num[key] = num.get(key, 0.0) + r * cap_lag / 1e8
        den[key] = den.get(key, 0.0) + cap_lag / 1e8
    pairs_r, pairs_w = [], []
    for m in P["markets"]:
        for s in P["sectors"]:
            for i, d in enumerate(dates):
                w = den.get((m, s, d), 0.0)
                pairs_r.append((f"{m}/{s}/{d}", P["ret"][m][s][i], num.get((m, s, d), 0.0) / w if w else 0.0))
                pairs_w.append((f"{m}/{s}/{d}", P["retw"][m][s][i], w))
    # 첫날은 전일 시총이 없어 가중이 비는 섹터가 있다 — 둘째 날부터 시장마다 하루 한 칸은 산다.
    cmp_abs("섹터 일별 수익률 (전일 시총 가중, 조정 종가 비)", pairs_r, TOL_RET,
            min_n=len(P["markets"]) * (n - 1))
    cmp_abs("섹터 일별 가중합", pairs_w, TOL_CAP, min_n=len(P["markets"]) * (n - 1))

    # ── ⑤ 종목 창별 집계 ──
    wins = sorted({int(w) for r in P["names"].values() for w in (r.get("win") or {})})
    WK = [("inst", "institution"), ("forgn", "foreign_"), ("indiv", "individual"),
          ("etc", "etc_corp"), ("tv", "acc_trde_qty"),
          ("invtrt", "invtrt"), ("penfnd_etc", "penfnd_etc")]
    for win in wins:
        if win > n:
            continue
        start = dates[n - win]
        sel = ", ".join(f"sum(s.{c} * 1.0 * abs(s.close))" for _k, c in WK)
        cur.execute(f"SELECT s.code, {sel} FROM supply_demand s "
                    f"JOIN stocks st ON st.code=s.code WHERE {SRC} GROUP BY 1",
                    ("kiwoom", start, d1))
        got = {r[0]: [float(v) / 1e8 for v in r[1:]] for r in cur.fetchall()}
        pairs = []
        for c, r in P["names"].items():
            w = (r.get("win") or {}).get(str(win))
            if not w:
                continue
            g = got.get(c)
            for j, (k, _col) in enumerate(WK):
                pairs.append((f"{c}/{win}/{k}", w.get(k),
                              None if g is None else g[j]))
        cmp_abs(f"종목 {win}일 집계", pairs, TOL_CAP,
                min_n=sum(1 for r in P["names"].values() if (r.get("win") or {}).get(str(win))))

    # 종목 구간 수익률 — 일별 조정 종가 수익률의 **기하** 누적(결측일은 0, 수급 행이 있는 날만).
    fr = {(c, d): aret[(c, d)] for c, d, _v in sd_rows if (c, d) in aret}
    pairs = []
    for win in wins:
        if win > n:
            continue
        span = dates[n - win:]
        for c, r in P["names"].items():
            w = (r.get("win") or {}).get(str(win))
            if not w or w.get("ret") is None:
                continue
            acc = 1.0
            for d in span:
                acc *= 1.0 + fr.get((c, d), 0.0) / 100.0
            pairs.append((f"{c}/{win}/ret", w["ret"], (acc - 1.0) * 100.0))
    cmp_abs("종목 구간 수익률 (기하누적)", pairs, 0.011, min_n=len(wins))

    # ── ⑥ 유니버스의 **폭** — 이 화면이 못 보는 종목이 몇 개인가. ──
    #
    # 이 리포트는 개인·기관세부가 키움 소스에만 있어 `sources=("kiwoom",)` 로
    # 유니버스를 좁히고, `stocks`(현재 상장 마스터)와 INNER JOIN 한다. 그래서
    # 구간 안에 거래가 있었지만 지금은 마스터에 없는 종목이 통째로 빠진다.
    # 이건 심사 성적이 아니라 **관측 화면의 한계**라 실패로 만들지 않는다 —
    # 다만 조용히 두지 않는다. `docs/GUARDRAILS.md` §4.1 이 다루는 생존편향의
    # 거울상이고, 폭이 갑자기 커지면 수집 쪽에 문제가 생겼다는 신호다.
    cur.execute(f"SELECT count(DISTINCT s.code) FROM supply_demand s "
                f"WHERE s.date BETWEEN {ph} AND {ph} AND s.code NOT IN "
                f"(SELECT code FROM stocks)", (d0, d1))
    outside = cur.fetchone()[0]
    inside = len(P["names"])
    chk("F", "유니버스 밖(마스터에 없는) 종목 비중 < 20%",
        outside < 0.2 * max(inside, 1),
        f"밖 {outside}종목 / 안 {inside}종목 = {outside/max(inside,1)*100:.1f}%")

    # ── ⑦ 원장 화면의 구간 합계 — **SQL 로 다시 집계**해 대조 ──
    #
    # G 층은 페이로드 안에서만 정합을 본다(화면 ↔ 페이로드). 페이로드 자체가
    # 틀리면 둘 다 같은 값으로 초록이다. 그래서 원장이 실제로 보여주는 수 —
    # (시장, 섹터, 구간, 주체)의 **구간 누적 순매수** — 를 DB 에서 처음부터
    # 다시 만든다. ①이 일별을 보고 여기는 화면이 쓰는 **구간 합**을 본다.
    # 시장 '전체' 도 화면과 같은 방식(두 시장의 합)으로 만들어 대조한다.
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "src"))
    from sectorflow.tui import ledger_view as LV
    mo = LV.Model(P)
    for W in LV.WINDOWS:
        if W > n:
            continue
        start = dates[n - W]
        sel = ", ".join(f"sum(s.{c} * 1.0 * abs(s.close))" for _k, c in ACT)
        cur.execute(f"SELECT st.market, {SEC}, {sel} FROM supply_demand s "
                    f"JOIN stocks st ON st.code=s.code "
                    f"WHERE s.source={ph} AND s.date BETWEEN {ph} AND {ph} "
                    f"GROUP BY 1,2", ("kiwoom", start, d1))
        got = {}
        for row in cur.fetchall():
            for j, (k, _c) in enumerate(ACT):
                got[(row[0], row[1], k)] = float(row[2 + j]) / 1e8
        mo.wi = LV.WINDOWS.index(W)
        pairs = []
        for mi, mk in enumerate(mo.markets):
            mo.mi = mi
            mkts = P["markets"] if mk == "전체" else [mk]
            for r in mo.rows():
                for k, _c in ACT:
                    pairs.append((f"{mk}/{r['sector']}/{k}", r[k],
                                  sum(got.get((m, r["sector"], k), 0.0)
                                      for m in mkts)))
        # 페이로드가 일별로 0.01억까지 반올림하므로 W 일을 더하면 그만큼 쌓인다.
        cmp_abs(f"원장 {W}일 구간 합계 (시장×섹터×주체)", pairs, TOL_FLOW * W,
                min_n=len(mo.markets))

    # ── ⑧ 한계 §7(생존편향)이 주장하는 **모양**이 오늘도 참인가 ──
    #
    # §7 은 세 가지를 말한다: 빠진 폭이 작다 · **거래소가 코스닥보다 크다** ·
    # 종목 수는 코스닥이 많다. 퍼센트 자체는 날마다 바뀌므로 검사하지 않고
    # (그러면 매일 빨개진다) **뒤집히면 안 되는 것**만 본다. 값은 detail 로 남긴다.
    # 셋 다 글이 인용하는 실측 주장이라 어긋나면 CLAIM 이다(수치 결함이 아니다).
    # 시장은 `stocks` 에 없는 종목이라 `delisted_stocks` 에서 가져온다.
    cur.execute(f"""SELECT d.market, count(DISTINCT s.code),
          sum(s.acc_trde_qty*1.0*abs(s.close)), sum(abs(s.institution*1.0*abs(s.close)))
        FROM supply_demand s LEFT JOIN delisted_stocks d ON d.code=s.code
        WHERE s.date BETWEEN {ph} AND {ph}
          AND s.code NOT IN (SELECT code FROM stocks) GROUP BY 1""", (d0, d1))
    out = {("거래소" if r[0] == "유가증권" else r[0]):
           (int(r[1]), float(r[2] or 0), float(r[3] or 0)) for r in cur.fetchall()}
    cur.execute(f"SELECT st.market, sum(s.acc_trde_qty*1.0*abs(s.close)), "
                f"sum(abs(s.institution*1.0*abs(s.close))) "
                f"FROM supply_demand s JOIN stocks st ON st.code=s.code "
                f"WHERE s.source={ph} AND s.date BETWEEN {ph} AND {ph} GROUP BY 1", args)
    ins = {r[0]: (float(r[1]), float(r[2])) for r in cur.fetchall()}
    share = {m: out[m][1] / ins[m][0] * 100 for m in out if m in ins}
    tot_tv = sum(v[1] for v in out.values()) / sum(v[0] for v in ins.values()) * 100
    _f_universe_gap(cur, ph, d0, d1)
    tot_inst = sum(v[2] for v in out.values()) / sum(v[1] for v in ins.values()) * 100
    detail = (f"상장폐지로 빠진 거래대금 {tot_tv:.3f}%(유니버스 대비 — 우선주·리츠 등 유니버스 밖은 별도 항목) · 기관 gross {tot_inst:.3f}% · "
              + " · ".join(f"{m} {share[m]:.3f}%({out[m][0]}종목)" for m in sorted(share)))
    claim("F", "한계 §7: 상장폐지로 빠진 거래대금 비중이 1% 미만인가", tot_tv < 1.0, detail)
    claim("F", "한계 §7: 빠진 금액은 코스닥이 아니라 거래소에서 큰가",
        share.get("거래소", 0) > share.get("코스닥", 0),
        f"거래소 {share.get('거래소', 0):.3f}% vs 코스닥 {share.get('코스닥', 0):.3f}%")
    claim("F", "한계 §7: 빠진 **종목 수**는 코스닥이 많은가",
        out.get("코스닥", (0,))[0] > out.get("거래소", (0,))[0],
        f"코스닥 {out.get('코스닥', (0,))[0]}종목 vs 거래소 {out.get('거래소', (0,))[0]}종목")
    _f_today_context(P, cur, ph, meta, px, cap_at)
    _f_cap_index_ratio(P, cur, ph, mcap)
    # 섹터 덮어쓰기는 마스터가 비었을 때만 쓴다. 마스터가 채워졌으면 표의 그 줄은 이제 할 일이 없다 — 경고(CLAIM)로 알린다.
    from sectorflow.sector_override import SECTOR_OVERRIDE
    codes = sorted(SECTOR_OVERRIDE)
    if codes:
        cur.execute(f"SELECT code, sector FROM stocks WHERE code IN ({','.join([ph] * len(codes))})", codes)
        filled = {c: s for c, s in cur.fetchall() if (s or "").strip()}
        claim("F", "섹터 덮어쓰기 표의 종목이 마스터에서 아직 비어 있는가(채워졌으면 표에서 지운다)", not filled,
              "; ".join(f"{c} 마스터 '{s}' (표 '{SECTOR_OVERRIDE[c]}')" for c, s in sorted(filled.items())))
    con.close()


#: KRX 종합지수 코드 — producer 상수를 읽지 않고 여기 적는다.
VF_KRX = (("거래소", "001"), ("코스닥", "101"))
#: 유니버스 밖 거래대금의 몫 — 문서가 "거래소 3.6%" 라 적는 값의 상한(시장별, %). 넘으면 그 글이 낡았다.
VF_UNIVERSE_GAP_MAX = 6.0


def _f_universe_gap(cur, ph: str, d0: str, d1: str) -> None:
    """유니버스 밖 거래대금 — **KRX 종합 거래대금 대비**. ``stocks`` 는 kind A(보통주)뿐이라 우선주·리츠 등은 DB 어느 표에도
    없다. 그래서 ``supply_demand`` 안에서 세면(위의 상장폐지 몫) 분모부터 좁다. ``sector_index`` 의 종합 행은 그 종목들까지 담은
    시장 전체라, 두 값의 차가 빠진 몫이다(2026-11-06 거래소 28.98조 − 27.94조 = 1.04조, 네이버 금융 시세 139종목 합과 일치).
    """
    try:
        cur.execute(f"SELECT code, sum(trade_value) FROM sector_index "
                    f"WHERE code IN ('001','101') AND date BETWEEN {ph} AND {ph} GROUP BY 1", (d0, d1))
        whole = {c: float(v or 0) for c, v in cur.fetchall()}
        cur.execute(f"SELECT st.market, sum(b.trade_value) FROM daily_bars b JOIN stocks st ON st.code=b.code "
                    f"WHERE b.date BETWEEN {ph} AND {ph} AND b.date IN "
                    f"(SELECT date FROM sector_index WHERE code='001' AND date BETWEEN {ph} AND {ph}) GROUP BY 1",
                    (d0, d1, d0, d1))
        inside = {m: float(v or 0) for m, v in cur.fetchall()}
    except Exception as e:  # noqa: BLE001 — 옛 스키마·시험 DB(거래대금 열 없음)는 잴 수 없다는 사실만 남긴다
        try:
            cur.connection.rollback()
        except Exception:  # noqa: BLE001
            pass
        print(f"  SKIP 유니버스 밖 거래대금 — 종합 거래대금을 못 읽었다({type(e).__name__})")
        return
    gaps = {m: (whole[c] - inside.get(m, 0.0)) / whole[c] * 100 for m, c in VF_KRX if whole.get(c)}
    if not gaps:
        print("  SKIP 유니버스 밖 거래대금 — 그 구간 종합 거래대금이 없다")
        return
    claim("F", f"한계: 유니버스 밖(우선주·리츠 등) 거래대금이 KRX 종합 대비 시장마다 {VF_UNIVERSE_GAP_MAX:.0f}% 미만인가",
          all(g < VF_UNIVERSE_GAP_MAX for g in gaps.values()),
          " · ".join(f"{m} {g:.2f}%" for m, g in gaps.items()) + " (KRX 종합 − 유니버스 합, 구간 합계 대비)")
#: 정규장 종가로 다시 잰 자체 바구니 vs KRX 지수 — 허용 차이[%p]. 바구니에 우선주가 들어 있고(코스피는 보통주)
#: 상장·폐지 시점이 달라 0 은 아니다. 실측 11-03~11-05: 0.01~0.04%p.
VF_REGULAR_TOL = 0.1


#: 시총/지수 비 하루 변화의 경고 문턱[%]. 2025-10~2026-11 실측: 주간 스냅샷 시작(07-20) 뒤 최대 2.47%(10-20 거래소, 통합 종가 ↔ 정규장
#: 지수의 차이), 일괄 갱신일 3.52%(07-09 코스닥)·10.49%(01-02 코스닥). 이 사이에 둔다.
VF_CAP_INDEX_JUMP = 3.0
VF_CAP_INDEX_MARK = "시총/지수 비 하루 변화"
#: 권리락 신주 미반영 표지의 규칙 — ``storage.rights_pending`` 과 같은 정의를 **상수를 읽지 않고** 적는다.
VF_RIGHTS_RT, VF_RIGHTS_DAYS, VF_RIGHTS_CAUGHT_UP = -10.0, 100, 1.05


#: 외부 참값(정규장 종가 대조)을 **연달아** SKIP 한 확정 리포트가 이 수에 이르면 WARN(실패 아님). 한 번의 SKIP 은
#: 정상이다(다음날 수급이 들어와야 채워지는 뷰) — 이어지면 대조가 며칠째 안 돈 것인데 예전엔 아무 말이 없었다(리뷰 3차 D1-m3).
VF_SKIP_WARN = 3
#: 연속 SKIP 을 셀 때 거슬러 보는 옆 기준일 폴더 수(이 리포트 제외).
VF_SKIP_SCAN = 10
#: VERIFY.txt 에 남는 표지 — 옆 폴더를 셀 때와 ``freshness_check.py`` 가 **같은 글**을 찾는다(``tests/test_freshness_check.py``).
VF_SKIP_MARK = "SKIP 정규장 종가 대조"
VF_SKIP_STREAK_MARK = "외부 참값 연속 SKIP"
#: 검증 중인 리포트 폴더 — ``main`` 이 채운다. 옆 기준일 폴더의 VERIFY.txt 를 볼 때만 쓴다.
REPORT_DIR: "str | None" = None
_DAY_DIR = re.compile(r"\d{4}-\d{2}-\d{2}")


def external_skip_streak(folder: "str | None", asof: str, skip_mark: str = VF_SKIP_MARK,
                         done_mark: str = "외부 참값:") -> int:
    """``asof`` 앞(미만) 기준일 폴더들이 외부 참값 대조를 **연달아** SKIP 한 수 — 최근부터 거슬러 센다.

    VERIFY.txt 에 SKIP 표지가 있으면 센다, 대조 줄(``외부 참값:``)이 있으면 멈춘다. 둘 다 없는 폴더(잠정만 남은 날·
    DB 대조 없이 돈 검증·리포트가 없는 날)는 대조를 시도하지 않은 것이라 세지도 끊지도 않는다.
    공표치 대조(:func:`_vt_krx_truth`)도 표지만 바꿔 같은 규칙으로 센다.
    """
    if not folder:
        return 0
    root = os.path.dirname(os.path.abspath(folder.rstrip("/")))
    try:
        names = sorted((x for x in os.listdir(root) if _DAY_DIR.fullmatch(x) and x < asof), reverse=True)
    except OSError:
        return 0
    n = 0
    for name in names[:VF_SKIP_SCAN]:
        try:
            with open(os.path.join(root, name, "VERIFY.txt"), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        if skip_mark in text:
            n += 1
        elif done_mark in text:
            break
    return n


def _external_skip(P: dict, why: str) -> None:
    """외부 참값 대조를 건너뛴다 — 사유와 **연속 횟수**를 남기고, :data:`VF_SKIP_WARN` 회째부터 WARN."""
    print(f"       {VF_SKIP_MARK} — {why}")
    streak = 1 + external_skip_streak(REPORT_DIR, P["dates"][-1])
    msg = f"{VF_SKIP_STREAK_MARK} {streak}회(이 리포트 포함, 확정 리포트만 센다)"
    if streak >= VF_SKIP_WARN:
        warn("F", msg, "정규장 종가(regular_close)가 며칠째 준비 안 됐다 — 바구니 ≈ KRX 지수 대조가 그동안 안 돌았다. "
                       "quant-airflow 의 regular_close 를 보라")
    else:
        print(f"       {msg}")


def cap_index_jumps(caps: dict, levels: dict) -> list:
    """(날짜, 전 거래일 대비 시총/지수 비 변화[%]) — 시총과 지수가 다 있는 날끼리. ``caps``·``levels`` 는 {날짜: 값}."""
    days = sorted(d for d, v in caps.items() if v and levels.get(d))
    ratio = [caps[d] / levels[d] for d in days]
    return [(d, (b / a - 1.0) * 100.0) for d, a, b in zip(days[1:], ratio, ratio[1:])]


def _f_cap_index_ratio(P: dict, cur, ph: str, mcap: dict) -> None:
    """시장 시총 ÷ KRX 지수의 하루 변화 — 가격과 무관한 **주식수 쪽 점프**를 본다.

    둘이 같이 움직이므로 비는 거의 평평하다. 비가 하루에 튀면 시총의 주식수 쪽이 한꺼번에 바뀐 것이다 —
    2026-01-02(113종목 낡은 주식수 일괄 갱신) 코스닥 +10.5%, 07-09(주간 스냅샷 시작) +3.5%. 값이 틀렸다는 뜻은
    아니므로(그 스냅샷이 사실이다) 실패가 아니라 WARN 이고, 창 안의 옛 점프로 매일 울리지 않게 **기준일**만 경고한다.
    독립: 시총은 F 층이 원자료에서 다시 만든 것, 지수는 ``sector_index`` 다.
    """
    d1 = P["dates"][-1]
    for m, code in VF_KRX:
        if m not in P["markets"]:
            continue
        cur.execute(f"SELECT date, close FROM sector_index WHERE code={ph} AND date BETWEEN {ph} AND {ph}",
                    (code, P["dates"][0], d1))
        levels = {str(d): float(v) for d, v in cur.fetchall() if v}
        jumps = cap_index_jumps({d: v for (mk, d), v in mcap.items() if mk == m}, levels)
        if not jumps or jumps[-1][0] != d1:
            print(f"       SKIP 시총/지수 비 {m} — {d1} 지수가 아직 없다")
            continue
        top = max(jumps, key=lambda t: abs(t[1]))
        detail = f"{m} {d1} {jumps[-1][1]:+.2f}% (문턱 ±{VF_CAP_INDEX_JUMP}%) · 창 안 최대 {top[0]} {top[1]:+.2f}%"
        if abs(jumps[-1][1]) > VF_CAP_INDEX_JUMP:
            warn("F", VF_CAP_INDEX_MARK, detail + " — 주식수가 한꺼번에 바뀌었는지 본다(docs/formulas.md §7.7)")
        else:
            print(f"       {VF_CAP_INDEX_MARK}: {detail}")


#: 대량 체결 의심의 문턱 — producer(sector_flow.BLOCK_*)를 읽지 않고 따로 적는다.
VF_BLOCK_OFF_MIN = 0.5
VF_BLOCK_SHARE_MIN = 0.01
VF_BLOCK_MIN_COVER = 0.95


def _f_block_suspects(P: dict, cur, ph: str, meta: dict, d1: str, bars: dict) -> None:
    """대량 체결 의심 = DB 재계산 — 분봉 합·수량·종가에서 목록과 skip 을 다시 만든다. 옛 리포트(블록 없음)는 침묵."""
    got = (P.get("today") or {}).get("block_suspects")
    if got is None:
        return
    from datetime import date, timedelta  # noqa: PLC0415
    nxt = (date.fromisoformat(d1) + timedelta(days=1)).isoformat()
    try:
        cur.execute(f"SELECT code, SUM(volume) FROM minute_bars WHERE ts >= {ph} AND ts < {ph} GROUP BY code", (d1, nxt))
        vm = {c: float(v) for c, v in cur.fetchall() if v is not None}
    except Exception:  # noqa: BLE001 — 표가 없는 DB(픽스처·BYO). 트랜잭션을 되돌려 뒤 검사가 돌게 한다.
        cur.connection.rollback()
        vm = None
    ratios = sorted(vm[c] / b[0] for c, b in bars.items() if b[0] > 0 and c in vm) if vm else []
    n = len(ratios)
    cover = (ratios[n // 2] if n % 2 else (ratios[n // 2 - 1] + ratios[n // 2]) / 2) if n else 0.0
    if not vm or cover < VF_BLOCK_MIN_COVER:
        chk("F", "대량 체결 의심 = DB 재계산(분봉 없음·미완이면 skip)", got.get("status") == "skip",
            f"분봉 합÷거래량 중앙 {cover:.3f} 인데 status={got.get('status')}")
        return
    cur.execute("SELECT s.code, abs(s.close), coalesce(s.individual,0), coalesce(s.foreign_,0), coalesce(s.institution,0) "
                f"FROM supply_demand s WHERE s.source='kiwoom' AND s.date={ph}", (d1,))
    per: dict = {}
    for c, close, *q in cur.fetchall():
        if c in meta and close is not None:
            per.setdefault(meta[c][0], []).append((c, [float(x) * float(close) / 1e8 for x in q]))
    bad = []
    unjudged = 0
    for m in P["markets"]:
        den = sum(abs(sum(a[i] for _c, a in per.get(m, []))) for i in range(3))
        exp = set()
        for c, a in per.get(m, []):
            if not den or max(abs(x) for x in a) < VF_BLOCK_SHARE_MIN * den:
                continue
            vol = bars.get(c, (0.0, 0.0))[0]
            if vol <= 0 or c not in vm:
                unjudged += 1
            elif max(0.0, vol - vm[c]) / vol >= VF_BLOCK_OFF_MIN:
                exp.add(c)
        have = {x["code"] for x in ((got.get("markets") or {}).get(m) or [])}
        if exp != have:
            bad.append(f"{m} 빠짐 {sorted(exp - have)[:3]} 더함 {sorted(have - exp)[:3]}")
    if got.get("unjudged") != unjudged:
        bad.append(f"판정 못 한 종목 {got.get('unjudged')} vs {unjudged}")
    chk("F", "대량 체결 의심 = DB 재계산(분봉 없음·미완이면 skip)", got.get("status") == "ok" and not bad, "; ".join(bad[:2]))


def _f_today_context(P: dict, cur, ph: str, meta: dict, px: dict, cap_at) -> None:
    """F 층의 '오늘' 맥락 — KRX 지수 수익률·금액 근사 오차를 DB 에서 다시 재고(리뷰 7), 확정 모드면
    전 거래일의 자체 바구니를 **정규장 종가**(``regular_close``)로 다시 재어 KRX 지수와 맞춘다(리뷰 12, 외부 참값).
    """
    T = P.get("today") or {}
    dates = P["dates"]
    if not T:
        print("       (오늘 블록이 없는 옛 리포트 — KRX 지수·근사 오차 대조는 건너뛴다)")
        return
    # ── KRX 지수 1·20일 수익률 ──
    want = []
    for m, code in VF_KRX:
        if m not in P["markets"]:
            continue
        need = [dates[-1]] + [dates[-1 - w] for w in (1, 20) if w < len(dates)]
        cur.execute(f"SELECT date, close FROM sector_index WHERE code={ph} AND date IN ({','.join([ph] * len(need))})",
                    (code, *need))
        px_i = {str(d): float(v) for d, v in cur.fetchall() if v}
        got = (((T.get("krx_index") or {}).get(m) or {}).get("ret")) or {}
        for w in (1, 20):
            base = px_i.get(dates[-1 - w]) if w < len(dates) else None
            c0 = px_i.get(dates[-1])
            exp = (c0 / base - 1.0) * 100.0 if c0 and base else None
            want.append((f"{m}/{w}일", got.get(str(w)), exp))
    bad = [f"{lab} {g} vs {e}" for lab, g, e in want
           if (g is None) != (e is None) or (g is not None and abs(g - e) > 0.0011)]
    chk("F", "KRX 지수 1·20일 수익률 = sector_index 재계산(없는 날은 결측)", not bad and bool(want),
        f"{len(want)}칸  " + "; ".join(bad[:2]))

    # ── 금액 근사 오차 — 종가 환산 vs VWAP 환산, n = 4주체 |순매수 수량| 합 ──
    d1 = dates[-1]
    cur.execute(f"SELECT code, volume, trade_value, source FROM daily_bars WHERE date={ph}", (d1,))
    bars: dict = {}
    for c, vol, tv, src in cur.fetchall():
        if c in bars and src != "kiwoom":
            continue
        bars[c] = (float(vol or 0), float(tv or 0))
    cur.execute(f"SELECT s.code, abs(s.close), abs(coalesce(s.individual,0))+abs(coalesce(s.foreign_,0))"
                f"+abs(coalesce(s.institution,0))+abs(coalesce(s.etc_corp,0)) "
                f"FROM supply_demand s JOIN stocks st ON st.code=s.code WHERE s.source='kiwoom' AND s.date={ph}", (d1,))
    acc: dict = {}
    for c, close, n in cur.fetchall():
        if c not in meta or close is None:
            continue
        m, sec = meta[c]
        vol, tv = bars.get(c, (0.0, 0.0))
        for key in (("전체", None), (m, None), (m, sec)):
            a = acc.setdefault(key, [0.0, 0.0])
            if vol > 0 and tv > 0:
                a[0] += float(n) * float(close)
                a[1] += float(n) * tv * 1e6 / vol
    bad, n_cmp = [], 0
    for mkey in ["전체", *P["markets"]]:
        g = (((T.get("market") or {}).get(mkey) or {}).get("approx_gap")) or {}
        c, v = acc.get((mkey, None), (0.0, 0.0))
        exp = abs(c - v) / v * 100.0 if v else None
        n_cmp += 1
        if (g.get("pct") is None) != (exp is None) or (exp is not None and abs(g["pct"] - exp) > 0.011):
            bad.append(f"{mkey} {g.get('pct')} vs {exp}")
        sm = g.get("sector_max")
        if mkey != "전체" and v and "sector_max" in g:
            secs = [(abs(cs - vs) / vs * 100.0, s) for (mm, s), (cs, vs) in acc.items()
                    if mm == mkey and s is not None and vs > 0 and vs >= 0.02 * v]
            top = max(secs) if secs else None
            if (sm is None) != (top is None) or (top and (sm["sector"] != top[1] or abs(sm["pct"] - top[0]) > 0.011)):
                bad.append(f"{mkey} 섹터 최대 {sm} vs {top}")
    chk("F", "금액 근사 오차(시장·섹터 최대) = DB 재계산", not bad and n_cmp > 0, "; ".join(bad[:2]))
    _f_block_suspects(P, cur, ph, meta, d1, bars)

    # ── 외부 참값: 전 거래일 자체 바구니(정규장 종가) ≈ KRX 지수 ──
    if not (P.get("final") or P.get("finalize_unverified")):
        print("       (잠정 리포트 — 정규장 종가 대조는 마감 값(확정·창 밖 재구성)에서만)")
        return
    if len(dates) < 3:
        _external_skip(P, "거래일이 셋 미만")
        return
    t1, t2 = dates[-2], dates[-3]
    cur.execute(f"SELECT code, date, close, src FROM regular_close WHERE date IN ({ph},{ph})", (t1, t2))
    rc: dict = {}
    srcs: dict = {}
    for c, d, cl, src in cur.fetchall():
        rc[(c, str(d))] = float(cl) if cl is not None else None
        if str(d) == t1:
            srcs[src] = srcs.get(src, 0) + 1
    ready = sum(v for k, v in srcs.items() if k in ("identity", "sd_close"))
    total = sum(srcs.values())
    if not total or ready < 0.9 * total:
        # 준비 안 됨은 실패가 아니다 — 다음날 수급이 들어와야 채워지는 뷰다. 사유를 남긴다.
        _external_skip(P, f"{t1} regular_close 준비 안 됨({srcs})")
        return
    # 재구성은 유니버스가 **지금의** stocks 마스터다. 그 뒤 폐지된 종목은 지수에는 있고 바구니에는 없어 같은 것을
    # 맞대는 대조가 아니다 — 2026-09-15 재구성: 09-14 에 폐지 예정 코스닥 종목이 −14~−34% 떨어져 바구니가 지수보다
    # 0.115%p 높았다. 그런 날은 실패가 아니라 사유를 남기고 건너뛴다. 평소 리포트(마스터가 그날 것)는 그대로 본다.
    gone = sorted({c for (c, d) in rc if d == t1} - set(meta))
    if P.get("rebuilt") and gone:
        print(f"       SKIP 정규장 종가 대조 — 재구성: {t1} 에 지금 stocks 마스터에 없는(그 뒤 폐지) 종목 "
              f"{len(gone)}개가 있어 바구니와 지수의 종목이 다르다({', '.join(gone[:5])})")
        return
    num: dict = {}
    den: dict = {}
    for c, (m, _sec) in meta.items():
        a, b = rc.get((c, t1)), rc.get((c, t2))
        if not a or not b:
            continue
        w = cap_at(c, t2, px.get((c, t2)))
        if not w:
            continue
        num[m] = num.get(m, 0.0) + w * (a / b - 1.0) * 100.0
        den[m] = den.get(m, 0.0) + w
    rows, bad = [], []
    for m, code in VF_KRX:
        if m not in P["markets"] or not den.get(m):
            continue
        cur.execute(f"SELECT date, close FROM sector_index WHERE code={ph} AND date IN ({ph},{ph})", (code, t1, t2))
        ix = {str(d): float(v) for d, v in cur.fetchall() if v}
        if t1 not in ix or t2 not in ix:
            bad.append(f"{m} 지수 {t1}/{t2} 없음")
            continue
        own = num[m] / den[m]
        krx = (ix[t1] / ix[t2] - 1.0) * 100.0
        rows.append(f"{m} 바구니 {own:+.3f}% vs 지수 {krx:+.3f}%")
        if abs(own - krx) > VF_REGULAR_TOL:
            bad.append(f"{m} {own:+.3f} vs {krx:+.3f}")
    chk("F", f"외부 참값: {t1} 자체 바구니(정규장 종가) ≈ KRX 지수 ±{VF_REGULAR_TOL}%p",
        not bad and bool(rows), " · ".join(rows) + ("  " + "; ".join(bad) if bad else ""))



# ─────────────────────────────────────────────── T. 오늘 — 1일·백분위·시장 합계·직전 대비

#: 오늘 백분위의 정의 — **producer 상수를 읽지 않고** 여기에 따로 적는다. 페이로드의 ``lookback`` 을 믿으면
#: producer 가 되돌아보기를 잘못 잡았을 때 검증도 같은 값으로 따라간다.
VT_LOOKBACK = 260
VT_MIN_LIVE = 0.25
VT_DETAIL = ("fnnc_invt", "invtrt", "penfnd_etc")


def _vt_series(P: dict, mks: list, sec: str, key: str):
    """(시장들, 섹터, 주체)의 일별 순매수 — 소수 2자리. ``sector_flow._series`` 를 부르지 않는다."""
    if key == "invpen":
        a = _vt_series(P, mks, sec, "invtrt")
        b = _vt_series(P, mks, sec, "penfnd_etc")
        return None if a is None or b is None else [round(x + y, 2) for x, y in zip(a, b)]
    tbl = P.get("detail" if key in VT_DETAIL else "flows") or {}
    arrs = [tbl[m][sec][key] for m in mks
            if key in (tbl.get(m, {}).get(sec) or {})]
    if not arrs:
        return None
    return [round(sum((a[i] or 0.0) for a in arrs), 2) for i in range(len(P["dates"]))]


def _vt_pct(ser: list):
    """마지막 값의 midrank 백분위 — 정렬 + 이분 탐색(producer 는 선형으로 센다)."""
    import bisect  # noqa: PLC0415
    n = min(len(ser), VT_LOOKBACK)
    if n < 2:
        return None
    srt = sorted(ser[-n:])
    zeros = bisect.bisect_right(srt, 0.0) - bisect.bisect_left(srt, 0.0)
    if n - zeros < VT_MIN_LIVE * n:
        return None
    lo, hi = bisect.bisect_left(srt, ser[-1]), bisect.bisect_right(srt, ser[-1])
    return 100.0 * (lo + (hi - lo - 1) / 2.0) / (n - 1)


def _vt_ranks(P: dict, mks: list, actor: str, win: int):
    vals = {}
    for s in P["sectors"]:
        if not any((P.get("n_by_sector") or {}).get(m, {}).get(s) for m in mks):
            continue
        ser = _vt_series(P, mks, s, actor)
        if ser is None or len(ser) < win:
            return None
        vals[s] = round(sum(ser[-win:]), 2)
    order = sorted(vals.items(), key=lambda kv: (-kv[1], kv[0]))
    return {s: i + 1 for i, (s, _v) in enumerate(order)}


def layer_t(P: dict, folder: "str | None" = None) -> None:
    """T. **오늘** — ``today``·``change``·``final_diff`` 와 종목 1일 집계를 페이로드 일별 배열에서 다시 센다.

    1일 값은 일별 배열의 마지막 칸, 백분위는 그 1년 분포(정의는 위 상수), 시장 합계는 섹터 마지막 칸의 합,
    시장 수익률은 섹터 ``ret×retw`` 를 다시 가중한 것이다. producer 는 시장 합계·수익률을 **종목에서 바로**
    모으므로 경로가 다르다. 직전 대비는 옆 폴더의 직전 리포트가 있으면 그 순위까지 다시 센다.
    """
    print("\nT. 오늘 — 1일 값·1년 백분위·시장 합계·직전 리포트 대비")
    dates, mkts, secs = P["dates"], P["markets"], P["sectors"]
    n = len(dates)
    T = P.get("today")
    chk("T", "today 가 있는가", isinstance(T, dict))
    if not isinstance(T, dict):
        return
    chk("T", "today 기준일 = 페이로드 마지막 날", T.get("date") == dates[-1], f"{T.get('date')}")
    chk("T", "되돌아보기 = min(거래일, 260)", T.get("lookback") == min(n, VT_LOOKBACK),
        f"{T.get('lookback')}")
    mkeys = [("전체", list(mkts))] + [(m, [m]) for m in mkts]
    actors = ("inst", "invtrt", "penfnd_etc", "invpen", "forgn", "indiv")
    bad_v, bad_p, bad_set, bad_r, bad_tv, cnt = [], [], [], [], [], 0
    for mk, mks in mkeys:
        rows = (T.get("sectors") or {}).get(mk) or {}
        want = {s for s in secs if any((P.get("n_by_sector") or {}).get(m, {}).get(s) for m in mks)}
        if set(rows) != want:
            bad_set.append(f"{mk}: 남음 {sorted(set(rows) - want)[:2]} 빠짐 {sorted(want - set(rows))[:2]}")
        for s in sorted(want & set(rows)):
            for a in actors:
                ser = _vt_series(P, mks, s, a)
                got = rows[s].get(a)
                if ser is None:
                    continue
                cnt += 1
                if not got or abs((got.get("v") or 0) - ser[-1]) > 0.006 or got.get("v") is None:
                    bad_v.append(f"{mk}/{s}/{a} {got and got.get('v')} vs {ser[-1]}")
                    continue
                p = _vt_pct(ser)
                q = got.get("pct")
                if (p is None) != (q is None) or (p is not None and abs(p - q) > 0.051):
                    bad_p.append(f"{mk}/{s}/{a} {q} vs {p if p is None else round(p, 1)}")
            # 거래대금은 ``flows`` 마지막 칸의 합 — 주체 칸(위)과 달리 ``_vt_series`` 를 거치지 않고 바로 더한다.
            tv = sum(((P["flows"].get(m) or {}).get(s) or {}).get("tv", [0.0])[-1] for m in mks)
            if rows[s].get("tv") is None or abs(rows[s]["tv"] - tv) > 0.006:
                bad_tv.append(f"{mk}/{s} {rows[s].get('tv')} vs {tv:.2f}")
            num = den = 0.0
            for m in mks:
                r, w = P["ret"][m][s][-1], P["retw"][m][s][-1]
                if r is not None and w:
                    num, den = num + r * w, den + w
            want_r = num / den if den else None
            got_r = rows[s].get("ret")
            if (want_r is None) != (got_r is None) or (want_r is not None and abs(want_r - got_r) > 0.002):
                bad_r.append(f"{mk}/{s} {got_r} vs {want_r}")
    chk("T", "오늘 섹터 칸 = 그 시장(들)에 종목이 있는 섹터", not bad_set, "; ".join(bad_set[:2]))
    chk("T", "오늘 섹터×주체 순매수 = 일별 배열 마지막 칸", not bad_v,
        f"{len(bad_v)}/{cnt}" + ("  " + "; ".join(bad_v[:2]) if bad_v else ""))
    chk("T", "오늘 섹터×주체 1년 백분위 재계산", not bad_p,
        f"{len(bad_p)}/{cnt}" + ("  " + "; ".join(bad_p[:2]) if bad_p else ""))
    chk("T", "오늘 섹터 수익률 = 마지막 날 ret×retw 가중", not bad_r, "; ".join(bad_r[:2]))
    chk("T", "오늘 섹터 거래대금 = 일별 배열 마지막 칸", not bad_tv and cnt > 0, "; ".join(bad_tv[:2]))

    # 시장 합계와 시장 수익률 — producer 는 종목에서, 여기는 섹터 배열에서.
    bad_m, bad_mp, bad_mr = [], [], []
    for mk, mks in mkeys:
        M = (T.get("market") or {}).get(mk) or {}
        for a in ("inst", "forgn", "indiv", "etc", "fnnc_invt", "invtrt", "penfnd_etc", "invpen", "tv"):
            ser = [0.0] * n
            for s in secs:
                one = _vt_series(P, mks, s, a)
                for i, v in enumerate(one or []):
                    ser[i] += v
            ser = [round(v, 2) for v in ser]
            got = M.get("tv") if a == "tv" else (M.get("net") or {}).get(a, {}).get("v")
            tol = 0.006 * len(secs) + 0.011
            if got is None or abs(got - ser[-1]) > tol:
                bad_m.append(f"{mk}/{a} {got} vs {ser[-1]:.2f}")
            if a != "tv":
                p, q = _vt_pct(ser), (M.get("net") or {}).get(a, {}).get("pct")
                # 섹터 반올림 합과 종목 합의 차(≤ tol)가 동률을 갈 수 있다 — 한 칸(100/(n−1)) 여유.
                if (p is None) != (q is None) or (p is not None and abs(p - q) > 100.0 / (n - 1) + 0.051):
                    bad_mp.append(f"{mk}/{a} {q} vs {p}")
        daily = []
        for i in range(n):
            num = den = 0.0
            for m in mks:
                for s in secs:
                    r, w = P["ret"][m][s][i], P["retw"][m][s][i]
                    if r is not None and w:
                        num, den = num + r * w, den + w
            daily.append(num / den if den else None)
        for w in (1, 20):
            got = (M.get("ret") or {}).get(str(w))
            if w > n or daily[-1] is None:
                want_r = None
            else:
                acc = 1.0
                for r in daily[-w:]:
                    acc *= 1.0 + (r or 0.0) / 100.0
                want_r = (acc - 1.0) * 100.0
            if (want_r is None) != (got is None) or (want_r is not None and abs(want_r - got) > 0.02):
                bad_mr.append(f"{mk}/{w}일 {got} vs {want_r}")
    chk("T", "오늘 시장 합계 = 섹터 마지막 칸의 합", not bad_m, "; ".join(bad_m[:3]))
    chk("T", "오늘 시장 합계 1년 백분위 재계산", not bad_mp, "; ".join(bad_mp[:3]))
    chk("T", "시장 수익률 1·20일 = 섹터 ret×retw 재가중의 기하누적", not bad_mr, "; ".join(bad_mr[:3]))

    # 종목 1일 집계 — 섹터 합이 섹터 마지막 칸과 맞는가(종목 값은 소수 1자리라 종목 수만큼 여유).
    win1 = [(r, r["win"]["1"]) for r in P["names"].values() if "1" in (r.get("win") or {})]
    chk("T", "종목 1일 집계가 있는가", bool(win1), f"{len(win1)}종목")
    agg: dict = {}
    for r, w in win1:
        a = agg.setdefault((r["market"], r["sector"]), [0.0, 0])
        a[0] += w.get("inst") or 0.0
        a[1] += 1
    bad = [f"{m}/{s} 종목합 {v:+.1f} vs {P['flows'][m][s]['inst'][-1]:+.2f}"
           for (m, s), (v, k) in agg.items()
           if m in P["flows"] and s in P["flows"][m]
           and abs(v - P["flows"][m][s]["inst"][-1]) > 0.05 * k + 0.011]
    chk("T", "종목 1일 기관 합 = 섹터 마지막 칸", not bad, "; ".join(bad[:2]))

    # 직전 리포트 대비
    C, why = P.get("change"), P.get("change_reason")
    prev_date = dates[-2] if n > 1 else None
    old = None
    if folder and prev_date:
        path = os.path.join(os.path.dirname(os.path.abspath(folder)), prev_date, "payload.json")
        if os.path.isfile(path):
            try:
                old = json.load(open(path, encoding="utf-8"))
            except ValueError:
                old = None
            if old and (old.get("dates") or [None])[-1] != prev_date:
                old = None
    chk("T", "직전 대비 키가 있는가", "change" in P)
    if C is None:
        chk("T", "직전 대비가 비었으면 이유가 있는가", bool(why), f"{why}")
        # 섹터 덮어쓰기가 바뀐 첫날은 비교하지 않는 것이 맞다(같은 섹터가 다른 종목 집합이다).
        fp = lambda q: ((q or {}).get("sector_override") or {}).get("fp") or "none"   # noqa: E731
        chk("T", "직전 리포트가 옆에 있는데 대비가 비지 않았는가(덮어쓰기 지문이 같을 때)",
            old is None or fp(old) != fp(P), f"{prev_date} 있음 · 지문 {fp(old)}→{fp(P)}" if old else "")
    else:
        chk("T", "직전 대비의 직전일 = 페이로드 바로 앞 거래일(미래 아님)",
            C.get("prev_date") == prev_date and prev_date < dates[-1], f"{C.get('prev_date')}")
        bad_c, bad_p2, bad_t = [], [], []
        topn = 5
        for mk, mks in mkeys:
            for a in ("inst", "invpen"):
                cur = _vt_ranks(P, mks, a, 20)
                got = ((C.get("rank") or {}).get(mk) or {}).get(a)
                if cur is None:
                    continue
                if not got or {s: v[1] for s, v in got.items()} != cur:
                    bad_c.append(f"{mk}/{a}")
                    continue
                if old is not None:
                    prev = _vt_ranks(old, mks, a, 20) if set(mks) <= set(old["markets"]) else None
                    if prev is None or {s: v[0] for s, v in got.items()} != {s: prev.get(s) for s in cur}:
                        bad_p2.append(f"{mk}/{a}")
                    else:
                        top_c = sorted(cur, key=cur.get)[:topn]
                        top_p = sorted(prev, key=prev.get)[:topn]
                        tt = ((C.get("top") or {}).get(mk) or {}).get(a) or {}
                        if (tt.get("cur"), tt.get("prev"), tt.get("entered"), tt.get("exited")) != (
                                top_c, top_p, [s for s in top_c if s not in top_p],
                                [s for s in top_p if s not in top_c]):
                            bad_t.append(f"{mk}/{a}")
        chk("T", "직전 대비 — 오늘 20일 순위 재계산", not bad_c, ", ".join(bad_c[:3]))
        if old is not None:
            chk("T", "직전 대비 — 직전 리포트의 20일 순위·상위 5 들고 남 재계산",
                not bad_p2 and not bad_t, ", ".join((bad_p2 + bad_t)[:3]))
        else:
            print("       (직전 리포트가 옆 폴더에 없다 — 직전 순위 대조는 건너뛴다)")

    # 잠정 → 확정
    if P.get("final"):
        chk("T", "확정 리포트에 잠정 대비(final_diff 또는 이유)가 있는가",
            "final_diff" in P and (P["final_diff"] is not None or bool(P.get("final_diff_reason"))))
    F = P.get("final_diff")
    if isinstance(F, dict):
        bad_f = []
        for r in F.get("changed") or []:
            ser = _vt_series(P, [r["market"]], r["sector"], r["actor"])
            want = None if ser is None else sum(ser[-r["win"]:])
            if want is None or abs(want - r["new"]) > 0.011:
                bad_f.append(f"{r['market']}/{r['sector']}/{r['actor']}/{r['win']} new {r['new']} vs {want}")
            elif r["old"] is not None and (abs(r["new"] - r["old"] - r["diff"]) > 0.011
                                           or abs(r["diff"]) <= F["tol"]):
                bad_f.append(f"{r['market']}/{r['sector']}/{r['actor']}/{r['win']} diff {r['diff']}")
        for t in F.get("market_totals") or []:
            tot = 0.0
            for s in secs:
                ser = _vt_series(P, [t["market"]], s, t["actor"])
                if ser is not None and (P.get("n_by_sector") or {}).get(t["market"], {}).get(s):
                    tot += sum(ser[-t["win"]:])
            if abs(tot - t["new"]) > 0.011 * len(secs):
                bad_f.append(f"{t['market']}/{t['actor']}/{t['win']} 합 {t['new']} vs {tot:.2f}")
        chk("T", "잠정 → 확정 — 확정값·차이가 페이로드에서 다시 나오는가",
            not bad_f and F.get("n_changed") == len(F.get("changed") or []),
            f"{len(bad_f)}건  " + "; ".join(bad_f[:2]))
    _vt_final_vs_provisional(P, folder)
    _vt_revision_check(P, folder)
    _vt_streak_check(P)
    _vt_day_change_check(P)
    _vt_concentration_check(P)
    _vt_krx_truth(P, folder)


# ─────────────────────────────────────────────── T. 잠정 → 확정 — 옆 폴더의 잠정 리포트에서 다시 센다
#
# daily_report.sh 는 확정 리포트를 ``<루트>/<기준일>.building.<pid>`` 에 짓고 **바꿔 끼우기 전에** 검증한다 —
# 그때 ``<루트>/<기준일>/payload.json`` 이 바뀔 잠정 리포트다. 화면이 적는 잠정값(``old``)·차이·칸 수·
# 극단 목록의 들고 남을 거기서 따로 다시 센다. 정의는 producer 상수를 읽지 않고 여기 적는다.

VT_FD_ACTORS = ("inst", "invtrt", "penfnd_etc", "forgn", "indiv", "etc")
VT_FD_WINS = (1, 5)
VT_FD_TOL = 0.05
VT_EXT_ACTORS = ("inst", "invpen", "forgn", "indiv", "etc")
VT_KST = 9 * 3600


def _vt_fp(Q: "dict | None") -> str:
    return ((Q or {}).get("sector_override") or {}).get("fp") or "none"


def _vt_extremes(Q: dict, mks: list, actor: str):
    if not set(mks) <= set(Q.get("markets") or []):
        return None
    hi, lo = set(), set()
    for s in Q["sectors"]:
        if not any((Q.get("n_by_sector") or {}).get(m, {}).get(s) for m in mks):
            continue
        ser = _vt_series(Q, mks, s, actor)
        p = None if ser is None else _vt_pct(ser)
        if p is None:
            continue
        if p >= 95.0:
            hi.add(s)
        elif p <= 5.0:
            lo.add(s)
    return hi, lo


def _vt_final_diff(P: dict, prov: dict) -> dict:
    """확정 ``P`` 와 잠정 ``prov`` 에서 다시 센 (바뀐 칸, 시장 합계, 1일 칸, 칸 수, 극단 들고 남)."""
    changed, totals, cells, n_cells = {}, {}, {}, 0
    for m in P["markets"]:
        for a in VT_FD_ACTORS:
            tn = dict.fromkeys(VT_FD_WINS, 0.0)
            to: dict = dict.fromkeys(VT_FD_WINS, 0.0)
            for s in P["sectors"]:
                if not (P.get("n_by_sector") or {}).get(m, {}).get(s):
                    continue
                sn = _vt_series(P, [m], s, a)
                if sn is None:
                    continue
                so = _vt_series(prov, [m], s, a) if m in (prov.get("markets") or []) else None
                cells[(m, s, a)] = (None if so is None else so[-1], sn[-1])
                for w in VT_FD_WINS:
                    vn = round(sum(sn[-w:]), 2)
                    vo = None if so is None else round(sum(so[-w:]), 2)
                    n_cells += 1
                    tn[w] += vn
                    to[w] = None if to[w] is None or vo is None else to[w] + vo
                    if vo is None or abs(vn - vo) > VT_FD_TOL:
                        changed[(m, s, a, w)] = (vo, vn)
            for w in VT_FD_WINS:
                totals[(m, a, w)] = (to[w], tn[w])
    ext: dict = {}
    for mk, mks in [("전체", list(P["markets"]))] + [(m, [m]) for m in P["markets"]]:
        for a in VT_EXT_ACTORS:
            new, old = _vt_extremes(P, mks, a), _vt_extremes(prov, mks, a)
            if new is None or old is None:
                continue
            ch = {"hi_in": sorted(new[0] - old[0]), "hi_out": sorted(old[0] - new[0]),
                  "lo_in": sorted(new[1] - old[1]), "lo_out": sorted(old[1] - new[1])}
            if any(ch.values()):
                ext.setdefault(mk, {})[a] = ch
    return {"changed": changed, "totals": totals, "cells": cells, "n_cells": n_cells, "ext": ext}


def _vt_close(a, b, tol: float) -> bool:
    return (a is None) == (b is None) and (a is None or abs(a - b) <= tol)


# ─────────────────────────────────────────────── T. 잠정 → 확정 — 종목(기관상위) 층을 다시 센다
#
# 섹터 칸과 같은 원칙: 정의는 producer 상수를 읽지 않고 여기 적는다. 기관상위 = (시장, 섹터)에서 1일
# |기관| 큰 순 누적 기여율 80% 를 처음 넘긴 행까지(그 행 포함), 잠정·확정의 합집합. names 의 (시장, 섹터)
# 종목 집합이 두 페이로드에서 다른 (시장, 섹터)는 통째로 뺀다.

VT_STK_ACTORS = ("inst", "invtrt", "penfnd_etc", "forgn")
VT_STK_CUT, VT_STK_TOL, VT_STK_CAP, VT_STK_MIN = 80.0, 0.5, 200, 6


def _vt_win1(Q: dict) -> dict:
    out = {}
    for code, r in (Q.get("names") or {}).items():
        w = (r.get("win") or {}).get("1")
        if w and r.get("market") and r.get("sector"):
            out[code] = (r["market"], r["sector"], r.get("name"), w)
    return out


def _vt_stk_drivers(w1: dict) -> set:
    groups: dict = {}
    for code, (m, s, _n, w) in w1.items():
        groups.setdefault((m, s), []).append((abs(w.get("inst") or 0.0), code))
    out: set = set()
    for rows in groups.values():
        tot = sum(v for v, _c in rows)
        if not tot:
            continue
        cum = 0.0
        for v, code in sorted(rows, key=lambda t: (-t[0], t[1])):
            out.add(code)
            cum += v
            if cum >= VT_STK_CUT / 100.0 * tot:
                break
    return out


def _vt_stock_diff(P: dict, prov: dict) -> "dict | None":
    """확정 ``P`` 와 잠정 ``prov`` 에서 다시 센 종목(기관상위) 행·요약 — ``final_diff["stocks"]`` 와 같은 정의.

    어느 쪽이든 1일 종목 칸이 하나도 없으면(옛 형식 게시본) ``None`` — 종목 층을 말할 수 없다."""
    new1, old1 = _vt_win1(P), _vt_win1(prov)
    if not new1 or not old1:
        return None
    mem: list[dict] = [{}, {}]
    for d, out_m in ((P, mem[0]), (prov, mem[1])):
        for code, r in (d.get("names") or {}).items():
            out_m.setdefault((r.get("market"), r.get("sector")), set()).add(code)
    dropped = {ms for ms in set(mem[0]) | set(mem[1]) if mem[0].get(ms) != mem[1].get(ms)}
    drivers = {c for c in _vt_stk_drivers(new1) | _vt_stk_drivers(old1)
               if (new1.get(c) or old1[c])[:2] not in dropped}
    tot: dict = {}
    for c in drivers:
        if c in new1:
            m, s, _n, w = new1[c]
            for a in VT_STK_ACTORS:
                tot[(m, s, a)] = tot.get((m, s, a), 0.0) + abs(w.get(a) or 0.0)
    rel, flips, rows = [], 0, []
    for c in sorted(drivers):
        m, s, name = (new1.get(c) or old1[c])[:3]
        wn, wo = (new1[c][3] if c in new1 else None), (old1[c][3] if c in old1 else None)
        vals, dmax = {}, None
        for a in VT_STK_ACTORS:
            vn = None if wn is None else wn.get(a)
            vo = None if wo is None else wo.get(a)
            vals[a] = [vo, vn]
            if vo is None or vn is None:
                dmax = float("inf")
                continue
            dmax = max(dmax or 0.0, abs(vn - vo))
            if vn and abs(vn) >= VT_REV_GATE * tot[(m, s, a)]:
                rel.append(abs(vn - vo) / abs(vn) * 100.0)
                flips += vo * vn < 0
        if dmax is not None and dmax > VT_STK_TOL:
            rows.append((dmax, [m, s, c, name, vals]))
    rows.sort(key=lambda t: (-t[0], t[1][2]))
    by: dict = {}
    for d, i in ((new1, 0), (old1, 1)):
        for code, (m, s, _n, w) in d.items():
            by.setdefault((m, s), [[], []])[i].append((code, w))
    kept = lists = 0
    for ms in sorted(by):
        rn, ro = by[ms]
        if ms in dropped or len(rn) < VT_STK_MIN or len(ro) < VT_STK_MIN:
            continue
        for a in VT_STK_ACTORS:
            tn = {c for c, _w in sorted(rn, key=lambda t: (-(t[1].get(a) or 0.0), t[0]))[:VT_TOP_N]}
            to = {c for c, _w in sorted(ro, key=lambda t: (-(t[1].get(a) or 0.0), t[0]))[:VT_TOP_N]}
            kept += len(tn & to)
            lists += 1
    med = statistics.median(rel) if rel else None
    return {"n_stocks": len(drivers), "n_cells": len(rel), "rel_median_pct": med, "flips": flips,
            "top3_kept": kept, "top3_lists": lists, "n_rows": len(rows),
            "dropped_sectors": len(dropped), "rows": [r for _d, r in rows[:VT_STK_CAP]]}


def _vt_stock_final_check(P: dict, prov: dict, F: dict) -> None:
    """확정 리포트의 ``final_diff["stocks"]`` 를 옆 잠정 리포트에서 다시 세어 대조한다."""
    S = F.get("stocks")
    W = _vt_stock_diff(P, prov)
    if W is None:
        chk("T", "잠정 → 확정 — 종목(기관상위) 층은 1일 칸이 없는 옛 형식 게시본 대비에서 비어 있는가", S is None,
            str(S)[:150])
        return
    bad = []
    if not isinstance(S, dict):
        bad.append("stocks 블록 없음")
    else:
        for k in ("n_stocks", "n_cells", "flips", "top3_kept", "top3_lists", "n_rows", "dropped_sectors"):
            if S.get(k) != W[k]:
                bad.append(f"{k} {S.get(k)} vs {W[k]}")
        if not _vt_close(S.get("rel_median_pct"), W["rel_median_pct"], 0.051):
            bad.append(f"rel_median_pct {S.get('rel_median_pct')} vs {W['rel_median_pct']}")
        got = {(r[0], r[1], r[2]): r[4] for r in S.get("rows") or []}
        want = {(r[0], r[1], r[2]): r[4] for r in W["rows"]}
        if set(got) != set(want):
            bad.append(f"행 집합 남음 {sorted(set(got) - set(want))[:2]} 빠짐 {sorted(set(want) - set(got))[:2]}")
        for k in set(got) & set(want):
            if any(not (_vt_close(got[k].get(a, [None, None])[0], want[k][a][0], 0.051)
                        and _vt_close(got[k].get(a, [None, None])[1], want[k][a][1], 0.051))
                   for a in VT_STK_ACTORS):
                bad.append(f"{'/'.join(k)} {got[k]} vs {want[k]}"[:160])
    chk("T", "잠정 → 확정 — 종목(기관상위) 행·요약을 옆 잠정 리포트에서 다시 센 것과 같은가", not bad,
        f"{len(bad)}건  " + "; ".join(bad[:3]))


def _vt_final_vs_provisional(P: dict, folder: "str | None") -> None:
    F = P.get("final_diff")
    if not P.get("final") or not folder:
        return
    asof = P["dates"][-1]
    sib = os.path.join(os.path.dirname(os.path.abspath(folder)), asof)
    spath = os.path.join(sib, "payload.json")
    if os.path.realpath(sib) == os.path.realpath(folder):
        print("       (바꿔 끼운 뒤다 — 잠정 리포트가 옆에 없어 잠정값 재계산은 건너뛴다. 배치는 바꿔 끼우기 전에 검증한다)")
        return
    prov = None
    if os.path.isfile(spath):
        try:
            prov = json.load(open(spath, encoding="utf-8"))
        except ValueError:
            prov = None
        if prov and (prov.get("dates") or [None])[-1] != asof:
            prov = None
    if not isinstance(F, dict):
        chk("T", "잠정 → 확정 — 바꿔 끼울 잠정 리포트가 옆에 있는데 비교가 비지 않았는가(덮어쓰기 지문이 같을 때)",
            prov is None or _vt_fp(prov) != _vt_fp(P), f"{sib} 있음 · 지문 {_vt_fp(prov)}→{_vt_fp(P)}" if prov else "")
        return
    chk("T", "잠정 → 확정 — 견준 잠정 리포트가 옆 폴더에 있고 지문이 같은가",
        prov is not None and _vt_fp(prov) == _vt_fp(P), spath if prov is None else f"지문 {_vt_fp(prov)}→{_vt_fp(P)}")
    if prov is None or _vt_fp(prov) != _vt_fp(P):
        return
    W = _vt_final_diff(P, prov)
    bad = []
    got = {(r["market"], r["sector"], r["actor"], r["win"]): r for r in F.get("changed") or []}
    if set(got) != set(W["changed"]):
        bad.append(f"바뀐 칸 집합 남음 {sorted(set(got) - set(W['changed']))[:2]} "
                   f"빠짐 {sorted(set(W['changed']) - set(got))[:2]}")
    for k in set(got) & set(W["changed"]):
        vo, vn = W["changed"][k]
        r = got[k]
        if not (_vt_close(r.get("old"), vo, 0.011) and _vt_close(r.get("new"), vn, 0.011)
                and _vt_close(r.get("diff"), None if vo is None else vn - vo, 0.011)):
            bad.append(f"{'/'.join(map(str, k))} {r.get('old')}→{r.get('new')} vs {vo}→{vn}")
    if F.get("n_cells") != W["n_cells"] or F.get("n_changed") != len(W["changed"]):
        bad.append(f"칸 수 {F.get('n_changed')}/{F.get('n_cells')} vs {len(W['changed'])}/{W['n_cells']}")
    tot = {(t["market"], t["actor"], t["win"]): t for t in F.get("market_totals") or []}
    if set(tot) != set(W["totals"]):
        bad.append("시장 합계 칸 집합")
    tol_t = 0.011 * len(P["sectors"])
    for k in set(tot) & set(W["totals"]):
        vo, vn = W["totals"][k]
        t = tot[k]
        if not (_vt_close(t.get("old"), vo, tol_t) and _vt_close(t.get("new"), vn, tol_t)):
            bad.append(f"합계 {'/'.join(map(str, k))} {t.get('old')}→{t.get('new')} vs {vo}→{vn}")
    cells = {(c[0], c[1], c[2]): (c[3], c[4]) for c in F.get("cells_1d") or []}
    if set(cells) != set(W["cells"]) or any(
            not (_vt_close(cells[k][0], W["cells"][k][0], 0.006) and _vt_close(cells[k][1], W["cells"][k][1], 0.006))
            for k in set(cells) & set(W["cells"])):
        bad.append("1일 칸(cells_1d)")
    chk("T", "잠정 → 확정 — 잠정값·차이·칸 수를 옆 잠정 리포트에서 다시 센 것과 같은가", not bad,
        f"{len(bad)}건  " + "; ".join(bad[:3]))
    chk("T", "잠정 → 확정 — %ile 극단 목록의 들고 남 재계산", (F.get("extremes_change") or {}) == W["ext"],
        f"{F.get('extremes_change')} vs {W['ext']}"[:300])
    _vt_stock_final_check(P, prov, F)
    from datetime import datetime, timezone  # noqa: PLC0415
    built = datetime.fromtimestamp(os.path.getmtime(spath) + VT_KST, timezone.utc).strftime("%Y-%m-%d %H:%M")
    chk("T", "잠정 → 확정 — 견준 리포트의 지위·생성 시각 = 옆 폴더의 것",
        F.get("base_final") == bool(prov.get("final")) and F.get("base_built") == built,
        f"{F.get('base_final')}/{F.get('base_built')} vs {bool(prov.get('final'))}/{built}")
    fj = os.path.join(folder, "final_diff.json")
    if os.path.isfile(fj):
        try:
            same = json.load(open(fj, encoding="utf-8")) == F
        except ValueError:
            same = False
        chk("T", "final_diff.json(다음 리포트들의 수정 폭 원천) = 페이로드의 final_diff", same, fj)


# ─────────────────────────────────────────────── T. 수정 폭 — 옆 폴더들의 확정 기록·시드에서 다시 센다

VT_REV_DAYS = 20
VT_REV_GATE = 0.02
VT_TOP_N = 3
VT_TOP_MIN = 6
VT_TOP_ACTORS = ("inst", "invpen", "forgn", "indiv", "etc")
VT_SEED_DIR = ".revision_seed"
VT_SEED_LABEL = "시드: 아카이브 18:10 게시본 대 확정 재구성"


def _vt_usable(fd, date: str):
    if not isinstance(fd, dict) or fd.get("base_final") or fd.get("cells_1d") is None:
        return None
    return fd if str(fd.get("base_built") or "")[:10] == date else None


def _vt_rev_cells(root: str, date: str):
    """(잠정 대비 기록, 시드인가) — 확정 폴더의 기록이 먼저, 없으면 시드. 못 쓰면 (None, False)."""
    d = os.path.join(root, date)
    if os.path.isfile(os.path.join(d, "FINAL.txt")) and not os.path.isfile(os.path.join(d, "VERIFY_FAILED.txt")):
        for name in ("final_diff.json", "payload.json"):
            path = os.path.join(d, name)
            if not os.path.isfile(path):
                continue
            try:
                doc = json.load(open(path, encoding="utf-8"))
            except ValueError:
                continue
            fd = _vt_usable(doc if name == "final_diff.json" else doc.get("final_diff"), date)
            if fd is not None:
                return fd, False
            break
    path = os.path.join(root, VT_SEED_DIR, f"{date}.json")
    if os.path.isfile(path):
        try:
            doc = json.load(open(path, encoding="utf-8"))
        except ValueError:
            doc = None
        if isinstance(doc, dict) and (doc.get("seed") or {}).get("label") == VT_SEED_LABEL:
            fd = _vt_usable(doc, date)
            if fd is not None:
                return fd, True
    return None, False


def _vt_top3(cells: list) -> tuple[int, int]:
    """(유지, 목록 수) — (시장·전체, 주체)마다 잠정 상위 3 ∩ 확정 상위 3."""
    vals: dict = {}
    bad: set = set()
    mkts = sorted({c[0] for c in cells})
    for m, s, a, old, new in cells:
        keys = ["invpen"] if a in ("invtrt", "penfnd_etc") else [a]
        for k in keys:
            if k not in VT_TOP_ACTORS:
                continue
            for mk in (m, "전체"):
                if old is None or new is None:
                    bad.add((mk, k, s))
                    continue
                o, n = vals.setdefault((mk, k), {}).get(s, (0.0, 0.0))
                vals[(mk, k)][s] = (o + old, n + new)
    # 투신+연기금은 두 칸이 다 있어야 한다.
    have = {(c[0], c[1], c[2]) for c in cells}
    for m, s, a, _o, _n in cells:
        if a in ("invtrt", "penfnd_etc") and not {(m, s, "invtrt"), (m, s, "penfnd_etc")} <= have:
            bad |= {(m, "invpen", s), ("전체", "invpen", s)}
    kept = lists = 0
    for (mk, k), d in vals.items():
        if mk != "전체" and mk not in mkts:
            continue
        rows = [(s, round(o, 2), round(n, 2)) for s, (o, n) in d.items() if (mk, k, s) not in bad]
        if len(rows) < VT_TOP_MIN:
            continue
        a = {r[0] for r in sorted(rows, key=lambda r: (-r[1], r[0]))[:VT_TOP_N]}
        b = {r[0] for r in sorted(rows, key=lambda r: (-r[2], r[0]))[:VT_TOP_N]}
        kept += len(a & b)
        lists += 1
    return kept, lists


def _vt_revision_check(P: dict, folder: "str | None") -> None:
    R = P.get("revision_stats")
    if not isinstance(R, dict) or not folder:
        return
    asof = P["dates"][-1]
    root = os.path.dirname(os.path.abspath(folder))
    found, seeded = [], []
    if P.get("final"):
        fd = _vt_usable(P.get("final_diff"), asof)
        if fd is not None:
            found.append((asof, fd))
    names = set(os.listdir(root))
    if os.path.isdir(os.path.join(root, VT_SEED_DIR)):
        names |= {f.removesuffix(".json") for f in os.listdir(os.path.join(root, VT_SEED_DIR))}
    for name in sorted((n for n in names if re.fullmatch(r"\d{4}-\d{2}-\d{2}", n) and n < asof), reverse=True):
        if len(found) >= VT_REV_DAYS:
            break
        fd, is_seed = _vt_rev_cells(root, name)
        if fd is not None:
            found.append((name, fd))
            if is_seed:
                seeded.append(name)
    rel, flips, kept, lists = [], 0, 0, 0
    for _d, fd in found:
        cells = fd["cells_1d"]
        tot: dict = {}
        for m, _s, a, _o, new in cells:
            tot[(m, a)] = tot.get((m, a), 0.0) + abs(new or 0.0)
        for m, _s, a, old, new in cells:
            if old is not None and new and abs(new) >= VT_REV_GATE * tot[(m, a)]:
                rel.append(abs(new - old) / abs(new) * 100.0)
                flips += old * new < 0
        k, n = _vt_top3(cells)
        kept, lists = kept + k, lists + n
    med = statistics.median(rel) if rel else None
    keep = 100.0 * kept / (lists * VT_TOP_N) if lists else None
    want_seed = {"label": VT_SEED_LABEL, "dates": sorted(seeded)} if seeded else None
    same_rev = (R.get("dates") == sorted(d for d, _ in found) and R.get("n_dates") == len(found)
                and R.get("n_cells") == len(rel) and R.get("flips") == flips
                and _vt_close(R.get("rel_median_pct"), med, 0.051))
    chk("T", "잠정→확정 수정 폭 — 날짜·칸 수·중앙값·부호 뒤집힘을 옆 폴더의 확정 기록·시드에서 다시 센 것과 같은가", same_rev,
        f"{R.get('n_dates')}일·{R.get('n_cells')}칸·중앙 {R.get('rel_median_pct')}·뒤집힘 {R.get('flips')} vs "
        f"{len(found)}일·{len(rel)}칸·중앙 {med if med is None else round(med, 1)}·뒤집힘 {flips}")
    chk("T", "잠정→확정 수정 폭 — 상위 3 유지율 재계산", _vt_close(R.get("top3_keep_pct"), keep, 0.051)
        and R.get("top3_lists") == lists, f"{R.get('top3_keep_pct')}/{R.get('top3_lists')} vs "
        f"{keep if keep is None else round(keep, 1)}/{lists}")
    chk("T", "잠정→확정 수정 폭 — 시드로 센 날짜와 출처 표지", R.get("seed") == want_seed,
        f"{R.get('seed')} vs {want_seed}"[:300])
    _vt_stock_rev_check(R, found)


def _vt_stock_rev_check(R: dict, found: list) -> None:
    """종목(기관상위) 집계 — 일별 ``final_diff["stocks"]`` 요약(칸·뒤집힘·유지는 합, 중앙값은 일별 중앙값의 중앙값)."""
    sd, meds, sc, fl, kp, ls = 0, [], 0, 0, 0, 0
    for _d, fd in found:
        S = fd.get("stocks")
        if not isinstance(S, dict):
            continue
        sd += 1
        sc += S.get("n_cells") or 0
        fl += S.get("flips") or 0
        if S.get("rel_median_pct") is not None:
            meds.append(S["rel_median_pct"])
        kp += S.get("top3_kept") or 0
        ls += S.get("top3_lists") or 0
    G = R.get("stocks")
    if not sd:
        chk("T", "잠정→확정 수정 폭 — 종목(기관상위) 집계는 일별 요약이 없으면 비어 있는가", G is None, str(G)[:150])
        return
    med = statistics.median(meds) if meds else None
    keep = 100.0 * kp / (ls * VT_TOP_N) if ls else None
    ok = (isinstance(G, dict) and G.get("n_dates") == sd and G.get("n_cells") == sc and G.get("flips") == fl
          and _vt_close(G.get("rel_median_pct"), med, 0.051)
          and G.get("top3_lists") == ls and _vt_close(G.get("top3_keep_pct"), keep, 0.051))
    chk("T", "잠정→확정 수정 폭 — 종목(기관상위) 일별 요약의 집계 재계산", ok,
        f"{G} vs {sd}일·{sc}칸·중앙 {med if med is None else round(med, 1)}·뒤집힘 {fl}·유지 "
        f"{keep if keep is None else round(keep, 1)}/{ls}"[:300])


# ─────────────────────────────────────────────── T. 연속 순매매 — 일별 배열에서 다시 센다

VT_STREAK_EPS = 0.01
VT_STREAK_MIN = 10
VT_STREAK_ACTORS = ("inst", "invpen", "forgn", "indiv", "etc")


def _vt_streak(ser: list, gross: list) -> dict:
    """뒤에서부터 같은 부호를 센다(producer 는 앞에서 이어 센다). 백분위는 정렬 + 이분 탐색."""
    import bisect  # noqa: PLC0415
    sg = [((v > 0) - (v < 0)) if g and abs(v) >= VT_STREAK_EPS * g else 0 for v, g in zip(ser, gross)]
    lens = []
    for i in range(len(sg)):
        j = i
        while j >= 0 and sg[j] != 0 and sg[j] == sg[i]:
            j -= 1
        lens.append(i - j)
    k = lens[-1] if lens else 0
    n = min(len(lens), VT_LOOKBACK)
    pct = None
    if k and n >= 2:
        srt = sorted(lens[-n:])
        lo, hi = bisect.bisect_left(srt, k), bisect.bisect_right(srt, k)
        pct = 100.0 * (lo + (hi - lo - 1) / 2.0) / (n - 1)
    return {"sign": sg[-1] if sg else 0, "days": k, "sum": sum(ser[-k:]) if k else 0.0, "pct": pct}


def _vt_streak_check(P: dict) -> None:
    T = P.get("today") or {}
    S = T.get("streaks")
    chk("T", "연속 순매매 블록(today.streaks)이 있는가", isinstance(S, dict))
    if not isinstance(S, dict):
        return
    n = len(P["dates"])
    nb = P.get("n_by_sector") or {}
    bad_m, bad_s, bad_l = [], [], []

    def same(got, want, tol_sum):
        return (isinstance(got, dict) and got.get("sign") == want["sign"] and got.get("days") == want["days"]
                and abs((got.get("sum") or 0.0) - want["sum"]) <= tol_sum and _vt_close(got.get("pct"), want["pct"], 0.051))

    for mk, mks in [("전체", list(P["markets"]))] + [(m, [m]) for m in P["markets"]]:
        present = [s for s in P["sectors"] if any(nb.get(m, {}).get(s) for m in mks)]
        for a in VT_STREAK_ACTORS:
            sers = {s: x for s in present if (x := _vt_series(P, mks, s, a)) is not None}
            if not sers:
                continue
            gross = [sum(abs(x[i]) for x in sers.values()) for i in range(n)]
            total = [round(sum(x[i] for x in sers.values()), 2) for i in range(n)]
            want = _vt_streak(total, gross)
            got = ((S.get("market") or {}).get(mk) or {}).get(a)
            if not same(got, want, 0.011 * len(present)):
                bad_m.append(f"{mk}/{a} {got} vs {want}")
            best = None
            elig = [s for s in sers if s != "(미분류)" and sum(int(nb.get(m, {}).get(s) or 0) for m in mks) >= VT_STREAK_MIN]
            rows = ((S.get("sectors") or {}).get(mk) or {})
            if {s for s in rows if a in rows[s]} != set(elig):
                bad_s.append(f"{mk}/{a} 섹터 집합")
            for s in elig:
                w = _vt_streak(sers[s], gross)
                if not same((rows.get(s) or {}).get(a), w, 0.011):
                    bad_s.append(f"{mk}/{s}/{a} {(rows.get(s) or {}).get(a)} vs {w}")
                if w["days"]:
                    key = (-w["days"], -abs(round(w["sum"], 2)), s)
                    best = key if best is None or key < best else best
            if ((S.get("longest") or {}).get(mk) or {}).get(a) != (best[2] if best else None):
                bad_l.append(f"{mk}/{a} {((S.get('longest') or {}).get(mk) or {}).get(a)} vs {best and best[2]}")
    chk("T", "연속 순매매 — 시장 합계의 연속 일수·누적·1년 백분위 재계산", not bad_m, "; ".join(bad_m[:2]))
    chk("T", "연속 순매매 — 섹터(얇은 섹터·미분류 제외)의 연속 재계산", not bad_s,
        f"{len(bad_s)}건  " + "; ".join(bad_s[:2]))
    chk("T", "연속 순매매 — 시장·주체별 최장 섹터 재계산", not bad_l, "; ".join(bad_l[:2]))


# ─────────────────────────────────────────────── T. 하루 단위 변화·거래대금 집중 — 일별 배열·종목 값에서 다시 센다

#: 정의를 producer 상수에서 읽지 않는다(그쪽이 틀리면 검증도 따라간다).
VT_DAY_HI, VT_DAY_LO = 95, 5
VT_DAY_FLIP_SHARE = 0.02
VT_DAY_ACTORS = ("inst", "invpen", "forgn", "indiv", "etc")
VT_CONC_SHARE = 0.20
VT_CONC_TOP = 2
#: 부호 줄·집중 줄의 금액 허용오차 — 일별 값이 소수 2자리, 종목 값이 소수 1자리다.
VT_DAY_TOL = 0.006


def _vt_visible(p: float) -> int:
    return int(math.floor(round(p, 1) + 0.5 + 1e-9))


def _vt_extreme_days(ser: list, hi: bool) -> int:
    """그날까지의 일별 값으로 날마다 다시 잰 %ile 이 같은 쪽 극단인 연속 일수 — 앞에서 전부 세고 끝에서 읽는다."""
    run = 0
    for i in range(1, len(ser)):                     # 마지막 칸이 오늘
        p = _vt_pct(ser[:i + 1])
        ok = p is not None and (_vt_visible(p) >= VT_DAY_HI if hi else _vt_visible(p) <= VT_DAY_LO)
        run = run + 1 if ok else 0
    return run


def _vt_day_change_check(P: dict) -> None:
    """``today.day_change`` — 극단 지속 일수와 어제와 부호가 바뀐 섹터를 일별 배열에서 다시 센다."""
    DC = (P.get("today") or {}).get("day_change")
    chk("T", "하루 단위 변화 블록(today.day_change)이 있는가", isinstance(DC, dict))
    if not isinstance(DC, dict):
        return
    dates, nb = P["dates"], P.get("n_by_sector") or {}
    chk("T", "하루 단위 변화 — 비교한 날 = 페이로드 끝에서 둘째 날",
        DC.get("prev_date") == (dates[-2] if len(dates) > 1 else None), f"{DC.get('prev_date')}")
    bad_e, bad_f, n_e, n_f = [], [], 0, 0
    for mk, mks in [("전체", list(P["markets"]))] + [(m, [m]) for m in P["markets"]]:
        present = [s for s in P["sectors"] if any(nb.get(m, {}).get(s) for m in mks)]
        for a in VT_DAY_ACTORS:
            sers = {s: x for s in present if (x := _vt_series(P, mks, s, a)) is not None}
            ext = ((DC.get("extreme") or {}).get(mk) or {}).get(a) or {}
            for side, hi in (("hi", True), ("lo", False)):
                got = ext.get(side) or {}
                # 끝이 극단인 칸만 긴 재계산을 한다 — 앞에서 다 세면 칸마다 260번 정렬이다.
                want = {s: _vt_extreme_days(x, hi) for s, x in sers.items()
                        if (p := _vt_pct(x)) is not None and (_vt_visible(p) >= VT_DAY_HI if hi else _vt_visible(p) <= VT_DAY_LO)}
                n_e += len(want)
                if got != want:
                    diff = sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k))[:2]
                    bad_e.append(f"{mk}/{a}/{side} {[(k, got.get(k), want.get(k)) for k in diff]}")
            if len(dates) < 2:
                continue
            g_now, g_prev = sum(abs(x[-1]) for x in sers.values()), sum(abs(x[-2]) for x in sers.values())
            want_f = sorted(((s, x[-2], x[-1]) for s, x in sers.items()
                             if s != "(미분류)" and sum(int(nb.get(m, {}).get(s) or 0) for m in mks) >= VT_STREAK_MIN
                             and ((x[-1] > 0 > x[-2]) or (x[-1] < 0 < x[-2]))
                             and abs(x[-1]) >= VT_DAY_FLIP_SHARE * g_now and abs(x[-2]) >= VT_DAY_FLIP_SHARE * g_prev),
                            key=lambda t: (-abs(t[2]), t[0]))
            got_f = ((DC.get("flips") or {}).get(mk) or {}).get(a) or []
            n_f += len(want_f)
            if ([f.get("sector") for f in got_f] != [t[0] for t in want_f]
                    or any(abs(f["prev"] - t[1]) > VT_DAY_TOL or abs(f["now"] - t[2]) > VT_DAY_TOL
                           for f, t in zip(got_f, want_f))):
                bad_f.append(f"{mk}/{a} {[f.get('sector') for f in got_f][:3]} vs {[t[0] for t in want_f][:3]}")
    chk("T", "하루 단위 변화 — 극단 지속 일수 재계산(날마다 그날까지의 분포)", not bad_e,
        f"{n_e}칸 · {len(bad_e)}건  " + "; ".join(bad_e[:2]))
    chk("T", "하루 단위 변화 — 어제와 부호가 바뀐 섹터 재계산(크기 문턱·얇은 섹터 제외)", not bad_f,
        f"{n_f}칸 · {len(bad_f)}건  " + "; ".join(bad_f[:2]))


def _vt_concentration_check(P: dict) -> None:
    """``today.concentration`` — 거래대금 집중을 종목 1일 값과 일별 배열에서 다시 센다."""
    C = (P.get("today") or {}).get("concentration")
    chk("T", "거래대금 집중 블록(today.concentration)이 있는가", isinstance(C, dict))
    if not isinstance(C, dict):
        return
    nb = P.get("n_by_sector") or {}
    bad, n_rows = [], 0
    for mk, mks in [("전체", list(P["markets"]))] + [(m, [m]) for m in P["markets"]]:
        by_sec: dict = {}
        for code, nm in (P.get("names") or {}).items():
            w = (nm.get("win") or {}).get("1")
            if w and nm.get("market") in mks and w.get("tv"):
                by_sec.setdefault(nm.get("sector"), []).append((str(code), w))
        tv = {s: x[-1] for s in P["sectors"] if any(nb.get(m, {}).get(s) for m in mks)
              and (x := _vt_series(P, mks, s, "tv"))}
        total = sum(tv.values())
        want = sorted((s for s, v in tv.items()
                       if s != "(미분류)" and sum(int(nb.get(m, {}).get(s) or 0) for m in mks) >= VT_STREAK_MIN
                       and total and v >= VT_CONC_SHARE * total and by_sec.get(s)), key=lambda s: (-tv[s], s))
        got = (C.get("markets") or {}).get(mk) or []
        if [r.get("sector") for r in got] != want:
            bad.append(f"{mk} 섹터 {[r.get('sector') for r in got]} vs {want}")
            continue
        for r in got:
            n_rows += 1
            s = r["sector"]
            top = sorted(by_sec[s], key=lambda t: (-t[1]["tv"], t[0]))[:VT_CONC_TOP]
            if [t.get("code") for t in r.get("top") or []] != [c for c, _w in top]:
                bad.append(f"{mk}/{s} 상위 {r.get('top')} vs {[c for c, _w in top]}")
                continue
            share = sum(w["tv"] for _c, w in top) / tv[s] * 100.0
            if abs((r.get("top_share_pct") or 0.0) - share) > 0.051:
                bad.append(f"{mk}/{s} 상위 몫 {r.get('top_share_pct')} vs {share:.2f}")
            if abs((r.get("market_share_pct") or 0.0) - tv[s] / total * 100.0) > 0.051:
                bad.append(f"{mk}/{s} 시장 몫 {r.get('market_share_pct')}")
            for a in VT_DAY_ACTORS:
                ser = _vt_series(P, mks, s, a)
                exp = (ser[-1] if ser else 0.0) - sum(
                    ((w.get("invtrt") or 0.0) + (w.get("penfnd_etc") or 0.0)) if a == "invpen" else (w.get(a) or 0.0)
                    for _c, w in top)
                if abs(((r.get("rest") or {}).get(a) or 0.0) - exp) > 0.051 + VT_DAY_TOL:
                    bad.append(f"{mk}/{s}/{a} 제외 값 {(r.get('rest') or {}).get(a)} vs {exp:.2f}")
    chk("T", "거래대금 집중 — 섹터·상위 종목·몫·제외 값 재계산", not bad, f"{n_rows}줄  " + "; ".join(bad[:2]))


#: 공표치 대조의 분모 게이트·칩 문턱 — producer(krx_truth)를 읽지 않고 따로 적는다.
VT_KRX_MIN_TV_SHARE = 0.002
VT_KRX_WARN = 5.0
#: 페이로드 주체 → krx_truth.json 공표 행 — 역시 독립 사본. 네이버는 기타법인 행이 없다(그 주체는 비교 없음).
VT_KRX_ROWS = {"inst": ("기관합계",), "forgn": ("외국인", "기타외국인"),
               "indiv": ("개인",), "etc": ("기타법인",)}
#: VERIFY.txt 표지 — ``freshness_check.py`` 가 **같은 글**을 찾는다(``tests/test_freshness_check.py``).
VT_SKIP_MARK = "SKIP 공표치 대조"
VT_SKIP_STREAK_MARK = "공표치 대조 연속 SKIP"
VT_DONE_MARK = "공표치 대조 — 오차가"
#: 공표 수치가 페이로드에 새지 않았나 — 블록 안에 이 키가 있으면 실패다(약관 미확인, 오차만 싣는다).
VT_KRX_LEAK_KEYS = ("official", "approx", "diff", "rows")


def _vt_krx_skip(P: dict, folder: "str | None", why: str) -> None:
    """공표치 대조를 건너뛴다 — 사유와 연속 횟수를 남기고 :data:`VF_SKIP_WARN` 회째부터 WARN(실패가 아니다)."""
    print(f"       {VT_SKIP_MARK} — {why}")
    streak = 1 + external_skip_streak(folder, P["dates"][-1], VT_SKIP_MARK, VT_DONE_MARK)
    msg = f"{VT_SKIP_STREAK_MARK} {streak}회(이 리포트 포함)"
    if streak >= VF_SKIP_WARN:
        warn("T", msg, "공표 시장값을 며칠째 못 받았다 — 화면 칩이 공표치 기준 오차로 판정하지 못한다. "
                       "VERIFY.txt 의 사유를 보라")
    else:
        print(f"       {msg}")


def _vt_krx_truth(P: dict, folder: "str | None") -> None:
    """``krx_truth.json`` (공표치, 사이드카) ↔ ``today.krx_truth`` (오차만).

    공표값은 파일의 **공표 행**(원)에서 다시 억으로 만들고, 근사값은 ``today.market[m].net`` 에서 읽어 주체 칸·
    시장 합계 오차·게이트·부호 반대·칩 판정(``hot``)을 따로 다시 센다. 기능이 꺼진 리포트(파일도 블록도 없음)는
    침묵, skip 블록은 SKIP 한 줄이고 연속이면 경고다 — 실패가 아니다(망·로그인 장벽은 리포트 품질이 아니다).
    파일은 있는데 블록이 없거나 수가 안 맞거나 공표 수치가 블록에 새면 실패다.
    """
    T = P.get("today") or {}
    kt = T.get("krx_truth")
    path = os.path.join(folder, "krx_truth.json") if folder else None
    have_file = bool(path and os.path.isfile(path))
    if kt is None and not have_file:
        return
    if isinstance(kt, dict) and kt.get("status") != "ok":
        _vt_krx_skip(P, folder, kt.get("reason") or "이유 없음")
        return
    bad: list[str] = []
    if not isinstance(kt, dict):
        bad.append("krx_truth.json 은 있는데 페이로드에 today.krx_truth 가 없다")
    elif not have_file:
        bad.append("today.krx_truth 는 ok 인데 폴더에 krx_truth.json 이 없다")
    else:
        official = json.load(open(path, encoding="utf-8"))
        if kt.get("date") != P["dates"][-1] or official.get("date") != P["dates"][-1]:
            bad.append(f"기준일 불일치: 블록 {kt.get('date')} · 파일 {official.get('date')} · 페이로드 {P['dates'][-1]}")
        leak = sorted({k for m in (kt.get("markets") or {}).values() for row in m.values()
                       if isinstance(row, dict) for k in row if k in VT_KRX_LEAK_KEYS})
        if leak or any(k in kt for k in VT_KRX_LEAK_KEYS + ("markets_raw",)):
            bad.append(f"공표 수치가 페이로드에 새었다: {leak}")
        n_cells = 0
        for m, rows in (official.get("rows") or {}).items():
            M = (T.get("market") or {}).get(m) or {}
            tv = M.get("tv")
            gate = None if tv is None else VT_KRX_MIN_TV_SHARE * tv
            got_m = (kt.get("markets") or {}).get(m) or {}
            err = den = 0.0
            for a, names in VT_KRX_ROWS.items():
                if not any(x in rows for x in names):
                    continue                      # 그 소스에 없는 주체 — 비교할 공표 행이 없다
                won = sum(float(str(rows.get(x, 0)).replace(",", "") or 0) for x in names if x in rows)
                off = round(won / 1e8, 2)
                got = got_m.get(a) or {}
                v = ((M.get("net") or {}).get(a) or {}).get("v")
                n_cells += 1
                if v is None:
                    bad.append(f"{m}/{a} 근사값 없음")
                    continue
                err += abs(v - off)
                den += abs(off)
                big = gate is not None and abs(off) >= gate and off != 0
                exp = round(abs(v - off) / abs(off) * 100.0, 2) if big else None
                g = got.get("gap_pct")
                if (g is None) != (exp is None) or (exp is not None and abs(g - exp) > 0.011):
                    bad.append(f"{m}/{a} 오차 {g} vs {exp}")
                flip = bool(gate is not None and off * v < 0 and max(abs(off), abs(v)) >= gate)
                if bool(got.get("flip")) != flip:
                    bad.append(f"{m}/{a} 부호 반대 {got.get('flip')} vs {flip}")
            total = round(err / den * 100.0, 2) if gate is not None and den and den >= gate else None
            t = got_m.get("total_pct")
            if (t is None) != (total is None) or (total is not None and abs(t - total) > 0.011):
                bad.append(f"{m} 합계 오차 {t} vs {total}")
            if bool(got_m.get("hot")) != bool(total is not None and total > VT_KRX_WARN):
                bad.append(f"{m} 칩 판정 {got_m.get('hot')} — 합계 {total} · 문턱 {VT_KRX_WARN}")
            print(f"       공표치 기준 오차 {m}: {'—' if total is None else f'{total:.2f}%'}"
                  f" (문턱 {VT_KRX_WARN:g}%, 소스 {official.get('source')})")
        if not n_cells:
            bad.append("대조한 칸이 0 — 파일 rows 가 비었다")
    chk("T", f"{VT_DONE_MARK} 공표 행에서 다시 나오는가", not bad, "; ".join(bad[:3]))


# ─────────────────────────────────────────────── W. 웹 — 그려진 페이지의 숫자
#
# E 층은 TUI 가 **그린 글자**를 본다. 웹은 그동안 데이터(D)만 봤고 페이지를 그려 보지 않았다 — 템플릿이
# 다른 열을 그리거나 반올림을 달리 해도(파이썬 96.5→96 vs JS 97, 2026-11-08 리뷰 M6) 매일 초록이었다.
# 여기서는 numbers.html 과 viewer.html 의 스크립트를 node 로 **실제로 돌려** 최소 DOM 에 그리게 하고,
# 그 칸의 글자를 페이로드 값(표시 규칙대로 반올림)과 TUI 가 같은 칸에 그리는 글자에 맞춘다.

#: 최소 DOM — 요소 트리·글자·dataset·이벤트만. 브라우저 API 를 흉내 내지 않는다(쓰는 것만).
_W_HARNESS = r"""
const fs=require("fs"), vm=require("vm");
const [page, probe] = [process.argv[2], process.argv[3]];
class Txt{constructor(t){this.nodeType=3;this._t=String(t);} get textContent(){return this._t;} set textContent(v){this._t=String(v);} get children(){return [];}}
class El{
  constructor(tag){this.tagName=String(tag).toUpperCase();this.children=[];this.attrs={};this.dataset={};this.style={};
    this.className="";this.listeners={};this.open=false;this.title="";this.id="";this.value="";this.type="";
    this.classList={add(){},remove(){},toggle(){},contains(){return false;}};}
  _n(n){return (typeof n==="string"||typeof n==="number")?new Txt(n):n;}
  append(...ns){for(const n of ns)this.children.push(this._n(n));}
  appendChild(n){this.append(n);return n;}
  prepend(...ns){this.children.unshift(...ns.map(n=>this._n(n)));}
  replaceChildren(...ns){this.children=[];this.append(...ns);}
  get firstChild(){if(!this.children.length)this.children.push(new Txt(""));return this.children[0];}
  get textContent(){return this.children.map(c=>c.textContent).join("");}
  set textContent(v){this.children=[new Txt(v==null?"":v)];}
  set innerHTML(v){this.children=[new Txt(String(v).replace(/<[^>]*>/g,""))];}
  get innerHTML(){return this.textContent;}
  setAttribute(k,v){this.attrs[k]=String(v);} getAttribute(k){return k in this.attrs?this.attrs[k]:null;}
  setAttributeNS(_n,k,v){this.attrs[k]=String(v);} removeAttribute(k){delete this.attrs[k];}
  addEventListener(ev,fn){(this.listeners[ev]=this.listeners[ev]||[]).push(fn);} removeEventListener(){}
  querySelector(){return null;} querySelectorAll(){return [];}
  getBoundingClientRect(){return {left:0,top:0,width:100,height:20,right:100,bottom:20};}
  get offsetWidth(){return 100;} get offsetHeight(){return 20;} get clientWidth(){return 1000;}
  focus(){} blur(){} scrollIntoView(){}
}
const ids={};
const document={
  getElementById(id){ if(!ids[id]){const e=new El("div"); e.id=id; ids[id]=e;} return ids[id]; },
  createElement(t){return new El(t);}, createElementNS(_ns,t){return new El(t);},
  createTextNode(t){return new Txt(t);}, addEventListener(){}, documentElement:new El("html"), body:new El("body"),
};
const ctx={document,console,Math,Date,JSON,Set,Map,Object,Array,Number,String,Boolean,Symbol,Intl,
  Option:function(l,v){const e=new El("option"); e.textContent=l; e.value=v; return e;},
  innerWidth:1440,innerHeight:900,addEventListener(){},matchMedia(){return {matches:false,addEventListener(){}};},
  requestAnimationFrame(f){f();},setTimeout(f){f();}};
ctx.window=ctx;
vm.createContext(ctx);
const src=fs.readFileSync(page,"utf8");
const m=src.match(/<script>([\s\S]*)<\/script>/);
vm.runInContext(m[1],ctx);
const D=(x)=>x;
ctx.__ids=ids;
ctx.__dump=function dump(e){ if(!e) return null; if(e.nodeType===3) return {t:e.textContent};
  return {tag:e.tagName.toLowerCase(), id:e.id||undefined, cls:e.className||undefined,
    data:Object.keys(e.dataset).length?e.dataset:undefined, kids:e.children.map(dump)}; };
ctx.__out=(o)=>process.stdout.write(JSON.stringify(o));
vm.runInContext(fs.readFileSync(probe,"utf8"),ctx);
"""

#: numbers.html — 창 × 시장 × 탐색(닫힘·열림)마다 본표, 시장마다 '오늘' 패널을 떠 온다.
_W_PROBE_NUMBERS = r"""
const txt=e=>e?e.textContent:null;
function table(){ const t=document.getElementById("tbl"); const [thead,tbody]=t.children;
  if(!thead) return null;
  const ths=thead.children[0].children;
  return {head:ths.map(th=>th.dataset.k), ev:ths.map(th=>{const s=th.children.filter(c=>(c.className||"").split(" ")[0]==="ev"); return s.length?s.map(x=>x.textContent).join(" · "):null;}),
    rows:(tbody?tbody.children:[]).map(tr=>({sector:tr.dataset.sector, thin:tr.className==="thin",
      cells:Object.fromEntries(tr.children.map(td=>[td.dataset.k, td.textContent]))}))}; }
function today(){ return document.getElementById("t-grid").children.map(c=>({id:c.id||null, title:txt(c.children[0]),
  lines:c.children.slice(1).map(l=>({cls:l.className, actor:(l.dataset||{}).actor, side:(l.dataset||{}).side, kind:(l.dataset||{}).kind,
    market:(l.dataset||{}).market, text:l.textContent,
    items:(l.children||[]).filter(x=>x.className==="it").map(x=>x.textContent)}))})); }
const out={wins:WINS, mkts:MKTS, views:[], today:{}};
for(const ex of [false,true]){
  document.getElementById("explore").open=ex;
  for(const w of WINS) for(const m of MKTS){ win=w; mkt=m; render();
    out.views.push({explore:ex, win:w, mkt:m, table:table()});
    if(!ex && w===WINS[0]) out.today[m]=today(); } }
__out(out);
"""

#: viewer.html — 1일 기간에서 섹터마다 순매수·순매도 상위 종목 목록.
_W_PROBE_VIEWER = r"""
i1=NDATE-1; i0=NDATE-1; market="전체"; actor="inst"; render();
const out={secs:{}};
for(const s of __SECS){ picked=s; renderNames();
  const grab=id=>document.getElementById(id).children.map(li=>{ const nm=li.children[0]||{children:[]};
    return {name:(nm.children[0]||{}).textContent, sub:((nm.children||[]).find(c=>c.className==="sub2")||{}).textContent||null,
            amt:(li.children[1]||{}).textContent, rt:(li.children[2]||{}).textContent}; });
  out.secs[s]={buy:grab("lst-buy"), sell:grab("lst-sell")}; }
__out(out);
"""


#: viewer.html — 프리셋 창 × 시장 × 주체마다 KPI 카드·랭킹 막대·전체 표·종목 층 표지(리뷰 D5-M1·D2-M1).
#: 예전 W 층은 뷰어의 1일 종목 목록만 그려 봐서, 본표·막대·카드가 다른 반올림(`toLocaleString`)을 써도 초록이었다.
_W_PROBE_VIEWER_FULL = r"""
const txt=e=>e?e.textContent:null;
const cl=e=>(e&&e.className)||"";
const out={views:[]};
const MK=["전체",...D.markets];
for(const [w] of PRESETS){ if(!w||w>NDATE) continue;
  for(const m of MK) for(const [a] of ACTORS){
    i1=NDATE-1; i0=NDATE-w; market=m; actor=a; metric="share"; picked=null; render();
    const v={win:w, mkt:m, actor:a};
    v.tiles=document.getElementById("tiles").children.map(t=>({k:txt(t.children[0]), v:txt(t.children[1])}));
    v.bars=document.getElementById("rows").children.map(b=>({name:txt(b.children[0].children[0].children[0]),
      val:txt(b.children[2].children[0]), sub:txt(b.children[2].children[1])}));
    v.ev={amt:txt(document.getElementById("ev-buy-amt")), share:txt(document.getElementById("ev-buy-share")),
          trust:txt(document.getElementById("ev-buy-trust")),
          sell:[txt(document.getElementById("ev-sell-amt")), txt(document.getElementById("ev-sell-share")),
                txt(document.getElementById("ev-sell-trust"))]};
    if(a==="inst"){ const t=document.getElementById("tbl"); const [thead,tbody]=t.children;
      v.head=thead.children[0].children.map(th=>({h:txt(th.children[0]), ev:txt(th.children.find(c=>cl(c)==="ev"))}));
      v.rows=tbody.children.map(tr=>tr.children.map(td=>txt(td))); }
    out.views.push(v); } }
__out(out);
"""


def _w_viewer_expect(P: dict, w: int, m: str) -> dict:
    """뷰어가 그릴 글자 — **템플릿 JS 를 읽지 않고** 페이로드에서 다시 계산한다(구간말 월 시총, 얇은 섹터 무관)."""
    nd = len(P["dates"])
    i0, i1 = nd - w, nd - 1
    mks = list(P["markets"]) if m == "전체" else [m]
    ym = P["dates"][i1][:7]
    nb = P.get("n_by_sector") or {}

    def tot(sec, k):
        # 잔차 제거는 뷰어 `Z`·블록 `sector_numbers.zsum` 과 같은 문턱 — 상쇄된 0 의 부호를 맞춘다.
        t = sum(sum(((P["flows"].get(mk) or {}).get(sec) or {}).get(k, [0.0] * nd)[i0:i1 + 1]) for mk in mks)
        return 0.0 if abs(t) < 1e-6 else t

    def cap(sec):
        c = 0.0
        for mk in mks:
            bym = ((P.get("cap_by_month") or {}).get(mk) or {}).get(sec) or {}
            c += bym[ym] if bym.get(ym) is not None else ((P.get("cap") or {}).get(mk) or {}).get(sec, 0.0)
        return c
    shaped = bool(nb) and all(isinstance(nb.get(mk), dict) for mk in mks)
    present = [s for s in P["sectors"] if not shaped or any((nb[mk].get(s) or 0) > 0 for mk in mks)]
    def mtot(k):
        t = sum(tot(s, k) for s in P["sectors"])
        return 0.0 if abs(t) < 1e-6 else t
    out = {"tot": {k: mtot(k) for k in ("inst", "forgn", "indiv", "etc", "tv")},
           "sec": {}}
    for s in present:
        c = cap(s)
        out["sec"][s] = {k: tot(s, k) for k in ("inst", "forgn", "indiv", "etc", "tv")}
        out["sec"][s]["cap"] = c
    return out


def _w_node() -> "str | None":
    import shutil
    return shutil.which("node") or ("/usr/bin/node" if os.path.exists("/usr/bin/node") else None)


def _w_run(node: str, page: str, probe: str) -> dict:
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        h, pr = os.path.join(td, "h.js"), os.path.join(td, "probe.js")
        open(h, "w", encoding="utf-8").write(_W_HARNESS)
        open(pr, "w", encoding="utf-8").write(probe)
        p = subprocess.run([node, h, page, pr], capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip().splitlines()[-1] if p.stderr.strip() else f"node rc={p.returncode}")
    return json.loads(p.stdout)


def _w_rhu(v: float, nd: int = 0) -> float:
    """표시 반올림 — 0.5 는 0 에서 먼 쪽. **producer 함수를 부르지 않고** 여기 다시 적는다."""
    m = 10 ** nd
    q = math.floor(abs(v) * m + 0.5 + 1e-9) / m
    return q if v >= 0 else -q


def _w_A(v) -> str:
    return "—" if v is None else ("+" if v >= 0 else "−") + f"{abs(_w_rhu(v)):,.0f}"


def _w_P(v, nd: int = 2) -> str:
    return "—" if v is None else ("+" if v >= 0 else "−") + f"{abs(_w_rhu(v, nd)):.{nd}f}"


def _w_N0(v) -> str:
    return "—" if v is None else f"{_w_rhu(v):,.0f}"


def _w_PI(p) -> str:
    return "—" if p is None else str(int(_w_rhu(p)))


def _w_pc(p) -> str:
    return "(—)" if p is None else f"({int(_w_rhu(p))})"


def _w_trusted(P: dict, mks: list, win: str) -> dict:
    out: dict = {}
    for nm in (P.get("names") or {}).values():
        if nm.get("market") not in mks:
            continue
        w = (nm.get("win") or {}).get(win) or {}
        if w.get("invtrt") is None and w.get("penfnd_etc") is None:
            continue
        out[nm["sector"]] = out.get(nm["sector"], 0.0) + (w.get("invtrt") or 0.0) + (w.get("penfnd_etc") or 0.0)
    # 합의 이진 잔차를 지운다 — 상쇄돼 0 인 합이 더하는 순서에 따라 ±2.7e-17 이 되면 부호가 '+0'·'−0' 으로 갈린다
    # (2026-09-01 종이/목재·09-09 금융 재구성: 웹 '+0' · TUI '−0'). 웹·TUI 도 같은 자리에서 지운다.
    return {s: _w_rhu(v, 6) + 0.0 for s, v in out.items()}


_W_SIGN = re.compile(r"(^|[\s(\[·])-(?=\d)")


def _w_minus(text: str) -> str:
    """TUI 의 ASCII 음수 부호(``-90``)를 웹의 U+2212(``−90``)로 — **숫자 앞의 부호만**.

    통째 ``replace("-", "−")`` 는 이름 안의 하이픈까지 바꿔 ``S-Oil`` 이 ``S−Oil`` 이 되고, 같은 글자를
    불일치로 냈다(2026-09~11 재구성 13일, 화학 'S-Oil'·금속 '와이지-원'). 부호는 줄 처음이나 공백·괄호·
    ``[``·``·`` 바로 뒤에서 숫자 앞에만 온다 — 이름 속 하이픈은 글자 뒤에 붙는다.
    """
    return _W_SIGN.sub(lambda m: m.group(1) + "−", text)


def _w_day_stocks(P: dict, mks: list, actor: str) -> dict:
    out: dict = {}
    for nm in (P.get("names") or {}).values():
        if nm.get("market") not in mks:
            continue
        w = (nm.get("win") or {}).get("1")
        if not w:
            continue
        if actor == "invpen":
            if w.get("invtrt") is None and w.get("penfnd_etc") is None:
                continue
            v = (w.get("invtrt") or 0.0) + (w.get("penfnd_etc") or 0.0)
        elif w.get(actor) is None:
            continue
        else:
            v = w[actor]
        out.setdefault(nm["sector"], []).append((nm.get("name") or "—", v))
    for arr in out.values():
        arr.sort(key=lambda t: (-t[1], t[0]))
    return out


#: 웹 본표의 열 키 — 기본과 탐색(x 계열·G). **템플릿을 읽지 않고** 여기 적는다(명세).
W_COLS_DEFAULT = ("sector", "n_all", "inst", "accel", "pct", "ret", "trust5", "trustw",
                  "forgn", "indiv", "etc", "tv", "cap", "drv")
W_COLS_EXPLORE = ("G", "exp", "x", "P", "xdot", "xddot")


def _w_wording(folder: str) -> None:
    """그날 만든 수치표·뷰어의 글(심은 데이터 줄 제외)에 금칙어가 없는가 — 표는 ``sectorflow.wording`` 하나다."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
    from sectorflow.wording import rendered_page_hits
    for name in ("numbers.html", "viewer.html"):
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                hits = rendered_page_hits(f.read())
        except OSError as e:
            hits = [f"못 읽었다: {e}"]
        chk("W", f"금칙어 — {name} 의 글에 권유·전망처럼 읽히는 말이 없는가", not hits, ", ".join(hits))


def layer_w(D: dict, P: dict, folder: str) -> None:
    """W. **웹 화면** — numbers.html(본표·'오늘' 패널)과 viewer.html(1일 종목 목록)을 node 로 그려 대조한다."""
    print("\nW. 웹 — 그려진 페이지의 숫자가 페이로드·TUI 와 같은가")
    node = _w_node()
    # node 가 없는 것은 **환경** 실패다(코드 결함이 아니다) — 이름에 그렇게 적는다. 그래도 그려 보지 않은 채 초록을 내지 않는다.
    chk("W", "환경 — node 가 있는가(웹 페이지를 그려 본다)", node is not None,
        node or "환경 실패(코드 결함 아님): PATH·/usr/bin 에 node 가 없다 — 웹 대조를 못 했다")
    if not node:
        return
    _w_wording(folder)
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
    from sectorflow.tui import flow_view as FV
    out = _w_run(node, os.path.join(folder, "numbers.html"), _W_PROBE_NUMBERS)
    T = D.get("today") or {}
    mk_all = [m for m in out["mkts"] if m != "전체"]

    def mkof(m):
        return mk_all if m == "전체" else [m]
    chk("W", "1일 탭이 있는가(오늘 블록이 있는 리포트)", (1 in out["wins"]) == bool(T.get("sectors")),
        f"탭 {out['wins']}")

    bad_head, bad_cell, bad_order, bad_ev, bad_tui, n_cells = [], [], [], [], [], 0
    for v in out["views"]:
        w, m, ex, tb = v["win"], v["mkt"], v["explore"], v["table"]
        if tb is None:
            bad_head.append(f"{w}|{m} 표가 비었다")
            continue
        want_head = list(W_COLS_DEFAULT[:8]) + (list(W_COLS_EXPLORE) if ex else []) + list(W_COLS_DEFAULT[8:])
        if tb["head"] != want_head:
            bad_head.append(f"{w}|{m}|탐색{int(ex)} {tb['head']}")
            continue
        mks = mkof(m)
        t5, tw = _w_trusted(P, mks, "5"), _w_trusted(P, mks, str(w))
        if w == 1:
            S = (T.get("sectors") or {}).get(m) or {}
            cap5 = {r["sector"]: r.get("cap") for r in (D["blocks"].get(f"5|{m}") or {}).get("rows", [])}
            cnt: dict = {}
            for nm in (P.get("names") or {}).values():
                if nm.get("market") in mks and "1" in (nm.get("win") or {}):
                    cnt[nm["sector"]] = cnt.get(nm["sector"], 0) + 1
            rows = []
            for s, x in S.items():
                r = {"sector": s, "n_all": cnt.get(s, 0), "thin": cnt.get(s, 0) < 10, "cap": cap5.get(s),
                     "ret": x.get("ret"), "tv": x.get("tv"), "accel": None, "pct": (x.get("inst") or {}).get("pct")}
                for a in ("inst", "forgn", "indiv", "etc"):
                    r[a] = (x.get(a) or {}).get("v")
                rows.append(r)
        else:
            rows = [dict(r, pct=(r.get("pct1y") or {}).get("inst")) for r in D["blocks"][f"{w}|{m}"]["rows"]]
        rows = [r for r in rows if not (r.get("n_all") == 0 and not r.get("cap"))]
        want = {}
        for r in rows:
            s = r["sector"]
            want[s] = {"sector": s + ("~" if r.get("thin") else ""), "n_all": "—" if r.get("n_all") is None else str(r["n_all"]),
                       "inst": _w_A(r.get("inst")), "accel": _w_P(r.get("accel")), "pct": _w_PI(r.get("pct")),
                       "ret": _w_P(r.get("ret")), "trust5": _w_A(t5.get(s)), "trustw": _w_A(tw.get(s)),
                       "forgn": _w_A(r.get("forgn")), "indiv": _w_A(r.get("indiv")), "etc": _w_A(r.get("etc")),
                       "tv": _w_N0(r.get("tv")), "cap": _w_N0(r.get("cap"))}
            if ex:
                want[s].update(x=_w_P(r.get("x")), exp=_w_P(r.get("exp")), P=_w_P(r.get("P"), 3),
                               xdot=_w_P(r.get("xdot"), 3), xddot=_w_P(r.get("xddot"), 3))
        got = {r["sector"]: r for r in tb["rows"]}
        if set(got) != set(want):
            bad_cell.append(f"{w}|{m} 섹터 집합 남음 {sorted(set(got) - set(want))[:2]} 빠짐 {sorted(set(want) - set(got))[:2]}")
        for s in set(got) & set(want):
            for k, val in want[s].items():
                n_cells += 1
                if got[s]["cells"].get(k) != val:
                    bad_cell.append(f"{w}|{m}|{s}|{k} 웹 {got[s]['cells'].get(k)!r} vs {val!r}")
        # 순서 — 기본 정렬 가속 내림차순(값 없으면 기관 임펄스), 얇은 섹터는 맨 아래.
        key = "accel" if any(r.get("accel") is not None for r in rows) else "inst"
        order = sorted(rows, key=lambda r: (bool(r.get("thin")), r.get(key) is None, -(r.get(key) or 0.0)))
        seq = [r["sector"] for r in tb["rows"]]
        exp_seq = [r["sector"] for r in order]
        if [s for s in seq if s in want] != exp_seq and len({r.get(key) for r in rows}) == len(rows):
            bad_order.append(f"{w}|{m}")
        # 증거 표지 — TUI(evidence_at)와 같은가. 이 표의 금액 열은 기관 기준이다.
        evk = {"inst": ("inst", "inst", None), "accel": ("accel", "inst", None), "forgn": ("forgn", "forgn", None),
               "trust5": ("trust", "invpen", "5"), "trustw": ("trust", "invpen", None)}
        for k, e in zip(tb["head"], tb["ev"]):
            ek, actor, fix = evk.get(k, (k, "inst", None))
            ev = FV.evidence_at(ek, fix or str(w), actor, m) if ek in FV.EVIDENCE else None
            want_ev = None if ev is None else ev[0] + (f" · {ev[1]}" if ev[1] else "")
            if e != want_ev:
                bad_ev.append(f"{w}|{m}|{k} 웹 {e} vs TUI {want_ev}")
        # TUI 가 같은 칸에 그리는 글자(리뷰 M6) — 반올림 규칙이 하나인가.
        if str(w) in FV.WINDOWS and not ex:
            st = FV.State(D)
            st.wi, st.mi, st.ai = FV.WINDOWS.index(str(w)), st.markets.index(m), 0
            cols = {c.header: c for c in FV.table_cols(st, 400)}
            pairs = (("1년[%ile]", "pct"), ("임펄스[억]", "inst"), ("가속[%p]", "accel"), ("수익률[%]", "ret"),
                     ("투신+연기금5일[억]", "trust5"), ("투신+연기금[억]", "trustw"), ("종목[수]", "n_all"))
            for r in st.rows():
                g = got.get(r["sector"])
                if not g:
                    continue
                for hdr, k in pairs:
                    tui = _w_minus(cols[hdr].fn(r, st).strip()) if hdr in cols else None
                    web = g["cells"].get(k)
                    if tui is not None and web is not None and tui != web and not (tui == "—" and web == "—"):
                        bad_tui.append(f"{w}|{m}|{r['sector']}|{hdr} TUI {tui!r} vs 웹 {web!r}")
    chk("W", "본표 열 = 명세(기본·탐색)", not bad_head, "; ".join(bad_head[:2]))
    chk("W", "본표 칸 = 페이로드 값(표시 반올림)", not bad_cell and n_cells > 0,
        f"{len(bad_cell)}/{n_cells}  " + "; ".join(bad_cell[:3]))
    chk("W", "본표 순서 = 가속 내림차순·얇은 섹터 맨 아래", not bad_order, ", ".join(bad_order[:3]))
    chk("W", "본표 증거 표지 = TUI 의 표지(창·주체 범위)", not bad_ev, "; ".join(bad_ev[:3]))
    chk("W", "본표 칸 = TUI 가 같은 칸에 그리는 글자", not bad_tui, "; ".join(bad_tui[:3]))

    # '오늘' 패널
    if T.get("sectors"):
        bad_t, bad_tt = [], []
        for m in out["mkts"]:
            cards = {c["id"]: c for c in out["today"].get(m) or [] if c["id"]}
            net = ((T.get("market") or {}).get(m) or {}).get("net") or {}
            want_net = [f"{ko} {_w_A((net.get(k) or {}).get('v'))}{_w_pc((net.get(k) or {}).get('pct'))}"
                        for k, ko in (("inst", "기관"), ("forgn", "외국인"), ("indiv", "개인"), ("etc", "기타법인"))]
            got_net = (cards.get("t-net") or {"lines": [{}]})["lines"][0].get("items")
            if got_net != want_net:
                bad_t.append(f"{m} 순매수 {got_net} vs {want_net}")
            # TUI 첫 화면과 같은 말인가 — 주체 목록(최대 순매매 주체 포함)·상위/하위·극단.
            st = FV.State(D)
            st.mi = st.markets.index(m)
            acts = FV.banner_actors(D, m)
            rank = [ln for ln in (cards.get("t-rank") or {}).get("lines", []) if ln.get("actor")]
            if [ln["actor"] for ln in rank if ln["side"] == "top"] != [a for a, _ in acts]:
                bad_tt.append(f"{m} 주체 {[ln['actor'] for ln in rank if ln['side'] == 'top']} vs TUI {[a for a, _ in acts]}")
            for ln in rank:
                a, side = ln["actor"], ln["side"]
                S = ((T.get("sectors") or {}).get(m)) or {}
                lst = sorted(((s, (x.get(a) or {}).get("v"), (x.get(a) or {}).get("pct")) for s, x in S.items()
                              if (x.get(a) or {}).get("v") is not None), key=lambda t: (-t[1], t[0]))
                pick = lst[:3] if side == "top" else lst[::-1][:3]
                stocks = _w_day_stocks(P, mkof(m), a)
                # 섹터 그날 수익률(리뷰 3차 D3-m1) — 1일 창 수익률 열과 같은 값, 소수 2자리.
                sret = {s: (S.get(s) or {}).get("ret") for s, _v, _p in pick}
                want_items = []
                for s, v, p in pick:
                    arr = stocks.get(s) or []
                    tag = ([t for t in arr if t[1] > 0][:2] if side == "top" else [t for t in arr[::-1] if t[1] < 0][:2])
                    r = sret[s]
                    want_items.append(f"{s} {_w_A(v)}{_w_pc(p)} {'—' if r is None else _w_P(r) + '%'}"
                                      + ("[" + "·".join(f"{n} {_w_A(x)}" for n, x in tag) + "]" if tag else ""))
                if ln["items"] != want_items:
                    bad_t.append(f"{m}/{a}/{side} {ln['items'][:1]} vs {want_items[:1]}")
                tui_rets = FV.sector_day_ret(D, m)
                tui_items = [f"{s} {FV.fmt_amt(v)}{FV._pct_tag(p)} {FV._fmt_ret(tui_rets.get(s))}" for s, v, p in pick]
                if [i.split("[")[0] for i in ln["items"]] != [_w_minus(t) for t in tui_items]:
                    bad_tt.append(f"{m}/{a}/{side} 웹 {ln['items'][:1]} vs TUI {tui_items[:1]}")
                # 종목 꼬리표 — 웹은 이름을 다 쓰고 TUI 는 10칸에서 자른다. 웹 꼬리표를 TUI 규칙으로 잘라 같은지 본다.
                tstocks = FV.day_stocks(D, mkof(m), a)
                for item, (s, _v, _p) in zip(ln["items"], pick):
                    web_tag = item[item.find("[") + 1:item.rfind("]")] if "[" in item else ""
                    clipped = "·".join(f"{FV.clip(n, 10)} {x}" for n, x in
                                       (t.rsplit(" ", 1) for t in web_tag.split("·") if t)) if web_tag else ""
                    tui_tag = FV._stock_tag(tstocks.get(s) or [], "buy" if side == "top" else "sell", 2)
                    if (f"[{clipped}]" if clipped else "") != _w_minus(tui_tag):
                        bad_tt.append(f"{m}/{a}/{side}/{s} 종목 웹 {web_tag!r} vs TUI {tui_tag!r}")
                        break
            for ln in (cards.get("t-ext") or {}).get("lines", []):
                if not ln.get("actor"):
                    continue
                hi, lo = FV.today_extremes(D, m, ln["actor"])
                if ln["items"] != (hi if ln["side"] == "hi" else lo):
                    bad_tt.append(f"{m}/{ln['actor']}/{ln['side']} 웹 {ln['items'][:2]} vs TUI {(hi if ln['side'] == 'hi' else lo)[:2]}")
            # 마감 값(확정·창 밖 재구성 finalize_unverified)이 아닐 때만 (잠정) 이다 — 페이지의 CLOSED 와 같은 판단.
            if not (D.get("final") or D.get("finalize_unverified")) and "(잠정)" not in ((cards.get("t-ext") or {}).get("title") or ""):
                bad_t.append(f"{m} 잠정 리포트인데 극단 목록에 (잠정) 표지가 없다")
            for ln in (cards.get("t-ret") or {}).get("lines", []):
                mm = ln.get("market")
                if not mm:
                    continue
                own = ((T.get("market") or {}).get(mm) or {}).get("ret") or {}
                kr = (((T.get("krx_index") or {}).get(mm) or {}).get("ret")) or {}
                # 글자 토큰을 순서대로 꺼내 정확히 맞춘다 — 부분 문자열로 보면 "−4.37%" 가 "−14.37%" 안에서도 찾아진다.
                toks = re.findall(r"[+−]\d[\d,]*\.\d{2}%|—", ln["text"])
                want_t = ["—" if v is None else _w_P(v) + "%"
                          for v in (own.get("1"), own.get("20"), kr.get("1"), kr.get("20"))]
                if toks != want_t:
                    bad_t.append(f"{m}/{mm} 수익률 {toks} vs {want_t}")
        chk("W", "'오늘' 패널 = 페이로드(시장 4주체·상위/하위+종목·수익률·잠정 표지)", not bad_t, "; ".join(bad_t[:3]))
        chk("W", "'오늘' 패널 = TUI 첫 화면(주체·상위/하위·%ile 극단 글자)", not bad_tt, "; ".join(bad_tt[:3]))
        # 연속 순매매 줄과 잠정 수정 폭 줄 — TUI 가 첫 화면에 그리는 글자(가장 긴 꼴)와 같은가.
        bad_sk = []
        for m in out["mkts"]:
            cards = {c["id"]: c for c in out["today"].get(m) or [] if c["id"]}
            want = FV.streak_items(D, m)
            card = cards.get("t-streak")
            got = ((card or {}).get("lines") or [{}])[0].get("items") if card else None
            if (want is None) != (card is None) or (want is not None and got != [_w_minus(w) for w in want]):
                bad_sk.append(f"{m} 연속 웹 {(got or [None])[:2]} vs TUI {(want or [None])[:2]}")
            rl = FV.revision_line(D)
            web = [ln["text"] for ln in (cards.get("t-net") or {}).get("lines", []) if "rev" in (ln.get("cls") or "")]
            if (rl.strip() if rl else None) != (web[0] if web else None):
                bad_sk.append(f"{m} 수정 폭 웹 {web[:1]} vs TUI {rl!r}")
        chk("W", "'오늘' 패널 연속 순매매·잠정 수정 폭 = TUI 첫 화면 글자", not bad_sk, "; ".join(bad_sk[:3]))
        # 기관 세부·어제와 부호가 바뀐 섹터·거래대금 집중 — TUI 첫 화면과 같은 글자인가.
        bad_day = []
        for m in out["mkts"]:
            cards = {c["id"]: c for c in out["today"].get(m) or [] if c["id"]}
            want = FV.market_detail_items(D, m)
            got = [ln.get("items") for ln in (cards.get("t-net") or {}).get("lines", []) if ln.get("kind") == "detail"]
            if (want is None) != (not got) or (want is not None and got[0] != [_w_minus(w) for w in want]):
                bad_day.append(f"{m} 기관 세부 웹 {got[:1]} vs TUI {want}")
            acts = [a for a, _ko in FV.banner_actors(D, m)]
            lines = [ln for ln in (cards.get("t-day") or {}).get("lines", []) if ln.get("actor")]
            if FV.day_flip_items(D, m, acts[0]) is None:
                if cards.get("t-day"):
                    bad_day.append(f"{m} 옛 리포트인데 부호 줄이 있다")
            elif [ln["actor"] for ln in lines] != acts:
                bad_day.append(f"{m} 부호 줄 주체 {[ln['actor'] for ln in lines]} vs {acts}")
            else:
                for ln in lines:
                    # 화살표 뒤의 음수 부호는 `_w_minus` 가 못 본다(숫자 앞이 공백·괄호가 아니다) — 화살표를 먼저 바꿔 띄워 본다.
                    tui = [_w_minus(w.replace(FV.TREND_ARROW, "→ ")).replace("→ ", "→")
                           for w in FV.day_flip_items(D, m, ln["actor"])]
                    if ln["items"] != tui:
                        bad_day.append(f"{m}/{ln['actor']} 부호 웹 {ln['items'][:2]} vs TUI {tui[:2]}")
            tui = FV.concentration_items(D, m) or []
            card = cards.get("t-conc")
            got = ((card or {}).get("lines") or [{}])[0].get("items") if card else []
            if got != [_w_minus(w) for w in tui]:
                bad_day.append(f"{m} 집중 웹 {got[:2]} vs TUI {tui[:2]}")
        chk("W", "'오늘' 패널 기관 세부·부호 바뀜·거래대금 집중 = TUI 첫 화면 글자", not bad_day, "; ".join(bad_day[:3]))

    # 뷰어 1일 종목 목록
    vpath = os.path.join(folder, "viewer.html")
    if os.path.isfile(vpath) and T.get("sectors"):
        S1 = (T.get("sectors") or {}).get("전체") or {}
        secs = sorted(S1, key=lambda s: -abs((S1[s].get("inst") or {}).get("v") or 0.0))[:6]
        vo = _w_run(node, vpath, f"const __SECS={json.dumps(secs, ensure_ascii=False)};\n" + _W_PROBE_VIEWER)
        bad_v, n_v = [], 0
        for s in secs:
            rows = []
            for code, nm in (P.get("names") or {}).items():
                w = (nm.get("win") or {}).get("1")
                if nm.get("sector") != s or not w or w.get("inst") is None:
                    continue
                cap, mark = nm.get("cap"), "!" if nm.get("cap_pending") else ""
                rows.append((nm.get("name"), w["inst"], w.get("invtrt"), w.get("penfnd_etc"),
                             _w_P(w["inst"] / cap * 100.0) + "%p" + mark if cap and cap > 0 else "—"))
            rows.sort(key=lambda t: -t[1])
            buy_all = [t for t in rows if t[1] > 0]
            sell_all = [t for t in rows if t[1] < 0][::-1]
            for side, everyone in (("buy", buy_all), ("sell", sell_all)):
                want = everyone[:6]
                got = (vo["secs"].get(s) or {}).get(side) or []
                if not want:
                    continue
                n_v += len(want)
                if [g["amt"] for g in got] != [_w_A(v) + "억" for _n, v, _i, _p, _r in want]:
                    bad_v.append(f"{s}/{side} {[g['amt'] for g in got][:2]} vs {[_w_A(v) + '억' for _n, v, _i, _p, _r in want][:2]}")
                # 같은 금액(표시 0.1억 단위)끼리는 순서를 묻지 않는다 — 뷰어(JS)와 여기(파이썬)의 동률 정렬이 다르다
                # (2026-09-15 금융: LG·JB금융지주 둘 다 +37.9억). 금액 순서는 위에서 이미 봤다.
                # 짝은 잘린 6개 안이 아니라 **그 쪽 전체**에서 찾는다 — 동률이 6번째 자리를 걸치면 뷰어는 잘린 밖의
                # 종목을 고를 수 있다(2026-10-30 유통: −0.5억 셋 중 에스에이엠티, 2026-09-16 IT 서비스: +7.5억 둘 중 넥슨게임즈).
                def _sub(iv, pf):
                    return f"투신 {'—' if iv is None else _w_A(iv)} · 연기금 {'—' if pf is None else _w_A(pf)}억"
                for g, (_n, v, _i, _p, _r) in zip(got, want):
                    peers = [(n, _sub(iv, pf), rt) for n, v2, iv, pf, rt in everyone if round(v2, 1) == round(v, 1)]
                    if not any((g.get("name") or "").startswith(n or "") and sub == g.get("sub") and rt == g.get("rt")
                               for n, sub, rt in peers):
                        bad_v.append(f"{s}/{side} {g.get('name')} {g.get('sub')} {g.get('rt')}")
                        break
        chk("W", "뷰어 1일 종목 목록 = names.win['1'](금액·투신·연기금·시총대비)", not bad_v and n_v > 0,
            f"{len(secs)}섹터 {n_v}종목  " + "; ".join(bad_v[:2]))
    if os.path.isfile(vpath):
        _w_viewer_full(D, P, node, vpath, FV)


def _w_viewer_full(D: dict, P: dict, node: str, vpath: str, FV) -> None:
    """뷰어 전 화면(카드·막대·전체 표)의 숫자 = 페이로드(표시 반올림) = TUI 블록, 종목 층 표지 = TUI(창·주체별)."""
    vo = _w_run(node, vpath, _W_PROBE_VIEWER_FULL)
    ACT = (("inst", "기관"), ("forgn", "외국인"), ("indiv", "개인"), ("etc", "기타법인"))
    bad_c, bad_tile, bad_bar, bad_tui, bad_ev = [], [], [], [], []
    n_c = n_bar = n_ev = 0
    exp_cache: dict = {}
    for v in vo["views"]:
        w, m, a = v["win"], v["mkt"], v["actor"]
        if (w, m) not in exp_cache:
            exp_cache[(w, m)] = _w_viewer_expect(P, w, m)
        E = exp_cache[(w, m)]
        tag = f"{w}|{m}|{a}"
        # KPI 카드 — 구간 거래대금(부호 없음)과 4주체 누적.
        want_t = [("구간 거래대금", _w_A(E["tot"]["tv"]).replace("+", ""))] + [(ko, _w_A(E["tot"][k])) for k, ko in ACT]
        if [(t["k"], t["v"]) for t in v["tiles"]] != want_t:
            bad_tile.append(f"{tag} {[(t['k'], t['v']) for t in v['tiles']][:2]} vs {want_t[:2]}")
        # 랭킹 막대 — 가속(1일은 금액) 값과 옆의 다른 단위.
        for b in v["bars"]:
            sec = (b["name"] or "").rstrip("~")
            x = E["sec"].get(sec)
            if x is None:
                bad_bar.append(f"{tag} {sec} 페이로드에 없다")
                continue
            n_bar += 1
            t, c = x[a], x["cap"]
            sh = (t / c * 100) if c else 0.0
            if w == 1:
                want = (_w_A(t), "—")
            else:
                want = (_w_P(sh), _w_A(t) + "억")
            if (b["val"], b["sub"]) != want:
                bad_bar.append(f"{tag} {sec} 웹 {(b['val'], b['sub'])} vs {want}")
        # 종목 층 표지 — TUI `name_evidence`(창·주체)와 같은 글자, 드릴다운 헤더의 짧은 꼴과 같은 표지.
        n_ev += 1
        want_ev = {"amt": " " + FV.name_evidence(a, "amt", w), "share": " " + FV.name_evidence(a, "share", w)}
        iv, pf = FV.name_evidence("invtrt", "amt", w), FV.name_evidence("penfnd", "amt", w)
        want_ev["trust"] = " " + (iv if iv == pf else f"투신 {iv} · 연기금 {pf}")
        got_ev = {k: v["ev"][k] for k in ("amt", "share", "trust")}
        if got_ev != want_ev or v["ev"]["sell"] != [want_ev["amt"], want_ev["share"], want_ev["trust"]]:
            bad_ev.append(f"{tag} 웹 {got_ev} vs TUI {want_ev}")
        if str(w) in FV.WINDOWS:
            st = FV.State(D)
            st.wi, st.ai = FV.WINDOWS.index(str(w)), [k for k, _ in FV.ACTORS].index(a)
            heads = {c.header: FV.name_head(c.header, st) for c in FV.names_cols(True)}
            # 헤더는 표(`name_evidence`)에서 다시 꺼낸 표지와 맞춘다 — 위에서 뷰어 = 표를 봤으므로 TUI = 뷰어다.
            # (뷰어 글자로 맞추면 뷰어 결함 하나가 두 대조를 함께 빨갛게 만들어 어느 쪽이 틀렸는지 못 가른다.)
            for hdr, text in (("순매수[억]", want_ev["amt"]), ("시총대비[%p]", want_ev["share"]),
                              (FV.COL_INVTRT, iv), (FV.COL_PENFND, pf)):
                want_h = hdr + FV.B_SHORT[FV.name_badge(text.strip())]
                if heads.get(hdr) != want_h:
                    bad_ev.append(f"{tag} TUI 헤더 {heads.get(hdr)!r} vs 표 {text.strip()!r}")
        if "head" not in v:
            continue
        # 전체 표 — 열 머리·표지(섹터 `evidence_at`), 칸 = 페이로드, 금액 칸 = TUI 블록.
        want_head = ["섹터", "기관", "외국인", "개인", "기타법인", "기관 가속 %p", "거래대금", "시총"]
        if [h["h"] for h in v["head"]] != want_head:
            bad_c.append(f"{tag} 머리 {[h['h'] for h in v['head']]}")
            continue
        for h, (ek, act) in zip(v["head"][1:6], (("inst", "inst"), ("forgn", "forgn"), ("indiv", "indiv"),
                                                 ("etc", "etc"), ("accel", "inst"))):
            e = FV.evidence_at(ek, str(w), act, m) if str(w) in FV.WINDOWS else None
            want = None if e is None else e[0] + (f" · {e[1]}" if e[1] else "")
            if (h["ev"] or None) != want:
                bad_ev.append(f"{tag} 표 {h['h']} 웹 {h['ev']} vs TUI {want}")
        # 1일은 블록이 없다(오늘 블록과 일별 흐름의 일치는 T 층이 본다).
        blk = {r["sector"]: r for r in (D["blocks"].get(f"{w}|{m}") or {}).get("rows", [])}
        for row in v["rows"]:
            sec = row[0].rstrip("~")
            x = E["sec"].get(sec)
            if x is None:
                bad_c.append(f"{tag} {sec} 페이로드에 없다")
                continue
            c = x["cap"]
            want = [_w_A(x[k]) for k, _ in ACT] + ["—" if w == 1 else _w_P((x["inst"] / c * 100) if c else 0.0),
                                                   _w_A(x["tv"]).replace("+", ""), _w_A(c).replace("+", "")]
            n_c += len(want)
            if row[1:] != want:
                bad_c.append(f"{tag} {sec} 웹 {row[1:]} vs {want}")
            b = blk.get(sec)
            if b:
                for (k, ko), web in zip(ACT, row[1:5]):
                    if b.get(k) is not None and _w_A(b[k]) != web:
                        bad_tui.append(f"{tag} {sec} {ko} 뷰어 {web} vs 블록 {_w_A(b[k])}")
    chk("W", "뷰어 전체 표 = 페이로드(표시 반올림, 창×시장)", not bad_c and n_c > 0,
        f"{len(bad_c)}건/{n_c}칸  " + "; ".join(bad_c[:2]))
    chk("W", "뷰어 KPI 카드 = 페이로드(창×시장×주체)", not bad_tile, "; ".join(bad_tile[:2]))
    chk("W", "뷰어 랭킹 막대 값 = 페이로드(창×시장×주체)", not bad_bar and n_bar > 0,
        f"{n_bar}줄  " + "; ".join(bad_bar[:2]))
    chk("W", "뷰어 전체 표 금액 = 수치표·TUI 블록 글자", not bad_tui, "; ".join(bad_tui[:3]))
    chk("W", "종목 표지 TUI = 뷰어(창별·주체별) · 섹터 표 표지 = evidence_at", not bad_ev and n_ev > 0,
        f"{n_ev}화면  " + "; ".join(bad_ev[:2]))


# ─────────────────────────────────────────────── H. 화면·키
#
# A~G 는 **값**을 본다. 이 층은 값이 아니라 "화면과 키가 실제로 그렇게 동작하는가"
# 를 본다. 오늘 실제로 나온 결함 넷은 전부 이 부류였고 값 검산은 하나도 못 잡았다:
#
#   1. 새 화면(`t`)의 키를 푸터 **다섯 단계 중 하나에만** 적었다 — 넓은 터미널을
#      쓰는 사람에게 그 키가 안 보였다.
#   2. 자모 표에 `ㅅ` 이 없어 한영을 켜 둔 채로는 `t` 가 안 먹었다.
#   3. 그 화면에서 `w`·`m`·`a` 가 통째로 죽어 있었다 — "위 공통 분기가 처리한다"
#      는 주석이 거짓이었다.
#   4. 검사는 폭 80 으로 묻고 화면은 79 로 그렸다.
#
# 공통점은 **코드가 서로를 검사하지 않는 자리**다. 그래서 이 층은 어느 쪽도
# 손으로 적은 목록을 믿지 않는다 — 듣는 키는 `handle_key` 를 실제로 태워서 상태
# 변화로 판정하고, 적힌 키는 푸터·도움말 **문자열을 파싱해서** 꺼낸다. 둘을
# 맞춘다. 손으로 적은 표는 다음 화면이 생길 때 조용히 낡는다.

#: 화면에 적히는 키 표기 → 키 이름. 푸터·도움말이 쓰는 글자 그대로다.
_KEY_WORDS = {
    "↑↓": ("KEY_UP", "KEY_DOWN"), "↑": ("KEY_UP",), "↓": ("KEY_DOWN",),
    "←": ("KEY_LEFT",), "→": ("KEY_RIGHT",),
    "PgUp": ("KEY_PPAGE",), "PgDn": ("KEY_NPAGE",),
    "Enter": ("KEY_ENTER",), "Esc": ("ESC",), "Home": ("KEY_HOME",),
    "End": ("KEY_END",), "Space": ("SPACE",), "F1": ("F1",),
}

#: 한글 한 글자 범위 — 푸터 토큰에서 설명(`w:구간`·`v화면`)을 떼어낼 때 쓴다.
_HANGUL = "가-힣㄰-㆏"


def _advertised(text: str) -> set:
    """화면에 적힌 한 줄이 **광고하는 키** 이름 집합.

    ``w:구간``·``v화면``·``g/G:처음/끝``·``q·Esc·?·Enter:닫기`` 를 전부 같은
    규칙으로 읽는다 — 콜론이나 한글이 나오면 거기서 끊고, 남은 것을 ``/``·``·``
    로 쪼갠다. 화면 문구를 고치면 이 파서가 따라온다(손으로 적은 목록이 아니다).
    """
    out = set()
    for tok in text.split():
        head = re.split(r"[:%s]" % _HANGUL, tok, maxsplit=1)[0]
        for part in re.split(r"[/·]", head):
            part = part.strip()
            if part in _KEY_WORDS:
                out.update(_KEY_WORDS[part])
            elif len(part) == 1 and part.isascii() and (part.isalnum() or part == "?"):
                out.add(part)
    return out


def _key_ints():
    import curses
    d = {c: ord(c) for c in
         "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ?1"}
    d.update({"KEY_UP": curses.KEY_UP, "KEY_DOWN": curses.KEY_DOWN,
              "KEY_LEFT": curses.KEY_LEFT, "KEY_RIGHT": curses.KEY_RIGHT,
              "KEY_PPAGE": curses.KEY_PPAGE, "KEY_NPAGE": curses.KEY_NPAGE,
              "KEY_HOME": curses.KEY_HOME, "KEY_END": curses.KEY_END,
              "KEY_ENTER": curses.KEY_ENTER, "ESC": 27, "SPACE": ord(" "),
              "F1": curses.KEY_F1})
    return d


def _live(make, apply, fields, keys, unseen: "list | None" = None, label: str = "") -> set:
    """실제로 **듣는** 키 — `handle_key` 를 태워 상태가 달라지는가로 판정한다.

    소스를 파싱하지 않는다. "위 공통 분기가 처리한다" 같은 주석은 거짓일 수 있고,
    실제로 거짓이었다. 상태를 직접 보는 쪽만 그걸 못 속인다.

    출발 커서는 **목록 길이에 맞춘 앱이 닿을 수 있는 자리**다: 끝(End 로 간 자리)이 L 이면 0·``min(3, L-1)``·L.
    키는 그중 한 곳에서라도 상태를 바꾸면 듣는다. 예전엔 3 고정이라 4행 목록(09-18 재구성의 전 종목)에서 3 이 곧
    끝이어서 ↓·PgDn·G 를 "안 듣는다" 고 했고, 2행 목록엔 앱이 만들 수 없는 커서 3 을 박았다. 2행이면 ↑ 는 끝에서만,
    ↓ 는 처음에서만 움직이므로 출발점 하나로는 안 된다. 한 행뿐이면 이동 키는 볼 수 없다 — 통과로 세지 않고
    ``unseen`` 에 적는다.
    """
    def snap(o):
        return tuple(getattr(o, f, None) for f in fields)

    def at(row: int):
        o = make()
        for f in _CURSOR_FIELDS:
            if isinstance(getattr(o, f, None), int):
                setattr(o, f, row)
        return o

    probe = at(0)
    apply(probe, keys["KEY_END"])
    last = max((getattr(probe, f) for f in _CURSOR_FIELDS if isinstance(getattr(probe, f, None), int)),
               default=0)
    starts = sorted({0, max(0, min(3, last - 1)), last})
    out = set()
    for name, k in keys.items():
        for row in starts:
            o = at(row)
            before = snap(o)
            cont = apply(o, k)
            if not cont or snap(o) != before:
                out.add(name)
                break
    one_row = last == 0 and "KEY_DOWN" not in out
    if one_row:
        out |= _MOVE_KEYS & set(keys)
        if unseen is not None:
            unseen.append(label)
    return out


#: 화면 상태의 커서 필드(flow ``State``·ledger ``Model``). ``_live`` 가 출발 커서를 둘 때 쓴다.
_CURSOR_FIELDS = ("row", "drow", "arow", "hrow", "crow", "help_row")
#: 한 행뿐인 목록에서는 듣는지 볼 수 없는 이동 키.
_MOVE_KEYS = {"KEY_UP", "KEY_DOWN", "KEY_PPAGE", "KEY_NPAGE", "KEY_HOME", "KEY_END", "g", "G", "j", "k"}


def _help_key_text(entries) -> str:
    """도움말의 **키 절**(`── 키 ──` 부터 다음 구역 전까지) 전체 글자.

    라벨만 보면 안 된다 — `G`(끝)·`Home`·`PgUp` 은 설명 쪽에 적혀 있다.
    """
    out, on = [], False
    for name, desc in entries:
        if desc.strip().startswith("──"):
            if on:
                break
            on = "키" in desc
            continue
        if on:
            out.append(name + " " + desc)
    return " ".join(out)



#: 합성 페이로드 — 경계는 **실데이터에 기대면 CI 에서 조용히 무력해진다.**
#: 오늘의 리포트에 우연히 27개 섹터가 다 있고 관문 통과 종목이 있어서 초록인
#: 검사는, 어느 날 그게 아닌 리포트를 만나면 그때 처음 터진다. 경계는 여기서
#: 손으로 만든다.
#: **두벌식 자판 배열** — 앱의 자모 표가 맞는지 보는 기준이다. 앱의 표를 앱의
#: 표로 검사하면 한 줄이 빠져도 통과한다(실측: `ㅅ` 을 빼도 `ㅆ` 이 남아 초록).
#: 무시프트 자리와, Shift 로 **다른 글자**가 나오는 다섯 자리(ㅂㅈㄷㄱㅅ)다.
_DUBEOL = dict(zip("qwertyuiop", "ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔ"))
_DUBEOL.update(dict(zip("asdfghjkl", "ㅁㄴㅇㄹㅎㅗㅓㅏㅣ")))
_DUBEOL.update(dict(zip("zxcvbnm", "ㅋㅌㅊㅍㅠㅜㅡ")))
_DUBEOL.update(dict(zip("QWERTOP", "ㅃㅉㄸㄲㅆㅒㅖ")))

#: 호환 자모(U+31xx) → 첫가끝 자모(U+11xx). IME·터미널 조합에 따라 뒤엣것이 온다.
_COMPAT_TO_JAMO = dict(zip(
    "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ",
    [chr(0x1100 + i) for i in range(19)]))
_COMPAT_TO_JAMO.update(dict(zip(
    "ㅏㅐㅑㅒㅓㅔㅕㅖㅗ", [chr(0x1161 + i) for i in range(9)])))
_COMPAT_TO_JAMO.update({"ㅛ": "\u116d", "ㅜ": "\u116e", "ㅠ": "\u1172",
                        "ㅡ": "\u1173", "ㅣ": "\u1175"})


def _DUBEOL_JAMO(key: str):
    """ASCII 키 → 그 자리에서 나오는 자모들(호환·첫가끝). 없는 자리면 빈 목록."""
    c = _DUBEOL.get(key)
    if not c:
        return []
    return [c] + ([_COMPAT_TO_JAMO[c]] if c in _COMPAT_TO_JAMO else [])


def _synth_flow():
    """(이름, ``numbers.html`` 의 D 와 같은 모양의 dict) 목록."""
    def name(code, sector, pick_ok=True):
        return (code, {"name": f"종목{code}", "sector": sector, "market": "전체",
                       "cap": 1000.0,
                       "win": {"20": {"inst": 10.0 if pick_ok else -10.0,
                                      "forgn": -1.0, "indiv": -8.0, "etc": -1.0,
                                      "tv": 500.0, "invtrt": 5.0,
                                      "penfnd_etc": 5.0, "ret": 1.0}}})

    def row(sector, **kw):
        base = {"sector": sector, "inst": 100.0, "forgn": -50.0, "indiv": -40.0,
                "etc": -10.0, "tv": 5000.0, "cap": 200000.0, "cap_idx": 200000.0,
                "accel": 0.05, "ret": 1.5, "n_all": 42, "thin": False,
                "pct1y": {"inst": 80.0}, "spark": {"inst": [1.0, 2.0, 3.0]},
                "a_idx": 0.05, "exp": 1.0, "x": 0.5, "U": 0.1, "xdot": 0.1,
                "xddot": 0.2, "G": 0.7, "G_pass": True, "P": 0.1}
        base.update(kw)
        return base

    def doc(rows, names):
        return {"asof": "2026-01-31", "finalized": True,
                "dates": ["2026-01-%02d" % (i + 1) for i in range(31)],
                "n_by_sector": {}, "names": dict(names),
                "blocks": {"20|전체": {"rows": rows, "from": "2026-01-01",
                                       "to": "2026-01-31", "k": 12.0, "b": 0.1,
                                       "t": 3.0}}}

    out = [
        # 표가 **한 줄도 없는** 블록. 커서·상세 패널·전 종목이 전부 빈 목록 위를 돈다.
        ("빈 블록", doc([], {})),
        # 섹터 하나 · 종목 하나. 순위 분모가 N−1=0 이 되는 자리다.
        ("1섹터 1종목", doc([row("금속")], [name("000001", "금속")])),
        # 섹터 점수가 **전멸**(G 없음) — 전 종목 화면이 한 줄도 못 만든다.
        ("관문 전멸", doc([row("금속", G=None), row("제약", G=None)],
                          [name("000001", "금속"), name("000002", "제약")])),
        # 옛 리포트 — 새 열(투신·연기금·종목수익률)이 통째로 없다.
        ("결측 열", doc([{"sector": "제약", "inst": 1.0}],
                        [("000003", {"name": "옛종목", "sector": "제약",
                                     "market": "전체",
                                     "win": {"20": {"inst": 1.0}}})])),
        # 이름이 **아주 긴** 종목·섹터. `pad` 가 자르는 자리(설계다)에서 줄 폭이
        # 어긋나지 않는지 본다 — 한글은 두 칸이라 홀수 폭에서 한 칸이 남는다.
        ("긴 이름", doc([row("아주아주긴섹터이름입니다정말로")],
                        [("000004", {"name": "아주아주긴종목이름입니다정말로그렇습니다",
                                     "sector": "아주아주긴섹터이름입니다정말로",
                                     "market": "전체", "cap": 10.0,
                                     "win": {"20": {"inst": 1.0, "tv": 1.0,
                                                    "invtrt": 1.0,
                                                    "penfnd_etc": 1.0,
                                                    "ret": 1.0}}})])),
    ]
    return out


def layer_h(D: dict, P: dict) -> None:
    """H. **화면·키** — 듣는 키와 화면에 적힌 키가 같은가.

    두 방향을 다 본다. **적혔는데 안 듣는 키**가 더 나쁘다 — 없는 기능을 찾아
    누르게 만들기 때문이다. 반대로 **듣는데 안 적힌 키**는 발견 불가능한 기능이라
    없는 것과 같다(이 저장소가 `r`·`G` 에서 이미 밟았다).
    """
    print("\nH. 화면·키 — 듣는 키와 적힌 키가 같은가")
    import curses
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "src"))
    try:
        from sectorflow.tui import flow_app as FA
        from sectorflow.tui import flow_view as FV
        from sectorflow.tui import ledger_app as LA
        from sectorflow.tui import ledger_view as LV
    except ImportError as e:                      # pragma: no cover
        chk("H", "TUI 모듈을 읽을 수 있는가", False, str(e))
        return

    KEYS = _key_ints()
    FF = ("wi", "mi", "ai", "si", "nsi", "row", "drow", "arow", "hrow",
          "drill", "allv", "help", "rev", "nrev", "explore", "banner")
    LF = ("wi", "mi", "ai", "si", "vi", "row", "crow", "hrow", "help_row", "help",
          "detrend")

    def fst(**kw):
        def mk():
            st = FA.State(D)
            st.row = st.drow = st.arow = st.hrow = 3
            for k, v in kw.items():
                setattr(st, k, v)
            return st
        return mk

    def lmo(view, help_=False):
        def mk():
            mo = LV.Model(P)
            mo.vi = [v for v, _ in LV.VIEWS].index(view)
            mo.row = mo.crow = mo.hrow = mo.help_row = 3
            mo.help = help_
            return mo
        return mk

    fapply = lambda st, k: FA.handle_key(st, k)          # noqa: E731
    lapply = lambda mo, k: LA._key(mo, k, 10)            # noqa: E731
    comb = FV.WINDOWS.index("종합")

    #: (앱, 화면 이름, 상태 생성기, 키 적용, 상태 필드, 푸터 단계) — 곱집합의 축.
    SCREENS = [
        ("flow", "섹터 표", fst(), fapply, FF, FV.footer_tiers(False, False)),
        ("flow", "드릴다운", fst(drill=True), fapply, FF, FV.footer_tiers(True, False)),
        # 탐색 모드(`e`) — 종합·전 종목(곱)은 여기서만 닿는다.
        ("flow", "탐색 섹터 표", fst(explore=True), fapply, FF,
         FV.footer_tiers(False, False, explore=True)),
        ("flow", "탐색 드릴다운", fst(drill=True, explore=True), fapply, FF,
         FV.footer_tiers(True, False, explore=True)),
        ("flow", "종합", fst(wi=comb, explore=True), fapply, FF,
         FV.footer_tiers(False, False, combined=True, explore=True)),
        ("flow", "전 종목(t)", fst(allv=True, explore=True), fapply, FF,
         FV.footer_tiers(False, True, explore=True)),
        ("flow", "도움말", fst(help=True), fapply, FF, FV.HELP_FOOT_TIERS),
        ("ledger", "원장", lmo("ledger"), lapply, LF, LV.FOOTER_TIERS),
        ("ledger", "전개", lmo("timeline"), lapply, LF, LV.FOOTER_TIERS),
        ("ledger", "동시성", lmo("comove"), lapply, LF, LV.FOOTER_TIERS),
        ("ledger", "한계", lmo("limits"), lapply, LF, LV.FOOTER_TIERS),
        ("ledger", "도움말", lmo("ledger", True), lapply, LF, LV.HELP_FOOT_TIERS),
    ]
    live_unseen: list = []
    live = {(app, name): _live(mk, ap, f, KEYS, live_unseen, f"{app}/{name}")
            for app, name, mk, ap, f, _t in SCREENS}

    # --- 푸터 단계는 **단조**인가 ------------------------------------------
    # 오늘 밟은 자리다 — 새 화면의 `t` 를 다섯 단계 중 하나에만 적어서, 가장
    # 자세한 단계를 받는 **넓은 터미널**에서 그 키가 화면에서 사라졌다. 좁은
    # 단계가 키를 버리는 것은 설계지만, 넓은 단계가 좁은 단계보다 적게 적는 것은
    # 언제나 실수다.
    bad = []
    seen_tiers = set()
    for app, name, _mk, _ap, _f, tiers in SCREENS:
        if id(tiers) in seen_tiers:
            continue
        seen_tiers.add(id(tiers))
        for i in range(len(tiers) - 1):
            wide, narrow = _advertised(tiers[i]), _advertised(tiers[i + 1])
            if not (narrow <= wide):
                bad.append(f"{app}/{name} 단계{i}⊉단계{i+1}: "
                           f"{sorted(narrow - wide)}")
            if FV.cell_len(tiers[i]) < FV.cell_len(tiers[i + 1]):
                bad.append(f"{app}/{name} 단계{i} 가 단계{i+1} 보다 짧다")
    chk("H", "푸터 단계가 단조인가 (넓은 단계가 좁은 단계의 키를 다 담는가)",
        not bad, "; ".join(bad[:3]))

    # --- 적혔는데 안 듣는 키 ------------------------------------------------
    #
    # ⚠️ **설계된 비활성이 딱 한 부류 있다.** 정렬이 없는 화면(flow 의 종합,
    # 원장의 동시성·한계)에서 `s`·`r` 은 일부러 아무 일도 안 한다 — 예전엔
    # `si` 가 돌아서 헤더 라벨과 힌트바만 따라 움직이고 표는 그대로였다.
    # 그 예외는 **화면이 스스로 적어야** 성립한다(아래 검사가 그걸 본다).
    # 여기 없는 화면에서 안 듣는 키가 적혀 있으면 그건 그냥 거짓말이다 —
    # 전 종목 화면이 섹터 표 푸터를 그리며 `s`·`r`·`Enter` 를 광고하던 자리다.
    INERT = {("flow", "종합"): {"s", "r"},
             ("ledger", "동시성"): {"s"}, ("ledger", "한계"): {"s"}}
    bad = []
    for app, name, _mk, _ap, _f, tiers in SCREENS:
        got = live[(app, name)] | INERT.get((app, name), set())
        for i, t in enumerate(tiers):
            for k in sorted(_advertised(t) - got):
                bad.append(f"{app}/{name} 단계{i} 가 적은 {k!r} 이 안 듣는다")
    chk("H", "푸터·도움말푸터가 적은 키가 그 화면에서 실제로 듣는가",
        not bad, f"{len(bad)}건  " + "; ".join(bad[:4])
        + (f"  (한 행뿐이라 이동 키를 못 본 화면: {', '.join(live_unseen)})" if live_unseen else ""))

    # 그 예외는 화면이 적는가 — 헤더가 "정렬이 없다" 를 말해야 한다.
    bad = []
    st = fst(wi=comb, explore=True)()
    if "정렬없음" not in " ".join(FV.header_lines(st, 120)):
        bad.append("flow/종합 헤더가 정렬 없음을 안 적는다")
    if "정렬" not in FA.hint_text(st, 120):
        bad.append("flow/종합 힌트바가 정렬 없음을 안 적는다")
    # 전 종목 화면은 **곱 내림차순 고정**이다. 예전엔 섹터 표의 힌트를 그대로
    # 그려서 `정렬 가속[%p]▼(내림차순, r 로 뒤집기)` 라고 적혀 있었다 — 표는 곱
    # 순인데 힌트는 가속으로 줄세웠다 하고, `r` 은 여기서 안 듣는다. "정렬" 이라는
    # 글자가 있는지만 보면 그 거짓말이 그대로 통과하므로 **무엇을 말하는지**를 본다.
    for w2 in (40, 80, 120, 200):
        t2 = FA.hint_text(fst(allv=True, explore=True)(), w2)
        if "고정" not in t2:
            bad.append(f"flow/전 종목 힌트({w2})가 순서 고정을 안 적는다: {t2.strip()!r}")
        if "뒤집기" in t2 or "▼" in t2:
            bad.append(f"flow/전 종목 힌트({w2})가 없는 정렬을 광고한다: {t2.strip()!r}")
    for v in ("comove", "limits"):
        mo = lmo(v)()
        if "순서[" not in " ".join(LV.header_lines(mo, 120)):
            bad.append(f"ledger/{v} 헤더가 정렬 없음을 안 적는다")
    chk("H", "정렬이 죽은 화면은 화면 스스로 그 사실을 적는가", not bad,
        "; ".join(bad))

    # --- 듣는데 안 적힌 키 --------------------------------------------------
    # 화면 어디에도 안 적힌 기능은 없는 것과 같다. 푸터는 폭 때문에 줄어드니
    # **도움말 키 절**을 기준으로 본다 — 거기가 전부를 적는 자리다.
    bad = []
    for app, entries in (("flow", FV.HELP), ("ledger", LV.LEDGER_HELP)):
        doc = _advertised(_help_key_text(entries))
        for a, name in [(a, n) for a, n, *_ in SCREENS if a == app]:
            for k in sorted(live[(a, name)] - doc):
                bad.append(f"{app}/{name} 의 {k!r} 이 도움말 키 절에 없다")
    chk("H", "듣는 키가 도움말 키 절에 전부 적혀 있는가",
        not bad, f"{len(bad)}건  " + "; ".join(bad[:4]))

    # --- 자모로도 닿는가 ----------------------------------------------------
    # 한영을 켜 둔 채로는 `t` 가 안 먹어 그 화면이 **존재하지 않는 것과 같았다**.
    # 표에 한 줄 빠뜨린 것을 사람이 다시 눈으로 세지 않게 한다.
    #: 두벌식에서 Shift 로 **다른 글자**가 나오는 자음 — ㅂㅈㄷㄱㅅ 자리뿐이다.
    #: 나머지 대문자는 터미널에 도착한 뒤에는 구분할 정보가 이미 없다.
    SHIFT_DISTINCT = set("qwert")
    bad, detail, pairs = 0, [], 0
    for app, name, mk, ap, f, _t in SCREENS:
        jam = LA.LEDGER_JAMO if app == "ledger" else FA.JAMO_TO_ASCII
        norm = LA.LEDGER_JAMO_KEY if app == "ledger" else FA.normalize_key
        for k in sorted(live[(app, name)]):
            if not (len(k) == 1 and k.isalpha()):
                continue
            if k.isupper() and k.lower() not in SHIFT_DISTINCT:
                # 구분 불가능한 대문자(A·S·G·M·V…). 한글에서는 소문자로 떨어지는
                # 것이 **맞는 동작**이다 — 소문자 쪽 검사가 그걸 이미 본다.
                continue
            want_j = [j for j in _DUBEOL_JAMO(k) if j]
            if not want_j:
                bad += 1
                detail.append(f"{app}/{name} {k!r} 은 두벌식 자판에 없는 자리다")
                continue
            # ⚠️ **자판이 기준이다.** 앱의 표를 앱의 표로 검사하면 "ㅅ 한 줄이
            # 빠졌는데 ㅆ 이 남아 있어" 통과한다 — 실제로 그렇게 통과했다
            # (한영을 켜면 `t` 가 안 먹던 그 결함이 검사를 그냥 지나갔다).
            # 두벌식 자판 배열을 여기 적고, 앱의 표가 그걸 덮는지 본다.
            for j in want_j:
                pairs += 1
                if j not in jam:
                    bad += 1
                    detail.append(f"{app}/{name} {k!r} 자리의 {j!r} 이 자모 표에 없다")
                    continue
                o1, o2 = mk(), mk()
                c1 = ap(o1, ord(k))
                c2 = ap(o2, norm(j))
                s1 = (c1,) + tuple(getattr(o1, x, None) for x in f)
                s2 = (c2,) + tuple(getattr(o2, x, None) for x in f)
                if s1 != s2:
                    bad += 1
                    detail.append(f"{app}/{name} {j!r}({k!r}) 가 다른 일을 한다")
    chk("H", "듣는 키마다 두벌식 자모(호환 U+31xx·첫가끝 U+11xx)가 같은 일을 하는가",
        not bad, f"{pairs}쌍 · {bad}건  " + "; ".join(detail[:3]))

    # 구분 불가능한 대문자는 **소문자 동작으로 떨어지는가.** 한글 상태에서
    # `A` 를 눌러도 `ㅁ` 이 오므로 역방향이 원리적으로 불가능한데, 그때 아무 일도
    # 안 하면 "키가 고장난 것" 으로 읽힌다. 도움말이 그 한계를 적는지도 본다.
    bad = []
    for app, entries in (("flow", FV.HELP), ("ledger", LV.LEDGER_HELP)):
        jam = LA.LEDGER_JAMO if app == "ledger" else FA.JAMO_TO_ASCII
        norm = LA.LEDGER_JAMO_KEY if app == "ledger" else FA.normalize_key
        for j, asc in jam.items():
            if norm(j) != ord(asc):
                bad.append(f"{app} {j!r} → {asc!r} 정규화 실패")
        txt = _help_key_text(entries)
        if "한영" not in txt:
            bad.append(f"{app} 도움말이 한글 입력 상태를 안 적는다")
    chk("H", "자모가 두벌식 같은 자리의 ASCII 로 정규화되고 그 한계가 적혀 있는가",
        not bad, "; ".join(bad[:3]))

    # --- 화면마다 같은 키가 같은 뜻인가 -------------------------------------
    bad = []
    for app, name, mk, ap, f, _t in SCREENS:
        doc = "도움말" in name
        # q — 도움말에서는 **닫기**, 나머지에서는 종료. 그 예외는 화면이 적는다.
        o = mk()
        quit_ = not ap(o, ord("q"))
        if quit_ == doc:
            bad.append(f"{app}/{name} q")
        if doc and o.help:
            bad.append(f"{app}/{name} q 가 도움말을 안 닫는다")
        # Esc — 어느 화면에서도 종료가 아니다(느린 SSH 에서 방향키가 쪼개진다).
        if not ap(mk(), 27):
            bad.append(f"{app}/{name} Esc 가 종료한다")
        # ? — 도움말이 아니면 연다, 도움말이면 닫는다.
        o = mk()
        ap(o, ord("?"))
        if o.help == doc:
            bad.append(f"{app}/{name} ? 가 도움말을 토글 안 한다")
    chk("H", "q·Esc·? 가 모든 화면에서 같은 뜻인가", not bad, "; ".join(bad[:4]))

    # g/G — 커서가 있는 화면이면 처음/끝이다.
    #
    # ⚠️ **"끝" 은 그 화면 목록의 마지막 행이다 — 고정된 수가 아니다.** 예전엔
    # 커서를 3 에 두고 G 뒤에 `> 3` 인지를 봤다. 그건 목록이 5행 이상이라는
    # 가정이고, 관문(a·x·ẍ 전부 양수)을 통과한 섹터가 적은 날에는 전 종목(t)이
    # 0~2행, 종합은 0행(G 가 나오는 창이 2개 미만)이라 **커서가 정확히 끝에
    # 가 있는데도** "끝으로 안 간다" 고 했다(2026-09-07: 2행에서 arow=1 = 끝).
    # 2026-09~10 리포트 27일 중 그 이유로만 빨간 날이 십여 일이다. 그래서 기대값을
    # 화면의 목록 크기에서 꺼내고, 목록이 빈 날은 **목록이 있는 구간·시장을
    # 찾아** 거기서 잰다 — 빈 목록에서 "0 이면 맞다" 로 통과하면 아무것도 안 본다.
    flow_cur = {"섹터 표": ("row", lambda st: len(st.rows())),
                "종합": ("row", lambda st: len(st.rows())),
                "드릴다운": ("drow", lambda st: len(st.names())),
                "탐색 섹터 표": ("row", lambda st: len(st.rows())),
                "탐색 드릴다운": ("drow", lambda st: len(st.names())),
                "전 종목(t)": ("arow", lambda st: len(st.all_picks())),
                # 도움말의 끝은 마지막 줄이 아니라 **마지막 한 화면의 첫 줄**이다
                # (`handle_key` 의 help_page 기본 10).
                "도움말": ("hrow", lambda st: max(
                    FV.help_lines(80, 0, 10**6, FV.help_entries(st))[1] - 10, 0) + 1)}

    def populated(name, mk):
        """(상태 생성기, 목록 크기) — 목록이 빈 날은 **찬 구간·시장**을 찾는다.

        커서는 `min(3, 끝)` 에 둔다. 3 은 "0 이 아닌 중간" 이라는 뜻이지 행 수에
        대한 가정이 아니다 — 2행짜리 목록에 커서 3 은 앱이 만들 수 없는 상태다.
        """
        cur, size = flow_cur[name]
        base = mk()
        cands = [(base.wi, base.mi)]
        if name != "도움말":
            wis = [base.wi] if name == "종합" else [
                i for i, w in enumerate(FV.WINDOWS) if w != "종합"]
            cands += [(wi, mi) for wi in wis for mi in range(len(base.markets))]
        for wi, mi in cands:
            o = mk()
            o.wi, o.mi = wi, mi
            n = size(o)
            if n > 0:
                break
        else:
            wi, mi, n = base.wi, base.mi, 0

        def make():
            o = mk()
            o.wi, o.mi = wi, mi
            setattr(o, cur, min(3, max(n - 1, 0)))
            return o
        return make, n

    def ledger_last(mk, cur, name):
        """원장 화면의 끝 — **그리는 쪽이 정한다.** 커서를 한참 넘겨 두고 한 번
        그리면 `screen()` 이 끝으로 잘라 되돌려 적는다(행·스크롤 화면 공통).
        도움말은 모달이라 `screen()` 이 안 자른다 — 키 처리의 하한(`_key` 의
        page=10)을 그대로 쓴다."""
        if name == "도움말":
            return max(LV.help_total() - 10, 0)
        e = mk()
        setattr(e, cur, 10**6)
        LV.screen(e, 100, 30)
        return getattr(e, cur)

    bad, unseen = [], []
    for app, name, mk, ap, f, _t in SCREENS:
        if app == "flow":
            cur, size = flow_cur[name]
            mk, _n0 = populated(name, mk)
            last = max(size(mk()), 1) - 1
        else:
            # 원장 쪽은 **모델에게 묻는다**(`Model.scroll_attr`). 여기 표를 따로
            # 들면 화면이 제 자리를 옮겼을 때 검사만 옛 자리를 보고 "키가 안
            # 듣는다"고 한다 — 동시성이 `row` 를 떠나 `crow` 로 갈 때 실제로
            # 그랬다. 도움말은 화면이 아니라 모달이라 표에 남는다.
            cur = "help_row" if name == "도움말" else mk().scroll_attr
            last = ledger_last(mk, cur, name)
        if last == 0:
            # 처음과 끝이 같은 자리면 g·G 가 듣는지 **볼 수 없다.** 통과로
            # 세지 않고 못 봤다고 적는다.
            unseen.append(f"{app}/{name}")
        for k, want_zero in ((ord("g"), True), (curses.KEY_HOME, True),
                             (ord("G"), False), (curses.KEY_END, False)):
            o = mk()
            # 출발점은 **반대쪽**이다 — 처음으로 가는 키는 0 이 아닌 곳에서,
            # 끝으로 가는 키는 0 에서. 출발점이 이미 목적지면 키가 죽어 있어도
            # 통과한다(짧은 목록에서 `min(3, 끝)` 이 곧 끝이다).
            setattr(o, cur, min(3, last) if want_zero else 0)
            ap(o, k)
            if app == "ledger":
                # 원장은 행 수를 **그리는 쪽만** 안다 — 루프가 그러듯 한 번 그려
                # 잘라 되돌려 적게 한 뒤에 본다(끝을 지나 쌓이던 자리다).
                LV.screen(o, 100, 30)
            v = getattr(o, cur)
            kn = chr(k) if k < 256 else ("Home" if want_zero else "End")
            if want_zero and v != 0:
                bad.append(f"{app}/{name} {kn} → {cur}={v}")
            elif not want_zero and v != last:
                bad.append(f"{app}/{name} {kn} 가 끝으로 안 간다 "
                           f"({cur}={v}, 끝={last})")
    chk("H", "g·Home 은 처음 · G·End 는 끝 — 커서가 있는 모든 화면에서",
        not bad, f"{len(bad)}건  " + "; ".join(bad[:4])
        + (f"  (처음=끝이라 못 본 화면: {', '.join(unseen)})" if unseen else ""))

    # t — 전 종목 화면을 여는 키. **탐색 모드에서만** 듣고, 거기서는 어디서나 같다.
    # 기본 모드에서 t 가 곱 화면을 열면 숨긴 탐색 점수가 키 하나로 새어 나온다.
    bad = []
    for name in ("탐색 섹터 표", "종합", "탐색 드릴다운"):
        o = [s for s in SCREENS if s[1] == name and s[0] == "flow"][0][2]()
        FA.handle_key(o, ord("t"))
        if not o.allv or o.drill:
            bad.append(f"flow/{name} 에서 t 가 전 종목 화면을 안 연다")
    for name in ("섹터 표", "드릴다운"):
        o = [s for s in SCREENS if s[1] == name and s[0] == "flow"][0][2]()
        FA.handle_key(o, ord("t"))
        if o.allv:
            bad.append(f"flow/{name}(기본 모드) 에서 t 가 탐색 화면을 열었다")
    o = fst(allv=True, explore=True)()
    FA.handle_key(o, ord("t"))
    if o.allv:
        bad.append("flow/전 종목 에서 t 가 안 닫는다")
    chk("H", "t 가 탐색 모드의 어느 화면에서나 전 종목 화면을 여닫고 기본 모드에서는 안 듣는가", not bad, "; ".join(bad))

    # w·m·a — 상태를 바꾸는 키는 도움말 말고는 어디서나 듣는다.
    bad = []
    for app, name, mk, ap, f, _t in SCREENS:
        if "도움말" in name:
            continue
        for k in "wma":
            if k not in live[(app, name)]:
                bad.append(f"{app}/{name} 에서 {k!r} 이 죽어 있다")
    chk("H", "w·m·a 가 도움말 밖 모든 화면에서 듣는가", not bad, "; ".join(bad[:4]))

    # --- 커서가 상태 변경에서 살아남는가 -----------------------------------
    # 시작 상태는 **앱이 만들 수 있는 상태**여야 한다. 예전엔 목록 크기와 무관하게
    # 커서를 3 에 두고 시작해서, 전 종목(t)이 0행인 날 정렬이 없는 그 화면에서
    # `s`·`r` 이 (설계대로) 아무 일도 안 하자 "arow=3 (행 0)" 이라고 했다 — 범위를
    # 벗어난 것은 키가 아니라 검사가 만든 출발점이었다. 출발점은 `populated` 가
    # 정한다(찬 목록 + 커서 min(3, 끝)). 목록이 줄어드는 키(`w`·`m`·`a`)가
    # 커서를 따라 줄이는지는 그대로 본다.
    bad = []
    for k in "wWmMaAsSr":
        for name, cur, size in (("섹터 표", "row", lambda st: len(st.rows())),
                                ("드릴다운", "drow", lambda st: len(st.names())),
                                ("전 종목(t)", "arow", lambda st: len(st.all_picks()))):
            st = populated(name, [s for s in SCREENS if s[1] == name][0][2])[0]()
            FA.handle_key(st, ord(k))
            n = size(st)
            v = getattr(st, cur)
            if not (0 <= v < max(n, 1)):
                bad.append(f"{name}/{k} → {cur}={v} (행 {n})")
    chk("H", "구간·시장·주체·정렬을 바꿔도 커서가 범위 안에 있는가",
        not bad, "; ".join(bad[:4]))

    # --- 폭·높이 전수 ------------------------------------------------------
    #
    # "검사는 폭 80 으로 묻고 화면은 79 로 그렸다" 가 이 저장소의 실제 사고다.
    # 그래서 폭은 **한 칸씩** 훑는다 — 열이 떨어지는 경계는 늘 한 칸 사이에 있다.
    # 그리고 `KQ_AMBIGUOUS_WIDE` 를 **켠 채로 한 번 더** 돈다: `…`·`·`·`▼` 는
    # East Asian Width 가 `A` 라 그 환경에서 폭이 두 배가 되고, 실제로 힌트바가
    # 딱 한 칸씩 넘쳤다(폭 24~200 중 54개 폭). 환경 하나를 안 돌면 그 부류는
    # 영원히 안 보인다.
    bad, seen = [], 0
    ambi0 = FV.AMBIGUOUS_WIDE
    try:
        for ambi in (False, True):
            FV.AMBIGUOUS_WIDE = ambi
            tag = "AMBIGUOUS_WIDE=1 " if ambi else ""
            for term in range(40, 201):
                w = FV.view_width(term)
                for name, mk in (("섹터 표", fst()), ("종합", fst(wi=comb, explore=True)),
                                 ("드릴다운", fst(drill=True)),
                                 ("전 종목(t)", fst(allv=True, explore=True)),
                                 ("도움말", fst(help=True))):
                    st = mk()
                    seen += 1
                    if name == "도움말":
                        lines = FV.help_lines(w, 0, 40)[0]
                    elif name == "전 종목(t)":
                        lines = FV.all_lines(st, w)[0]
                    elif name == "드릴다운":
                        lines = FV.names_lines(st, w)[0]
                    else:
                        lines = FV.table_lines(st, w, 40)[0]
                    lines = list(lines) + FV.header_lines(st, w)
                    for ln in lines:
                        if FV.cell_len(ln) != w:
                            bad.append(f"{tag}{term}/{name} 본문 "
                                       f"{FV.cell_len(ln)} != {w}")
                            break
                    # 힌트바·푸터는 `pad` 가 자르기 **전에** 폭 안이어야 한다 —
                    # 잘린 안내문은 "여기가 전부" 로 읽힌다. 마지막 단계만은
                    # 예외다(그보다 짧게 쓸 문장이 없다는 뜻이라 잘림을 감수한다).
                    for what, s2, tiers in (
                            ("힌트", FA.hint_text(st, w), None),
                            ("푸터", FV.footer_line(w, st.drill, st.allv, not st.sortable,
                                                    st.explore),
                             FV.footer_tiers(st.drill, st.allv, not st.sortable,
                                             st.explore))):
                        if FV.cell_len(s2) > w and (tiers is None
                                                    or s2 != tiers[-1]):
                            bad.append(f"{tag}{term}/{name} {what} "
                                       f"{FV.cell_len(s2)} > {w}: {s2[:24]!r}")
    finally:
        FV.AMBIGUOUS_WIDE = ambi0
    chk("H", "폭 40~200 × 5화면 × (기본·KQ_AMBIGUOUS_WIDE) 에서 줄이 폭을 안 넘는가",
        not bad, f"{seen}조합 · {len(bad)}건  " + "; ".join(bad[:3]))

    # 높이 — 앱이 죽지 않는 하한부터. `layout` 이 음수 행이나 화면 밖 y 를 내면
    # curses 가 그 자리에서 터진다(SSH 창을 줄이다 죽는 부류다).
    bad = []
    for h in range(1, 61):
        for drill in (False, True):
            L = FA.layout(h, drill)
            if not (0 <= L.foot_y < max(h, 1)) or L.rows < 0 or L.detail < 0:
                bad.append(f"h={h} drill={drill} {L}")
            if L.hint_y >= 0 and not (L.hint_y < h and L.hint_y < L.foot_y):
                bad.append(f"h={h} drill={drill} hint_y={L.hint_y}")
    for hh in range(1, 61):
        for vi in range(len(LV.VIEWS)):
            mo = LV.Model(P)
            mo.vi = vi
            s2 = LV.screen(mo, 80, hh)
            if len(s2["lines"]) != max(4, hh):
                bad.append(f"ledger h={hh}/{LV.VIEWS[vi][0]} 줄수 {len(s2['lines'])}")
    chk("H", "높이 1~60 에서 배치가 화면 밖을 안 가리키는가", not bad,
        "; ".join(bad[:3]))

    # --- 도움말 모달의 마지막 줄 -------------------------------------------
    # 키 안내는 잘려도 "여기가 전부" 로 읽힐 뿐이지만, **위치 표시가 잘리면
    # 다른 숫자가 된다**(전체 200줄이 20줄로 읽힌다). 그래서 위치가 먼저 자리를
    # 잡는지를 폭마다 본다.
    bad = []
    for w2 in range(20, 201):
        for total in (7, 52, 200, 1234):
            ln = FA.help_foot(0, 20, total, w2)
            if FV.cell_len(ln) != w2:
                bad.append(f"폭 {w2} 줄폭 {FV.cell_len(ln)}")
            elif f"/ {total}" not in ln and w2 >= len(f" 1-20 / {total}") + 2:
                bad.append(f"폭 {w2}/전체 {total}: 위치가 잘렸다 {ln.strip()[-14:]!r}")
    chk("H", "도움말 마지막 줄의 위치 표시가 어느 폭에서도 안 잘리는가", not bad,
        f"{len(bad)}건  " + "; ".join(bad[:3]))

    # --- 한계 화면의 스크롤이 끝을 지나 쌓이지 않는가 -----------------------
    # 커서가 없는 화면은 그리는 쪽이 잘라 쓰기만 하고 상태를 안 고쳐서, PgDn 을
    # 끝에서 더 누르면 그만큼 쌓였다. 그러면 ↑ 가 **죽은 것처럼** 보인다.
    bad, flat = [], []
    for view in ("limits", "ledger", "timeline", "comove"):
        mo = lmo(view)()
        attr = mo.scroll_attr
        seen2 = []
        for i in range(60):
            LA._key(mo, curses.KEY_NPAGE, 24)
            LV.screen(mo, 100, 30)      # 루프가 그러듯 매번 그린다
            if i in (29, 59):
                seen2.append(getattr(mo, attr))
        if seen2[0] != seen2[1]:
            bad.append(f"{view}: PgDn 30번 {attr}={seen2[0]} · 60번 {seen2[1]} "
                       f"— 끝을 지나 쌓인다")
        # 쌓였으면 ↑ 를 그만큼 눌러야 돌아온다. 한 번에 움직여야 한다.
        # 끝이 0 이면(목록이 한 화면에 다 들어간다 — 섹터가 적은 날·합성 DB) ↑ 는 0 에 머무는 게
        # 맞다. 예전엔 −1 을 요구해 "0→0" 을 실패라 했다. 그 화면은 이 검사로 **못 본** 것이다.
        v0 = getattr(mo, attr)
        LA._key(mo, curses.KEY_UP, 24)
        LV.screen(mo, 100, 30)
        if v0 == 0:
            flat.append(view)
        if getattr(mo, attr) != max(v0 - 1, 0):
            bad.append(f"{view}: 끝에서 ↑ 한 번에 {attr} 가 {v0}→"
                       f"{getattr(mo, attr)}")
    chk("H", "끝까지 내린 뒤 스크롤이 쌓이지 않고 ↑ 한 번에 되돌아오는가",
        not bad, "; ".join(bad) + (f"  (한 화면에 다 들어가 못 본 화면: {', '.join(flat)})"
                                   if flat else ""))

    # --- 전 종목 화면(t): 그려진 글자 = 열 정의가 내는 값 -------------------
    #
    # 이 화면에는 여태 **아무 검사도 없었다.** E 층이 섹터 표와 종목 목록에 하는
    # 것을 그대로 한다 — 종목명·섹터명은 `pad` 가 자르는 게 설계지만 숫자 열은
    # 하나도 잘리면 안 된다(`-1,360` 이 `-1` 로 보이는 것이 표적이다).
    TEXT = {"종목", "섹터"}

    def cells(line, widths, i):
        a, wd = FV.span_at(widths, i)
        cur, buf = 0, []
        for ch in line:
            if a <= cur < a + wd:
                buf.append(ch)
            cur += FV.cell_width(ch)
        return "".join(buf).strip()

    bad_v, bad_w, seen = [], [], 0
    for term in (80, 120, 200):
        w = FV.view_width(term)
        for wi in range(len(FV.WINDOWS)):
            for ai in range(4):
                st = FA.State(D)
                st.wi, st.ai, st.allv = wi, ai, True
                seen += 1
                lines, nh = FV.all_lines(st, w)
                for ln in lines:
                    if FV.cell_len(ln) != w:
                        bad_w.append(f"{term}/{FV.WINDOWS[wi]} "
                                     f"{FV.cell_len(ln)} != {w}")
                        break
                cols = FV._fit(FV.all_cols(), w)
                widths = [c.width for c in cols]
                picks = st.all_picks()
                # ⚠️ **열 정의를 열 정의로 검산하지 않는다.** `c.fn` 이 낸 값과
                # 그려진 글자를 맞추면 정의가 틀렸을 때 양쪽이 같이 틀려서 통과한다
                # (곱을 소수 한 자리로 찍게 바꿔도 검사가 초록이었다). 세 열은
                # 여기서 **따로 셈해** 기대값을 만든다 — G 층이 이미 같은 규율이다.
                mine = {"곱": lambda t: f"{t['gsec'] * t['pick']:.2f}",
                        "섹터G": lambda t: f"{t['gsec']:.2f}",
                        "종목선정": lambda t: f"{t['pick']:.2f}"}
                for ln, t in zip(lines[nh:], picks):
                    for i, c in enumerate(cols):
                        if c.header in TEXT:
                            continue
                        want = (mine[c.header](t) if c.header in mine
                                else c.fn(t, st).strip())
                        if cells(ln, widths, i) != want:
                            bad_v.append(f"{term}/{FV.WINDOWS[wi]} {c.header!r} "
                                         f"{cells(ln, widths, i)!r} != {want!r}")
                order = [t["both"] for t in picks]
                if any(b > a + 1e-12 for a, b in zip(order, order[1:])):
                    bad_v.append(f"{term}/{FV.WINDOWS[wi]} 곱 내림차순이 아니다")
    chk("H", "전 종목 화면: 모든 줄의 표시 폭 = view_width", not bad_w,
        f"{len(bad_w)}건  " + "; ".join(bad_w[:2]))
    chk("H", "전 종목 화면: 그려진 글자 = 열 정의가 내는 값 · 곱 = 두 층의 곱 · 내림차순",
        not bad_v, f"{len(bad_v)}건  " + "; ".join(bad_v[:2]))
    chk("H", "전 종목 화면에서 검사한 (폭,구간,주체) 조합", seen >= 60, f"{seen}개")

    # 제목이 **무엇과 무엇을 곱했는지**를 어느 폭에서도 말하는가. 종합에서는 두
    # 층이 다른 구간을 본다 — 잘려서 `20일 기준` 만 남으면 섹터 쪽도 20일인 것처럼
    # 읽힌다(그 문구를 단계로 만든 이유가 그거다).
    bad = []
    for term in range(40, 201):
        w = FV.view_width(term)
        st = FA.State(D)
        st.allv, st.wi = True, comb
        title = FV.all_lines(st, w)[0][0]
        if "…" in title or ("종합" not in title and "20" not in title):
            bad.append(f"{term}: {title.strip()[:40]!r}")
    chk("H", "전 종목 제목이 어느 폭에서도 곱한 대상을 잘린 채로 말하지 않는가",
        not bad, f"{len(bad)}건  " + "; ".join(bad[:2]))

    # --- 합성 페이로드 — 경계는 실데이터에 기대면 CI 에서 조용히 무력해진다 --
    bad = []
    for label, synth in _synth_flow():
        for term in (40, 80, 200):
            w = FV.view_width(term)
            try:
                st = FA.State(synth)
                for name in ("섹터 표", "드릴다운", "전 종목(t)"):
                    st.drill = name == "드릴다운"
                    st.allv = name == "전 종목(t)"
                    if name == "전 종목(t)":
                        lines = FV.all_lines(st, w)[0]
                    elif name == "드릴다운":
                        lines = FV.names_lines(st, w)[0]
                    else:
                        lines = FV.table_lines(st, w, 20)[0]
                    lines = list(lines) + FV.header_lines(st, w) + [
                        FV.footer_line(w, st.drill, st.allv)]
                    for ln in lines[:-1]:
                        if FV.cell_len(ln) != w:
                            bad.append(f"{label}/{term}/{name} 폭 {FV.cell_len(ln)}")
                            break
                    for k in "wmastrgGjkl":
                        FA.handle_key(FA.State(synth), ord(k))
            except Exception as e:
                bad.append(f"{label}/{term} {type(e).__name__}: {e}")
    chk("H", "합성 페이로드(빈 블록·1섹터·관문 전멸·결측 열)에서 안 죽는가",
        not bad, f"{len(bad)}건  " + "; ".join(bad[:3]))


#: main 이 도는 층 — ``--layers`` 가 고를 수 있는 이름. 층을 더하면 여기에도 적는다(모르는 이름은 거부).
LAYERS = ("A", "A3", "B", "C", "D", "E", "G", "H", "T", "W", "F")


def run_layer(layer: str, fn, *args) -> None:
    """한 층이 터져도 나머지 층은 돈다 — 터진 것은 그 층의 **실패**로 적는다.

    생 트레이스백으로 끝나면 VERIFY.txt 에 실패 목록이 안 남고, 뒤 층은 검사도 못 한다.
    """
    try:
        fn(*args)
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        chk(layer, "검사가 끝까지 돌았는가", False, f"{type(e).__name__}: {e}")


def write_claims(d: str) -> None:
    """문서 주장 어긋남을 ``CLAIMS.txt`` 에 적는다. 없으면 옛 파일을 지운다(다시 만든 폴더에 남지 않게).

    VERIFY_FAILED 와 따로 두는 이유는 :func:`claim` 머리말. 이 파일이 있다는 것은 "숫자는 맞고
    도움말·한계 절의 실측 인용이 오늘 데이터와 어긋났다 — 글을 고쳐라" 이다.
    """
    path = os.path.join(d, "CLAIMS.txt")
    if not CLAIMS:
        if os.path.exists(path):
            os.remove(path)
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write("문서 주장 어긋남 — 수치 결함이 아니다. 도움말·한계 절의 실측 인용이 이 리포트의 데이터와 "
                "다르다.\n\n")
        for c in CLAIMS:
            f.write(f"- {c}\n")
    print(f"문서 주장 어긋남 {len(CLAIMS)}건 → {path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="리포트 폴더(numbers.html·payload.json)")
    ap.add_argument("--db-check", action="store_true")
    ap.add_argument("--db", default=os.environ.get("KR_QUANT_DB"))
    ap.add_argument("--layers", default="",
                    help="쉼표로 고른 층만 돈다(예: A,A3,F). 테스트가 빠르게 돌려고 쓴다 — 배치는 늘 전 층이다. "
                         "고르면 출력 첫 줄에 '부분 검증' 이라고 적는다")
    ap.add_argument("--asof", help="재구성 리포트의 기준일 — B 층이 DB 최신일 대신 이 날을 기준일로 본다")
    a = ap.parse_args()
    want = {x.strip() for x in a.layers.split(",") if x.strip()}
    unknown = want - set(LAYERS)
    if unknown:
        ap.error(f"모르는 층: {', '.join(sorted(unknown))} (있는 층: {', '.join(LAYERS)})")
    if want:
        # 부분 결과가 전 층 통과로 읽히지 않게 맨 앞에 적는다.
        print(f"부분 검증 — 층 {','.join(x for x in LAYERS if x in want)} 만 돈다(전 층이 아니다)")

    def go(layer: str, fn, *args) -> None:
        if not want or layer in want:
            run_layer(layer, fn, *args)

    d = os.path.expanduser(a.dir)
    global REPORT_DIR
    REPORT_DIR = d
    html = open(os.path.join(d, "numbers.html"), encoding="utf-8").read()
    D = json.loads(re.search(r"const D = (\{.*?\});\n", html, re.S).group(1))
    payload = json.load(open(os.path.join(d, "payload.json"), encoding="utf-8"))

    # 배치는 옆 폴더(`<기준일>.building.<pid>`)에 짓고 검증한 뒤 제자리로 옮긴다 — 임시 이름을
    # 적으면 VERIFY.txt 가 사라진 경로를 가리킨다. 옮겨 갈 이름을 적는다.
    print(f"검증 대상: {re.sub(r'[.]building[.][0-9]+/?$', '', d.rstrip('/'))}")
    print(f"  기준일 {payload['dates'][-1]} · 거래일 {len(payload['dates'])} · "
          f"섹터 {len(payload['sectors'])} · 블록 {len(D['blocks'])}")
    # DB 대조를 요구했는데 DB 가 없으면 **실패**다. 건너뛰고 "전부 통과" 를 내면 DB 를 본 것처럼 읽힌다.
    if a.db_check and not a.db:
        chk("F", "--db-check 인데 DB 접속정보가 있는가", False,
            "KR_QUANT_DB 나 --db 가 없다 — set -a; . ~/git/quant-airflow/.env; set +a")
    go("A", layer_a, D)
    go("A3", layer_a3, D, payload)
    go("B", layer_b, payload, a.db if a.db_check else None, a.asof)
    go("C", layer_c, D, html)
    go("D", layer_d, D, payload, html)
    go("E", layer_e, D)
    go("G", layer_g, payload, D)
    go("H", layer_h, D, payload)
    go("T", layer_t, payload, d)
    go("W", layer_w, D, payload, d)
    if a.db_check and a.db:
        go("F", layer_f, payload, a.db)

    print(f"\n검사 {CHECKS}건 · 실패 {len(FAILS)}건 · 문서 주장 어긋남 {len(CLAIMS)}건")
    if WARNS:
        print(f"경고 {len(WARNS)}건(실패 아님): " + ", ".join(WARNS))
    write_claims(d)
    if FAILS:
        print("실패 목록: " + ", ".join(FAILS))
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
