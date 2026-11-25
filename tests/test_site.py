"""공개 랜딩(``site/index.html`` + ``site/build.py``) — 데모만, 스크립트·추적 없음, 고지·후원·자기 키 안내가 있고 그 명령이 실제로 돈다.

공개 표면 전반(그림 원장·벤더 업종명·추적)은 ``tests/test_public_surface.py`` 가 ``site/``·``_site/`` 까지 본다.
여기서는 랜딩만의 약속과 빌드의 관문(실데이터 폴더는 받지 않는다)을 본다. DB·네트워크·브라우저 없음.
"""
from __future__ import annotations

import html
import importlib.util
import json
import re
from pathlib import Path

import pytest
from test_docs_cli_options import declared, drift
from test_public_surface import html_problems, tracking_hits

from sectorflow.basis import DATA_BASIS_SHORT, DEMO_MARK, DEMO_NOTICE
from sectorflow.wording import map_hits

REPO = Path(__file__).resolve().parent.parent
LANDING = (REPO / "site" / "index.html").read_text("utf-8")


def _build():
    spec = importlib.util.spec_from_file_location("_site_build", REPO / "site" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _build()


def _visible(page: str) -> str:
    """사람이 읽는 글 — 스타일·태그를 떼고 엔티티를 푼다(alt·aria·meta 설명도 읽힌다)."""
    body = re.sub(r"<style.*?</style>", " ", page, flags=re.S)
    attrs = " ".join(re.findall(r'(?:alt|aria-label|content|title)="([^"]*)"', body))
    return html.unescape(re.sub(r"<[^>]+>", " ", body) + " " + attrs)


def test_landing_is_marked_demo_and_passes_the_map_wording():
    """맨 위 띠와 꼬리에 데모 표지, 글은 금칙어·지도용 피할 말(유입·몰림·성지 …)을 통과한다."""
    assert LANDING.count(DEMO_NOTICE) >= 2 and DEMO_MARK in LANDING
    assert html_problems(LANDING) == []
    assert map_hits(_visible(LANDING)) == []


def test_landing_has_no_script_and_talks_only_to_known_hosts():
    """스크립트가 하나도 없다 — 분석·쿠키가 들어올 자리가 없다. 밖으로 가는 주소는 GitHub·글꼴뿐."""
    assert "<script" not in LANDING.lower() and tracking_hits(LANDING) == []
    assert not re.search(r"\son[a-z]+=", LANDING), "인라인 이벤트 처리기도 스크립트다"
    hosts = set(re.findall(r'(?:href|src|content)="https?://([^/"]+)', LANDING))
    assert hosts <= {"github.com", "younghwan91.github.io", "fonts.googleapis.com", "fonts.gstatic.com"}, hosts


def test_landing_carries_the_notices_and_the_sponsor_line():
    """고지(투자 조언 아님·예측 아님·각자 이용 조건·권리자 연락)와 후원 줄(혜택 없음·데이터 판매 아님) — 설계 §6.3."""
    text = _visible(LANDING)
    for must in ("투자 조언이 아닙니다", "예측이 아닙니다", "각자의 이용 조건", "권리자 연락", "후원해도 더 보이는 것은 없습니다",
                 "데이터를 팔지 않고", DATA_BASIS_SHORT):
        assert must in text, must
    for link in ("DISCLAIMER.md", "SECURITY.md", "github.com/sponsors/younghwan91", "/issues"):
        assert link in LANDING, link


def test_byo_commands_on_the_landing_exist():
    """'내 키로 보는 법' 의 명령을 그대로 쳤는데 argparse 가 거절하면 그 안내는 거짓말이다."""
    code = html.unescape("\n".join(re.findall(r"<pre><code>(.*?)</code></pre>", LANDING, re.S)))
    code = re.sub(r"<[^>]+>", " ", code)
    assert "scripts/sector_flow.py" in code and "scripts/sector_map.py" in code
    assert drift(code, REPO / "scripts") == []
    collect = declared(REPO / "src" / "sectorflow" / "collect" / "run.py")
    used = re.search(r"sector-flow-collect ([^\n]*)", code).group(1)
    for opt in re.findall(r"(?<![\w-])--[a-z][\w-]*", used):
        assert opt in collect, f"sector-flow-collect 에 {opt} 이 없다"
    scripts = (REPO / "pyproject.toml").read_text("utf-8")
    for exe in ("sw-flow", "sector-flow-collect"):
        assert f"\n{exe} = " in scripts, exe


# ── 빌드 — 합성 데모 폴더만 받는다 ─────────────────────────────────────────────────────────
def _demo(tmp_path: Path, *, marker=True, demo=True, sectors=("가상가", "가상나")) -> Path:
    d = tmp_path / "demo"
    d.mkdir()
    f = lambda **k: {a: list(v) for a, v in k.items()}  # noqa: E731
    flows = {s: f(indiv=[-10, -20], forgn=[6, 12], inst=[4, 8], etc=[0, 0]) for s in sectors}
    payload = {"dates": ["2026-01-05", "2026-01-06"], "sectors": list(sectors), "markets": ["거래소"],
               "flows": {"거래소": flows}, "demo": demo}
    (d / "payload.json").write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    if marker:
        (d / "DEMO.txt").write_text(DEMO_NOTICE, "utf-8")
    return d


def _images(tmp_path: Path) -> Path:
    im = tmp_path / "images"
    im.mkdir()
    for n in B.IMAGES:
        (im / n).write_bytes(b"fake-" + n.encode())
    return im


def test_build_makes_the_site_from_a_demo_folder(tmp_path):
    out = tmp_path / "_site"
    names = B.build(_demo(tmp_path), out, images=_images(tmp_path))
    days = ("2026-01-05", "2026-01-06")
    assert set(names) == {"index.html", "map.html", *B.IMAGES, B.PREVIEW.name, "d/index.html",
                          *(f"d/{d}/{n}" for d in days for n in ("index.html", "map.html"))}
    assert (out / "index.html").read_text("utf-8") == LANDING
    page = (out / "map.html").read_text("utf-8")
    assert "const D = {" in page and '"demo":true' in page.replace(" ", "")
    assert html_problems(page) == [] and tracking_hits(page) == []
    assert (out / "map-demo.gif").read_bytes() == b"fake-map-demo.gif"
    assert not list(tmp_path.glob("_site.building.*")), "임시 폴더가 남았다"


@pytest.mark.parametrize("kw, says", [
    ({"marker": False}, "DEMO.txt"),
    ({"demo": False}, '"demo": true'),
    ({"sectors": ("가상가", "운송/창고")}, "'가상' 으로 시작하지 않는 섹터 1개"),
], ids=["no-marker", "not-demo-payload", "real-sector-name"])
def test_build_refuses_anything_but_a_demo(tmp_path, capsys, kw, says):
    """실 리포트 폴더(표식 없음·demo 아님·실 업종명)로는 짓지 않는다 — 공개 경로에 실데이터가 나갈 길을 만들지 않는다(설계 §1)."""
    out = tmp_path / "_site"
    assert B.main(["--demo", str(_demo(tmp_path, **kw)), "--out", str(out)]) == 2
    assert says in capsys.readouterr().err
    assert not out.exists()


def test_build_will_not_wipe_a_folder_it_did_not_make(tmp_path):
    out = tmp_path / "mine"
    out.mkdir()
    (out / "notes.txt").write_text("keep", "utf-8")
    with pytest.raises(SystemExit, match="지우지 않는다"):
        B.build(_demo(tmp_path), out, images=_images(tmp_path))
    assert (out / "notes.txt").read_text("utf-8") == "keep"


def test_build_replaces_its_own_previous_output(tmp_path):
    out, images = tmp_path / "_site", _images(tmp_path)
    B.build(_demo(tmp_path), out, images=images)
    (out / "stale.html").write_text("old", "utf-8")
    B.build(tmp_path / "demo", out, images=images)
    assert not (out / "stale.html").exists() and (out / "index.html").exists()


def test_build_fails_on_a_dead_landing_link(tmp_path, monkeypatch):
    """랜딩이 가리키는 상대 경로가 사이트에 없으면 짓지 않는다 — 배포된 첫 화면의 영상·지도 링크가 404 가 된다."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(LANDING.replace('src="map-demo.gif"', 'src="map-demo.webm"'), "utf-8")
    monkeypatch.setattr(B, "SITE", site)
    with pytest.raises(SystemExit, match="map-demo.webm"):
        B.build(_demo(tmp_path), tmp_path / "_site", images=_images(tmp_path))


def test_local_refs_skip_external_and_in_page_links():
    page = ('<a href="map.html">x</a><a href="#byo">y</a><a href="https://github.com/x">z</a><a href="./">h</a>'
            '<link rel="icon" href="data:image/svg+xml,%3Csvg"><source srcset="a.png"><img src="b.gif">')
    assert B.local_refs(page) == ["a.png", "b.gif", "map.html"]
    assert set(B.local_refs(LANDING)) == {"map.html", "d/index.html", *B.IMAGES}
    assert B.local_refs('<a href="d/">x</a><a href="../">up</a>') == ["d/index.html"]


# ── 링크 미리보기·날짜 영구 링크 — 카카오톡·X·긱뉴스에 붙인 링크가 그림을 보인다 ───────────────────────────
def _metas(page: str) -> dict[str, str]:
    return dict(re.findall(r'<meta (?:property|name)="([^"]+)" content="([^"]*)"', page))


def _assert_card_meta(page: str, url: str) -> None:
    m = _metas(page)
    for k in ("og:title", "og:description", "og:image", "og:image:alt", "og:url", "twitter:title", "twitter:description",
              "twitter:image"):
        assert m.get(k), k
    assert m["twitter:card"] == "summary_large_image" and m["og:url"] == url
    assert m["og:image"] == m["twitter:image"] and m["og:image"].startswith("https://") and m["og:image"].endswith(".png")
    assert DEMO_MARK in m["og:description"] or DEMO_MARK in m["og:title"], "링크 미리보기에도 데모 표지"


def test_landing_link_preview_shows_the_map_picture():
    """링크 카드는 절대 주소의 PNG 를 본다(상대 경로는 카카오톡·X 가 못 읽는다) — 그 PNG 가 사이트에 실린다."""
    _assert_card_meta(LANDING, "https://younghwan91.github.io/sector-flow/")
    img = _metas(LANDING)["og:image"]
    assert img == "https://younghwan91.github.io/sector-flow/" + B.PREVIEW.name and B.PREVIEW.is_file()


def test_day_permalinks_carry_their_own_card_and_caption(tmp_path):
    """날짜마다 쪽 하나 — og:image 는 그날 카드(있으면), 글은 caption 초안, 지도는 그날까지 자른 것. 링크가 다 산다."""
    images = _images(tmp_path)
    (images / B.DAYS_DIR).mkdir()
    (images / B.DAYS_DIR / "2026-01-06.png").write_bytes(b"card")
    out = tmp_path / "_site"
    B.build(_demo(tmp_path), out, images=images)
    url = "https://younghwan91.github.io/sector-flow/d/2026-01-06/"
    page = (out / "d/2026-01-06/index.html").read_text("utf-8")
    _assert_card_meta(page, url)
    assert _metas(page)["og:image"] == url + "card.png" and (out / "d/2026-01-06/card.png").read_bytes() == b"card"
    old = (out / "d/2026-01-05/index.html").read_text("utf-8")
    assert _metas(old)["og:image"].endswith("/" + B.PREVIEW.name), "카드가 없는 날은 소셜 미리보기로"
    for p in (page, old, (out / "d/index.html").read_text("utf-8")):
        assert html_problems(p) == [] and tracking_hits(p) == [] and "<script" not in p
        assert map_hits(_visible(p)) == []
    m = json.loads(re.search(r"const D = (\{.*?\});\n", (out / "d/2026-01-05/map.html").read_text("utf-8"), re.S).group(1))
    assert m["asof"] == "2026-01-05" and m["demo"] is True
