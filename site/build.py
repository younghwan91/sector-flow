#!/usr/bin/env python3
"""공개 랜딩(GitHub Pages)을 짓는다 — 합성 데모 폴더 → ``_site/``(랜딩·데모 지도·데모 그림). 생성물은 커밋하지 않는다.

    uv run python site/build.py                              # 데모를 임시로 지어 _site/ 에
    uv run python site/build.py --demo <데모 폴더> --out _site  # 이미 지은 데모(sector-flow-demo --out)

공개 사이트에는 **합성 데모만** 나간다(설계 §4·§6). 그래서 실데이터 폴더는 받지 않는다 — 폴더의 표식(``DEMO.txt``),
페이로드의 ``"demo": true``, 섹터 이름이 전부 ``가상`` 으로 시작하는 것, 셋 다여야 짓는다. 짓고 나서는
``tests/test_public_surface.py`` 가 ``_site/`` 도 본다(Pages 워크플로가 배포 전에 돌린다).

들어가는 것: ``site/index.html`` 그대로, 데모 페이로드로 지은 ``map.html``(``scripts/sector_map.py``), README 와 같은
데모 그림 ``docs/images/map-demo.{gif,png}``·링크 미리보기 ``docs/brand/social-preview.png``(원장 ``docs/SCREENSHOTS.md`` 의 것을
바이트 그대로). 그리고 날짜 영구 링크 ``d/<날짜>/`` — 데모의 마지막 :data:`sectorflow.caption.PERMALINK_DAYS` 거래일마다 그날까지
자른 지도(``map.html``), 게시용 글 초안(``sectorflow.caption``), ``og:image`` 가 그날 지도 카드(``docs/images/days/<날짜>.png``,
``scripts/day_cards.py`` 가 찍어 원장에 적는다 — 없으면 소셜 미리보기)인 쪽, 목록 ``d/``. 랜딩이 가리키는 상대 경로가 다 있는지도 본다.

종료코드: 0 지음, 2 데모가 아니거나 랜딩의 링크가 깨짐(아무것도 안 남김).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from html import escape
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SITE = REPO / "site"
sys.path.insert(0, str(REPO / "src"))

#: 랜딩에 그대로 싣는 데모 그림 — README 가 쓰는 것과 같은 파일(스크린샷 원장이 데모로 찍었다고 적는다).
IMAGES = ("map-demo.gif", "map-demo.png")
#: 링크 미리보기(og:image) — 1280×640, ``scripts/social_preview.py`` 가 찍어 원장에 적는다.
PREVIEW = REPO / "docs" / "brand" / "social-preview.png"
#: 날짜 카드 폴더 이름(``docs/images/days/<날짜>.png``) — ``scripts/day_cards.py``.
DAYS_DIR = "days"
#: 데모 섹터 이름의 머리(``sectorflow.demo.SECTORS``·``THIN_SECTOR``). 실제처럼 보이는 가짜 이름은 실데이터로 오인된다.
DEMO_PREFIX = "가상"
#: 데모 폴더 표식 — ``sectorflow.demo.MARKER``.
MARKER = "DEMO.txt"
#: 이 도구가 지은 폴더라는 표식 — 이것이 없는 비어 있지 않은 폴더는 지우지 않는다(``--out`` 을 잘못 줘도 남의 폴더를 안 덮는다).
BUILT = ".sector-flow-site"
_LOCAL_REF = re.compile(r"""(?:href|src|srcset)="([^"#:]+?)(?:#[^"]*)?\"""")


def demo_problems(folder: Path) -> list[str]:
    """공개해도 되는 합성 데모 폴더가 아닌 까닭들 — 비면 통과."""
    out = []
    if not (folder / MARKER).is_file():
        out.append(f"{MARKER} 표식이 없다 — sector-flow-demo 가 지은 폴더가 아니다")
    try:
        payload = json.loads((folder / "payload.json").read_text("utf-8"))
    except (OSError, ValueError) as e:
        return out + [f"payload.json 을 못 읽었다({type(e).__name__})"]
    if payload.get("demo") is not True:
        out.append('payload.json 에 "demo": true 가 없다 — 실데이터 리포트다')
    real = sorted(s for s in payload.get("sectors") or [] if not str(s).startswith(DEMO_PREFIX))
    if not payload.get("sectors"):
        out.append("payload.json 에 섹터가 없다")
    if real:
        out.append(f"'{DEMO_PREFIX}' 으로 시작하지 않는 섹터 {len(real)}개 — 실데이터 이름일 수 있다")
    return out


def local_refs(html: str) -> list[str]:
    """HTML 이 가리키는 상대 경로(외부 주소·같은 쪽 안 링크·``./`` 는 뺀다). 폴더(``d/``)는 그 ``index.html`` 로 센다."""
    return sorted({r + "index.html" if r.endswith("/") else r for r in _LOCAL_REF.findall(html)
                   if r not in ("./", ".") and not r.startswith(("/", "../"))})


def _caption():
    sys.path.insert(0, str(REPO / "src"))
    from sectorflow import caption  # noqa: PLC0415

    return caption


#: 날짜 쪽의 겉모양 — 랜딩의 색·글꼴 변수를 줄여 옮겼다(스크립트 없음).
_DAY_CSS = """:root{color-scheme:dark;--bg:#0a0d16;--panel:#0f1424;--ink:#eef1f8;--muted:#8b93a8;--rule:#262c3b;--demo:#f3b44b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.7 -apple-system,"Apple SD Gothic Neo",
"Noto Sans KR",sans-serif;word-break:keep-all;overflow-wrap:anywhere}a{color:inherit}
.notice{background:#2a2213;color:var(--demo);font-size:13.5px;text-align:center;padding:7px 14px}
main{max-width:1240px;margin:0 auto;padding:22px max(4vw,18px) 48px}h1{font-size:clamp(22px,3.4vw,32px);margin:8px 0 14px}
img{display:block;width:100%;height:auto;border:1px solid var(--rule);border-radius:12px}
pre{white-space:pre-wrap;background:var(--panel);border:1px solid var(--rule);border-radius:10px;padding:14px 16px;font-size:14.5px}
nav{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:14.5px;color:var(--muted)}ul{padding-left:1.2em}
footer{margin-top:28px;color:var(--muted);font-size:13.5px}"""


def _meta(title: str, desc: str, url: str, image: str, alt: str, w: int, h: int) -> str:
    t, d, a = escape(title), escape(desc), escape(alt)
    return (f'<meta name="description" content="{d}">\n<meta property="og:type" content="website">\n'
            f'<meta property="og:site_name" content="sector-flow">\n<meta property="og:locale" content="ko_KR">\n'
            f'<meta property="og:url" content="{url}">\n<meta property="og:title" content="{t}">\n'
            f'<meta property="og:description" content="{d}">\n<meta property="og:image" content="{image}">\n'
            f'<meta property="og:image:width" content="{w}">\n<meta property="og:image:height" content="{h}">\n'
            f'<meta property="og:image:alt" content="{a}">\n<meta name="twitter:card" content="summary_large_image">\n'
            f'<meta name="twitter:title" content="{t}">\n<meta name="twitter:description" content="{d}">\n'
            f'<meta name="twitter:image" content="{image}">\n<meta name="twitter:image:alt" content="{a}">\n'
            f'<link rel="canonical" href="{url}">')


def _page(head: str, body: str, title: str) -> str:
    from sectorflow.basis import DEMO_MARK, DEMO_NOTICE  # noqa: PLC0415

    return (f'<!DOCTYPE html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>{escape(title)}</title>\n{head}\n'
            f'<style>{_DAY_CSS}</style>\n</head>\n<body>\n<div class="notice" role="note"><b>{DEMO_MARK}</b> {DEMO_NOTICE}</div>\n'
            f'<main>\n{body}\n<footer><p>{DEMO_NOTICE}</p><p>숫자는 같은 기간에 관측한 값이고 앞날의 예측이 아닙니다. 투자 조언이 아닙니다 — '
            f'<a href="https://github.com/younghwan91/sector-flow/blob/main/DISCLAIMER.md">고지</a> · '
            f'<a href="https://github.com/younghwan91/sector-flow">GitHub</a></p></footer>\n</main>\n</body>\n</html>\n')


def day_pages(payload: dict, images: Path, sm) -> dict[str, bytes]:
    """날짜 영구 링크 ``d/<날짜>/{index.html,map.html,card.png}`` 와 목록 ``d/index.html`` — 데모의 마지막 몇 거래일."""
    cap = _caption()
    days = payload["dates"][-cap.PERMALINK_DAYS:]
    template = Path(sm.TEMPLATE).read_text("utf-8")
    files: dict[str, bytes] = {}
    for day in days:
        P = cap.upto(payload, day)
        url = cap.permalink(day)
        text = cap.caption(P, url=url, today=False)
        card = images / DAYS_DIR / f"{day}.png"
        if card.is_file():
            files[f"d/{day}/card.png"] = card.read_bytes()
            image, w, h = url + "card.png", 1200, 675
        else:
            image, w, h = cap.SITE + PREVIEW.name, 1280, 640
        title = f"{day} 섹터 수급 지도 [합성 데모] · sector-flow"
        desc = " ".join(text.splitlines()[1:3])
        alt = f"{day} 섹터 수급 지도 카드(합성 데모) — 왼쪽 순매도 주체, 가운데 섹터, 오른쪽 순매수 주체"
        others = " · ".join(f'<a href="../{d}/">{d}</a>' if d != day else f"<b>{d}</b>" for d in days)
        pic = (f'<a href="map.html"><img src="card.png" width="1200" height="675" alt="{escape(alt)}"></a>'
               if f"d/{day}/card.png" in files else "")
        body = (f'<nav><a href="../../">sector-flow</a><a href="../">날짜별 지도</a><a href="map.html">이날 지도 열기 →</a></nav>\n'
                f"<h1>{escape(day)} 섹터 수급 지도</h1>\n{pic}\n"
                f"<h2>게시용 글 초안</h2>\n<pre>{escape(text)}</pre>\n<p>다른 날: {others}</p>")
        files[f"d/{day}/index.html"] = _page(_meta(title, desc, url, image, alt, w, h), body, title).encode("utf-8")
        files[f"d/{day}/map.html"] = sm.render(sm.build_map_data(P), template).encode("utf-8")
    items = "\n".join(f'<li><a href="{d}/">{d}</a></li>' for d in reversed(days))
    title = "날짜별 섹터 수급 지도 [합성 데모] · sector-flow"
    desc = "하루마다 한 장 — 그날 누가 어느 섹터를 넘기고 넘겨받았는지. 이 사이트의 날짜·숫자는 합성 데모입니다."
    head = _meta(title, desc, cap.SITE + "d/", cap.SITE + PREVIEW.name, "sector-flow 수급 지도(합성 데모)", 1280, 640)
    body = (f'<nav><a href="../">sector-flow</a></nav>\n<h1>날짜별 섹터 수급 지도</h1>\n'
            f"<p>{escape(desc)} 날짜마다 그날까지 자른 지도와 공유용 카드가 있습니다.</p>\n<ul>\n{items}\n</ul>")
    files["d/index.html"] = _page(head, body, title).encode("utf-8")
    return files


def _sector_map():
    spec = importlib.util.spec_from_file_location("_site_sector_map", REPO / "scripts" / "sector_map.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build(demo_dir: Path, out: Path, *, images: Path = REPO / "docs" / "images", preview: Path = PREVIEW) -> list[str]:
    """``out`` 을 새로 짓는다 → 쓴 파일 이름. 문제가 있으면 :class:`SystemExit`(코드 2) — 그때 ``out`` 은 손대지 않았다."""
    bad = demo_problems(demo_dir)
    if bad:
        raise SystemExit("공개 사이트는 합성 데모로만 짓는다 — " + "; ".join(bad))
    if out.exists() and any(out.iterdir()) and not (out / BUILT).exists():
        raise SystemExit(f"{out} 는 비어 있지 않고 이 도구가 지은 폴더도 아니다 — 지우지 않는다. 다른 --out 을 주어라.")
    landing = (SITE / "index.html").read_text("utf-8")
    sm = _sector_map()
    payload = json.loads((demo_dir / "payload.json").read_text("utf-8"))
    files = {"index.html": landing.encode("utf-8"),
             "map.html": sm.render(sm.build_map_data(payload), Path(sm.TEMPLATE).read_text("utf-8")).encode("utf-8")}
    for name in IMAGES:
        files[name] = (images / name).read_bytes()
    files[PREVIEW.name] = preview.read_bytes()
    files.update(day_pages(payload, images, sm))
    missing = [r for r in local_refs(landing) if r not in files]
    if missing:
        raise SystemExit(f"랜딩이 가리키는데 사이트에 없는 파일: {missing}")
    for name in [n for n in files if n.startswith("d/") and n.endswith("index.html")]:
        base = name.rsplit("/", 1)[0]
        dead = []
        for r in local_refs(files[name].decode("utf-8")) + [r for r in re.findall(r'href="(\.\./[^"]*)"', files[name].decode())]:
            path = os.path.normpath(os.path.join(base, r + ("index.html" if r.endswith("/") else "")))
            if path not in files:
                dead.append(r)
        if dead:
            raise SystemExit(f"{name} 가 가리키는데 사이트에 없는 파일: {dead}")
    stage = Path(tempfile.mkdtemp(prefix=out.name + ".building.", dir=out.parent if out.parent.exists() else None))
    try:
        for name, body in files.items():
            (stage / name).parent.mkdir(parents=True, exist_ok=True)
            (stage / name).write_bytes(body)
        (stage / BUILT).write_text("site/build.py 가 지은 폴더 — 다시 지으면 통째로 바뀐다\n", "utf-8")
        stage.chmod(0o755)
        if out.exists():
            shutil.rmtree(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stage), str(out))
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return sorted(files)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--demo", help="sector-flow-demo --out 으로 지은 합성 데모 폴더(없으면 임시로 짓는다)")
    ap.add_argument("--out", default=str(REPO / "_site"), help="지을 폴더(기본: 저장소의 _site/, gitignore)")
    a = ap.parse_args(argv)
    keep = None
    try:
        if a.demo:
            demo_dir = Path(a.demo)
        else:
            from sectorflow import demo  # noqa: PLC0415 — pandas 등이 데모를 지을 때만 필요하다

            keep = tempfile.mkdtemp(prefix="sf-site.")
            demo_dir = Path(demo.ensure(Path(keep) / "demo"))
        try:
            names = build(demo_dir, Path(a.out))
        except SystemExit as e:
            print(f"site/build.py: {e}", file=sys.stderr)
            return 2
    finally:
        if keep:
            shutil.rmtree(keep, ignore_errors=True)
    print(f"wrote {a.out}: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
