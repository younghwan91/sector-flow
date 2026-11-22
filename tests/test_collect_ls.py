"""LS증권 어댑터 — 손으로 쓴 최소 표본 응답과 가짜 서버로(네트워크·실제 키 없음). **미검증 어댑터다.**

표본은 포털 API 메타의 응답 예시와 블록 필드 목록(``docs/byo/brokers.md`` §2.3)을 보고 손으로 썼다(값은 지어낸 것).
지키는 것: 약관 경고를 찍고 ``--i-understand-ls-terms`` 없이는 요청 전에 멈춘다 · 1:1 대응(국가외→국가, 외인계→외국인) ·
수량·순매수·일간·KRX 단독(``K``) · 토큰은 폼으로 · 연속조회 두 길 · 초당 1건 · 종목 목록은 보통주만, 섹터는 저장된 것을 둔다.
"""

from __future__ import annotations

import sqlite3
from urllib.parse import parse_qs

import pytest

from sectorflow.collect import run as R
from sectorflow.collect import write
from sectorflow.collect.brokers import BROKERS, CAPABILITIES, Settings
from sectorflow.collect.brokers import ls as LS
from sectorflow.collect.brokers.http import HttpSession, is_form
from sectorflow.storage import INVESTOR_COLUMNS
from tests.test_collect_daily import CAL, kst

#: 손 표본 한 줄 — 순매수 수량(``volvalgb=1``·``msmdgb=0``). 기관 = 사모+증권+보험+투신+은행+종금+기금.
SAMPLE = {"date": "20261120", "close": 12500, "sign": "5", "change": -150, "diff": "-1.19", "volume": 345678,
          "tjj0000": 100, "tjj0001": 200, "tjj0002": 50, "tjj0003": 400, "tjj0004": 0, "tjj0005": 10,
          "tjj0006": 740, "tjj0007": 300, "tjj0008": -3000, "tjj0009": 1100, "tjj0010": 100, "tjj0011": 3,
          "tjj0018": 1500, "tjj0016": 1200, "tjj0017": 303, "value": 4321}
TRUTH = CAL.trading_days("2026-01-02", "2026-11-20")[0]


def test_sample_row_maps_one_to_one():
    (row,) = LS.flow_rows("000120", [SAMPLE], "2026-11-01", "2026-11-30")
    assert row == {"code": "000120", "date": "2026-11-20", "close": 12500, "flu_rt": None, "acc_trde_qty": 345678,
                   "individual": -3000, "foreign_": 1200, "institution": 1500, "fnnc_invt": 200, "insrnc": 50,
                   "invtrt": 400, "bank": 0, "penfnd_etc": 740, "samo_fund": 100, "natn": 3, "etc_corp": 300,
                   "source": "ls"}
    assert CAPABILITIES["ls"].actors == frozenset(INVESTOR_COLUMNS) and CAPABILITIES["ls"].verified is False


class FakeLs:
    def __init__(self, days, *, page=20, server_cont=False):
        self.days = sorted(days, reverse=True)
        self.page, self.server_cont = page, server_cont
        self.seen: list[tuple[str, dict, dict]] = []

    def __call__(self, method, url, headers, body, timeout):
        assert method == "POST"
        path = url.split(":8080", 1)[1]
        self.seen.append((path, dict(headers), body))
        if path == LS.TOKEN_PATH:
            assert is_form(headers) and body["scope"] == "oob" and body["grant_type"] == "client_credentials"
            return 200, {}, {"access_token": "LT", "token_type": "Bearer", "expires_in": 86400, "scope": "oob"}
        if path == LS.LIST_PATH:
            assert headers["tr_cd"] == "t8436"
            g = body["t8436InBlock"]["gubun"]
            rows = {"1": [("000120", "CJ대한통운", "0"), ("069500", "KODEX 200", "1"), ("005935", "삼성전자우", "0")],
                    "2": [("044490", "태웅로직스", "0")]}[g]
            return 200, {}, {"rsp_cd": "00000", "rsp_msg": "정상적으로 조회가 완료되었습니다.", "t8436OutBlock": [
                {"shcode": c, "hname": n, "gubun": g, "etfgubun": e, "spac_gubun": "N"} for c, n, e in rows]}
        assert path == LS.FLOW_PATH and headers["tr_cd"] == "t1702"
        ib = body["t1702InBlock"]
        assert (ib["volvalgb"], ib["msmdgb"], ib["gubun"], ib["exchgubun"]) == ("1", "0", "0", "K")
        days = [d for d in self.days if ib["fromdt"] <= d.replace("-", "") <= ib["todt"]]
        off = int(headers["tr_cont_key"] or 0) if headers["tr_cont"] == "Y" else 0
        rows = days[off:off + self.page]
        hdr = {"tr_cont": "Y", "tr_cont_key": str(off + self.page)} if self.server_cont and off + self.page < len(days) \
            else {"tr_cont": "N"}
        return 200, hdr, {"rsp_cd": "00000", "rsp_msg": "정상적으로 조회가 완료되었습니다.",
                          "t1702OutBlock1": [{**SAMPLE, "date": d.replace("-", "")} for d in rows]}

    def flows(self):
        return [s for s in self.seen if s[0] == LS.FLOW_PATH]


def _broker(fake, max_calls=None):
    http = HttpSession("ls", LS.PROD_URL, allowed=LS.ALLOWED, limits=LS.LIMITS, rate=None, max_calls=max_calls,
                       transport=fake, sleep=lambda s: None, secrets=("lk", "ls"))
    return LS.LsBroker("lk", "ls", http, wall=lambda: kst("2026-11-20 21:00").timestamp())


def test_terms_warning_is_printed_and_nothing_runs_without_the_flag(monkeypatch):
    monkeypatch.setenv(LS.KEY_ENV, "lk")
    monkeypatch.setenv(LS.SECRET_ENV, "ls")
    said: list[str] = []
    with pytest.raises(LS.TermsNotAccepted, match="--i-understand-ls-terms"):
        LS.make(Settings(notice=said.append, transport=lambda *a: pytest.fail("요청을 보냈다")))
    assert LS.TERMS_CLAUSE in said[0] and "20회" in said[0]
    said.clear()
    b = LS.make(Settings(notice=said.append, accepted_terms=frozenset({"ls"})))
    assert LS.TERMS_CLAUSE in said[0], "받아들였어도 경고는 찍는다"
    assert b.http.min_interval == pytest.approx(1.0) and b.http.limits == ((1, 1.0),)
    assert LS.make(Settings(rate=5.0, accepted_terms=frozenset({"ls"}))).http.min_interval == pytest.approx(1.0)


def test_cli_stops_before_any_request_without_the_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(LS.KEY_ENV, "lk")
    monkeypatch.setenv(LS.SECRET_ENV, "ls")
    monkeypatch.setattr("sectorflow.collect.brokers.http.urllib_transport", lambda *a: pytest.fail("요청을 보냈다"))
    with pytest.raises(SystemExit, match="--i-understand-ls-terms"):
        R.main(["universe", "--db", str(tmp_path / "l.db"), "--broker", "ls"])
    assert LS.TERMS_CLAUSE in capsys.readouterr().err
    assert not (tmp_path / "l.db").exists(), "약관을 받아들이기 전에는 sqlite 도 만들지 않는다"


def test_walks_back_with_todt_until_fromdt():
    fake = FakeLs(TRUTH)
    info: dict = {}
    rows = _broker(fake).investor_daily("000120", "2026-08-03", "2026-11-20", days=78, info=info)
    assert [r["date"] for r in rows] == sorted([d for d in TRUTH if d >= "2026-08-03"], reverse=True)
    blocks = [b["t1702InBlock"] for _p, _h, b in fake.flows()]
    assert {b["fromdt"] for b in blocks} == {"20260803"} and blocks[0]["todt"] == "20261120"
    assert all(n["todt"] < p["todt"] for p, n in zip(blocks, blocks[1:])) and info["more"] is False


def test_follows_the_continuation_header():
    fake = FakeLs(TRUTH, page=30, server_cont=True)
    rows = _broker(fake).investor_daily("000120", "2026-08-03", "2026-11-20", days=78)
    assert len(rows) == len([d for d in TRUTH if d >= "2026-08-03"])
    heads = [(h["tr_cont"], h["tr_cont_key"]) for _p, h, _b in fake.flows()]
    assert heads == [("N", ""), ("Y", "30"), ("Y", "60")]


def test_listing_keeps_common_stocks_and_leaves_sectors_to_the_stored_set(tmp_path, monkeypatch):
    db = tmp_path / "l.db"
    con = write.open_db(db)
    write.upsert_stocks(con, [{"code": "000120", "name": "옛", "market": "거래소", "sector": "운송/창고", "kind": "A",
                               "sector_source": "kiwoom"}])
    con.commit()
    con.close()
    fake = FakeLs(TRUTH)
    monkeypatch.setitem(BROKERS, "ls", lambda s: _broker(fake, max_calls=s.max_calls))
    assert R.main(["universe", "--db", str(db), "--broker", "ls", "--i-understand-ls-terms"]) == 0
    got = sqlite3.connect(db).execute("SELECT code, name, market, sector, sector_source FROM stocks ORDER BY code")
    assert got.fetchall() == [("000120", "CJ대한통운", "거래소", "운송/창고", "kiwoom"),
                              ("044490", "태웅로직스", "코스닥", "", None)], "ETF·우선주는 빠지고 키움 업종은 남는다"


def test_backfill_with_ls_fills_all_eleven_actors(tmp_path, monkeypatch):
    db = tmp_path / "l.db"
    fake = FakeLs(TRUTH)
    monkeypatch.setitem(BROKERS, "ls", lambda s: _broker(fake, max_calls=s.max_calls))
    monkeypatch.setattr(R, "_now_kst", lambda: kst("2026-11-20 21:00"))
    assert R.main(["backfill", "--db", str(db), "--broker", "ls", "--i-understand-ls-terms", "--map-only",
                   "--codes", "000120", "--days", "25"]) == 0
    con = sqlite3.connect(db)
    q = "SELECT count(*), " + ", ".join(f"count({c})" for c in INVESTOR_COLUMNS) + " FROM supply_demand"
    assert con.execute(q).fetchone() == (25,) * 12
    assert con.execute("SELECT DISTINCT source FROM supply_demand").fetchall() == [("ls",)]


def test_default_transport_sends_the_token_request_as_a_form(monkeypatch):
    import io
    import urllib.request

    from sectorflow.collect.brokers import http as H
    sent = {}

    class Resp(io.BytesIO):
        status, headers = 200, {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        sent.update(url=req.full_url, data=req.data, method=req.get_method())
        return Resp(b'{"access_token": "x"}')
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    H.urllib_transport("POST", "https://h/oauth2/token", {"content-type": "application/x-www-form-urlencoded"},
                       {"grant_type": "client_credentials", "scope": "oob"}, 5)
    assert parse_qs(sent["data"].decode()) == {"grant_type": ["client_credentials"], "scope": ["oob"]}
    H.urllib_transport("GET", "https://h/q", {}, {"a": "1"}, 5)
    assert sent["url"] == "https://h/q?a=1" and sent["data"] is None and sent["method"] == "GET"
    H.urllib_transport("POST", "https://h/p", {"content-type": "application/json"}, {"a": 1}, 5)
    assert sent["data"] == b'{"a": 1}'
