"""BYO 수집기의 **실제 전송 경로** — 가짜 ``transport`` 를 끼우지 않고 ``urllib_transport`` 를 루프백 서버에 돌린다.

나머지 수집 검사는 전송을 바꿔 끼워서, 소문자화·HTTP 메서드·오류 응답 처리처럼 전송 함수 안의 일은 아무도 보지
않았다. 헤더 소문자화가 빠지면 실서버의 ``Cont-Yn`` 을 못 읽어 1쪽만 받고 **조용히 끝난다**. 실서버는 부르지 않는다 —
``127.0.0.1`` 의 ``http.server`` 뿐이다.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sectorflow.collect import dart, kiwoom

TOKEN_REPLY = {"token": "tok-1", "expires_dt": "20991231235959", "return_code": 0}


class Loopback:
    """요청을 기록하고 ``script`` 의 다음 응답(상태, 헤더, 본문 바이트)을 돌려준다. POST·GET 은 따로 받는다."""

    def __init__(self, allow: str):
        self.seen: list[dict] = []
        self.script: list[tuple[int, dict, bytes]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def _serve(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.seen.append({"method": self.command, "path": self.path, "headers": dict(self.headers.items()),
                                   "body": self.rfile.read(n)})
                status, hdrs, body = outer.script.pop(0)
                self.send_response(status)
                for k, v in hdrs.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        setattr(H, f"do_{allow}", H._serve)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def post_server(monkeypatch):
    monkeypatch.setenv("no_proxy", "*")          # 프록시 환경에서도 루프백으로 곧장 간다
    s = Loopback("POST")
    yield s
    s.close()


@pytest.fixture
def get_server(monkeypatch):
    monkeypatch.setenv("no_proxy", "*")
    s = Loopback("GET")
    monkeypatch.setattr(dart, "BASE_URL", s.url)
    yield s
    s.close()


def _json(status: int, doc: dict, **hdrs) -> tuple[int, dict, bytes]:
    return status, {"Content-Type": "application/json;charset=UTF-8", **hdrs}, json.dumps(doc).encode()


def test_kiwoom_real_transport_pages_on_mixed_case_response_headers(post_server):
    """실서버 응답 헤더는 ``Cont-Yn``·``Next-Key`` 꼴이다 — 소문자로 못 읽으면 1쪽만 받고 오류 없이 끝난다."""
    post_server.script += [
        _json(200, TOKEN_REPLY),
        _json(200, {"rows": [{"d": 1}], "return_code": 0}, **{"Cont-Yn": "Y", "Next-Key": "k-2"}),
        _json(200, {"rows": [{"d": 2}], "return_code": 0}, **{"Cont-Yn": "N", "Next-Key": ""}),
    ]
    api = kiwoom.KiwoomRest("KEY", "SECRET", base_url=post_server.url, rate=0)
    info: dict = {}
    rows = api.request_pages("ka10059", {"stk_cd": "005930"}, "rows", max_pages=5, info=info)
    assert rows == [{"d": 1}, {"d": 2}]
    assert info["pages"] == 2 and info["more"] is False

    token, first, second = post_server.seen
    assert [r["method"] for r in post_server.seen] == ["POST"] * 3
    assert token["path"] == kiwoom.TOKEN_PATH and first["path"] == kiwoom.TR_PATHS["ka10059"]
    assert json.loads(token["body"])["appkey"] == "KEY"
    assert json.loads(first["body"]) == {"stk_cd": "005930"}
    # 둘째 쪽은 첫 쪽의 응답 헤더로 이어 받는다.
    sent = {k.lower(): v for k, v in second["headers"].items()}
    assert sent["api-id"] == "ka10059" and sent["authorization"] == "Bearer tok-1"
    assert sent["cont-yn"] == "Y" and sent["next-key"] == "k-2"


def test_kiwoom_real_transport_hands_http_errors_back_as_values(post_server):
    """4xx·5xx 는 예외가 아니라 ``(status, 헤더, 본문)`` 이다 — 그래야 429 재시도·오류 분류가 돈다."""
    post_server.script += [_json(429, {"return_msg": "느리게"}, **{"Retry-After": "1"})]
    status, hdrs, body = kiwoom.urllib_transport(post_server.url + "/x", {"a": "b"}, {"q": 1}, 5.0)
    assert (status, body) == (429, {"return_msg": "느리게"}) and hdrs["retry-after"] == "1"

    post_server.script += [_json(200, TOKEN_REPLY), _json(429, {"return_msg": "느리게"}),
                           _json(200, {"rows": [{"d": 1}], "return_code": 0})]
    slept: list[float] = []
    api = kiwoom.KiwoomRest("KEY", "SECRET", base_url=post_server.url, rate=0, backoff=2.0, sleep=slept.append)
    assert api.request("ka10059", {})[0]["rows"] == [{"d": 1}]
    assert slept == [2.0] and api.retries["ka10059"] == 1


def test_kiwoom_real_transport_survives_a_body_that_is_not_json(post_server):
    """게이트웨이 오류 페이지(HTML)가 와도 파서가 죽지 않고 상태로 끝난다 — 본문은 잘라서 싣는다."""
    post_server.script += [(502, {"Content-Type": "text/html"}, b"<html>bad gateway</html>")]
    status, _h, body = kiwoom.urllib_transport(post_server.url + "/x", {}, {}, 5.0)
    assert status == 502 and body == {"_raw": "<html>bad gateway</html>"}


def test_dart_real_transport_gets_and_retries_server_errors(get_server):
    get_server.script += [(500, {}, b"busy"), (200, {}, b'{"status":"000"}')]
    client = dart.DartClient("DART-KEY", rate=0, retries=1, backoff=0, sleep=lambda _s: None)
    assert client.get_json("stockTotqySttus.json", {"corp_code": "1"}) == {"status": "000"}
    assert [r["method"] for r in get_server.seen] == ["GET", "GET"]
    assert get_server.seen[0]["path"].startswith("/stockTotqySttus.json?crtfc_key=DART-KEY&corp_code=1")
    assert get_server.seen[0]["headers"]["User-Agent"] == "sector-flow-collect"


def test_dart_real_transport_reports_http_errors_without_the_key(get_server):
    """4xx 는 재시도할 일이 아니다 — 상태를 그대로 말하고, 메시지에 키가 없다. 전송이 예외를 그대로 던지면
    통신 오류로 뭉뚱그려져(``net``) 재시도하고 상태를 잃는다."""
    get_server.script += [(404, {}, b"no such path DART-KEY")]
    client = dart.DartClient("DART-KEY", rate=0, retries=2, backoff=0, sleep=lambda _s: None)
    with pytest.raises(dart.DartError) as e:
        client.get("stockTotqySttus.json", {})
    assert e.value.status == "HTTP 404"
    assert "DART-KEY" not in str(e.value)
    assert len(get_server.seen) == 1, "4xx 를 재시도했다"


@pytest.mark.parametrize("hdrs", [{"Cont-Yn": "Y"}, {"Cont-Yn": "N", "Next-Key": "k"}, {}])
def test_kiwoom_paging_needs_both_the_flag_and_the_key(post_server, hdrs):
    """연속 표시와 다음 키가 **둘 다** 있어야 잇는다 — 하나만 보면 같은 쪽을 상한까지 다시 받는다."""
    post_server.script += [_json(200, TOKEN_REPLY), _json(200, {"rows": [{"d": 1}], "return_code": 0}, **hdrs)]
    api = kiwoom.KiwoomRest("KEY", "SECRET", base_url=post_server.url, rate=0)
    assert api.request_pages("ka10059", {}, "rows", max_pages=3) == [{"d": 1}]
    assert len(post_server.seen) == 2


def test_kiwoom_server_that_never_stops_saying_more_ends_at_max_pages(post_server):
    """서버가 계속 ``Cont-Yn: Y`` 를 주는 고장도 ``max_pages`` 에서 끝나고 잘렸다고 알린다. 가짜 서버의 대본도 유한하다 —
    상한이 빠지면 대본이 바닥나 오류로 끝나지, 끝없이 자라지 않는다."""
    forever = _json(200, {"rows": [{"d": 1}], "return_code": 0}, **{"Cont-Yn": "Y", "Next-Key": "k"})
    post_server.script += [_json(200, TOKEN_REPLY), *[forever] * 6]
    api = kiwoom.KiwoomRest("KEY", "SECRET", base_url=post_server.url, rate=0)
    info: dict = {}
    assert len(api.request_pages("ka10059", {}, "rows", max_pages=3, info=info)) == 3
    assert info == {"pages": 3, "more": True, "stopped": False}
    assert len(post_server.seen) == 4, "토큰 1 + 쪽 3 이어야 한다"
