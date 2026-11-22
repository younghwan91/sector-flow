"""한국투자(KIS) 어댑터 — 손으로 쓴 최소 표본 응답과 가짜 서버로(네트워크·실제 키 없음). **미검증 어댑터다.**

표본은 공개 포털 메타와 공식 GitHub 예제의 필드 이름표(``docs/byo/brokers.md`` §2.2)를 보고 손으로 썼다 — 벤더 응답을 복사하지
않았다. 지키는 것: 필드 대응(증권→금융투자, 기금→연기금, 국가 결측 — 기타·기타단체를 국가로 넣지 않는다) · 빈 칸은 0 이 아니라
NULL · KRX 단독(``J``) · 연속조회 두 길(헤더 ``tr_cont``, 날짜 옮기기) · 잘림 보고 · 속도 제한 재시도와 토큰 한 번 재발급 ·
허용 경로 밖은 요청 전에 막힌다 · 기본 속도 5·상한 18 · 실전 키 경고 · 목록 API 가 없어 저장된 ``stocks`` 를 쓴다.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, timedelta

import pytest

from sectorflow.collect import run as R
from sectorflow.collect import write
from sectorflow.collect.brokers import BROKERS, CAPABILITIES, Settings
from sectorflow.collect.brokers import kis as KIS
from sectorflow.collect.brokers.http import ForbiddenPath, HttpSession
from sectorflow.collect.kiwoom import CallBudgetExceeded
from tests.test_collect_daily import CAL, kst

#: 손 표본 한 줄 — 공개 필드 이름표의 순매수 칸. 대금·매도·매수 칸은 어댑터가 읽지 않아 뺐다.
SAMPLE = {"stck_bsop_date": "20261120", "stck_clpr": "-12500", "prdy_ctrt": "-1.20", "acml_vol": "345678",
          "prsn_ntby_qty": "-3000", "frgn_ntby_qty": "1200", "frgn_reg_ntby_qty": "1100", "frgn_nreg_ntby_qty": "100",
          "orgn_ntby_qty": "1500", "scrt_ntby_qty": "200", "ivtr_ntby_qty": "400", "pe_fund_ntby_vol": "100",
          "bank_ntby_qty": "0", "insu_ntby_qty": "50", "mrbn_ntby_qty": "10", "fund_ntby_qty": "740",
          "etc_ntby_qty": "5", "etc_corp_ntby_vol": "300", "etc_orgt_ntby_vol": "7"}


def test_sample_row_maps_to_supply_demand():
    (row,) = KIS.flow_rows("000120", [SAMPLE], "2026-11-01", "2026-11-30")
    assert row == {"code": "000120", "date": "2026-11-20", "close": 12500, "flu_rt": None, "acc_trde_qty": 345678,
                   "individual": -3000, "foreign_": 1200, "institution": 1500, "fnnc_invt": 200, "insrnc": 50,
                   "invtrt": 400, "bank": 0, "penfnd_etc": 740, "samo_fund": 100,
                   "natn": None,               # 국가 칸이 없다 — 기타(5)·기타단체(7)를 국가로 넣지 않는다
                   "etc_corp": 300, "source": "kis"}


def test_blank_fields_are_unknown_not_zero():
    (row,) = KIS.flow_rows("000120", [{**SAMPLE, "fund_ntby_qty": "", "etc_corp_ntby_vol": None}], "2026-11-20",
                           "2026-11-20")
    assert row["penfnd_etc"] is None and row["etc_corp"] is None and row["bank"] == 0


def test_capability_is_unverified_krx_without_natn():
    cap = CAPABILITIES["kis"]
    assert (cap.venue, cap.verified, cap.missing) == ("KRX", False, ("natn",))
    assert "kis" in BROKERS and "모의투자" in cap.terms_note


class FakeKis:
    """가짜 KIS — 토큰 발급과 일별 투자자 TR. ``FID_INPUT_DATE_1`` 이하 거래일을 최신부터 ``page`` 행씩.

    ``server_cont`` 면 응답 헤더 ``tr_cont: M`` 으로 다음 쪽을 알리고 요청 헤더 ``tr_cont: N`` 을 받는다(날짜는 그대로).
    """

    def __init__(self, days, *, page=30, server_cont=False, rate_errors=0, expire_once=False):
        self.days = sorted(days, reverse=True)
        self.page, self.server_cont = page, server_cont
        self.rate_errors, self.expire_once = rate_errors, expire_once
        self.seen: list[tuple[str, str, dict, dict]] = []
        self.tokens = 0

    def __call__(self, method, url, headers, body, timeout):
        path = url.split(":9443", 1)[1]
        self.seen.append((method, path, dict(headers), dict(body)))
        if path == KIS.TOKEN_PATH:
            self.tokens += 1
            return 200, {}, {"access_token": f"T{self.tokens}", "token_type": "Bearer", "expires_in": 86400,
                             "access_token_token_expired": "2026-11-21 21:00:00"}
        assert method == "GET" and headers["tr_id"] == KIS.FLOW_TR and headers["custtype"] == "P"
        assert body["FID_COND_MRKT_DIV_CODE"] == "J", "KRX 단독"
        if self.rate_errors:
            self.rate_errors -= 1
            return 500, {}, {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}
        if self.expire_once and headers["authorization"] == "Bearer T1":
            return 500, {}, {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}
        anchor = body["FID_INPUT_DATE_1"]
        days = [d for d in self.days if d.replace("-", "") <= anchor]
        off = 0
        if self.server_cont and headers["tr_cont"] == "N":
            off = self.cursor
        rows = days[off:off + self.page]
        hdr = {}
        if self.server_cont and off + self.page < len(days):
            self.cursor = off + self.page
            hdr = {"tr_cont": "M"}
        return 200, hdr, {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리 되었습니다.",
                          "output1": {"stck_prpr": "12500"},
                          "output2": [{**SAMPLE, "stck_bsop_date": d.replace("-", "")} for d in rows]}

    def flows(self):
        return [s for s in self.seen if s[1] == KIS.FLOW_PATH]


def _broker(fake, max_calls=None) -> KIS.KisBroker:
    http = HttpSession("kis", KIS.PROD_URL, allowed=KIS.ALLOWED, limits=KIS.LIMITS, rate=None, max_calls=max_calls,
                       transport=fake, sleep=lambda s: None, secrets=("appk", "apps"))
    return KIS.KisBroker("appk", "apps", http, wall=lambda: kst("2026-11-20 21:00").timestamp())


TRUTH = CAL.trading_days("2026-01-02", "2026-11-20")[0]


def test_walks_back_by_date_until_the_window_is_covered():
    fake = FakeKis(TRUTH)
    info: dict = {}
    rows = _broker(fake).investor_daily("000120", "2026-07-01", "2026-11-20", days=99, info=info)
    want = [d for d in TRUTH if d >= "2026-07-01"]
    assert [r["date"] for r in rows] == sorted(want, reverse=True)
    anchors = [b["FID_INPUT_DATE_1"] for _m, _p, _h, b in fake.flows()]
    assert anchors[0] == "20261120" and len(anchors) == 4             # 30행씩 — 99거래일은 4번
    for prev, nxt in zip(anchors, anchors[1:]):
        assert nxt < prev, "다음 쪽은 받은 가장 오래된 날의 앞날부터"
    assert info == {"pages": 4, "more": False}
    assert fake.tokens == 1


def test_follows_the_server_continuation_header():
    fake = FakeKis(TRUTH, page=50, server_cont=True)
    rows = _broker(fake).investor_daily("000120", "2026-08-01", "2026-11-20", days=80)
    assert len(rows) == len([d for d in TRUTH if d >= "2026-08-01"])
    conts = [h["tr_cont"] for _m, _p, h, _b in fake.flows()]
    assert conts == ["", "N"] and {b["FID_INPUT_DATE_1"] for *_x, b in fake.flows()} == {"20261120"}


def test_truncation_is_reported_not_silently_short():
    fake = FakeKis(TRUTH, page=5)
    info: dict = {}
    rows = _broker(fake).investor_daily("000120", "2026-01-02", "2026-11-20", days=30, info=info)
    assert info["more"] is True and info["pages"] == len(fake.flows()) == math.ceil(30 / KIS.PAGE_ROWS_GUESS) + 2
    assert min(r["date"] for r in rows) > "2026-01-02"


def test_short_history_stops_without_truncation():
    listed = [d for d in TRUTH if d >= "2026-11-02"]                  # 11월 상장 — 그 앞 이력이 없다
    fake = FakeKis(listed)
    info: dict = {}
    rows = _broker(fake).investor_daily("000120", "2026-06-01", "2026-11-20", days=120, info=info)
    assert len(rows) == len(listed) and info["more"] is False


def test_rate_limit_is_retried_and_an_expired_token_reissued_once():
    fake = FakeKis(TRUTH, rate_errors=2, expire_once=True)
    b = _broker(fake)
    rows = b.investor_daily("000120", "2026-11-16", "2026-11-20", days=5)
    assert len(rows) == 5 and b.retries[KIS.FLOW_TR] == 2 and b.reissues == 1 and fake.tokens == 2


def test_errors_and_messages_do_not_leak_keys():
    def bad(method, url, headers, body, timeout):
        if url.endswith(KIS.TOKEN_PATH):
            return 403, {}, {"error_code": "EGW00103", "error_description": "유효하지 않은 AppKey appk 입니다."}
        raise AssertionError
    with pytest.raises(Exception) as e:
        _broker(bad).investor_daily("000120", "2026-11-20", "2026-11-20", days=1)
    assert "appk" not in str(e.value) and "EGW00103" in str(e.value)
    assert "appk" not in repr(_broker(FakeKis(TRUTH)))


def test_paths_outside_the_allow_list_never_leave():
    fake = FakeKis(TRUTH)
    http = _broker(fake).http
    with pytest.raises(ForbiddenPath):
        http.send("POST", "/uapi/domestic-stock/v1/trading/order-cash", {}, {}, what="order")
    assert fake.seen == []
    for name in BROKERS:
        mod = __import__(f"sectorflow.collect.brokers.{name}", fromlist=["ALLOWED"])
        for _m, path in getattr(mod, "ALLOWED", ()):
            segs = path.lower().strip("/").split("/")
            assert not any(any(w in seg for w in ("order", "ordr", "acnt", "account", "balance", "holding",
                                                  "buying", "sellable", "transaction"))
                           or seg == "trading" for seg in segs), (name, path)


def test_call_budget_counts_the_token_and_stops_before_sending():
    fake = FakeKis(TRUTH)
    with pytest.raises(CallBudgetExceeded):
        _broker(fake, max_calls=2).investor_daily("000120", "2026-06-01", "2026-11-20", days=120)
    assert len(fake.seen) == 2


def test_make_warns_about_live_keys_and_stays_under_the_published_cap(monkeypatch):
    monkeypatch.setenv(KIS.KEY_ENV, "appk")
    monkeypatch.setenv(KIS.SECRET_ENV, "apps")
    said: list[str] = []
    b = KIS.make(Settings(notice=said.append))
    assert "모의투자" in said[0] and b.http.min_interval == pytest.approx(1 / KIS.DEFAULT_RATE)
    assert KIS.make(Settings(rate=100.0)).http.min_interval == pytest.approx(1 / 18)
    assert b.http.limits == ((18, 1.0),)


# ── CLI 끝까지 ─────────────────────────────────────────────────────────────
def _patch(monkeypatch, fake, now="2026-11-20 21:00"):
    monkeypatch.setitem(BROKERS, "kis", lambda s: _broker(fake, max_calls=s.max_calls))
    monkeypatch.setattr(R, "_now_kst", lambda: kst(now))


def _seed_listing(db):
    con = write.open_db(db)
    write.upsert_stocks(con, [{"code": c, "name": n, "market": m, "sector": sec, "kind": "A", "sector_source": "kiwoom"}
                              for c, n, m, sec in (("000120", "CJ대한통운", "거래소", "운송/창고"),
                                                   ("044490", "태웅로직스", "코스닥", "운송/창고"),
                                                   ("005930", "삼성전자", "거래소", "전기/전자"))])
    con.commit()
    con.close()


def test_backfill_with_kis_uses_the_stored_listing(tmp_path, monkeypatch):
    db = tmp_path / "k.db"
    _seed_listing(db)
    fake = FakeKis(TRUTH)
    _patch(monkeypatch, fake)
    assert R.main(["backfill", "--db", str(db), "--broker", "kis", "--map-only", "--sectors", "운송/창고",
                   "--days", "30"]) == 0
    con = sqlite3.connect(db)
    assert {b["FID_INPUT_ISCD"] for *_x, b in fake.flows()} == {"000120", "044490"}
    rows = con.execute("SELECT source, count(*), count(natn), count(penfnd_etc) FROM supply_demand GROUP BY source")
    assert rows.fetchall() == [("kis", 60, 0, 60)], "국가는 NULL(모름), 나머지는 찬다"
    first = (date.fromisoformat(TRUTH[-30]))
    assert con.execute("SELECT min(date) FROM supply_demand").fetchone()[0] == first.isoformat()
    kind, calls = con.execute("SELECT kind, calls FROM collect_runs").fetchone()
    assert kind == "backfill-map" and set(json.loads(calls)) == {"token", KIS.FLOW_TR}
    assert dict(con.execute("SELECT code, sector_source FROM stocks")) == {
        "000120": "kiwoom", "044490": "kiwoom", "005930": "kiwoom"}, "키움 업종을 그대로 둔다"


def test_daily_with_kis_adds_the_next_day(tmp_path, monkeypatch):
    db = tmp_path / "k.db"
    _seed_listing(db)
    _patch(monkeypatch, FakeKis(TRUTH))
    assert R.main(["backfill", "--db", str(db), "--broker", "kis", "--map-only", "--codes", "000120",
                   "--days", "10"]) == 0
    nxt = CAL.trading_days("2026-11-21", "2026-11-30")[0][0]
    fake = FakeKis(CAL.trading_days("2026-01-02", nxt)[0])
    _patch(monkeypatch, fake, now=f"{nxt} 21:00")
    assert R.main(["daily", "--db", str(db), "--broker", "kis", "--map-only"]) == 0
    con = sqlite3.connect(db)
    assert con.execute("SELECT max(date), count(*) FROM supply_demand WHERE code = '000120'").fetchone() == (nxt, 11)
    # 같은 섹터의 저장된 다른 종목(044490)은 신규 상장처럼 따라 받는다 — 키움 daily 와 같은 규칙
    assert sorted(b["FID_INPUT_ISCD"] for *_x, b in fake.flows()) == ["000120", "044490"]


def test_kis_needs_a_stored_listing_and_has_no_universe(tmp_path, monkeypatch):
    _patch(monkeypatch, FakeKis(TRUTH))
    with pytest.raises(SystemExit, match="universe --broker kiwoom"):
        R.main(["backfill", "--db", str(tmp_path / "e.db"), "--broker", "kis", "--map-only"])
    with pytest.raises(SystemExit, match="목록 API 가 없다"):
        R.main(["universe", "--db", str(tmp_path / "e.db"), "--broker", "kis"])
    with pytest.raises(SystemExit, match="--map-only"):
        R.main(["backfill", "--db", str(tmp_path / "e.db"), "--broker", "kis"])


def test_sectors_add_missing_builds_a_listing_from_a_krx_download(tmp_path):
    csv = tmp_path / "krx.csv"
    csv.write_bytes(("종목코드,종목명,시장구분,업종명\n000120,CJ대한통운,KOSPI,운수창고업\n044490,태웅로직스,KOSDAQ,운송\n"
                     "005935,삼성전자우,KOSPI,전기전자\n123456,코넥스사,KONEX,기타\n").encode("cp949"))
    db = tmp_path / "s.db"
    assert R.main(["sectors", "--db", str(db), "--csv", str(csv), "--add-missing"]) == 0
    con = sqlite3.connect(db)
    got = con.execute("SELECT code, name, market, sector, sector_source FROM stocks ORDER BY code").fetchall()
    assert got == [("000120", "CJ대한통운", "거래소", "운수창고업", "csv:krx"),
                   ("044490", "태웅로직스", "코스닥", "운송", "csv:krx")], "우선주·코넥스는 넣지 않는다"


def test_date_walk_steps_one_calendar_day_before_the_oldest():
    fake = FakeKis([d for d in TRUTH if d <= "2026-11-20"], page=3)
    _broker(fake).investor_daily("000120", "2026-11-01", "2026-11-20", days=14)
    anchors = [b["FID_INPUT_DATE_1"] for *_x, b in fake.flows()]
    got = [d for d in TRUTH if d <= "2026-11-20"][::-1]
    assert anchors[1] == (date.fromisoformat(got[2]) - timedelta(days=1)).strftime("%Y%m%d")


def test_a_token_rejected_twice_stops_instead_of_looping():
    class Always(FakeKis):
        def __call__(self, method, url, headers, body, timeout):
            if url.endswith(KIS.FLOW_PATH):
                self.seen.append((method, KIS.FLOW_PATH, dict(headers), dict(body)))
                return 500, {}, {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}
            return super().__call__(method, url, headers, body, timeout)
    fake = Always(TRUTH)
    b = _broker(fake)
    with pytest.raises(Exception, match="EGW00123"):
        b.investor_daily("000120", "2026-11-20", "2026-11-20", days=1)
    assert b.reissues == 1 and fake.tokens == 2 and len(fake.flows()) == 2


def test_http_session_spaces_requests_at_the_rate():
    t = {"now": 0.0}
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        t["now"] += s
    http = HttpSession("x", "https://h", allowed=frozenset({("GET", "/a")}), limits=((18, 1.0),), rate=5.0,
                       transport=lambda *a: (200, {}, {}), clock=lambda: t["now"], sleep=sleep)
    for _ in range(3):
        http.send("GET", "/a", {}, {}, what="a")
    assert slept == [pytest.approx(0.2), pytest.approx(0.2)]


def test_mask_hides_kis_and_toss_style_secret_fields():
    from sectorflow.collect.auth import mask
    out = mask('{"appkey": "AK1", "appsecret": "AS1", "client_secret": "CS1", "access_token": "TK1"}')
    assert not any(s in out for s in ("AK1", "AS1", "CS1", "TK1"))


def test_kis_sqlite_builds_a_flows_only_payload(tmp_path, monkeypatch):
    """키움 밖 어댑터의 행(source=kis)도 수급만 페이로드가 읽는다 — 키움 소스만 읽으면 지도가 빈다."""
    import subprocess
    import sys
    from pathlib import Path
    db = tmp_path / "k.db"
    _seed_listing(db)
    _patch(monkeypatch, FakeKis(TRUTH))
    assert R.main(["backfill", "--db", str(db), "--broker", "kis", "--map-only", "--sectors", "운송/창고",
                   "--days", "10"]) == 0
    repo = Path(__file__).resolve().parent.parent
    out = tmp_path / "p.json"
    p = subprocess.run([sys.executable, str(repo / "scripts" / "sector_flow.py"), "--db", str(db), "--days", "10",
                        "--flows-only", "--json", str(out)], cwd=repo, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    P = json.loads(out.read_text("utf-8"))
    assert P["n_names"] == 2 and P["dates"][-1] == "2026-11-20"
    # 기관 1500주 × 종가 12500원 / 1억 = 0.19억 — 그날 그 섹터(종목 하나) 기관 칸
    assert P["flows"]["거래소"]["운송/창고"]["inst"][-1] == round(1500 * 12500 / 1e8, 2)
