#!/usr/bin/env python3
"""날짜 영구 링크의 공유 카드를 **합성 데모**로 찍는다 — ``docs/images/days/<날짜>.png``(1200×675, 링크 미리보기 ``og:image``).

    uv run --with 'playwright==1.48.0' --with pillow python scripts/day_cards.py    # 카드 + docs/SCREENSHOTS.md 의 이 표 sha

데모의 마지막 :data:`sectorflow.caption.PERMALINK_DAYS` 거래일마다 그날까지 자른 페이로드(``caption.upto``)로 지도를 짓고,
배치의 공유 카드와 같은 길(``scripts/map_card.py`` 의 ``?card=wide``)로 찍는다. 랜딩 빌드(``site/build.py``)가 바이트 그대로 싣는다.
``demo_shots.py`` 처럼 감시 경로가 커밋된 뒤에만 찍고 원장의 ``docs/images/days/`` 줄을 HEAD 로 고친다(``demo_shots.py`` 는
이 줄을 건드리지 않는다). 실데이터 카드는 배치가 리포트 폴더 안에 짓는다(``map_wide.png``) — 공개하지 않는다.

종료코드: 0 찍었다, 1 못 찍었다, 2 Playwright·Chromium·Pillow 가 없거나 감시 경로에 커밋 안 한 변경이 있다.
"""
from __future__ import annotations

import argparse
import functools
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from sectorflow import caption  # noqa: E402

OUT = REPO / "docs" / "images" / "days"
MANIFEST = REPO / "docs" / "SCREENSHOTS.md"
#: 카드 색 수 — 지도는 띠 그라데이션이라 128색(``demo_shots`` 의 지도와 같다).
COLORS = 128


@functools.cache
def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_day_cards_{name}", REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True).stdout.strip()


def days(payload: dict) -> list[str]:
    return payload["dates"][-caption.PERMALINK_DAYS:]


def shoot(payload: dict, out: Path) -> list[Path]:
    """날마다 지도를 짓고 넓은 카드를 찍어 ``out/<날짜>.png`` 로 — 쓴 파일들."""
    from PIL import Image  # noqa: PLC0415 — 찍을 때만

    sm, card = _load("sector_map"), _load("map_card")
    template = Path(sm.TEMPLATE).read_text("utf-8")
    out.mkdir(parents=True, exist_ok=True)
    done = []
    tmp = Path(tempfile.mkdtemp(prefix="day-cards."))
    try:
        for day in days(payload):
            d = tmp / day
            d.mkdir()
            (d / "map.html").write_text(sm.render(sm.build_map_data(caption.upto(payload, day)), template), "utf-8")
            card.shoot(d / "map.html", d)
            target = out / f"{day}.png"
            Image.open(d / "map_wide.png").convert("RGB").quantize(colors=COLORS, method=Image.MEDIANCUT).save(
                target, optimize=True)
            print(f"{target.relative_to(REPO)}: {target.stat().st_size:,} bytes")
            done.append(target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return done


def manifest_rows(names: list[str], watched: list[str], sha: str) -> str:
    return "\n".join(f"| docs/images/days/{n} | {w} | {sha} |" for n in names for w in watched)


#: 카드가 기대는 코드 — 바뀌면 ``tests/test_screenshots_fresh.py`` 가 재촬영을 요구한다.
WATCHED = ("scripts/day_cards.py", "scripts/map_card.py", "scripts/templates/sector_map.html", "scripts/sector_map.py",
           "src/sectorflow/flowmap.py", "src/sectorflow/caption.py", "src/sectorflow/demo.py")
_TABLE = re.compile(r"(<!-- day-cards -->\n\| 이미지 \| 감시 경로 \| 찍은 커밋 \|\n\|---\|---\|---\|\n)(?:\|[^\n]*\n)*")


def stamp(text: str, names: list[str], sha: str) -> str:
    """원장의 날짜 카드 표를 지금 찍은 파일들 × :data:`WATCHED` 로 다시 쓴다."""
    new, n = _TABLE.subn(lambda m: m.group(1) + manifest_rows(names, list(WATCHED), sha) + "\n", text)
    if n != 1:
        raise SystemExit("docs/SCREENSHOTS.md 에서 날짜 카드 표(<!-- day-cards -->)를 못 찾았다")
    return new


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--demo-dir", help="이미 만든 합성 데모 폴더 — 없으면 기본 데모 폴더(sectorflow.demo.ensure)")
    a = ap.parse_args(argv)
    dirty = _git("status", "--porcelain", "--", *WATCHED)
    if dirty:
        print(f"감시 경로에 커밋 안 한 변경이 있다 — 카드가 HEAD 의 화면이 아니게 된다. 먼저 커밋하라:\n{dirty}", file=sys.stderr)
        return 2
    try:
        import PIL  # noqa: F401, PLC0415
    except ImportError:
        print("Pillow 가 없다 — uv run --with pillow 로 돌려라", file=sys.stderr)
        return 2
    from sectorflow import demo  # noqa: PLC0415 — pandas 등이 데모를 지을 때만 필요하다

    report = Path(a.demo_dir or demo.ensure())
    payload = json.loads((report / "payload.json").read_text("utf-8"))
    if payload.get("demo") is not True:
        print("합성 데모 페이로드가 아니다 — 공개 카드는 데모로만 찍는다", file=sys.stderr)
        return 2
    card = _load("map_card")
    try:
        for old in OUT.glob("*.png"):
            old.unlink()
        done = shoot(payload, OUT)
    except card.NoBrowser as e:
        print(f"찍지 않았다 — {e}", file=sys.stderr)
        return 2
    except card.CardError as e:
        print(f"찍지 않았다 — {e}", file=sys.stderr)
        return 1
    MANIFEST.write_text(stamp(MANIFEST.read_text("utf-8"), [p.name for p in done], _git("rev-parse", "HEAD")), "utf-8")
    print(f"wrote {MANIFEST.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
