"""지난 기준일 재구성(``sector_flow.py --asof``·``KR_QUANT_ASOF``·``rebuild_history.py``).

핵심 계약: **기준일 뒤의 행이 하나도 새어 들지 않는다.** 기준일까지만 든 DB 로 만든 페이로드와, 그 뒤의 행이
모든 표(수급·조정 종가·원 종가·업종지수·주식수·수정주가 이벤트)에 더 든 DB 로 ``--asof`` 를 주고 만든 페이로드가
바이트까지 같아야 한다. 뒤의 병합은 조정 종가를 과거까지 백조정하는 실제 DB 모양 그대로 넣는다.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from sectorflow.storage import connect

REPO = Path(__file__).resolve().parent.parent
DAYS = [d.date().isoformat() for d in pd.bdate_range("2026-01-05", periods=40)]
ASOF = DAYS[29]
SECTORS = ("반도체", "금융")


def _db(path: Path, upto: str, seed: int = 7) -> str:
    """종목 12개 × 두 시장 × 두 섹터. ``upto`` 이하 날짜만 넣는다. 난수는 날짜와 상관없이 같은 순서로 뽑는다."""
    rng = np.random.default_rng(seed)
    con = connect(str(path))
    codes = [f"{i:06d}" for i in range(12)]
    con.executemany("INSERT INTO stocks(code,name,market,sector,kind) VALUES (?,?,?,?,?)",
                    [(c, f"종목{c}", "거래소" if i % 2 else "코스닥", SECTORS[(i // 2) % 2], "stock")
                     for i, c in enumerate(codes)])
    split_code, split_day = codes[3], DAYS[34]          # 기준일 **뒤** 1:5 분할 — 조정 종가가 과거까지 ÷5
    for i, c in enumerate(codes):
        px = float(rng.integers(5_000, 50_000))
        for j, d in enumerate(DAYS):
            px *= 1 + rng.normal(0, 0.02)
            raw = round(px / 5 if (c == split_code and d >= split_day) else px)
            adj = px / 5 if c == split_code else px    # 백조정: 분할 전 날도 ÷5
            flows = [int(v) for v in rng.integers(-5_000, 5_000, size=11)]
            if d > upto:
                continue
            con.execute(
                "INSERT INTO supply_demand(code,date,close,flu_rt,acc_trde_qty,individual,foreign_,institution,"
                "fnnc_invt,insrnc,invtrt,bank,penfnd_etc,samo_fund,natn,etc_corp,source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'kiwoom')",
                (c, d, raw, 0.0, 50_000 + j, *flows))
            con.execute("INSERT INTO daily_bars(code,date,open,high,low,close,volume,trade_value) "
                        "VALUES (?,?,?,?,?,?,?,?)", (c, d, raw, raw, raw, raw, 1, round((50_000 + j) * raw / 1e6)))
            con.execute("INSERT INTO daily_bars_raw VALUES (?,?,?)", (c, d, raw))
            con.execute("INSERT INTO daily_bars_adjusted VALUES (?,?,?)", (c, d, adj))
        for d, n in ((DAYS[0], 1_000_000 + i), (DAYS[33], 2_000_000 + i)):   # 기준일 뒤 스냅샷 하나
            if d <= upto:
                con.execute("INSERT INTO shares_outstanding_history VALUES (?,?,?)", (c, d, n))
    if split_day <= upto:
        con.execute("INSERT INTO corporate_actions VALUES (?,?,?,?)", (split_code, split_day, "8", -80.0))
    for k, (name, codes_) in enumerate((("반도체", ("0013", "2013")), ("금융", ("0021", "2021")))):
        lvl = 1000.0 + k
        for d in DAYS:
            lvl *= 1 + rng.normal(0, 0.01)
            if d > upto:
                continue
            for code in codes_:
                con.execute("INSERT INTO sector_index(code,name,date,close) VALUES (?,?,?,?)",
                            (code, name, d, round(lvl)))
    con.commit()
    con.close()
    return str(path)


def _payload(tmp_path: Path, db: str, tag: str, *args: str) -> dict:
    out = tmp_path / f"{tag}.json"
    p = subprocess.run([sys.executable, str(REPO / "scripts" / "sector_flow.py"), "--db", db, "--days", "20",
                        "--json", str(out), *args], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(out.read_text("utf-8"))


def test_no_row_after_asof_leaks_into_a_rebuilt_payload(tmp_path):
    """그때 DB(기준일까지)로 만든 것 == 지금 DB(뒤의 행·분할·스냅샷까지)에서 --asof 로 만든 것."""
    then = _db(tmp_path / "then.db", upto=ASOF)
    now = _db(tmp_path / "now.db", upto=DAYS[-1])
    want = _payload(tmp_path, then, "then")
    got = _payload(tmp_path, now, "now", "--asof", ASOF)
    assert got["dates"][-1] == ASOF and want["dates"] == got["dates"]
    assert json.dumps(got, sort_keys=True) == json.dumps(want, sort_keys=True), \
        "기준일 뒤의 행이 재구성 페이로드에 섞였다"
    # 대조군: --asof 없이 지금 DB 로 만들면 정말 달라진다(위 비교가 아무것도 안 보는 게 아니다).
    assert _payload(tmp_path, now, "latest")["dates"][-1] == DAYS[-1]


def test_asof_on_a_holiday_refuses_instead_of_using_the_day_before(tmp_path):
    db = _db(tmp_path / "now.db", upto=DAYS[-1])
    p = subprocess.run([sys.executable, str(REPO / "scripts" / "sector_flow.py"), "--db", db, "--days", "20",
                        "--json", str(tmp_path / "x.json"), "--asof", "2026-01-10"],   # 토요일
                       capture_output=True, text=True, timeout=120)
    assert p.returncode != 0 and "거래일이 아니다" in p.stdout + p.stderr
    assert not (tmp_path / "x.json").exists()


def test_rebuilt_flag_marks_payload_and_skips_the_provisional_comparison(tmp_path):
    db = _db(tmp_path / "now.db", upto=DAYS[-1])
    P = _payload(tmp_path, db, "r", "--asof", ASOF, "--final", "--rebuilt")
    assert P["rebuilt"] is True and P["final"] is True
    assert P["final_diff"] is None and "재구성" in P["final_diff_reason"]
    assert "rebuilt" not in _payload(tmp_path, db, "plain", "--asof", ASOF)


# ── rebuild_history.py — 오래된 날부터, 주말은 안 부르고, 휴장은 요약에 남긴다 ─────────────────────

FAKE_DAILY = r"""#!/bin/sh
echo "$KR_QUANT_ASOF|${KR_QUANT_REBUILD_ORIGIN:-}|${KR_QUANT_FORCE:-}" >> "$CALLS"
case "$KR_QUANT_ASOF" in *-09) exit 3 ;; esac
d="$KR_QUANT_REPORTS/$KR_QUANT_ASOF"; mkdir -p "$d"
: > "$d/numbers.html"; echo "원본은 x" > "$d/REBUILT.txt"
if [ "$KR_QUANT_ASOF" = "2026-10-08" ]; then
  printf '검사 10건 · 실패 1건\n실패 목록: T:직전 대비\n' > "$d/VERIFY.txt"; cp "$d/VERIFY.txt" "$d/VERIFY_FAILED.txt"; exit 1
fi
printf '검사 10건 · 실패 0건\n전부 통과\n' > "$d/VERIFY.txt"
"""


def test_rebuild_history_runs_oldest_first_and_summarises(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(REPO / "scripts" / "rebuild_history.py", repo / "scripts")
    fake = repo / "scripts" / "daily_report.sh"
    fake.write_text(FAKE_DAILY)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    origin = tmp_path / "old"
    (origin / "2026-10-07").mkdir(parents=True)
    calls = tmp_path / "calls"
    root = tmp_path / "root"
    p = subprocess.run([sys.executable, str(repo / "scripts" / "rebuild_history.py"), "--root", str(root),
                        "--from", "2026-10-07", "--to", "2026-10-12", "--origin-root", str(origin),
                        "--origin-label", "reports/.archive/x"],
                       capture_output=True, text=True, timeout=60,
                       env={**os.environ, "CALLS": str(calls), "KR_QUANT_FINAL": "1"})
    assert p.returncode == 1, p.stdout + p.stderr          # 10-08 검증 실패
    seen = [ln.split("|") for ln in calls.read_text().splitlines()]
    assert [s[0] for s in seen] == ["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12"], \
        "오래된 날부터 차례로, 주말(10-10·10-11)은 빼고"
    assert seen[0][1] == "reports/.archive/x/2026-10-07" and seen[1][1] == "", "원본이 있는 날만 원본 위치를 적는다"
    summary = (root / ".rebuild" / "summary.tsv").read_text("utf-8").splitlines()
    by = {r.split("\t")[0]: r.split("\t") for r in summary[1:]}
    assert by["2026-10-07"][2] == "재구성" and "실패 0" in by["2026-10-07"][3]
    assert by["2026-10-08"][2] == "검증 실패" and "직전 대비" in by["2026-10-08"][4]
    assert by["2026-10-09"][2] == "휴장"
    assert (root / ".rebuild" / "2026-10-12.log").is_file()


def test_verify_takes_the_rebuilt_asof_as_the_reference_day(tmp_path):
    """B 층의 "기준일 = DB 최신일" 은 재구성에선 늘 틀린다 — ``--asof`` 를 주면 그날(그 이하 마지막 거래일)과 맞춘다."""
    import importlib.util
    import sqlite3
    spec = importlib.util.spec_from_file_location("verify_report", REPO / "scripts" / "verify_report.py")
    VR = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(VR)

    def db_for(name, upto):
        db = _db(tmp_path / name, upto=upto)
        con = sqlite3.connect(db)                  # B 층의 OHLC 불변식이 읽는 열(운영 표에는 있다)
        con.executescript("ALTER TABLE daily_bars_adjusted ADD COLUMN high REAL;"
                          "ALTER TABLE daily_bars_adjusted ADD COLUMN low REAL;"
                          "UPDATE daily_bars_adjusted SET high=close, low=close;")
        con.close()
        return db

    now, then = db_for("now.db", DAYS[-1]), db_for("then.db", ASOF)
    P = _payload(tmp_path, now, "r", "--asof", ASOF)

    def ref_fails(db, asof):
        VR.FAILS.clear()
        VR.layer_b(P, db, asof)
        return [f for f in VR.FAILS if "기준일" in f or "거래대금" in f]

    assert ref_fails(now, ASOF) == []
    assert ref_fails(now, None), "대조군: --asof 없이는 DB 최신일과 달라 걸려야 한다"
    assert ref_fails(now, DAYS[-1]), "다른 날을 --asof 로 주면 걸려야 한다"
    # 리포트가 지정한 날보다 앞에서 끝났으면(그 사이 거래일이 없어 DB 마지막 날과는 맞아도) 걸려야 한다.
    assert ref_fails(then, DAYS[-1]), "재구성하라고 한 날이 아닌 리포트를 통과시켰다"
