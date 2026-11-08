"""wheel 이 리포트 생성기를 싣는가 — ``uv tool install .`` 한 사용자가 리포트를 만들 수 있어야 한다.

wheel 빌드 자체는 네트워크(hatchling)가 필요할 수 있어 여기서 돌리지 않는다. 대신 선언끼리 맞물리는지 본다:
콘솔 진입점이 가리키는 함수가 있다 · 그 함수가 돌리는 스크립트가 wheel 복사 목록(force-include)에 있다 ·
복사 원본이 실제로 있다 · 레포 체크아웃에서 진입점이 그 스크립트를 찾는다.
"""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = tomllib.loads((REPO / "pyproject.toml").read_text("utf-8"))
FORCE = PY["tool"]["hatch"]["build"]["targets"]["wheel"].get("force-include", {})


def _runs(fn_name: str) -> str:
    """``report_cli.<fn>`` 이 ``_run("<파일>")`` 으로 넘기는 파일 이름."""
    tree = ast.parse((REPO / "src/sectorflow/report_cli.py").read_text("utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fn_name)
    call = next(n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_run")
    return call.args[0].value


def test_every_console_script_resolves():
    for name, target in PY["project"]["scripts"].items():
        mod, fn = target.split(":")
        assert callable(getattr(importlib.import_module(mod), fn)), name


def test_report_entry_points_ship_their_script_and_templates():
    shipped = {Path(dst).name for dst in FORCE.values() if dst.startswith("sectorflow/_scripts/")}
    for name, target in PY["project"]["scripts"].items():
        mod, fn = target.split(":")
        if mod == "sectorflow.report_cli":
            assert _runs(fn) in shipped, f"{name}: {_runs(fn)} 가 wheel 에 안 실린다(force-include)"
    assert "templates" in shipped, "템플릿 없이는 HTML 을 못 만든다"
    for src in FORCE:
        assert (REPO / src).exists(), f"force-include 원본이 없다: {src}"


def test_entry_point_runs_the_repo_script_in_a_checkout():
    out = subprocess.run([sys.executable, "-c", "from sectorflow.report_cli import payload; payload()",
                          "--help"], capture_output=True, text=True, cwd=REPO / "tests")
    assert out.returncode == 0 and "--from-json" in out.stdout
