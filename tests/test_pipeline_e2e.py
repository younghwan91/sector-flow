"""파이프라인 끝에서 끝까지 — 합성 sqlite → ``sector_flow`` → ``sector_numbers`` → ``verify_report --db-check``.

검증기의 수치 층(A·A3·F)이 **producer 의 버그를 정말 잡는지** 아무도 보인 적이 없었다. 검사는
있었지만 실데이터로만 돌아서, 초록이 "버그가 없다" 인지 "검사가 아무것도 안 봤다" 인지 갈리지
않았다(F 층 비교는 0쌍이어도 ``0/0 불일치`` 로 초록이었다). 여기서는 씨앗을 고정한 작은 DB 를 손으로
만들고 일일 배치와 **같은 명령**으로 리포트를 지은 뒤, DB 대조까지 포함한 검증이 실패 0 인지 본다.
``tests/mutations.toml`` 의 producer 변이(수량×종가 → 수량, 시총가중 → 등가중, 주식수 이벤트 배율
누락, ẍ 부호 등)가 이 파일의 검사를 각자 **이름이 정해진 한 검사**로 빨갛게 만든다.

DB 에 넣은 함정 — 실데이터에서 실제로 사고를 낸 모양들이다:

* **10:1 병합 + 늦은 주식수 스냅샷**(078130 모양): 병합일 뒤 보름 동안 주식수 행이 병합 전 수다.
  ``corporate_actions`` 의 배율을 안 쓰면 그 보름의 시총·수익률 가중이 10배가 된다.
* **조정 종가가 빠진 날** — 그날 수익률은 결측이고 가중에서도 빠져야 한다.
* **거래정지 구간**, **한 시장에만 있는 섹터**(다른 시장 칸은 시총 0 → 가속도 결측),
  **폐지 종목**(네이버 소스, ``stocks`` 밖), **얇은 섹터**(10종목 미만).
* 4주체 순매수 수량의 합은 정확히 0 이다(기타법인 포함).
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from sectorflow.storage import connect

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

#: 거래일 수(DB)와 리포트 구간. 첫날의 전일 종가가 있어야 첫날 수익률이 나오므로 DB 가 더 길다.
DB_DAYS, REPORT_DAYS = 135, 130
#: (섹터, 거래소 종목 수, 코스닥 종목 수). 10 미만은 얇은 섹터, 음식료는 거래소에만 있다.
SECTORS = (("전기/전자", 10, 10), ("금융", 10, 10), ("화학", 10, 10), ("기계", 10, 10),
           ("의약품", 10, 10), ("운송", 10, 10),
           ("건설", 2, 2), ("유통", 2, 2), ("섬유", 2, 2), ("음식료", 3, 0))
MERGE_CODE, MERGE_DAY, SNAPSHOT_DAY = "000001", 100, 115   # 병합일·늦은 스냅샷일(거래일 순번)
HOLE_CODE, HOLE_DAY = "000002", 110                        # 조정 종가가 빠진 날
HALT_CODE, HALT_DAYS = "060001", range(50, 56)             # 거래정지(얇은 섹터)
#: 씨앗 — 관문 통과 섹터가 여러 블록에 나오고 종합에 "판정된 창 일부만 통과" 한 줄이 생기는 것으로
#: 골랐다(`test_fixture_plants_what_it_claims` 가 그 성질을 지킨다).
SEED = 6


def _mod(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def build_db(path: Path, seed: int = SEED) -> dict:
    """씨앗 고정 합성 DB. 무엇을 어디에 심었는지 돌려준다(픽스처가 헛돌지 않는지 보려고)."""
    rnd = random.Random(seed)
    con = connect(str(path))
    # 실 스키마(quant-airflow)에는 있고 테스트용 SCHEMA 에는 없는 것 — 검증기 B·F 층이 읽는다.
    con.execute("ALTER TABLE daily_bars_adjusted ADD COLUMN high REAL")
    con.execute("ALTER TABLE daily_bars_adjusted ADD COLUMN low REAL")
    con.execute("CREATE TABLE IF NOT EXISTS delisted_stocks (code TEXT PRIMARY KEY, name TEXT, market TEXT)")
    days = [d.date().isoformat() for d in pd.bdate_range("2026-03-02", periods=DB_DAYS)]

    stocks, sd, bars, raw, adj, shares, si = [], [], [], [], [], [], []
    for j, (sec, n_k, n_q) in enumerate(SECTORS):
        for mk, cnt in (("거래소", n_k), ("코스닥", n_q)):
            for i in range(cnt):
                code = f"{j:02d}{0 if mk == '거래소' else 5}{i + 1:03d}"   # 6자리(실코드 폭)
                stocks.append((code, f"{sec}{mk[0]}{i}", mk, sec, "stock"))
        # 업종지수 — 이름이 섹터명이고 코드가 둘이면 작은 쪽이 거래소다.
        for c, lvl in ((f"I{j}1", 1000.0), (f"I{j}2", 800.0)):
            for d in days:
                lvl *= 1 + rnd.gauss(0, 0.01)
                si.append((c, sec, d, int(lvl)))

    sector_beta = {sec: rnd.gauss(0, 0.004) for sec, *_ in SECTORS}
    # 섹터마다 기관 순매수 쏠림 — 무작위만으로는 가속도 a>0 인 섹터가 드물어 관문이 비었다.
    inst_tilt = {sec: rnd.choice((-0.04, 0.0, 0.03, 0.06)) for sec, *_ in SECTORS}
    for code, _nm, mk, sec, _k in stocks:
        px = rnd.uniform(5_000, 120_000)
        base_sh = rnd.randint(10, 300) * 1_000_000
        shares.append((code, "2025-12-31", base_sh))
        merged = code == MERGE_CODE
        if merged:
            shares.append((code, days[SNAPSHOT_DAY], base_sh // 10))
        for t, d in enumerate(days):
            px *= 1 + sector_beta[sec] + rnd.gauss(0, 0.02)
            mult = 10.0 if merged and t >= MERGE_DAY else 1.0        # 10:1 병합 → 원 종가 ×10
            close = max(100, int(px * mult))
            adj_close = close / mult * 10.0 if merged else float(close)   # 백조정: 병합 전이 ×10
            if code == HALT_CODE and t in HALT_DAYS:
                continue
            qty = rnd.randint(20_000, 2_000_000) // (10 if merged and t >= MERGE_DAY else 1)
            inst = rnd.randint(-qty // 5, qty // 5) + int(inst_tilt[sec] * qty)
            forgn = rnd.randint(-qty // 5, qty // 5)
            etc = rnd.randint(-qty // 20, qty // 20)
            indiv = -(inst + forgn + etc)
            parts = [rnd.random() for _ in range(6)]
            det = [int(inst * p / sum(parts)) for p in parts]
            det[0] += inst - sum(det)                                # 세부 합 = 기관
            sd.append((code, d, close, rnd.randint(-500, 500), qty, indiv, forgn, inst,
                       *det, 0, etc, "kiwoom"))
            tv = round(qty * close * (1 + rnd.gauss(0, 0.03)) / 1e6)  # 백만원 — VWAP 근사 오차
            bars.append((code, d, close, close, close, close, qty, tv, "kiwoom"))
            raw.append((code, d, close))
            if not (code == HOLE_CODE and t == HOLE_DAY):
                adj.append((code, d, adj_close, adj_close * 1.01, adj_close * 0.99))
    for k, (code, mk) in enumerate((("990001", "유가증권"), ("990002", "코스닥"))):
        con.execute("INSERT INTO delisted_stocks VALUES (?,?,?)", (code, f"폐지{k}", mk))
        for d in days[:40]:
            q = rnd.randint(10_000, 100_000)
            sd.append((code, d, 3_000, 0, q, None, rnd.randint(-q // 5, q // 5),
                       rnd.randint(-q // 5, q // 5), *([None] * 7), None, "naver"))

    con.executemany("INSERT INTO stocks VALUES (?,?,?,?,?)", stocks)
    con.executemany(
        "INSERT INTO supply_demand(code,date,close,flu_rt,acc_trde_qty,individual,foreign_,"
        "institution,fnnc_invt,insrnc,invtrt,bank,penfnd_etc,samo_fund,natn,etc_corp,source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", sd)
    con.executemany("INSERT INTO daily_bars(code,date,open,high,low,close,volume,trade_value,source)"
                    " VALUES (?,?,?,?,?,?,?,?,?)", bars)
    con.executemany("INSERT INTO daily_bars_raw VALUES (?,?,?)", raw)
    con.executemany("INSERT INTO daily_bars_adjusted(code,date,close,high,low) VALUES (?,?,?,?,?)", adj)
    con.executemany("INSERT INTO shares_outstanding_history VALUES (?,?,?)", shares)
    con.execute("INSERT INTO corporate_actions VALUES (?,?,'16',900.0)", (MERGE_CODE, days[MERGE_DAY]))
    con.executemany("INSERT INTO sector_index(code,name,date,close) VALUES (?,?,?,?)", si)
    con.commit()
    con.close()
    return {"days": days, "report_from": days[DB_DAYS - REPORT_DAYS]}


def _run(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    env.pop("KR_QUANT_DB", None)                       # 실 DB 를 절대 안 보게
    return subprocess.run([sys.executable, *args], cwd=str(REPO), env=env,
                          capture_output=True, text=True, timeout=600)


def run_pipeline(root: Path) -> dict:
    """``daily_report.sh`` 와 같은 생산 단계(페이로드 → 뷰어 → 수치표). 검증은 :func:`verified` 가 따로 돈다."""
    db = root / "fixture.db"
    meta = build_db(db)
    out = root / "2026-09-07.building.4242"           # 배치처럼 옆 폴더에 짓는다
    out.mkdir()
    for args in (("sector_flow.py", "--db", str(db), "--days", str(REPORT_DAYS),
                  "--json", str(out / "payload.json")),
                 ("sector_flow.py", "--from-json", str(out / "payload.json"),
                  "--html", str(out / "viewer.html")),
                 ("sector_numbers.py", "--payload", str(out / "payload.json"),
                  "--html", str(out / "numbers.html"))):
        p = _run(str(SCRIPTS / args[0]), *args[1:])
        assert p.returncode == 0, f"{args[0]} 실패\n{p.stdout[-2000:]}\n{p.stderr[-3000:]}"
    html = (out / "numbers.html").read_text("utf-8")
    D = json.loads(re.search(r"const D = (\{.*?\});\n", html, re.S).group(1))
    P = json.loads((out / "payload.json").read_text("utf-8"))
    return {"D": D, "P": P, "db": db, "dir": out, "_verified": {}, **meta}


def verified(report: dict, *layers: str) -> dict:
    """``verify_report --db-check`` 결과(rc·out·marks). 층을 주면 그 층만, 안 주면 배치와 같은 전 층.

    전 층은 ~20초다(대부분 H 층 — 원장 화면 수백 장). 주입 검증(`verify_merge.py`)은 변이마다 이
    파일의 검사를 새 프로세스로 돌리므로, 특정 층의 표시만 보는 검사까지 전 층을 기다리면 변이 16개가
    e2e 에만 10분을 썼다. 층마다 독립이라(공유 상태는 ``chk`` 의 집계뿐) 부분 실행의 표시는 전 층
    실행의 같은 표시와 같다 — ``test_layer_subset_marks_equal_the_full_run`` 이 그걸 지킨다.
    같은 모듈 안에서는 층 조합마다 한 번만 돈다.
    """
    key = tuple(sorted(layers))
    if key not in report["_verified"]:
        extra = ("--layers", ",".join(key)) if key else ()
        v = _run(str(SCRIPTS / "verify_report.py"), "--dir", str(report["dir"]), "--db-check",
                 "--db", str(report["db"]), *extra)
        marks: dict[str, list[str]] = {}
        for ln in v.stdout.splitlines():
            m = re.match(r"\s*\[(OK|FAIL|CLAIM)\s*\] (\S+) · (.+?)(?:   |$)", ln)
            if m:
                marks.setdefault(f"{m.group(2)}/{m.group(3).strip()}", []).append(m.group(1))
        report["_verified"][key] = {"rc": v.returncode, "out": v.stdout + v.stderr, "marks": marks}
    return report["_verified"][key]


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    return run_pipeline(tmp_path_factory.mktemp("e2e"))


def _ok(report, *names: str) -> None:
    """이름이 정해진 검사가 **돌았고** 전부 OK 인가. 없는 이름은 실패다(검사가 사라져도 초록이면 안 된다).

    이름의 층(``F/…`` 의 ``F``)만 돌린다 — 층을 안 돌려 이름이 없으면 그것도 실패다."""
    v = verified(report, *sorted({n.split("/", 1)[0] for n in names}))
    for name in names:
        got = v["marks"].get(name)
        assert got, f"검사 '{name}' 가 출력에 없다\n" + v["out"][-3000:]
        assert set(got) == {"OK"}, f"검사 '{name}' = {got}\n" + "\n".join(
            ln for ln in v["out"].splitlines() if name.split("/", 1)[1] in ln)


# ── 픽스처가 헛돌지 않는가 ───────────────────────────────────────────────────

def test_fixture_plants_what_it_claims(report):
    P, D = report["P"], report["D"]
    assert P["markets"] == ["거래소", "코스닥"] and len(P["dates"]) == REPORT_DAYS
    assert P["dates"][0] < report["days"][MERGE_DAY] < report["days"][SNAPSHOT_DAY] <= P["dates"][-1]
    # 조정 종가가 빠진 날이 리포트 구간 안이다.
    assert P["dates"][0] < report["days"][HOLE_DAY] < P["dates"][-1]
    # 한 시장에만 있는 섹터 → 다른 시장 칸은 가속도 결측.
    row = next(r for r in D["blocks"]["20|코스닥"]["rows"] if r["sector"] == "음식료")
    assert row["accel"] is None
    # 관문 통과가 둘 이상인 블록(G 순위가 실제로 매겨진다)이 있고, 종합에 "판정된 창 일부만
    # 통과"(`*` 없음)와 "판정된 창 전부 통과"(`*`) 줄이 둘 다 있다.
    assert any(sum(r.get("G") is not None for r in B["rows"]) >= 2 for B in D["blocks"].values())
    rows = [r for C in D["combined"].values() for r in C["rows"]]
    assert any(0 < r["pass_n"] < r["seen"] for r in rows)
    assert any(r["G_pass"] for r in rows)
    assert all(r["xddot"] is not None for r in D["blocks"]["20|전체"]["rows"] if not r["thin"])
    # 구간 20 은 3 의 배수가 아니다 — ẍ 조각 길이 문제가 여기서 드러난다.
    assert 20 % 3


def test_pipeline_report_verifies_with_no_failures(report):
    """DB 대조(F)까지 포함한 전 층이 실패 0. 문서 주장 어긋남(CLAIM)은 실패가 아니다."""
    v = verified(report)
    fails = sorted(k for k, x in v["marks"].items() if "FAIL" in x)
    assert v["rc"] == 0 and not fails, f"{fails}\n" + v["out"][-4000:]
    assert sum(len(x) for x in v["marks"].values()) > 150
    assert "부분 검증" not in v["out"]
    # 검증 대상 줄은 옮겨 갈 이름이다(임시 `.building.NNN` 가 아니다).
    assert "검증 대상: " + str(report["dir"]).rsplit(".building.", 1)[0] + "\n" in v["out"]


def test_layer_subset_marks_equal_the_full_run(report):
    """빠른 부분 실행(``--layers``)이 전 층 실행과 **같은 표시**를 내는가 — 다르면 위 검사들이
    배치가 실제로 보는 것과 다른 것을 본다."""
    full = verified(report)["marks"]
    for layers in (("A", "A3"), ("F",), ("A",), ("A3",)):
        part = verified(report, *layers)
        assert "부분 검증" in part["out"]
        assert part["marks"], layers
        assert {k for k in part["marks"] if k.split("/", 1)[0] not in layers} == set(), layers
        assert part["marks"] == {k: x for k, x in full.items() if k.split("/", 1)[0] in layers}, layers


def test_every_layer_main_runs_is_selectable():
    """``--layers`` 이름표(``LAYERS``)와 main 이 실제로 부르는 층이 어긋나면 새 층이 고를 수 없게 된다."""
    import ast
    src = (SCRIPTS / "verify_report.py").read_text("utf-8")
    main = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "main")
    called = {c.args[0].value for c in ast.walk(main) if isinstance(c, ast.Call)
              and getattr(c.func, "id", None) == "go" and isinstance(c.args[0], ast.Constant)}
    assert called == set(_mod("verify_report").LAYERS)


def test_pipeline_db_recount_matches_flows(report):
    """수량 → 금액 환산(× 종가)과 기관 세부를 DB 에서 다시 센 값과 맞댄다."""
    _ok(report, "F/유량 일별 (시장×섹터×5값×거래일)", "F/기관 세부 일별",
        "F/종목 20일 집계", "F/원장 20일 구간 합계 (시장×섹터×주체)")


def test_pipeline_db_recount_matches_returns_and_weights(report):
    """섹터 일별 수익률(전일 시총 가중)과 그 가중합·기간말 시총 — 병합 구간이 들어 있다."""
    _ok(report, "F/섹터 일별 수익률 (전일 시총 가중, 조정 종가 비)", "F/섹터 일별 가중합",
        "F/섹터 시총 (구간말, 원 종가 × 주식수)", "F/종목 구간 수익률 (기하누적)")


def test_pipeline_derived_columns_are_recomputed(report):
    """구간 수익률·가속도·1년%ile·dW/dt·ẋ·ẍ·t 를 페이로드 일별 배열에서 다시 셈한다."""
    _ok(report, *(f"A3/{n}" for n in (
        "임펄스·거래대금 = 일별 합 (4주체·tv)", "가속도 = 임펄스/시총×100 (시총 없으면 결측)",
        "구간 수익률 = 일별 시총가중 수익률의 기하누적",
        "1년[%ile] = 롤링 합의 1년 분포에서 오늘의 중간순위",
        "t = 블록 적합 기울기의 t (소수 2자리)", "dW/dt = (W_뒤 − W_앞)/(뒤 반의 거래일수)",
        "ẋ = −(x₃ − x₁)/2Δ (같은 길이 세 조각)", "ẍ = −(x₃ − 2x₂ + x₁)/Δ² (같은 길이 세 조각)")),
        "A/t 반올림 편차(표시용)")


def test_pipeline_combined_mark_means_every_judged_window(report):
    _ok(report, "A/종합 * = 판정된 모든 창에서 통과 (seen = 판정 창, pass_n = G 창)",
        "A/종합 G = G 가 나온 창의 등가중 평균")


# ── F 층: 비교한 것이 없으면 실패 ──────────────────────────────────────────────

def test_db_layer_fails_when_nothing_was_compared(tmp_path, report):
    """DB 도 비고 페이로드도 0 이면 예전엔 ``0/N 불일치`` 로 초록이었다 — 본 것이 없다."""
    VR = _mod("verify_report")
    P = json.loads(json.dumps(report["P"]))
    for blob in ("flows", "detail"):                    # 시장 → 섹터 → 키 → 일별
        for m in P[blob].values():
            for s in m.values():
                for k in s:
                    s[k] = [0.0] * len(s[k])
    for blob in ("ret", "retw"):                        # 시장 → 섹터 → 일별
        for m in P[blob].values():
            for s in m:
                m[s] = [0.0] * len(m[s])
    P["cap"], P["names"] = {}, {}
    empty = tmp_path / "empty.db"
    connect(str(empty)).execute(
        "CREATE TABLE delisted_stocks (code TEXT, name TEXT, market TEXT)").connection.close()
    VR.FAILS.clear()
    VR.run_layer("F", VR.layer_f, P, str(empty))
    assert "F/유량 일별 (시장×섹터×5값×거래일)" in VR.FAILS, VR.FAILS
    assert "F/섹터 일별 수익률 (전일 시총 가중, 조정 종가 비)" in VR.FAILS, VR.FAILS


# ── B 층: 거래대금 단위 — 같은 정의끼리 ─────────────────────────────────────────

def _units_fails(db: Path) -> list[str]:
    VR = _mod("verify_report")
    VR.FAILS.clear()
    con = connect(str(db))
    VR.check_trade_value_units(con.cursor())
    con.close()
    return [f for f in VR.FAILS if "금액 단위" in f]


def test_trade_value_unit_check_ignores_close_vs_vwap_gaps_but_catches_unit_errors(tmp_path):
    """애프터마켓 통합 뒤 종가 × 수량이 거래대금(VWAP 합)과 종목마다 5% 넘게 벌어지는 날이 늘었다.
    그건 정의 차이지 단위 오류가 아니다 — 그런 날도 조용해야 하고, 단위가 1000배 틀리면 빨개야 한다."""
    db = tmp_path / "u.db"
    meta = build_db(db)
    con = connect(str(db))
    last = meta["days"][-1]
    # 기준일 종목의 40% 를 ±12% 어긋나게 — 옛 검사(5% 이내 > 90%)는 여기서 빨갰다.
    rows = con.execute("SELECT code, trade_value FROM daily_bars WHERE date=?", (last,)).fetchall()
    for i, (code, tv) in enumerate(rows):
        if i % 5 < 2:
            con.execute("UPDATE daily_bars SET trade_value=? WHERE code=? AND date=?",
                        (round(tv * (1.12 if i % 2 else 0.88)), code, last))
    con.commit()
    assert _units_fails(db) == []
    con.execute("UPDATE daily_bars SET trade_value = trade_value * 1000 WHERE date=?", (last,))
    con.commit()
    con.close()
    assert _units_fails(db), "거래대금이 1000배(원 단위)인데 단위 검사가 조용하다"
