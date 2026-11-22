"""``sector-flow-byo`` — 자기 키로 채운 sqlite 에서 수급 지도까지 한 명령(``docs/byo/quickstart.md``).

    sector-flow-byo map --db data/byo.db --out data/map

순서: 수급만 페이로드(``sector_flow.py --flows-only`` → ``payload.json``) → 지도(``sector_map.py`` → ``map.html``) →
(카드 생성기 ``map_card.py`` 와 Playwright 가 있으면) 공유 카드 PNG. 카드는 곁가지다 — 없거나 실패해도 지도는 그대로다.
TimescaleDB·네트워크 없이 끝난다(지도 페이지는 설치본에서 원격 글꼴을 떼어 낸 사본이다).

지도 전용 수집(``sector-flow-collect backfill --map-only``)은 수익률·시총이 없어 수치표·터미널 화면은 짓지 않는다.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from sectorflow.report_cli import script_path

#: 지도 페이로드의 기본 거래일 수 — ``sector_flow.py`` 기본과 같다. 저장된 날이 적으면 있는 만큼만 싣는다.
DEFAULT_DAYS = 260
#: 공유 카드 생성기(설계 Task 27, ``map_card.py --dir <폴더>`` → ``map.png``). 아직 없는 설치본도 있다.
CARD_SCRIPT = "map_card.py"


def _script(name: str) -> "Path | None":
    try:
        return script_path(name)
    except SystemExit:
        return None


def _has_playwright() -> bool:
    return importlib.util.find_spec("playwright") is not None


def _step(name: str, *args: str) -> int:
    path = _script(name)
    if path is None:
        print(f"⏹ {name} 를 찾지 못했다 — 설치본이면 다시 설치한다(uv tool install --force …)", file=sys.stderr)
        return 1
    return subprocess.run([sys.executable, str(path), *args]).returncode


def card(out: Path) -> str:
    """공유 카드 — 생성기와 Playwright 가 다 있을 때만. 돌려주는 것은 한 줄 설명(실패해도 예외를 올리지 않는다)."""
    path = _script(CARD_SCRIPT)
    if path is None:
        return "카드: 이 설치본에 카드 생성기가 없다 — 건너뛴다"
    if not _has_playwright():
        return "카드: Playwright 가 없다 — 건너뛴다(지도는 그대로). 카드까지: uv tool install --with playwright …"
    rc = subprocess.run([sys.executable, str(path), "--dir", str(out)]).returncode
    return f"카드: {out / 'map.png'}" if rc == 0 else f"카드: 생성기가 {rc} 로 끝났다 — 지도는 그대로다"


def _guard_out(out: Path) -> None:
    """``--out`` 에 수급만이 아닌 리포트(배치·데모 폴더)가 있으면 덮지 않는다."""
    p = out / "payload.json"
    if not p.exists():
        return
    try:
        flows_only = bool(json.loads(p.read_text("utf-8")).get("flows_only"))
    except (OSError, ValueError, AttributeError):
        flows_only = False
    if not flows_only:
        raise SystemExit(f"{out} 에 수급만이 아닌 payload.json 이 있다 — 리포트 폴더를 덮지 않는다. 다른 --out 을 준다.")


def cmd_map(a) -> int:
    out = Path(a.out).expanduser().resolve()
    db = Path(a.db).expanduser()
    if not db.is_file():
        raise SystemExit(f"{db} 가 없다 — 먼저 sector-flow-collect universe·backfill --map-only 로 채운다")
    _guard_out(out)
    out.mkdir(parents=True, exist_ok=True)
    payload, html = out / "payload.json", out / "map.html"
    extra = ["--asof", a.asof] if a.asof else []
    rc = _step("sector_flow.py", "--db", str(db), "--days", str(a.days), *extra, "--flows-only", "--json", str(payload))
    if rc != 0:
        print(f"⏹ 페이로드를 못 지었다({rc}) — 위 메시지를 본다", file=sys.stderr)
        return rc
    rc = _step("sector_map.py", "--payload", str(payload), "--html", str(html))
    if rc != 0:
        print(f"⏹ 지도를 못 지었다({rc})", file=sys.stderr)
        return rc
    print(card(out) if not a.no_card else "카드: --no-card — 건너뛴다")
    print(f"✅ 브라우저로 연다: {html.as_uri()}")
    return 0


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(prog="sector-flow-byo", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    mp = sub.add_parser("map", help="sqlite → payload.json(수급만) → map.html (→ Playwright 가 있으면 카드)")
    mp.add_argument("--db", required=True, help="sector-flow-collect 가 채운 sqlite")
    mp.add_argument("--out", required=True, help="쓸 폴더(없으면 만든다)")
    mp.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"싣는 거래일 수(기본 {DEFAULT_DAYS} — 저장된 날이 적으면 있는 만큼)")
    mp.add_argument("--asof", help="이 기준일(YYYY-MM-DD)까지로 — 기본은 저장된 마지막 날")
    mp.add_argument("--no-card", action="store_true", help="공유 카드를 만들지 않는다")
    a = ap.parse_args(argv)
    return {"map": cmd_map}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
