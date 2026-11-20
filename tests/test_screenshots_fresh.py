"""README 스크린샷이 **지금 화면 코드**로 찍힌 것인가 — docs/SCREENSHOTS.md 원장 대조, 그리고 찍는 도구(scripts/demo_shots.py).

스크린샷은 커밋 사이에서 조용히 썩는다 — 화면 코드가 열 번 바뀌어도 PNG 는 그대로라
README 가 없는 화면을 보여준다(2026-11-08 촬영본이 열 번의 UI 머지를 지나 낡았다).
날짜 비교는 화면과 무관한 커밋에도 울어서 못 쓴다. 대신 원장이 "찍은 커밋" sha 를 적고,
여기서 그 sha 와 HEAD 의 **감시 경로 git 트리**를 비교한다 — 트리가 다르면 화면이
바뀐 것이고, 스크린샷을 다시 찍고 원장 sha 를 갱신해야 통과한다.

한계: HEAD 끼리의 비교다 — 커밋 안 한 작업 트리 수정은 못 본다(CI 는 커밋을 보니 충분하다).
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "docs" / "SCREENSHOTS.md"

#: | docs/images/….png | 감시 경로 | sha |
ROW = re.compile(r"^\|\s*(docs/images/\S+?\.png)\s*\|\s*(\S+?)/?\s*\|\s*([0-9a-f]{40})\s*\|",
                 re.MULTILINE)
README_IMG = re.compile(r"\]\((docs/images/[\w.-]+\.png)\)")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


def _rows() -> list[tuple[str, str, str]]:
    rows = ROW.findall(MANIFEST.read_text("utf-8"))
    assert rows, f"{MANIFEST} 에서 원장 행을 하나도 못 읽었다 — 표 형식이 바뀌었나"
    return rows


def _need_git() -> None:
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        pytest.skip("git 저장소가 아니다(배포 트리?) — 원장 대조는 git 트리가 필요하다")


def test_ui_change_after_manifest_sha_demands_retake():
    """감시 경로의 트리가 원장 sha 이후 바뀌었으면 '스크린샷 재촬영' 으로 실패한다."""
    _need_git()
    stale, broken = [], []
    for img, watched, sha in _rows():
        then = _git("rev-parse", f"{sha}:{watched}")
        now = _git("rev-parse", f"HEAD:{watched}")
        if then.returncode != 0 or now.returncode != 0:
            broken.append(f"{img}: {sha[:12]}:{watched} 또는 HEAD:{watched} 를 git 이 모른다 — "
                          f"{(then.stderr or now.stderr).strip()}")
        elif then.stdout.strip() != now.stdout.strip():
            stale.append(f"{img} — {watched} 가 {sha[:12]} 이후 바뀌었다")
    assert not broken, "원장을 대조할 수 없다:\n  " + "\n  ".join(broken)
    assert not stale, ("스크린샷 재촬영: 화면 코드가 바뀌었는데 스크린샷은 옛 코드다.\n  "
                       + "\n  ".join(stale)
                       + "\n  다시 찍고 docs/SCREENSHOTS.md 의 sha 를 갱신하라(찍는 법은 그 문서).")


def test_every_readme_image_is_in_the_manifest():
    """README 가 쓰는 docs/images/*.png 는 전부 원장에 있고, 원장의 파일도 실제로 있다."""
    used = set(README_IMG.findall((REPO / "README.md").read_text("utf-8")))
    assert used, "README 에서 docs/images 참조를 하나도 못 읽었다 — 패턴이 낡았나"
    listed = {img for img, _, _ in _rows()}
    missing = sorted(used - listed)
    assert not missing, ("README 가 쓰는 이미지가 docs/SCREENSHOTS.md 원장에 없다 — "
                         "원장 밖 이미지는 신선도 검사를 안 받는다: " + ", ".join(missing))
    gone = sorted(p for p in listed if not (REPO / p).is_file())
    assert not gone, "원장이 적은 이미지 파일이 없다: " + ", ".join(gone)


def _shots():
    spec = importlib.util.spec_from_file_location("_shots", REPO / "scripts" / "demo_shots.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_stamp_rewrites_every_manifest_row_and_only_those():
    """찍은 뒤 sha 를 다 고친다 — 한 줄이라도 남으면 그 그림만 옛 코드로 적힌다. 감시 경로는 표에서 읽는다."""
    S = _shots()
    text = MANIFEST.read_text("utf-8")
    new = S.stamp_manifest(text, "a" * 40)
    assert len(_rows()) == new.count("a" * 40) and new.split("|---")[0] == text.split("|---")[0]
    assert set(S.watched(text)) == {p.rstrip("/") for _i, p, _s in _rows()}
    assert {"src/sectorflow/demo.py", "src/sectorflow/tui", "scripts/templates"} <= set(S.watched(text))


def test_ansi_to_html_keeps_color_width_and_trims_only_when_asked():
    S = _shots()
    raw = "\x1b[38;5;196m빨강\x1b[0m a\n\x1b[1m굵게\x1b[22m\n\n"
    page = S.ansi_html(raw)
    assert 'color:#ff0000' in page and "font-weight:700" in page
    assert '<span class="g2">빨</span>' in page, "한글은 2칸 격자에 넣어야 표 열이 안 어긋난다"
    assert page.count('class="r"') == 3
    assert S.ansi_html(raw, trim=True).count('class="r"') == 2
