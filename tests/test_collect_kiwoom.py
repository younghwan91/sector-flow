"""BYO 키움 클라이언트 — 네트워크 없이 가짜 전송으로.

지키는 것: 허용 목록 밖 TR(주문 포함)은 요청 전에 막힌다 · 무효 토큰(HTTP 200 + return_code 3)에서 한 번만
재발급한다 · 429·return_code 5 는 백오프 재시도 · 연속조회 키는 **응답 헤더**에서 읽는다 · TR별 속도 제한 ·
호출 상한 · 토큰·키가 repr·오류 메시지에 남지 않는다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sectorflow.collect import kiwoom as K

REPO = Path(__file__).resolve().parent.parent
FAKE_KEY, FAKE_SECRET = "fake-app-key-0001", "fake-secret-0002"  # allowlist-secret


class Fake:
    """``(status, headers, body)`` 를 차례로 돌려주는 전송. 토큰 요청은 따로 센다."""

    def __init__(self, replies=(), token_replies=None):
        self.replies = list(replies)
        self.token_replies = list(token_replies or [])
        self.sent: list[tuple[str, dict, dict]] = []
        self.tokens = 0

    def __call__(self, url, headers, body, timeout):
        if url.endswith(K.TOKEN_PATH):
            self.tokens += 1
            if self.token_replies:
                return self.token_replies.pop(0)
            return 200, {}, {"token": f"tok{self.tokens}", "expires_in": 86400, "return_code": 0}
        self.sent.append((url, dict(headers), dict(body)))
        return self.replies.pop(0)


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def _api(fake, clock=None, **kw):
    clock = clock or Clock()
    return K.KiwoomRest(FAKE_KEY, FAKE_SECRET, transport=fake, clock=clock.now, sleep=clock.sleep,
                        wall=clock.now, **kw), clock


def ok(body=None, **headers):
    return 200, headers, {"return_code": 0, **(body or {})}


def test_order_and_unknown_trs_are_refused_before_sending():
    fake = Fake()
    api, _ = _api(fake)
    for tr in ("kt10000", "kt10001", "ka10075", "au10001"):   # 매수주문·매도주문·미체결·토큰폐기
        with pytest.raises(K.ForbiddenTR):
            api.request(tr, {})
    assert fake.sent == [] and fake.tokens == 0


def test_allowlist_is_read_only_and_no_order_capable_library_is_imported():
    assert set(K.TR_PATHS) == {"ka10099", "ka10059", "ka10001", "ka10081", "ka20006", "ka20003"}
    assert not any(p.endswith(("/ordr", "/acnt", "/crdordr")) for p in K.TR_PATHS.values())
    for f in sorted((REPO / "src/sectorflow/collect").glob("*.py")):
        tree = ast.parse(f.read_text("utf-8"))
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            assert not any(n.split(".")[0] == "kiwoom_client" for n in names), \
                f"{f.name}: kiwoom_client(주문 API 포함)를 import 한다"


def test_invalid_token_payload_reissues_once_then_retries():
    fake = Fake([(200, {}, {"return_code": 3, "return_msg": "[8005:Token이 유효하지 않습니다]"}),
                 ok({"list": [1]})])
    api, _ = _api(fake)
    data, _ = api.request("ka10099", {"mrkt_tp": "0"})
    assert data["list"] == [1]
    assert fake.tokens == 2
    assert fake.sent[0][1]["authorization"] == "Bearer tok1"
    assert fake.sent[1][1]["authorization"] == "Bearer tok2"


def test_second_invalid_token_is_a_conflict_not_a_loop():
    bad = (200, {}, {"return_code": 3, "return_msg": "8005"})
    fake = Fake([bad, bad, bad])
    api, _ = _api(fake)
    with pytest.raises(K.TokenConflict):
        api.request("ka10099", {})
    assert fake.tokens == 2 and len(fake.sent) == 2


def test_429_and_rc5_back_off_then_succeed():
    fake = Fake([(429, {}, {}), (200, {}, {"return_code": 5, "return_msg": "허용된 요청 개수를 초과"}),
                 ok({"x": 1})])
    api, clock = _api(fake, rate=None, backoff=1.0)
    data, _ = api.request("ka10001", {"stk_cd": "005930"})
    assert data["x"] == 1
    assert clock.slept == [1.0, 2.0]
    assert api.retries["ka10001"] == 2 and api.calls["ka10001"] == 3


def test_429_gives_up_after_max_retries():
    fake = Fake([(429, {}, {})] * 3)
    api, _ = _api(fake, rate=None, max_retries=2, backoff=0.0)
    with pytest.raises(K.KiwoomError):
        api.request("ka10001", {})
    assert len(fake.sent) == 3


def test_pagination_follows_response_headers():
    fake = Fake([ok({"rows": [{"dt": "20261106"}]}, **{"cont-yn": "Y", "next-key": "K1"}),
                 ok({"rows": [{"dt": "20260601"}]}, **{"cont-yn": "Y", "next-key": "K2"}),
                 ok({"rows": [{"dt": "20260101"}]}, **{"cont-yn": "N", "next-key": ""})])
    api, _ = _api(fake, rate=None)
    rows = api.request_pages("ka10059", {"dt": "20261108"}, "rows", max_pages=5)
    assert [r["dt"] for r in rows] == ["20261106", "20260601", "20260101"]
    assert [(h["cont-yn"], h["next-key"]) for _, h, _ in fake.sent] == [("N", ""), ("Y", "K1"), ("Y", "K2")]


def test_pagination_stops_at_max_pages_and_on_stop():
    page = ok({"rows": [{"dt": "20260601"}]}, **{"cont-yn": "Y", "next-key": "K"})
    fake = Fake([page] * 5)
    api, _ = _api(fake, rate=None)
    assert len(api.request_pages("ka10059", {}, "rows", max_pages=2)) == 2
    assert len(api.request_pages("ka10059", {}, "rows", max_pages=5,
                                 stop=lambda p: p[0]["dt"] <= "20260701")) == 1
    assert len(fake.sent) == 3


def test_rate_limit_is_per_tr():
    fake = Fake([ok()] * 3)
    api, clock = _api(fake, rate=0.5)
    api.request("ka10059", {})
    api.request("ka10081", {})          # 다른 TR — 기다리지 않는다
    assert clock.slept == []
    api.request("ka10059", {})          # 같은 TR — 2초 간격
    assert clock.slept == [pytest.approx(2.0)]


def test_call_budget_counts_token_and_stops_before_sending():
    fake = Fake([ok()] * 5)
    api, _ = _api(fake, rate=None, max_calls=3)
    api.request("ka10001", {})
    api.request("ka10001", {})
    with pytest.raises(K.CallBudgetExceeded):
        api.request("ka10001", {})
    assert len(fake.sent) == 2 and api.total_calls == 3


def test_token_is_reissued_before_expiry():
    clock = Clock()
    fake = Fake([ok(), ok()], token_replies=[(200, {}, {"token": "t1", "expires_in": 600}),
                                             (200, {}, {"token": "t2", "expires_in": 600})])
    api, _ = _api(fake, clock=clock, rate=None)
    api.request("ka10001", {})
    clock.t += 400                       # 만료 200초 전 — 여유(300초) 안이다
    api.request("ka10001", {})
    assert [h["authorization"] for _, h, _ in fake.sent] == ["Bearer t1", "Bearer t2"]


def test_secrets_do_not_leak_into_repr_or_errors():
    fake = Fake([(500, {}, {"echo": f"Bearer tok1 appkey={FAKE_KEY}"})],
                token_replies=[(200, {}, {"token": "tok1", "expires_in": 600})])
    api, _ = _api(fake, rate=None, max_retries=0)
    with pytest.raises(K.KiwoomError) as e:
        api.request("ka10001", {})
    for s in (FAKE_KEY, FAKE_SECRET, "tok1"):
        assert s not in str(e.value) and s not in repr(api)


def test_failed_token_issue_does_not_echo_secret():
    fake = Fake(token_replies=[(403, {}, {"return_code": 1, "return_msg": f"bad key {FAKE_KEY}"})])
    api, _ = _api(fake)
    with pytest.raises(K.KiwoomError) as e:
        api.request("ka10001", {})
    assert FAKE_KEY not in str(e.value)


def test_token_ping_pong_is_capped_per_run():
    """다른 프로그램과 서로 밀어내면 요청마다 1회씩 재발급해 끝없이 핑퐁한다 — 실행당 상한에서 멈춘다."""
    bad = (200, {}, {"return_code": 3, "return_msg": "8005"})
    fake = Fake([bad, ok(), bad, ok(), bad, ok()])
    api, _ = _api(fake, rate=None, max_reissues=2)
    api.request("ka10001", {})
    api.request("ka10001", {})
    with pytest.raises(K.TokenConflict):
        api.request("ka10001", {})
    assert api.reissues == 2 and fake.tokens == 3


def test_expires_dt_is_read_as_kst_regardless_of_host_timezone(monkeypatch):
    import time
    from datetime import datetime, timezone
    want = datetime(2026, 11, 8, 15, 0, tzinfo=timezone.utc).timestamp()   # = 2026-11-09 00:00 KST
    for tz in ("UTC", "America/New_York"):        # 이 호스트는 KST 라 순진한 해석도 맞아 보인다 — 시간대를 바꿔 본다
        monkeypatch.setenv("TZ", tz)
        time.tzset()
        try:
            assert K._parse_expiry({"expires_dt": "20261109000000"}, lambda: 0.0) == want, tz
        finally:
            monkeypatch.undo()
            time.tzset()


def test_pages_info_tells_truncation_from_end_of_history():
    more = ok({"rows": [{"dt": "20260601"}]}, **{"cont-yn": "Y", "next-key": "K"})
    end = ok({"rows": [{"dt": "20260101"}]}, **{"cont-yn": "N", "next-key": ""})
    api, _ = _api(Fake([more, more, more, end]), rate=None)
    info: dict = {}
    api.request_pages("ka10059", {}, "rows", max_pages=2, info=info)
    assert info == {"pages": 2, "more": True, "stopped": False}
    info = {}
    api.request_pages("ka10059", {}, "rows", max_pages=5, info=info)
    assert info == {"pages": 2, "more": False, "stopped": False}


def test_guard_runs_before_every_request_including_token():
    seen: list[str] = []

    def guard():
        seen.append("g")
        if len(seen) == 3:
            raise K.BusyWindow("창")
    fake = Fake([ok(), ok()])
    api, _ = _api(fake, rate=None, guard=guard)
    api.request("ka10001", {})          # 토큰(1) + 요청(2)
    with pytest.raises(K.BusyWindow):
        api.request("ka10001", {})      # 3번째 검사에서 멈춘다 — 요청은 안 나간다
    assert len(fake.sent) == 1


def test_mask_covers_single_quoted_and_bearer_forms():
    from sectorflow.collect.auth import mask
    out = mask(str({"token": "abc123", "appkey": "k1"}) + ' {"secretkey": "s9"} Bearer zzz')
    for s in ("abc123", "k1", "s9", "zzz"):
        assert s not in out


def test_env_file_accepts_export_lines(tmp_path, monkeypatch):
    from sectorflow.collect.auth import load_keys
    monkeypatch.delenv("KIWOOM_APP_KEY", raising=False)
    monkeypatch.delenv("KIWOOM_APP_SECRET", raising=False)
    f = tmp_path / ".env"
    f.write_text("# c\nexport KIWOOM_APP_KEY='kk'\nKIWOOM_APP_SECRET=ss\n", encoding="utf-8")
    assert load_keys(f) == ("kk", "ss")
