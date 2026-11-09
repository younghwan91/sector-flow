#!/usr/bin/env python3
"""일일 리포트를 웹으로 연다 — 읽기 전용, 표준 라이브러리만.

    python3 scripts/serve_reports.py --host 100.x.y.z --port 8765

``reports/<기준일>/`` 의 두 화면(``numbers.html``·``viewer.html``)과 검증·품질 페이지(``trust.html``)·문서 사본
(``validation/*.md``)·다이제스트(``digest.md``·``digest.png``), 검증·확정·미완 기록만 내보낸다.
``payload.json``·``cron.log``·짓는 중인 ``*.building.*`` 폴더는 목록에 없으면 안 보인다 — 경로를
정규식으로 고르므로 ``..`` 같은 것은 애초에 도달하지 못한다. 이 웹은 Tailscale 안의 비공개 화면이다 — 다이제스트를 여기
내보내는 것은 밖에 게시하는 것이 아니다(그 길은 만들지 않는다, ``scripts/digest.py`` 머리말).

- ``/`` 는 기준일 목록. 최신·확정/잠정·검증 실패·수집 미완을 표시한다.
- ``/latest/…`` 는 지금의 ``latest`` 가 가리키는 기준일로 넘긴다(302). 즐겨찾기가 늘 최신을 연다.
- HTML 은 ``no-cache`` + ETag — 리포트가 다시 만들어지면 새로고침 한 번에 바뀐다.
- 3MB 짜리 페이지라 gzip 으로 보낸다(약 10분의 1).
- 이 파일이 바뀌면(배포) 스스로 끝난다. 크론(``scripts/serve.sh``)이 5분 안에 새 코드로 다시 띄운다.

접속 기록은 남기지 않는다(오류만). 크론 래퍼가 출력을 통째로 들고 있기 때문이다.
"""
from __future__ import annotations

import argparse
import atexit
import errno
import gzip
import html
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
from datetime import date
from email.utils import formatdate, parsedate_to_datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: 내보내는 파일. 나머지는 404 다.
FILES = {
    "numbers.html": "text/html; charset=utf-8",
    "viewer.html": "text/html; charset=utf-8",
    "trust.html": "text/html; charset=utf-8",  # 검증·품질 — 배치가 짓는 폴더 안에서 만든다(scripts/trust_page.py)
    "digest.md": "text/plain; charset=utf-8",  # 오늘의 수급 글 — 비공개 웹에서만 읽는다
    "digest.png": "image/png",
    "VERIFY.txt": "text/plain; charset=utf-8",
    "VERIFY_FAILED.txt": "text/plain; charset=utf-8",
    "INCOMPLETE.txt": "text/plain; charset=utf-8",
    "FINAL.txt": "text/plain; charset=utf-8",
    "REBUILT.txt": "text/plain; charset=utf-8",
}
#: 문서 사본 폴더 — trust_page.DOCS_DIR 와 같다. 이름은 글자로 시작하는 ``.md`` 만(``/`` 는 애초에 못 든다).
DOCS_DIR = "validation"
DOC_RE = re.compile(r"^\w[\w.\-]*\.md$")
DOC_TYPE = "text/plain; charset=utf-8"
PAGES = (("numbers.html", "수치표"), ("viewer.html", "흐름"))
#: 있을 때만 잇는 산출물 — 다이제스트를 끄거나 페이지를 못 만든 날은 단추도 없다.
EXTRAS = (("trust.html", "검증·품질"), ("digest.md", "다이제스트 글"), ("digest.png", "다이제스트 카드"))
#: 화면(`flow_view.STALE_DAYS`)·페이지(`staleChip`)와 같은 문턱.
STALE_DAYS = 4
#: 폴더를 바꿔 끼우는 순간(배치가 옆에 짓고 옮긴다)을 넘기는 짧은 재시도.
SWAP_RETRY = (0.1, 0.3, 0.6)
#: 이만큼 연달아(5분 × 12 = 1시간) 못 뜨면 그때 한 번 알린다 — 5분마다 알리면 하루 288건이다.
SOFT_FAIL_ALERT_EVERY = 12
_GZ: dict = {}
_GZ_LOCK = threading.Lock()


def _file_type(rest: list[str]) -> str | None:
    """기준일 폴더 안 경로(조각들)의 Content-Type — 허용 목록에 없으면 ``None``(404)."""
    if len(rest) == 1:
        return FILES.get(rest[0])
    if len(rest) == 2 and rest[0] == DOCS_DIR and DOC_RE.match(rest[1]):
        return DOC_TYPE
    return None


def report_dates(root: str) -> list[str]:
    try:
        names = os.listdir(root)
    except FileNotFoundError:
        return []
    return sorted((n for n in names if DATE_RE.match(n) and os.path.isdir(os.path.join(root, n))),
                  reverse=True)


def latest_problem(root: str) -> str | None:
    """``latest`` 가 쓸 수 있는 리포트를 가리키나 — 이유 또는 None. 아직 아무 리포트가 없는 새 루트는 문제가 아니다.

    끊긴 링크에도 목록·``latest_date`` 는 가장 늦은 폴더로 넘어가 멀쩡해 보이는데, ``reports/latest`` 를 여는 화면(sw-flow·sw-ledger)과
    게시 경로는 죽어 있다. 바꿔 끼우는 순간(배치가 옆에 짓고 옮긴다)은 짧게 기다려 넘긴다.
    """
    link = os.path.join(root, "latest")
    why = None
    for wait in (0, *SWAP_RETRY):
        time.sleep(wait)
        if not os.path.lexists(link):
            has_reports = any(os.path.exists(os.path.join(root, d, "numbers.html")) for d in report_dates(root))
            return "latest 링크가 없다" if has_reports else None
        target = os.path.realpath(link)
        missing = [f for f, _ in PAGES if not (os.path.isfile(os.path.join(target, f))
                                               and os.path.getsize(os.path.join(target, f)) > 0)]
        if not missing:
            return None
        why = f"latest({os.path.basename(target)})에 {', '.join(missing)} 가 없다"
    return why


def latest_date(root: str) -> str | None:
    """``latest`` 링크가 가리키는 기준일. 링크가 없거나 이상하면 가장 늦은 폴더."""
    for wait in (0, *SWAP_RETRY):
        time.sleep(wait)
        try:
            target = os.path.basename(os.path.normpath(os.readlink(os.path.join(root, "latest"))))
        except OSError:
            break
        if not DATE_RE.match(target):
            break
        if os.path.isdir(os.path.join(root, target)):
            return target
        # 링크는 있는데 폴더가 잠깐 없다 = 바꿔 끼우는 중. 전날로 넘기지 말고 기다린다.
    ds = [d for d in report_dates(root) if os.path.exists(os.path.join(root, d, "numbers.html"))]
    return ds[0] if ds else None


def rebuilt_field(path: str, key: str) -> str | None:
    """``REBUILT.txt`` 의 ``key: 값`` 줄(daily_report.sh 재구성이 쓴다). 없거나 못 읽으면 ``None``."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                k, sep, v = line.partition(":")
                if sep and k.strip() == key:
                    return v.strip()
    except OSError:
        return None
    return None


def day_tags(root: str, d: str) -> list[str]:
    """한 기준일의 상태 표 — ``확정``/``잠정``, ``검증 실패``/``검증 통과``, ``수집 미완``.

    ``확정`` 은 08:20 확정 모드가 확정치로 다시 만든 날(``FINAL.txt``)뿐이다. 그 표시가
    없는 날은 18:10 의 잠정치로 만든 그대로다 — 확정 모드가 확정치를 확인하지 못했거나
    그 모드가 생기기 전의 리포트다. 리포트가 없는 날(수집 미완)에는 둘 다 안 붙인다.

    ``재구성`` 은 지난 기준일을 나중 코드·확정 데이터로 다시 만든 날(``REBUILT.txt``)이다 — 그날 저녁에
    게시된 그대로가 아니라는 표시라 ``확정`` 과 함께 단다.
    """
    p = os.path.join(root, d)
    has = {f: os.path.exists(os.path.join(p, f)) for f in FILES}
    tags = []
    if not has["numbers.html"]:
        if has["INCOMPLETE.txt"]:
            tags.append(f'<a class="tag warn" href="/{d}/INCOMPLETE.txt">수집 미완</a>')
        return tags
    fin = rebuilt_field(os.path.join(p, "REBUILT.txt"), "finalize") if has["REBUILT.txt"] else None
    if has["FINAL.txt"] and has["REBUILT.txt"] and fin != "verified":
        # 옛 재구성(finalize: 줄 없음) — FINAL.txt 는 있어도 그날을 덮은 확정 실행을 확인한 기록이 없다(trust_page 와 같은 말).
        tags.append(f'<a class="tag prov" href="/{d}/REBUILT.txt">확정 미확인</a>')
    elif has["FINAL.txt"]:
        tags.append(f'<a class="tag ok" href="/{d}/FINAL.txt">확정</a>')
    elif has["REBUILT.txt"] and fin == "unverified":
        # daily_finalize 창 밖의 지난 날 — 마감 값이지만 확정 기록이 없다. 잠정(값이 바뀔 수 있다)도 확정도 아니다.
        tags.append(f'<a class="tag prov" href="/{d}/REBUILT.txt">확정 기록 없음</a>')
    else:
        tags.append('<span class="tag prov">잠정</span>')
    if has["REBUILT.txt"]:
        tags.append(f'<a class="tag rebuilt" href="/{d}/REBUILT.txt">재구성</a>')
    if has["VERIFY_FAILED.txt"]:
        tags.append(f'<a class="tag warn" href="/{d}/VERIFY_FAILED.txt">검증 실패</a>')
    elif has["VERIFY.txt"]:
        tags.append(f'<a class="tag" href="/{d}/VERIFY.txt">검증 통과</a>')
    return tags


INDEX_TITLE = "섹터 자금흐름 리포트"
INDEX_LEGEND = """<p class=dim>평일 18:10 리포트는 그날 <b>잠정치</b>로 만든다. 다음날 08:20 에 그 기준일을 확정치로 다시 만들면
<b>확정</b> 표시가 붙는다(확정치가 확인되지 않으면 잠정 그대로 둔다). 크론은 그보다 앞선 날을 다시 만들지 않는다.
<b>재구성</b> 은 지난 기준일을 나중 코드와 확정 데이터로 다시 만든 날이다 — 그날 저녁 게시본이 아니다.</p>"""


def _demo_notice(root: str, day: "str | None") -> "str | None":
    """그 기준일이 합성 데모면 안내 문구(``DEMO.txt`` 첫 줄) — 목록도 실 리포트처럼 읽히면 안 된다."""
    try:
        with open(os.path.join(root, day or "", "DEMO.txt"), encoding="utf-8") as f:
            return f.readline().strip() or "[합성 데모]"
    except OSError:
        return None


def index_html(root: str, today: date | None = None) -> str:
    today = today or date.today()
    latest = latest_date(root)
    demo = _demo_notice(root, latest) if latest else None
    rows = []
    for d in report_dates(root):
        p = os.path.join(root, d)
        links = " ".join(f'<a class="pg" href="/{d}/{f}">{ko}</a>' for f, ko in (*PAGES, *EXTRAS)
                         if os.path.exists(os.path.join(p, f)))
        tags = day_tags(root, d)
        cls = ' class="latest"' if d == latest else ""
        if d == latest:
            tags.insert(0, '<span class="tag new">최신</span>')
        rows.append(f'<li{cls}><span class="d">{d}</span>'
                    f'<span class="pgs">{links or "—"}</span>'
                    f'<span class="tags">{" ".join(tags)}</span></li>')
    head = "<p>리포트가 아직 없다 — 크론이 평일 18:10 에 만든다.</p>"
    if latest:
        age = (today - date.fromisoformat(latest)).days
        stale = (f'<p><span class="tag warn">⚠ {age}일 지난 리포트 — 배치가 멈췄는지 '
                 f'<code>reports/cron.log</code> 를 보라</span></p>') if age >= STALE_DAYS and not demo else ""
        head = (f'<div class="hero"><p class="big">최신 <b>{latest}</b> '
                f'<span class="tags">{" ".join(day_tags(root, latest))}</span></p>{stale}'
                + "<p>" + " ".join(f'<a class="btn" href="/latest/{f}">{ko}</a>' for f, ko in (
                    *PAGES, *((f, ko) for f, ko in EXTRAS if os.path.exists(os.path.join(root, latest, f)))))
                + "</p><p class=dim>위 단추는 즐겨찾기해도 늘 최신을 연다.</p></div>")
    title = f"[합성 데모] {INDEX_TITLE}" if demo else INDEX_TITLE
    bar = f'<p class="demo" data-demo-banner>{html.escape(demo)}</p>' if demo else ""
    legend = "" if demo else INDEX_LEGEND
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{font:15px/1.5 system-ui,sans-serif;margin:24px auto;max-width:760px;padding:0 12px;color:#1d2433;background:#f7f8fa}}
h1{{font-size:20px;margin:0 0 12px}} a{{color:#2457c5}} .big{{font-size:18px;margin:0 0 4px}}
.dim{{color:#6b7280;font-size:13px}} code{{font-size:12px}}
.hero{{background:#fff;border:2px solid #2457c5;border-radius:8px;padding:12px 14px;margin:0 0 12px}}
.hero p{{margin:4px 0}}
.btn{{display:inline-block;margin:4px 8px 0 0;padding:8px 16px;border-radius:6px;background:#2457c5;color:#fff;text-decoration:none}}
.tag{{display:inline-block;font-size:12px;line-height:1.6;padding:0 6px;border-radius:4px;background:#e8ecf3;color:#374151;text-decoration:none;white-space:nowrap}}
.tag.new{{background:#2457c5;color:#fff;font-weight:600}} .tag.ok{{background:#dcfce7;color:#166534}}
.tag.prov{{background:#f1f5f9;color:#475569;border:1px dashed #94a3b8}}
.tag.warn{{background:#fef3c7;color:#92400e;font-weight:600;white-space:normal}}
.demo{{background:#b45309;color:#fff;font-weight:700;padding:6px 12px;border-radius:6px;margin:0 0 12px}}
.tag.rebuilt{{background:#ede9fe;color:#5b21b6}}
ul.days{{list-style:none;margin:0;padding:0;background:#fff;border:1px solid #e2e5ea;border-radius:8px}}
ul.days li{{display:flex;flex-wrap:wrap;align-items:center;gap:4px 12px;padding:8px 10px;border-bottom:1px solid #e2e5ea}}
ul.days li:last-child{{border-bottom:0}}
ul.days li.latest{{background:#eef3fd;border-left:4px solid #2457c5;font-weight:600}}
.d{{font-variant-numeric:tabular-nums;min-width:6.2em}} .pgs a{{margin-right:10px}}
.tags{{display:inline-flex;flex-wrap:wrap;gap:4px}}
@media (max-width:480px){{body{{margin:12px auto}} .d{{min-width:0;width:100%}}}}
</style></head><body>
{bar}<h1>섹터 자금흐름 리포트</h1>
{head}
{legend}
<ul class="days">{''.join(rows)}</ul>
</body></html>"""


def _gzip(path: str, st: os.stat_result) -> bytes:
    key = (path, st.st_mtime_ns, st.st_size)
    with _GZ_LOCK:
        hit = _GZ.get(path)
        if hit and hit[0] == key:
            return hit[1]
    with open(path, "rb") as f:
        body = gzip.compress(f.read(), compresslevel=6, mtime=0)
    with _GZ_LOCK:
        _GZ[path] = (key, body)
    return body


def soft_fail(root: str, msg: str) -> int:
    """곧 저절로 풀릴 수 있는 기동 실패(Tailscale 미기동·포트 점유). 연달아 센 횟수가
    :data:`SOFT_FAIL_ALERT_EVERY` 의 배수일 때만 1 — 그때만 크론 래퍼가 알린다."""
    path = os.path.join(root, ".serve.misses")
    try:
        with open(path, encoding="utf-8") as f:
            n = int(f.read().strip() or 0) + 1
    except (OSError, ValueError):
        n = 1
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(n))
    except OSError:
        pass
    print(f"[{time.strftime('%F %T')}] {msg} — 다음 크론에 다시 본다({n}회째)", flush=True)
    return 1 if n % SOFT_FAIL_ALERT_EVERY == 0 else 0


class Handler(BaseHTTPRequestHandler):
    root = os.path.join(REPO, "reports")
    #: 연결만 열고 요청을 안 보내는 클라이언트가 스레드를 무기한 잡지 않게.
    timeout = 30
    server_version = "sector-flow"
    sys_version = ""

    def log_message(self, fmt, *args):  # 접속 기록은 남기지 않는다(모듈 설명)
        pass

    def log_error(self, fmt, *args):
        sys.stderr.write(f"[{time.strftime('%F %T')}] {self.address_string()} {fmt % args}\n")

    def _head(self, code, ctype, length, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()

    def _send(self, code, ctype, body: bytes, extra=()):
        self._head(code, ctype, len(body), extra)
        if self.command != "HEAD":
            self.wfile.write(body)

    def _redirect(self, to: str):
        self._send(HTTPStatus.FOUND, "text/plain; charset=utf-8", b"",
                   (("Location", to), ("Cache-Control", "no-store")))

    def _not_found(self, why="없는 경로다"):
        self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8",
                   f"404 — {why}. 목록: /\n".encode())

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/healthz":
            return self._healthz()
        if path in ("/", "/index.html"):
            return self._send(HTTPStatus.OK, "text/html; charset=utf-8",
                              index_html(self.root).encode(), (("Cache-Control", "no-cache"),))
        parts = [p for p in path.split("/") if p]
        if parts and parts[0] == "latest":
            d = latest_date(self.root)
            if not d:
                return self._not_found("아직 리포트가 없다")
            rest = parts[1:] or ["numbers.html"]
            if _file_type(rest) is None:
                return self._not_found()
            return self._redirect(f"/{d}/{'/'.join(rest)}")
        if len(parts) == 1 and DATE_RE.match(parts[0]):
            p = os.path.join(self.root, parts[0])
            if not os.path.exists(os.path.join(p, "numbers.html")) and \
                    os.path.exists(os.path.join(p, "INCOMPLETE.txt")):
                return self._redirect(f"/{parts[0]}/INCOMPLETE.txt")
            return self._redirect(f"/{parts[0]}/numbers.html")
        ctype = _file_type(parts[1:]) if parts and DATE_RE.match(parts[0]) else None
        if ctype is None:
            return self._not_found()
        rel = "/".join(parts[1:])
        full = os.path.join(self.root, parts[0], *parts[1:])
        st = None
        for wait in (0, *SWAP_RETRY):  # 같은 기준일을 다시 만들어 바꿔 끼우는 순간
            time.sleep(wait)
            try:
                st = os.stat(full)
                break
            except FileNotFoundError:
                # 폴더는 있는데 파일이 없다 — 기다려도 안 생긴다. 폴더째 없으면 바꿔 끼우는 중일 수 있다.
                if os.path.isdir(os.path.join(self.root, parts[0])):
                    break
        if st is None:
            why = f"{parts[0]} 에 {rel} 가 없다"
            if os.path.exists(os.path.join(self.root, parts[0], "INCOMPLETE.txt")):
                why += f" — 수집 미완이라 안 만들었다(/{parts[0]}/INCOMPLETE.txt)"
            return self._not_found(why)
        # 그림은 이미 압축돼 있다 — 다시 눌러 봐야 CPU 만 쓴다.
        gz = ctype.startswith("text/") and "gzip" in (self.headers.get("Accept-Encoding") or "")
        # 압축본과 원본은 다른 바이트다 — 같은 ETag 를 주면 중간 캐시가 섞어 줄 수 있다.
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}{"-gz" if gz else ""}"'
        cache = (("ETag", etag), ("Last-Modified", formatdate(st.st_mtime, usegmt=True)),
                 ("Cache-Control", "no-cache"), ("Vary", "Accept-Encoding"))
        if self.headers.get("If-None-Match") == etag or self._not_modified_since(st):
            self.send_response(HTTPStatus.NOT_MODIFIED)
            for k, v in cache:
                self.send_header(k, v)
            return self.end_headers()
        if gz:
            body = _gzip(full, st)
            return self._send(HTTPStatus.OK, ctype, body, cache + (("Content-Encoding", "gzip"),))
        with open(full, "rb") as f:
            body = f.read()
        return self._send(HTTPStatus.OK, ctype, body, cache)

    def _healthz(self):
        """대답만이 아니라 **보여 줄 수 있나**를 본다 — 리포트 루트를 읽을 수 있고 목록이 그려지고 latest 가 살아 있나(수 ms).

        예전엔 프로세스가 대답하면 200 이었다. 루트가 지워지거나(마운트 빠짐) 권한이 바뀌어도, 목록이 예외로
        죽어도 serve.sh 는 "건강하다" 며 그대로 뒀다. 503 이면 serve.sh 가 다시 띄우고, 다시 떠도 안 되면
        5분마다 되풀이되며 serve.log 에 남는다 — 본문이 이유다.
        """
        try:
            os.listdir(self.root)
            index_html(self.root)
            sick = latest_problem(self.root)
        except Exception as e:  # noqa: BLE001 — 무엇이든 "보여 줄 수 없다"
            sick = f"{type(e).__name__}: {e}"
        if sick:
            return self._send(HTTPStatus.SERVICE_UNAVAILABLE, "text/plain; charset=utf-8",
                              f"unhealthy — {sick}\n".encode())
        return self._send(HTTPStatus.OK, "text/plain; charset=utf-8", b"ok\n")

    def _not_modified_since(self, st) -> bool:
        ims = self.headers.get("If-Modified-Since")
        if not ims or self.headers.get("If-None-Match"):
            return False
        try:
            return int(st.st_mtime) <= parsedate_to_datetime(ims).timestamp()
        except (TypeError, ValueError):
            return False


def _exit_when_redeployed(server: ThreadingHTTPServer, interval: float = 60.0) -> None:
    """이 파일이 바뀌면 끝낸다 — 배포(07:50 pull)가 옛 코드로 계속 도는 서버를 남기지 않게."""
    me = os.path.abspath(__file__)
    born = os.stat(me).st_mtime_ns
    while True:
        time.sleep(interval)
        try:
            if os.stat(me).st_mtime_ns != born:
                sys.stderr.write(f"[{time.strftime('%F %T')}] 코드가 바뀌었다 — 끝내고 크론이 다시 띄운다\n")
                server.shutdown()
                return
        except FileNotFoundError:
            server.shutdown()
            return


def single_report_root(path: str) -> str:
    """``--root`` 에 부모가 아니라 리포트 한 벌(``sector-flow-demo --out`` 결과 등)을 주면 그 한 벌을 보여 준다.

    목록은 기준일 이름의 하위 폴더만 본다 — 안 받으면 멀쩡한 폴더에 '리포트가 아직 없다' 와 ``/latest`` 404 가 뜬다.
    옆 임시 폴더에 ``<기준일>``·``latest`` 링크를 만들어 다른 경로와 같은 길로 낸다(원본 폴더엔 안 쓴다).
    """
    if not os.path.exists(os.path.join(path, "numbers.html")):
        return path
    try:
        with open(os.path.join(path, "payload.json"), encoding="utf-8") as f:
            day = json.load(f)["dates"][-1]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        day = None
    if not (isinstance(day, str) and DATE_RE.match(day)):
        raise SystemExit(f"{path} 는 리포트 한 벌인데 payload.json 에서 기준일을 못 읽었다 — --root 에는 기준일 폴더들의 부모를 주어라")
    view = tempfile.mkdtemp(prefix="sector-flow-serve.")
    atexit.register(shutil.rmtree, view, True)
    os.symlink(path, os.path.join(view, day))
    os.symlink(day, os.path.join(view, "latest"))
    print(f"리포트 한 벌을 연다: {path} ({day})", flush=True)
    return view


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.path.join(REPO, "reports"),
                    help="기준일 폴더들의 부모(reports) 또는 리포트 한 벌(sector-flow-demo --out 의 폴더)")
    ap.add_argument("--host", default="127.0.0.1", help="묶을 주소 (기본 127.0.0.1 — 밖에서 보려면 Tailscale 주소)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--soft-fail", metavar="이유", help="띄우지 않고 기동 실패만 센다(serve.sh 가 쓴다)")
    a = ap.parse_args(argv)
    Handler.root = os.path.abspath(a.root)
    if a.soft_fail:
        return soft_fail(Handler.root, a.soft_fail)
    Handler.root = single_report_root(Handler.root)
    try:
        srv = ThreadingHTTPServer((a.host, a.port), Handler)
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, errno.EADDRNOTAVAIL):
            return soft_fail(Handler.root, f"{a.host}:{a.port} 에 못 묶었다({e.strerror})")
        raise
    try:
        os.remove(os.path.join(Handler.root, ".serve.misses"))
    except OSError:
        pass
    # serve.sh 가 "떠 있는데 대답이 없다" 일 때 죽일 대상을 안다(잠금 주인은 flock 래퍼라 pid 를 모른다).
    try:
        with open(os.path.join(Handler.root, ".serve.pid"), "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    srv.daemon_threads = True
    # 재부팅·손으로 끈 것(SIGTERM)은 고장이 아니다 — 0 으로 끝나 알림을 안 보낸다.
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=srv.shutdown, daemon=True).start())
    threading.Thread(target=_exit_when_redeployed, args=(srv,), daemon=True).start()
    print(f"[{time.strftime('%F %T')}] http://{a.host}:{a.port}/ — {Handler.root}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
