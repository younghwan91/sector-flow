"""화면을 쓰는 사람이 겪는 일 — 2026-11-03 사용자 관점 리뷰에서 재현한 결함의 회귀 검사."""
from __future__ import annotations

import curses
import json
from datetime import date

import pytest

from sectorflow.tui import flow_app, ledger_app, ledger_view
from sectorflow.tui.flow_app import _draw, handle_key, leave_banner, load, terminal_problem
from sectorflow.tui.flow_view import (
    State, _actors_line, footer_line, header_lines, scroll_mark, staleness_warning, table_lines)


def _name(sector, flow, invtrt=10.0, pen=10.0, five=None, tv=5000.0, ret=1.0):
    win = {"inst": flow, "forgn": -flow, "indiv": 0.0, "etc": 0.0, "tv": tv,
           "invtrt": invtrt, "penfnd_etc": pen, "ret": ret}
    w5 = dict(win, inst=flow / 2 if five is None else five)
    return {"name": f"종목{abs(int(flow))}", "sector": sector, "market": "거래소", "cap": 100000.0,
            "win": {"20": win, "5": w5, "60": dict(win)}}


def _row(sec, accel):
    return {"sector": sec, "n_all": 20, "thin": False, "G": accel / 10, "G_pass": True,
            "inst": accel * 100, "forgn": -1.0, "indiv": -2.0, "etc": 3.0, "cap": 100000.0,
            "accel": accel, "ret": 0.5, "x": 1.0, "U": 1.0, "P": 0.1, "xdot": 0.1, "xddot": 0.1,
            "a_idx": accel, "cap_idx": 100000.0, "pct1y": {}, "spark": {}}


SECTORS = [f"섹터{i:02d}" for i in range(30)]


@pytest.fixture
def data():
    names = {
        "000001": _name(SECTORS[0], 900.0, ret=9.0),
        "000002": _name(SECTORS[0], 800.0, ret=1.0),
        "000003": _name(SECTORS[0], 700.0, ret=3.0),
        "000004": _name(SECTORS[0], -600.0, invtrt=-1.0),
        "000005": _name(SECTORS[0], 0.0),
        "000006": _name(SECTORS[0], 0.4),
        # 목록 안 몫이 작아 관문이 지울 행. 최근 5일이 몰려 **지우기 전 최근집중은 가장 크다**.
        "000008": _name(SECTORS[0], 10.0, five=50.0),
        "000007": _name(SECTORS[1], 500.0),
    }
    n = len(SECTORS)
    def block(sign, k=10.179):
        # 창마다 순서가 달라야 "보던 섹터에 머무나" 를 잴 수 있다.
        return {"from": "2026-10-02", "to": "2026-11-02", "k": k, "b": 0.1, "t": 2.5,
                "rows": [_row(s, sign * (i - n / 2)) for i, s in enumerate(SECTORS)]}
    blocks = {"20|전체": block(-1), "5|전체": block(1), "60|전체": block(-1),
              "120|전체": block(1), "20|거래소": block(1)}
    comb = {"windows": [20, 60, 120],
            "rows": [{"sector": s, "n_all": 20, "thin": False, "G": 0.5, "G_pass": True,
                      "per": {"20": 0.5}, "pass_n": 1, "seen": 1} for s in SECTORS]}
    return {"asof": "2026-11-02", "finalized": True, "dates": ["2025-10-10", "2026-11-02"],
            "blocks": blocks, "combined": {"전체": comb}, "names": names}


def _in_sector0(d) -> State:
    st = State(json.loads(json.dumps(d)))
    st.row = next(i for i, r in enumerate(st.rows()) if r["sector"] == SECTORS[0])
    st.drill = True
    return st


class Scr:
    def __init__(self, h, w):
        self.h, self.w, self.lines = h, w, {}

    def getmaxyx(self):
        return self.h, self.w

    def addstr(self, y, _x, s, _attr=0):
        self.lines[y] = s

    def erase(self):
        self.lines = {}

    def refresh(self):
        pass

    def chgat(self, *_a):
        pass

    def text(self):
        return "\n".join(self.lines[k] for k in sorted(self.lines))


# --- 드릴다운 ---------------------------------------------------------------

def test_pick_sort_puts_the_highest_score_first(data):
    """정렬 뒤에 점수를 매기면 전 행이 결측인 채 줄세워져 코드순으로 남았다."""
    st = _in_sector0(data)
    st.nsi = [k for k, _ in flow_app.NAME_SORTS].index("pick")
    picks = [t["pick"] for t in st.names()]
    got = [p for p in picks if p is not None]
    assert len(got) >= 2, picks
    assert got == sorted(got, reverse=True) and picks[:len(got)] == got, picks


def test_recent_concentration_sort_leaves_gated_rows_last(data):
    """관문이 지운 행(`—`)이 맨 위에 벽을 쌓으면 안 된다."""
    st = _in_sector0(data)
    st.nsi = [k for k, _ in flow_app.NAME_SORTS].index("conc")
    concs = [t["conc"] for t in st.names()]
    assert concs[0] is not None, concs
    n = sum(c is not None for c in concs)
    assert all(c is None for c in concs[n:]) and None in concs, concs


def test_reverse_of_net_buy_brings_the_biggest_seller_to_the_top(data):
    """도움말이 약속한 '순매도 상위'. 절댓값을 뒤집으면 위가 +0 벽이었다."""
    st = _in_sector0(data)
    assert st.names()[0]["flow"] == 900.0
    st.nrev = True
    rows = st.names()
    assert rows[0]["flow"] == -600.0
    assert all(t["cum"] is None for t in rows), "부호순 누적은 뜻이 없다"


def test_stock_panel_is_drawn_in_the_drilldown(data):
    """`layout` 이 자리를 잡아 두고 그리는 코드가 없어 빈 줄만 남았다."""
    st = _in_sector0(data)
    scr = Scr(24, 80)
    _draw(scr, st)
    assert "구간별 순매수[억]" in scr.text()
    assert "연기금" in scr.lines[max(k for k in scr.lines if "연기금" in scr.lines[k])]


# --- 선택·위치 --------------------------------------------------------------

@pytest.mark.parametrize("key", ["w", "a", "W"])
def test_window_and_actor_keys_stay_on_the_same_sector(data, key):
    st = State(json.loads(json.dumps(data)))
    st.row = 7
    sec = st.selected()["sector"]
    handle_key(st, ord(key))
    assert st.selected()["sector"] == sec


def test_market_key_stays_on_the_same_sector(data):
    st = State(json.loads(json.dumps(data)))
    st.row = 7
    sec = st.selected()["sector"]
    handle_key(st, ord("m"))
    assert st.market == "거래소" and st.selected()["sector"] == sec


def test_sort_key_still_goes_to_the_new_top(data):
    st = State(json.loads(json.dumps(data)))
    st.row = 7
    handle_key(st, ord("s"))
    assert st.row == 0


def test_scroll_position_is_on_the_footer_when_rows_are_hidden(data):
    st = State(json.loads(json.dumps(data)))
    scr = Scr(24, 80)
    _draw(scr, st)
    assert scr.lines[23].rstrip().endswith("/30"), scr.lines[23]
    assert scroll_mark(0, 30, 30) == ""


# --- 머리글·푸터·배너 --------------------------------------------------------

@pytest.mark.parametrize("width", [60, 80, 100, 120, 150])
def test_header_never_cuts_a_number_or_the_actor_note(data, width):
    """`k=10.179` 가 `k=1` 로 남으면 다른 숫자다."""
    st = State(json.loads(json.dumps(data)))
    st.ai = 1
    line = header_lines(st, width)[1]
    assert "k=" not in line or "k=10.179 (t=2.5)" in line, line
    assert "기관 기준" in line or "기관" not in line.split("주체")[-1].split("]", 1)[-1], line


def test_combined_footer_does_not_advertise_dead_keys():
    for width in (200, 120, 90):
        assert "s:정렬" not in footer_line(width, combined=True)
        assert "r:역순" not in footer_line(width, combined=True)
    assert "s:정렬" in footer_line(200)


def test_combined_pass_count_is_over_all_windows(data):
    """`seen` 은 G 가 나온 창이라 언제나 pass_n 과 같다 — 1/1·2/2 만 나왔다."""
    st = State(json.loads(json.dumps(data)))
    st.wi = flow_app.State(data).wi + 3
    assert st.window == "종합"
    lines, _thin, nhead = table_lines(st, 120, 40)
    assert "1/3" in lines[nhead], lines[nhead]


def test_sell_leaders_are_only_sellers(data):
    st = State(json.loads(json.dumps(data)))
    leads = st._leads()
    assert [t["flow"] for t in leads[SECTORS[0]]["sell"]] == [-600.0]
    assert leads[SECTORS[1]]["sell"] == []
    assert all(t["flow"] > 0 for t in leads[SECTORS[0]]["buy"])


def test_actor_line_keeps_the_selected_actor_when_narrow():
    r = {"inst": 1.0, "forgn": -2.0, "indiv": 3.0, "etc": 4.0}
    assert "외국인" in _actors_line(r, 32, actor="외국인")


@pytest.mark.parametrize("key,alive,helping", [
    (ord("q"), False, False), (-1, False, False), (ord("?"), True, True), (ord("x"), True, False)])
def test_banner_keys(data, key, alive, helping):
    st = State(json.loads(json.dumps(data)))
    assert leave_banner(st, key) is alive
    assert st.help is helping


# --- 환경 --------------------------------------------------------------------

@pytest.mark.parametrize("term,inp,out,enc,want", [
    ("xterm", True, True, "UTF-8", None),
    ("xterm", False, True, "UTF-8", "터미널이 아니다"),
    ("xterm", True, False, "UTF-8", "터미널이 아니다"),
    ("", True, True, "UTF-8", "TERM="),
    ("dumb", True, True, "UTF-8", "TERM=dumb"),
    ("xterm", True, True, "ANSI_X3.4-1968", "UTF-8 로케일"),
    ("xterm", True, True, "EUC-KR", "UTF-8 로케일"),
])
def test_terminal_problem(term, inp, out, enc, want):
    got = terminal_problem(term, inp, out, enc)
    assert got is None if want is None else want in got


def test_curses_failure_is_a_message_not_a_traceback(monkeypatch):
    def boom(*_a):
        raise curses.error("setupterm: could not find terminal")
    monkeypatch.setattr(curses, "wrapper", boom)
    monkeypatch.setattr(flow_app, "terminal_problem", lambda *_a: None)
    with pytest.raises(SystemExit) as e:
        flow_app.run_curses(lambda *_a: None)
    assert "TERM=xterm-256color" in str(e.value)


@pytest.mark.parametrize("today,n", [(date(2026, 11, 5), 3), (date(2026, 11, 6), 4)])
def test_staleness_warning_starts_after_a_normal_weekend(today, n):
    got = staleness_warning("2026-11-02", today)
    assert got == ([] if n < 4 else ["4일 지난 리포트"])


def test_load_says_so_when_the_report_is_not_utf8(tmp_path):
    (tmp_path / "numbers.html").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(SystemExit, match="UTF-8"):
        load(str(tmp_path))
    (tmp_path / "payload.json").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(SystemExit, match="UTF-8"):
        ledger_view.load(str(tmp_path))


def test_ledger_load_rejects_a_payload_that_is_not_an_object(tmp_path):
    (tmp_path / "payload.json").write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit, match="리포트 형식"):
        ledger_view.load(str(tmp_path))


# --- 원장 --------------------------------------------------------------------

@pytest.fixture
def ledger():
    n = 5
    secs = ["가", "나", "다", "라"]
    flows = {m: {s: {a: [float((i + 1) * (j + 1) * (1 if m == "거래소" else -1)) for j in range(n)]
                     for a in ("inst", "forgn", "indiv", "etc", "tv")}
                 for i, s in enumerate(secs)} for m in ("거래소", "코스닥")}
    d = {"dates": [f"2026-10-{i:02d}" for i in range(1, n + 1)], "sectors": secs,
         "markets": ["거래소", "코스닥"], "flows": flows,
         "cap": {m: {s: 1000.0 for s in secs} for m in ("거래소", "코스닥")},
         "n_by_sector": {s: 20 for s in secs}, "names": {}, "finalized": True}
    return ledger_view.Model(d)


def test_ledger_keys_stay_on_the_same_sector(ledger):
    """코스닥은 부호가 반대라 정렬이 뒤집힌다 — 자리만 지키면 다른 섹터가 된다."""
    ledger.row = 0
    sec = ledger.selected()["sector"]
    ledger_app._key(ledger, ord("m"), 10)
    ledger_app._key(ledger, ord("m"), 10)
    assert ledger.market == "코스닥"
    assert ledger.selected()["sector"] == sec


def test_ledger_header_says_whether_beta_removal_is_on(ledger):
    ledger.vi = [v for v, _ in ledger_view.VIEWS].index("comove")
    assert "β제거[끔]" in ledger_view.header_lines(ledger, 120)[1]
    ledger.detrend = True
    assert "β제거[켬]" in ledger_view.header_lines(ledger, 120)[1]


def test_ledger_survives_a_terminal_without_cursor_hiding(ledger, monkeypatch):
    """vt100 은 `curs_set` 이 ERR 이다 — 예전엔 트레이스백으로 죽었다."""
    def no_cursor(_n):
        raise curses.error("curs_set() returned ERR")
    monkeypatch.setattr(curses, "curs_set", no_cursor)
    monkeypatch.setattr(ledger_app, "_init_colors", lambda *_a: 0)
    monkeypatch.setattr(ledger_app, "_draw", lambda *_a: None)
    monkeypatch.setattr(ledger_app, "_read_key", lambda _s: -1)

    class S:
        def keypad(self, _on):
            pass
    ledger_app._loop(S(), ledger)
