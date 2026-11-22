"""Phase 1 동등성 시험 도구 — kr_quant 조각 추출 · 표 행 대조 · 페이로드 대조.

**유니버스를 좁히는 방법.** 리포트 코드(``scripts/sector_flow.py``)에 종목 필터를 넣지 않는다. 대신 kr_quant 에서
같은 섹터의 행만 **읽어** 같은 ``storage.SCHEMA`` 의 sqlite 로 옮긴다(``slice``). 그러면 BYO sqlite 와 kr_quant
조각이 **같은 코드 경로**(sqlite)로 리포트를 만들고, 차이는 데이터에서만 나온다. 고른 섹터의 종목을 **전부**
담으므로 섹터 합계는 전체 유니버스 리포트의 그 섹터 값과 같아야 한다 — ``compare`` 에 전체 리포트를 세 번째로
주면 그 항등도 확인한다(조각 추출 자체의 검산).

    python -m sectorflow.collect.equiv slice  --sectors "운송/창고,오락/문화" --like byo.db --out krq.db
    python -m sectorflow.collect.equiv rowdiff byo.db krq.db
    python -m sectorflow.collect.equiv compare byo.json krq.json [--full full.json] [--out diff.json]

kr_quant 는 읽기만 한다(SELECT 만, 세션 ``statement_timeout``).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from sectorflow.storage import INVESTOR_COLUMNS, connect

from .write import open_db

INDEX_TOTALS = ("001", "101")
SD_COLS = ("code", "date", "close", "flu_rt", "acc_trde_qty", *INVESTOR_COLUMNS, "source")
FLOW_KEYS = ("indiv", "forgn", "inst", "etc", "tv")
DETAIL_KEYS = ("fnnc_invt", "insrnc", "invtrt", "bank", "penfnd_etc", "samo_fund")
WINDOWS = (1, 5, 20, 60, 120, 260)
#: 상대오차를 낼 분모의 하한[억]. 그보다 작은 합은 상대오차가 아니라 절대차로만 본다.
REL_FLOOR_EOK = 1.0


def _val(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat") and not isinstance(v, str):
        return v.isoformat()[:10]
    return v


# ── slice ──────────────────────────────────────────────────────────────────
def slice_kr_quant(dsn: str, out: str, sectors: list[str], start: str, end: str, *,
                   force: bool = False) -> dict:
    """kr_quant 에서 ``sectors`` 종목 전부와 그 업종지수를 ``[start, end]`` 로 잘라 sqlite 에 쓴다.

    주식수·기업행동은 날짜로 자르지 않는다 — as-of 조회가 구간 앞의 행을 쓰고(``market_cap_asof_bulk``),
    구간 뒤 행은 ``end`` 이하만 남긴다(리포트가 미래 행을 볼 일이 없게).
    """
    if os.path.exists(out) and not force:
        raise SystemExit(f"{out} 가 이미 있다 — 덮어쓰려면 --force")
    src = connect(dsn)
    if os.path.exists(out):
        os.remove(out)
    dst = open_db(out)
    cur = src.cursor()
    cur.execute("SELECT code, name, market, sector, kind FROM stocks WHERE sector = ANY(%s)", (sectors,))
    stocks = cur.fetchall()
    codes = sorted(r[0] for r in stocks)
    dst.executemany("INSERT INTO stocks (code, name, market, sector, kind) VALUES (?,?,?,?,?)", stocks)
    n: dict = {"stocks": len(stocks)}

    def copy(table: str, cols: tuple[str, ...], where: str, params: tuple, dst_cols=None) -> None:
        cur.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE {where}", params)  # 상수 표·열
        rows = [tuple(_val(v) for v in r) for r in cur.fetchall()]
        dc = dst_cols or cols
        dst.executemany(f"INSERT OR REPLACE INTO {table} ({', '.join(dc)}) VALUES "
                        f"({', '.join('?' * len(dc))})", rows)
        n[table] = len(rows)

    rng = "code = ANY(%s) AND date >= %s AND date <= %s"
    copy("supply_demand", SD_COLS, rng, (codes, start, end))
    copy("daily_bars", ("code", "date", "open", "high", "low", "close", "volume", "trade_value", "source"),
         rng, (codes, start, end))
    copy("daily_bars_adjusted", ("code", "date", "open", "high", "low", "close"), rng, (codes, start, end))
    copy("daily_bars_raw", ("code", "date", "close"), rng, (codes, start, end))
    copy("shares_outstanding_history", ("code", "date", "shares_outstanding"),
         "code = ANY(%s) AND date <= %s", (codes, end))
    copy("corporate_actions", ("code", "date", "upd_stkpc_tp", "upd_rt"),
         "code = ANY(%s) AND date <= %s", (codes, end))
    cur.execute("SELECT DISTINCT code FROM sector_index WHERE name = ANY(%s) OR code = ANY(%s)",
                (sectors, list(INDEX_TOTALS)))
    idx = sorted(r[0] for r in cur.fetchall())
    copy("sector_index", ("code", "name", "date", "open", "high", "low", "close", "volume", "trade_value"),
         rng, (idx, start, end))
    dst.commit()
    dst.close()
    src.close()
    n["index_codes"] = idx
    return n


# ── rowdiff ────────────────────────────────────────────────────────────────
def _frame(db: str, sql: str) -> pd.DataFrame:
    con = connect(db)
    try:
        return pd.read_sql_query(sql, con)
    finally:
        con.close()


def rowdiff(a_db: str, b_db: str, *, start: str | None = None) -> dict:
    """두 sqlite 의 원 표를 (code, date) 로 맞대 열마다 불일치 행 수·최대 차를 센다."""
    w = f" WHERE date >= '{start}'" if start else ""
    out: dict = {}
    sa = set(_frame(a_db, "SELECT code FROM stocks")["code"])
    sb = set(_frame(b_db, "SELECT code FROM stocks")["code"])
    out["stocks"] = {"a_only": sorted(sa - sb), "b_only": sorted(sb - sa), "common": len(sa & sb)}
    meta_a = _frame(a_db, "SELECT code, name, market, sector FROM stocks").set_index("code")
    meta_b = _frame(b_db, "SELECT code, name, market, sector FROM stocks").set_index("code")
    common = sorted(sa & sb)
    mm = [c for c in common if tuple(meta_a.loc[c]) != tuple(meta_b.loc[c])]
    out["stocks"]["meta_mismatch"] = {c: [list(meta_a.loc[c]), list(meta_b.loc[c])] for c in mm}

    specs = {
        "supply_demand": ("code, date", ["close", "flu_rt", "acc_trde_qty", *INVESTOR_COLUMNS]),
        "daily_bars": ("code, date", ["open", "high", "low", "close", "volume", "trade_value"]),
        "daily_bars_adjusted": ("code, date", ["close"]),
        "daily_bars_raw": ("code, date", ["close"]),
        "sector_index": ("code, date", ["close"]),
    }
    for table, (key, cols) in specs.items():
        sel = f"SELECT {key}, {', '.join(cols)} FROM {table}{w}"
        a, b = _frame(a_db, sel), _frame(b_db, sel)
        a["date"], b["date"] = a["date"].astype(str), b["date"].astype(str)
        if table != "sector_index":
            a, b = a[a["code"].isin(common)], b[b["code"].isin(common)]
        m = a.merge(b, on=["code", "date"], how="outer", suffixes=("_a", "_b"), indicator=True)
        rep = {"a_only": int((m["_merge"] == "left_only").sum()),
               "b_only": int((m["_merge"] == "right_only").sum()),
               "both": int((m["_merge"] == "both").sum()), "cols": {}}
        both = m[m["_merge"] == "both"]
        for c in cols:
            x, y = both[f"{c}_a"].astype(float), both[f"{c}_b"].astype(float)
            d = (x - y).abs()
            bad = d > 1e-9 * (1 + y.abs())
            if bad.any():
                worst = both.loc[d.idxmax()]
                rep["cols"][c] = {"rows": int(bad.sum()), "max_abs": float(d.max()),
                                  "worst": [worst["code"], worst["date"], _val(worst[f"{c}_a"]),
                                            _val(worst[f"{c}_b"])],
                                  "dates": sorted(both.loc[bad, "date"].unique().tolist())[:8]}
        out[table] = rep
    return out


# ── compare ────────────────────────────────────────────────────────────────
def _rel(a: float, b: float, floor: float = REL_FLOOR_EOK) -> "float | None":
    return abs(a - b) / abs(b) if abs(b) >= floor else None


def _maxrec(cur: "dict | None", cand: dict, key: str) -> dict:
    if cand.get(key) is None:
        return cur or cand
    if cur is None or cur.get(key) is None or cand[key] > cur[key]:
        return cand
    return cur


def flows_table(A: dict, B: dict) -> list[dict]:
    """시장×섹터×주체마다: 일별 최대 절대차[억], L1 상대오차(Σ|a−b|/Σ|b|), 창별 합의 최대 상대오차."""
    rows = []
    for m in B["markets"]:
        for s in B["sectors"]:
            if not B["n_by_sector"].get(m, {}).get(s):
                continue
            for group, keys in (("flows", FLOW_KEYS), ("detail", DETAIL_KEYS)):
                for k in keys:
                    a = A[group].get(m, {}).get(s, {}).get(k)
                    b = B[group][m][s][k]
                    if a is None:
                        rows.append({"market": m, "sector": s, "actor": k, "missing_in_a": True})
                        continue
                    d = [abs(x - y) for x, y in zip(a, b)]
                    imax = max(range(len(d)), key=d.__getitem__)
                    l1b = sum(abs(y) for y in b)
                    win = None
                    for wnd in WINDOWS:
                        sa, sb = sum(a[-wnd:]), sum(b[-wnd:])
                        win = _maxrec(win, {"win": wnd, "rel": _rel(sa, sb), "a": round(sa, 2),
                                            "b": round(sb, 2)}, "rel")
                    rows.append({"market": m, "sector": s, "actor": k,
                                 "max_abs": round(d[imax], 2), "max_abs_date": B["dates"][imax],
                                 "days_diff": sum(1 for x in d if x > 0.005),
                                 "l1_rel": (sum(d) / l1b) if l1b else None,
                                 "win_worst": win})
    return rows


def ret_table(A: dict, B: dict, key: str) -> list[dict]:
    """섹터 일별 수익률(``ret`` 자체 계산 / ``iret`` 업종지수)의 최대 차[%p]와 창별 누적 수익률 차."""
    rows = []
    for m in B["markets"]:
        for s in B["sectors"]:
            a, b = A[key].get(m, {}).get(s), B[key].get(m, {}).get(s)
            if b is None and a is None:
                continue
            if a is None or b is None:
                rows.append({"market": m, "sector": s, "only": "a" if a is not None else "b"})
                continue
            pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
            d = [abs(x - y) for x, y in pairs]
            cum = {}
            for wnd in WINDOWS:
                ca = cb = 1.0
                for x, y in pairs[-wnd:]:
                    ca *= 1 + x / 100.0
                    cb *= 1 + y / 100.0
                cum[str(wnd)] = round((ca - cb) * 100.0, 4)
            rows.append({"market": m, "sector": s, "max_abs_pp": round(max(d), 4) if d else None,
                         "days_diff": sum(1 for x in d if x > 1e-6),
                         "cum_diff_pp": cum})
    return rows


#: 아무 날에 끝나는 창의 길이(1 = 일별). 뷰어가 임의 구간·260일 프리셋을 고를 수 있어 기준일 끝 창만으로는 모자란다.
ANY_END_WINDOWS = (1, 5, 20, 60, 120, 260)
ALL_MARKETS = "전체"


def _cell_series(P: dict, key: str, m: str, s: str) -> "list | None":
    """한 칸의 일별 수익률. ``m == "전체"`` 이면 시장별 ``ret`` 를 전일 시총 가중치 ``retw`` 로 합친다
    (``scripts/sector_numbers.py`` ``agg`` 와 같은 식 — 수치표·뷰어의 전체 칸)."""
    if m != ALL_MARKETS:
        return P[key].get(m, {}).get(s)
    parts = [(P[key].get(mk, {}).get(s), P["retw"].get(mk, {}).get(s)) for mk in P["markets"]]
    parts = [(r, w) for r, w in parts if r and w]
    if not parts:
        return None
    out = []
    for i in range(len(P["dates"])):
        num = den = 0.0
        for r, w in parts:
            if r[i] is not None and w[i]:
                num += r[i] * w[i]
                den += w[i]
        out.append(num / den if den else 0.0)
    return out


def ret_any_end(A: dict, B: dict, key: str = "ret", windows: tuple[int, ...] = ANY_END_WINDOWS, *,
                first: "str | None" = None, last: "str | None" = None) -> dict:
    """창 길이마다 **모든 끝날**의 누적 수익률 차 최대 |A − B|[%p]와 그 칸(시장·섹터·끝날).

    시장 키는 페이로드의 시장들에 ``전체``(시총 가중 합, ``key == "ret"`` 일 때)를 더한다.
    누적 = Π(1 + r/100) − 1. 결측(``None``)은 0 수익으로 센다 — 한쪽만 결측이면 그 차가 그대로 드러난다.
    ``first``·``last`` 를 주면 그 날짜 범위 **안에 통째로 든** 창만 본다(오차가 몰린 구간을 가르는 용도).
    Returns: ``{"<창>": {"max_pp", "market", "sector", "end", "n"}}`` — ``n`` = 잰 (칸, 끝날) 수.
    """
    dates = B["dates"]
    lo = 0 if first is None else next((i for i, d in enumerate(dates) if d >= first), len(dates))
    hi = len(dates) - 1 if last is None else max((i for i, d in enumerate(dates) if d <= last), default=-1)
    markets = list(B["markets"]) + ([ALL_MARKETS] if key == "ret" else [])
    cells = []
    for m in markets:
        for s in B["sectors"]:
            a, b = _cell_series(A, key, m, s), _cell_series(B, key, m, s)
            if a is None and b is None:
                continue
            ca, cb = [1.0], [1.0]
            for x, y in zip(a or [None] * len(dates), b or [None] * len(dates)):
                ca.append(ca[-1] * (1 + (x or 0.0) / 100.0))
                cb.append(cb[-1] * (1 + (y or 0.0) / 100.0))
            cells.append((m, s, ca, cb))
    out: dict = {}
    for w in windows:
        best: dict = {"max_pp": 0.0, "market": None, "sector": None, "end": None, "n": 0}
        for m, s, ca, cb in cells:
            for e in range(lo + w - 1, hi + 1):
                d = abs((ca[e + 1] / ca[e + 1 - w]) - (cb[e + 1] / cb[e + 1 - w])) * 100.0
                best["n"] += 1
                if d > best["max_pp"]:
                    best.update(max_pp=d, market=m, sector=s, end=dates[e])
        best["max_pp"] = round(best["max_pp"], 4)
        out[str(w)] = best
    return out


def leaf_diff(a: Any, b: Any, path: str = "", out: "list | None" = None) -> list:
    """두 JSON 의 숫자 잎을 경로별로 대조한다. 구조가 다르면 그것도 적는다."""
    out = [] if out is None else out
    if isinstance(b, dict):
        if not isinstance(a, dict):
            out.append((path, "type", None, None))
            return out
        for k in set(a) | set(b):
            if k not in a or k not in b:
                out.append((f"{path}/{k}", "missing_a" if k not in a else "missing_b", None, None))
            else:
                leaf_diff(a[k], b[k], f"{path}/{k}", out)
    elif isinstance(b, list):
        if not isinstance(a, list) or len(a) != len(b):
            out.append((path, "len", len(a) if isinstance(a, list) else None, len(b)))
            return out
        for i, (x, y) in enumerate(zip(a, b)):
            leaf_diff(x, y, f"{path}[{i}]", out)
    elif isinstance(b, (int, float)) and not isinstance(b, bool):
        if not isinstance(a, (int, float)) or isinstance(a, bool):
            out.append((path, "type", a, b))
        elif not (math.isclose(a, b, rel_tol=0, abs_tol=1e-12)):
            out.append((path, "num", a, b))
    elif a != b:
        out.append((path, "value", a, b))
    return out


def _group(path: str) -> str:
    import re
    p = re.sub(r"\[\d+\]", "[]", path)
    p = re.sub(r"/names/\d{6}", "/names/<code>", p)
    return p


def summarize_leaves(diffs: list) -> list[dict]:
    """경로 무리(인덱스·종목코드를 접은 경로)별 개수와 최대 절대·상대차."""
    g: dict = {}
    for path, kind, a, b in diffs:
        key = (_group(path), kind)
        e = g.setdefault(key, {"group": key[0], "kind": kind, "n": 0, "max_abs": 0.0, "max_rel": None,
                               "example": None})
        e["n"] += 1
        if kind == "num":
            d = abs(a - b)
            if d >= e["max_abs"]:
                e["max_abs"], e["example"] = d, [path, a, b]
            if abs(b) > 1e-9:
                r = d / abs(b)
                e["max_rel"] = r if e["max_rel"] is None else max(e["max_rel"], r)
        elif e["example"] is None:
            e["example"] = [path, a, b]
    return sorted(g.values(), key=lambda e: (-e["n"], e["group"]))


def _scripts_dir() -> Path:
    here = Path(__file__).resolve()
    for cand in (here.parents[3] / "scripts", here.parents[1] / "_scripts"):
        if (cand / "sector_numbers.py").exists():
            return cand
    raise FileNotFoundError("scripts/sector_numbers.py 를 못 찾았다")


def numbers_of(payload: dict) -> dict:
    """``scripts/sector_numbers.build`` — 수치표가 화면에 싣는 값."""
    spec = importlib.util.spec_from_file_location("_sn", _scripts_dir() / "sector_numbers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build(payload)


def restrict(full: dict, sectors: list[str]) -> dict:
    """전체 유니버스 페이로드에서 ``sectors`` 칸만 — 조각 추출 검산용."""
    out = dict(full)
    for k in ("flows", "detail", "ret", "retw", "iret"):
        out[k] = {m: {s: v for s, v in full[k].get(m, {}).items() if s in sectors} for m in full["markets"]}
    out["n_by_sector"] = {m: {s: v for s, v in full["n_by_sector"].get(m, {}).items() if s in sectors}
                          for m in full["markets"]}
    out["sectors"] = [s for s in full["sectors"] if s in sectors]
    return out


def compare(a: dict, b: dict) -> dict:
    """``a`` = 대조 대상(BYO), ``b`` = 기준(kr_quant 조각)."""
    res: dict = {"dates_equal": a["dates"] == b["dates"],
                 "dates": [b["dates"][0], b["dates"][-1], len(b["dates"])],
                 "n_by_sector": {"a": a["n_by_sector"], "b": b["n_by_sector"]},
                 "flows": flows_table(a, b), "ret": ret_table(a, b, "ret"),
                 "iret": ret_table(a, b, "iret"), "ret_any_end": ret_any_end(a, b)}
    res["payload_leaves"] = summarize_leaves(leaf_diff(a, b))
    try:
        res["numbers_leaves"] = summarize_leaves(leaf_diff(numbers_of(a), numbers_of(b)))
    except Exception as e:  # noqa: BLE001 — 수치표 대조가 죽어도 페이로드 대조는 남긴다
        res["numbers_error"] = f"{type(e).__name__}: {e}"
    return res


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(prog="sectorflow.collect.equiv")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("slice")
    s.add_argument("--dsn", default=os.environ.get("KR_QUANT_DB", ""))
    s.add_argument("--sectors", required=True)
    s.add_argument("--like", help="이 sqlite 의 supply_demand 날짜 범위를 쓴다")
    s.add_argument("--start")
    s.add_argument("--end")
    s.add_argument("--out", required=True)
    s.add_argument("--force", action="store_true", help="--out 이 이미 있으면 지우고 다시 만든다")
    r = sub.add_parser("rowdiff")
    r.add_argument("a")
    r.add_argument("b")
    r.add_argument("--start")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--full", help="전체 유니버스 kr_quant 페이로드 — b 와 같은 섹터 칸이 같은지 검산")
    c.add_argument("--out")
    args = ap.parse_args(argv)

    if args.cmd == "slice":
        if not args.dsn.startswith(("postgres://", "postgresql://")):
            raise SystemExit("--dsn(또는 KR_QUANT_DB)이 Postgres DSN 이 아니다")
        start, end = args.start, args.end
        if args.like:
            span = _frame(args.like, "SELECT min(date) a, max(date) b FROM supply_demand")
            start, end = start or span["a"][0], end or span["b"][0]
        sectors = [x.strip() for x in args.sectors.split(",") if x.strip()]
        print(json.dumps(slice_kr_quant(args.dsn, args.out, sectors, start, end, force=args.force),
                         ensure_ascii=False))
        return 0
    if args.cmd == "rowdiff":
        print(json.dumps(rowdiff(args.a, args.b, start=args.start), ensure_ascii=False, indent=1,
                         default=str))
        return 0
    with open(args.a, encoding="utf-8") as f:
        A = json.load(f)
    with open(args.b, encoding="utf-8") as f:
        B = json.load(f)
    res = compare(A, B)
    if args.full:
        with open(args.full, encoding="utf-8") as f:
            F = restrict(json.load(f), B["sectors"])
        res["slice_vs_full"] = {"flows": flows_table(B, F), "iret": ret_table(B, F, "iret"),
                                "ret": ret_table(B, F, "ret"), "dates_equal": B["dates"] == F["dates"]}
    text = json.dumps(res, ensure_ascii=False, indent=1, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    print(text[:20000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
