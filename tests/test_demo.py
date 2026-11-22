"""첫 실행 경로 — 깨끗한 복제본에서 ``sw-flow`` 를 처음 치는 사람이 막다른 길에 서지 않는가, 합성 데모가 가짜임을 말하는가.

예전 경로는 순환이었다: ``sw-flow`` → "``daily_report.sh`` 를 돌려라" → "DB 접속정보가 없다". 샘플 데이터도 없었다.
데모는 씨앗 고정 sqlite 를 배치와 같은 생성기에 태워 만든다. 그래서 형식이 바뀌어도 낡지 않고, 검증기를 그대로 통과해야 한다.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from sectorflow import demo
from sectorflow.basis import DEMO_MARK, DEMO_NOTICE
from sectorflow.storage import connect
from sectorflow.tui import flow_app, ledger_view
from sectorflow.tui import flow_view
from sectorflow.tui.flow_view import State, banner_lines, header_lines
from sectorflow.wording import page_hits

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo")
    db, out = root / "demo.db", root / demo.ASOF.isoformat()   # 게시 폴더 이름 — 검증기는 옆 폴더를 잠정 리포트로 읽는다
    demo.build_db(db)
    demo.write_report(db, out)
    return {"db": db, "dir": out}


def test_demo_report_passes_the_same_verifier_as_a_real_report(built):
    """데모가 검사를 못 넘으면 처음 보는 사람에게 틀린 화면을 보여 주는 것이다. DB 대조(F)까지 전 층."""
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    env.pop("KR_QUANT_DB", None)
    p = subprocess.run([sys.executable, str(REPO / "scripts/verify_report.py"), "--dir", str(built["dir"]),
                        "--db-check", "--db", str(built["db"])],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-2000:]
    assert "실패 0건" in p.stdout


def _number_cuts(monkeypatch) -> list[str]:
    """`pad` 가 숫자 안에서 자른 자리를 모은다 — `+4,552억` 이 `+4,5` 로 보이면 다른 값이다. 출력은 폭에 맞으니 자르기 전에 본다."""
    cuts: list[str] = []
    real = flow_view.pad

    def spy(text, width, right=False):
        kept = real(text, width).rstrip(" ")
        rest = text[len(kept):] if text.startswith(kept) else ""
        if rest and re.search(r"[\d+\-−,.]$", kept) and re.match(r"[\d,.%억조]", rest):
            cuts.append(f"폭 {width}: {text}")
        return real(text, width, right)
    monkeypatch.setattr(flow_view, "pad", spy)
    monkeypatch.setattr(ledger_view, "pad", spy)
    return cuts


def test_no_screen_cuts_a_number_mid_digit(built, monkeypatch):
    """전 화면 — 숫자를 자릿수 중간에서 자르지 않는다. 도움말은 80칸 이상을 약속한다(좁으면 글이 잘린다는 것을 화면이 안다)."""
    d, ld = flow_app.load(str(built["dir"])), ledger_view.load(str(built["dir"]))
    cuts = _number_cuts(monkeypatch)
    for w in (60, 70, 80, 100, 140):
        st = State(d)
        flow_view.banner_lines(st, w)
        if w >= 80:
            flow_view.help_lines(w, 0, 10_000, flow_view.help_entries(st))
        for mi in range(len(st.markets)):
            for ai in range(len(flow_view.ACTORS)):
                for win in ("1", "20", "120"):
                    st = State(d)
                    st.mi, st.ai = mi, ai
                    st.goto_window(win)
                    flow_view.header_lines(st, w)
                    flow_view.table_lines(st, w, 40)
                    for row in range(min(3, len(st.rows()))):
                        st.row = row
                        flow_view.detail_lines(st, w)
                    st.drill = True
                    flow_view.names_lines(st, w)
                    flow_view.detail_lines(st, w)
        st = State(d)
        st.toggle_explore()
        flow_view.table_lines(st, w, 40)
        flow_view.all_lines(st, w)
        if w >= 80:                 # 원장의 표는 71칸 밑에서 그리지 않고, 한계·동시성 글은 80칸을 약속한다
            ledger_view.render_text(ledger_view.Model(ld), w)
    assert not cuts, f"{len(cuts)}곳, 처음: {cuts[:5]}"


def test_detail_panel_drops_whole_items_with_a_count_at_80_cols(built):
    """80칸 하단 패널 — 잘린 금액 대신 온전한 항목과 `외 N`. 합성 데모의 건설 3종목이 80칸에 안 든다."""
    st = State(flow_app.load(str(built["dir"])))
    lines = "\n".join(ln for r in range(len(st.rows())) for ln in (setattr(st, "row", r), flow_view.detail_lines(st, 80))[1])
    assert " 외 1" in lines or " 외 2" in lines
    assert not re.search(r"[+\-−][\d,]*\s*$", lines, re.M), "줄 끝이 단위 없는 숫자다"


def test_demo_band_leaves_room_above_jump_targets():
    """데모 고정 띠가 점프한 제목을 가리면 안 된다 — 띠가 두세 줄이 되는 폰 폭까지(값은 Playwright 로 재 정했다)."""
    assert re.search(r"\[id\]\{scroll-margin-top:(\d+)px!important\}", demo.BANNER)
    assert int(re.search(r"scroll-margin-top:(\d+)", demo.BANNER).group(1)) >= 90
    css = (REPO / "scripts/templates/sector_numbers.html").read_text("utf-8")
    assert "#main-table{overflow:clip}" in css, "overflow:hidden 인 칸은 크롬이 scroll-margin 을 무시한다(390·1440폭 실측)"


def test_first_screen_stock_names_are_whole_at_the_readme_width(built):
    """README 첫 그림(160칸)의 `[ ]` 이름이 10칸에 잘려 `가상전자…` 로 다 같았다 — 폭이 되면 다 적는다."""
    text = "\n".join(banner_lines(State(flow_app.load(str(built["dir"]))), 159))
    day = [ln for ln in text.splitlines() if ln.strip().startswith("기관 상위") or ln.strip().startswith("기관 하위")]
    tags = re.findall(r"\[(가상[^\]]*)\]", "\n".join(day))
    assert len(tags) >= 6 and not [t for t in tags if "…" in t], tags
    assert not re.search(r"\[가상..…", text), "한 칸 모자란 줄도 머리글자만 남기면 안 된다(11칸 단계)"


def test_one_day_trust_column_matches_the_first_screen(built):
    """1일 표의 투신+연기금 = 첫 화면 값(``today.sectors``). 종목 합이면 데모에서 −140·−141 로 갈린다."""
    d = flow_app.load(str(built["dir"]))
    st = State(d)
    st.goto_window("1")
    secs = d["today"]["sectors"][st.market]
    assert any(flow_view.fmt_amt(v) != flow_view.fmt_amt(secs[s]["invpen"]["v"])
               for s, v in flow_view._sector_trusted(d, st.markets[1:], "1").items() if s in secs), "데모에 갈리는 칸이 없다"
    for s, v in st.trusted_window().items():
        if s in secs:
            assert flow_view.fmt_amt(v) == flow_view.fmt_amt(secs[s]["invpen"]["v"]), s


def test_demo_has_no_real_name_or_code(built):
    """실데이터·실종목명을 레포에도 산출물에도 넣지 않는다 — 이름은 '가상', 코드는 KRX 에 없는 'Z' 로 시작한다."""
    P = json.loads((built["dir"] / "payload.json").read_text("utf-8"))
    assert P["sectors"] and all(s.startswith("가상") for s in P["sectors"]), P["sectors"]
    assert P["names"]
    for code, row in P["names"].items():
        assert code.startswith("Z") and row["name"].startswith("가상"), (code, row["name"])


def test_demo_shows_what_a_real_report_shows(built):
    """비었으면 데모가 아니다 — 첫 화면의 층이 다 차고, 얇은 섹터 표시도 보인다."""
    d = flow_app.load(str(built["dir"]))
    today = d["today"]
    assert today["market"] and today.get("streaks") and today.get("krx_index")
    rows = d["blocks"]["20|전체"]["rows"]
    assert any(r.get("thin") for r in rows) and any(not r.get("thin") for r in rows)
    assert any(r.get("G") is not None for B in d["blocks"].values() for r in B["rows"])


def test_demo_shows_every_mark_a_customer_can_meet(built):
    """대량 체결(판정 못 한 종목 포함)·권리락·거래대금 집중·확정·잠정→확정 변경·들고 남·네이버 대조 — 화면 표지가 데모에서 다 뜬다."""
    d = flow_app.load(str(built["dir"]))
    T = d["today"]
    bs = T["block_suspects"]
    assert bs["status"] == "ok" and bs["markets"]["거래소"] and bs["markets"]["코스닥"] and bs["unjudged"] >= 1, bs
    assert any(n.get("cap_pending") for n in d["names"].values())
    assert any(T["concentration"]["markets"].values())
    assert d["final"] is True and d["final_diff"] and d["final_diff"]["n_changed"] > 0
    assert d["change"] and any(x["entered"] or x["exited"] for x in d["change"]["top"]["전체"].values())
    assert T["krx_truth"]["markets"]["거래소"]["hot"] and not T["krx_truth"]["markets"]["코스닥"]["hot"]
    assert any(any(rows.values()) for rows in T["day_change"]["flips"].values()), "부호 바뀜 줄이 비었다"
    text = "\n".join(banner_lines(State(d), 160))
    for mark in ("분봉 밖 거래 큼", "네이버 시장값과 차이 큼", "거래대금 집중", "잠정→확정 변경", "마감·확정", "들어옴", "분봉 밖 판정 못 한"):
        assert mark in text, mark


def test_demo_identities_hold_after_the_provisional_edit(built, tmp_path):
    """잠정 사본도 4주체 합 = 0·기관 세부 합 = 기관이다 — 실 DB 의 항등식을 데모가 깨면 검증기가 빨개진다."""
    path = tmp_path / "prov.db"
    demo.provisional_db(built["db"], path)
    con = connect(str(path))
    a, b = con.execute("SELECT MAX(ABS(individual + foreign_ + institution + etc_corp)), "
                       "MAX(ABS(institution - fnnc_invt - insrnc - invtrt - bank - penfnd_etc - samo_fund)) FROM supply_demand "
                       "WHERE date = ?", (demo.ASOF.isoformat(),)).fetchone()
    con.close()
    assert (a, b) == (0, 0)


def test_demo_links_stay_inside_the_folder_and_the_trust_page_gives_the_grounds(built):
    """면책 문구의 '검증·품질' 링크가 데모에서 404 였다 — 리포트 없이 보는 사람에겐 데모가 유일한 길이다. 판정 글자는 실 페이지와 같다."""
    pages = {n: (built["dir"] / n).read_text("utf-8") for n in ("numbers.html", "viewer.html", "trust.html")}
    for name, html in pages.items():
        for href in re.findall(r'<a [^>]*href="([^"#]+)', html):
            if not href.startswith("http"):
                assert (built["dir"] / href).exists(), f"{name}: {href}"
    assert 'href="trust.html#validation"' in pages["numbers.html"]
    trust = pages["trust.html"]
    assert DEMO_NOTICE in trust and "data-demo-banner" in trust and "[합성 데모]" in trust and 'id="validation"' in trust
    assert not page_hits(trust), page_hits(trust)
    spec = importlib.util.spec_from_file_location("_tp", REPO / "scripts" / "trust_page.py")
    tp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tp)
    verdicts = {r["id"]: r["verdict"] for r in tp.VALIDATION}
    assert (verdicts["Q3d-sector"], verdicts["Q3d-G"], verdicts["S1-S3"]) == ("증거 없음", "판정 불가", "증거 없음")
    said = {v for _q, v, _w in demo.TRUST_RESULTS}
    assert said <= set(verdicts.values()), said - set(verdicts.values())


@pytest.mark.parametrize("width", (40, 80, 160))
def test_demo_mark_survives_at_every_width_on_every_title(built, width):
    """표지는 제목 줄 **맨 앞** — 뒤에 두면 좁은 창에서 잘려 가짜 숫자가 진짜처럼 보인다."""
    st = State(flow_app.load(str(built["dir"])))
    assert banner_lines(st, width)[0].lstrip().startswith(DEMO_MARK)
    assert header_lines(st, width)[0].lstrip().startswith(DEMO_MARK)
    st.explore = True
    assert header_lines(st, width)[0].lstrip().startswith(DEMO_MARK)
    mo = ledger_view.Model(ledger_view.load(str(built["dir"])))
    assert ledger_view.header_lines(mo, width)[0].lstrip().startswith(DEMO_MARK)


def test_real_report_carries_no_demo_mark(built, tmp_path):
    """표지가 실 리포트에 번지면 그것도 거짓이다 — 표지는 ``demo`` 키가 있을 때만."""
    html = (built["dir"] / "numbers.html").read_text("utf-8").replace('const D = {"demo": true, ', "const D = {")
    (tmp_path / "numbers.html").write_text(html, "utf-8")
    d = flow_app.load(str(tmp_path))
    assert "demo" not in d
    assert DEMO_MARK not in header_lines(State(d), 160)[0]


def test_demo_survives_a_copy_because_the_mark_is_in_the_data(built, tmp_path):
    """폴더 이름을 바꾸고 표식 파일을 빼도 표지는 남는다 — 화면이 읽는 데이터 안에 있다."""
    for name in ("numbers.html", "payload.json"):
        (tmp_path / name).write_bytes((built["dir"] / name).read_bytes())
    assert flow_app.load(str(tmp_path))["demo"] is True
    assert ledger_view.load(str(tmp_path))["demo"] is True


@pytest.mark.parametrize("page", ("numbers.html", "viewer.html"))
def test_web_pages_carry_the_banner_and_title(built, page):
    html = (built["dir"] / page).read_text("utf-8")
    assert DEMO_NOTICE in html and "data-demo-banner" in html and "position:sticky" in html
    assert "<title>[합성 데모]" in html


def test_demo_has_map(built):
    """README Quick start 의 웹 줄이 수급 지도까지 연다 — 데모 폴더에 map.html 이 없으면 첫 그림이 문서에만 있다."""
    html = (built["dir"] / "map.html").read_text("utf-8")
    assert "<title>[합성 데모]" in html and '"demo":true' in html and DEMO_NOTICE in html


def test_fixed_date_demo_does_not_warn_about_age(built):
    """고정 기준일이라 '며칠 지난 리포트' 는 뜻이 없다 — 받은 폴더를 위한 경고가 데모를 고장처럼 보이게 한다."""
    assert flow_app.load(str(built["dir"]))["report_warnings"] == []
    assert ledger_view.load(str(built["dir"]))["report_warnings"] == []


def test_db_is_deterministic(tmp_path):
    """같은 씨앗이면 같은 숫자 — 문서의 데모 예시를 다시 만들 수 있어야 한다."""
    def digest(p):
        demo.build_db(p)
        con = connect(str(p))
        rows = con.execute("SELECT COUNT(*), SUM(institution), SUM(foreign_), SUM(close) FROM supply_demand").fetchone()
        con.close()
        return tuple(rows)
    assert digest(tmp_path / "a.db") == digest(tmp_path / "b.db")


def test_ensure_never_overwrites_a_folder_that_is_not_a_demo(tmp_path):
    """``--out`` 을 잘못 줘 실 리포트를 덮으면 안 된다."""
    (tmp_path / "numbers.html").write_text("실 리포트", "utf-8")
    with pytest.raises(SystemExit, match="덮어쓰지 않는다"):
        demo.ensure(tmp_path)
    assert (tmp_path / "numbers.html").read_text("utf-8") == "실 리포트"


def test_ensure_rebuilds_when_the_generator_changed(tmp_path, monkeypatch):
    """지문이 다르면 옛 형식이다 — 그대로 열면 새 열이 비어 데모가 고장처럼 보인다."""
    made = []
    monkeypatch.setattr(demo, "build_db", lambda p: Path(p).write_text("", "utf-8"))

    def fake_write(db, out):
        made.append(out)
        for n in ("numbers.html", "payload.json"):
            (out / n).write_text("{}", "utf-8")
        (out / demo.MARKER).write_text(f"fingerprint: {demo._fingerprint()}\n", "utf-8")
    monkeypatch.setattr(demo, "write_report", fake_write)
    target = tmp_path / "demo"
    assert demo.ensure(target) == str(target) and len(made) == 1
    assert demo.ensure(target) == str(target) and len(made) == 1, "지문이 같으면 다시 안 짓는다"
    monkeypatch.setattr(demo, "_fingerprint", lambda: "다른지문")
    demo.ensure(target)
    assert len(made) == 2, "지문이 바뀌면 다시 짓는다"
    assert not list(tmp_path.glob("*.building.*")), "짓던 폴더가 남았다"


def _clean_env(tmp_path):
    env = {**os.environ, "XDG_CACHE_HOME": str(tmp_path / "cache"), "PYTHONPATH": str(REPO / "src")}
    env.pop("KR_QUANT_DB", None)
    return env


def test_cli_demo_flag_runs_end_to_end(tmp_path):
    """깨끗한 사용자가 치는 그대로 — ``sw-ledger --demo --dump`` 가 표지와 함께 뜬다(캐시는 임시)."""
    code = "from sectorflow.tui.ledger_app import main; main()"
    p = subprocess.run([sys.executable, "-c", code, "--demo", "--dump", "--width", "100"],
                       env=_clean_env(tmp_path), capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr[-2000:]
    assert DEMO_MARK in p.stdout and "가상" in p.stdout
    assert "실데이터 아님" in p.stderr, "만드는 동안 무엇을 하는지 말한다"
    assert (tmp_path / "cache/sector-flow/demo/numbers.html").exists()


def test_flow_demo_flag_opens_the_demo_folder(built, monkeypatch):
    """``sw-flow --demo`` 는 데모 폴더를 연다 — curses 만 빼고 진입점 그대로."""
    seen = {}
    monkeypatch.setattr("sys.argv", ["sw-flow", "--demo"])
    monkeypatch.setattr("sectorflow.demo.ensure", lambda: str(built["dir"]))
    monkeypatch.setattr(flow_app, "run_curses", lambda _loop, data: seen.update(data))
    flow_app.main()
    assert seen.get("demo") is True


@pytest.mark.parametrize("app", ("sectorflow.tui.flow_app", "sectorflow.tui.ledger_app"))
def test_empty_clone_error_points_to_demo_and_received_folder(tmp_path, app):
    """막다른 길의 입구 — 리포트가 없을 때 데모·받은 폴더·DB 세 갈래를 다 말한다."""
    code = f"from {app} import main; main()"
    p = subprocess.run([sys.executable, "-c", code, "--dir", str(tmp_path / "없는폴더")],
                       env=_clean_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert p.returncode != 0
    for must in ("--demo", "합성", "실데이터 아님", "--dir", "§4.3", "scripts/daily_report.sh", "§4.1"):
        assert must in p.stderr, (must, p.stderr)


def test_batch_without_db_points_back_to_the_demo():
    """순환의 다른 쪽 — ``daily_report.sh`` 가 DB 없을 때 화면으로 가는 길을 적는다."""
    src = (REPO / "scripts/_env.sh").read_text("utf-8")
    line = next(ln for ln in src.splitlines() if "cp .env.example .env" in ln)
    assert "sw-flow --demo" in line and "§4.3" in line, line


def test_demo_module_never_reads_a_real_db_env():
    """생성기를 띄울 때 ``KR_QUANT_DB`` 를 지운다 — sector_flow 기본값이 실 DB 를 보면 데모가 실데이터가 된다."""
    src = (REPO / "src/sectorflow/demo.py").read_text("utf-8")
    assert re.search(r'if k != "KR_QUANT_DB"', src)


# mutation-exempt: wheel 설치본에 sector_map.py 가 없어도 데모 지문·생성이 멈추지 않음을 확인하는 호환성 검사다
def test_installed_copy_without_the_map_generator_still_fingerprints(monkeypatch):
    """설치본(wheel)에는 웹 전용 sector_map.py 가 없다 — 지도만 빠지고 데모 지문·생성은 멈추지 않는다."""
    real = demo.script_path

    def _no_map(name):
        if name == "sector_map.py":
            raise SystemExit("없음")
        return real(name)

    with_map = demo._fingerprint()
    monkeypatch.setattr(demo, "script_path", _no_map)
    assert demo._script_or_none("sector_map.py") is None
    assert demo._fingerprint() != with_map              # 지도 생성기가 생기거나 빠지면 데모를 다시 짓는다
