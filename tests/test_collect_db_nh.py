"""DB증권·NH투자증권 어댑터 — 기관계만 주는 둘. 손으로 쓴 최소 표본과 가짜 서버로(네트워크·실제 키 없음). **미검증.**

표본은 공개 문서의 필드 이름표(``docs/byo/brokers.md`` §2.5·§2.6)를 보고 손으로 썼다(값은 지어낸 것). 지키는 것: 주는 세 주체만
채우고 나머지(기관 세부·기타법인·국가)는 **NULL**(0 이 아니다) · 기타법인을 잔여 −(개인+외국인+기관)로 메우지 않는다 · DB 는 매수 −
매도로 순매수, 둘 중 하나라도 비면 NULL · KRX 단독 · DB 는 ``cont_yn``/날짜 옮기기, NH 는 기준일 없이 머리 ``cts`` 로만 과거로 ·
NH 의 빈 응답 블록은 메시지를 싣고 실패.
"""

from __future__ import annotations

import sqlite3

import pytest

from sectorflow.collect import run as R
from sectorflow.collect import write
from sectorflow.collect.brokers import BROKERS, CAPABILITIES
from sectorflow.collect.brokers import db as DB
from sectorflow.collect.brokers import nh as NH
from sectorflow.collect.brokers.http import BrokerError, HttpSession
from sectorflow.storage import INVESTOR_COLUMNS
from tests.test_collect_daily import CAL, kst

TRUTH = CAL.trading_days("2026-01-02", "2026-11-20")[0]
THREE = ("individual", "foreign_", "institution")
DB_ROW = {"Date": "20261120", "Prpr": "12500", "PrdyVrssSign": "5", "PrdyVrss": "-150", "PrdyCtrt": "-1.19",
          "AcmlVol": "345678", "AcmlTrPbmn": "4321",
          "PrsnShnuVol": "5000", "PrsnSelnVol": "8000", "FrgnRegShnuVol": "3100", "FrgnRegSelnVol": "2000",
          "OrgnShnuVol": "2500", "OrgnSelnVol": "1000"}
NH_ROW = {"bsop_date1": "20261120", "bsop_date2": "26112000", "stck_prpr": 12500, "prdy_vrss_sign": "5",
          "prdy_vrss": -150, "prdy_ctrt": -1.19, "acml_vol": 345678.0, "for_rate": 12.3, "frgn_ntby_qty": 1100.0,
          "person": -3000.0, "gigwan": 1500.0, "invest": 1050.0, "account": 0.0, "program": 0.0}


def test_only_three_actors_and_the_rest_unknown():
    for name in ("db", "nh"):
        cap = CAPABILITIES[name]
        assert cap.actors == frozenset(THREE) and cap.verified is False and cap.venue == "KRX"
        assert set(cap.missing) == set(INVESTOR_COLUMNS) - set(THREE)
    (d,) = DB.flow_rows("000120", [DB_ROW], "2026-11-01", "2026-11-30")
    (n,) = NH.flow_rows("000120", [NH_ROW], "2026-11-01", "2026-11-30")
    for row in (d, n):
        assert (row["individual"], row["foreign_"], row["institution"]) == (-3000, 1100, 1500)
        assert all(row[c] is None for c in INVESTOR_COLUMNS if c not in THREE), "주지 않는 칸은 NULL(모름)"
        assert row["etc_corp"] is None, "기타법인을 잔여(−(개인+외국인+기관) = 400)로 메우지 않는다"
        assert (row["close"], row["acc_trde_qty"]) == (12500, 345678)


def test_db_net_is_buy_minus_sell_and_unknown_if_either_is_blank():
    (row,) = DB.flow_rows("000120", [{**DB_ROW, "OrgnSelnVol": ""}], "2026-11-20", "2026-11-20")
    assert row["institution"] is None and row["individual"] == -3000


class FakeDb:
    def __init__(self, days, *, page=20, server_cont=False):
        self.days, self.page, self.server_cont = sorted(days, reverse=True), page, server_cont
        self.seen: list[tuple[str, dict, dict]] = []

    def __call__(self, method, url, headers, body, timeout):
        path = url.split(":8443", 1)[1]
        self.seen.append((path, dict(headers), body))
        if path == DB.TOKEN_PATH:
            assert body["appsecretkey"] == "ds" and body["scope"] == "oob"
            return 200, {}, {"access_token": "DT", "expires_in": 86400}
        if path == DB.LIST_PATH:
            return 200, {"cont_yn": "N"}, {"rsp_cd": "00000", "Out": [
                {"Iscd": "000120", "KorIsnm": "CJ대한통운", "MrktClsCode": "4", "MrktClsName": "거래소(코스피)"},
                {"Iscd": "044490", "KorIsnm": "태웅로직스", "MrktClsCode": "1", "MrktClsName": "코스닥"},
                {"Iscd": "005935", "KorIsnm": "삼성전자우", "MrktClsCode": "4", "MrktClsName": "거래소(코스피)"}]}
        ib = body["In"]
        assert path == DB.FLOW_PATH and ib["InputCondMrktDivCode"] == "J"
        days = [d for d in self.days if ib["InputDate1"] <= d.replace("-", "") <= ib["InputDate2"]]
        off = int(headers["cont_key"] or 0) if headers["cont_yn"] == "Y" else 0
        rows = days[off:off + self.page]
        hdr = {"cont_yn": "Y", "cont_key": str(off + self.page)} if self.server_cont and off + self.page < len(days) \
            else {"cont_yn": "N"}
        return 200, hdr, {"rsp_cd": "00000", "Out": [{**DB_ROW, "Date": d.replace("-", "")} for d in rows]}

    def flows(self):
        return [s for s in self.seen if s[0] == DB.FLOW_PATH]


def _db(fake):
    http = HttpSession("db", DB.PROD_URL, allowed=DB.ALLOWED, limits=DB.LIMITS, rate=None, transport=fake,
                       sleep=lambda s: None, secrets=("dk", "ds"))
    return DB.DbBroker("dk", "ds", http, wall=lambda: kst("2026-11-20 21:00").timestamp())


@pytest.mark.parametrize("server_cont", [False, True])
def test_db_pages_back_both_ways(server_cont):
    fake = FakeDb(TRUTH, page=25, server_cont=server_cont)
    rows = _db(fake).investor_daily("000120", "2026-08-03", "2026-11-20", days=78)
    assert [r["date"] for r in rows] == sorted([d for d in TRUTH if d >= "2026-08-03"], reverse=True)
    heads = [h["cont_yn"] for _p, h, _b in fake.flows()]
    assert (heads[1:] == ["Y"] * (len(heads) - 1)) is server_cont


def test_db_listing_keeps_common_stocks(tmp_path, monkeypatch):
    fake = FakeDb(TRUTH)
    monkeypatch.setitem(BROKERS, "db", lambda s: _db(fake))
    assert R.main(["universe", "--db", str(tmp_path / "d.db"), "--broker", "db"]) == 0
    got = sqlite3.connect(tmp_path / "d.db").execute("SELECT code, market FROM stocks ORDER BY code").fetchall()
    assert got == [("000120", "거래소"), ("044490", "코스닥")]


class FakeNh:
    def __init__(self, days, *, page=30, empty=False):
        self.days, self.page, self.empty = sorted(days, reverse=True), page, empty
        self.seen: list[tuple[str, dict, dict]] = []

    def __call__(self, method, url, headers, body, timeout):
        path = url.split(":8443", 1)[1]
        self.seen.append((path, dict(headers), body))
        if path == NH.TOKEN_PATH:
            return 200, {}, {"access_token": "NT", "token_type": "Bearer", "expires_in": 86400}
        ib = body["Input_0"]
        assert path == NH.FLOW_PATH and ib["market_cd"] == "KRX" and ib["array_cnt"] == "100"
        if self.empty:
            return 200, {}, {"rsp_cd": "00218", "rsp_msg": "조회할 자료가 없습니다."}
        off = int(headers.get("cts") or 0) if headers.get("cts_flag") == "Y" else 0
        rows = self.days[off:off + self.page]
        more = off + self.page < len(self.days)
        hdr = {"cts": str(off + self.page), "cts_flag": "Y"} if more else {"cts_flag": "N"}
        return 200, hdr, {"rsp_cd": "00000", "rsp_msg": "조회가 완료되었습니다.",
                          "Output_0": [{**NH_ROW, "bsop_date1": d.replace("-", "")} for d in rows]}

    def flows(self):
        return [s for s in self.seen if s[0] == NH.FLOW_PATH]


def _nh(fake):
    http = HttpSession("nh", NH.PROD_URL, allowed=NH.ALLOWED, limits=NH.LIMITS, rate=None, transport=fake,
                       sleep=lambda s: None, secrets=("nk", "ns"))
    return NH.NhBroker("nk", "ns", http, wall=lambda: kst("2026-11-20 21:00").timestamp())


def test_nh_reaches_back_only_through_the_cts_header():
    fake = FakeNh(TRUTH)
    info: dict = {}
    rows = _nh(fake).investor_daily("000120", "2026-09-01", "2026-11-20", days=55, info=info)
    assert [r["date"] for r in rows] == sorted([d for d in TRUTH if d >= "2026-09-01"], reverse=True)
    heads = [(h.get("cts"), h.get("cts_flag")) for _p, h, _b in fake.flows()]
    assert heads == [(None, None), ("30", "Y")] and info["more"] is False


def test_nh_without_a_data_block_fails_with_its_message():
    with pytest.raises(BrokerError, match="00218"):
        _nh(FakeNh(TRUTH, empty=True)).investor_daily("000120", "2026-11-20", "2026-11-20", days=1)


def test_nh_token_notice_and_rate(monkeypatch):
    from sectorflow.collect.brokers import Settings
    monkeypatch.setenv(NH.KEY_ENV, "nk")
    monkeypatch.setenv(NH.SECRET_ENV, "ns")
    said: list[str] = []
    b = NH.make(Settings(notice=said.append, rate=50.0))
    assert "보안 알림" in said[0] and b.http.min_interval == pytest.approx(1 / 5)
    assert NH.make(Settings()).http.min_interval == pytest.approx(1 / NH.DEFAULT_RATE)


@pytest.mark.parametrize("name, fake, make", [("db", FakeDb(TRUTH), _db), ("nh", FakeNh(TRUTH), _nh)])
def test_backfill_leaves_unsupplied_actors_null(tmp_path, monkeypatch, name, fake, make):
    db = tmp_path / f"{name}.db"
    con = write.open_db(db)
    write.upsert_stocks(con, [{"code": "000120", "name": "CJ대한통운", "market": "거래소", "sector": "운송/창고",
                               "kind": "A", "sector_source": "kiwoom"}])
    con.commit()
    con.close()
    monkeypatch.setitem(BROKERS, name, lambda s: make(fake))
    monkeypatch.setattr(R, "_now_kst", lambda: kst("2026-11-20 21:00"))
    assert R.main(["backfill", "--db", str(db), "--broker", name, "--map-only", "--codes", "000120",
                   "--days", "20"]) == 0
    con = sqlite3.connect(db)
    counts = dict(zip(INVESTOR_COLUMNS, con.execute(
        "SELECT " + ", ".join(f"count({c})" for c in INVESTOR_COLUMNS) + " FROM supply_demand").fetchone()))
    assert {c: n for c, n in counts.items() if n} == {c: 20 for c in THREE}
    assert con.execute("SELECT DISTINCT source FROM supply_demand").fetchall() == [(name,)]
