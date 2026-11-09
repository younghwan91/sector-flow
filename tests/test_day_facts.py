"""첫 화면의 새 사실 — 하루 단위 변화·기관 세부·거래대금 집중과 그 표시·검증(2026-11-09 4회차 리뷰).

페이로드는 합성 표로 만들고, 값은 손으로 센 것과 맞춘다. 검증기(T 층)는 값을 망가뜨려 빨개지는지 본다.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from sectorflow.tui import flow_app as FA
from sectorflow.tui import flow_view as FV

REPO = Path(__file__).resolve().parent.parent


def _mod(name: str):
    spec = importlib.util.spec_from_file_location(f"_df_{name}", REPO / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sf = _mod("sector_flow")
vr = _mod("verify_report")
dg = _mod("digest")

N = 30
MK = ("거래소", "코스닥")
SECS = {"전기/전자": 20, "금융": 15, "얇은섹터": 3, "(미분류)": 12, "제약": 12}


def _synth() -> dict:
    """일별 배열을 직접 채운 페이로드 — 어느 칸이 무슨 값인지 테스트에서 보인다."""
    dates = [f"2026-10-{i + 1:02d}" for i in range(N)]
    zeros = lambda: [0.0] * N                                       # noqa: E731
    P: dict = {"dates": dates, "markets": list(MK), "sectors": sorted(SECS),
               "n_by_sector": {"거래소": dict(SECS), "코스닥": {"제약": 12}},
               "flows": {}, "detail": {}, "ret": {}, "retw": {}, "names": {}}
    for m in MK:
        P["flows"][m] = {s: {k: [1.0 + i * 0.01 for i in range(N)] for k in ("indiv", "forgn", "inst", "etc", "tv")} for s in SECS}
        P["detail"][m] = {s: {k: [0.5 + i * 0.01 for i in range(N)] for k, _ in sf.INST_DETAIL} for s in SECS}
        P["ret"][m] = {s: zeros() for s in SECS}
        P["retw"][m] = {s: zeros() for s in SECS}
    P["today"] = {"market": {"전체": {"tv": 0.0}, "거래소": {"tv": 0.0}, "코스닥": {"tv": 0.0}},
                  "sectors": {"전체": {}, "거래소": {}, "코스닥": {}}}
    return P


def _with_flip(P: dict) -> dict:
    """전기/전자·금융은 큰 부호 전환, 제약은 같은 크기여도 어제가 작다, 얇은섹터·(미분류)는 크지만 빠져야 한다."""
    x = copy.deepcopy(P)
    inst = {s: x["flows"]["거래소"][s]["inst"] for s in SECS}
    for s, (prev, now) in {"전기/전자": (-1625.0, 7057.0), "금융": (500.0, -3000.0), "제약": (-1.0, 800.0),
                           "얇은섹터": (-900.0, 900.0), "(미분류)": (900.0, -900.0)}.items():
        inst[s][-2], inst[s][-1] = prev, now
    return x


def test_flip_lists_only_big_sign_changes_of_real_sectors():
    B = sf.day_change_block(_with_flip(_synth()))
    assert B["prev_date"] == "2026-10-29"
    rows = B["flips"]["거래소"]["inst"]
    # |오늘| 큰 순. 제약은 어제 값이 목록의 2% 미만이라 잡음이고, 얇은 섹터·(미분류)는 섹터로 읽지 않는다.
    assert [(r["sector"], r["prev"], r["now"]) for r in rows] == [("전기/전자", -1625.0, 7057.0), ("금융", 500.0, -3000.0)]


def test_flip_gate_is_a_share_of_each_days_list():
    P = _with_flip(_synth())
    inst = P["flows"]["거래소"]["제약"]["inst"]
    inst[-2], inst[-1] = -90.0, 800.0                                # 어제 Σ|값| ≈ 4,000 의 2%(80) 위
    assert "제약" in [r["sector"] for r in sf.day_change_block(P)["flips"]["거래소"]["inst"]]
    inst[-2] = -60.0                                                 # 아래
    assert "제약" not in [r["sector"] for r in sf.day_change_block(P)["flips"]["거래소"]["inst"]]


def test_extreme_run_counts_days_at_that_extreme_with_each_days_own_distribution():
    # 오름차순이라 매일 그날까지의 최고 — 첫 비교 가능한 날부터 전부 ≥95.
    assert sf.extreme_run([float(i) for i in range(1, 11)], hi=True) == 9
    # 어제까지 오름차순, 오늘 꺾어 내린다 — 상단 연속이 끊긴다.
    assert sf.extreme_run([float(i) for i in range(1, 11)] + [5.0], hi=True) == 0
    # 오늘만 튄 값 — 오늘 처음.
    assert sf.extreme_run([3.0, 1.0, 2.0, 2.5, 1.5, 2.2, 9.0], hi=True) == 1
    # 내림차순은 하단 쪽으로 같다.
    assert sf.extreme_run([float(-i) for i in range(1, 11)], hi=False) == 9
    # 분포를 오늘 값으로 고정하면 미래를 본다 — 어제가 오늘로 밀려 내려가면 안 된다(어제는 어제까지의 최고였다).
    assert sf.extreme_run([1.0, 2.0, 3.0, 4.0, 5.0, 100.0], hi=True) == 5


def test_run_tag_is_new_on_the_first_day_and_counts_after():
    assert FV._run_tag(1) == "·신규"
    assert FV._run_tag(3) == "·3일째"
    assert FV._run_tag(None) == ""


def _today_payload(**over) -> dict:
    """화면 함수가 읽는 최소 ``today``."""
    net = {"inst": {"v": 19714.16, "pct": 91.5}, "forgn": {"v": -28336.4, "pct": 16.6}, "indiv": {"v": 4050.74, "pct": 56.0},
           "etc": {"v": 4552.71, "pct": 83.4}, "fnnc_invt": {"v": 20180.67, "pct": 92.0},
           "invtrt": {"v": -3186.31, "pct": 11.2}, "penfnd_etc": {"v": 2504.44, "pct": 98.1}, "invpen": {"v": -681.87, "pct": 47.9}}
    d = {"asof": "2026-11-06", "today": {"market": {"전체": {"net": net, "ret": {}}}, "sectors": {"전체": {}},
         "actors": {}}}
    d["today"].update(over)
    return d


def test_extremes_carry_how_many_days_at_that_extreme():
    d = _today_payload(day_change={"extreme": {"전체": {"inst": {"hi": {"통신": 1, "제약": 3}, "lo": {"금융": 2}}}}})
    d["today"]["sectors"]["전체"] = {"통신": {"inst": {"v": 9.0, "pct": 99.6}}, "제약": {"inst": {"v": 8.0, "pct": 96.0}},
                                     "금융": {"inst": {"v": -9.0, "pct": 2.0}}, "화학": {"inst": {"v": 1.0, "pct": 50.0}}}
    assert FV.today_extremes(d, "전체", "inst") == (["통신 100·신규", "제약 96·3일째"], ["금융 2·2일째"])


def test_market_detail_line_states_the_securities_share():
    items = FV.market_detail_items(_today_payload(), "전체")
    assert items == ["금투 +20,181(92)", "투신 -3,186(11)", "연기금 +2,504(98)", "금투÷기관 102%"]


def test_market_detail_share_has_a_denominator_gate():
    d = _today_payload()
    d["today"]["market"]["전체"]["net"]["inst"]["v"] = 100.0        # 4주체 Σ|금액| 의 10% 미만 — 비율이 튄다
    items = FV.market_detail_items(d, "전체")
    assert not any("금투÷기관" in i for i in items), items


def test_old_payload_without_securities_has_no_detail_line():
    d = _today_payload()
    del d["today"]["market"]["전체"]["net"]["fnnc_invt"]
    assert FV.market_detail_items(d, "전체") is None


def _streak_payload() -> dict:
    fact = lambda days, s, v, p: {"sign": s, "days": days, "sum": v, "pct": p}   # noqa: E731
    return _today_payload(streaks={
        "actors": {"inst": "기관", "invpen": "투신+연기금", "forgn": "외국인", "indiv": "개인", "etc": "기타법인"},
        "market_actors": ["inst", "invpen", "forgn", "indiv"],
        "market": {"전체": {"inst": fact(3, 1, 41000.0, 75.0), "invpen": fact(2, -1, -3771.0, 61.0),
                            "forgn": fact(6, -1, -121000.0, 86.0), "indiv": fact(4, 1, 54000.0, 92.0)}},
        "longest": {"전체": {"inst": "금융", "invpen": "제약", "forgn": "전기/전자"}},
        "sectors": {"전체": {"금융": {"inst": fact(11, 1, 18000.0, 98.0)}, "제약": {"invpen": fact(6, 1, 6484.0, 92.0)},
                             "전기/전자": {"forgn": fact(6, -1, -99000.0, 89.0)}}}})


def test_streak_puts_the_days_largest_actor_first():
    items = FV.streak_items(_streak_payload(), "전체")
    assert items[0].startswith("외국인 6일 연속 순매도"), items


@pytest.mark.parametrize("width", [79, 119, 159])
def test_streak_lines_keep_every_item_at_every_width(width):
    d = _streak_payload()
    joined = " ".join(FV._streak_lines(d, "전체", width))
    for who in ("외국인 6일", "기관 3일", "투신+연기금 2일", "개인 4일", "기관 최장 금융", "외국인 최장 전기/전자"):
        assert who in joined, (width, who, joined)
    assert " 외 " not in joined, joined                              # 예전엔 `외 5` 로 숨겼다
    lines = FV._streak_lines(d, "전체", width)
    assert all(FV.cell_len(ln) == width for ln in lines)
    assert "순매도 -12.1조(86)" in joined or "누적 -12.1조(86)" in joined      # 숫자가 자릿수 중간에서 안 끊긴다


def test_fold_items_breaks_at_item_boundaries():
    lines = FV._fold_items(" 머리: ", ["가" * 20, "나" * 20, "다" * 20], 50)
    assert [ln.strip() for ln in lines] == ["머리: " + "가" * 20, "나" * 20, "다" * 20]
    assert FV._fold_items(" 머리: ", [], 20)[0].strip() == "머리: 없음"
    # 머리말 옆에 첫 항목이 안 들어가면 머리말이 혼자 선다.
    alone = FV._fold_items("머" * 20 + ": ", ["가" * 20], 42)
    assert alone[0].strip().endswith(":") and "가" * 20 in alone[1], alone


def test_concentration_shows_top_stocks_and_values_without_them():
    P = _synth()
    P["today"]["market"]["전체"]["tv"] = 1000.0
    P["today"]["market"]["거래소"]["tv"] = 1000.0
    P["today"]["market"]["코스닥"]["tv"] = 0.0
    P["today"]["sectors"]["거래소"] = {
        "전기/전자": {"tv": 630.0, "inst": {"v": 7057.0}, "forgn": {"v": -21248.0}, "invpen": {"v": -3931.0},
                     "indiv": {"v": 1.0}, "etc": {"v": 2.0}},
        "금융": {"tv": 100.0, "inst": {"v": 5.0}},
        "얇은섹터": {"tv": 250.0, "inst": {"v": 1.0}},
        "(미분류)": {"tv": 200.0, "inst": {"v": 1.0}}}
    w = lambda tv, inst, forgn, it=0.0, pf=0.0: {"1": {"tv": tv, "inst": inst, "forgn": forgn, "invtrt": it, "penfnd_etc": pf, "indiv": 0.0, "etc": 0.0}}  # noqa: E731
    P["names"] = {
        "000660": {"name": "SK하이닉스", "sector": "전기/전자", "market": "거래소", "win": w(300.0, 8827.0, -19429.0, -1975.0)},
        "005930": {"name": "삼성전자", "sector": "전기/전자", "market": "거래소", "win": w(179.0, -471.0, -1260.0, -295.0)},
        "000001": {"name": "작은종목", "sector": "전기/전자", "market": "거래소", "win": w(151.0, 1.0, 1.0)}}
    rows = sf.concentration_block(P)["markets"]["거래소"]
    # 거래대금 몫이 20% 미만(금융)·얇은 섹터·(미분류)는 줄에 없다.
    assert [r["sector"] for r in rows] == ["전기/전자"]
    r = rows[0]
    assert r["market_share_pct"] == 63.0
    assert [t["name"] for t in r["top"]] == ["SK하이닉스", "삼성전자"]
    assert r["top_share_pct"] == round(479.0 / 630.0 * 100, 1)
    assert r["rest"]["inst"] == round(7057.0 - 8827.0 + 471.0, 1)
    assert r["rest"]["invpen"] == round(-3931.0 + 1975.0 + 295.0, 1)   # 투신+연기금은 종목의 둘을 더해 뺀다


def test_concentration_items_are_read_as_facts():
    d = _today_payload(concentration={"min_share": 0.2, "top_n": 2, "markets": {"전체": [
        {"sector": "전기/전자", "market_share_pct": 63.2, "top": [{"name": "SK하이닉스"}, {"name": "삼성전자"}],
         "top_share_pct": 76.4, "rest": {"inst": -1299.0, "invpen": -1661.0, "forgn": -559.0}}]}})
    assert FV.concentration_items(d, "전체") == [
        "전기/전자 거래대금 시장의 63%", "상위2 SK하이닉스·삼성전자 76%", "2종목 제외 기관 -1,299",
        "2종목 제외 투신+연기금 -1,661", "2종목 제외 외국인 -559"]


# ───────────────────────────── 검증기 — 값을 망가뜨리면 빨개지는가

@pytest.fixture(scope="module")
def full_payload() -> dict:
    td = _mod("sector_flow")                                        # 같은 합성 표를 쓴다
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(3)
    dates = [d.date().isoformat() for d in pd.bdate_range("2025-06-02", periods=80)]
    rows, caps = [], []
    i = 0
    for mk, secs in {"거래소": ("전기/전자", "금융", "제약", "화학"), "코스닥": ("제약", "화학")}.items():
        for sec in secs:
            for j in range(12):
                code = f"{i:06d}"
                i += 1
                for d in dates:
                    r = {"code": code, "date": d, "name": f"N{code}", "sector": sec, "market": mk}
                    for k in ("indiv", "forgn", "inst", "etc"):
                        r[k] = float(rng.normal(0, 30 if sec == "전기/전자" else 5))
                    r["tv"] = float(rng.uniform(1, 400 if sec == "전기/전자" else 40))
                    for k, _ in td.INST_DETAIL:
                        r[k] = float(rng.normal(0, 3))
                    r["ret"] = float(rng.normal(0.05, 2))
                    r["cap"] = 1000.0
                    rows.append(r)
                caps.append({"code": code, "sector": sec, "market": mk, "cap": 1000.0})
    df = pd.DataFrame(rows).sort_values(["code", "date"])
    df["cap_lag"] = df.groupby("code")["cap"].shift(1)
    return json.loads(json.dumps(td.build_payload(df, dates, pd.DataFrame(caps))))


def _t_fails(P: dict, fn) -> list[str]:
    vr.FAILS.clear()
    fn(P)
    return list(vr.FAILS)


def test_verifier_accepts_the_blocks_it_recounts(full_payload):
    assert _t_fails(full_payload, vr._vt_day_change_check) == []
    assert _t_fails(full_payload, vr._vt_concentration_check) == []
    assert full_payload["today"]["concentration"]["markets"]["거래소"], "시험 자료에 집중 섹터가 없으면 검사가 빈 것을 본다"
    assert "fnnc_invt" in full_payload["today"]["market"]["전체"]["net"]


def _first_ext(P):
    for mk in P["today"]["day_change"]["extreme"].values():
        for e in mk.values():
            for side in ("hi", "lo"):
                if e[side]:
                    return e[side]
    raise AssertionError("극단 칸이 하나도 없다 — 시험 자료를 고쳐라")


def _bump_run(P):
    x = _first_ext(P)
    k = next(iter(x))
    x[k] += 1


def _extra_flip(P):
    P["today"]["day_change"]["flips"]["전체"]["inst"].append({"sector": "제약", "prev": 1.0, "now": -1.0})


def _wrong_prev_date(P):
    P["today"]["day_change"]["prev_date"] = P["dates"][-3]


@pytest.mark.parametrize("corrupt", [_bump_run, _extra_flip, _wrong_prev_date])
def test_verifier_catches_a_wrong_day_change(full_payload, corrupt):
    P = copy.deepcopy(full_payload)
    corrupt(P)
    assert _t_fails(P, vr._vt_day_change_check), corrupt.__name__


def _conc(P):
    return P["today"]["concentration"]["markets"]["거래소"][0]


def _wrong_share(P):
    _conc(P)["top_share_pct"] += 5.0


def _wrong_rest(P):
    _conc(P)["rest"]["inst"] += 5.0


def _wrong_top(P):
    _conc(P)["top"].reverse()


def _dropped_sector(P):
    P["today"]["concentration"]["markets"]["거래소"].pop(0)


@pytest.mark.parametrize("corrupt", [_wrong_share, _wrong_rest, _wrong_top, _dropped_sector])
def test_verifier_catches_a_wrong_concentration(full_payload, corrupt):
    P = copy.deepcopy(full_payload)
    corrupt(P)
    assert _t_fails(P, vr._vt_concentration_check), corrupt.__name__


# ───────────────────────────── 다이제스트 — 라벨·반올림·표지

def _digest_payload(**over) -> dict:
    d = _today_payload()
    d["today"].update({"date": "2026-11-06", "sectors": {"전체": {}}, "krx_index": {}, "lookback": 260})
    d.update({"dates": ["2026-11-05", "2026-11-06"], "markets": ["거래소", "코스닥"], "final": True, "finalized": True})
    d["today"]["market"]["코스닥"] = {"net": {}, "ret": {"1": -3.812, "20": -26.415}}
    d.update(over)
    return d


def test_provisional_digest_labels_returns_at_collection_time_like_the_screen():
    d = _digest_payload(final=False)
    F = dg.collect(d)
    assert F["ret_label_short"] == FV.ret_label(d, short=True) == "~수집시각 잠정"
    assert "~수집시각 잠정" in dg.body_text(F)
    assert "~20시" not in dg.body_text(F)
    assert "~수집시각 잠정" in dg.card_html(F)
    assert "~20시" not in dg.card_visible_text(dg.card_html(F))


def test_closed_digest_labels_returns_at_twenty_oclock():
    for over in ({"final": True}, {"final": False, "finalize_unverified": True}):
        F = dg.collect(_digest_payload(**over))
        assert "~20시 통합종가" in dg.body_text(F)
        assert "잠정치다" not in dg.body_text(F), over                # 창 밖 재구성도 마감 값이다


def test_digest_states_rebuilt_and_data_basis():
    F = dg.collect(_digest_payload(rebuilt=True))
    assert "재구성" in F["status"]
    body, card = dg.body_text(F), dg.card_visible_text(dg.card_html(F))
    for text in (body, card):
        assert "재구성" in text
        assert "NXT 제외" in text


def test_digest_status_is_the_screens_status():
    for over in ({"final": True}, {"final": False, "finalized": True}, {"final": False, "finalized": False},
                 {"final": False, "finalize_unverified": True}, {"final": True, "rebuilt": True}):
        d = _digest_payload(**over)
        assert dg.status_label(d) == FV.status_chip(d), over


@pytest.mark.parametrize("v", [-26.415, 26.415, -4.365, 1.005, 0.125, -0.005, 0.0, 12.345, -2.675])
def test_digest_rounds_returns_like_the_screen(v):
    assert dg.ret_str(v) == FV._fmt_ret(v)


@pytest.mark.parametrize("v", [0.5, 1.5, 2.5, -2.5, 999.5, 1234.5, -19714.5, 9999.5, 12345.0, -123456.0, 0.0])
def test_digest_rounds_amounts_like_the_screen(v):
    assert dg.amt(v) == FV.fmt_amt(v)
    assert dg.fmt_big(v) == FV.fmt_big(v)


@pytest.mark.parametrize("p", [94.5, 95.0, 96.5, 0.5, 4.5, 99.5, None])
def test_digest_percentile_is_the_screens(p):
    assert dg.pct_tag(p) == FV._pct_tag(p)


def test_disclaimer_does_not_bind_percentile_to_tested():
    for text in (dg.DISCLAIMER, *FV.BANNER_DISCLAIMER[:2]):
        assert "%ile" in text and "앞날 검정 안 함" in text, text
        assert "섹터 수급의 앞날 관계는 검정함" not in text


def test_digest_lists_detail_flip_and_concentration():
    d = _digest_payload()
    d["today"]["day_change"] = {"prev_date": "2026-11-05", "flip_min_share": 0.02,
                                "flips": {"전체": {"inst": [{"sector": "전기/전자", "prev": -1625.0, "now": 7057.0}]}},
                                "extreme": {"전체": {"inst": {"hi": {"통신": 1, "제약": 3}, "lo": {}}}}}
    d["today"]["sectors"]["전체"] = {"통신": {"inst": {"v": 10.0, "pct": 99.6}}, "제약": {"inst": {"v": 9.0, "pct": 96.0}}}
    d["today"]["concentration"] = {"markets": {"전체": [
        {"sector": "전기/전자", "market_share_pct": 63.2, "top": [{"name": "SK하이닉스"}, {"name": "삼성전자"}],
         "top_share_pct": 76.4, "rest": {"inst": -1299.0, "invpen": -1661.0, "forgn": -559.0}}]}}
    body = dg.body_text(dg.collect(d))
    assert "금투 +20,181(92)" in body and "금투÷기관 102%" in body
    assert "전기/전자 -1,625→+7,057" in body
    assert "통신 100·신규" in body and "제약 96·3일째" in body
    assert "상위2 SK하이닉스·삼성전자 76%" in body and "2종목 제외 기관 -1,299" in body
    dg.check_compliance(body)


# ───────────────────────────── TUI 조작 — 첫 화면에서 표로

def _state() -> FV.State:
    return FV.State(json.loads(json.dumps(_tui_data())))


def _tui_data() -> dict:
    rows = [{"sector": f"섹터{i}", "n_all": 20, "cap": 100.0, "inst": float(10 - i), "forgn": 1.0, "indiv": 1.0, "etc": 1.0,
             "accel": float(10 - i), "ret": 1.0, "tv": 1.0, "pct1y": {"inst": 50.0}} for i in range(8)]
    blocks = {f"{w}|{m}": {"rows": copy.deepcopy(rows), "k": 1.0, "b": 0.0, "t": 1.0, "from": "2026-11-01", "to": "2026-11-06"}
              for w in ("5", "20", "60", "120") for m in ("전체", "거래소", "코스닥")}
    d = _today_payload()
    d["today"]["sectors"] = {m: {f"섹터{i}": {"inst": {"v": 1.0, "pct": 50.0}, "tv": 1.0, "ret": 0.1} for i in range(8)}
                             for m in ("전체", "거래소", "코스닥")}
    d.update({"blocks": blocks, "markets": ["전체", "거래소", "코스닥"], "combined": {}, "names": {}, "n_names": 0,
              "dates": ["2026-11-05", "2026-11-06"]})
    return d


def test_table_keys_on_the_first_screen_do_their_work():
    st = _state()
    st.banner = True
    assert FA.banner_key(st, ord("1"), 200, 200) is True
    assert st.banner is False and st.window == FV.ONE_DAY, "`1` 을 눌렀는데 20일 표가 떴다"
    st = _state()
    st.banner = True
    assert FA.banner_key(st, ord("m"), 200, 200) is True
    assert st.banner is False and st.market == "거래소"


def test_enter_on_the_first_screen_only_closes_it():
    st = _state()
    st.banner = True
    assert FA.banner_key(st, 10, 200, 200) is True
    assert st.banner is False and not st.drill, "표로 넘어가려던 Enter 가 드릴다운이 됐다"


def test_q_on_the_first_screen_quits():
    st = _state()
    st.banner = True
    assert FA.banner_key(st, ord("q"), 200, 200) is False


def test_space_at_the_end_of_the_first_screen_goes_to_the_table():
    st = _state()
    st.banner = True
    width, height = 79, 8
    total = len(FV.banner_lines(st, width))
    assert total > height
    for _ in range(total):
        if not st.banner:
            break
        assert FA.banner_key(st, ord(" "), width, height) is True
    assert st.banner is False, "제목은 '아무 키나 눌러 표로' 인데 Space 가 끝에서 멈췄다"


def test_arrow_at_the_end_of_the_first_screen_stays():
    st = _state()
    st.banner = True
    width, height = 79, 8
    for _ in range(len(FV.banner_lines(st, width)) + 2):
        FA.banner_key(st, FA.curses.KEY_DOWN, width, height)
    assert st.banner is True, "↓ 는 훑는 키다 — 끝에서 표로 나가면 안 된다"


def test_elapsed_note_says_how_old_the_day_is():
    from datetime import date
    assert FV.elapsed_note("2026-11-06", date(2026, 11, 9)) == "기준일 11-06(금)·3일 전"
    assert FV.elapsed_note("2026-11-06", date(2026, 11, 6)) == ""
    assert FV.elapsed_note("이상한값", date(2026, 11, 6)) == ""


def test_first_screen_states_elapsed_days(monkeypatch):
    monkeypatch.setattr(FV, "elapsed_note", lambda asof, today=None: "기준일 11-06(금)·3일 전")
    assert "3일 전" in FV.banner_lines(_state(), 100)[1]


# ───────────────────────────── 표지

def test_sector_table_head_carries_the_evidence_mark_only_where_tested():
    st = _state()
    head = FV.table_lines(st, 159, 20)[0][0]
    assert "가속[%p][무]" in head and "임펄스[억][무]" in head, head            # 20일·전체·기관
    st.mi = st.markets.index("거래소")
    head = FV.table_lines(st, 159, 20)[0][0]
    assert "[무]" not in head, head                                            # 거래소는 검정하지 않았다
    st.mi = 0
    st.wi = FV.WINDOWS.index("5")
    assert "[무]" not in FV.table_lines(st, 159, 20)[0][0]                      # 5일도


def test_marked_headers_fit_their_columns_at_80():
    st = _state()
    cols = FV._fit(FV.table_cols(st, 79), 79)
    for c in cols:
        assert FV.cell_len(FV.table_head(c.header, st)) <= c.width, c.header
    assert {"가속[%p]", "임펄스[억]", "수익률[%]"} <= {c.header for c in cols}


def test_explore_columns_carry_their_marks():
    st = _state()
    st.explore = True
    for hdr, mark in (("미실현[%p]", "[반전]"), ("G", "[불가]")):
        c = next(x for x in FV.table_cols(st, 400) if x.header == hdr)
        text = FV.table_head(hdr, st)
        assert text.endswith(mark) and FV.cell_len(text) <= c.width, (hdr, text)
    c = next(x for x in FV.names_cols(True) if x.header == "선정")
    assert FV.name_head("선정", st) == "선정[음]" and FV.cell_len("선정[음]") <= c.width


def test_names_screen_shows_its_fixed_note():
    st = _state()
    st.d["names"] = {"000001": {"name": "가", "sector": st.rows()[0]["sector"], "market": "거래소", "cap": 1.0,
                                "win": {"20": {"inst": 1.0, "forgn": 1.0, "indiv": 1.0, "etc": 1.0, "tv": 1.0}}}}
    lines, nhead = FV.names_lines(st, 79)
    assert nhead == 3 and lines[1].strip() == FV._first_fitting(79, list(FV.NAMES_NOTE)).strip()
    assert "추천 아님" in lines[1]
    assert all("추천 아님" in t for t in FV.NAMES_NOTE) and "20일" in FV.NAMES_NOTE[0] and "금액" in FV.NAMES_NOTE[0]


def test_ledger_drops_sectors_absent_from_the_market():
    from sectorflow.tui import ledger_view as LV
    mk = ("거래소", "코스닥")
    d = {"dates": ["2026-11-05", "2026-11-06"], "markets": list(mk), "sectors": ["있음", "없음"],
         "n_by_sector": {"거래소": {"있음": 12, "없음": 12}, "코스닥": {"있음": 12}},
         "flows": {m: {s: {k: [1.0, 2.0] for k in ("indiv", "forgn", "inst", "etc", "tv")} for s in ("있음", "없음")} for m in mk},
         "cap": {"거래소": {"있음": 10.0, "없음": 10.0}, "코스닥": {"있음": 10.0, "없음": 0.0}},
         "cap_by_month": {}, "detail": {}, "names": {}, "asof": "2026-11-06"}
    mo = LV.Model(d)
    mo.mi = mo.markets.index("코스닥")
    assert [r["sector"] for r in mo.rows()] == ["있음"], "코스닥에 없는 섹터가 +0 행으로 떴다"
    mo.mi = mo.markets.index("거래소")
    assert {r["sector"] for r in mo.rows()} == {"있음", "없음"}
