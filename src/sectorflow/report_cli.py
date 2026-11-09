"""리포트 생성기 콘솔 진입점 — ``uv tool install .`` 로 설치해도 ``scripts/`` 가 따라오게.

리포트 생성기(``scripts/sector_flow.py`` 등)는 지금 레포의 ``scripts/`` 에 있고 wheel 은 ``src/sectorflow`` 만
담았다(BYO 타당성 문서 §2.7 "패키지 구성"). 파일을 옮기면 크론·테스트·다른 작업이 모두 경로를 바꿔야 하므로
옮기지 않는다. 대신 wheel 빌드가 그 파일들을 ``sectorflow/_scripts/`` 로 복사하고(``pyproject.toml`` 의
``force-include``), 여기 진입점이 설치본이면 그 사본을, 레포 체크아웃이면 ``scripts/`` 원본을 ``__main__`` 으로
돌린다. 템플릿은 스크립트 옆 ``templates/`` 를 ``__file__`` 기준으로 찾으므로 그대로 따라온다.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def script_path(name: str) -> Path:
    for base in (_HERE / "_scripts", _HERE.parents[1] / "scripts"):
        p = base / name
        if p.is_file():
            return p
    raise SystemExit(f"{name} 를 찾지 못했다 — 설치본이면 wheel 에 sectorflow/_scripts 가 있어야 한다")


def _run(name: str) -> None:
    path = script_path(name)
    sys.argv[0] = str(path)
    runpy.run_path(str(path), run_name="__main__")


def payload() -> None:
    """``sector-flow-payload`` = ``scripts/sector_flow.py`` (payload.json · viewer.html)."""
    _run("sector_flow.py")


def numbers() -> None:
    """``sector-flow-numbers`` = ``scripts/sector_numbers.py`` (numbers.html)."""
    _run("sector_numbers.py")


def gate() -> None:
    """``sector-flow-gate`` = ``scripts/completeness_gate.py``."""
    _run("completeness_gate.py")


def verify() -> None:
    """``sector-flow-verify`` = ``scripts/verify_report.py``."""
    _run("verify_report.py")
