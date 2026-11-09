"""오늘의 수급 다이제스트(``scripts/digest.py``) — 글·사실·카드가 페이로드를 그대로 옮기고, 권유처럼 읽히는
말이 섞이면 **만들지 않는지**.

합성 페이로드(``today``·``change``·``final_diff`` 모양만)로 돌린다. DB·네트워크 불요. PNG 는 Playwright 가
있을 때만 찍어 본다 — 없으면 카드 HTML 만 본다.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _mod(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dg = _mod("digest")

ACTORS = ("inst", "invtrt", "penfnd_etc", "invpen", "forgn", "indiv", "etc")


def _row(**kv) -> dict:
    """섹터 한 줄 — ``kv`` 는 주체 → (값, %ile). 안 준 주체는 0·50."""
    out = {a: {"v": 0.0, "pct": 50.0} for a in ACTORS}
    for a, (v, p) in kv.items():
        out[a] = {"v": v, "pct": p}
    out["tv"] = 1000.0
    out["ret"] = 0.5
    return out


SECTORS = {
    "반도체": _row(inst=(1200.4, 97.0), invpen=(300.0, 80.0), forgn=(-5000.0, 3.0)),
    "금융": _row(inst=(800.0, 90.0), invpen=(-120.0, 20.0), forgn=(400.0, 70.0)),
    "화학": _row(inst=(-650.0, 4.0), invpen=(500.0, 99.5), forgn=(-300.0, 30.0)),
    "보험": _row(inst=(15.0, 55.0), invpen=(10.0, None), forgn=(900.0, 96.0)),
    "통신": _row(inst=(-20.0, 40.0), invpen=(-400.0, 2.0), forgn=(-10.0, 45.0)),
    "유통": _row(inst=(300.0, 99.0), invpen=(0.0, None), forgn=(50.0, 60.0)),
    "건설": _row(inst=(-90.0, 5.0), invpen=(60.0, 65.0), forgn=(120.0, 62.0)),
}


def fixture_payload(*, final: bool = False, finalized: bool = True) -> dict:
    net = {"inst": {"v": 2500.0, "pct": 88.0}, "forgn": {"v": -4200.5, "pct": 6.0},
           "indiv": {"v": 1500.0, "pct": 70.0}, "etc": {"v": 200.0, "pct": 51.0},
           "invtrt": {"v": -100.0, "pct": 40.0}, "penfnd_etc": {"v": 450.0, "pct": 93.0},
           "invpen": {"v": 350.0, "pct": 77.0}}
    P = {
        "dates": ["2026-11-05", "2026-11-06"], "markets": ["거래소", "코스닥"], "finalized": finalized,
        "today": {
            "date": "2026-11-06", "lookback": 260, "min_live": 0.25, "approx_gap_warn": 5.0,
            "sectors": {"전체": copy.deepcopy(SECTORS)},
            "market": {
                "전체": {"net": net, "ret": {"1": -4.324, "20": -22.838}, "approx_gap": {"pct": 1.58}},
                "거래소": {"net": net, "ret": {"1": -4.37, "20": -22.497}, "approx_gap": {"pct": 1.2}},
                "코스닥": {"net": net, "ret": {"1": -3.812, "20": -26.415}, "approx_gap": {"pct": 6.2}},
            },
            "streaks": {
                "actors": {"inst": "기관", "invpen": "투신+연기금", "forgn": "외국인", "indiv": "개인"},
                "market_actors": ["inst", "invpen", "forgn", "indiv"],
                "market": {"전체": {"forgn": {"sign": -1, "days": 7, "sum": -41234.5, "pct": 98.1},
                                    "inst": {"sign": 1, "days": 2, "sum": 4300.0, "pct": 55.0}}},
                "sectors": {"전체": {"반도체": {"forgn": {"sign": -1, "days": 9, "sum": -21000.0, "pct": 97.2}}}},
                "longest": {"전체": {"forgn": "반도체", "inst": "금융"}},
            },
            "krx_index": {"거래소": {"code": "001", "name": "코스피", "ret": {"1": -6.02, "20": -23.638}},
                          "코스닥": {"code": "101", "name": "코스닥", "ret": {"1": -5.032, "20": None}}},
        },
        "revision_stats": {"days": 10, "n_dates": 7, "rel_median_pct": 3.6, "flips": 14,
                           "top3_keep_pct": 88.9, "top3_lists": 42, "n_cells": 11340,
                           "seed": {"label": "아카이브 시드", "dates": ["2026-10-20"]}},
        "change": {"prev_date": "2026-11-05", "window": 20, "top_n": 5, "top": {"전체": {
            "inst": {"entered": ["유통"], "exited": ["보험"]},
            "invpen": {"entered": [], "exited": []}}}},
        "change_reason": None,
    }
    if final:
        P["final"] = True
        P["final_diff"] = {"n_changed": 12, "n_cells": 1234, "tol": 0.05, "changed": [], "market_totals": []}
    return P


EXPECTED = """\
오늘의 수급 · 2026-11-06(금) · 마감·잠정
잠정치다 — 다음 거래일 새벽 확정치로 바뀔 수 있다.
최근 7일 잠정→확정: 섹터 칸 중앙 4%, 부호 뒤집힘 14회, 상위3 유지 89%

시장 순매수[억] · 전체(거래소+코스닥) · 괄호는 1년 일별 %ile
- 기관 +2,500(88) · 외국인 -4,200(6) · 개인 +1,500(70) · 기타법인 +200(51)
- 금액 근사 오차 큼(코스닥 6.2%)

1일 수익률 — KRX 지수(정규장 15:30) | 자체 바구니(~20시 통합 종가)
- 거래소: 코스피 -6.02% | 바구니 -4.37%
- 코스닥: 코스닥 -5.03% | 바구니 -3.81%

오늘 섹터 순매수 상·하위 3[억(1년%ile)]
- 기관
  상위: 반도체 +1,200(97) · 금융 +800(90) · 유통 +300(99)
  하위: 화학 -650(4) · 건설 -90(5) · 통신 -20(40)
- 투신+연기금
  상위: 화학 +500(100) · 반도체 +300(80) · 건설 +60(65)
  하위: 통신 -400(2) · 금융 -120(20) · 유통 +0(—)
- 외국인 — 오늘 금액이 가장 큰 주체
  상위: 보험 +900(96) · 금융 +400(70) · 건설 +120(62)
  하위: 반도체 -5,000(3) · 화학 -300(30) · 통신 -10(45)

1년 분위 상단(≥95)·하단(≤5) — 오늘 값이 1년 일별 분포의 어디인가
- 기관 상단: 유통 99 · 반도체 97 / 하단: 화학 4 · 건설 5
- 투신+연기금 상단: 화학 100 / 하단: 통신 2
- 외국인 상단: 보험 96 / 하단: 반도체 3

연속 순매매[일·누적(연속 길이 1년%ile)] — 외국인: 7일 연속 순매도 누적 -4.1조(98) · 최장 섹터 반도체 9일 연속 순매도 누적 -2.1조(97)

20일 순매수 상위5 변동 — 20일 누적 기준, 11-05 리포트 대비
- 기관: 상위5에 새로 든 섹터: 유통 · 빠진 섹터: 보험
- 투신+연기금: 변동 없음

---
이 글은 수급 사실 요약이며 투자 판단·추천이 아니다. 섹터 수급 지표와 앞날 수익률의 관계는 검정했고 증거가 없다.
자료: 한국거래소 투자자별 매매동향·지수(키움증권 API로 수집). 금액은 순매수 수량×종가 근사, 섹터 구분·수익률은 자체 계산.
"""


def test_snapshot_from_fixture():
    """같은 페이로드면 같은 글 — 줄 순서는 금액 크기, %ile 은 페이로드 값 그대로, 외국인이 금액 최대 주체."""
    _F, text, _pages = dg.build(fixture_payload())
    assert text == EXPECTED


def test_dominant_actor_is_by_absolute_amount():
    """순매도가 가장 큰 주체도 '오늘 금액이 가장 큰 주체' 다 — 부호로 고르면 순매수 쪽만 뽑힌다."""
    net = {"inst": {"v": 100.0}, "forgn": {"v": -900.0}, "indiv": {"v": 700.0}, "etc": {"v": 100.0}}
    assert dg.dominant_actor(net) == "forgn"
    F = dg.collect(fixture_payload())
    assert [s["actor"] for s in F["sectors"]] == ["inst", "invpen", "forgn"]
    # 기관이 최대면 세 번째 묶음을 겹쳐 싣지 않는다
    P = fixture_payload()
    P["today"]["market"]["전체"]["net"]["inst"]["v"] = 9999.0
    F = dg.collect(P)
    assert [s["actor"] for s in F["sectors"]] == ["inst", "invpen"]
    assert F["sectors"][0]["dominant"]


def _long_payload(name_len: int, n: int = 30) -> dict:
    """극단이 많고 섹터 이름이 긴 날 — 상한을 넘기게."""
    P = fixture_payload()
    secs = {}
    for i in range(n):
        nm = f"{'가' * name_len}{i:02d}"
        hi = i % 2 == 0
        secs[nm] = _row(inst=(1000.0 - i, 99.0 if hi else 1.0), invpen=(500.0 - i, 99.0 if hi else 1.0),
                        forgn=(-3000.0 + i, 1.0 if hi else 99.0))
    P["today"]["sectors"]["전체"] = secs
    return P


def test_text_fits_char_limit_and_trims_extremes_first():
    """긴 날에는 극단 목록부터 줄여 상한 안에 넣는다 — 상위·하위 3 과 시장 줄은 그대로다.
    다 줄여도 넘치면 만들지 않는다(잘린 글을 내지 않는다)."""
    P = _long_payload(name_len=6)
    full4 = dg.body_text(dg.collect(P), dg.EXTREME_CAPS[0]) + "\n\n" + dg.FOOTER + "\n"
    assert len(full4) > dg.MAX_CHARS, "픽스처가 상한을 안 넘는다 — 줄이기를 시험하지 못한다"
    _F, text, _ = dg.build(P)
    assert len(text) <= dg.MAX_CHARS
    assert text.endswith(dg.FOOTER + "\n")
    assert "상위: 가가가가가가00 +1,000(99)" in text
    assert "곳" in text or " 외 " in text
    # 극단을 줄여서 들어가는 날은 연속 줄이 산다 — 연속을 먼저 떨어뜨리면 여기서 잡힌다(TRIM_LADDER 순서).
    _F, mid, _ = dg.build(_long_payload(name_len=2, n=20))
    assert len(mid) <= dg.MAX_CHARS and "곳" in mid and "연속 순매매" in mid
    with pytest.raises(dg.DigestError, match="상한"):
        dg.build(_long_payload(name_len=40))
    _F, text, _ = dg.build(fixture_payload(final=True))
    assert len(text) <= dg.MAX_CHARS


def test_disclaimer_is_fixed_tail():
    """면책·자료 출처가 정해진 글자 그대로 글 끝과 카드에 있다."""
    assert dg.DISCLAIMER == ("이 글은 수급 사실 요약이며 투자 판단·추천이 아니다. "
                             "섹터 수급 지표와 앞날 수익률의 관계는 검정했고 증거가 없다.")
    assert dg.SOURCE == ("자료: 한국거래소 투자자별 매매동향·지수(키움증권 API로 수집). "
                         "금액은 순매수 수량×종가 근사, 섹터 구분·수익률은 자체 계산.")
    assert "공개" not in dg.SOURCE        # 재배포 허락이 확인되지 않았다
    assert "밖에 게시하지 않는다" in dg.__doc__
    for kw in (dict(), dict(final=True)):
        _F, text, pages = dg.build(fixture_payload(**kw))
        assert text.endswith(f"\n\n---\n{dg.DISCLAIMER}\n{dg.SOURCE}\n")
        assert text.count(dg.DISCLAIMER) == 1
        for page in pages:
            assert dg.DISCLAIMER in page and dg.SOURCE in page


def test_provisional_and_final_wording():
    """값의 지위를 정확히 말한다 — 잠정은 바뀔 수 있다고, 확정은 몇 칸 바뀌었는지."""
    _F, prov, _ = dg.build(fixture_payload())
    assert "· 마감·잠정\n" in prov and "잠정치다" in prov
    assert not prov.splitlines()[0].endswith("확정")
    # 정직 줄 — 잠정이면 최근 확정일들의 수정 폭을 같이 말한다(없으면 없다고).
    assert "최근 7일 잠정→확정: 섹터 칸 중앙 4%, 부호 뒤집힘 14회, 상위3 유지 89%" in prov
    assert "잠정→확정 변경" not in prov
    P0 = fixture_payload()
    P0["revision_stats"] = {"n_dates": 0, "rel_median_pct": None, "flips": 0}
    _F, prov0, _ = dg.build(P0)
    assert "잠정→확정 수정 폭: 아직 확정 기록이 없다" in prov0

    _F, fin, pages = dg.build(fixture_payload(final=True))
    assert fin.splitlines()[0].endswith("· 마감·확정")
    assert "잠정치다" not in fin and "수정 폭" not in fin and "최근 7일 잠정→확정" not in fin
    assert "잠정→확정 변경: 12칸 / 1,234칸(±0.05억 초과, 섹터 1·5일 순매수)" in fin
    assert 'class="chip final"' in pages[0]

    P = fixture_payload(final=True)
    P["final_diff"], P["final_diff_reason"] = None, "같은 기준일의 잠정 리포트가 없었다"
    _F, fin2, _ = dg.build(P)
    assert "잠정→확정 비교 없음: 같은 기준일의 잠정 리포트가 없었다" in fin2

    _F, live, _ = dg.build(fixture_payload(finalized=False))
    assert live.splitlines()[0].endswith("· 장중·미확정")


def test_banned_word_in_body_fails_build(tmp_path):
    """본문에 금칙어가 들면 세 파일 모두 안 쓰고, 옛 다이제스트도 지운다(어제 것이 오늘 것처럼 남지 않게)."""
    P = fixture_payload()
    P["today"]["sectors"]["전체"]["급등주"] = P["today"]["sectors"]["전체"].pop("반도체")
    with pytest.raises(dg.DigestError, match="금칙어"):
        dg.build(P)
    (tmp_path / "payload.json").write_text(json.dumps(P, ensure_ascii=False), encoding="utf-8")
    for n in ("digest.md", "digest.json", "digest_card.html", "digest.png"):
        (tmp_path / n).write_text("어제 것", encoding="utf-8")
    assert dg.main(["--dir", str(tmp_path), "--png", "skip"]) == 1
    assert not any((tmp_path / n).exists() for n in dg.OUTPUTS)


def test_banned_words_are_caught_across_spacing():
    """띄어쓰기를 바꿔도 잡는다 — '수익  보장', '목표 가', '매수신호'."""
    assert "수익 보장" in dg.banned_hits("이건 수익  보장")
    assert dg.banned_hits("목표 가 3만") == ["목표가"]
    assert "매수 신호" in dg.banned_hits("매수신호")
    assert dg.banned_hits("기관 +1,200(97)") == []


def test_banned_words_survive_unicode_and_punctuation_tricks():
    """폭 0 문자·전각·문장부호를 끼워도 잡는다. 목록 구분자를 넘어 붙여 읽지는 않는다."""
    assert dg.banned_hits("추\u200b천") == ["추천"]
    assert dg.banned_hits("추.천 섹터") == ["추천"]
    assert dg.banned_hits("급-등") == ["급등"]
    assert dg.banned_hits("（대장주）") == ["대장주"]
    assert dg.banned_hits("수\ufeff혜") == ["수혜"]
    for w in ("유망", "주도주", "매수하라", "담아라", "전망", "기대", "급락"):
        assert w in dg.banned_hits(f"섹터 {w}"), w
    assert dg.banned_hits("통신 · 호텔") == []                 # '신호' 가 아니다
    assert dg.banned_hits(EXPECTED.split("---")[0]) == []


def test_disclaimer_alone_passes_guard(tmp_path):
    """꼬리는 '추천이 아니다' 를 말해야 하므로 금칙어 검사에서 빠진다 — 본문만 본다."""
    assert dg.banned_hits(dg.FOOTER), "꼬리에 금칙어가 없다면 이 검사는 아무것도 지키지 않는다"
    (tmp_path / "payload.json").write_text(json.dumps(fixture_payload(), ensure_ascii=False), encoding="utf-8")
    assert dg.main(["--dir", str(tmp_path), "--png", "skip"]) == 0
    assert (tmp_path / "digest.md").read_text("utf-8") == EXPECTED
    F = json.loads((tmp_path / "digest.json").read_text("utf-8"))
    assert F["asof"] == "2026-11-06" and F["status"] == "마감·잠정" and F["dominant"] == "forgn"
    assert F["text_chars"] == len(EXPECTED) and F["png"] is None
    assert F["net"][1] == {"actor": "forgn", "ko": "외국인", "v": -4200.5, "pct": 6.0}


def test_main_refuses_verify_failed_report(tmp_path):
    """자기 검증에 걸린 리포트로는 만들지 않는다 — 밖에 나갈 글이다."""
    (tmp_path / "payload.json").write_text(json.dumps(fixture_payload(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "VERIFY_FAILED.txt").write_text("x", encoding="utf-8")
    assert dg.main(["--dir", str(tmp_path), "--png", "skip"]) == 1
    assert not (tmp_path / "digest.md").exists()
    assert dg.main(["--dir", str(tmp_path), "--png", "skip", "--allow-unverified"]) == 0


def test_card_html_is_card_sized_and_carries_facts():
    """카드는 1080×1350, 스크립트·외부 자원 없음, 값은 글과 같고 순매도 막대는 매도 색이다."""
    _F, _text, pages = dg.build(fixture_payload())
    page = pages[0]
    assert (dg.CARD_W, dg.CARD_H) == (1080, 1350)
    assert "width: 1080px; height: 1350px" in page
    assert "<script" not in page and "http://" not in page and "https://" not in page
    for s in ("+2,500", "-4,200", "-6.02%", "-3.81%", "오늘 금액 최대", "상위5에 새로 든 섹터: 유통",
              "금액 근사 오차 큼 코스닥 6.2%", "빨강 = 순매수", "파랑 = 순매도", "상단(≥95)·하단(≤5)"):
        assert s in page, s
    # 외국인 막대(순매도)는 sell, 기관(순매수)은 buy — 0 기준선 왼쪽으로 뻗는다
    rows = page.split('<div class="net-row">')[1:]
    assert 'class="bar buy" style="left:50%' in rows[0]
    assert 'class="bar sell" style="left:0.00%;width:50.00%"' in rows[1]
    # 줄인 판은 극단 목록·연속 줄만 다르다
    assert len(pages) == len(dg.CARD_LADDER)
    assert "연속 순매매" in pages[0] and "연속 순매매" not in pages[-1]


def test_streak_line_names_the_dominant_actor_and_longest_sector():
    """연속 줄은 오늘 금액 최대 주체의 시장 연속과 최장 섹터를 옮긴다 — 부호·조 단위·%ile 그대로."""
    F = dg.collect(fixture_payload())
    assert F["streak"]["actor"] == "forgn" and F["streak"]["sector"] == "반도체"
    line = dg.streak_line(F)
    assert line == ("연속 순매매[일·누적(연속 길이 1년%ile)] — 외국인: 7일 연속 순매도 누적 -4.1조(98)"
                    " · 최장 섹터 반도체 9일 연속 순매도 누적 -2.1조(97)")
    assert dg.streak_line(F, with_sector=False).endswith("-4.1조(98)")
    assert dg.fmt_big(9999.4) == "+9,999억" and dg.fmt_big(-10000.0) == "-1.0조"
    # ≈0 날은 연속이 없다고 말한다
    assert dg.streak_text({"sign": 0, "days": 0, "sum": 0.0, "pct": None}) == "연속 없음(오늘 ≈0)"
    # streaks 가 없는 옛 리포트는 줄을 빼되 만들어는 진다
    P = fixture_payload()
    del P["today"]["streaks"]
    _F, text, _ = dg.build(P)
    assert "연속 순매매" not in text


def test_out_dir_keeps_the_report_folder_untouched(tmp_path):
    """--out 이 있으면 산출물은 거기로만 간다 — 읽기 전용 리포트 폴더(reports/latest)에 쓰지 않는다."""
    src = tmp_path / "report"
    src.mkdir()
    (src / "payload.json").write_text(json.dumps(fixture_payload(), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "scratch" / "digest-out"
    assert dg.main(["--dir", str(src), "--out", str(out), "--png", "skip"]) == 0
    assert (out / "digest.md").exists() and (out / "digest.json").exists()
    assert sorted(p.name for p in src.iterdir()) == ["payload.json"]


def test_card_text_guard_catches_what_only_the_card_says(monkeypatch):
    """카드에만 있는 글(제목·범례 등)에 금칙어가 들면 만들지 않는다 — 글 검사만으로는 못 잡는다."""
    real = dg.card_html
    monkeypatch.setattr(dg, "card_html",
                        lambda F, cap=3, streak="full": real(F, cap, streak).replace("오늘의 수급</h1>", "유망 섹터</h1>"))
    with pytest.raises(dg.DigestError, match="digest_card.html"):
        dg.build(fixture_payload())


def test_displayed_percentile_decides_the_band():
    """보이는 정수로 문턱을 비교한다 — 94.6 은 '95' 로 보이니 상단에 들고, 5.4 는 '5' 로 하단에 든다.
    99.6 은 '100' 으로 보인다(둘이 어긋나지 않는다)."""
    P = fixture_payload()
    secs = P["today"]["sectors"]["전체"]
    secs["금융"]["inst"]["pct"] = 94.6
    secs["통신"]["inst"]["pct"] = 5.4
    secs["보험"]["inst"]["pct"] = 94.4
    secs["유통"]["inst"]["pct"] = 99.6
    F = dg.collect(P)
    inst = F["sectors"][0]
    assert ["금융", 95] in inst["hi"] and ["유통", 100] in inst["hi"]
    assert ["통신", 5] in inst["lo"]
    assert all(n != "보험" for n, _ in inst["hi"])
    text = dg.compose(F)[1]
    assert "금융 +800(95)" in text and "금융 95" in text
    assert dg.pct_int(94.5) == 95 and dg.pct_int(5.5) == 6 and dg.pct_int(None) is None


def test_unclassified_bucket_is_not_listed_as_a_sector():
    """'(미분류)' 는 섹터가 아니다 — 상·하위·분위·변동 목록 어디에도 안 싣는다."""
    P = fixture_payload()
    P["today"]["sectors"]["전체"]["(미분류)"] = _row(inst=(99999.0, 100.0), invpen=(-99999.0, 0.0),
                                                   forgn=(-99999.0, 0.0))
    P["change"]["top"]["전체"]["inst"]["entered"] = ["유통", "(미분류)"]
    F, text, pages = dg.build(P)
    assert "미분류" not in text and "미분류" not in pages[0]
    assert F["excluded"] == ["(미분류)"]


def test_few_sectors_never_list_one_sector_as_both_top_and_bottom():
    """섹터가 6개 미만인 날 같은 섹터가 상위와 하위에 함께 서지 않는다."""
    P = fixture_payload()
    secs = P["today"]["sectors"]["전체"]
    for k in list(secs)[4:]:
        del secs[k]
    F = dg.collect(P)
    for s in F["sectors"]:
        tops = {n for n, *_ in s["top"]}
        assert not tops & {n for n, *_ in s["bottom"]}, s
        assert len(s["top"]) == 3 and len(s["bottom"]) == 1


HOOK_RE = r"^if \[ -n \"\$\{KR_QUANT_DIGEST:-\}\" \]; then .*$"


def _hook_line() -> str:
    import re
    lines = re.findall(HOOK_RE, (REPO / "scripts" / "daily_report.sh").read_text("utf-8"), flags=re.M)
    assert len(lines) == 1, lines
    return lines[0]


def _run_hook(tmp_path: Path, fake_body: str, env_extra: dict) -> "subprocess.CompletedProcess":
    """daily_report.sh 의 다이제스트 줄만 떼어 set -euo pipefail 아래에서 가짜 uv 로 돌린다."""
    fake = tmp_path / "fake_uv"
    fake.write_text("#!/usr/bin/env bash\n" + fake_body, encoding="utf-8")
    fake.chmod(0o755)
    script = tmp_path / "hook.sh"
    script.write_text(f"set -euo pipefail\nUVR=({fake})\nBUILD={tmp_path}\n{_hook_line()}\necho PUBLISHED\n",
                      encoding="utf-8")
    env = {**os.environ, "KR_QUANT_DIGEST": "1", **env_extra}
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=40, env=env)


@pytest.mark.skipif(sys.platform == "win32", reason="bash")
def test_digest_hook_cannot_block_or_fail_publishing(tmp_path):
    """다이제스트가 멈추거나(TERM 도 무시) 실패해도 리포트 게시(다음 줄)는 계속된다. 오프라인·판 고정으로 부른다."""
    rec = tmp_path / "args"
    r = _run_hook(tmp_path, f'echo "$UV_OFFLINE $*" > {rec}\n', {})
    assert r.returncode == 0 and "PUBLISHED" in r.stdout
    assert rec.read_text().split() == ["1", "--with", "playwright==1.48.0", "python", "scripts/digest.py",
                                       "--dir", str(tmp_path)]

    r = _run_hook(tmp_path, "exit 1\n", {})
    assert r.returncode == 0 and "PUBLISHED" in r.stdout and "못 만들었다" in r.stderr

    t0 = time.monotonic()
    r = _run_hook(tmp_path, "trap '' TERM\nexec sleep 120\n", {"KR_QUANT_DIGEST_TIMEOUT": "1s"})
    assert r.returncode == 0 and "PUBLISHED" in r.stdout
    assert time.monotonic() - t0 < 30


def test_render_waits_are_bounded():
    """브라우저 단계마다 상한이 있다 — fonts.ready 가 안 풀려도 5초 뒤 찍는다."""
    import inspect
    src = inspect.getsource(dg.render_png)
    assert "FONTS_READY_JS" in src and 'evaluate("document.fonts.ready")' not in src
    assert "setTimeout(r, 5000)" in dg.FONTS_READY_JS
    assert src.count("timeout=RENDER_TIMEOUT_MS") >= 3


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


def test_png_card_renders_at_card_size(tmp_path):
    """Playwright Chromium 이 있으면 실제로 찍어 크기를 본다. 없으면(CI) 건너뛴다 — 위 HTML 검사가 대신한다."""
    pytest.importorskip("playwright")
    (tmp_path / "payload.json").write_text(json.dumps(fixture_payload(final=True), ensure_ascii=False),
                                           encoding="utf-8")
    try:
        rc = dg.main(["--dir", str(tmp_path), "--png", "require"])
    except Exception as e:                                   # pragma: no cover — 브라우저 사정
        pytest.skip(f"Chromium 을 못 띄웠다: {e}")
    assert rc == 0
    assert _png_size(tmp_path / "digest.png") == (dg.CARD_W, dg.CARD_H)
    F = json.loads((tmp_path / "digest.json").read_text("utf-8"))
    assert F["png"] == "digest.png"


def test_content_past_card_edge_is_overflow():
    """넘침 판정 — 아래·오른쪽 어느 쪽으로든 1px 이라도 넘치면 찍지 않는다. 브라우저 없이 돈다."""
    dg.check_extent(dg.CARD_W, dg.CARD_H)
    for sw, sh in ((dg.CARD_W, dg.CARD_H + 1), (dg.CARD_W + 1, dg.CARD_H)):
        with pytest.raises(dg.CardOverflow):
            dg.check_extent(sw, sh)


def test_overflowing_card_is_not_shot(tmp_path):
    """내용이 카드를 넘치면 잘린 PNG 를 남기지 않는다(꼬리 면책이 잘린 카드가 한때 통과했다)."""
    pytest.importorskip("playwright")
    P = copy.deepcopy(_long_payload(name_len=3, n=30))
    F = dg.collect(P)
    page = dg.card_html(F, 30).replace("</body>", "<div style='height:400px'>넘침</div></body>")
    (tmp_path / "c.html").write_text(page, encoding="utf-8")
    try:
        with pytest.raises(dg.CardOverflow):
            dg.render_png(tmp_path / "c.html", tmp_path / "c.png")
    except dg.DigestError as e:                              # pragma: no cover
        pytest.skip(f"Chromium 을 못 띄웠다: {e}")
    assert not (tmp_path / "c.png").exists()


def test_card_says_provisional_only_on_a_provisional_card():
    """카드 표지가 값의 지위와 같다 — 뒤집혀도 글(md) 검사는 초록이었다(카드 노트는 따로 만든다)."""
    _F, _t, prov = dg.build(fixture_payload())
    _F, _t, final = dg.build(fixture_payload(final=True))
    for page in prov:
        assert "잠정치다" in page and 'class="chip prov"' in page
        assert "잠정→확정 변경" not in page
    for page in final:
        assert "잠정치다" not in page and 'class="chip final"' in page
        assert "잠정→확정 변경: 12칸 / 1,234칸" in page


def _css(page: str) -> dict[str, dict[str, str]]:
    """``<style>`` 의 선택자 → 속성. 브라우저 없이 읽는 만큼만(중첩·미디어 쿼리 없는 템플릿이다)."""
    style = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    out: dict[str, dict[str, str]] = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style):
        props = dict(p.split(":", 1) for p in body.split(";") if ":" in p)
        for s in sel.split(","):
            out.setdefault(s.strip(), {}).update({k.strip(): v.strip() for k, v in props.items()})
    return out


def _lum(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def test_card_disclaimer_and_source_are_visible_without_a_browser():
    """면책·출처는 글자가 HTML 에 있어도 CSS 로 가려지면 PNG 에서 사라진다. 숨기는 속성이 없고, 꼬리는 body 바로
    아래 ``footer`` 이며 읽을 만한 크기·대비다(대비 4.5:1 — 본문 글자 기준)."""
    page = dg.build(fixture_payload())[2][0]
    css = _css(page)
    for sel, props in css.items():
        assert props.get("display") != "none", sel
        assert props.get("visibility") not in ("hidden", "collapse"), sel
        assert props.get("opacity") not in ("0", "0.0"), sel
        assert not re.fullmatch(r"0(px|em|rem)?", props.get("font-size", "1")), sel
    foot = re.search(r"</section>\s*<footer>(.*?)</footer>\s*</body>", page, re.S)
    assert foot, "꼬리가 body 바로 아래 footer 가 아니다"
    assert dg.DISCLAIMER in foot.group(1) and dg.SOURCE in foot.group(1)
    assert "<!--" not in foot.group(1)
    rule = css["footer"]
    assert int(rule["font-size"].removesuffix("px")) >= 16
    root = css[":root"]
    ink, bg = (root[rule["color"][4:-1]], root["--surface"])
    hi, lo = sorted((_lum(ink), _lum(bg)), reverse=True)
    assert (hi + 0.05) / (lo + 0.05) >= 4.5, f"꼬리 글자 대비가 낮다: {ink} on {bg}"


def test_card_colors_match_its_legend():
    """범례가 '빨강 = 순매수, 파랑 = 순매도' 라고 말한다 — 색 변수가 뒤집히면 막대가 반대로 읽힌다."""
    root = _css(dg.build(fixture_payload())[2][0])[":root"]
    buy, sell = root["--buy"], root["--sell"]
    assert int(buy[1:3], 16) > int(buy[5:7], 16) and int(sell[5:7], 16) > int(sell[1:3], 16)


def test_change_phrase_table():
    """한쪽만 바뀌어도 변동이다. 비교 자체가 없으면(None) '비교 없음'."""
    ko = {"actor": "inst", "ko": "기관"}
    assert dg.change_phrase({**ko, "entered": None, "exited": None}, 5) == "비교 없음"
    assert dg.change_phrase({**ko, "entered": [], "exited": []}, 5) == "변동 없음"
    assert dg.change_phrase({**ko, "entered": ["금융"], "exited": []}, 5).startswith("상위5에 새로 든 섹터: 금융")
    assert "빠진 섹터: 금융" in dg.change_phrase({**ko, "entered": [], "exited": ["금융"]}, 5)


def test_old_format_payload_falls_back_to_the_stated_defaults():
    """옛 페이로드(키 없음)는 문서에 적힌 기본값 — 상위5·20일, 바뀐 칸 0."""
    P = fixture_payload(final=True)
    del P["change"]["top_n"], P["change"]["window"]
    del P["final_diff"]["n_changed"], P["final_diff"]["n_cells"]
    F = dg.collect(P)
    assert F["change"]["top_n"] == 5 and F["change"]["window"] == 20
    assert F["final_diff"]["n_changed"] == 0 and F["final_diff"]["n_cells"] == 0


def test_trim_ladders_end_with_counts_only_and_no_streak():
    """길이가 넘칠 때 줄이는 순서 — 칸마다 **더 줄어든다**. 극단 목록은 끝내 개수만 남고(`N곳`), 마지막은 연속 줄도 없다."""
    F = dg.collect(_long_payload(name_len=6))
    bodies = [dg.body_text(F, cap, st) for cap, st in dg.TRIM_LADDER]
    assert all(a > b for a, b in zip(map(len, bodies), map(len, bodies[1:]))), [len(b) for b in bodies]
    for body in bodies[3:]:                                    # 개수만 남은 칸들
        ext = next(ln for ln in body.splitlines() if ln.startswith("- 기관") and "상단:" in ln)
        assert re.search(r"상단: \d+곳 / 하단: \d+곳", ext), ext
    assert "연속" in bodies[0] and "연속" not in bodies[-1]

    pages = [dg.card_html(F, cap, st) for cap, st in dg.CARD_LADDER]
    assert all(a > b for a, b in zip(map(len, pages), map(len, pages[1:]))), [len(p) for p in pages]
    assert re.search(r"상단: \d+곳 <span", pages[-1]) and "연속" not in pages[-1]


def test_card_defaults_are_the_first_rung():
    F, _t, pages = dg.build(fixture_payload())
    assert dg.card_html(F) == pages[0]


def test_card_missing_net_value_draws_no_bar():
    """값이 없는 주체는 막대 없이 '—' 다 — 폭이 0 이 아니면 없는 값이 숫자처럼 보인다."""
    P = fixture_payload()
    P["today"]["market"]["전체"]["net"]["etc"] = {"v": None, "pct": None}
    page = dg.build(P)[2][0]
    row = page.split('<div class="net-row">')[4]
    assert "width:0.00%" in row and ">—<" in row


def test_empty_extreme_list_says_none_in_words():
    assert dg._capped([], 3) == "없음" and dg._capped([], 0) == "없음"
    assert dg._capped(["가", "나", "다"], 2) == "가 · 나 외 1" and dg._capped(["가", "나"], 0) == "2곳"
    P = fixture_payload()
    for r in P["today"]["sectors"]["전체"].values():
        for a in ("inst", "invpen", "forgn"):
            r[a]["pct"] = 50.0
    _F, text, pages = dg.build(P)
    assert "상단: 없음 / 하단: 없음" in text and "상단: 없음" in pages[0] and "None" not in pages[0]


def test_reference_day_comes_from_today_then_from_the_report_dates():
    P = fixture_payload()
    P["today"]["date"], P["dates"] = "2026-11-06", ["2026-11-05", "2026-11-07"]
    assert dg.collect(P)["asof"] == "2026-11-06"
    del P["today"]["date"]
    assert dg.collect(P)["asof"] == "2026-11-07"


def test_streak_line_keeps_the_sector_only_on_the_full_rung():
    """연속 줄의 꼴 — 첫 판은 최장 섹터까지, 줄인 판은 시장만, 마지막 판은 줄 없이."""
    F, _t, pages = dg.build(fixture_payload())
    full, nosector, none = pages[:3]
    assert "최장 섹터 반도체" in full
    assert "연속 순매매" in nosector and "최장 섹터" not in nosector
    assert "연속 순매매" not in none
    assert "최장 섹터" not in dg.body_text(F, 0, "nosector")


def test_no_comparison_still_states_the_default_window_and_top_n():
    """직전 리포트가 없는 날의 머리 — 20일·상위5(비교 없음이라 값이 어디서도 안 온다)."""
    P = fixture_payload()
    P["change"], P["change_reason"] = None, "직전 리포트가 없다"
    F, _t, pages = dg.build(P)
    assert (F["change"]["window"], F["change"]["top_n"]) == (20, 5)
    assert "20일 순매수 상위5 변동" in pages[0] and "비교 없음 — 직전 리포트가 없다" in pages[0]


def test_ties_rank_by_sector_name_whatever_the_payload_order():
    """같은 금액이면 이름순 — 페이로드 순서에 달리면 같은 수치가 날마다 다른 글이 된다."""
    secs = {"하": _row(inst=(10.0, 50.0)), "가": _row(inst=(10.0, 50.0)), "나": _row(inst=(20.0, 50.0))}
    assert [t[0] for t in dg._rank(secs, "inst")] == ["나", "가", "하"]


# ── PNG 경로 — 브라우저 없이 `main` 의 사다리·실패 처리를 돈다(찍는 함수만 바꿔 끼운다). 실제 찍기는 위 Playwright 검사.

def _png_dir(tmp_path: Path) -> Path:
    (tmp_path / "payload.json").write_text(json.dumps(_long_payload(name_len=6), ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_png_ladder_shoots_the_next_smaller_card_after_an_overflow(tmp_path, monkeypatch, capsys):
    """넘치면 다음 판으로 다시 찍는다 — 찍힌 판이 폴더에 남고, 넘친 판의 PNG 는 없다."""
    shot = []

    def fake(html_path, png_path):
        shot.append(html_path.read_text("utf-8"))
        if len(shot) < 3:
            raise dg.CardOverflow("넘친다")
        png_path.write_bytes(b"png")

    monkeypatch.setattr(dg, "render_png", fake)
    assert dg.main(["--dir", str(_png_dir(tmp_path)), "--png", "require"]) == 0
    F, _t, pages = dg.build(_long_payload(name_len=6))
    assert shot == pages and len(shot) == len(dg.CARD_LADDER)
    assert (tmp_path / "digest_card.html").read_text("utf-8") == pages[-1]
    assert (tmp_path / "digest.png").read_bytes() == b"png"
    assert json.loads((tmp_path / "digest.json").read_text("utf-8"))["png"] == "digest.png"
    err = capsys.readouterr().err
    assert "극단 목록 1개·연속 줄 nosector" in err and "극단 목록 0개·연속 줄 none" in err


def test_png_gives_up_when_every_rung_overflows(tmp_path, monkeypatch):
    """끝까지 넘치면 auto 는 글을 남기고 PNG 없이, require 는 아무것도 안 남기고 실패한다."""
    def over(html_path, png_path):
        png_path.write_bytes(b"truncated")
        raise dg.CardOverflow("넘친다")

    monkeypatch.setattr(dg, "render_png", over)
    d = _png_dir(tmp_path)
    assert dg.main(["--dir", str(d), "--png", "auto"]) == 0
    assert not (d / "digest.png").exists() and (d / "digest.md").exists()
    assert json.loads((d / "digest.json").read_text("utf-8"))["png"] is None
    assert dg.main(["--dir", str(d), "--png", "require"]) == 1
    assert not any((d / n).exists() for n in dg.OUTPUTS)


def test_png_without_playwright_skips_or_fails_by_mode(tmp_path, monkeypatch):
    def missing(html_path, png_path):
        raise ImportError("playwright")

    monkeypatch.setattr(dg, "render_png", missing)
    d = _png_dir(tmp_path)
    assert dg.main(["--dir", str(d), "--png", "auto"]) == 0
    assert (d / "digest.md").exists() and json.loads((d / "digest.json").read_text("utf-8"))["png"] is None
    assert dg.main(["--dir", str(d), "--png", "require"]) == 1
    assert not any((d / n).exists() for n in dg.OUTPUTS)


def test_png_skip_never_calls_the_browser(tmp_path, monkeypatch):
    """찍기 예외는 ``main`` 이 삼키므로(카드만 실패) 예외가 아니라 호출 자체를 센다."""
    calls = []
    monkeypatch.setattr(dg, "render_png", lambda *a: calls.append(a))
    assert dg.main(["--dir", str(_png_dir(tmp_path)), "--png", "skip"]) == 0
    assert calls == []
