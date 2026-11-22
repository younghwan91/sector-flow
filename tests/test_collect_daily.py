"""``sector-flow-collect daily`` — 마지막 저장일 다음 거래일부터 오늘까지 증분, 가짜 전송으로(네트워크 없음).

지키는 것: 끝날은 KRX 휴장 달력 정본(``scripts/krx_calendar.py``)이 정한다 — 주말·휴장은 건너뛰고, 장 마감(애프터마켓
20:00) 전이면 오늘은 받지 않는다 · 종목마다 자기 마지막 저장일부터 받아 그날 값(잠정)을 새 값으로 덮는다 ·
``--map-only`` 는 ka10099·ka10059 둘만 부른다 · 이미 최신이면 키움을 한 번도 부르지 않는다 · 운영 창에서 멈춘 실행을
다시 돌리면 남은 종목만 받는다(``--resume`` 의미 내장) · 같은 섹터의 신규 상장은 따라 받고, 상장 목록에서 빠진 종목은
메모로 남긴다 · 달력은 설치본(wheel)에도 실린다.
"""

from __future__ import annotations

import json
import sqlite3
import tomllib
from datetime import datetime

import pytest

from sectorflow.collect import kiwoom as K
from sectorflow.collect import run as R
from sectorflow.collect import write
from sectorflow.report_cli import script_path

CAL = R.krx_calendar()
TRUTH = CAL.trading_days("2026-06-01", "2026-12-30")[0]       # 가짜 서버가 아는 거래일(달력과 같다)
LISTING = [("000120", "CJ대한통운", "거래소", "운송/창고"), ("044490", "태웅로직스", "코스닥", "운송/창고")]


def kst(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=R.KST)


def _ymd(d: str) -> str:
    return d.replace("-", "")


class FakeServer:
    """요청의 기준일(dt·base_dt) 이하의 거래일만 최신부터 돌려준다. ka10059 는 100일 한 쪽 + 헤더 연속조회."""

    def __init__(self, listing=LISTING):
        self.listing = list(listing)
        self.seen: list[tuple[str, dict]] = []

    def __call__(self, url, headers, body, timeout):
        if url.endswith(K.TOKEN_PATH):
            return 200, {}, {"token": "t", "expires_in": 86400}
        tr = headers["api-id"]
        self.seen.append((tr, dict(body)))
        if tr == "ka10099":
            mk = {"0": "거래소", "10": "코스닥"}[body["mrkt_tp"]]
            return 200, {}, {"return_code": 0, "list": [
                {"code": c, "name": n, "marketName": m, "upName": u, "kind": "A"}
                for c, n, m, u in self.listing if m == mk]}
        if tr == "ka20003":
            lst = {"001": [("001", "종합(KOSPI)"), ("019", "운송/창고")],
                   "101": [("101", "종합(KOSDAQ)"), ("110", "운송/창고")]}[body["inds_cd"]]
            return 200, {}, {"return_code": 0, "all_inds_idex": [{"stk_cd": c, "stk_nm": n} for c, n in lst]}
        anchor = body.get("dt") or body.get("base_dt")
        days = [d for d in reversed(TRUTH) if _ymd(d) <= anchor] if anchor else []
        if tr == "ka10059":
            off = int(headers["next-key"] or 0) if headers["cont-yn"] == "Y" else 0
            page = days[off:off + 100]
            hdr = {"cont-yn": "Y", "next-key": str(off + 100)} if off + 100 < len(days) else {"cont-yn": "N"}
            return 200, hdr, {"return_code": 0, "stk_invsr_orgn": [
                {"dt": _ymd(d), "cur_prc": "-1000", "flu_rt": "+0", "acc_trde_qty": "1000", "ind_invsr": "-300",
                 "frgnr_invsr": "+100", "orgn": "+150", "fnnc_invt": "50", "insrnc": "0", "invtrt": "40",
                 "bank": "0", "penfnd_etc": "60", "samo_fund": "0", "natn": "0", "etc_corp": "+50"}
                for d in page]}
        if tr in ("ka10081", "ka20006"):
            key = "stk_dt_pole_chart_qry" if tr == "ka10081" else "inds_dt_pole_qry"
            return 200, {}, {"return_code": 0, key: [
                {"dt": _ymd(d), "cur_prc": "1000", "open_pric": "1", "high_pric": "1", "low_pric": "1",
                 "trde_qty": "1", "trde_prica": "1", "upd_stkpc_tp": "", "upd_rt": ""} for d in days]}
        if tr == "ka10001":
            return 200, {}, {"return_code": 0, "flo_stk": "50000"}
        raise AssertionError(tr)

    def trs(self) -> list[str]:
        return [t for t, _ in self.seen]


def _seed(db, codes=("000120", "044490"), through="2026-11-13", listing=LISTING):
    """백필이 끝난 sqlite 흉내 — 수급 값은 0(잠정치였다고 친다)."""
    con = write.open_db(db)
    write.upsert_stocks(con, [{"code": c, "name": n, "market": m, "sector": u, "kind": "A"}
                              for c, n, m, u in listing if c in codes])
    days = [d for d in TRUTH if "2026-10-01" <= d <= through]
    write.upsert_supply_demand(con, [(c, d, 1000, 0.0, 0, *([0] * 11), "kiwoom") for c in codes for d in days])
    con.commit()
    con.close()


def _patch(monkeypatch, fake, now, guard=None):
    asked: dict = {}

    def client(args, only=None):
        asked["only"] = only
        return K.KiwoomRest("k", "s", transport=fake, rate=None, max_calls=args.max_calls,
                            sleep=lambda s: None, guard=guard, only=only)
    monkeypatch.setattr(R, "_client", client)
    monkeypatch.setattr(R, "_now_kst", lambda: kst(now))
    return asked


def _dates(con, code):
    return [r[0] for r in con.execute("SELECT date FROM supply_demand WHERE code=? ORDER BY date", (code,))]


@pytest.mark.parametrize("now, end", [
    ("2026-11-20 21:00", "2026-11-20"),    # 금요일 마감 뒤 — 오늘까지
    ("2026-11-20 19:59", "2026-11-19"),    # 애프터마켓 도중 — 오늘은 아직 아니다
    ("2026-11-21 10:00", "2026-11-20"),    # 토요일
    ("2026-10-10 21:00", "2026-10-08"),    # 토요일, 금 10-09 한글날
    ("2026-10-12 08:00", "2026-10-08"),    # 월요일 장 전 — 앞 거래일이 휴장을 건너뛴다
    ("2026-10-12 20:00", "2026-10-12"),
    ("2026-10-09 21:00", "2026-10-08"),    # 휴장일(한글날) 저녁 — 평일이어도 오늘이 아니다
])
def test_target_day_follows_the_krx_calendar_and_the_close(now, end):
    assert R.target_day(kst(now)) == end


def test_target_day_refuses_a_year_the_calendar_does_not_cover():
    with pytest.raises(SystemExit, match="2031년이"):
        R.target_day(kst("2031-03-03 21:00"))


def test_calendar_is_the_canonical_script_and_ships_in_the_wheel():
    assert CAL.__file__ == str(script_path("krx_calendar.py"))
    py = tomllib.loads((script_path("krx_calendar.py").parents[1] / "pyproject.toml").read_text("utf-8"))
    force = py["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force.get("scripts/krx_calendar.py") == "sectorflow/_scripts/krx_calendar.py"


def test_daily_map_only_fills_from_the_last_saved_day_with_two_trs(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    _seed(db)
    fake = FakeServer()
    asked = _patch(monkeypatch, fake, "2026-11-20 21:00")
    assert R.main(["daily", "--db", str(db), "--map-only"]) == 0
    assert asked["only"] == R.map_only_trs              # 클라이언트가 그 밖의 TR 을 요청 전에 막는다
    con = sqlite3.connect(db)
    for code in ("000120", "044490"):
        assert _dates(con, code)[-6:] == ["2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19",
                                          "2026-11-20"]
    # 마지막 저장일(11-13)은 다시 받아 덮는다 — 잠정치가 새 값으로 바뀐다. 그 앞은 건드리지 않는다.
    got = dict(con.execute("SELECT date, institution FROM supply_demand WHERE code='000120'").fetchall())
    assert got["2026-11-13"] == 150 and got["2026-11-12"] == 0
    assert set(fake.trs()) == set(R.map_only_trs) == {"ka10099", "ka10059"}
    assert fake.trs().count("ka10059") == 2                                # 종목당 한 쪽
    assert all(b["dt"] == "20261120" for t, b in fake.seen if t == "ka10059")
    assert sorted(r[0] for r in con.execute("SELECT code FROM collect_done WHERE asof='2026-11-20'")) == [
        "000120", "044490"]
    kind, ok, asof = con.execute("SELECT kind, ok, asof FROM collect_runs").fetchone()
    assert (kind, ok, asof) == ("daily-map", 1, "2026-11-20")


def test_daily_skips_weekends_and_holidays(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    _seed(db, through="2026-10-02")          # 금요일. 10-05 개천절 대체 · 10-09 한글날
    _patch(monkeypatch, FakeServer(), "2026-10-12 08:00")
    assert R.main(["daily", "--db", str(db), "--map-only"]) == 0
    con = sqlite3.connect(db)
    assert _dates(con, "000120")[-4:] == ["2026-10-02", "2026-10-06", "2026-10-07", "2026-10-08"]


def test_daily_when_up_to_date_makes_no_call_at_all(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    _seed(db)
    fake = FakeServer()
    _patch(monkeypatch, fake, "2026-11-20 21:00")
    assert R.main(["daily", "--db", str(db), "--map-only"]) == 0
    n = len(fake.seen)
    calls = []
    fake2 = lambda *a: calls.append(a) or fake(*a)  # noqa: E731
    _patch(monkeypatch, fake2, "2026-11-21 10:00")      # 토요일 — 끝날은 그대로 11-20
    assert R.main(["daily", "--db", str(db), "--map-only"]) == 0
    assert calls == [] and len(fake.seen) == n          # 토큰조차 받지 않는다


def test_daily_resumes_after_a_busy_window(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    _seed(db)
    fake = FakeServer()
    state = {"n": 0, "limit": 4}            # 토큰 · ka10099 ×2 · 첫 종목 ka10059 → 둘째 종목에서 창이 열린다

    def guard():
        state["n"] += 1
        if state["n"] > state["limit"]:
            raise K.BusyWindow("운영 창")
    _patch(monkeypatch, fake, "2026-11-20 21:00", guard)
    assert R.main(["daily", "--db", str(db), "--map-only"]) == R.EXIT_BUSY
    con = sqlite3.connect(db)
    assert [r[0] for r in con.execute("SELECT code FROM collect_done")] == ["000120"]
    assert _dates(con, "044490")[-1] == "2026-11-13"
    before = len(fake.seen)
    state["limit"] = 10**9
    assert R.main(["daily", "--db", str(db), "--map-only"]) == 0
    again = fake.seen[before:]
    assert [b["stk_cd"] for t, b in again if t == "ka10059"] == ["044490"]     # 끝난 종목은 다시 안 받는다
    assert _dates(con, "044490")[-1] == "2026-11-20"


def test_daily_follows_listing_changes_within_the_same_sectors(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    gone = ("002320", "한진", "거래소", "운송/창고")
    _seed(db, codes=("000120", "044490", "002320"), listing=LISTING + [gone])
    listing = LISTING + [("009990", "새물류", "코스닥", "운송/창고"),       # 같은 섹터 신규 상장 — 따라 받는다
                         ("005930", "삼성전자", "거래소", "전기/전자")]      # 받은 적 없는 섹터 — 안 받는다
    fake = FakeServer(listing)
    _patch(monkeypatch, fake, "2026-11-20 21:00")
    assert R.main(["daily", "--db", str(db), "--map-only"]) == 0
    con = sqlite3.connect(db)
    asked = sorted(b["stk_cd"] for t, b in fake.seen if t == "ka10059")
    assert asked == ["000120", "009990", "044490"]
    assert _dates(con, "009990")[0] == "2026-11-13" and _dates(con, "009990")[-1] == "2026-11-20"
    assert con.execute("SELECT sector FROM stocks WHERE code='009990'").fetchone() == ("운송/창고",)
    assert con.execute("SELECT count(*) FROM stocks WHERE code='005930'").fetchone() == (0,)
    notes = con.execute("SELECT notes FROM collect_runs").fetchone()[0]
    assert "002320" in notes and "상장 목록에 없다" in notes


def test_daily_full_mode_also_updates_prices_shares_and_indexes(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    _seed(db)
    fake = FakeServer()
    asked = _patch(monkeypatch, fake, "2026-11-20 21:00")
    assert R.main(["daily", "--db", str(db)]) == 0
    assert asked["only"] is None
    con = sqlite3.connect(db)
    assert {"ka10081", "ka10001", "ka20003", "ka20006"} <= set(fake.trs())
    for t in ("daily_bars", "daily_bars_adjusted", "daily_bars_raw"):
        assert con.execute(f"SELECT min(date), max(date) FROM {t}").fetchone() == ("2026-11-13", "2026-11-20"), t
    assert sorted(r[0] for r in con.execute("SELECT DISTINCT code FROM sector_index")) == ["001", "019", "101", "110"]
    assert con.execute("SELECT max(date) FROM sector_index").fetchone() == ("2026-11-20",)
    assert con.execute("SELECT count(*) FROM shares_outstanding_history WHERE date='2026-11-20'").fetchone() == (2,)
    kind, ok, calls = con.execute("SELECT kind, ok, calls FROM collect_runs").fetchone()
    assert (kind, ok) == ("daily", 1) and json.loads(calls)["ka10059"] == 2


def test_daily_without_any_saved_flow_refuses(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    write.open_db(db).close()
    fake = FakeServer()
    _patch(monkeypatch, fake, "2026-11-20 21:00")
    with pytest.raises(SystemExit, match="backfill"):
        R.main(["daily", "--db", str(db), "--map-only"])
    assert fake.seen == []
