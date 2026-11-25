"""게시용 글 초안(``sectorflow.caption``) — 지도와 같은 숫자, 두 주체를 잇지 않음, 피할 말 없음, 데모 표지, 보내지 않음."""
from __future__ import annotations

import json

import pytest

from sectorflow import caption as C
from sectorflow import flowmap
from sectorflow.basis import DEMO_MARK
from sectorflow.wording import map_hits


def _payload(demo=True):
    f = lambda **k: {a: list(v) for a, v in k.items()}  # noqa: E731
    flows = {"가상전자": f(indiv=[5, -300], forgn=[0, 120], inst=[-5, 170], etc=[0, 10]),
             "가상화학": f(indiv=[1, 40], forgn=[0, -90], inst=[-1, 60], etc=[0, -10]),
             "(미분류)": f(indiv=[0, -999], forgn=[0, 0], inst=[0, 999], etc=[0, 0])}
    return {"dates": ["2026-01-05", "2026-01-06"], "sectors": list(flows), "markets": ["거래소"],
            "flows": {"거래소": flows}, "demo": demo, "today": {"x": 1}}


def test_names_the_biggest_seller_and_buyer_with_their_top_sector_separately():
    text = C.caption(_payload(), url="https://example.invalid/d/2026-01-06/")
    lines = text.splitlines()
    assert lines[0] == f"{DEMO_MARK} 01/06 섹터 수급 지도"
    # 시장 합은 (미분류) 포함(지도 머리글과 같다) — 순매도 1위 개인 −1,259, 순매수 1위 기관 +1,229. 맨 위 섹터는 (미분류)를 빼고 고른다
    assert lines[1] == "오늘 개인은 가상전자를 가장 많이 넘겼고(−300억), 기관은 가상전자를 가장 많이 받았습니다(+170억)."
    assert "누가 누구에게 판 것이 아님" in text and "예측·투자 조언 아님" in text
    assert lines[-1] == "https://example.invalid/d/2026-01-06/"
    assert map_hits(text) == []


def test_numbers_match_the_map_window_sums():
    P = _payload()
    f = C.day_facts(P)
    sums = flowmap.window_sums(P, ["거래소"], 1, 1)
    assert f["gave"]["sector_v"] == sums["가상전자"]["indiv"] and f["took"]["total"] == 1229


def test_long_form_adds_market_line_and_map_headline_facts():
    text = C.caption(_payload(), long=True, today=False)
    assert "시장 전체 순매매: 개인 −1,259억 · 외국인 +30억 · 기관 +1,229억 · 기타법인 0억" in text.replace("+0억", "0억")
    assert "· 가상전자 — 개인 −300억" in text and not text.splitlines()[1].startswith("오늘")


def test_upto_cuts_the_date_axis_and_drops_day_only_blocks():
    P = C.upto(_payload(), "2026-01-05")
    assert P["dates"] == ["2026-01-05"] and P["flows"]["거래소"]["가상전자"]["indiv"] == [5]
    assert "today" not in P, "그날 배치의 요약은 잘라 만들 수 없다"
    with pytest.raises(C.CaptionError, match="날짜에 없다"):
        C.upto(_payload(), "2026-01-07")


def test_refuses_when_nothing_moved():
    P = _payload()
    for sec in P["flows"]["거래소"].values():
        for a in sec:
            sec[a] = [0, 0]
    with pytest.raises(C.CaptionError, match="없다"):
        C.caption(P)


def test_refuses_avoided_words(monkeypatch):
    monkeypatch.setattr(C, "TAIL", "외국인 자금 유입")
    with pytest.raises(C.CaptionError, match="피할 말"):
        C.caption(_payload())


def test_cli_prints_only_and_warns_on_real_data(tmp_path, capsys):
    p = tmp_path / "payload.json"
    p.write_text(json.dumps(_payload(demo=False), ensure_ascii=False), "utf-8")
    assert C.main(["--payload", str(p)]) == 0
    out, err = capsys.readouterr()
    assert DEMO_MARK not in out and "https://" not in out, "실데이터 글에 공개 링크를 붙이지 않는다"
    assert "게시하지 않는다" in err
    p.write_text(json.dumps(_payload(), ensure_ascii=False), "utf-8")
    assert C.main(["--payload", str(p), "--date", "2026-01-06"]) == 0
    assert capsys.readouterr().out.rstrip().endswith(C.permalink("2026-01-06"))


def test_x_weight_counts_hangul_double_and_links_flat():
    assert C.x_weight("가나 ab https://x.invalid/very/long/path") == 4 + 1 + 2 + 1 + 23
