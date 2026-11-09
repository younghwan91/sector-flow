"""콘솔 진입점이 **제 스크립트**를 돌리는가 — ``sector-flow-verify`` 가 관문을 돌려도 ``test_packaging`` 은 초록이었다
(거기서는 그 파일이 wheel 에 실리는지만 본다)."""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = tomllib.loads((REPO / "pyproject.toml").read_text("utf-8"))["project"]["scripts"]

#: 진입점 → 그 스크립트만 가진 옵션. 선언이지 구현이 아니다 — 이름과 일이 이어져 있는지 본다.
ROLE = {"sector-flow-payload": "--from-json", "sector-flow-numbers": "--payload",
        "sector-flow-gate": "--asof", "sector-flow-verify": "--db-check"}


def test_table_covers_every_report_entry_point():
    assert {n for n, t in SCRIPTS.items() if t.startswith("sectorflow.report_cli:")} == set(ROLE)


@pytest.mark.parametrize("name,flag", sorted(ROLE.items()))
def test_entry_point_runs_its_own_script(name, flag):
    fn = SCRIPTS[name].split(":")[1]
    out = subprocess.run([sys.executable, "-c", f"from sectorflow.report_cli import {fn}; {fn}()", "--help"],
                         capture_output=True, text=True, cwd=REPO / "tests", timeout=120)
    assert out.returncode == 0 and flag in out.stdout, f"{name} → {out.stdout[:400]}{out.stderr[-400:]}"
