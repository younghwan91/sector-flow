"""``sector-flow-collect universe`` — ka10099 두 시장 → ``stocks``(업종 포함), 가짜 전송으로(네트워크 없음).

지키는 것: 거래소·코스닥 둘 다 받는다 · 연속조회(응답 헤더 ``cont-yn``·``next-key``)로 쪽을 이어 붙인다 · 보통주만 ·
업종(``upName``)이 ``stocks.sector`` 에 실린다 · ka10099 말고는 부르지 않는다 · 쪽 상한에서 잘린 목록을 성공으로
넘기지 않는다(일부 시장만 담긴 유니버스는 섹터 합계를 조용히 줄인다) · 운영 창이면 요청 전에 멈춘다.
"""

from __future__ import annotations

import json
import sqlite3

from sectorflow.collect import fetch as F
from sectorflow.collect import kiwoom as K
from sectorflow.collect import run as R

KOSPI_P1 = [("000120", "CJ대한통운", "운송/창고"), ("005930", "삼성전자", "전기/전자"),
            ("005935", "삼성전자우", "전기/전자")]           # 우선주 — 빠진다
KOSPI_P2 = [("000660", "SK하이닉스", "전기/전자")]
KOSDAQ = [("044490", "태웅로직스", "운송/창고"), ("035900", "JYP Ent.", "오락/문화")]


class FakeList:
    """ka10099 만 아는 가짜 서버. 코스피는 두 쪽(헤더 연속조회), 코스닥은 한 쪽. ``endless`` 면 늘 '더 있다'."""

    def __init__(self, endless: bool = False):
        self.seen: list[tuple[str, str, str]] = []
        self.endless = endless

    def __call__(self, url, headers, body, timeout):
        if url.endswith(K.TOKEN_PATH):
            return 200, {}, {"token": "t", "expires_in": 86400}
        tr = headers["api-id"]
        assert tr == "ka10099", tr
        self.seen.append((body["mrkt_tp"], headers["cont-yn"], headers["next-key"]))

        def page(rows, market):
            return {"return_code": 0, "list": [{"code": c, "name": n, "marketName": market, "upName": u,
                                                "kind": "A"} for c, n, u in rows]}
        if self.endless:
            return 200, {"cont-yn": "Y", "next-key": "more"}, page(KOSPI_P1, "거래소")
        if body["mrkt_tp"] == "0":
            if headers["cont-yn"] == "Y" and headers["next-key"] == "p2":
                return 200, {"cont-yn": "N"}, page(KOSPI_P2, "거래소")
            return 200, {"cont-yn": "Y", "next-key": "p2"}, page(KOSPI_P1, "거래소")
        return 200, {"cont-yn": "N"}, page(KOSDAQ, "코스닥")


def _patch(monkeypatch, fake, guard=None):
    monkeypatch.setattr(R, "_client", lambda args: K.KiwoomRest(
        "k", "s", transport=fake, rate=None, max_calls=args.max_calls, sleep=lambda s: None, guard=guard))


def test_universe_writes_both_markets_with_sectors_across_pages(tmp_path, monkeypatch):
    fake = FakeList()
    _patch(monkeypatch, fake)
    db = tmp_path / "u.db"
    assert R.main(["universe", "--db", str(db)]) == 0
    con = sqlite3.connect(db)
    got = con.execute("SELECT code, name, market, sector FROM stocks ORDER BY code").fetchall()
    assert got == [("000120", "CJ대한통운", "거래소", "운송/창고"), ("000660", "SK하이닉스", "거래소", "전기/전자"),
                   ("005930", "삼성전자", "거래소", "전기/전자"), ("035900", "JYP Ent.", "코스닥", "오락/문화"),
                   ("044490", "태웅로직스", "코스닥", "운송/창고")]
    # 두 시장 × 첫 쪽 + 코스피 둘째 쪽(앞 응답 헤더의 next-key 로)
    assert fake.seen == [("0", "N", ""), ("0", "Y", "p2"), ("10", "N", "")]
    kind, ok, calls, rows = con.execute("SELECT kind, ok, calls, rows FROM collect_runs").fetchone()
    assert (kind, ok) == ("universe", 1)
    assert json.loads(calls) == {"/oauth2/token": 1, "ka10099": 3}
    assert json.loads(rows) == {"stocks": 5}
    # 다시 돌려도 같다(멱등 upsert)
    assert R.main(["universe", "--db", str(db)]) == 0
    assert con.execute("SELECT count(*) FROM stocks").fetchone() == (5,)


def test_universe_truncated_list_is_a_failure_not_a_smaller_market(tmp_path, monkeypatch):
    _patch(monkeypatch, FakeList(endless=True))
    db = tmp_path / "u.db"
    assert R.main(["universe", "--db", str(db)]) == 1
    con = sqlite3.connect(db)
    assert con.execute("SELECT count(*) FROM stocks").fetchone() == (0,)      # 반쪽 목록을 쓰지 않는다
    ok, failed = con.execute("SELECT ok, failed FROM collect_runs").fetchone()
    assert ok == 0 and "잘" in failed and "ka10099" in failed


def test_stock_list_raises_on_truncation():
    api = K.KiwoomRest("k", "s", transport=FakeList(endless=True), rate=None, sleep=lambda s: None)
    try:
        F.stock_list(api, "kospi", max_pages=3)
    except F.TruncatedResponse as e:
        assert "3쪽" in str(e)
    else:
        raise AssertionError("잘린 목록이 통과했다")


def test_universe_stops_before_sending_in_a_busy_window(tmp_path, monkeypatch):
    fake = FakeList()

    def guard():
        raise K.BusyWindow("운영 창")
    _patch(monkeypatch, fake, guard)
    db = tmp_path / "u.db"
    assert R.main(["universe", "--db", str(db)]) == R.EXIT_BUSY
    assert fake.seen == []
    con = sqlite3.connect(db)
    assert con.execute("SELECT count(*) FROM stocks").fetchone() == (0,)
    assert "busy" in con.execute("SELECT notes FROM collect_runs").fetchone()[0]
