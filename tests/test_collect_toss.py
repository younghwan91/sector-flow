"""토스증권 어댑터 — 손으로 쓴 최소 표본 응답과 가짜 서버로(네트워크·실제 키 없음). **미검증, KRX+NXT 통합.**

표본은 공개 OpenAPI 문서의 스키마·예시(``docs/byo/brokers.md`` §2.4)를 보고 손으로 썼다(값은 지어낸 것). 지키는 것: 필드 대응 ·
당일 잠정 기록의 null(개인·기관 세부·기타법인)은 NULL · 국가 결측 · ``nextUntil`` 로 과거로 · 종가는 원 종가 일봉에서 날짜로 붙이고
없는 날은 NULL · 보통주만, 토스 섹터는 쓰지 않는다 · 허용 경로(``{symbol}`` 자리) · **KRX 단독 행과 섞이면 수집 전에 거부** ·
페이로드·지도 표지가 KRX 단독 문구 대신 통합 문구.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from sectorflow import flowmap as FM
from sectorflow.basis import DATA_BASIS_INTEGRATED_SHORT, DATA_BASIS_SHORT
from sectorflow.collect import run as R
from sectorflow.collect import write
from sectorflow.collect.brokers import BROKERS, CAPABILITIES, venue_of
from sectorflow.collect.brokers import toss as T
from sectorflow.collect.brokers.http import ForbiddenPath, HttpSession
from tests.test_collect_daily import CAL, kst

REPO = Path(__file__).resolve().parent.parent
TRUTH = CAL.trading_days("2026-01-02", "2026-11-20")[0]


def _vol(n):
    return {"buyVolume": "1", "sellVolume": "1", "netBuyVolume": str(n)}


#: 확정 기록 하나 — 기관 = 세부 7개 합(기타금융 10 포함).
FINAL = {"date": "2026-11-19", "updatedAt": "2026-11-19T19:10:00+09:00", "individual": _vol(-3000),
         "foreigner": _vol(1100), "institution": {**_vol(1500), "breakdown": {
             "financialInvestment": _vol(200), "insurance": _vol(50), "trust": _vol(400),
             "privateEquityFund": _vol(100), "bank": _vol(0), "otherFinancialInstitution": _vol(10),
             "pensionFund": _vol(740)}},
         "otherCorporation": _vol(300), "foreignerHolding": None, "cfd": None}
#: 당일 잠정 기록 — 개인·기관 세부·기타법인이 null.
PROVISIONAL = {"date": "2026-11-20", "updatedAt": "2026-11-20T14:35:08+09:00", "individual": None,
               "foreigner": _vol(120), "institution": {**_vol(-113), "breakdown": None}, "otherCorporation": None,
               "foreignerHolding": None, "cfd": None}


def test_records_map_and_provisional_nulls_stay_unknown():
    candles = {"2026-11-19": (12500, 345678)}
    new, old = T.flow_rows("000120", [PROVISIONAL, FINAL], candles, "2026-11-01", "2026-11-30")
    assert old == {"code": "000120", "date": "2026-11-19", "close": 12500, "flu_rt": None, "acc_trde_qty": 345678,
                   "individual": -3000, "foreign_": 1100, "institution": 1500, "fnnc_invt": 200, "insrnc": 50,
                   "invtrt": 400, "bank": 0, "penfnd_etc": 740, "samo_fund": 100, "natn": None, "etc_corp": 300,
                   "source": "toss"}
    assert (new["individual"], new["invtrt"], new["etc_corp"], new["foreign_"]) == (None, None, None, 120)
    assert new["close"] is None, "일봉이 없는 날의 종가는 모름이다 — 0 으로 채우면 금액이 0 이 된다"


def test_capability_is_integrated_and_unverified():
    cap = CAPABILITIES["toss"]
    assert (cap.venue, cap.verified, cap.missing) == ("KRX+NXT", False, ("natn",))
    assert venue_of("toss") == "KRX+NXT" and venue_of("kiwoom") == venue_of("kis") == venue_of("ls") == "KRX"
    assert venue_of("naver") == "KRX", "등록부 밖(옛 행)은 KRX 단독으로 본다"


class FakeToss:
    def __init__(self, days, *, flow_page=100, candle_page=200, missing_candle=None):
        self.days = sorted(days, reverse=True)
        self.flow_page, self.candle_page = flow_page, candle_page
        self.missing = set(missing_candle or ())
        self.seen: list[tuple[str, str, dict]] = []

    def __call__(self, method, url, headers, body, timeout):
        path = url.split(".com", 1)[1]
        self.seen.append((method, path, dict(body)))
        if path == T.TOKEN_PATH:
            assert body["grant_type"] == "client_credentials" and body["client_id"] == "cid"
            return 200, {}, {"access_token": "TT", "token_type": "Bearer", "expires_in": 86400}
        assert headers["authorization"] == "Bearer TT"
        if path == T.LIST_PATH:
            assert (body["securityType"], body["commonShare"]) == ("STOCK", "true")
            rows = {"KOSPI": [("000120", "CJ대한통운", "STOCK", True), ("005935", "삼성전자우", "STOCK", False)],
                    "KOSDAQ": [("044490", "태웅로직스", "STOCK", True)]}[body["market"]]
            return 200, {}, {"result": [{"symbol": c, "name": n, "securityType": t, "isCommonShare": cs,
                                         "isinCode": "KR7" + c} for c, n, t, cs in rows]}
        if path == T.CANDLE_PATH:
            assert (body["interval"], body["adjusted"]) == ("1d", "false"), "원 종가 일봉"
            upto = body["before"][:10]
            days = [d for d in self.days if d <= upto and d not in self.missing][:self.candle_page]
            nxt = days[-1] if len(days) == self.candle_page else None
            return 200, {}, {"result": {"candles": [
                {"timestamp": f"{d}T00:00:00+09:00", "openPrice": "1", "highPrice": "1", "lowPrice": "1",
                 "closePrice": "12500", "volume": "1000", "currency": "KRW"} for d in days],
                "nextBefore": f"{nxt}T00:00:00+09:00" if nxt else None}}
        assert path.endswith("/investor-trading") and method == "GET"
        upto = body["until"]
        days = [d for d in self.days if d <= upto]
        page = days[:min(body["count"], self.flow_page)]
        nxt = days[len(page)] if len(days) > len(page) else None
        return 200, {}, {"result": {"nextUntil": nxt, "records": [{**FINAL, "date": d} for d in page]}}

    def flows(self):
        return [s for s in self.seen if s[1].endswith("/investor-trading")]


def _broker(fake, max_calls=None):
    http = HttpSession("toss", T.PROD_URL, allowed=T.ALLOWED, limits=T.LIMITS, rate=None, max_calls=max_calls,
                       transport=fake, sleep=lambda s: None, secrets=("cid", "csec"))
    return T.TossBroker("cid", "csec", http, wall=lambda: kst("2026-11-20 21:00").timestamp())


def test_pages_back_with_next_until_and_joins_raw_closes():
    fake = FakeToss(TRUTH, flow_page=40, candle_page=30, missing_candle={"2026-10-01"})
    info: dict = {}
    rows = _broker(fake).investor_daily("000120", "2026-07-01", "2026-11-20", days=99, info=info)
    want = [d for d in TRUTH if d >= "2026-07-01"]
    assert [r["date"] for r in rows] == sorted(want, reverse=True) and info["more"] is False
    untils = [b["until"] for _m, _p, b in fake.flows()]
    assert untils[0] == "2026-11-20" and len(untils) == 3 and untils == sorted(untils, reverse=True)
    closes = {r["date"]: r["close"] for r in rows}
    assert closes["2026-10-01"] is None and {v for d, v in closes.items() if d != "2026-10-01"} == {12500}


def test_symbol_slot_is_allowed_but_orders_are_not():
    fake = FakeToss(TRUTH)
    http = _broker(fake).http
    for bad in ("/api/v1/orders", "/api/v1/stocks/000120/investor-trading/x", "/api/v1/stocks/a/b/investor-trading"):
        with pytest.raises(ForbiddenPath):
            http.send("GET", bad, {}, {}, what="x")
    assert fake.seen == []


def test_listing_takes_common_stocks_and_never_toss_sectors(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    fake = FakeToss(TRUTH)
    monkeypatch.setitem(BROKERS, "toss", lambda s: _broker(fake, max_calls=s.max_calls))
    assert R.main(["universe", "--db", str(db), "--broker", "toss"]) == 0
    got = sqlite3.connect(db).execute("SELECT code, market, sector, sector_source FROM stocks ORDER BY code").fetchall()
    assert got == [("000120", "거래소", "", None), ("044490", "코스닥", "", None)]
    assert not any("sectors" in p for _m, p, _b in fake.seen), "토스 섹터(다중 소속)는 부르지도 않는다"


def _seed_kiwoom_rows(db):
    con = write.open_db(db)
    write.upsert_stocks(con, [{"code": "000120", "name": "CJ대한통운", "market": "거래소", "sector": "운송/창고",
                               "kind": "A", "sector_source": "kiwoom"}])
    write.upsert_supply_demand(con, [("000120", "2026-11-19", 1000, 0.0, 1, *([0] * 11), "kiwoom")])
    con.commit()
    con.close()


def test_integrated_rows_never_join_a_krx_only_sqlite(tmp_path, monkeypatch, capsys):
    db = tmp_path / "mix.db"
    _seed_kiwoom_rows(db)
    fake = FakeToss(TRUTH)
    monkeypatch.setitem(BROKERS, "toss", lambda s: _broker(fake, max_calls=s.max_calls))
    monkeypatch.setattr(R, "_now_kst", lambda: kst("2026-11-20 21:00"))
    with pytest.raises(SystemExit, match="거래소 기준이 다른"):
        R.main(["backfill", "--db", str(db), "--broker", "toss", "--map-only", "--days", "5"])
    assert fake.flows() == [], "섞인다는 걸 알면 수급을 한 번도 부르지 않는다"
    with pytest.raises(SystemExit, match="kiwoom=KRX"):
        R.main(["daily", "--db", str(db), "--broker", "toss", "--map-only"])
    sources = sqlite3.connect(db).execute("SELECT DISTINCT source FROM supply_demand").fetchall()
    assert sources == [("kiwoom",)]


def test_toss_sqlite_map_says_integrated_not_krx_only(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    fake = FakeToss(TRUTH)
    monkeypatch.setitem(BROKERS, "toss", lambda s: _broker(fake, max_calls=s.max_calls))
    monkeypatch.setattr(R, "_now_kst", lambda: kst("2026-11-20 21:00"))
    assert R.main(["universe", "--db", str(db), "--broker", "toss"]) == 0
    assert R.main(["backfill", "--db", str(db), "--broker", "toss", "--map-only", "--days", "5"]) == 0
    out = tmp_path / "p.json"
    p = subprocess.run([sys.executable, str(REPO / "scripts" / "sector_flow.py"), "--db", str(db), "--days", "5",
                        "--flows-only", "--json", str(out)], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    P = json.loads(out.read_text("utf-8"))
    assert P["venue"] == "KRX+NXT" and P["n_names"] == 2
    assert FM.build_map_data(P)["basis"] == DATA_BASIS_INTEGRATED_SHORT
    html = tmp_path / "m.html"
    p = subprocess.run([sys.executable, str(REPO / "scripts" / "sector_map.py"), "--payload", str(out),
                        "--html", str(html)], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    text = html.read_text("utf-8")
    assert DATA_BASIS_INTEGRATED_SHORT in text and DATA_BASIS_SHORT not in text


def test_a_hand_mixed_sqlite_refuses_to_build_a_payload(tmp_path):
    db = tmp_path / "m.db"
    _seed_kiwoom_rows(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO supply_demand (code, date, close, source) VALUES ('000120', '2026-11-20', 1, 'toss')")
    con.commit()
    con.close()
    p = subprocess.run([sys.executable, str(REPO / "scripts" / "sector_flow.py"), "--db", str(db), "--days", "2",
                        "--flows-only", "--json", str(tmp_path / "p.json")], cwd=REPO, capture_output=True, text=True,
                       timeout=120)
    assert p.returncode != 0 and "섞였다" in p.stderr


def test_kr_quant_style_payload_keeps_the_krx_only_label():
    P = {"dates": ["2026-11-20"], "sectors": ["가"], "markets": ["거래소"],
         "flows": {"거래소": {"가": {k: [1.0] for k in ("indiv", "forgn", "inst", "etc", "tv")}}}, "detail": {}}
    assert FM.build_map_data(P)["basis"] == DATA_BASIS_SHORT


def test_truncation_is_reported():
    fake = FakeToss(TRUTH, flow_page=3)
    info: dict = {}
    _broker(fake).investor_daily("000120", "2026-01-02", "2026-11-20", days=20, info=info)
    assert info["more"] is True and info["pages"] == len(fake.flows()) == 3
