#!/usr/bin/env python3
"""공개 랜딩(GitHub Pages)을 짓는다 — 합성 데모 폴더 → ``_site/``(랜딩·데모 지도·데모 그림). 생성물은 커밋하지 않는다.

    uv run python site/build.py                              # 데모를 임시로 지어 _site/ 에
    uv run python site/build.py --demo <데모 폴더> --out _site  # 이미 지은 데모(sector-flow-demo --out)

공개 사이트에는 **합성 데모만** 나간다(설계 §4·§6). 그래서 실데이터 폴더는 받지 않는다 — 폴더의 표식(``DEMO.txt``),
페이로드의 ``"demo": true``, 섹터 이름이 전부 ``가상`` 으로 시작하는 것, 셋 다여야 짓는다. 짓고 나서는
``tests/test_public_surface.py`` 가 ``_site/`` 도 본다(Pages 워크플로가 배포 전에 돌린다).

들어가는 것: ``site/index.html`` 그대로, 데모 페이로드로 지은 ``map.html``(``scripts/sector_map.py``), README 와 같은
데모 그림 ``docs/images/map-demo.{gif,png}``(원장 ``docs/SCREENSHOTS.md`` 의 것을 바이트 그대로). 랜딩이 가리키는
상대 경로가 다 있는지도 본다.

종료코드: 0 지음, 2 데모가 아니거나 랜딩의 링크가 깨짐(아무것도 안 남김).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SITE = REPO / "site"
sys.path.insert(0, str(REPO / "src"))

#: 랜딩에 그대로 싣는 데모 그림 — README 가 쓰는 것과 같은 파일(스크린샷 원장이 데모로 찍었다고 적는다).
IMAGES = ("map-demo.gif", "map-demo.png")
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
    """HTML 이 가리키는 상대 경로(외부 주소·같은 쪽 안 링크·``./`` 는 뺀다)."""
    return sorted({r for r in _LOCAL_REF.findall(html) if r not in ("./", ".") and not r.startswith("/")})


def _sector_map():
    spec = importlib.util.spec_from_file_location("_site_sector_map", REPO / "scripts" / "sector_map.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build(demo_dir: Path, out: Path, *, images: Path = REPO / "docs" / "images") -> list[str]:
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
    missing = [r for r in local_refs(landing) if r not in files]
    if missing:
        raise SystemExit(f"랜딩이 가리키는데 사이트에 없는 파일: {missing}")
    stage = Path(tempfile.mkdtemp(prefix=out.name + ".building.", dir=out.parent if out.parent.exists() else None))
    try:
        for name, body in files.items():
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
