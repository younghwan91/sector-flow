"""가짜 키움 서버로 백필 → 그 sqlite 로 ``scripts/sector_flow.py`` 가 리포트를 만든다(네트워크 없음).

지키는 것: 수집기가 쓰는 표·열·날짜 모양이 리포트 코드가 읽는 것과 맞는다 · 섹터 필터·보통주 필터 ·
업종지수는 고른 섹터와 이름이 같은 코드 + 종합 둘 · 주식수 시드 덕에 과거 시총(=섹터 수익률 가중치)이 비지
않는다 · 원장(collect_runs)에 호출 수가 남는다 · 운영 창에 들어서면 요청 전에 멈추고 --resume 이 이어 받는다 ·
ka10059 가 잘린 것(쪽 상한)과 신규 상장의 짧은 이력을 가른다 · 증분 응답이 범위 밖 기업행동을 지우지 않는다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from sectorflow.collect import kiwoom as K
from sectorflow.collect import run as R

REPO = Path(__file__).resolve().parent.parent
DAYS = [d.strftime("%Y%m%d") for d in pd.bdate_range(end="2026-11-06", periods=45)]
STOCKS = [  # code, name, marketName, upName
    ("000120", "CJ대한통운", "거래소", "운송/창고"),
    ("044490", "태웅로직스", "코스닥", "운송/창고"),
    ("005930", "삼성전자", "거래소", "전기/전자"),      # 다른 섹터 — 빠진다
    ("000125", "가짜우", "거래소", "운송/창고"),         # 우선주 — 빠진다
]


def _price(code: str, i: int) -> int:
    return 1000 + 10 * i + (int(code) % 7)


class FakeKiwoom:
    def __init__(self, sd_days: "dict[str, int] | None" = None, sd_more: bool = False):
        self.seen: list[str] = []
        self.sd_days = sd_days or {}     # 종목별 수급 이력 길이(신규 상장 흉내)
        self.sd_more = sd_more           # 쪽마다 "더 있다"(cont-yn=Y) — 잘림 흉내

    def __call__(self, url, headers, body, timeout):
        if url.endswith(K.TOKEN_PATH):
            return 200, {}, {"token": "t", "expires_in": 86400}
        tr = headers["api-id"]
        self.seen.append(tr)
        newest_first = list(reversed(DAYS))
        if tr == "ka10099":
            mk = {"0": "거래소", "10": "코스닥"}[body["mrkt_tp"]]
            return 200, {}, {"return_code": 0, "list": [
                {"code": c, "name": n, "marketName": m, "upName": u, "kind": "A"}
                for c, n, m, u in STOCKS if m == mk]}
        if tr == "ka20003":
            lst = ({"001": [("001", "종합(KOSPI)"), ("019", "운송/창고"), ("013", "전기/전자")],
                    "101": [("101", "종합(KOSDAQ)"), ("110", "운송/창고")]}[body["inds_cd"]])
            return 200, {}, {"return_code": 0, "all_inds_idex": [{"stk_cd": c, "stk_nm": n} for c, n in lst]}
        if tr == "ka20006":
            return 200, {}, {"return_code": 0, "inds_dt_pole_qry": [
                {"dt": d, "cur_prc": str(250000 + 100 * (len(DAYS) - k)), "open_pric": "1",
                 "high_pric": "1", "low_pric": "1", "trde_qty": "1", "trde_prica": "1"}
                for k, d in enumerate(newest_first)]}
        code = body["stk_cd"]
        if tr == "ka10059":
            n = self.sd_days.get(code, len(DAYS))
            hdr = {"cont-yn": "Y", "next-key": "K"} if self.sd_more else {"cont-yn": "N"}
            return 200, hdr, {"return_code": 0, "stk_invsr_orgn": [
                {"dt": d, "cur_prc": f"-{_price(code, len(DAYS) - k)}", "flu_rt": "+10",
                 "acc_trde_qty": "1000", "ind_invsr": "-300", "frgnr_invsr": "+100", "orgn": "+150",
                 "fnnc_invt": "50", "insrnc": "0", "invtrt": "40", "bank": "0", "penfnd_etc": "60",
                 "samo_fund": "0", "natn": "0", "etc_corp": "+50"} for k, d in enumerate(newest_first[:n])]}
        if tr == "ka10081":
            ev = {DAYS[20]: ("8", "-50.00")} if code == "000120" else {}
            return 200, {}, {"return_code": 0, "stk_dt_pole_chart_qry": [
                {"dt": d, "cur_prc": str(_price(code, len(DAYS) - k)), "open_pric": "1", "high_pric": "1",
                 "low_pric": "1", "trde_qty": "1000", "trde_prica": "1",
                 "upd_stkpc_tp": ev.get(d, ("", ""))[0], "upd_rt": ev.get(d, ("", ""))[1]}
                for k, d in enumerate(newest_first)]}
        if tr == "ka10001":
            return 200, {}, {"return_code": 0, "flo_stk": "50000"}
        raise AssertionError(tr)


def _patch(monkeypatch, fake, guard=None):
    monkeypatch.setattr(R, "_client", lambda args: K.KiwoomRest(
        "k", "s", transport=fake, rate=None, max_calls=args.max_calls, sleep=lambda s: None, guard=guard))


@pytest.fixture
def backfilled(tmp_path, monkeypatch):
    fake = FakeKiwoom()
    _patch(monkeypatch, fake)
    db = tmp_path / "byo.db"
    rc = R.main(["backfill", "--db", str(db), "--sectors", "운송/창고", "--days", "30"])
    return rc, db, fake


def test_backfill_fills_every_table_the_report_reads(backfilled):
    rc, db, fake = backfilled
    assert rc == 0
    import sqlite3
    con = sqlite3.connect(db)
    q = lambda s: con.execute(s).fetchall()  # noqa: E731
    assert sorted(r[0] for r in q("SELECT code FROM stocks")) == ["000120", "044490"]
    assert q("SELECT count(DISTINCT date), min(date), max(date) FROM supply_demand") == [
        (30, f"{DAYS[-30][:4]}-{DAYS[-30][4:6]}-{DAYS[-30][6:]}", "2026-11-06")]
    assert sorted(r[0] for r in q("SELECT DISTINCT code FROM sector_index")) == ["001", "019", "101", "110"]
    for t in ("daily_bars", "daily_bars_adjusted", "daily_bars_raw"):
        assert q(f"SELECT count(*) FROM {t}") == [(60,)], t
    assert q("SELECT count(*) FROM daily_bars_adjusted WHERE high IS NULL OR low IS NULL") == [(0,)]  # verify B
    assert q("SELECT code, date, upd_stkpc_tp FROM corporate_actions") == [
        ("000120", f"{DAYS[20][:4]}-{DAYS[20][4:6]}-{DAYS[20][6:]}", "8")]
    shares = q("SELECT code, shares_outstanding FROM shares_outstanding_history ORDER BY code, date")
    assert shares == [("000120", 25_000_000), ("000120", 50_000_000),     # 분할 전 시드 = 현재 ÷ 2
                      ("044490", 50_000_000), ("044490", 50_000_000)]
    assert R.main(["reseed", "--db", str(db)]) == 0          # 호출 없이 다시 만들어도 같다(멱등)
    assert q("SELECT code, shares_outstanding FROM shares_outstanding_history ORDER BY code, date") == shares
    run = q("SELECT kind, ok, calls FROM collect_runs")
    assert run[0][:2] == ("backfill", 1)
    calls = json.loads(run[0][2])
    assert calls["ka10059"] == 2 and calls["ka10001"] == 2 and calls["ka10081"] == 4
    assert "005930" not in str(fake.seen)


def test_report_generator_reads_the_byo_db(backfilled, tmp_path):
    _, db, _ = backfilled
    out = tmp_path / "p.json"
    subprocess.run([sys.executable, str(REPO / "scripts" / "sector_flow.py"), "--db", str(db),
                    "--days", "25", "--json", str(out), "--reports-root", str(tmp_path / "none")],
                   check=True, cwd=REPO, capture_output=True)
    P = json.loads(out.read_text("utf-8"))
    assert P["n_by_sector"] == {"거래소": {"운송/창고": 1}, "코스닥": {"운송/창고": 1}}
    # 기관 150주 × 그날 종가 / 1억 — 수량 × 종가 환산이 그대로 실린다
    last = _price("000120", len(DAYS))
    assert P["flows"]["거래소"]["운송/창고"]["inst"][-1] == round(150 * last / 1e8, 2)
    # 시드가 과거 시총을 채운다 → 첫날 다음부터 섹터 수익률 가중치가 0 이 아니다
    assert all(w > 0 for w in P["retw"]["거래소"]["운송/창고"][1:])
    assert P["iret"]["거래소"]["운송/창고"] is not None and P["iret"]["코스닥"]["운송/창고"] is not None


def test_busy_window_mid_run_stops_before_sending_and_resume_finishes(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "byo.db"
    fake = FakeKiwoom()
    state = {"n": 0, "limit": 15}       # 토큰·지수·목록 9건 + 첫 종목 4건 뒤, 둘째 종목의 셋째 요청에서 창이 열린다

    def guard():
        state["n"] += 1
        if state["n"] > state["limit"]:
            raise K.BusyWindow("운영 창")
    _patch(monkeypatch, fake, guard)
    assert R.main(["backfill", "--db", str(db), "--sectors", "운송/창고", "--days", "30"]) == R.EXIT_BUSY
    sent_before = len(fake.seen)
    assert sent_before == state["limit"] - 1          # 창에 걸린 요청은 나가지 않았다(토큰 1건 제외)
    con = sqlite3.connect(db)
    assert [r[0] for r in con.execute("SELECT code FROM collect_done")] == ["000120"]
    assert con.execute("SELECT count(*) FROM supply_demand WHERE code='044490'").fetchone() == (0,)  # 롤백
    assert "busy" in con.execute("SELECT notes FROM collect_runs").fetchone()[0]
    con.close()

    state["limit"] = 10**9
    assert R.main(["backfill", "--db", str(db), "--sectors", "운송/창고", "--days", "30", "--resume"]) == 0
    resumed = fake.seen[sent_before:]
    assert resumed.count("ka10059") == 1 and resumed.count("ka10001") == 1   # 끝난 000120 은 다시 안 받는다
    con = sqlite3.connect(db)
    assert sorted(r[0] for r in con.execute("SELECT code FROM collect_done")) == ["000120", "044490"]


def test_truncated_history_fails_but_new_listing_is_only_a_note(tmp_path, monkeypatch):
    import sqlite3
    _patch(monkeypatch, FakeKiwoom(sd_days={"044490": 12}))
    db = tmp_path / "a.db"
    assert R.main(["backfill", "--db", str(db), "--sectors", "운송/창고", "--days", "30"]) == 0
    con = sqlite3.connect(db)
    failed, notes = con.execute("SELECT failed, notes FROM collect_runs").fetchone()
    assert json.loads(failed) == [] and "044490" in notes and "이력 시작" in notes

    _patch(monkeypatch, FakeKiwoom(sd_days={"044490": 12}, sd_more=True))
    db = tmp_path / "b.db"
    R.main(["backfill", "--db", str(db), "--sectors", "운송/창고", "--days", "30"])
    con = sqlite3.connect(db)
    failed = json.loads(con.execute("SELECT failed FROM collect_runs").fetchone()[0])
    assert [f[0] for f in failed if f[1] == "ka10059"] == ["044490"] and "잘림" in failed[0][2]


def test_replace_events_keeps_events_outside_the_response_range(tmp_path):
    from sectorflow.collect import write
    con = write.open_db(tmp_path / "e.db")
    write.replace_events(con, "000120", [("000120", "2024-03-02", "8", -50.0),
                                         ("000120", "2026-05-20", "16", 900.0)], "2024-01-01", "2026-11-06")
    # 증분 실행: 최근 15일만 덮는 응답 — 그 안에 이벤트가 없어도 과거 분할은 남아야 한다
    write.replace_events(con, "000120", [], "2026-10-20", "2026-11-06")
    assert [r[0] for r in con.execute("SELECT date FROM corporate_actions ORDER BY date")] == [
        "2024-03-02", "2026-05-20"]
    write.replace_events(con, "000120", [], "2026-05-01", "2026-11-06")
    assert [r[0] for r in con.execute("SELECT date FROM corporate_actions")] == ["2024-03-02"]
