"""DART 주식수 이력 — 녹화한 응답만 쓴다(네트워크 없음).

픽스처(``tests/fixtures/dart/``)는 2026-11-08 실측 응답이다. 키는 요청 URL 에만 있고 응답에는 없다.
``corpCode_subset.xml`` 은 실제 corpCode.xml(약 12만 건)에서 세 건만 남긴 것이다.

지키는 것:
- 보통주 ``istc_totqy`` 와 응답의 ``stlm_dt`` 를 그대로 쓴다(합계·유통주식수가 아니다).
- 시드가 못 보는 유상증자가 DART 행으로 과거 시총에 들어간다(Phase 1 실패의 원인).
- 실측 행이 덮는 구간을 시드가 가로채지 않는다.
- 호출 상한·속도·허용 경로, 키가 예외·원장에 남지 않는다.
"""

from __future__ import annotations

import io
import json
import sqlite3
import urllib.parse
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from sectorflow.collect import auth
from sectorflow.collect import dart as D
from sectorflow.collect import run as R
from sectorflow.collect import write
from sectorflow.collect.kiwoom import CallBudgetExceeded
from sectorflow.collect.shares import reseed
from sectorflow.storage import market_cap_asof_bulk

FIX = Path(__file__).resolve().parent / "fixtures" / "dart"
KEY = "TESTKEY0123456789abcdef"
CORP = "00145437"   # 012170 아센디오


def _rec(year: int, rep: str) -> bytes:
    return (FIX / f"stockTotqySttus_{CORP}_{year}_{rep}.json").read_bytes()


def _corp_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", (FIX / "corpCode_subset.xml").read_bytes())
    return buf.getvalue()


NO_DATA = json.dumps({"status": "013", "message": "조회된 데이타가 없습니다."}).encode()


class FakeDart:
    """URL 로 녹화 응답을 고른다. 받은 요청은 키를 뺀 쿼리로 남긴다."""

    def __init__(self, missing: "set[tuple[str, str]] | None" = None):
        self.seen: list[dict] = []
        self.keys: list[str] = []
        self.missing = missing or set()

    def __call__(self, url: str, timeout: float):
        path = url.split("?")[0].rsplit("/", 1)[1]
        q = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
        self.keys.append(q.pop("crtfc_key"))
        self.seen.append({"path": path, **q})
        if path == "corpCode.xml":
            return 200, _corp_zip()
        if q["corp_code"] != CORP or (q["bsns_year"], q["reprt_code"]) in self.missing:
            return 200, NO_DATA
        try:
            return 200, _rec(int(q["bsns_year"]), q["reprt_code"])
        except FileNotFoundError:
            return 200, NO_DATA


def _client(fake, **kw) -> D.DartClient:
    return D.DartClient(KEY, transport=fake, sleep=lambda s: None, **kw)


# ── 파싱 ─────────────────────────────────────────────────────────────────
def test_recorded_response_gives_common_issued_shares_at_its_settlement_date():
    got = D.parse_shares(json.loads(_rec(2025, "11011")))
    assert got == (10_873_743, "2025-12-31", "20260318001629")   # 유통주식수 10,867,315 가 아니다


def test_total_row_alone_or_no_data_is_not_a_value():
    data = json.loads(_rec(2025, "11011"))
    data["list"] = [r for r in data["list"] if r["se"] != "보통주"]
    assert D.parse_shares(data) is None
    assert D.parse_shares(json.loads(NO_DATA)) is None


def _labelled(*rows):
    return {"status": "000", "list": [{"se": se, "istc_totqy": n, "stlm_dt": "2025-12-31", "rcept_no": "1"}
                                      for se, n in rows] + [{"se": "합계", "istc_totqy": "9", "stlm_dt": "2025-12-31"}]}


@pytest.mark.parametrize("rows,want", [
    ([("의결권 있는 주식\n(보통주)", "534,569,512"), ("의결권 없는 주식\n(우선주)", "-")], 534_569_512),  # 028670
    ([("의결권 있는 주식", "24,453,930")], 24_453_930),                                         # 047820
    ([("의결권이 있는 주식", "100")], 100),
    ([("보통주식", "14,351,010"), ("종류주식", "-")], 14_351_010),                               # 403850
    ([("보통주식", "213,940,500"), ("유통주식", "-")], None),     # 035250: 모르는 라벨 → 추측하지 않는다
    ([("보통주", "10"), ("보통주식", "11")], None),               # 후보 둘
    ([], None),                                                  # 111710: 합계·비고뿐
])
def test_company_specific_labels_follow_the_kr_quant_parser(rows, want):
    got = D.parse_shares(_labelled(*rows))
    assert (got[0] if got else None) == want


def test_corp_codes_map_listed_stocks_only():
    assert D.parse_corp_codes(_corp_zip()) == {"012170": CORP, "044480": "00173449"}
    with pytest.raises(D.DartError) as e:
        D.parse_corp_codes(json.dumps({"status": "020", "message": "한도 초과"}).encode())
    assert e.value.status == "020"


@pytest.mark.parametrize("start,end,want", [
    ("2025-08-13", "2026-11-06", ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30",
                                  "2026-09-30"]),
    ("2025-03-31", "2025-03-31", ["2025-03-31"]),
    ("2025-03-30", "2025-04-01", ["2024-12-31", "2025-03-31"]),
])
def test_periods_cover_the_start_and_stop_at_the_last_closed_quarter(start, end, want):
    got = D.periods(start, end)
    assert [p[2] for p in got] == want
    assert {(p[2][5:7], p[1]) for p in got} <= {("03", "11013"), ("06", "11012"), ("09", "11014"), ("12", "11011")}


def test_no_data_is_final_only_after_the_filing_deadline():
    assert not D.settled("2026-09-30", "2026-11-08T12:00:00+00:00")   # 3분기 보고서 기한(11-14) 전
    assert D.settled("2025-12-31", "2026-11-08T12:00:00+00:00")


def test_unit_errors_are_dropped_not_divided():
    keep, drop = D.drop_unit_errors([("2024-12-31", 5_000_000), ("2025-12-31", 5_000_000_000)], [5_100_000])
    assert keep == [("2024-12-31", 5_000_000)] and drop == [("2025-12-31", 5_000_000_000)]
    assert D.drop_unit_errors([("2025-12-31", 12_000_000_000)])[1] == [("2025-12-31", 12_000_000_000)]


# ── 클라이언트 ───────────────────────────────────────────────────────────
def test_client_refuses_paths_outside_the_allow_list():
    fake = FakeDart()
    with pytest.raises(ValueError):
        _client(fake).get("list.json")
    assert fake.seen == []


def test_client_throttles_and_stops_at_the_call_cap_before_sending():
    t = {"now": 0.0}
    waits: list[float] = []
    fake = FakeDart()
    c = D.DartClient(KEY, rate=1.0, max_calls=2, transport=fake, clock=lambda: t["now"],
                     sleep=lambda s: (waits.append(s), t.__setitem__("now", t["now"] + s)))
    c.get("corpCode.xml")
    t["now"] += 0.25
    c.get("corpCode.xml")
    assert waits == [pytest.approx(0.75)]
    with pytest.raises(CallBudgetExceeded):
        c.get("corpCode.xml")
    assert len(fake.seen) == 2 and c.total_calls == 2


def test_errors_never_carry_the_key():
    def echo(url, timeout):
        return 400, f"bad request {url}".encode()
    with pytest.raises(D.DartError) as e:
        _client(echo).get("corpCode.xml")
    assert KEY not in str(e.value)

    def quota(url, timeout):
        return 200, json.dumps({"status": "020", "message": f"key {KEY} exhausted"}).encode()
    with pytest.raises(D.DartError) as e:
        _client(quota).get_json("stockTotqySttus.json", {"corp_code": CORP})
    assert e.value.status == "020" and KEY not in str(e.value)
    assert "crtfc_key=***" in auth.mask(f"https://x/api/a.json?crtfc_key={KEY}&corp_code=1")


def test_server_errors_are_retried_and_counted():
    n = {"i": 0}

    def flaky(url, timeout):
        n["i"] += 1
        if n["i"] == 1:
            raise OSError("reset")
        return (503, b"") if n["i"] == 2 else (200, b"ok")
    c = _client(flaky, retries=2)
    assert c.get("corpCode.xml") == b"ok" and c.total_calls == 3
    with pytest.raises(D.DartError) as e:
        _client(lambda u, t: (503, b""), retries=1).get("corpCode.xml")
    assert e.value.status == "net"


# ── 원장 → 주식수 이력 → 시총 ─────────────────────────────────────────────
PLAN = D.periods("2025-08-13", "2026-11-06")


def _db(tmp_path, first="2025-08-13", snap=("2026-11-08", 10_300_000)) -> sqlite3.Connection:
    con = write.open_db(tmp_path / "d.db")
    con.row_factory = None
    con.execute("INSERT INTO stocks VALUES ('012170','아센디오','KOSPI','운송/창고','stock')")
    con.execute("INSERT INTO supply_demand(code, date, close, source) VALUES ('012170', ?, 1000, 'kiwoom')",
                (first,))
    write.upsert_shares(con, [("012170", *snap)], "kiwoom")
    con.commit()
    return con


def test_history_fills_rows_by_settlement_date_and_resume_skips_settled_cells(tmp_path):
    con = _db(tmp_path)
    fake = FakeDart()
    clock = iter(["2026-11-08T12:00:00+00:00"] * 100)
    st = D.fetch_history(con, _client(fake), ["012170", "999990"], PLAN, now=lambda: next(clock))
    assert st["no_corp"] == ["999990"] and st["asked"] == len(PLAN)
    assert [s["path"] for s in fake.seen].count("corpCode.xml") == 1
    assert set(fake.keys) == {KEY}
    applied = D.apply_history(con, ["012170"])
    rows = con.execute("SELECT h.date, h.shares_outstanding, p.source FROM shares_outstanding_history h "
                       "JOIN shares_provenance p USING (code, date) ORDER BY h.date").fetchall()
    assert applied["rows"] == len([r for r in rows if r[2] == "dart"]) >= 4
    assert ("2025-12-31", 10_873_743, "dart") in rows and rows[-1] == ("2026-11-08", 10_300_000, "kiwoom")
    assert KEY not in json.dumps(con.execute("SELECT * FROM dart_fetch").fetchall())

    # 다시 돌리면: 값이 있는 칸·기한 지난 013 은 묻지 않고, 기한 전 013(3분기 미제출)만 다시 묻는다
    unsettled = {(y, r) for y, r, pe in PLAN if not D.settled(pe, "2026-11-08T12:00:00+00:00")}
    fake2 = FakeDart()
    statuses = dict(((y, r), st) for y, r, st in con.execute(
        "SELECT bsns_year, reprt_code, status FROM dart_fetch WHERE code='012170'").fetchall())
    again = {k for k in unsettled if statuses[k] != D.OK}
    st2 = D.fetch_history(con, _client(fake2), ["012170"], PLAN)
    assert {(int(s["bsns_year"]), s["reprt_code"]) for s in fake2.seen if s["path"] != "corpCode.xml"} == again
    assert st2["skipped"] == len(PLAN) - len(again)
    D.apply_history(con, ["012170"])          # 멱등
    assert con.execute("SELECT count(*) FROM shares_outstanding_history").fetchone()[0] == len(rows)


def _ledger(con, rows):
    con.executemany("INSERT INTO dart_fetch VALUES (?,?,?,?,?,?,?,?,?)",
                    [("012170", y, r, pe, "000", n, st, rc, "2026-11-08T00:00:00+00:00")
                     for y, r, pe, st, n, rc in rows])


def test_latest_filing_wins_and_a_same_day_kiwoom_snapshot_is_kept(tmp_path):
    con = _db(tmp_path, snap=("2025-12-31", 7_000_000))
    # 9월 결산 회사: 3분기 코드로 물어도 사업보고서 코드로 물어도 기준일 09-30 — 더 늦게 접수된 값이 이긴다
    _ledger(con, [(2025, "11014", "2025-09-30", "2025-09-30", 6_000_000, "20251114000001"),
                  (2025, "11011", "2025-12-31", "2025-09-30", 6_100_000, "20251229000001"),
                  (2026, "11013", "2026-03-31", "2025-12-31", 6_900_000, "20260318000002")])
    D.apply_history(con, ["012170"])
    rows = con.execute("SELECT h.date, h.shares_outstanding, p.source FROM shares_outstanding_history h "
                       "JOIN shares_provenance p USING (code, date) ORDER BY h.date").fetchall()
    assert rows == [("2025-09-30", 6_100_000, "dart"), ("2025-12-31", 7_000_000, "kiwoom")]


def test_nothing_to_ask_does_not_touch_the_network(tmp_path):
    con = _db(tmp_path)
    D.fetch_history(con, _client(FakeDart()), ["012170"], PLAN[:1], now=lambda: "2026-11-08T00:00:00+00:00")
    fake = FakeDart()
    assert D.fetch_history(con, _client(fake), ["012170"], PLAN[:1])["asked"] == 0
    assert fake.seen == []


def test_rights_issue_the_seed_cannot_see_reaches_past_market_cap(tmp_path):
    """병합·유상증자가 겹친 종목(Phase 1 의 012170 부류): 시드는 현재 값만 거꾸로 세고, DART 행은 그 사이 증자를 본다."""
    con = _db(tmp_path, snap=("2026-11-08", 50_000_000))
    con.execute("INSERT INTO corporate_actions VALUES ('012170','2026-08-28','16',400.0)")  # 5:1 병합
    con.execute("INSERT INTO dart_fetch VALUES ('012170',2025,'11011','2025-12-31','000',20000000,"
                "'2025-12-31','20260318000001','2026-11-08T00:00:00+00:00')")
    con.commit()
    day = pd.DataFrame({"code": ["012170"], "date": ["2026-01-05"]})
    con.execute("INSERT INTO supply_demand(code, date, close, source) VALUES ('012170','2026-01-05',100,'kiwoom')")
    reseed(con, ["012170"])
    seed_only = market_cap_asof_bulk(con, day).iloc[0]
    assert seed_only == pytest.approx(100 * 50_000_000 * 5)       # 시드: 병합 전 = 현재 × 5
    D.apply_history(con, ["012170"])
    reseed(con, ["012170"])
    assert market_cap_asof_bulk(con, day).iloc[0] == pytest.approx(100 * 20_000_000)   # DART 실측
    after = pd.DataFrame({"code": ["012170"], "date": ["2026-09-01"]})
    con.execute("INSERT INTO supply_demand(code, date, close, source) VALUES ('012170','2026-09-01',500,'kiwoom')")
    assert market_cap_asof_bulk(con, after).iloc[0] == pytest.approx(500 * 20_000_000 / 5)  # 병합 다시 세기


def test_seed_only_fills_the_gap_before_the_earliest_measured_row(tmp_path):
    con = _db(tmp_path, first="2025-08-13", snap=("2026-11-08", 10_000_000))
    con.execute("INSERT INTO corporate_actions VALUES ('012170','2025-10-01','8',-50.0)")   # 2:1 분할
    write.upsert_shares(con, [("012170", "2025-12-31", 9_000_000)], "dart")
    reseed(con, ["012170"])
    seed = con.execute("SELECT date, shares_outstanding FROM shares_outstanding_history h JOIN shares_provenance p "
                       "USING (code, date) WHERE p.source='seed'").fetchall()
    assert seed == [("2025-08-13", 4_500_000)]       # 가장 이른 실측(DART 900만) ÷ 2, 현재값(1,000만)이 아니다
    write.upsert_shares(con, [("012170", "2025-06-30", 4_400_000)], "dart")
    reseed(con, ["012170"])
    assert con.execute("SELECT count(*) FROM shares_provenance WHERE source='seed'").fetchone() == (0,)


# ── CLI ─────────────────────────────────────────────────────────────────
def _patch_client(monkeypatch, fake):
    orig = D.DartClient
    monkeypatch.setattr(R.dart, "DartClient", lambda key, **kw: orig(key, transport=fake, sleep=lambda s: None,
                                                                     **kw))


def test_cli_uses_only_dart_and_logs_the_run(tmp_path, monkeypatch):
    con = _db(tmp_path)
    con.close()
    fake = FakeDart()

    def no_kiwoom(*a, **k):
        raise AssertionError("dart-shares 가 키움을 불렀다")
    monkeypatch.setattr(R, "_client", no_kiwoom)
    _patch_client(monkeypatch, fake)
    monkeypatch.delenv(auth.DART_ENV, raising=False)
    env = tmp_path / ".env"
    env.write_text(f"export {auth.DART_ENV}='{KEY}'\n", "utf-8")
    db = str(tmp_path / "d.db")
    assert R.main(["--env-file", str(env), "dart-shares", "--db", db, "--until", "2026-11-06"]) == 0
    assert set(fake.keys) == {KEY}
    con = sqlite3.connect(db)
    kind, ok, calls, rows = con.execute("SELECT kind, ok, calls, rows FROM collect_runs").fetchone()
    assert (kind, ok) == ("dart-shares", 1)
    assert json.loads(calls) == {"dart:corpCode.xml": 1, "dart:stockTotqySttus.json": len(PLAN)}
    assert json.loads(rows)["dart"] >= 4


def test_cli_stops_at_the_cap_but_keeps_what_it_got(tmp_path, monkeypatch, capsys):
    _db(tmp_path).close()
    fake = FakeDart()
    _patch_client(monkeypatch, fake)
    monkeypatch.setenv(auth.DART_ENV, KEY)
    db = str(tmp_path / "d.db")
    assert R.main(["dart-shares", "--db", db, "--until", "2026-11-06", "--dart-max-calls", "4"]) == 1
    out = capsys.readouterr()
    assert KEY not in out.out + out.err
    con = sqlite3.connect(db)
    assert con.execute("SELECT count(*) FROM dart_fetch").fetchone() == (3,)
    assert con.execute("SELECT ok FROM collect_runs").fetchone() == (0,)
    assert "상한" in con.execute("SELECT notes FROM collect_runs").fetchone()[0]


def test_missing_dart_key_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv(auth.DART_ENV, raising=False)
    with pytest.raises(auth.MissingKeys) as e:
        auth.load_dart_key(tmp_path / "none.env")
    assert auth.DART_ENV in str(e.value)


# ── 이미 반영한 병합을 다시 세지 않는다 ─────────────────────────────────────
MERGER = [("2026-07-03", "16", 400.0)]       # 5:1 병합 — 변경상장(키움 이벤트일)이 분기말 뒤


def test_post_merger_quarter_value_moves_to_the_relisting_day():
    """033830(2026-11-08 실측): 06-30 발행주식총수가 이미 병합 뒤인데 키움 이벤트는 07-03 — 그대로면 ÷5 를 한 번 더 한다."""
    pts = [("2026-03-31", 94_535_650), ("2026-06-30", 18_907_130)]
    got = D.align_to_listing(pts, [("2026-11-08", 18_907_000)], MERGER)
    assert got == [("2026-03-31", 94_535_650, "dart"), ("2026-07-03", 18_907_130, "dart_moved")]


def test_pre_merger_value_or_an_ambiguous_gap_is_left_alone():
    # 병합 전 값이고 다시 세면 다음 실측과 맞는다 — 그대로
    assert D.align_to_listing([("2026-06-30", 50_000_000)], [("2026-11-08", 10_000_000)], MERGER) == [
        ("2026-06-30", 50_000_000, "dart")]
    # 012170: 병합 뒤 증자까지 겹쳐 어느 쪽과도 안 맞는다 — 추측하지 않고 표지만 남긴다
    loose: list[str] = []
    assert D.align_to_listing([("2026-06-30", 20_250_194)], [("2026-11-08", 10_302_000)],
                              [("2026-08-28", "16", 400.0)], loose) == [("2026-06-30", 20_250_194, "dart")]
    assert loose == ["2026-06-30"]
    clean: list[str] = []
    D.align_to_listing([("2026-06-30", 50_000_000)], [("2026-11-08", 10_000_000)], MERGER, clean)
    assert clean == []
    # 옮길 날에 키움 실측이 이미 있으면 그 점은 뺀다(같은 날 두 값이 없게)
    assert D.align_to_listing([("2026-06-30", 18_907_130)], [("2026-07-03", 18_907_000)], MERGER) == []


def test_aligned_history_gives_the_listing_based_market_cap(tmp_path):
    con = _db(tmp_path, snap=("2026-11-08", 18_907_000))
    con.execute("INSERT INTO corporate_actions VALUES ('012170','2026-07-03','16',400.0)")
    _ledger(con, [(2026, "11013", "2026-03-31", "2026-03-31", 94_535_650, "20260515000001"),
                  (2026, "11012", "2026-06-30", "2026-06-30", 18_907_130, "20260814000001")])
    assert D.apply_history(con, ["012170"])["moved"] == 1
    days = pd.DataFrame({"code": ["012170"] * 2, "date": ["2026-07-01", "2026-09-01"]})
    con.executemany("INSERT INTO supply_demand(code, date, close, source) VALUES ('012170', ?, 100, 'kiwoom')",
                    [(d,) for d in days["date"]])
    cap = market_cap_asof_bulk(con, days).tolist()
    assert cap == [pytest.approx(100 * 94_535_650), pytest.approx(100 * 18_907_130)]   # ÷5 를 두 번 하지 않는다


def test_pre_listing_value_of_a_new_listing_is_not_used(tmp_path):
    """403850(2026-11-08 실측): 구간 중 상장한 종목의 상장 전 사업보고서 값에는 공모 신주가 없다."""
    con = _db(tmp_path, first="2025-11-18", snap=("2026-11-08", 14_351_000))
    con.execute("INSERT INTO supply_demand(code, date, close, source) VALUES ('000120', '2025-08-13', 1, 'kiwoom')")
    _ledger(con, [(2024, "11011", "2024-12-31", "2024-12-31", 12_351_010, "20250320000001"),
                  (2025, "11011", "2025-12-31", "2025-12-31", 14_351_010, "20260320000001")])
    D.apply_history(con, ["012170"])
    reseed(con, ["012170"])
    rows = con.execute("SELECT h.date, h.shares_outstanding, p.source FROM shares_outstanding_history h "
                       "JOIN shares_provenance p USING (code, date) WHERE code='012170' ORDER BY h.date").fetchall()
    assert rows[:2] == [("2025-11-18", 14_351_010, "seed"), ("2025-12-31", 14_351_010, "dart")]
    # 구간 첫날부터 있던 종목은 구간 앞 값을 그대로 쓴다(as-of 조회의 출발점)
    (tmp_path / "b").mkdir()
    con2 = _db(tmp_path / "b")
    _ledger(con2, [(2024, "11011", "2024-12-31", "2024-12-31", 9_000_000, "20250320000001")])
    D.apply_history(con2, ["012170"])
    assert con2.execute("SELECT count(*) FROM shares_provenance WHERE source='dart'").fetchone() == (1,)
