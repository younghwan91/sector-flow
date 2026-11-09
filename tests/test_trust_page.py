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
    (r / ".archive" / "2026-11-05-provisional" / "numbers.html").write_text("<html>", "utf-8")
    (r / ".archive" / "rebuild-logs" / "2026-11-05").mkdir(parents=True)     # 리포트가 없는 묶음은 보관본이 아니다
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
    assert tiles["지금 판 검증 실패"] == 3         # 11-02 실패 · 11-09 검사기 중단 · 11-10 VERIFY_FAILED 만
    assert tiles["지금 판 검증 통과"] == 5         # 11-03·11-11 은 문서 주장 어긋남이 있는 통과
    assert tiles["첫 게시 검증 실패"] == 3         # 재구성 아닌 날은 지금 판이 곧 첫 게시다(원장 없음)
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
LIMITS = {"approx", "session", "survivorship", "labels", "inst-detail", "provisional", "weak-defs", "universe",
          "untested", "declared-checks"}


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
                 "중앙 2", "검정된 적 없는 값", "우선주", "139종목", "약 160조", "약 1.04조", "3.6%"):
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
            if pre:   # 그 커밋 시점 문서의 사본으로 잇는다(저장소가 비공개라 GitHub 링크는 익명에게 404 다)
                assert f"validation/{pre[1]}-{os.path.basename(pre[0])}" in page
                assert f"validation/{res[1]}-{os.path.basename(res[0])}" in page


def test_no_banned_words_no_signed_returns_no_leaked_log(page):
    t = _text(page)
    assert tp.banned_hits(page) == []
    for w in ("추천", "매수 신호", "신호", "급등", "수익 보장", "적중", "주도주"):
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


def _serve(root):
    srv.Handler.root = str(root)
    s = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()

    def get(path):
        c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
        c.request("GET", path)
        resp = c.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    return s, get


def _build(root, asof="2026-11-14", **files):
    """배치가 짓는 폴더 — 아직 게시 전이다."""
    b = root / f"{asof}.building.77"
    b.mkdir()
    base = {"numbers.html": "<html>", "payload.json": "{}", "viewer.html": "<html>", "VERIFY.txt": _pass(asof), **files}
    for name, body in base.items():
        (b / name).write_text(body, "utf-8")
    return b


def test_build_mode_writes_inside_the_build_folder_and_counts_that_day(root):
    """배치의 길 — 짓는 폴더에 페이지와 문서 사본을 쓰고, 그날 줄은 곧 바꿔 낄 그 폴더의 기록이다."""
    b = _build(root, **{"FINAL.txt": "[..] daily_finalize 확인 뒤 확정치로 만들었다.\n"})
    assert tp.main(["--root", str(root), "--build", str(b), "--asof", "2026-11-14"]) == 0
    page = (b / "trust.html").read_text("utf-8")
    assert not (root / "2026-11-14").exists() and list(root.glob("20*/trust.html")) == [b / "trust.html"], "게시된 폴더를 건드렸다"
    assert (b / "validation" / "formulas.md").exists()
    assert "기준 폴더 2026-11-14" in page
    assert ">확정<" in _row(page, "2026-11-14") and "<tr><td>2026-11-02</td>" in page
    assert _tiles(page)["기준일 폴더"] == 11


def test_a_published_folder_cannot_be_rewritten(root, tmp_path):
    """게시된 폴더의 파일 하나를 고쳐 쓰는 길은 없다 — 바로든 latest 링크로든. 원하면 그 기준일을 다시 만든다."""
    for out in (root / "2026-11-06" / "trust.html", root / "latest" / "trust.html",
                root / "2026-11-06" / "x" / "trust.html"):
        assert tp.main(["--root", str(root), "--out", str(out)]) == 2
    assert not list(root.glob("20*/trust.html"))
    with pytest.raises(SystemExit):
        tp.main(["--root", str(root)])                                   # 어디에 쓸지 안 주면 쓰지 않는다
    assert not list(root.glob("20*/trust.html"))
    assert tp.main(["--root", str(root), "--build", str(root / "x.building.1")]) == 2


def test_every_document_link_opens_on_the_private_web_and_none_goes_to_github(root):
    """비공개 레포의 GitHub 링크는 익명에게 404 다 — 문서 사본을 웹이 내보내고, 페이지의 링크가 모두 실제로 열린다."""
    b = _build(root)
    assert tp.main(["--root", str(root), "--build", str(b), "--asof", "2026-11-14"]) == 0
    b.rename(root / "2026-11-14")
    (root / "latest").unlink()
    (root / "latest").symlink_to(root / "2026-11-14")
    page = (root / "2026-11-14" / "trust.html").read_text("utf-8")
    assert "github.com" not in page
    hrefs = sorted(set(re.findall(r'href="(validation/[^"]+)"', page)))
    assert any(h.endswith("/formulas.md") for h in hrefs) and any(h.endswith("-2026-11-08-results.md") for h in hrefs)
    s, get = _serve(root)
    try:
        for h in hrefs:
            st, hdr, body = get(f"/2026-11-14/{html.unescape(h)}")
            assert st == 200 and hdr["Content-Type"].startswith("text/plain") and body, h
        st, hdr, _ = get("/latest/trust.html")
        assert st == 302 and hdr["Location"] == "/2026-11-14/trust.html"
        st, hdr, body = get("/2026-11-14/trust.html")
        assert st == 200 and hdr["Content-Type"].startswith("text/html") and "검증·품질" in body.decode()
    finally:
        s.shutdown()
        s.server_close()


def test_pinned_document_copy_is_that_commits_text_not_head(tmp_path):
    """링크 칸은 '그 커밋 시점의 문서' 라 적는다 — 사본이 지금 판이면 그 말이 거짓이다."""
    if _git("cat-file", "-e", "dae572f^{commit}").returncode:
        pytest.skip("얕은 클론이라 연구 커밋이 없다")
    docs = tp.export_docs(str(tmp_path))
    got = (tmp_path / docs[(tp.RESULTS, "dae572f")]).read_bytes()      # 이 문서는 그 뒤에도 커밋됐다 — 지금 판과 다르다
    assert got == subprocess.run(["git", "-C", str(REPO), "show", f"dae572f:{tp.RESULTS}"], capture_output=True).stdout
    assert got != (REPO / tp.RESULTS).read_bytes()


def test_report_pages_link_the_private_copy_not_the_private_repo():
    for name in ("sector_numbers.html", "sector_flow.html"):
        t = (REPO / "scripts" / "templates" / name).read_text("utf-8")
        assert "github.com/younghwan91/sector-flow" not in t, name
        assert 'href="../latest/trust.html#validation"' in t and 'href="../latest/trust.html"' in t, name


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
    assert len(re.findall(r'<span class="cell ', page)) - 5 == 6     # 범례 칸 5개 + 첫 게시·지금 판 띠 각 3칸
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
            outcome="rebuilt", gate="pass", verify="pass", final=True, diff=None, commit="abcdef0123456789",
            source="backfill", origin="marker"),
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
    assert "관문 차단</b> — 2026-11-12" in slo and "검증 실패(이 30일)</b> — 2026-11-02" in slo
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


def test_no_report_folder_means_no_page(tmp_path):
    empty = tmp_path / "reports"
    empty.mkdir()
    out = tmp_path / "밖" / "trust.html"
    assert tp.main(["--root", str(empty), "--out", str(out)]) == 1
    assert not out.exists()


# ─────────────────────────────── 첫 게시 — 재구성이 지운 실패를 숨기지 않는다

def _rebuilt_root(tmp_path, ledger_rows=(), archive_failed=None):
    """전부 재구성한 루트 — 지금 판은 모두 통과다. 첫 게시 기록은 원장·보관 원본에만 있다."""
    r = tmp_path / "reports"
    for d in ("2026-10-26", "2026-10-27", "2026-10-28"):
        p = r / d
        p.mkdir(parents=True)
        (p / "numbers.html").write_text("<html>", "utf-8")
        (p / "payload.json").write_text("{}", "utf-8")
        (p / "VERIFY.txt").write_text(_pass(d), "utf-8")
        (p / "REBUILT.txt").write_text("2026-11-09 05:00 다시 만듦\ncommit: abc\nfinalize: unverified\n", "utf-8")
    for d, body in (archive_failed or {}).items():
        a = r / ".archive" / "pre-rebuild-2026-11-08" / d
        a.mkdir(parents=True)
        (a / "numbers.html").write_text("<html>", "utf-8")
        (a / "VERIFY_FAILED.txt").write_text(body, "utf-8")
    (r / "events.jsonl").write_text("".join(_ev(**x) + "\n" for x in ledger_rows), "utf-8")
    os.symlink(r / "2026-10-28", r / "latest")
    return r


def _pub(asof, verify, ts):
    return dict(ts=ts, event="report", asof=asof, mode="normal", rc=None, outcome="published", gate="pass", verify=verify,
                final=False, diff=None, source="backfill", origin="cron.log")


def _reb(asof, ts="2026-11-08T20:00:00Z"):
    return dict(ts=ts, event="report", asof=asof, mode="rebuild", rc=None, outcome="rebuilt", final=False, diff=None,
                source="backfill", origin="marker")


def test_first_publication_failure_is_on_the_page_even_when_every_folder_was_rebuilt(tmp_path):
    """자기모순 방지 — 지금 판이 전부 통과여도 첫 게시 실패가 칸·타일·사유에 나오고 '실패가 없다' 고 말하지 않는다."""
    fail = "\n검사 137건 · 실패 2건\n실패 목록: G/동시성 범위, H/커서 범위\n"
    r = _rebuilt_root(tmp_path, [_pub("2026-10-26", "pass", "2026-10-26T09:12:00Z"), _reb("2026-10-26"),
                                 _pub("2026-10-27", "fail", "2026-10-27T09:10:48Z"), _reb("2026-10-27"), _reb("2026-10-28")],
                      {"2026-10-27": fail})
    page = tp.render(str(r), now=NOW)
    tiles = _tiles(page)
    assert tiles["첫 게시 검증 실패"] == 1 and tiles["지금 판 검증 실패"] == 0 and tiles["첫 게시 기록 없음"] == 1
    assert "실패가 없다" not in page and "실패 기록이 없다" not in page
    assert "실패 2" in _row(page, "2026-10-27") and ">통과<" in _row(page, "2026-10-26")
    assert "기록 없음" in _row(page, "2026-10-28")                       # 재구성뿐 — 첫 게시를 지어내지 않는다
    assert "G/동시성 범위" in page and "첫 게시 1일" in page
    assert '<span class="cell fail" title="2026-10-27 · 첫 게시' in page
    assert "전 기간 첫 게시 실패</b> — 3일 중 1일" in page.split("운영 SLO")[1]


def test_a_rebuild_is_never_the_first_publication_even_if_ledger_order_says_so(tmp_path):
    """원장이 시각순이 아니어도(백필 뒤 덧붙임) 가장 이른 재구성 아닌 게시가 첫 게시다."""
    early, late = _pub("2026-10-27", "fail", "2026-10-27T09:00:00Z"), _pub("2026-10-27", "pass", "2026-10-28T09:00:00Z")
    for order in ([late, early], [early, late]):                         # 파일 순서가 어느 쪽이든 시각이 정한다
        rows = [_reb("2026-10-27", "2026-10-27T01:00:00Z"), *order]
        assert tp.first_publications([json.loads(_ev(**x)) for x in rows])["2026-10-27"]["verify"] == "fail"
    r = _rebuilt_root(tmp_path, [_reb("2026-10-27", "2026-10-27T01:00:00Z"), late, early])
    assert "실패" in _row(tp.render(str(r), now=NOW), "2026-10-27")


def test_first_result_falls_back_to_the_archived_original_when_the_ledger_is_silent(tmp_path):
    crash = LAYERS.format(d="2026-10-27") + "Traceback\nTypeError: boom\n"
    r = _rebuilt_root(tmp_path, [_reb("2026-10-27")], {"2026-10-27": crash})
    row = _row(tp.render(str(r), now=NOW), "2026-10-27")
    assert "검사기 중단" in row and "보관 원본" in row


def test_the_table_is_not_cut_to_a_recent_window(tmp_path):
    """창을 줄이면 앞쪽의 실패가 페이지에서 사라진다 — 기본은 전 기준일이다."""
    r = tmp_path / "reports"
    for i in range(70):
        p = r / f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
        p.mkdir(parents=True)
        (p / "numbers.html").write_text("<html>", "utf-8")
        (p / "VERIFY.txt").write_text(_pass(p.name), "utf-8")
    page = tp.render(str(r), now=NOW)
    days = page.split("기준일별 기록")[1]
    assert len(re.findall(r"<tr><td>\d{4}-\d{2}-\d{2}</td>", days)) == 70 and "전 기준일 70개" in page


def test_page_does_not_claim_the_records_are_as_they_were(page):
    """전부 재구성한 날의 검증을 '그때 기록 그대로' 라 적으면 거짓이다."""
    assert "그때 기록 그대로" not in _text(page) and "재구성 때 코드" in _text(page)


# ─────────────────────────────── 확정 수 — finalize: 줄 없는 옛 재구성

def test_old_rebuild_without_finalize_line_is_not_counted_as_final(tmp_path):
    """옛 재구성(3617962)은 FINAL.txt 가 있어도 finalize: 줄이 없다. 확정으로 세면 확인하지 않은 날이 25 에 든다."""
    r = _rebuilt_root(tmp_path)
    (r / "2026-10-26" / "REBUILT.txt").write_text("2026-11-08 17:52 현재 코드(3617962)·확정 데이터로 다시 만듦\n", "utf-8")
    (r / "2026-10-27" / "REBUILT.txt").write_text("다시 만듦\ncommit: abc\nfinalize: verified\n", "utf-8")
    for d in ("2026-10-26", "2026-10-27"):
        (r / d / "FINAL.txt").write_text("재구성\n", "utf-8")
    page = tp.render(str(r), now=NOW)
    tiles = _tiles(page)
    assert tiles["확정"] == 1 and tiles["확정 미확인"] == 1 and tiles["확정 기록 없음"] == 1
    assert ">확정 미확인<" in _row(page, "2026-10-26") and ">확정<" not in _row(page, "2026-10-26")
    assert ">확정<" in _row(page, "2026-10-27")
    srv_row = next(li for li in srv.index_html(str(r)).split("<li") if "2026-10-26" in li and 'class="d"' in li)
    assert ">확정 미확인<" in srv_row, "목록과 페이지가 다른 말을 한다"


# ─────────────────────────────── 정정의 크기 — 전후 값과 원인

MKTS = ("거래소", "코스닥")


def _payload(dates, by_date):
    """by_date = {날: {시장: {주체|tv: 값}}} → 섹터 둘로 쪼갠 ``flows`` — 합이 시장 합계가 되는지까지 본다."""
    flows = {m: {"가": {}, "나": {}} for m in MKTS}
    for m in MKTS:
        for k in ("inst", "forgn", "indiv", "etc", "tv"):
            col = [by_date.get(d, {}).get(m, {}).get(k) for d in dates]
            flows[m]["가"][k] = [None if v is None else v / 2 for v in col]
            flows[m]["나"][k] = [None if v is None else v / 2 for v in col]
    return {"dates": dates, "flows": flows}


def _totals(**kw):
    base = {"거래소": {"inst": 100.0, "forgn": -100.0, "indiv": 0.0, "etc": 0.0, "tv": 150000.0},
            "코스닥": {"inst": 10.0, "forgn": -10.0, "indiv": 0.0, "etc": 0.0, "tv": 50000.0}}
    for key, v in kw.items():
        m, k = key.split("_")
        base[{"K": "거래소", "Q": "코스닥"}[m]][k] = v
    return base


def test_market_totals_sum_the_sector_flows_of_that_day():
    P = _payload(["2026-10-01", "2026-10-02"], {"2026-10-02": _totals(K_etc=500.0)})
    got = tp.market_totals(P, "2026-10-02")
    assert got["거래소"]["etc"] == 500.0 and got["코스닥"]["tv"] == 50000.0
    assert tp.market_totals(P, "2026-09-30") is None


def test_cause_of_a_change_is_read_from_the_records():
    before = _totals(K_etc=15691.0, K_inst=-2465.0, K_tv=183375.0)
    after = _totals(K_etc=-544.0, K_inst=8920.0, K_tv=216476.0)
    # 다음날 원본 게시본이 이미 지금 값 — 코드가 아니라 DB 값이 바뀌었다(작은 차는 설명의 10% 안).
    near = _totals(K_etc=-544.0, K_inst=8920.0, K_tv=216476.0, Q_tv=50050.0)
    assert tp.errata_cause(before, after, near)[0] == "provisional"
    assert "잠정치 → 확정치" in tp.errata_cause(before, after, near)[1]
    assert tp.errata_cause(before, after, before)[0] == "later"          # 다음날 판은 옛 값 — 나중 코드·정정
    half = _totals(K_etc=7000.0, K_inst=3000.0, K_tv=200000.0)
    assert tp.errata_cause(before, after, half)[0] == "mixed"
    assert tp.errata_cause(before, after, None)[0] == "unknown"          # 모르면 모른다고 적는다
    assert tp.errata_cause(after, after, near)[0] == "same"
    # 10-27 — 코스닥 거래대금 1,099억 → 59,125억: 수집 미완 중 게시. 다음날 판과 견주기 전에 먼저 말한다.
    code, why = tp.errata_cause(_totals(Q_tv=1099.0), _totals(Q_tv=59125.0), _totals(Q_tv=59125.0))
    assert code == "incomplete" and "코스닥 거래대금이 나중 값의 1.9%" in why


def test_small_market_is_not_called_incomplete_by_its_ratio():
    """분모 게이트 — 나중 값이 작으면 비율로 말하지 않는다(작은 시장의 흔들림)."""
    code, _ = tp.errata_cause(_totals(Q_tv=100.0), _totals(Q_tv=500.0), None)
    assert code != "incomplete"


def test_size_keeps_the_largest_cell_and_sign_flips_only():
    before = _totals(K_etc=16702.0, K_forgn=-6367.0, K_inst=100.0, K_tv=162592.0, Q_forgn=-30.0)
    after = _totals(K_etc=9875.0, K_forgn=390.0, K_inst=120.0, K_tv=177237.0, Q_forgn=40.0)
    sz = tp.errata_size(before, after)
    assert [(c["market"], c["actor"], c["flip"]) for c in sz["net"]] == [("거래소", "기타법인", False), ("거래소", "외국인", True)]
    assert {t["market"]: (t["before"], t["after"]) for t in sz["tv"]}["거래소"] == (162592.0, 177237.0)


@pytest.fixture
def errata_root(tmp_path):
    """보관 원본·지금 판·다음날 원본이 다 있는 재구성 — 옛 형식 payload(``today`` 없음)도 ``flows`` 로 읽는다."""
    r = tmp_path / "reports"
    d1, d2 = "2026-09-03", "2026-09-04"
    was, now = _totals(K_etc=15691.0, K_tv=183375.0), _totals(K_etc=-544.0, K_tv=216476.0)
    arch = r / ".archive" / "pre-rebuild-2026-11-08"
    (arch / d1).mkdir(parents=True)
    (arch / d1 / "payload.json").write_text(json.dumps(_payload([d1], {d1: was})), "utf-8")
    (arch / d2).mkdir()
    (arch / d2 / "payload.json").write_text(json.dumps(_payload([d1, d2], {d1: now, d2: now})), "utf-8")
    for d in (d1, d2):
        p = r / d
        p.mkdir()
        (p / "numbers.html").write_text("<html>", "utf-8")
        (p / "VERIFY.txt").write_text(_pass(d), "utf-8")
        (p / "payload.json").write_text(json.dumps(_payload([d1, d2], {d1: now, d2: now})), "utf-8")
        (p / "REBUILT.txt").write_text("다시 만듦\ncommit: abc\nfinalize: verified\n", "utf-8")
    (r / "events.jsonl").write_text(_ev(ts="2026-11-07T16:37:02Z", event="archive", asof=d1, source="backfill") + "\n"
                                    + _ev(ts="2026-11-07T16:37:02Z", event="archive", asof="2026-09-30", source="backfill") + "\n",
                                    "utf-8")
    os.symlink(r / d2, r / "latest")
    return r


def test_errata_row_says_how_much_and_why_not_just_archived(errata_root):
    page = tp.render(str(errata_root), now=NOW)
    err = page.split("정정 —")[1].split("기준일별 기록")[0]
    row = re.search(r"<tr><td>2026-09-03</td>(.*?)</tr>", err, re.S).group(1)
    t = _text(row)
    assert "거래소 기타법인 +15,691 → −544" in t and "부호 반대" in t
    assert "거래소 183,375 → 216,476" in t and "코스닥 50,000 → 50,000" in t
    assert "잠정치 → 확정치" in t and "원본 보관" not in t
    other = _text(re.search(r"<tr><td>2026-09-30</td>(.*?)</tr>", err, re.S).group(1))
    assert "크기를 모른다" in other                                       # 원본이 없으면 없다고 적는다


def test_final_correction_row_carries_the_market_moves_from_final_txt(root, tmp_path):
    (root / "2026-11-05" / "FINAL.txt").write_text(
        "[..] 확정\n잠정 → 확정 비교\n시장 합계: 12칸 중 2칸 바뀜\n  거래소 기관 1일  +1.00 → +2.00 (+1.00)\n"
        "  거래소 기관 5일  +9.00 → +10.00 (+1.00)\n섹터 칸: 240칸 중 5칸 바뀜 — 큰 순 5\n", "utf-8")
    err = tp.render(str(root), now=NOW).split("정정 —")[1].split("기준일별 기록")[0]
    row = _text(re.search(r"<tr><td>2026-11-05</td>(.*?)</tr>", err, re.S).group(1))
    assert "거래소 기관 +1.00 → +2.00" in row and "+9.00" not in row and "섹터 칸 5개 변경" in row


# ─────────────────────────────── 낡은 주장

def test_stale_claims_about_the_screen_are_gone(page):
    t = _text(page)
    assert "포텐셜" not in t, "화면에서 지운 열이다"
    assert "탐색 점수" not in t, "G 의 화면 표지는 [판정 불가: G] 다"
    assert "[판정 불가: G]" in _qrow(page, "Q3d-G")
    assert "앞날 검정을 하지 않았다" in _qrow(page, "untested")
    assert "1년%ile" in _qrow(page, "untested") and "앞날 검정을 하지 않았다" in t.split("검정된 적 없는 값")[1]


def test_latest_dir_ignores_a_dangling_or_foreign_link(tmp_path):
    """``latest`` 가 사라진 기준일이나 날짜가 아닌 폴더를 가리키면 리포트가 있는 가장 늦은 폴더로 간다."""
    root = tmp_path / "r"
    for d, files in (("2026-11-05", ["numbers.html"]), ("2026-11-06", ["numbers.html"]), ("2026-11-07", [])):
        (root / d).mkdir(parents=True)
        for f in files:
            (root / d / f).write_text("x", "utf-8")
    (root / "scratch").mkdir()
    link = root / "latest"
    link.symlink_to(root / "2026-11-30")                        # 날짜 꼴인데 폴더가 없다
    assert tp.latest_dir(str(root)) == "2026-11-06"
    link.unlink()
    link.symlink_to(root / "scratch")                           # 폴더는 있는데 날짜가 아니다
    assert tp.latest_dir(str(root)) == "2026-11-06"
    link.unlink()
    link.symlink_to(root / "2026-11-05")                        # 멀쩡한 링크는 그대로 따른다
    assert tp.latest_dir(str(root)) == "2026-11-05"


def test_folder_errata_lists_a_final_only_when_a_sector_cell_moved():
    """확정이 섹터 칸을 **하나라도** 바꿔야 정정이다 — 0칸은 정정이 아니고 1칸은 정정이다."""
    def day(n):
        return {"date": "2026-11-05", "rebuilt": None, "final": {"kind": "diff", "sector": (n, 240), "market": None}}
    assert tp.folder_errata([day(0)]) == []
    assert [e["detail"] for e in tp.folder_errata([day(1)])] == ["섹터 칸 1개 변경"]


def test_day_without_a_verify_record_says_so():
    assert "기록 없음" in tp.verify_cell({"verify": None, "date": "2026-11-05", "verify_status": "unknown"})


def test_rebuilt_commit_is_shown_short_or_as_unknown(page):
    """재구성 커밋은 앞 12자리로 — 표와 정정 목록이 같은 글자이고, 기록이 없으면 '?' 다."""
    assert "나중 코드(abcdef012345)" in _row(page, "2026-11-06")
    def day(commit):
        return {"date": "2026-11-06", "final": None, "rebuilt": {"commit": commit, "finalize": "verified"}}
    assert tp.folder_errata([day("abcdef0123456789")])[0]["detail"] == "commit abcdef012345 · finalize verified"
    assert tp.folder_errata([day(None)])[0]["detail"] == "commit ? · finalize verified"


def test_git_info_reads_a_real_commit_and_shrugs_at_a_missing_one(tmp_path):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
           "GIT_AUTHOR_DATE": "2026-01-02T03:04:00+09:00", "GIT_COMMITTER_DATE": "2026-01-02T03:04:00+09:00"}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "--allow-empty", "-m", "x"], check=True, env=env)
    sha = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True,
                         env=env).stdout.strip()
    got = tp.git_info(sha, str(tmp_path))
    assert got is not None and got[0] == "2026-01-02 03:04" and isinstance(got[1], int)
    assert tp.git_info("deadbeef" * 5, str(tmp_path)) is None


def test_parse_verify_without_marks_counts_zero():
    """표시가 하나도 없는 출력(검증기가 시작도 못 함)은 0건 — 없는 실패를 지어내지 않는다."""
    got = tp.parse_verify("Traceback (most recent call last):\nImportError: x\n")
    assert got == {"checks": 0, "fails": 0, "claims": 0, "complete": False, "fail_names": []}


def test_only_days_with_an_archive_say_so(page):
    assert "원본 보관 1" in _row(page, "2026-11-05")
    assert "원본 보관" not in _row(page, "2026-11-03")


def test_final_cell_speaks_only_for_a_final_with_a_record():
    diff = {"kind": "diff", "sector": (5, 240), "market": None}
    def cell(state, f):
        return tp.final_cell({"state": state, "final": f, "date": "2026-11-05"})
    assert "섹터 5/240칸" in cell("확정", diff) and "섹터 5/240칸" in cell("확정 미확인", diff)
    assert ">—<" in cell("확정", None)                         # 확정인데 기록이 없다 — 죽지 않는다
    assert ">—<" in cell("잠정", diff)                         # 잠정 폴더에 남은 기록은 말하지 않는다
    assert ">—<" in cell("잠정", None)


def test_prereg_gap_is_result_minus_preregistration_in_minutes():
    """간격은 (결과 커밋 − 사전등록 커밋)/60 — 페이지가 '사전등록 N분 뒤 결과' 로 적는 값이다."""
    label, pre, res = next(lk for r in tp.VALIDATION for lk in r["links"] if lk[1] and lk[2][1])
    gaps = tp.prereg_gaps({pre[1]: ("x", 1_000_000), res[1]: ("y", 1_000_000 + 600)})
    assert 10 in gaps and all(g == 10 for g in gaps), (label, gaps)
