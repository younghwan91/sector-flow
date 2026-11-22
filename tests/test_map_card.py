"""수급 지도 공유 카드(``scripts/map_card.py`` + ``map.html?card=tall|wide``) — 카드 크기, 단위·근사·출처 줄, 데모 표지.

브라우저 없이 도는 검사(꼬리 줄의 글, 게시 폴더 거부, 옛 카드 지우기)는 늘 돈다. Chromium 으로 실제로 찍는 검사는 Playwright 가
없으면 건너뛴다(``uv run --extra dev --with 'playwright==1.48.0' pytest tests/test_map_card.py``) — 주간 ``harness-selftest``
처럼 ``SF_WEB_SMOKE_REQUIRED=1`` 이면 건너뛰지 않고 실패한다.
"""
from __future__ import annotations

import importlib.util
import os
import re
import struct
from pathlib import Path

import pytest

from sectorflow.basis import DATA_BASIS_SHORT, DEMO_NOTICE
from sectorflow.wording import map_hits

REPO = Path(__file__).resolve().parent.parent


def _mod(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SM = _mod("sector_map", REPO / "scripts" / "sector_map.py")
MC = _mod("map_card", REPO / "scripts" / "map_card.py")


def _payload(demo: bool) -> dict:
    """손 페이로드 — 3섹터 × 6일, 실데이터 아님."""
    names = ["가", "나", "다"]
    f = {s: {"indiv": [(-1) ** (d + i) * 10.0 * (i + 1) for d in range(6)], "forgn": [3.0 * (i + 1)] * 6,
             "inst": [-2.0 * (i + 1)] * 6, "etc": [0.5] * 6} for i, s in enumerate(names)}
    return {"dates": [f"2026-01-{5 + d:02d}" for d in range(6)], "sectors": names, "markets": ["거래소"],
            "flows": {"거래소": f}, "demo": demo}


def _html(demo: bool) -> str:
    return SM.render(SM.build_map_data(_payload(demo)), Path(SM.TEMPLATE).read_text("utf-8"))


def _cardfoot(html: str) -> str:
    m = re.search(r'<div class="cardfoot" id="cardfoot">(.*?)\n  </div>', html, re.S)
    assert m, "카드 꼬리(cardfoot)가 없다"
    return re.sub(r"<[^>]+>", "", m.group(1))


def test_card_has_basis_line():
    """카드 꼬리에 자료 범위·단위·근사·출처가 들어간다. 데모 카드의 출처는 실데이터 출처가 아니라 합성 데모다."""
    real, demo = _cardfoot(_html(False)), _cardfoot(_html(True))
    for foot in (real, demo):
        assert DATA_BASIS_SHORT in foot and "단위 억원(종가 환산 근사)" in foot and "예측이 아니다" in foot
        assert "섹터와 섹터, 주체와 주체를 잇지 않는다" in foot
        assert map_hits(foot) == []
    assert SM.SOURCE in real and SM.DEMO_SOURCE not in real
    assert SM.DEMO_SOURCE in demo and SM.SOURCE not in demo
    assert "[합성 데모]" in demo and DEMO_NOTICE in demo


def test_card_refuses_a_published_folder_and_clears_stale_cards(tmp_path, capsys):
    """게시된 폴더(이름이 기준일)에는 쓰지 않는다. 지도가 없으면 실패하고 옛 카드를 남기지 않는다 — 어제 카드가 오늘 것처럼 걸린다."""
    pub = tmp_path / "2026-01-10"
    pub.mkdir()
    assert MC.main(["--dir", str(pub)]) == 2 and "게시된 폴더" in capsys.readouterr().err
    build = tmp_path / "2026-01-10.building.1"
    build.mkdir()
    for n in MC.OUTPUTS:
        (build / n).write_bytes(b"old")
    assert MC.main(["--dir", str(build)]) == 1
    assert not any((build / n).exists() for n in MC.OUTPUTS), "옛 카드가 남았다"
    assert "map.html" in capsys.readouterr().err


def _png_size(p: Path) -> tuple[int, int]:
    b = p.read_bytes()
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", b[16:24])


def _need_browser():
    if importlib.util.find_spec("playwright") is None:
        if os.environ.get("SF_WEB_SMOKE_REQUIRED") == "1":
            pytest.fail("SF_WEB_SMOKE_REQUIRED=1 인데 Playwright 가 없다")
        pytest.skip("Playwright 없음 — uv run --with 'playwright==1.48.0' pytest tests/test_map_card.py")


def test_card_sizes(tmp_path):
    # mutation-exempt: Chromium 이 있는 로컬·주간 워크플로에서만 돈다 — 기본 CI·변이 하네스에서는 건너뛴다(글은 위 검사가 변이로 지킨다)
    _need_browser()
    (tmp_path / "map.html").write_text(_html(True), "utf-8")
    rc = MC.main(["--dir", str(tmp_path)])
    if rc == 2 and os.environ.get("SF_WEB_SMOKE_REQUIRED") != "1":
        pytest.skip("Chromium 을 못 띄웠다")
    assert rc == 0
    assert _png_size(tmp_path / "map.png") == (1080, 1350)
    assert _png_size(tmp_path / "map_wide.png") == (1200, 675)


def test_card_layout_is_still_and_carries_its_labels(tmp_path):
    # mutation-exempt: Chromium 이 있을 때만 돈다(위와 같다)
    _need_browser()
    from playwright.sync_api import sync_playwright

    (tmp_path / "map.html").write_text(_html(True), "utf-8")
    dg = MC._digest()
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:
            exe = dg._cached_chromium()
            if not exe:
                pytest.skip("Chromium 없음")
            b = p.chromium.launch(executable_path=exe)
        try:
            for mode, _name, w, h in MC.CARDS:
                pg = b.new_page(viewport={"width": w, "height": h})
                pg.route("**/*", lambda r: r.continue_() if r.request.url.startswith("file:") else r.abort())
                pg.goto((tmp_path / "map.html").as_uri() + f"?card={mode}")
                pg.wait_for_timeout(200)
                facts = pg.evaluate("""() => ({text: document.body.innerText,
                  canvas: getComputedStyle(document.getElementById("cv")).display,
                  ctl: getComputedStyle(document.querySelector(".ctl")).display,
                  nodes: document.querySelectorAll("#svg .node").length})""")
                assert facts["canvas"] == "none" and facts["ctl"] == "none", f"{mode}: 입자·조작이 카드에 남았다"
                assert facts["nodes"] > 0
                for must in ("[합성 데모]", DATA_BASIS_SHORT, "단위 억원(종가 환산 근사)", SM.DEMO_SOURCE):
                    assert must in facts["text"], f"{mode}: '{must}' 가 안 보인다"
                assert pg.evaluate(MC.NODES_OUTSIDE_JS) == 0
                pg.close()
        finally:
            b.close()
