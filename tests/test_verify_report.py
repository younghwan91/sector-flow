"""``scripts/verify_report.py`` — 검사 **자체**가 맞는지.

일일 리포트 검증이 2026-09~10 의 27일 중 25일 빨갰다. 그중 H 층 두 줄은 화면의
결함이 아니라 검사의 가정이었다:

* ``G·End 는 끝`` — 커서를 3 에 두고 G 뒤에 ``> 3`` 인지를 봤다. 관문을 통과한
  섹터가 적은 날에는 전 종목(t)이 0~2행, 종합이 0행이라 커서가 **정확히 끝에
  가 있는데도** "끝으로 안 간다" 고 했다(09-07: 2행 목록에서 arow=1).
* ``커서가 범위 안에`` — 목록 크기와 무관하게 커서 3 에서 출발해, 0행인 전 종목
  화면에서 (설계대로) 안 듣는 ``s``·``r`` 뒤에 "arow=3 (행 0)" 이라고 했다.
  범위를 벗어난 것은 키가 아니라 검사가 만든 출발점이었다.

거짓 경보가 매일 뜨면 진짜 경보가 그 소음에 묻힌다. 그래서 두 방향을 다 본다 —
**짧은 목록에서 조용한가**, 그리고 **끝 키가 정말 끝에 못 가면 여전히 무는가**.
DB·네트워크 없이 합성 페이로드만 쓴다.
"""
from __future__ import annotations

import curses
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

END_CHECK = "g·Home 은 처음"
RANGE_CHECK = "커서가 범위 안에"


def _load():
    spec = importlib.util.spec_from_file_location(
        "verify_report", REPO / "scripts" / "verify_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _flow(n_names: int, gsec: float | None = 0.6) -> dict:
    """섹터 하나·종목 ``n_names`` 개 — 전 종목(t) 목록이 **짧은** 날.

    ``combined`` 는 비운다 — G 가 나오는 창이 2개 미만인 날(2026-10-07 이 그랬다)
    ``sector_numbers`` 가 종합 축을 아예 안 싣는다.
    """
    names = {}
    for i in range(n_names):
        inst = 1000.0 - 100.0 * i
        names[f"{i:06d}"] = {
            "name": f"종목{i}", "sector": "S", "market": "거래소", "cap": 100000.0,
            "win": {"5": {"inst": inst * (0.7 - 0.1 * i), "forgn": 0.0,
                          "indiv": 0.0, "etc": 0.0, "tv": 1000.0,
                          "invtrt": 100.0 + 50 * i, "penfnd_etc": 60.0 - 10 * i,
                          "ret": 0.0},
                    "20": {"inst": inst, "forgn": 0.0, "indiv": 0.0, "etc": 0.0,
                           "tv": 4000.0 + 500 * i, "invtrt": 600.0 - 100 * i,
                           "penfnd_etc": 300.0 - 40 * i, "ret": 0.0}}}
    block = {"from": "2026-08-01", "to": "2026-08-28", "k": 1.0, "b": 0.0, "t": 2.0,
             "rows": [{"sector": "S", "n_all": n_names, "thin": False,
                       "inst": 1000.0, "forgn": 0.0, "indiv": 0.0, "etc": 0.0,
                       "cap": 100000.0, "accel": 1.0, "ret": 0.0, "a_idx": 1.0,
                       "cap_idx": 100000.0, "G": gsec, "G_pass": gsec is not None,
                       "pct1y": {}, "spark": {}, "top": {}}]}
    return {"asof": "2026-08-28", "finalized": True,
            "dates": ["2026-08-01", "2026-08-28"], "names": names,
            "blocks": {f"{w}|{m}": block for w in (5, 20, 60, 120)
                       for m in ("전체", "거래소", "코스닥")},
            "combined": {}}


def _ledger() -> dict:
    """원장 쪽 합성 페이로드 — ``test_tui_ledger_view`` 의 픽스처와 같은 모양.

    섹터가 **4개**다 — 원장 표가 4행이라 예전 검사(`> 3`)는 원장 쪽에서도 같은
    거짓 경보를 냈다("ledger/원장 G 가 끝으로 안 간다 (row=3)"). 실제 리포트는
    27섹터라 안 드러났을 뿐이다. 섹터를 늘리면 H 층 폭 전수가 수십 배 느려진다.
    """
    from sectorflow.tui.ledger_view import ACTOR_KEYS

    n = 40
    dates = [f"2026-0{1 + i // 28}-{1 + i % 28:02d}" for i in range(n)]
    secs = ["전기/전자", "운송장비/부품", "부동산", "출판/매체복제"]
    mkts = ["거래소", "코스닥"]

    def cell(seed: int):
        out = {k: [round((-1) ** (i + j) * (seed + i * (j + 1)) * 1.5, 2)
                   for i in range(n)] for j, k in enumerate(ACTOR_KEYS)}
        out["tv"] = [1000.0] * n
        return out

    return {"dates": dates, "sectors": secs, "markets": mkts,
            "flows": {m: {s: cell(1 + i * 7 + len(m)) for i, s in enumerate(secs)}
                      for m in mkts},
            "cap": {m: {s: 10000.0 * (i + 1) for i, s in enumerate(secs)}
                    for m in mkts},
            "n_by_sector": {m: {s: 50 for s in secs} for m in mkts},
            "n_names": 100, "finalized": True}


def _h_fails(D: dict) -> list[str]:
    vr = _load()
    vr.FAILS.clear()
    vr.layer_h(D, _ledger())
    return list(vr.FAILS)


@pytest.mark.parametrize("n_names", [2, 3])
def test_end_key_check_accepts_the_true_last_row_of_a_short_list(n_names):
    """회귀 — 2~3행 목록에서 G 가 끝(1·2행)에 갔는데 검사가 `> 3` 을 요구했다.

    종합이 0행인 것도 같이 태운다(10-07 의 `flow/종합 … row=0`).
    주입: 검사를 예전 `v <= 3` 판정으로 되돌리면 실패한다.
    """
    from sectorflow.tui.flow_view import State

    st = State(_flow(n_names))
    st.allv = True
    assert 0 < len(st.all_picks()) <= 3, "픽스처가 짧은 목록을 못 만든다 — 검사가 헛돈다"
    fails = _h_fails(_flow(n_names))
    assert not [f for f in fails if END_CHECK in f], fails


@pytest.mark.parametrize("n_names", [2, 4])
def test_advertised_keys_count_as_live_on_a_list_too_short_to_move_from_row_3(n_names):
    """회귀 — 09-18 재구성: 전 종목이 4행이라 출발 커서 3 이 이미 끝이어서 ↓·PgDn·G 가 "안 듣는다" 고 했다.

    키가 듣는지는 목록 길이에 맞춘, 앱이 닿을 수 있는 자리(처음·min(3, 끝-1)·끝)에서 본다. 한 행뿐이면
    이동 키는 볼 수 없다 — 통과가 아니라 "못 본 화면" 으로 적혀야 한다.
    주입: 출발점을 다시 행 3 하나로 두면 실패한다.
    """
    from sectorflow.tui import flow_app as FA
    from sectorflow.tui.flow_view import State

    vr = _load()
    keys = vr._key_ints()

    def screen(**kw):
        def make():
            st = State(_flow(n_names))
            for k, v in kw.items():
                setattr(st, k, v)
            return st
        return make

    assert len(screen(allv=True, explore=True)().all_picks()) == n_names, "픽스처가 그 길이의 목록을 못 만든다"
    unseen: list = []
    live = vr._live(screen(allv=True, explore=True), FA.handle_key, ("arow", "allv", "wi", "mi"), keys,
                    unseen, "전 종목")
    for k in ("KEY_DOWN", "KEY_UP", "KEY_NPAGE", "KEY_PPAGE", "G", "g", "KEY_END", "KEY_HOME"):
        assert k in live, f"{n_names}행 전 종목에서 {k} 를 '안 듣는다' 고 했다"
    assert unseen == [], "두 행 이상인데 '못 봤다' 고 적었다"
    # 이 픽스처의 섹터 표는 **한 행**이다 — 이동 키는 볼 수 없으니 통과로 세지 않고 못 봤다고 적는다.
    vr._live(screen(), FA.handle_key, ("row", "wi", "mi"), keys, unseen, "섹터 표")
    assert unseen == ["섹터 표"]


def test_cursor_range_check_starts_from_a_state_the_app_can_reach():
    """회귀 — 2행 목록에 커서 3 을 박고 시작해 "arow=3 (행 2)" 라고 했다.

    주입: 출발 커서를 다시 목록과 무관한 3 으로 두면 실패한다.
    """
    fails = _h_fails(_flow(2))
    assert not [f for f in fails if RANGE_CHECK in f], fails


def test_end_key_check_says_which_screens_it_could_not_see(capsys):
    """관문 전멸 — 어느 구간·시장에서도 목록이 없으면 **못 봤다고 적는다.**

    빈 목록에서 "0 이면 맞다" 로 조용히 통과하면 검사는 아무것도 안 본 것이다.
    """
    vr = _load()
    vr.FAILS.clear()
    vr.layer_h(_flow(3, gsec=None), _ledger())
    assert not [f for f in vr.FAILS if END_CHECK in f or RANGE_CHECK in f], vr.FAILS
    line = [ln for ln in capsys.readouterr().out.splitlines() if END_CHECK in ln][0]
    assert "못 본 화면" in line and "전 종목(t)" in line and "종합" in line, line


def test_end_key_check_still_bites_when_end_stops_short(monkeypatch, capsys):
    """반대 방향 — 끝 키가 **한 칸 모자라게** 서면 검사가 여전히 빨개야 한다.

    `> 3` 을 지운 대가로 검사가 이빨을 잃으면 안 된다. 짧은 목록(3행)에서
    G 가 끝-1 에 서는 결함은 예전 검사로는 **원리적으로** 못 잡던 것이다.
    """
    from sectorflow.tui import flow_app as FA

    real = FA.handle_key

    def short(st, k, *a, **kw):
        out = real(st, k, *a, **kw)
        if st.allv and k in (ord("G"), curses.KEY_END):
            st.arow = max(st.arow - 1, 0)
        return out

    monkeypatch.setattr(FA, "handle_key", short)
    fails = _h_fails(_flow(3))
    assert [f for f in fails if END_CHECK in f], (
        "끝 키가 끝에 못 가는데 검사가 통과했다: " + repr(fails))
    assert "끝=2" in capsys.readouterr().out


# ── 빈 시장: 건너뛰지 말고 실패로 ─────────────────────────────────────────────
#
# 2026-10-27 리포트는 코스닥 시총이 통째로 0 이었고, 다른 날의 떠돌이 한 행은
# ``obs_neg_frac()`` 를 None 으로 만들어 ``None * 100`` 에서 검증을 죽였다.

def _market_payload(last_kosdaq_tv: float = 100.0, kosdaq_cap: float = 500.0) -> dict:
    n = 10
    dates = [f"2026-08-{i + 1:02d}" for i in range(n)]
    flow = lambda last: {"tv": [100.0] * (n - 1) + [last]}  # noqa: E731
    return {"dates": dates, "sectors": ["S"], "markets": ["거래소", "코스닥"],
            "flows": {"거래소": {"S": flow(100.0)}, "코스닥": {"S": flow(last_kosdaq_tv)}},
            "cap": {"거래소": {"S": 500.0}, "코스닥": {"S": kosdaq_cap}}}


def _fails(vr, layer_call) -> list[str]:
    vr.FAILS.clear()
    layer_call()
    return list(vr.FAILS)


def test_market_presence_passes_a_normal_day():
    vr = _load()
    assert _fails(vr, lambda: vr.check_market_presence(_market_payload())) == []


@pytest.mark.parametrize("kw", [{"kosdaq_cap": 0.0}, {"last_kosdaq_tv": 0.0},
                                {"last_kosdaq_tv": 1.9}])
def test_market_presence_fails_on_empty_market(kw):
    vr = _load()
    assert _fails(vr, lambda: vr.check_market_presence(_market_payload(**kw)))


def test_market_presence_tolerates_real_quiet_day():
    """260거래일 실측 최저(직전 5일 중앙값의 0.60배)는 빈 시장이 아니다."""
    vr = _load()
    assert _fails(vr, lambda: vr.check_market_presence(_market_payload(last_kosdaq_tv=60.0))) == []


class _FakeModel:
    """구간·시장에 따라 ``obs_neg_frac`` 가 None 인 원장 모델."""
    markets = ["거래소", "코스닥"]

    def __init__(self):
        self.mi = self.wi = self.ai = 0
        self.detrend = False

    @property
    def market(self):
        return self.markets[self.mi]

    def obs_neg_frac(self):
        return None if self.market == "코스닥" else 0.2

    def null_neg_frac(self):
        return 0.5


def test_comove_table_reports_empty_market_instead_of_crashing():
    vr = _load()
    obs, empty = vr.comove_table(_FakeModel(), windows=(20, 60), min_n=20,
                                 actors=(("inst", "기관"),))
    assert obs["기관"] == [(20.0, 50.0, "거래소", 20), (20.0, 50.0, "거래소", 60)]
    assert empty == ["코스닥/20/기관", "코스닥/60/기관"]


def test_a_crashing_layer_is_an_explicit_failure_and_later_layers_still_run(capsys):
    vr = _load()
    ran = []

    def boom(_):
        raise TypeError("unsupported operand type(s) for *: 'NoneType' and 'int'")

    vr.FAILS.clear()
    vr.run_layer("G", boom, 1)
    vr.run_layer("H", ran.append, 2)
    assert ran == [2]
    assert any("G" in f and "끝까지" in f for f in vr.FAILS)
    assert "NoneType" in capsys.readouterr().out


def test_empty_sector_index_coverage_warns_but_does_not_fail_the_report():
    """업종지수(``iret``)는 어떤 화면도 읽지 않는다 — 비었다고 리포트를 VERIFY_FAILED(⚠ 띠·다이제스트 안 만듦)로 만들면
    화면에 없는 값 때문에 화면 숫자가 의심받는다(P8). 경고로 남기고(freshness_check 가 알린다) 실패는 아니다."""
    VR = _load()
    spec = importlib.util.spec_from_file_location("payload_today", REPO / "tests" / "test_payload_today.py")
    PT = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(PT)
    P = PT._payload()
    P["iret"] = {m: {} for m in P["markets"]}
    VR.layer_b(P, None)
    assert not [f for f in VR.FAILS if "업종지수" in f], VR.FAILS
    assert "B/업종지수 커버리지(화면 미사용 iret)" in VR.WARNS


# ─────────────────────────────── M 층 — 수급 지도(map.html)의 숫자가 페이로드에서 다시 센 값과 같은가

def _map_payload() -> dict:
    """2시장 × 11섹터 × 6일 + 기관 세부 — 작은 섹터 셋이 꼬리로 묶이고, 하루 값에 None·0 이 섞인다. 손 페이로드(실데이터 아님)."""
    names = [f"가상{i}" for i in range(11)]
    dates = [f"2026-01-{5 + i:02d}" for i in range(6)]
    flows, detail = {}, {}
    for mi, m in enumerate(("거래소", "코스닥")):
        flows[m], detail[m] = {}, {}
        for si, s in enumerate(names):
            if m == "코스닥" and si == 10:
                continue                                    # 그 시장에 없는 섹터
            k = (12 - si) ** 2 * (1 + mi)                    # 뒤 섹터일수록 작다 — 꼬리
            ind = [(-1) ** (d + si) * k * (d + 1) for d in range(6)]
            ind[2] = None if si == 3 else ind[2]
            fo = [0.6 * k * (1 if (d + si) % 3 else -1) for d in range(6)]
            ins = [0.3 * k * (1 if (d * si) % 2 else -1) for d in range(6)]
            et = [0.0] * 6
            flows[m][s] = {"indiv": ind, "forgn": fo, "inst": ins, "etc": et}
            detail[m][s] = {"invtrt": [x * 0.4 for x in ins], "penfnd_etc": [x * 0.3 for x in ins]}
    return {"dates": dates, "sectors": names, "markets": ["거래소", "코스닥"], "flows": flows, "detail": detail,
            "detail_labels": {"invtrt": "투신", "penfnd_etc": "연기금"}}


def _map_html(P: dict | None = None, data=None) -> tuple[dict, str]:
    spec = importlib.util.spec_from_file_location("sector_map", REPO / "scripts" / "sector_map.py")
    SM = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(SM)
    data = data if data is not None else SM.build_map_data(P or _map_payload())
    return data, SM.render(data, Path(SM.TEMPLATE).read_text("utf-8"))


def _rerender(data: dict) -> str:
    return _map_html(data=data)[1]


def _bad(vr, P, html) -> dict:
    return {k: v for k, v in vr.map_mismatches(P, html).items() if v}


def test_m_layer_is_green_on_a_built_map_without_calling_flowmap(monkeypatch):
    """지은 지도는 통과한다 — 그리고 층 M 은 flowmap 을 부르지 않는다(같은 버그를 두 번 통과시키지 않게)."""
    import sectorflow.flowmap as FM

    vr = _load()
    P = _map_payload()
    data, html = _map_html(P)
    assert data["views"]["전체"]["5"]["exchange"]["tail"], "시험 페이로드에 꼬리가 있어야 꼬리 합을 본다"
    for name in ("window_sums", "exchange_graph", "actor_graph", "frames", "build_map_data", "map_headline", "inst_detail_sums"):
        monkeypatch.setattr(FM, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("flowmap 을 불렀다")))
    assert _bad(vr, P, html) == {}


def test_m_layer_catches_a_changed_cell_and_a_broken_conservation():
    """지도 데이터의 (주체, 섹터) 한 칸을 바꾸면 간선 대조와 섹터 보존식(Σ넘긴 = Σ넘겨받은)이 같이 빨개진다."""
    vr = _load()
    P = _map_payload()
    data, _html = _map_html(P)
    row = data["views"]["전체"]["5"]["exchange"]["sectors"][0]
    a = next(iter(row["give"]))
    row["give"][a] += 1.0
    bad = _bad(vr, P, _rerender(data))
    assert bad["exchange"] and bad["conserve"], bad


def test_m_layer_catches_a_wrong_tail_sum():
    vr = _load()
    P = _map_payload()
    data, _html = _map_html(P)
    tail = data["views"]["전체"]["5"]["exchange"]["tail"]
    a = next(iter(tail["take"]))
    tail["take"][a] += 5.0
    assert _bad(vr, P, _rerender(data))["tail"]


def test_m_layer_catches_actor_views_timelapse_and_headline():
    vr = _load()
    P = _map_payload()
    data, _html = _map_html(P)
    A = data["views"]["코스닥"]["1"]["actors"]["inst:invtrt"]
    (A["took"] or A["gave"])[0][1] += 3.0
    data["timelapse"]["전체"]["frames"][-1]["sectors"][0]["give"] = {"indiv": 123456.0}
    data["views"]["거래소"]["5"]["headline"]["lead"] = "최근 5거래일 거래소 — 다른 말"
    bad = _bad(vr, P, _rerender(data))
    assert bad["actors"] and bad["timelapse"] and bad["headline"], bad


def test_m_layer_rejects_any_sector_to_sector_structure():
    """섹터→섹터 구조는 관측되지 않는다 — 데이터에 잇는 칸이나 섹터 아래 섹터가 생기면 빨개진다."""
    vr = _load()
    P = _map_payload()
    data, _html = _map_html(P)
    data["views"]["전체"]["20" if "20" in data["views"]["전체"] else "5"]["links"] = [["가상0", "가상1", 10.0]]
    assert _bad(vr, P, _rerender(data))["structure"]
    data, _html = _map_html(P)
    data["views"]["전체"]["1"]["exchange"]["sectors"][0]["give"]["가상1"] = 1.0
    assert _bad(vr, P, _rerender(data))["structure"]


def test_m_layer_catches_avoided_words_in_the_page_text():
    vr = _load()
    P = _map_payload()
    _data, html = _map_html(P)
    assert _bad(vr, P, html.replace("<footer>", "<footer>섹터로 유입된 돈 "))["wording"]


def test_m_layer_runs_only_when_the_map_exists_unless_asked(tmp_path, capsys):
    """전 층에서는 지도가 있을 때만 돈다(X 와 같은 규칙). ``--layers M`` 은 지도가 없으면 실패다. 배치가 이 명령을 부른다."""
    import json as _json
    import subprocess
    import sys

    P = _map_payload()
    (tmp_path / "payload.json").write_text(_json.dumps(P, ensure_ascii=False), "utf-8")
    (tmp_path / "numbers.html").write_text('const D = {"blocks": {}};\n', "utf-8")
    run = lambda: subprocess.run([sys.executable, str(REPO / "scripts" / "verify_report.py"), "--dir", str(tmp_path),  # noqa: E731
                                  "--layers", "M"], capture_output=True, text=True)
    r = run()
    assert r.returncode == 1 and "수급 지도(map.html)가 있는가" in r.stdout, r.stdout
    data, html = _map_html(P)
    (tmp_path / "map.html").write_text(html, "utf-8")
    r = run()
    assert r.returncode == 0 and "[FAIL]" not in r.stdout and "M · 보존식" in r.stdout, r.stdout
    data["views"]["전체"]["1"]["exchange"]["gross"] += 9.0
    (tmp_path / "map.html").write_text(_rerender(data), "utf-8")
    r = run()
    assert r.returncode == 1 and "M/보존식" in r.stdout, r.stdout
    vr = _load()
    vr.layer_m(P, None, False)
    assert "건너뜀" in capsys.readouterr().out and not vr.FAILS


def test_m_layer_checks_the_time_flow_bands():
    """두 번째 보기(시간 흐름)의 띠 — 하루 값·'기타' 합을 일별 배열에서 다시 센다. 띠에 잇는 칸이 생기면 구조 위반."""
    vr = _load()
    P = _map_payload()
    data, _html = _map_html(P)
    data["stream"]["views"]["거래소"]["inst"][0]["v"][-1] += 0.5
    assert _bad(vr, P, _rerender(data))["stream"]
    data, _html = _map_html(P)
    rest = data["stream"]["views"]["전체"]["indiv"][-1]
    assert rest.get("rest"), "시험 페이로드에 '기타' 띠가 있어야 한다"
    rest["v"][0] += 1.0
    assert _bad(vr, P, _rerender(data))["stream"]
    data, _html = _map_html(P)
    data["stream"]["views"]["전체"]["forgn"][0]["to"] = "가상1"
    assert _bad(vr, P, _rerender(data))["structure"]
