"""``scripts/web_smoke.py`` — 진짜 Chromium 으로 두 웹 페이지를 1440·390 에서 열어 콘솔 오류·가로 넘침·'오늘' 패널을 본다.

판정(:func:`judge`)은 브라우저 없이 늘 돈다. 브라우저로 실제 페이지를 여는 검사는 Playwright 가 없으면 건너뛴다 —
주간 ``harness-selftest`` 워크플로가 ``SF_WEB_SMOKE_REQUIRED=1`` 로 Playwright 와 Chromium 을 깔고 돌린다(그때는
건너뛰지 않고 실패한다).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


def _mod(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


WS = _mod("web_smoke", REPO / "scripts" / "web_smoke.py")

CLEAN = {"console": [], "errors": [], "scroll_w": 390, "inner_w": 390,
         "today": {"w": 380, "h": 900, "top": 200, "display": "block", "visibility": "visible", "cards": 6}}


def test_judge_flags_console_errors_page_overflow_and_a_missing_today_panel():
    """주입: 판정에서 콘솔 오류·가로 넘침·'오늘' 패널 중 하나를 빼면 실패한다."""
    assert WS.judge("numbers.html", 390, CLEAN) == []
    assert WS.judge("viewer.html", 390, dict(CLEAN, today=None)) == []        # 뷰어에는 '오늘' 패널이 없다
    assert any("콘솔 오류" in x for x in WS.judge("numbers.html", 390, dict(CLEAN, console=["boom"])))
    assert any("페이지 예외" in x for x in WS.judge("viewer.html", 1440, dict(CLEAN, errors=["TypeError"])))
    assert any("가로 넘침" in x for x in WS.judge("numbers.html", 390, dict(CLEAN, scroll_w=412)))
    assert WS.judge("numbers.html", 390, dict(CLEAN, scroll_w=391)) == []      # 소수 픽셀 반올림 여유
    assert any("없다" in x for x in WS.judge("numbers.html", 390, dict(CLEAN, today=None)))
    hidden = dict(CLEAN["today"], display="none")
    assert any("안 보인다" in x for x in WS.judge("numbers.html", 390, dict(CLEAN, today=hidden)))
    empty = dict(CLEAN["today"], cards=0)
    assert any("비었다" in x for x in WS.judge("numbers.html", 390, dict(CLEAN, today=empty)))


def _pages(tmp: Path) -> Path:
    """합성 페이로드로 배치와 같은 치환(``__DATA__``·자료 범위 자리표시)을 해 두 페이지를 짓는다."""
    from sectorflow.basis import BASIS_SHORT_SLOT, BASIS_SLOT, DATA_BASIS, DATA_BASIS_SHORT

    SN = _mod("sector_numbers", REPO / "scripts" / "sector_numbers.py")
    SF = _mod("sector_flow", REPO / "scripts" / "sector_flow.py")
    PT = _mod("payload_today", REPO / "tests" / "test_payload_today.py")
    P = PT._payload()
    D = SN.build(P)
    tpl = (REPO / "scripts" / "templates" / "sector_numbers.html").read_text("utf-8")
    (tmp / "numbers.html").write_text(
        tpl.replace("__DATA__", json.dumps(D, ensure_ascii=False).replace("</", "<\\/"))
        .replace(BASIS_SHORT_SLOT, DATA_BASIS_SHORT).replace(BASIS_SLOT, DATA_BASIS), "utf-8")
    vt = (REPO / "scripts" / "templates" / "sector_flow.html").read_text("utf-8")
    (tmp / "viewer.html").write_text(vt.replace("__DATA__", SF.script_json(P)).replace(BASIS_SLOT, DATA_BASIS), "utf-8")
    return tmp


def test_chromium_opens_both_pages_at_desktop_and_phone_width_cleanly(tmp_path):
    # mutation-exempt: Playwright·Chromium 이 있는 주간 워크플로에서만 돈다 — 기본 CI·로컬 하네스에서는 건너뛰어 변이를 증명할 수 없다(판정은 위 검사가 변이로 지킨다)
    required = os.environ.get("SF_WEB_SMOKE_REQUIRED") == "1"
    if importlib.util.find_spec("playwright") is None:
        if required:
            pytest.fail("SF_WEB_SMOKE_REQUIRED=1 인데 Playwright 가 없다")
        pytest.skip("Playwright 없음 — uv run --with playwright pytest tests/test_web_smoke.py")
    try:
        problems = WS.smoke(str(_pages(tmp_path)))
    except WS.NoBrowser as e:
        if required:
            raise
        pytest.skip(str(e))
    assert problems == [], problems


# ── 브라우저 없이 스모크의 배선을 본다 — 가짜 playwright(같은 API 의 쓰는 부분만)로 ─────────────────────

class _Msg:
    def __init__(self, type_, text):
        self.type, self.text = type_, text


class _FakePW:
    """``sync_playwright()`` 의 쓰는 부분 — 페이지·폭마다 잰 값과 콘솔·예외를 미리 정해 둔다."""

    def __init__(self, facts=None, events=None, fail_default=False, fail_exe=False):
        self.facts, self.events = facts or {}, events or {}
        self.fail_default, self.fail_exe = fail_default, fail_exe
        self.launches, self.routes, self.shots = [], [], []

    # sync_playwright() 자리
    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def chromium(self):
        return self

    def launch(self, **kw):
        self.launches.append(kw)
        if (not kw and self.fail_default) or (kw and self.fail_exe):
            raise RuntimeError("no browser")
        return _Browser(self)


class _Browser:
    def __init__(self, pw):
        self.pw = pw

    def new_context(self, viewport):
        return _Ctx(self.pw, viewport["width"])

    def close(self):
        pass


class _Ctx:
    def __init__(self, pw, width):
        self.pw, self.width = pw, width

    def route(self, pattern, handler):
        self.pw.routes.append(pattern)

    def new_page(self):
        return _Page(self.pw, self.width)

    def close(self):
        pass


class _Page:
    def __init__(self, pw, width):
        self.pw, self.width, self.cb, self.name = pw, width, {}, None

    def on(self, ev, fn):
        self.cb[ev] = fn

    def goto(self, url, wait_until):
        self.name = url.rsplit("/", 1)[-1]
        for kind, payload in self.pw.events.get((self.name, self.width), []):
            self.cb[kind](payload)

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, js):
        return self.pw.facts.get((self.name, self.width), {k: v for k, v in CLEAN.items() if k not in ("console", "errors")})

    def screenshot(self, path):
        Path(path).write_bytes(b"png")
        self.pw.shots.append(Path(path).name)


def _fake(monkeypatch, pw):
    import types
    pkg, sub = types.ModuleType("playwright"), types.ModuleType("playwright.sync_api")
    sub.sync_playwright = pw
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sub)


def _folder(tmp_path) -> Path:
    for p in WS.PAGES:
        (tmp_path / p).write_text("<html></html>", "utf-8")
    return tmp_path


def test_smoke_judges_every_page_at_every_width_and_keeps_only_console_errors(tmp_path, monkeypatch):
    """두 쪽 × 두 폭을 다 연다, 콘솔은 error 만 센다(경고는 아니다), 외부 요청을 막는다, 캡처를 남긴다.

    주입: 콘솔 경고까지 오류로 세거나 외부 요청을 안 막으면 실패한다.
    """
    pw = _FakePW(facts={("numbers.html", 390): dict(scroll_w=500, inner_w=390, today=CLEAN["today"])},
                 events={("viewer.html", 1440): [("console", _Msg("warning", "slow")), ("console", _Msg("error", "boom")),
                                                ("pageerror", "TypeError: x")]})
    _fake(monkeypatch, pw)
    shots = tmp_path / "shots"
    problems = WS.smoke(str(_folder(tmp_path)), shots=str(shots))
    assert problems == ["numbers.html@390 가로 넘침: 문서 폭 500px > 창 390px",
                        "viewer.html@1440 콘솔 오류: boom", "viewer.html@1440 페이지 예외: TypeError: x"], problems
    assert sorted(pw.shots) == ["numbers-1440.png", "numbers-390.png", "viewer-1440.png", "viewer-390.png"]
    assert pw.routes and all(r.startswith("http") for r in pw.routes)
    (tmp_path / "viewer.html").unlink()
    assert any("viewer.html 이 없다" in x for x in WS.smoke(str(tmp_path)))


def test_smoke_opens_trust_and_list_pages_and_checks_demo_links(tmp_path, monkeypatch):
    """검증·품질은 있으면, 기준일 목록은 폴더가 기준일이면 — 목록 넘침·안내 페이지 결함은 두 쪽만 보면 못 잡았다. 데모 폴더는 링크도 본다.

    주입: 선택 쪽을 안 열거나 깨진 링크를 안 세면 실패한다.
    """
    day = tmp_path / "2026-10-30"
    day.mkdir()
    _folder(day)
    (day / "trust.html").write_text("<html></html>", "utf-8")
    (day / WS.DEMO_MARKER).write_text("[합성 데모]\n", "utf-8")
    base = {k: v for k, v in CLEAN.items() if k not in ("console", "errors")}
    pw = _FakePW(facts={("viewer.html", 390): dict(base, links=["numbers.html", "#x", "https://a.b/c", "../latest/trust.html"]),
                        ("trust.html", 1440): dict(base, scroll_w=409, inner_w=390)})
    _fake(monkeypatch, pw)
    shots = tmp_path / "shots"
    problems = WS.smoke(str(day), shots=str(shots))
    assert {"trust-390.png", "index-390.png", "trust-1440.png", "index-1440.png"} <= set(pw.shots), pw.shots
    assert problems == ["viewer.html@390 깨진 링크: ../latest/trust.html",
                        "trust.html@1440 가로 넘침: 문서 폭 409px > 창 390px"], problems
    (day / WS.DEMO_MARKER).unlink()
    assert not any("깨진 링크" in x for x in WS.smoke(str(day))), "실 리포트의 ../latest 링크는 게시 루트에서 열린다"


def test_smoke_exit_codes_and_the_chromium_fallback(tmp_path, monkeypatch, capsys):
    """0 통과 · 1 문제 · 2 환경(Playwright·Chromium 없음). 기본 Chromium 이 없으면 SF_CHROMIUM 으로 띄운다.

    주입: 문제가 있는데 0 으로 끝나거나, 기본 실패 때 대체 경로를 안 쓰면 실패한다.
    """
    folder = str(_folder(tmp_path))
    _fake(monkeypatch, _FakePW())
    assert WS.main(["--dir", folder]) == 0
    _fake(monkeypatch, _FakePW(facts={("numbers.html", 1440): dict(CLEAN, today=None)}))
    assert WS.main(["--dir", folder]) == 1 and "'오늘 한 장'(#oneday)이 없다" in capsys.readouterr().out

    exe = tmp_path / "chrome"
    exe.write_text("")
    monkeypatch.setenv("SF_CHROMIUM", str(exe))
    pw = _FakePW(fail_default=True)
    _fake(monkeypatch, pw)
    assert WS.main(["--dir", folder]) == 0 and pw.launches[-1] == {"executable_path": str(exe)}
    _fake(monkeypatch, _FakePW(fail_default=True, fail_exe=True))
    assert WS.main(["--dir", folder]) == 2
    monkeypatch.delenv("SF_CHROMIUM")
    monkeypatch.setattr(WS, "_chromium_path", lambda: None)
    _fake(monkeypatch, _FakePW(fail_default=True))
    assert WS.main(["--dir", folder]) == 2
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)        # import 가 ImportError 를 낸다
    assert WS.main(["--dir", folder]) == 2
