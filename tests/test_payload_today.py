"""페이로드의 "오늘" — 1일 값·1년 백분위·시장 합계·시장 수익률·직전 리포트 대비·잠정→확정 차이.

합성 종목 표(``load()`` 가 내는 모양)로 ``build_payload`` 를 직접 돌리고, 검증기(T 층)가 그 결과를
**따로 다시 세어** 맞추는지, 그리고 값을 틀리게 바꾸면 검증기가 빨개지는지 본다. DB·네트워크 불요.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent


def _mod(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sf = _mod("sector_flow")
vr = _mod("verify_report")

N_DAYS = 280            # 되돌아보기(260)보다 길게 — 1년 밖의 날이 백분위에 섞이면 잡히게
SECTORS = {"거래소": ["반도체", "금융", "화학", "보험"], "코스닥": ["반도체", "금융", "화학"]}


def _df(n_days: int = N_DAYS, seed: int = 7) -> tuple[pd.DataFrame, list[str], pd.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = [d.date().isoformat() for d in pd.bdate_range("2025-06-02", periods=n_days)]
    rows, caps = [], []
    i = 0
    for mk, secs in SECTORS.items():
        for sec in secs:
            for j in range(4):
                code = f"{i:06d}"
                i += 1
                cap0 = float(rng.uniform(100, 5000))
                quiet = sec == "화학" and j > 0          # 거의 늘 0 인 종목 — 0 이 많은 줄
                for t, d in enumerate(dates):
                    if j == 3 and t % 7 == 0:            # 가끔 거래가 없는 날
                        continue
                    r = {"code": code, "date": d, "name": f"N{code}", "sector": sec, "market": mk}
                    for k in ("indiv", "forgn", "inst", "etc"):
                        r[k] = 0.0 if quiet and rng.random() < 0.9 else float(rng.normal(0, 10))
                    r["tv"] = float(rng.uniform(1, 100))
                    for k, _ in sf.INST_DETAIL:
                        r[k] = 0.0 if (quiet or rng.random() < 0.3) else float(rng.normal(0, 3))
                    r["ret"] = None if rng.random() < 0.02 else float(rng.normal(0.05, 2))
                    r["cap"] = cap0 * (1 + t / 1000)
                    rows.append(r)
                caps.append({"code": code, "sector": sec, "market": mk, "cap": cap0})
    df = pd.DataFrame(rows).sort_values(["code", "date"])
    df["cap_lag"] = df.groupby("code")["cap"].shift(1)
    return df, dates, pd.DataFrame(caps)


def _payload(n_days: int = N_DAYS, seed: int = 7, df_mod=None) -> dict:
    df, dates, caps = _df(n_days, seed)
    if df_mod:
        df = df_mod(df)
    P = json.loads(json.dumps(sf.build_payload(df, dates, caps)))
    P["change"], P["change_reason"] = sf.change_block(P, None)
    return P


@pytest.fixture(scope="module")
def P() -> dict:
    return _payload()


def _t_fails(P: dict, folder=None) -> list[str]:
    vr.FAILS.clear()
    vr.layer_t(P, folder)
    return list(vr.FAILS)


# ── 1일 값과 검증기 ──────────────────────────────────────────────────────────

def test_today_is_the_last_day_and_the_verifier_agrees(P):
    T = P["today"]
    assert T["date"] == P["dates"][-1]
    assert T["sectors"]["거래소"]["반도체"]["inst"]["v"] == P["flows"]["거래소"]["반도체"]["inst"][-1]
    both = round(P["detail"]["거래소"]["금융"]["invtrt"][-1] + P["detail"]["코스닥"]["금융"]["invtrt"][-1]
                 + P["detail"]["거래소"]["금융"]["penfnd_etc"][-1]
                 + P["detail"]["코스닥"]["금융"]["penfnd_etc"][-1], 2)
    assert T["sectors"]["전체"]["금융"]["invpen"]["v"] == pytest.approx(both, abs=0.011)
    # 종목이 없는 (시장, 섹터)는 0 이 아니라 칸이 없다.
    assert "보험" not in T["sectors"]["코스닥"] and "보험" in T["sectors"]["전체"]
    for row in T["sectors"]["전체"].values():
        assert not {"x", "G", "accel"} & set(row), "1일 칸에 정의 안 되는 파생값을 실었다"
    assert _t_fails(P) == []


def test_market_totals_and_returns_come_from_the_stocks(P):
    df, dates, _ = _df()
    last = df[df["date"] == dates[-1]]
    ks = last[last["market"] == "코스닥"]
    M = P["today"]["market"]["코스닥"]
    assert M["net"]["inst"]["v"] == pytest.approx(ks["inst"].sum(), abs=0.006)
    g = ks.dropna(subset=["ret", "cap_lag"])
    assert M["ret"]["1"] == pytest.approx((g["ret"] * g["cap_lag"]).sum() / g["cap_lag"].sum(), abs=0.001)
    acc = 1.0
    for d in dates[-20:]:
        g = df[(df["date"] == d) & (df["market"] == "코스닥")].dropna(subset=["ret", "cap_lag"])
        acc *= 1 + (g["ret"] * g["cap_lag"]).sum() / g["cap_lag"].sum() / 100
    assert M["ret"]["20"] == pytest.approx((acc - 1) * 100, abs=0.001)
    assert set(P["today"]["market"]) == {"전체", "거래소", "코스닥"}


def test_stocks_have_a_one_day_window(P):
    df, dates, _ = _df()
    last = df[df["date"] == dates[-1]].set_index("code")
    for code, r in P["names"].items():
        if code in last.index:
            assert r["win"]["1"]["inst"] == pytest.approx(last.loc[code, "inst"], abs=0.051)
        else:
            assert "1" not in r["win"], "오늘 거래가 없는 종목에 1일 칸이 생겼다"


# ── 백분위 ──────────────────────────────────────────────────────────────────

def test_quiet_series_is_not_read_as_a_record_day():
    """0 이 대부분인 줄은 백분위를 안 내고, 값이 다 같으면 가운데(50)다 — `<=` 로 세면 100 이 된다."""
    assert sf.day_pct([0.0] * 200 + [5.0] * 10) is None
    assert sf.day_pct([3.0] * 100) == pytest.approx(50.0)
    assert sf.day_pct([1.0, 2.0, 3.0]) == pytest.approx(100.0)
    assert sf.day_pct([3.0, 2.0, 1.0]) == pytest.approx(0.0)


def test_percentile_looks_back_one_year_only():
    """1년보다 앞의 날은 분포에 안 들어간다 — 40일 전 큰 값이 오늘을 눌러선 안 된다."""
    ser = [1000.0] * 40 + [float(i) for i in range(260)]
    assert sf.day_pct(ser) == pytest.approx(100.0)


# ── 검증기가 무는가 ──────────────────────────────────────────────────────────

def _bump_value(P):
    P["today"]["sectors"]["거래소"]["반도체"]["inst"]["v"] += 1.0


def _bump_pct(P):
    row = P["today"]["sectors"]["전체"]["금융"]["inst"]
    row["pct"] = 100.0 - row["pct"] if row["pct"] != 50.0 else 10.0


def _bump_market(P):
    P["today"]["market"]["거래소"]["net"]["forgn"]["v"] += 5.0


def _bump_market_pct(P):
    m = P["today"]["market"]["전체"]["net"]["inst"]
    m["pct"] = 100.0 - m["pct"] if abs(m["pct"] - 50) > 5 else 99.0


def _bump_ret(P):
    P["today"]["market"]["코스닥"]["ret"]["20"] += 0.5


def _drop_sector(P):
    del P["today"]["sectors"]["코스닥"]["화학"]


def _drop_name_window(P):
    for r in P["names"].values():
        r["win"].pop("1", None)


@pytest.mark.parametrize("corrupt", [_bump_value, _bump_pct, _bump_market, _bump_market_pct,
                                     _bump_ret, _drop_sector, _drop_name_window])
def test_verifier_catches_a_wrong_today(P, corrupt):
    bad = json.loads(json.dumps(P))
    corrupt(bad)
    assert _t_fails(bad), f"{corrupt.__name__} 를 검증기가 못 잡았다"


# ── 직전 리포트 대비 ─────────────────────────────────────────────────────────

def _write(root: Path, date: str, payload: dict, *extra: str) -> Path:
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    (d / "payload.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    for name in extra:
        (d / name).write_text("x")
    return d


def _prev_and_cur():
    df, dates, caps = _df()
    prev = json.loads(json.dumps(sf.build_payload(df[df["date"] < dates[-1]], dates[:-1], caps)))
    cur = json.loads(json.dumps(sf.build_payload(df, dates, caps)))
    for p in (prev, cur):
        p["change"], p["change_reason"] = sf.change_block(p, None)
    return prev, cur


def test_change_compares_with_the_previous_trading_days_report(tmp_path):
    prev, cur = _prev_and_cur()
    _write(tmp_path, prev["dates"][-1], prev, "VERIFY_FAILED.txt")
    # 같은 날짜 폴더(바꿔 끼울 잠정본)가 있어도 직전 대비는 그걸 읽지 않는다.
    decoy = json.loads(json.dumps(cur))
    for m in decoy["flows"]:
        for s in decoy["flows"][m]:
            decoy["flows"][m][s]["inst"] = [-v for v in decoy["flows"][m][s]["inst"]]
    _write(tmp_path, cur["dates"][-1], decoy)
    C, why = sf.change_block(cur, str(tmp_path))
    assert why is None and C["prev_date"] == prev["dates"][-1] < cur["dates"][-1]
    assert C["prev_verify_failed"] is True and C["prev_final"] is False
    r = C["rank"]["전체"]["inst"]
    assert sorted(v[1] for v in r.values()) == list(range(1, len(r) + 1))
    t = C["top"]["거래소"]["invpen"]
    assert t["entered"] == [s for s in t["cur"] if s not in t["prev"]]
    cur["change"], cur["change_reason"] = C, why
    assert _t_fails(cur, str(tmp_path / f"{cur['dates'][-1]}.building.1")) == []
    # 순위를 틀리게 바꾸면 검증기가 직전 리포트에서 다시 세어 잡는다.
    s0 = next(iter(r))
    r[s0][0] = (r[s0][0] or 0) + 1
    assert _t_fails(cur, str(tmp_path / f"{cur['dates'][-1]}.building.1"))


def test_change_is_null_with_a_reason_when_the_previous_report_is_missing(tmp_path):
    prev, cur = _prev_and_cur()
    C, why = sf.change_block(cur, str(tmp_path))
    assert C is None and "폴더가 없다" in why
    (tmp_path / prev["dates"][-1]).mkdir()
    (tmp_path / prev["dates"][-1] / "INCOMPLETE.txt").write_text("x")
    C, why = sf.change_block(cur, str(tmp_path))
    assert C is None and "INCOMPLETE" in why
    C, why = sf.change_block(cur, None)
    assert C is None and why
    # 직전 리포트가 옆에 있는데 대비가 비었으면 검증기가 잡는다.
    _write(tmp_path, prev["dates"][-1], prev)
    cur["change"], cur["change_reason"] = None, "어쩌다"
    assert _t_fails(cur, str(tmp_path / cur["dates"][-1]))


def test_comparisons_skip_when_the_sector_override_changed_between_reports(tmp_path):
    """덮어쓰기가 바뀐 첫날 — 같은 섹터 이름이 다른 종목 집합이라 직전 대비·잠정→확정을 내지 않는다(이유는 적는다)."""
    prev, cur = _prev_and_cur()
    cur["sector_override"] = sf.override_block({"024110": "금융"})
    _write(tmp_path, prev["dates"][-1], prev)                   # 옛 리포트 — 지문 없음(none)
    C, why = sf.change_block(cur, str(tmp_path))
    assert C is None and "덮어쓰기" in why
    cur["change"], cur["change_reason"] = C, why
    assert _t_fails(cur, str(tmp_path / cur["dates"][-1])) == [], "지문이 다르면 비교가 비는 것이 맞다"
    _write(tmp_path, cur["dates"][-1], dict(cur, sector_override=None))
    fd, why = sf.final_diff_block(cur, str(tmp_path))
    assert fd is None and "덮어쓰기" in why
    prev["sector_override"] = cur["sector_override"]
    _write(tmp_path, prev["dates"][-1], prev)
    assert sf.change_block(cur, str(tmp_path))[0] is not None    # 같은 지문이면 비교한다


def test_reports_root_is_only_inferred_from_a_dated_folder(tmp_path):
    assert sf.reports_root(str(tmp_path / "2026-11-06.building.123" / "payload.json")) == str(tmp_path)
    assert sf.reports_root(str(tmp_path / "2026-11-06" / "payload.json")) == str(tmp_path)
    assert sf.reports_root(str(tmp_path / "scratch" / "payload.json")) is None


# ── 잠정 → 확정 ──────────────────────────────────────────────────────────────

def test_final_diff_lists_only_changes_beyond_rounding(tmp_path):
    _prev, new = _prev_and_cur()
    old = json.loads(json.dumps(new))
    old["flows"]["코스닥"]["금융"]["inst"][-1] -= 12.34          # 확정에서 바뀐 칸
    old["flows"]["거래소"]["화학"]["inst"][-1] -= 0.01           # 반올림 안쪽
    old["detail"]["거래소"]["반도체"]["penfnd_etc"][-3] += 7.0   # 5일 창에만 걸린다
    _write(tmp_path, new["dates"][-1], old)
    fd, why = sf.final_diff_block(new, str(tmp_path))
    assert why is None and fd["base_final"] is False
    got = {(r["market"], r["sector"], r["actor"], r["win"]): r["diff"] for r in fd["changed"]}
    assert got == {("코스닥", "금융", "inst", 1): 12.34, ("코스닥", "금융", "inst", 5): 12.34,
                   ("거래소", "반도체", "penfnd_etc", 5): -7.0}
    tot = {(t["market"], t["actor"], t["win"]): t["diff"] for t in fd["market_totals"]}
    assert tot[("코스닥", "inst", 1)] == pytest.approx(12.34, abs=0.011)
    new["final"] = True
    new["final_diff"], new["final_diff_reason"] = fd, why
    txt = sf.final_diff_text(new)
    assert "코스닥/금융 기관 1일" in txt and "3칸 바뀜" in txt
    assert _t_fails(new) == []
    new["final_diff"]["changed"][0]["new"] += 1.0
    assert "T/잠정 → 확정 — 확정값·차이가 페이로드에서 다시 나오는가" in _t_fails(new), "확정값을 틀리게 적어도 검증기가 모른다"


def test_final_diff_row_within_the_rounding_tolerance_fails_the_verifier(tmp_path):
    """문턱 안쪽 칸을 '바뀜' 으로 실으면 검증기가 거부한다 — 생산자 규칙이 느슨해지면 알게 하는 쪽."""
    _prev, new = _prev_and_cur()
    fd = sf.final_diff_compute(new, json.loads(json.dumps(new)))
    ser = new["flows"]["코스닥"]["금융"]["inst"]
    fd["changed"] = [{"market": "코스닥", "sector": "금융", "actor": "inst", "win": 1,
                      "old": round(ser[-1] - 0.05, 2), "new": ser[-1], "diff": 0.05}]
    fd["n_changed"] = 1
    new.update(final=True, final_diff=fd, final_diff_reason=None)
    assert "T/잠정 → 확정 — 확정값·차이가 페이로드에서 다시 나오는가" in _t_fails(new)


def test_final_diff_keeps_every_one_day_cell_as_provisional_and_final(tmp_path):
    _prev, new = _prev_and_cur()
    old = json.loads(json.dumps(new))
    old["flows"]["코스닥"]["금융"]["inst"][-1] -= 12.34
    _write(tmp_path, new["dates"][-1], old)
    fd, _why = sf.final_diff_block(new, str(tmp_path))
    cells = {(m, s, a): (o, n) for m, s, a, o, n in fd["cells_1d"]}
    o, n = cells[("코스닥", "금융", "inst")]
    assert n - o == pytest.approx(12.34, abs=0.011)
    assert len(cells) == sum(1 for m in new["markets"] for s in new["sectors"]
                             if new["n_by_sector"][m].get(s)) * len(sf.FINAL_DIFF_ACTORS)
    assert fd["extremes_change"] == {} or all(
        set(ch) == {"hi_in", "hi_out", "lo_in", "lo_out"} for v in fd["extremes_change"].values() for ch in v.values())


def test_extremes_change_names_sectors_that_entered_the_top_percentile_after_finalisation():
    _prev, new = _prev_and_cur()
    old = json.loads(json.dumps(new))
    ser = old["flows"]["코스닥"]["금융"]["inst"]
    ser[-1] = min(ser) - 1.0                 # 잠정: 1년 최저 → 확정: 원래 값
    newser = new["flows"]["코스닥"]["금융"]["inst"]
    newser[-1] = max(newser) + 1.0           # 확정: 1년 최고
    ch = sf.extremes_change(new, old)["코스닥"]["inst"]
    assert ch["hi_in"] == ["금융"] and ch["lo_out"] == ["금융"]


def _final_folder(root: Path, date: str, cells, base_final=False, as_payload=False, built=None, failed=False):
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    (d / "FINAL.txt").write_text("확정")
    if failed:
        (d / "VERIFY_FAILED.txt").write_text("x")
    fd = {"base_final": base_final, "cells_1d": cells, "base_built": built or f"{date} 18:12"}
    if as_payload:
        (d / "payload.json").write_text(json.dumps({"final": True, "final_diff": fd}), encoding="utf-8")
    else:
        (d / "final_diff.json").write_text(json.dumps(fd), encoding="utf-8")


def test_revision_stats_pool_final_reports_up_to_the_report_day_only(tmp_path):
    """잠정→확정 수정 폭: 지난 확정 리포트의 1일 칸에서, 분모 게이트(목록 안 2%)를 넘는 칸만."""
    big = [["거래소", f"S{i}", "inst", 100.0, 100.0] for i in range(8)]
    cells_a = big + [["거래소", "X", "inst", 50.0, 100.0],      # 50% 수정
                     ["거래소", "Y", "inst", -10.0, 100.0],     # 부호 뒤집힘 (110%)
                     ["거래소", "Z", "inst", 0.0, 0.5]]         # 게이트 밖(0.5 < 2% × 1,000.5)
    _final_folder(tmp_path, "2026-11-04", cells_a)
    _final_folder(tmp_path, "2026-11-05", big, as_payload=True)
    _final_folder(tmp_path, "2026-11-03", big, base_final=True)           # 확정 위의 확정 — 뺀다
    _final_folder(tmp_path, "2026-11-02", big, built="2026-11-03 07:40")  # 다음날 아침에 다시 만든 잠정 — 뺀다
    _final_folder(tmp_path, "2026-10-31", big, failed=True)               # 검증 실패 — 뺀다
    (tmp_path / "2026-10-30").mkdir()                                       # 잠정 폴더 — 확정 아님
    _final_folder(tmp_path, "2026-11-09", [["거래소", "F", "inst", -999.0, 100.0]])   # 기준일 뒤 — 미래
    P = {"dates": ["2026-11-05", "2026-11-06"]}
    R = sf.revision_stats(P, str(tmp_path))
    assert R["dates"] == ["2026-11-04", "2026-11-05"] and R["n_dates"] == 2
    assert R["n_cells"] == 8 + 2 + 8 and R["flips"] == 1
    assert R["rel_median_pct"] == 0.0
    assert not {"2026-11-03", "2026-11-02", "2026-10-31"} & set(R["dates"])
    assert {"2026-11-03", "2026-11-02", "2026-10-31"} <= set(R["skipped"]), R["skipped"]
    only_a = sf.revision_rows(cells_a)
    assert sorted(only_a[0])[-2:] == pytest.approx([50.0, 110.0]) and only_a[1] == 1
    # 확정 리포트는 자기 잠정 대비도 넣는다.
    P2 = {"dates": ["2026-11-05", "2026-11-06"], "final": True,
          "final_diff": {"base_final": False, "cells_1d": cells_a, "base_built": "2026-11-06 18:12"}}
    assert sf.revision_stats(P2, str(tmp_path))["dates"][-1] == "2026-11-06"
    assert sf.revision_stats({"dates": ["2026-11-06"]}, None)["n_dates"] == 0
    # 최근 것부터 REV_DAYS 개를 모으면 멈춘다 — 그보다 오래된 폴더의 사유는 적지 않는다.
    for i in range(1, 25):
        _final_folder(tmp_path, f"2026-09-{i:02d}", big)
    R = sf.revision_stats(P, str(tmp_path))
    assert R["n_dates"] == sf.REV_DAYS and R["dates"][-1] == "2026-11-05" and "2026-09-01" not in R["dates"]


def test_sector_override_fills_only_listed_codes_and_names_the_rest_unclassified():
    from sectorflow.sector_override import SECTOR_OVERRIDE, sector_sql
    codes = pd.Series(["024110", "323410", "999999", "000001", "006220"])
    secs = pd.Series(["", None, "", "화학", "은행"])        # 제주은행 마스터가 채워졌다 — 마스터를 따른다
    got = sf.apply_sector_override(codes, secs).tolist()
    assert got == [SECTOR_OVERRIDE["024110"], SECTOR_OVERRIDE["323410"], "(미분류)", "화학", "은행"]
    # 덮는 값은 마스터에 이미 있는 섹터여야 한다 — 새 섹터를 만들지 않는다.
    assert set(SECTOR_OVERRIDE.values()) <= {"금융"}
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE stocks(code, sector)")
    con.executemany("INSERT INTO stocks VALUES (?,?)", list(zip(codes, secs)))
    q = f"SELECT {sector_sql('st.code', 'st.sector')} FROM stocks st ORDER BY rowid"
    assert [r[0] for r in con.execute(q)] == got, "검증기 F 층의 SQL 이 같은 표를 써야 한다"


def test_final_diff_without_a_provisional_report_says_so(tmp_path):
    _prev, new = _prev_and_cur()
    fd, why = sf.final_diff_block(new, str(tmp_path))
    assert fd is None and "잠정 리포트가 없었다" in why
    new["final"], new["final_diff"], new["final_diff_reason"] = True, fd, why
    assert "비교 없음" in sf.final_diff_text(new)
    assert _t_fails(new) == []
    del new["final_diff"]
    assert _t_fails(new), "확정 리포트에 잠정 대비가 빠져도 검증기가 모른다"


def test_main_writes_final_diff_next_to_the_payload(tmp_path, monkeypatch):
    """확정 모드는 산출물 옆에 final_diff.txt 를 남긴다 — daily_report.sh 가 FINAL.txt 에 붙인다."""
    df, dates, caps = _df()
    monkeypatch.setattr(sf, "connect", lambda db: type("C", (), {"close": lambda self: None})())
    monkeypatch.setattr(sf, "load", lambda con, days, asof=None: (df.drop(columns=["cap", "cap_lag"]), dates))
    monkeypatch.setattr(sf, "attach_daily_cap", lambda con, d: df)
    monkeypatch.setattr(sf, "sector_cap", lambda con, d, last: caps)
    monkeypatch.setattr(sf, "index_returns", lambda *a, **k: {m: {} for m in SECTORS})
    monkeypatch.setattr(sf, "sector_cap_monthly", lambda *a: {})
    monkeypatch.setattr(sf, "market_context", lambda con, d, P: None)
    build = tmp_path / f"{dates[-1]}.building.9"
    build.mkdir()
    out = build / "payload.json"
    monkeypatch.setattr("sys.argv", ["sector_flow.py", "--db", "x", "--json", str(out), "--final"])
    sf.main()
    P = json.loads(out.read_text(encoding="utf-8"))
    assert P["final"] is True and P["final_diff"] is None
    assert "잠정 리포트가 없었다" in (build / "final_diff.txt").read_text(encoding="utf-8")
    assert P["change"] is None and "폴더가 없다" in P["change_reason"]
    assert os.path.basename(sf.reports_root(str(out)) or "") == tmp_path.name


def test_numbers_page_carries_today_for_the_terminal(P):
    """화면(TUI)은 numbers.html 의 D 하나만 읽는다 — 오늘·직전 대비가 거기까지 가야 그릴 수 있다."""
    sn = _mod("sector_numbers")
    D = sn.build(P)
    assert D["today"] == P["today"]
    assert "change" in D and D["change_reason"] == P["change_reason"]


# ── 시장 맥락 — KRX 지수(정규장)·금액 근사 오차 (DB 는 sqlite 메모리 표로 흉내) ──


def _ctx_db(dates, index_rows, bars):
    import sqlite3

    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE sector_index (code TEXT, name TEXT, date TEXT, close REAL)")
    con.executemany("INSERT INTO sector_index VALUES (?,?,?,?)", index_rows)
    con.execute("CREATE TABLE daily_bars (code TEXT, date TEXT, volume REAL, trade_value REAL, source TEXT)")
    con.executemany("INSERT INTO daily_bars VALUES (?,?,?,?,?)", bars)
    return con


def test_krx_index_returns_use_this_payloads_trading_days_and_never_fill():
    """KRX 종합지수 1·20일 수익률 — 기준일과 그 1·20 거래일 전 종가의 비. 없으면 메우지 않고 None.

    주입: 20일 기준을 20 거래일 전이 아니라 19 거래일 전으로 잡으면 값이 바뀐다.
    """
    dates = [f"2026-10-{d:02d}" for d in range(1, 26)]
    rows = [("001", "종합(KOSPI)", d, 1000.0 + 10 * i) for i, d in enumerate(dates)]
    rows += [("101", "종합(KOSDAQ)", d, 500.0) for d in dates[:-1]]       # 기준일 지수 없음
    con = _ctx_db(dates, rows, [])
    got = sf.krx_index_block(con, dates, ["거래소", "코스닥"])
    k = got["거래소"]["ret"]
    assert k["1"] == pytest.approx((1240 / 1230 - 1) * 100, abs=1e-3)
    assert k["20"] == pytest.approx((1240 / 1040 - 1) * 100, abs=1e-3)
    assert got["거래소"]["name"] == "코스피"
    assert got["코스닥"]["ret"] == {"1": None, "20": None}


def test_approx_gap_compares_close_with_vwap_weighted_by_absolute_net_quantity():
    """금액 근사 오차 = |Σ n·close − Σ n·vwap| / Σ n·vwap, n = 4주체 |순매수 수량| 합, vwap = 거래대금(백만원)·1e6/거래량.

    주입: 거래대금 단위를 원으로 읽으면(1e6 을 빼면) 오차가 100% 가까이 된다.
    """
    asof = "2026-11-06"
    df = pd.DataFrame([
        {"code": "A", "date": asof, "market": "거래소", "close": 100.0,
         "individual": -10, "foreign_": 4, "institution": 6, "etc_corp": 0},
        {"code": "B", "date": asof, "market": "코스닥", "close": 50.0,
         "individual": 0, "foreign_": -2, "institution": 2, "etc_corp": 0},
        {"code": "C", "date": asof, "market": "코스닥", "close": 10.0,      # 거래량 0 — 양쪽에서 빠진다
         "individual": 5, "foreign_": -5, "institution": 0, "etc_corp": 0},
    ])
    # A: vwap 110 (거래대금 1.1백만원 / 10,000주), B: vwap 50 — naver 행은 키움이 있으면 무시
    bars = [("A", asof, 10000, 1.1, "kiwoom"), ("A", asof, 10000, 9.9, "naver"),
            ("B", asof, 20000, 1.0, "kiwoom"), ("C", asof, 0, 0.0, "kiwoom")]
    got = sf.approx_gap(_ctx_db([asof], [], bars), df, asof, ["거래소", "코스닥"])
    assert got["거래소"]["pct"] == pytest.approx(abs(20 * 100 - 20 * 110) / (20 * 110) * 100, abs=0.01)
    assert got["코스닥"]["pct"] == pytest.approx(0.0)
    assert got["코스닥"]["cover"] == pytest.approx(200 / 300 * 100, abs=0.1)
    want = abs(2000 + 200 - (2200 + 200)) / 2400 * 100
    assert got["전체"]["pct"] == pytest.approx(want, abs=0.01)


def test_sector_gap_is_larger_than_the_market_gap_where_errors_do_not_cancel():
    """시장 합계는 종목 간 오차가 상쇄된다 — 섹터 칸 최대값을 같이 싣는다(분모 게이트 2%)."""
    asof = "2026-11-06"
    df = pd.DataFrame([
        {"code": "A", "date": asof, "market": "거래소", "sector": "금융", "close": 100.0,
         "individual": -100, "foreign_": 0, "institution": 100, "etc_corp": 0},
        {"code": "B", "date": asof, "market": "거래소", "sector": "화학", "close": 100.0,
         "individual": 100, "foreign_": 0, "institution": -100, "etc_corp": 0},
        # 작은 섹터(시장 VWAP 금액의 1% 남짓)의 큰 오차(50%) — 분모 게이트(2%) 밖이라 최대값이 아니다
        {"code": "C", "date": asof, "market": "거래소", "sector": "보험", "close": 100.0,
         "individual": -1, "foreign_": 0, "institution": 1, "etc_corp": 0},
    ])
    # A: vwap 110(종가 −9%), B: vwap 90.9(종가 +10%) — 시장에서는 거의 상쇄, 섹터에서는 그대로
    bars = [("A", asof, 10000, 1.1, "kiwoom"), ("B", asof, 11000, 1.0, "kiwoom"),
            ("C", asof, 100, 0.02, "kiwoom")]
    got = sf.approx_gap(_ctx_db([asof], [], bars), df, asof, ["거래소"])["거래소"]
    assert got["pct"] < 1.0
    assert got["sector_max"]["sector"] == "화학" and got["sector_max"]["pct"] == pytest.approx(10.0, abs=0.01)


def test_market_context_puts_index_and_gap_inside_today(P, monkeypatch):
    """KRX 지수·근사 오차는 `today` 안에 실린다 — 화면은 numbers.html 의 D 하나만 읽고 build 가 today 를 통째로 건넨다."""
    Q = json.loads(json.dumps(P))
    monkeypatch.setattr(sf, "krx_index_block", lambda con, dates, mk: {"거래소": {"ret": {"1": 1.0}}})
    monkeypatch.setattr(sf, "approx_gap", lambda con, df, asof, mk: {"코스닥": {"pct": 6.0}})
    sf.market_context(None, None, Q)
    assert Q["today"]["krx_index"] == {"거래소": {"ret": {"1": 1.0}}}
    assert Q["today"]["market"]["코스닥"]["approx_gap"] == {"pct": 6.0}
    assert Q["today"]["market"]["거래소"]["approx_gap"] is None
    assert Q["today"]["approx_gap_warn"] == sf.APPROX_GAP_WARN == 5.0


def test_today_rows_carry_all_four_actors_for_the_one_day_window(P):
    """1일 창은 주체를 4개 다 돈다 — `today` 섹터 칸에 기타법인까지 있어야 한다."""
    for rows in P["today"]["sectors"].values():
        for row in rows.values():
            assert {"inst", "forgn", "indiv", "etc"} <= set(row)
