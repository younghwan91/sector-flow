"""리포트 웹 서비스(`scripts/serve_reports.py`) — 실제로 띄워 HTTP 로 두드린다."""
from __future__ import annotations

import gzip
import http.client
import importlib.util
import os
import signal
import socket
import subprocess
import sys
import time
import threading
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("serve_reports", REPO / "scripts" / "serve_reports.py")
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "reports"
    for d, extra in (("2026-11-05", ["VERIFY_FAILED.txt"]), ("2026-11-06", [])):
        p = r / d
        p.mkdir(parents=True)
        for f in ("numbers.html", "viewer.html", "VERIFY.txt", "payload.json", *extra):
            (p / f).write_text(f"<!DOCTYPE html>{d}/{f}" + "x" * 5000, "utf-8")
    (r / "2026-11-07").mkdir()
    (r / "2026-11-07" / "INCOMPLETE.txt").write_text("수집 미완", "utf-8")
    (r / "2026-11-09.building.123").mkdir()
    (r / "cron.log").write_text("DB 접속 실패 …", "utf-8")
    os.symlink(r / "2026-11-06", r / "latest")
    return r


@pytest.fixture
def get(root):
    srv.Handler.root = str(root)
    s = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()

    def _get(path, headers=None, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
        c.request(method, path, headers=headers or {})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    yield _get
    s.shutdown()
    s.server_close()


@pytest.mark.parametrize("path", ["/2026-11-06/payload.json", "/cron.log", "/../etc/passwd",
                                  "/2026-11-06/../../cron.log", "/2026-11-09.building.123/numbers.html",
                                  "/latest/../cron.log", "/%2e%2e/cron.log"])
def test_only_the_report_pages_are_served(get, path):
    """페이로드·크론 로그(DB 오류문)·짓는 중인 폴더는 안 나간다."""
    status, _h, body = get(path)
    assert status in (302, 404), (path, status)
    if status == 302:
        assert "cron.log" not in _h["Location"] and ".." not in _h["Location"]
    assert b"DB" not in body


def test_latest_redirects_to_the_dated_page(get):
    status, h, _ = get("/latest/viewer.html")
    assert status == 302 and h["Location"] == "/2026-11-06/viewer.html"
    assert get("/latest/")[1]["Location"] == "/2026-11-06/numbers.html"


def test_pages_are_utf8_revalidated_and_gzipped(get):
    status, h, body = get("/2026-11-06/numbers.html", {"Accept-Encoding": "gzip"})
    assert status == 200
    assert h["Content-Type"] == "text/html; charset=utf-8"
    assert h["Cache-Control"] == "no-cache" and h["ETag"]
    assert h["Content-Encoding"] == "gzip" and gzip.decompress(body).startswith(b"<!DOCTYPE html>2026-11-06")
    assert get("/2026-11-06/numbers.html", {"If-None-Match": h["ETag"], "Accept-Encoding": "gzip"})[0] == 304


def test_head_sends_no_body(get):
    status, h, body = get("/2026-11-06/viewer.html", method="HEAD")
    assert status == 200 and body == b"" and int(h["Content-Length"]) > 0


def test_index_marks_latest_failed_and_incomplete(get):
    status, _h, body = get("/")
    page = body.decode()
    assert status == 200
    assert "최신" in page and 'href="/latest/numbers.html"' in page
    assert "검증 실패" in page and "/2026-11-05/VERIFY_FAILED.txt" in page
    assert "수집 미완" in page
    assert "building" not in page


def test_index_warns_when_the_latest_report_is_old(root):
    assert "일 지난 리포트" in srv.index_html(str(root), today=date(2026, 11, 12))
    assert "일 지난 리포트" not in srv.index_html(str(root), today=date(2026, 11, 9))


def test_latest_falls_back_to_the_newest_built_folder(root):
    """링크가 없거나 깨져도 목록·단추가 죽지 않는다 — 짓다 만 폴더(INCOMPLETE 만 있는 날)는 고르지 않는다."""
    (root / "latest").unlink()
    assert srv.latest_date(str(root)) == "2026-11-06"


def test_serve_sh_does_nothing_without_a_tailscale_address(tmp_path):
    """부팅 직후 Tailscale 이 아직 없으면 조용히 다음 크론을 기다린다 — 0 으로 끝나 알림을 안 보낸다."""
    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    (tmp_path / "tailscale").write_text("#!/bin/sh\nexit 1\n")
    (tmp_path / "tailscale").chmod(0o755)
    os.symlink(sys.executable, tmp_path / "python3")
    for tool in ("dirname", "date", "head", "mkdir"):
        os.symlink(f"/usr/bin/{tool}" if os.path.exists(f"/usr/bin/{tool}") else f"/bin/{tool}",
                   tmp_path / tool)
    p = subprocess.run(["/bin/bash", str(REPO / "scripts" / "serve.sh")], capture_output=True,
                       text=True, env=env, timeout=10)
    assert p.returncode == 0, p.stderr
    assert "Tailscale 주소가 없다" in p.stdout


def test_soft_failures_alert_once_an_hour_not_every_five_minutes(tmp_path):
    """포트 점유·Tailscale 미기동은 저절로 풀리곤 한다. 5분마다 알리면 하루 288건이다."""
    rcs = [srv.soft_fail(str(tmp_path), "포트") for _ in range(2 * srv.SOFT_FAIL_ALERT_EVERY)]
    assert rcs.count(1) == 2 and rcs[srv.SOFT_FAIL_ALERT_EVERY - 1] == 1


def test_a_taken_port_is_a_soft_failure(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert srv.main(["--root", str(tmp_path), "--host", "127.0.0.1", "--port", str(port)]) == 0
    assert (tmp_path / ".serve.misses").read_text() == "1"


def test_sigterm_is_a_clean_exit(tmp_path):
    """재부팅·손으로 끄는 것은 고장이 아니다 — 0 이 아니면 크론 래퍼가 실패로 알린다."""
    p = subprocess.Popen([sys.executable, str(REPO / "scripts" / "serve_reports.py"), "--root", str(tmp_path),
                          "--host", "127.0.0.1", "--port", "0"], stdout=subprocess.PIPE, text=True)
    p.stdout.readline()  # 떴다는 줄
    time.sleep(0.2)
    p.send_signal(signal.SIGTERM)
    assert p.wait(timeout=10) == 0


def test_idle_connections_do_not_hold_threads_forever():
    assert 0 < srv.Handler.timeout <= 60


def test_an_incomplete_day_leads_to_its_note_not_a_dead_end(get):
    status, h, _ = get("/2026-11-07/")
    assert status == 302 and h["Location"] == "/2026-11-07/INCOMPLETE.txt"
    status, _h, body = get("/2026-11-07/numbers.html")
    assert status == 404 and "INCOMPLETE.txt" in body.decode()


def test_gzip_and_raw_have_different_etags(get):
    a = get("/2026-11-06/viewer.html", {"Accept-Encoding": "gzip"})[1]["ETag"]
    b = get("/2026-11-06/viewer.html")[1]["ETag"]
    assert a != b


def test_a_folder_swap_does_not_404(root, get):
    """배치는 같은 기준일을 옆에 짓고 ``mv`` 두 번으로 바꿔 끼운다 — 그 틈에 온 요청이 404 였다."""
    d = root / "2026-11-06"
    d.rename(root / "2026-11-06.old")
    threading.Timer(0.15, lambda: (root / "2026-11-06.old").rename(d)).start()
    assert get("/2026-11-06/numbers.html")[0] == 200
    (root / "latest").unlink()
    os.symlink(d, root / "latest")


def _row(page: str, d: str) -> str:
    """목록에서 그 기준일의 한 줄(`<li …>…</li>`)."""
    i = page.index(f'<span class="d">{d}</span>')
    return page[page.rindex("<li", 0, i):page.index("</li>", i)]


def test_each_row_says_final_or_provisional_and_links_both_pages(root):
    """줄마다 확정(FINAL.txt)/잠정, 검증 실패/통과, 수집 미완을 적고 두 화면을 잇는다.

    08:20 확정 모드가 전날을 확정치로 다시 만드는데, 목록은 어느 날이 확정인지 안 적었다 —
    잠정 리포트를 확정으로 읽어도 알 길이 없었다.
    """
    (root / "2026-11-05" / "FINAL.txt").write_text("[…] 확정치로 만들었다.", "utf-8")
    page = srv.index_html(str(root), today=date(2026, 11, 7))
    final, prov, inc = _row(page, "2026-11-05"), _row(page, "2026-11-06"), _row(page, "2026-11-07")
    assert ">확정<" in final and "잠정" not in final and "검증 실패" in final
    assert ">잠정<" in prov and ">확정<" not in prov and "검증 통과" in prov
    assert "수집 미완" in inc and "잠정" not in inc and "확정" not in inc, "리포트 없는 날에 확정/잠정을 붙였다"
    for d, row in (("2026-11-05", final), ("2026-11-06", prov)):
        assert f'href="/{d}/numbers.html"' in row and f'href="/{d}/viewer.html"' in row


def test_the_newest_row_and_its_status_stand_out(root):
    page = srv.index_html(str(root), today=date(2026, 11, 7))
    assert _row(page, "2026-11-06").startswith('<li class="latest">')
    assert 'class="latest"' not in _row(page, "2026-11-05")
    hero = page[page.index('class="hero"'):page.index("</div>", page.index('class="hero"'))]
    assert "2026-11-06" in hero and "잠정" in hero and "검증 통과" in hero


def test_index_does_not_claim_reports_never_change(root):
    """08:20 확정 모드가 전날 리포트를 다시 만든다 — "만들어 둔 리포트는 안 바뀐다" 는 틀린 말이었다."""
    page = srv.index_html(str(root))
    assert "안 바뀐다" not in page
    assert "08:20" in page and "확정" in page


def test_index_fits_a_phone_without_a_wide_table(root):
    """390px 폰에서 가로 스크롤이 안 생긴다 — 고정 열 표 대신 줄바꿈되는 목록이다."""
    page = srv.index_html(str(root))
    assert 'name="viewport" content="width=device-width' in page
    assert "<table" not in page
    assert "ul.days li{display:flex;flex-wrap:wrap" in page, "목록 줄이 좁은 화면에서 안 접힌다"


def test_final_note_is_served_as_text(get, root):
    (root / "2026-11-06" / "FINAL.txt").write_text("확정", "utf-8")
    status, h, body = get("/2026-11-06/FINAL.txt")
    assert status == 200 and h["Content-Type"].startswith("text/plain") and body.decode() == "확정"
