"""검증·품질 페이지(`scripts/trust_page.py`) — 픽스처 리포트 루트에서 만들어 내용을 본다."""
from __future__ import annotations

import http.client
import importlib.util
import os
import re
import subprocess
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tp = _load("trust_page")
srv = _load("serve_reports")

LAYERS = """검증 대상: /x/reports/{d}
  기준일 {d} · 거래일 260 · 섹터 27 · 블록 12

A. 수식 — 표의 값이 정의된 항등식을 만족하는가
  [OK  ] A · a = 임펄스/시총×100   최대오차 0.00e+00

F. 독립 재계산 — DB 원자료에서 다시 만든 값과 같은가
  [OK  ] F · 유량 일별   0/70200 불일치
"""


def _pass(d, n=144, claims=None):
    tail = f"검사 {n}건 · 실패 0건" + (f" · 문서 주장 어긋남 {claims}건" if claims is not None else "")
    return LAYERS.format(d=d) + f"\n{tail}\n전부 통과\n"


GATE_PASS = """완전성 관문 — 기준일 {d} · 기준 직전 5거래일 중앙값 · 문턱 97%
  [PASS] 거래소: 수급 800/803 · 시총 800/803
  [PASS] 코스닥: 수급 1820/1827 · 시총 1823/1827
"""
GATE_FAIL = """완전성 관문 — 기준일 {d} · 기준 직전 5거래일 중앙값 · 문턱 97%
  [PASS] 거래소: 수급 803/803 · 시총 803/803
  [FAIL] 코스닥: 수급 1099/1827 < 97% · 시총 종목수 0/1827 < 97%
"""


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "reports"

    def day(d, **files):
        p = r / d
        p.mkdir(parents=True)
        for name, body in files.items():
            (p / name.replace("_txt", ".txt").replace("_html", ".html").replace("_json", ".json")).write_text(
                body, "utf-8")
        return p

    base = {"numbers_html": "<html>", "payload_json": "{}", "viewer_html": "<html>"}
    old_fail = (LAYERS.format(d="2026-11-02") + "\n  [FAIL] G · 동시성 범위\n\n검사 137건 · 실패 1건\n"
                "실패 목록: G/도움말·한계의 동시성 범위가 오늘 실측을 덮는가(β제거 끈 상태)\n")
    day("2026-11-02", **base, VERIFY_txt=old_fail, VERIFY_FAILED_txt=old_fail)
    day("2026-11-03", **base, VERIFY_txt=_pass("2026-11-03", claims=2),
        CLAIMS_txt="문서 주장 어긋남\n\n- G/a\n- G/b\n")
    day("2026-11-04", INCOMPLETE_txt="[..] 2026-11-04 수집 미완\n" + GATE_FAIL.format(d="2026-11-04"))
    day("2026-11-05", **base, VERIFY_txt=_pass("2026-11-05"),
        FINAL_txt="[2026-11-06 08:20:00] daily_finalize 확인 뒤 확정치로 만들었다.\n"
                  "잠정 → 확정 비교 — 바꿔 끼운 잠정 리포트(2026-11-05 18:10 KST 생성) 대비\n"
                  "시장 합계: 12칸 중 2칸 바뀜\n  KOSPI 기관 1일  +1.00 → +2.00 (+1.00)\n"
                  "섹터 칸: 240칸 중 5칸 바뀜 — 큰 순 5\n")
    day("2026-11-06", **base, VERIFY_txt=_pass("2026-11-06"),
        FINAL_txt="[..] 재구성 — 기준일 뒤에 확정된 DB 값으로 만들었다(REBUILT.txt).\n"
                  "잠정 → 확정 비교 없음: 재구성 리포트다 — 옛 리포트와 비교하지 않는다\n",
        REBUILT_txt="2026-11-08 10:00 현재 코드(abc)·확정 데이터로 다시 만듦; 원본은 /x/.archive/2026-11-06.1\n")
    crash = LAYERS.format(d="2026-11-09") + "Traceback (most recent call last):\nTypeError: boom\n"
    day("2026-11-09", **base, VERIFY_txt=crash, VERIFY_FAILED_txt=crash)
    (r / ".archive" / "2026-11-05-provisional").mkdir(parents=True)
    (r / "2026-11-10.building.77").mkdir()
    (r / "cron.log").write_text(
        "DB 접속 실패 postgres://user:hunter2@db\n"
        + GATE_FAIL.format(d="2026-11-05") + "[..] 수집 미완\n"
        + GATE_PASS.format(d="2026-11-05") + "[..] 생성 시작\n"
        + GATE_PASS.format(d="2026-11-03"), "utf-8")
    os.symlink(r / "2026-11-06", r / "latest")
    return r


@pytest.fixture
def page(root, tmp_path):
    out = tmp_path / "trust.html"
    assert tp.main(["--root", str(root), "--out", str(out)]) == 0
    return out.read_text("utf-8")


def _row(page, d):
    m = re.search(rf"<tr><td>{d}</td>(.*?)</tr>", page, re.S)
    assert m, f"{d} 줄이 없다"
    return m.group(1)


def _text(page):
    return re.sub(r"<[^>]+>", " ", re.sub(r"<style>.*?</style>", "", page, flags=re.S))


def test_each_report_folder_gets_its_state_verify_gate_and_final_diff(page):
    """폴더마다 잠정/확정/재구성·검증 요약·관문 수치·잠정→확정 변경 칸 수가 그 폴더의 기록대로 실린다."""
    r02 = _row(page, "2026-11-02")
    assert "잠정" in r02 and "실패 1" in r02 and "136/137" in r02 and "VERIFY_FAILED.txt" in r02
    r03 = _row(page, "2026-11-03")
    assert "통과" in r03 and "2건" in r03 and "최저 99.6%" in r03  # 1820/1827 = 99.6%
    r04 = _row(page, "2026-11-04")
    assert "수집 미완" in r04 and "막힘" in r04 and "1099/1827" in r04
    r05 = _row(page, "2026-11-05")
    assert "확정" in r05 and "시장 2/12" in r05 and "섹터 5/240칸" in r05 and "원본 보관 1" in r05
    # 같은 기준일 관문이 크론 로그에 두 번 — 막힌 뒤 통과했으면 마지막(통과)이 그날의 관문이다.
    assert "통과 · 최저" in r05 and "막힘" not in r05
    r06 = _row(page, "2026-11-06")
    assert "재구성" in r06 and "REBUILT.txt" in r06 and "비교 없음" in r06
    assert ">확정<" not in r06
    r09 = _row(page, "2026-11-09")
    assert "중단" in r09 and "요약 줄 없음" in r09
    assert '<span class="cell fail" title="2026-11-09' in page
    assert "building" not in page


def test_failure_reasons_and_tiles_count_what_happened(page):
    assert "G/도움말·한계의 동시성 범위가 오늘 실측을 덮는가" in page
    tiles = dict((k, int(n)) for n, k in re.findall(r'<div class="tile"><b>(\d+)</b><span>([^<]+)</span>', page))
    assert tiles["기준일 폴더"] == 6
    assert tiles["자기 검증 실패"] == 2           # 11-02 실패 · 11-09 검사기 중단
    assert tiles["자기 검증 통과"] == 3
    assert tiles["통과 · 문서 주장 어긋남"] == 1
    assert tiles["수집 미완(안 만듦)"] == 1
    assert tiles["재구성"] == 1 and tiles["확정"] == 1


def test_unfavorable_results_and_limitations_are_on_the_page(page):
    """불리한 결과를 뺀 페이지는 이 페이지의 존재 이유를 잃는다."""
    t = _text(page)
    for must in ("증거 없음", "판정 불가", "사실상 섹터 20일 반전", "쓸 수 있는 형태는 못 보임",
                 "역효과", "불합격", "코스닥 불안정", "G 불합격"):
        assert must in t, must
    for must in ("순매수 금액은 근사다", "애프터마켓 포함 통합값", "2026-09-14", "생존편향",
                 "섹터 라벨은 지금의 분류다"):
        assert must in t, must
    for commit in ("bd27017", "6ca326a", "b702dc4"):
        assert commit in page
    assert "docs/formulas.md" in page and "docs/validation/2026-11-08-results.md" in page


def test_no_banned_words_no_signed_returns_no_leaked_log(page):
    t = _text(page)
    assert tp.banned_hits(page) == []
    for w in ("추천", "매수 신호", "급등", "수익 보장", "적중"):
        assert w not in page
    assert not re.findall(r"[+−-]\d+(?:\.\d+)?\s*%", t), "수익률처럼 읽히는 부호 붙은 % 가 있다"
    assert "hunter2" not in page and "postgres://" not in page
    assert "{{" not in page and 'name="viewport"' in page


def test_a_banned_word_coming_from_the_data_blocks_the_write(root, tmp_path):
    """검사 이름 같은 데이터로 금지어가 들어오면 페이지를 쓰지 않고 2 로 끝난다."""
    for name in ("VERIFY.txt", "VERIFY_FAILED.txt"):
        bad = root / "2026-11-02" / name
        bad.write_text(bad.read_text("utf-8").replace("동시성 범위가", "급등 범위가"), "utf-8")
    out = tmp_path / "x.html"
    assert tp.main(["--root", str(root), "--out", str(out)]) == 2
    assert not out.exists()


def test_default_output_lands_in_the_latest_folder_and_the_web_serves_it(root):
    """웹은 기준일 폴더의 허용 목록 파일만 내보낸다 — 기본 출력이 latest 폴더이고 /latest/trust.html 로 열린다."""
    assert tp.main(["--root", str(root)]) == 0
    assert (root / "2026-11-06" / "trust.html").exists()
    srv.Handler.root = str(root)
    s = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        def get(path):
            c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
            c.request("GET", path)
            resp = c.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        st, h, _ = get("/latest/trust.html")
        assert st == 302 and h["Location"] == "/2026-11-06/trust.html"
        st, h, body = get("/2026-11-06/trust.html")
        assert st == 200 and h["Content-Type"].startswith("text/html") and "검증·품질" in body.decode()
    finally:
        s.shutdown()
        s.server_close()


def test_layers_come_from_the_latest_verify(page):
    assert "수식 — 표의 값이 정의된 항등식을 만족하는가" in page
    assert "독립 재계산 — DB 원자료에서 다시 만든 값과 같은가" in page


def _git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


def test_preregistration_commits_are_real_and_precede_their_results():
    """사전등록 해시 = 그 파일을 처음 넣은 커밋이고, 결과 커밋의 조상이며, 시각도 앞선다."""
    if _git("rev-parse", "--git-dir").returncode != 0:
        pytest.skip("git 저장소가 아니다")
    for *_, (ppath, ph), (rpath, rh) in tp.VALIDATION + tp.CHECKS:
        if _git("cat-file", "-e", f"{ph}^{{commit}}").returncode or _git("cat-file", "-e", f"{rh}^{{commit}}").returncode:
            pytest.skip("얕은 클론이라 연구 커밋이 없다")
        added = _git("log", "--diff-filter=A", "--format=%h", "--abbrev=7", "--", ppath).stdout.split()
        assert added and added[-1].startswith(ph), (ppath, ph, added)
        touched = _git("log", "--format=%h", "--abbrev=7", "--", rpath).stdout.split()
        assert any(t.startswith(rh) for t in touched), (rpath, rh)
        assert _git("merge-base", "--is-ancestor", ph, rh).returncode == 0, (ph, rh)
        tp_, tr = (int(_git("log", "-1", "--format=%ct", h).stdout) for h in (ph, rh))
        assert tp_ < tr, (ph, rh)


def test_parse_verify_reads_both_summary_formats_and_a_crash():
    old = tp.parse_verify("  [OK  ] A · x\n  [FAIL] B · y\n\n검사 2건 · 실패 1건\n실패 목록: B/y\n")
    assert old == {"checks": 2, "fails": 1, "claims": 0, "complete": True, "fail_names": ["B/y"]}
    new = tp.parse_verify("  [CLAIM] G · z\n\n검사 3건 · 실패 0건 · 문서 주장 어긋남 1건\n전부 통과\n")
    assert new["claims"] == 1 and new["fails"] == 0 and new["complete"]
    crash = tp.parse_verify("  [OK  ] A · x\n  [FAIL] A · y\nTraceback\n")
    assert crash["complete"] is False and crash["checks"] == 2 and crash["fails"] == 1
