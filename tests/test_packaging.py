"""wheel 이 리포트 생성기를 싣는가 — ``uv tool install .`` 한 사용자가 리포트를 만들 수 있어야 한다.

wheel 빌드 자체는 네트워크(hatchling)가 필요할 수 있어 여기서 돌리지 않는다. 대신 선언끼리 맞물리는지 본다:
콘솔 진입점이 가리키는 함수가 있다 · 그 함수가 돌리는 스크립트가 wheel 복사 목록(force-include)에 있다 ·
복사 원본이 실제로 있다 · 레포 체크아웃에서 진입점이 그 스크립트를 찾는다.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from sectorflow import offline_templates as T

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
    for src in FORCE:
        assert (REPO / src).is_file(), f"force-include 는 파일 하나씩이다 — 없거나 폴더다: {src}"
    # 템플릿은 빌드 훅이 원격 글꼴을 떼고 파일 하나씩 싣는다 — 목록이 레포 템플릿 전부를 덮어야 한다.
    hook = PY["tool"]["hatch"]["build"]["targets"]["wheel"]["hooks"]["custom"]
    assert (REPO / hook["path"]).is_file()
    assert sorted(T.TEMPLATES) == sorted(p.name for p in (REPO / "scripts/templates").glob("*.html"))


def test_hook_ships_offline_templates(tmp_path):
    """훅을 hatchling 없이 흉내 내 돌린다 — 싣는 사본에 자동으로 불러오는 원격 자원이 없어야 한다."""
    import types
    sys.modules.setdefault("hatchling", types.ModuleType("hatchling"))
    for m in ("hatchling.builders", "hatchling.builders.hooks", "hatchling.builders.hooks.plugin"):
        sys.modules.setdefault(m, types.ModuleType(m))
    iface = types.ModuleType("hatchling.builders.hooks.plugin.interface")
    iface.BuildHookInterface = type("BuildHookInterface", (), {})
    sys.modules.setdefault("hatchling.builders.hooks.plugin.interface", iface)
    spec = importlib.util.spec_from_file_location("_hb", REPO / "hatch_build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    hook = mod.OfflineTemplates.__new__(mod.OfflineTemplates)
    hook.root, hook.target_name = str(REPO), "wheel"
    data: dict = {"force_include": {}}
    hook.initialize("standard", data)
    assert sorted(data["force_include"].values()) == sorted(
        f"sectorflow/_scripts/templates/{n}" for n in T.TEMPLATES)
    for src in data["force_include"]:
        html = Path(src).read_text("utf-8")
        assert not T.AUTO_FETCH.search(html), src
        assert "googleapis" not in html and "gstatic" not in html
    editable: dict = {"force_include": {}}
    hook.initialize("editable", editable)
    assert editable["force_include"] == {}


def test_strip_remote_keeps_clickable_links():
    html = ('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
            '<style>@import url("https://x.example/a.css");</style>'
            '<a href="https://github.com/x">문서</a><svg xmlns="http://www.w3.org/2000/svg"></svg>')
    out = T.strip_remote(html)
    assert not T.AUTO_FETCH.search(out)
    assert '<a href="https://github.com/x">' in out and "w3.org/2000/svg" in out


def test_shipped_code_talks_only_to_kiwoom_and_dart():
    """설계 §4.2 — 네트워크 목적지는 키움과 (선택) OpenDART 둘뿐. 중계·공유 캐시·텔레메트리 없음."""
    allowed = {"api.kiwoom.com", "opendart.fss.or.kr"}
    hosts: dict[str, set[str]] = {}
    shipped = list((REPO / "src/sectorflow").rglob("*.py")) + [REPO / "scripts" / Path(d).name
                                                              for d in FORCE.values() if d.endswith(".py")]
    for f in shipped:
        for h in re.findall(r"https?://([A-Za-z0-9.\-]+)", f.read_text("utf-8")):
            hosts.setdefault(h, set()).add(f.name)
    assert set(hosts) <= allowed, {h: v for h, v in hosts.items() if h not in allowed}
    net = re.compile(r"^\s*(?:import|from)\s+(socket|http\.client|requests|httpx|aiohttp|urllib3)\b", re.M)
    offenders = {f.name for f in shipped if net.search(f.read_text("utf-8"))}
    assert not offenders, offenders


def test_entry_point_runs_the_repo_script_in_a_checkout():
    out = subprocess.run([sys.executable, "-c", "from sectorflow.report_cli import payload; payload()",
                          "--help"], capture_output=True, text=True, cwd=REPO / "tests")
    assert out.returncode == 0 and "--from-json" in out.stdout
