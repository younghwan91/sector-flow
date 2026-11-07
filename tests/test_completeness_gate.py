"""``scripts/completeness_gate.py`` — 수집이 덜 끝난 날을 리포트로 내보내지 않는가.

2026-10-27 사고: daily_collection 이 18:47 까지 도는데 리포트는 18:10 에 만들어졌다. 코스닥
거래대금이 다음 날 리포트의 1.9%, 코스닥 시총 0 인 채로 ``latest`` 가 됐고, 다음 실행은 기준일이
달라서 그날을 다시 만들지 않았다. 판정은 순수 함수와 sqlite 경로로 본다 — DB·네트워크 불요.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DAYS = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02"]
KOSPI, KOSDAQ = "거래소", "코스닥"


def _load():
    spec = importlib.util.spec_from_file_location(
        "completeness_gate", REPO / "scripts" / "completeness_gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()


def _counts(today_kospi=(800, 800, 1000.0), today_kosdaq=(1800, 1800, 500.0)):
    """시장 → 날짜 → (수급 종목수, 시총 있는 종목수, 시총 합). 앞 5일은 온전하다."""
    out = {KOSPI: {}, KOSDAQ: {}}
    for d in DAYS[:-1]:
        out[KOSPI][d] = (800, 800, 1000.0)
        out[KOSDAQ][d] = (1800, 1800, 500.0)
    out[KOSPI][DAYS[-1]] = today_kospi
    out[KOSDAQ][DAYS[-1]] = today_kosdaq
    return out


def test_complete_day_passes():
    ok, lines = G.evaluate(_counts(), DAYS[-1])
    assert ok, lines


def test_real_incident_shape_fails():
    """10-27 모양 — 코스닥이 거의 비고 시총이 0. 시장 이름이 사유에 나와야 한다."""
    ok, lines = G.evaluate(_counts(today_kospi=(780, 780, 900.0),
                                   today_kosdaq=(34, 0, 0.0)), DAYS[-1])
    assert not ok
    text = "\n".join(lines)
    assert KOSDAQ in text and "FAIL" in text


@pytest.mark.parametrize("n", [1, 900, 1700])
def test_shrunk_market_fails_on_name_count(n):
    # 시총 종목수는 온전하게 둔다 — 그쪽 판정이 대신 잡으면 종목수 판정이 죽어도 이 검사가 초록이다.
    ok, lines = G.evaluate(_counts(today_kosdaq=(n, 1800, 500.0)), DAYS[-1])
    assert not ok
    assert any("수급 종목수" in x for x in lines)


def test_threshold_boundary():
    """문턱은 포함이다 — 기준의 97% 는 통과, 그 아래는 실패."""
    at = int(1800 * G.MIN_COVERAGE)
    assert G.evaluate(_counts(today_kosdaq=(at, at, 500.0)), DAYS[-1])[0]
    below = at - 1
    assert not G.evaluate(_counts(today_kosdaq=(below, at, 500.0)), DAYS[-1])[0]


def test_real_low_day_still_passes():
    """실측 최저(2026-11-02 거래소 796/803 = 99.1%)는 막지 않는다."""
    assert G.evaluate(_counts(today_kospi=(793, 795, 1000.0)), DAYS[-1])[0]


def test_bars_missing_fails_even_when_names_are_all_there():
    """수급은 다 들어왔는데 일봉이 아직 — 시총 분모가 비면 비중 변화가 거짓이 된다."""
    ok, lines = G.evaluate(_counts(today_kosdaq=(1800, 900, 250.0)), DAYS[-1])
    assert not ok
    assert any("시총" in x for x in lines)


def test_zero_market_cap_fails_regardless_of_count():
    ok, lines = G.evaluate(_counts(today_kosdaq=(1800, 1800, 0.0)), DAYS[-1])
    assert not ok
    assert any("시총" in x for x in lines)


def test_market_absent_today_fails_explicitly():
    """그날 한 시장이 통째로 없다 — 건너뛰는 게 아니라 실패다."""
    c = _counts()
    del c[KOSDAQ][DAYS[-1]]
    ok, lines = G.evaluate(c, DAYS[-1])
    assert not ok
    assert any(KOSDAQ in x for x in lines)


def test_baseline_is_not_fooled_by_a_partial_previous_day():
    """전 거래일 하나만 기준이면 이틀 연속 부분 수집이 서로를 통과시킨다."""
    c = _counts(today_kosdaq=(34, 34, 5.0))
    c[KOSDAQ][DAYS[-2]] = (34, 34, 5.0)
    assert not G.evaluate(c, DAYS[-1])[0]


def test_no_history_cannot_be_compared():
    c = {KOSPI: {DAYS[-1]: (800, 800, 1000.0)}, KOSDAQ: {DAYS[-1]: (1800, 1800, 500.0)}}
    ok, lines = G.evaluate(c, DAYS[-1])
    assert not ok
    assert any("비교" in x for x in lines)


# ── DB 경로: 리포트와 같은 유니버스(키움 수급 ∩ stocks)를 읽는가 ──────────────────

def _db(path: Path, today_kosdaq: int, source: str = "kiwoom", adj_missing: bool = False) -> str:
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE stocks(code TEXT PRIMARY KEY, name TEXT, market TEXT, sector TEXT);
      CREATE TABLE supply_demand(code TEXT, date TEXT, close REAL, institution REAL, source TEXT);
      CREATE TABLE daily_bars(code TEXT, date TEXT, close REAL);
      CREATE TABLE shares_outstanding_history(code TEXT, date TEXT, shares_outstanding INTEGER);
      CREATE TABLE daily_bars_raw(code TEXT, date TEXT, close REAL);
      CREATE TABLE daily_bars_adjusted(code TEXT, date TEXT, close REAL);
      CREATE TABLE corporate_actions(code TEXT, date TEXT, upd_stkpc_tp TEXT, upd_rt REAL);
    """)
    for i in range(100):
        code = f"{i:06d}"
        market = KOSPI if i < 50 else KOSDAQ
        con.execute("INSERT INTO stocks VALUES(?,?,?,?)", (code, code, market, "S"))
        con.execute("INSERT INTO shares_outstanding_history VALUES(?,?,?)",
                    (code, "2020-01-01", 1000))
        for d in DAYS:
            if d == DAYS[-1] and market == KOSDAQ and i - 50 >= today_kosdaq:
                continue
            con.execute("INSERT INTO supply_demand VALUES(?,?,?,?,?)",
                        (code, d, 100.0, 1.0, source if d == DAYS[-1] else "kiwoom"))
            con.execute("INSERT INTO daily_bars VALUES(?,?,?)", (code, d, 100.0))
            con.execute("INSERT INTO daily_bars_raw VALUES(?,?,?)", (code, d, 100.0))
            if not (d == DAYS[-1] and adj_missing):
                con.execute("INSERT INTO daily_bars_adjusted VALUES(?,?,?)", (code, d, 100.0))
    con.commit()
    con.close()
    return str(path)


def test_read_counts_and_verdict_on_sqlite(tmp_path):
    con = sqlite3.connect(_db(tmp_path / "a.db", today_kosdaq=50))
    counts = G.read_counts(con, DAYS[-1])
    con.close()
    assert counts[KOSDAQ][DAYS[-1]][:2] == (50, 50)
    assert G.evaluate(counts, DAYS[-1])[0]


def test_stray_row_day_fails_on_sqlite(tmp_path):
    """행 하나짜리 날(옛 catchup 이 만들던 모양)이 기준일이 되면 막는다."""
    con = sqlite3.connect(_db(tmp_path / "a.db", today_kosdaq=1))
    counts = G.read_counts(con, DAYS[-1])
    con.close()
    assert counts[KOSDAQ][DAYS[-1]][0] == 1
    assert not G.evaluate(counts, DAYS[-1])[0]


def test_non_kiwoom_rows_do_not_count(tmp_path):
    """리포트는 키움 행만 읽는다. 네이버 행으로 채운 날은 그 유니버스로 비어 있다."""
    con = sqlite3.connect(_db(tmp_path / "a.db", today_kosdaq=50, source="naver"))
    counts = G.read_counts(con, DAYS[-1])
    con.close()
    assert not G.evaluate(counts, DAYS[-1])[0]


def test_null_supply_rows_do_not_count(tmp_path):
    """close·institution 이 NULL 인 행은 그날 수급이 아직 없는 행이다 — 세면 빈 칸이 찬 것으로 보인다."""
    path = _db(tmp_path / "a.db", today_kosdaq=50)
    con = sqlite3.connect(path)
    con.execute("UPDATE supply_demand SET institution = NULL WHERE date = ? AND code = '000060'",
                (DAYS[-1],))
    con.execute("UPDATE supply_demand SET close = NULL WHERE date = ? AND code = '000061'",
                (DAYS[-1],))
    counts = G.read_counts(con, DAYS[-1])
    con.close()
    assert counts[KOSDAQ][DAYS[-1]][0] == 48
    assert counts[KOSDAQ][DAYS[-2]][0] == 50


def test_read_counts_reads_only_the_recent_days_up_to_asof(tmp_path):
    con = sqlite3.connect(_db(tmp_path / "a.db", today_kosdaq=50))
    con.execute("INSERT INTO supply_demand VALUES('000000', '2026-01-02', 100.0, 1.0, 'kiwoom')")
    assert sorted(G.read_counts(con, DAYS[-1])[KOSPI]) == DAYS
    assert sorted(G.read_counts(con, DAYS[-2])[KOSPI]) == ["2026-01-02", *DAYS[:-1]]
    con.close()


def test_market_with_no_rows_stays_in_the_counts_as_an_empty_market(tmp_path):
    """그 시장 종목이 하루도 안 들어온 경우 — 키가 빠지면 evaluate 가 그 시장을 안 본다."""
    path = _db(tmp_path / "a.db", today_kosdaq=50)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO stocks VALUES('999999', 'x', '넥스트', 'S')")
    counts = G.read_counts(con, DAYS[-1])
    con.close()
    assert counts["넥스트"] == {}
    assert not G.evaluate(counts, DAYS[-1])[0]


def test_main_exit_codes(tmp_path, capsys):
    db = _db(tmp_path / "a.db", today_kosdaq=50)
    assert G.main(["--db", db, "--asof", DAYS[-1]]) == 0
    assert "PASS" in capsys.readouterr().out
    db2 = _db(tmp_path / "b.db", today_kosdaq=3)
    assert G.main(["--db", db2, "--asof", DAYS[-1]]) == G.EX_INCOMPLETE
    assert "FAIL" in capsys.readouterr().out


def test_missing_adjusted_closes_block_the_report(tmp_path):
    """섹터 수익률은 조정 종가에서 나온다. 조정 표가 아직 안 돈 저녁엔 그날 수익률이 비어
    섹터 수익률이 남은 종목만으로 계산된다 — 만들지 말고 다음 크론을 기다린다."""
    con = sqlite3.connect(_db(tmp_path / "a.db", today_kosdaq=50, adj_missing=True))
    ok, lines = G.evaluate(G.read_counts(con, DAYS[-1]), DAYS[-1])
    assert not ok
    assert any("조정 종가" in x for x in lines)
