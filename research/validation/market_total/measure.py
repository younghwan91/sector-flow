"""시장 합계 순매수 금액 ↔ 네이버 시장값 오차 분해 — 리포트 코드를 import 하지 않는 독립 재계산.

결과 문서: docs/validation/2026-11-09-market-total-vs-published.md (내부 대조 전용 — 네이버 약관 미확인).
공표 수치는 ``out/`` (gitignore) 에만 둔다. 이 스크립트의 출력은 오차·상대값이다.

    research/validation/run.sh research/validation/market_total/measure.py [--start 2026-08-27] [--end 2026-11-06]

- 네이버 호출: 시장 시장값 날짜×2, 시가총액 목록 약 45쪽, DB 에 없는 종목마다 1회(약 140). 받은 것은 ``out/`` 에 캐시.
- 첫 게시본 비교는 ``reports/.archive/pre-rebuild-2026-11-08`` 가 있을 때만(섹터 칸 단위).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import connect_ro, out_dir, reports_dir  # noqa: E402

UA = "sector-flow-internal-verification"
MK = {"거래소": "KOSPI", "코스닥": "KOSDAQ"}
AC = ("indiv", "forgn", "inst")
QTY = {"indiv": "individual", "forgn": "foreign_", "inst": "institution"}
NK = {"indiv": "personalValue", "forgn": "foreignValue", "inst": "institutionalValue"}
#: 블록 후보 — 그날 거래량 중 분봉에 없는 몫 ≥ 이 값, 최대 주체 금액 ≥ 시장 Σ|3주체| 의 이 몫. 정의 문서 §2.
OFF_MIN, SHARE_MIN = 0.5, 0.01
REGIME = "2026-09-14"


def num(s) -> float:
    t = str(s).replace(",", "").replace("+", "").strip()
    return float(t) if t not in ("", "-", "None") else 0.0


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.load(r)


def cached(path: Path, make):
    if path.exists():
        return json.loads(path.read_text("utf-8"))
    v = make()
    path.write_text(json.dumps(v, ensure_ascii=False), "utf-8")
    return v


def fetch_market(dates: list[str]) -> dict:
    out = {}
    for d in dates:
        for m, nv in MK.items():
            out[f"{m}|{d}"] = get_json(f"https://m.stock.naver.com/api/index/{nv}/trend?bizdate={d.replace('-', '')}")
            time.sleep(0.2)
    return out


def fetch_absent(have: set[str], end: str) -> dict:
    """네이버 '주식' 목록 중 supply_demand 에 없는 코드(우선주·리츠 등)의 종목별 순매매 수량 60거래일."""
    names = {}
    for m, nv in MK.items():
        for page in range(1, 40):
            rows = get_json(f"https://m.stock.naver.com/api/stocks/marketValue/{nv}?page={page}&pageSize=100").get("stocks") or []
            if not rows:
                break
            for x in rows:
                if x.get("stockEndType") == "stock" and x["itemCode"] not in have:
                    names[x["itemCode"]] = {"market": m, "name": x.get("stockName")}
            time.sleep(0.2)
    # 이 API 의 bizdate 는 상한 **제외**다(bizdate=20261106 은 11-05 부터 준다) — 하루 뒤로 물어야 end 가 든다.
    biz = (date.fromisoformat(end) + timedelta(days=1)).strftime("%Y%m%d")
    for code, row in names.items():
        row["trend"] = get_json(f"https://m.stock.naver.com/api/stock/{code}/trend?pageSize=60&bizdate={biz}")
        time.sleep(0.2)
    return names


def load_db(start: str, end: str) -> pd.DataFrame:
    c = connect_ro()
    q = lambda s: pd.read_sql_query(s, c, params=[start, end])  # noqa: E731
    sd = q("SELECT code, date, abs(close)::float AS close, acc_trde_qty::float AS qty, "
           "coalesce(individual,0)::float AS individual, coalesce(foreign_,0)::float AS foreign_, "
           "coalesce(institution,0)::float AS institution FROM supply_demand "
           "WHERE source='kiwoom' AND date >= %s AND date <= %s")
    st = pd.read_sql_query("SELECT code, name, market FROM stocks", c)
    db = q("SELECT DISTINCT ON (code, date) code, date, volume::float AS volume, trade_value::float AS tv "
           "FROM daily_bars WHERE date >= %s AND date <= %s ORDER BY code, date, (source <> 'kiwoom')")
    mn = pd.read_sql_query(
        "SELECT code, ts::date AS date, sum(volume)::float AS vm FROM minute_bars "
        "WHERE ts >= %s AND ts < (%s::date + 1) GROUP BY 1, 2", c, params=[start, end])
    d = sd.merge(st, on="code").merge(db, on=["code", "date"], how="left").merge(mn, on=["code", "date"], how="left")
    d["date"] = d["date"].astype(str)
    d["vwap"] = (d["tv"] * 1e6 / d["volume"]).where((d["volume"] > 0) & (d["tv"] > 0))
    d["offr"] = ((d["volume"] - d["vm"]).clip(lower=0) / d["volume"]).where(d["volume"] > 0)
    for a in AC:
        d["x_" + a] = d[QTY[a]] * d["close"] / 1e8
    return d


def totals(d: pd.DataFrame, price: str = "close") -> pd.DataFrame:
    p = d["close"] if price == "close" else d[price].fillna(d["close"])
    return pd.DataFrame({a: (d[QTY[a]] * p / 1e8).groupby([d["market"], d["date"]]).sum() for a in AC}).rename_axis(["m", "d"])


def summary(t: pd.DataFrame, pub: pd.DataFrame, m: str, f=lambda _d: True) -> str:
    idx = [i for i in pub.index if i[0] == m and f(i[1])]
    e = (t.reindex(pub.index).fillna(0) - pub).abs().loc[idx]
    rel = e.sum(axis=1) / pub.abs().loc[idx].sum(axis=1) * 100
    return (f"Σ|오차|/Σ|공표| {e.sum().sum() / pub.abs().loc[idx].sum().sum() * 100:5.1f}%  일별 중앙 {rel.median():5.1f}%  "
            f"p90 {rel.quantile(.9):5.1f}%  최대 {rel.max():6.1f}%  5%초과 {int((rel > 5).sum())}/{len(idx)}일")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-08-27")
    ap.add_argument("--end", default="2026-11-06")
    a = ap.parse_args()
    out = out_dir(__file__)
    d = load_db(a.start, a.end)
    dates = sorted(d["date"].unique())
    print(f"기간 {dates[0]}~{dates[-1]} {len(dates)}일, 수급 종목 {d['code'].nunique()}")

    mk = cached(out / "naver_market.json", lambda: fetch_market(dates))
    pub = pd.DataFrame([{"m": m, "d": dd, **{x: num(mk[f"{m}|{dd}"][NK[x]]) for x in AC}}
                        for m in MK for dd in dates]).set_index(["m", "d"])[list(AC)]
    ab = cached(out / "naver_absent.json", lambda: fetch_absent(set(d["code"]), a.end))
    rows = []
    for code, v in ab.items():
        for r in v.get("trend") or []:
            b = r["bizdate"]
            px = abs(num(r["closePrice"]))
            rows.append({"code": code, "m": v["market"], "d": f"{b[:4]}-{b[4:6]}-{b[6:]}",
                         "indiv": num(r["individualPureBuyQuant"]) * px / 1e8,
                         "forgn": num(r["foreignerPureBuyQuant"]) * px / 1e8,
                         "inst": num(r["organPureBuyQuant"]) * px / 1e8})
    ax = pd.DataFrame(rows)
    pref = ~ax["code"].str.endswith("0")
    add = ax.groupby(["m", "d"])[list(AC)].sum().reindex(pub.index).fillna(0)
    add_pref = ax[pref].groupby(["m", "d"])[list(AC)].sum().reindex(pub.index).fillna(0)
    add_sec = ax[ax["code"] == "005935"].groupby(["m", "d"])[list(AC)].sum().reindex(pub.index).fillna(0)
    print(f"\n[유니버스] 네이버 '주식' 중 수급 표에 없는 코드 {len(ab)}개 — 우선주 꼴 "
          f"{sum(not c.endswith('0') for c in ab)}, 나머지(리츠·인프라 등) {sum(c.endswith('0') for c in ab)}")

    base = totals(d)
    den = base.abs().sum(axis=1)
    amt = d[["x_" + x for x in AC]].abs().max(axis=1)
    cand = d[(d["offr"] >= OFF_MIN) & (amt >= SHARE_MIN * pd.Series(list(zip(d["market"], d["date"])), index=d.index).map(den))]
    late = cand[cand["date"] >= REGIME]
    print(f"\n[블록 후보] off≥{OFF_MIN}·금액 몫≥{SHARE_MIN}: 전체 {len(cand)}건 ({REGIME}~ {len(late)}건, {late['date'].nunique()}일)")

    # 후보를 뺐을 때 그날 Σ|오차| 가 주는가 — 네이버 시장값에 그 체결이 들어 있지 않으면 준다.
    naive = base + add
    verdict = []
    for _, s in cand.iterrows():
        k = (s["market"], s["date"])
        xs = np.array([s["x_" + x] for x in AC])
        e = (naive.loc[k] - pub.loc[k]).to_numpy()
        verdict.append({"post": s["date"] >= REGIME, "excluded": np.abs(e - xs).sum() < np.abs(e).sum(), "amt": np.abs(xs).max()})
    v = pd.DataFrame(verdict)
    print("후보를 빼면 오차가 주는 건수(= 시장값에 없다로 읽음):")
    for lab, g in (("~09-11", v[~v["post"]]), ("09-14~", v[v["post"]])):
        print(f"  {lab}: {int(g['excluded'].sum())}/{len(g)}  |금액≥300억: {int(g[g['amt'] >= 300]['excluded'].sum())}/{int((g['amt'] >= 300).sum())}")

    # 같은 체결이 네이버 **종목** 값에는 있는가 — 시장값 정의 문제인지 수량 문제인지 가른다(수집분은 10-12 까지).
    c = connect_ro()
    nit = pd.read_sql_query("SELECT code, date, individual_net, institution_net FROM naver_investor_trend "
                            "WHERE date >= %s AND date <= %s", c, params=[a.start, a.end])
    nit["date"] = nit["date"].astype(str)
    m = late.merge(nit, on=["code", "date"])
    same = (m["individual"] == m["individual_net"].astype(float)) & (m["institution"] == m["institution_net"].astype(float))
    print(f"09-14~ 후보 중 naver_investor_trend 에 있는 {len(m)}건: 개인·기관 수량이 수급 표와 같은 건 {int(same.sum())}")

    no_blk = d.drop(late.index)
    print("\n[분해] 표기 = 시장별 Σ·중앙·p90·최대·5%초과")
    for m in MK:
        print(f"-- {m}")
        print("  현재(수량×종가)             ", summary(base, pub, m))
        print("  + 삼성전자우                ", summary(base + add_sec, pub, m))
        print("  + 우선주 꼴 전부             ", summary(base + add_pref, pub, m))
        print("  + 수급 표에 없는 코드 전부    ", summary(base + add, pub, m))
        print("  09-14~ 블록 후보 제외         ", summary(totals(no_blk), pub, m))
        print("  둘 다                        ", summary(totals(no_blk) + add, pub, m))
        print("  VWAP 환산만                  ", summary(totals(d, "vwap"), pub, m))
        print("  VWAP·블록 제외(유니버스 그대로)", summary(totals(no_blk, "vwap"), pub, m))
        print("  VWAP·블록 제외·유니버스 보정   ", summary(totals(no_blk, "vwap") + add, pub, m))

    print("\n[VWAP 사전 바] 블록 후보 제외, Σ 감소율·쌍대 승 (바: 두 시장 ≥20%, 구간 모두 감소, 승≥60%; 사전 기록의 후보 문턱은 ≥100억이었고 여기는 ≥1% — 판정 같음)")
    close_b, vwap_b = totals(no_blk), totals(no_blk, "vwap")
    bands = (("전체", lambda _d: True), ("~09-11", lambda x: x < REGIME), ("09-14~10-08", lambda x: REGIME <= x <= "2026-10-08"),
             ("10-12~11-06", lambda x: x >= "2026-10-12"))
    for corrected, lab2 in ((0, "유니버스 그대로"), (1, "유니버스 보정(참고)")):
        for m in MK:
            for lab, f in bands:
                idx = [i for i in pub.index if i[0] == m and f(i[1])]
                aa = (close_b + corrected * add - pub).abs().loc[idx].sum(axis=1)
                bb = (vwap_b + corrected * add - pub).abs().loc[idx].sum(axis=1)
                print(f"  {lab2} {m} {lab:12s} 감소 {(1 - bb.sum() / aa.sum()) * 100:5.1f}%  승 {int((bb < aa).sum())}/{len(idx)}")

    arc = reports_dir() / ".archive" / "pre-rebuild-2026-11-08"
    if arc.is_dir():
        print("\n[첫 게시 ↔ 확정 ↔ 시장값] 18시대 첫 게시본, 칸 단위 |오차|[억], 유니버스 보정 전후")
        bad = {"2026-08-27", "2026-08-28", "2026-10-13", "2026-11-06"}   # 18시대 첫 게시가 아니다(재생성본)
        recs = []
        for dd in dates:
            p = arc / dd / "payload.json"
            if dd in bad or not p.exists():
                continue
            P = json.loads(p.read_text("utf-8"))
            for mk_ in MK:
                for x in AC:
                    first = sum(P["flows"][mk_][s_][x][-1] for s_ in P["flows"][mk_])
                    o, f_, u = pub.loc[(mk_, dd), x], base.loc[(mk_, dd), x], add.loc[(mk_, dd), x]
                    recs.append({"m": mk_, "d": dd, "first": abs(first - o), "final": abs(f_ - o),
                                 "first_u": abs(first + u - o), "final_u": abs(f_ + u - o)})
        r = pd.DataFrame(recs)
        blk_days = set(zip(late["market"], late["date"]))
        r["blk"] = [(x, y) in blk_days for x, y in zip(r["m"], r["d"])]
        for mk_ in MK:
            for lab, f in (("~09-11", lambda x: x < REGIME), ("09-14~", lambda x: x >= REGIME)):
                g = r[(r["m"] == mk_) & r["d"].map(f)]
                print(f"  {mk_} {lab} n={len(g)} 첫 게시 중앙 {g['first'].median():.0f}·p90 {g['first'].quantile(.9):.0f}·최대 {g['first'].max():.0f}"
                      f" | 확정 중앙 {g['final'].median():.0f}·p90 {g['final'].quantile(.9):.0f}·최대 {g['final'].max():.0f}"
                      f" | 보정 후 첫 게시 가까움 {int((g['first_u'] < g['final_u'] - 1).sum())}·확정 가까움 {int((g['final_u'] < g['first_u'] - 1).sum())}")
        g = r[r["d"] >= REGIME]
        for lab, h in (("후보 있는 시장-일", g[g["blk"]]), ("후보 없는 시장-일", g[~g["blk"]])):
            print(f"  09-14~ {lab}: Σ첫 게시 {h['first_u'].sum():.0f} vs Σ확정 {h['final_u'].sum():.0f}(보정 후)"
                  f" · 첫 게시 가까움 {int((h['first_u'] < h['final_u'] - 1).sum())}·확정 가까움 {int((h['final_u'] < h['first_u'] - 1).sum())}")

    # 부호 반대, 09-14 전후
    sgn = ((base * pub) < 0)
    print(f"\n[부호 반대] 현재 {int(sgn.sum().sum())}칸/{sgn.size} · 유니버스·블록 보정 후 {int((((totals(no_blk) + add) * pub) < 0).sum().sum())}칸")
    print("[Σ3주체 잔차] 보정 후 시장별 |중앙|", ((totals(no_blk) + add).sum(axis=1) - pub.sum(axis=1)).abs().groupby("m").median().round(0).to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
