"""검증·품질 페이지(`scripts/trust_page.py`) — 픽스처 리포트 루트에서 만들어 내용을 본다."""
from __future__ import annotations

import html
import http.client
import json
import importlib.util
import os
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone
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
        REBUILT_txt="2026-11-08 10:00 현재 코드(abc)·확정 데이터로 다시 만듦; 원본은 /x/.archive/2026-11-06.1\n"
                    "commit: abcdef0123456789\nfinalize: verified\n")
    crash = LAYERS.format(d="2026-11-09") + "Traceback (most recent call last):\nTypeError: boom\n"
    day("2026-11-09", **base, VERIFY_txt=crash, VERIFY_FAILED_txt=crash)
    # VERIFY.txt 없이 VERIFY_FAILED.txt 만 남은 폴더 — 실패 이름에 HTML 이 섞여 있다(이스케이프 확인).
    day("2026-11-10", **base, VERIFY_FAILED_txt=LAYERS.format(d="2026-11-10")
        + "\n검사 9건 · 실패 1건\n실패 목록: E/<b>굵게</b> 열\n")
    # 문서 주장 구분 이전 형식의 요약 + CLAIMS.txt — 건수는 CLAIMS.txt 에서 읽는다.
    day("2026-11-11", **base, VERIFY_txt=_pass("2026-11-11", n=5), CLAIMS_txt="문서 주장 어긋남\n\n- G/x\n- G/y\n- G/z\n")
    day("2026-11-12", INCOMPLETE_txt="완전성 관문 — 기준일 2026-11-12 · 기준 직전 5거래일 중앙값 · 문턱 97%\n"
                                     "  [FAIL] 2026-11-12 시장 목록이 비었다 — 비교할 수 없다\n")
    # daily_finalize 창 밖의 재구성 — 마감 값이지만 확정 기록이 없다(FINAL.txt 없음, finalize: unverified).
    day("2026-11-13", **base, VERIFY_txt=_pass("2026-11-13"),
        REBUILT_txt="2026-11-09 05:12 현재 코드(abcdef0123456789)·마감 데이터(daily_finalize 창 밖 — 확정 기록 없음)로 다시 만듦\n"
                    "commit: abcdef0123456789\nfinalize: unverified\n")
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
    days = page.split("기준일별 기록")[1]           # 정정 표에도 <td>기준일</td> 행이 있다 — 기준일별 표만 본다
    m = re.search(rf"<tr><td>{d}</td>(.*?)</tr>", days, re.S)
    assert m, f"{d} 줄이 없다"
    return m.group(1)


def _text(page):
    return html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<style>.*?</style>", "", page, flags=re.S)))


def _tiles(page):
    return {k: int(n) for n, k in re.findall(r'<div class="tile"><b>(\d+)</b><span>([^<]+)</span>', page)}


def _qrow(page, qid):
    m = re.search(rf'<tr data-qid="{re.escape(qid)}">(.*?)</tr>', page, re.S)
    assert m, f"질문 {qid} 행이 없다"
    return _text(m.group(1))


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
    assert ">확정<" in r06                              # FINAL.txt(확정 확인) + 재구성 — 둘 다 단다(serve_reports 와 같다)
    r13 = _row(page, "2026-11-13")
    assert ">확정 기록 없음<" in r13 and "재구성" in r13 and ">확정<" not in r13 and ">잠정<" not in r13
    r09 = _row(page, "2026-11-09")
    assert "중단" in r09 and "요약 줄 없음" in r09
    assert '<span class="cell fail" title="2026-11-09' in page
    assert "building" not in page


def test_failure_reasons_and_tiles_count_what_happened(page):
    assert "G/도움말·한계의 동시성 범위가 오늘 실측을 덮는가" in page
    tiles = _tiles(page)
    assert tiles["기준일 폴더"] == 10
    assert tiles["자기 검증 실패"] == 3           # 11-02 실패 · 11-09 검사기 중단 · 11-10 VERIFY_FAILED 만
    assert tiles["자기 검증 통과"] == 5           # 11-03·11-11 은 문서 주장 어긋남이 있는 통과
    assert tiles["통과 · 문서 주장 어긋남"] == 2
    assert tiles["수집 미완(안 만듦)"] == 2
    assert tiles["재구성"] == 2 and tiles["확정"] == 2 and tiles["확정 기록 없음"] == 1   # 확정 = 11-05 + 재구성 뒤 확정 확인 11-06


#: 질문 id → 판정 칸에 있어야 할 말과 설명에 있어야 할 말. 행을 지우거나 판정을 유리하게 고치면 빨개진다.
EXPECTED_ROWS = {
    "Q3d-sector": ("증거 없음", "배제하지 못한다"),
    "Q3d-G": ("판정 불가", "중앙 2"),
    "x": ("사실상 섹터 20일 반전", "0.82~0.93"),
    "S1-S3": ("증거 없음", "여섯 칸 모두 탈락"),
    "K1": ("쓸 수 있는 형태는 못 보임", "K1>0"),
    "K2-K3": ("미확인 · 탈락", "K3 는 발견에서 탈락"),
    "untested": ("검정된 적 없음", "종목 선정"),
    "Q1": ("불합격", "미해명 16셀"),
    "Q2": ("둘은 남김", "그때 남긴 것"),
    "Q3a": ("G 불합격", "62.5%"),
    "Q3b": ("코스닥 불안정", "코스닥 71%"),
    "Q3c": ("약함", "42%"),
}
#: K1 의 단서 — 하나라도 빠지면 확인된 음의 IC 가 쓸 수 있는 것처럼 읽힌다.
K1_CAVEATS = ("역효과", "확인 구간 IC 는 증거 없음", "왕복 비용을 빼면 발견 구간에서 진다", "공매도 없는 계좌에서 실현할 수 없다",
              "단기 반전", "생존편향")
LIMITS = {"approx", "session", "survivorship", "labels", "inst-detail", "provisional", "weak-defs", "untested",
          "declared-checks"}


def test_every_unfavorable_result_row_is_on_the_page_with_its_verdict(page):
    """불리한 결과를 뺀 페이지는 이 페이지의 존재 이유를 잃는다 — 질문마다 행과 판정을 본다."""
    assert set(re.findall(r'<tr data-qid="([^"]+)"', page)) == set(EXPECTED_ROWS)
    for qid, (verdict, why) in EXPECTED_ROWS.items():
        row = _qrow(page, qid)
        assert verdict in row and why in row, (qid, verdict, why)
    k1 = _qrow(page, "K1")
    for c in K1_CAVEATS:
        assert c in k1, c


def test_every_limitation_is_on_the_page(page):
    # 속성이 더 붙으면(hidden 등) 안 보이는 항목이다 — 정확히 이 꼴만 센다.
    assert set(re.findall(r'<li data-limit="([^"]+)">', page)) == LIMITS
    t = _text(page)
    for must in ("순매수 금액은 근사다", "애프터마켓 포함 통합값", "2026-09-14", "생존편향", "섹터 라벨은 지금의 분류다",
                 "중앙 2", "검정된 적 없는 값"):
        assert must in t, must


def test_preregistration_wording_claims_only_commit_order_and_prints_gaps(page):
    """커밋 시각은 '결과 문서보다 먼저 커밋' 만 보여준다 — 분석 전 고정의 증명처럼 쓰지 않고 간격을 적는다."""
    t = _text(page)
    assert "그 순서뿐이다" in t and "증명은 아니다" in t
    assert "커밋 시각이 그 순서를 보여준다" not in t
    if tp.git_info("6ca326a") is None:
        pytest.skip("연구 커밋이 없다(얕은 클론)")
    assert "간격 10분" in _qrow(page, "Q3d-sector")
    assert re.search(r"간격이 \d+~\d+분이라", t)
    for r in tp.VALIDATION + tp.CHECKS:
        for _label, pre, res in r["links"]:
            if pre:
                assert f"/blob/{pre[1]}/{pre[0]}" in page and f"/blob/{res[1]}/{res[0]}" in page


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
    pairs = {(pre, res) for r in tp.VALIDATION + tp.CHECKS for _l, pre, res in r["links"] if pre}
    assert pairs
    for (ppath, ph), (rpath, rh) in pairs:
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


def test_window_limits_strip_table_and_tiles(root):
    page = tp.render(str(root), window=3)
    assert len(re.findall(r'<span class="cell ', page)) - 5 == 3     # 범례 칸 5개
    days = page.split("기준일별 기록")[1]                             # 정정 표는 창과 무관하게 전 기간이다
    assert len(re.findall(r"<tr><td>\d{4}-\d{2}-\d{2}</td>", days)) == 3
    assert _tiles(page)["기준일 폴더"] == 3 and "최근 3개 기준일" in page
    assert "<tr><td>2026-11-02</td>" not in page and 'title="2026-11-02' not in page


def test_verify_failed_only_and_claims_txt_fallbacks(page):
    r10 = _row(page, "2026-11-10")
    assert "실패 1" in r10 and "VERIFY_FAILED.txt" in r10
    r11 = _row(page, "2026-11-11")
    assert "3건" in r11 and '<span class="cell claim" title="2026-11-11' in page


def test_names_from_the_data_are_escaped(page):
    assert "<b>굵게</b>" not in page and "&lt;b&gt;굵게&lt;/b&gt;" in page


def test_gate_with_no_markets_at_all_is_shown_as_blocked(page):
    r12 = _row(page, "2026-11-12")
    assert "막힘" in r12 and "시장 목록이 비었다" in r12


# ─────────────────────────────── 운영 사건 원장(events.jsonl) — SLO·정정

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=timezone(timedelta(hours=9)))     # KST — TZ 환경변수와 무관해야 한다


def _ev(**kw):
    return json.dumps(kw, ensure_ascii=False)


@pytest.fixture
def ledger(root):
    """게시·확정·관문·프로브 사건 — 제때 2일, 늦음 1일, 확정 목표 내 1일, 프로브 4회 중 3회 OK."""
    rows = [
        # 11-02: 잠정 제때(18:12 KST), 검증 실패. 확정 기록 없음 → 기한 지나 미확정.
        _ev(ts="2026-11-02T09:12:00Z", event="report", asof="2026-11-02", mode="normal", rc=1,
            outcome="published", gate="pass", verify="fail", final=False, diff=None),
        # 11-05: 잠정 제때 + 다음날 08:25 KST 확정(diff 5 → 확정 정정).
        _ev(ts="2026-11-05T09:12:00Z", event="report", asof="2026-11-05", mode="normal", rc=0,
            outcome="published", gate="pass", verify="pass", final=False, diff=None),
        _ev(ts="2026-11-05T23:25:00Z", event="report", asof="2026-11-05", mode="final", rc=0,
            outcome="final", gate="pass", verify="pass", final=True, diff=5),
        # 11-06: 재구성만(잠정 게시 기록 없음).
        _ev(ts="2026-11-08T01:00:00Z", event="report", asof="2026-11-06", mode="rebuild", rc=0,
            outcome="rebuilt", gate="pass", verify="pass", final=True, diff=None, commit="abcdef0123456789"),
        # 11-01: 폴더가 없는 날의 보관 사건 — 원장만 아는 정정이다.
        _ev(ts="2026-11-08T01:05:00Z", event="archive", asof="2026-11-01"),
        # 11-11: 잠정이 늦었다(19:00 KST).
        _ev(ts="2026-11-11T10:00:00Z", event="report", asof="2026-11-11", mode="normal", rc=0,
            outcome="published", gate="pass", verify="pass", final=False, diff=None),
        # 11-12: 관문 차단.
        _ev(ts="2026-11-12T09:11:00Z", event="report", asof="2026-11-12", mode="normal", rc=75,
            outcome="incomplete", gate="blocked", verify=None, final=False, diff=None),
        _ev(ts="2026-11-13T00:00:00Z", event="probe", status=200, ok=True),
        _ev(ts="2026-11-13T00:05:00Z", event="probe", status=200, ok=True),
        _ev(ts="2026-11-13T00:10:00Z", event="probe", status=503, ok=False),
        _ev(ts="2026-11-13T00:15:00Z", event="probe", status=200, ok=True),
    ]
    (root / "events.jsonl").write_text("\n".join(rows) + "\n", "utf-8")
    return root


def test_slo_card_counts_come_from_the_ledger(ledger):
    page = tp.render(str(ledger), now=NOW)
    slo = page.split("운영 SLO")[1].split("정정 —")[0]
    assert "제때 2일" in slo and "늦음 1일 — 2026-11-11(11-11 19:00)" in slo
    assert "기록 없음 1일" in slo                                    # 11-06 — 재구성만 남은 날
    assert "목표 내 1일" in slo and "기한 지나 미확정 2일 — 2026-11-02, 2026-11-11" in slo
    assert "관문 차단</b> — 2026-11-12" in slo and "검증 실패</b> — 2026-11-02" in slo
    assert "4회 중 200 OK 3회 (75.0%)" in slo
    assert "docs/slo.md" in slo


def test_slo_without_ledger_says_so_instead_of_pretending(page):
    """원장이 없으면 SLO 를 폴더로 흉내 내지 않는다 — 지워진 흔적으로 재면 준시성이 과장된다."""
    slo = page.split("운영 SLO")[1].split("정정 —")[0]
    assert "원장(<code>events.jsonl</code>)이 아직 없다" in slo
    assert "제때" not in slo and "%" not in re.sub(r"<[^>]+>", "", slo).replace("97%", "")


def test_probe_only_ledger_shows_probes_but_not_fake_timeliness(root):
    """지금 실서버 상태 — 원장에 프로브만 있다. 응답률은 싣되 게시 준시성을 0일 집계로 그리면 안 된다."""
    rows = [_ev(ts="2026-11-13T00:00:00Z", event="probe", status=200, ok=True),
            _ev(ts="2026-11-13T00:05:00Z", event="probe", status=503, ok=False)]
    (root / "events.jsonl").write_text("\n".join(rows) + "\n", "utf-8")
    slo = tp.render(str(root), now=NOW).split("운영 SLO")[1].split("정정 —")[0]
    assert "2회 중 200 OK 1회 (50.0%)" in slo and "아직 게시 사건이 없다" in slo
    assert "제때" not in slo and "목표 내" not in slo


def test_errata_come_from_the_ledger_when_it_has_history(ledger):
    page = tp.render(str(ledger), now=NOW)
    err = page.split("정정 —")[1].split("기준일별 기록")[0]
    assert "ops_ledger.py --errata" in err
    assert "2026-11-01" in err                                       # 폴더에 없는 날 — 원장만 아는 보관 사건
    assert "2026-11-06" in err and "재구성" in err
    assert "2026-11-05" in err and "섹터 칸 5개 변경" in err
    assert "하한" not in err                                          # 폴더 출처 문구가 아니다


def test_errata_fall_back_to_folder_markers_without_a_ledger(page):
    err = page.split("정정 —")[1].split("기준일별 기록")[0]
    assert "REBUILT.txt" in err and "하한" in err                     # 출처와 한계를 같이 적는다
    rows = {(m.group(1), m.group(2)) for m in
            re.finditer(r"<tr><td>(\d{4}-\d{2}-\d{2})</td><td>([^<]+)</td>", err)}
    assert rows == {("2026-11-05", "확정 정정"), ("2026-11-06", "재구성"), ("2026-11-13", "재구성")}
    assert "finalize unverified" in err and "commit abcdef012345" in err


def test_no_report_folder_means_no_page_rather_than_an_unserved_root_file(tmp_path):
    empty = tmp_path / "reports"
    empty.mkdir()
    assert tp.main(["--root", str(empty)]) == 1
    assert not (empty / "trust.html").exists()
