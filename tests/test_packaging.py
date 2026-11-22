"""wheel 이 리포트 생성기를 싣는가 — ``uv tool install .`` 한 사용자가 리포트를 만들 수 있어야 한다.

두 층이다. 선언끼리 맞물리는가(콘솔 진입점이 가리키는 함수가 있다 · 그 함수가 돌리는 스크립트가 wheel 복사 목록
(force-include)에 있다 · 복사 원본이 실제로 있다 · 싣는 코드의 import 와 옆 파일 참조가 설치본 안에서 닫힌다), 그리고
**진짜 wheel** 을 빌드해(hatchling 은 dev 의존성이라 네트워크가 필요 없다) 푼 사본만으로 리포트 단계를 끝까지 돌리는가.
선언 검사가 다 초록이어도 wheel 은 옆 스크립트 하나(krx_truth.py) 때문에 ``--json`` 에서 죽고 있었다(2026-11-09).
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

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
    # 템플릿은 빌드 훅이 원격 글꼴을 떼고 파일 하나씩 싣는다 — 레포 템플릿은 하나도 빠짐없이 '싣는다' 또는
    # '웹 전용' 중 한쪽에 적혀 있어야 한다. 새 템플릿이 어느 쪽인지 정하지 않으면 여기서 빨개진다.
    hook = PY["tool"]["hatch"]["build"]["targets"]["wheel"]["hooks"]["custom"]
    assert (REPO / hook["path"]).is_file()
    assert not set(T.TEMPLATES) & set(T.WEB_ONLY_TEMPLATES)
    assert sorted(T.TEMPLATES + T.WEB_ONLY_TEMPLATES) == sorted(p.name for p in (REPO / "scripts/templates").glob("*.html"))


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
    # 임시 사본은 빌드가 끝나면 지운다 — 빌드마다 sf-templates-* 가 쌓였다.
    folders = {Path(src).parent for src in data["force_include"]}
    assert len(folders) == 1 and next(iter(folders)).is_dir()
    hook.finalize("standard", data, "x.whl")
    assert not next(iter(folders)).exists()


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


#: 싣는 코드가 부르지만 wheel 에 일부러 없는 옆 파일 → 이유. 여기 적은 파일은 없어도 돌아야 한다(스모크가 본다).
NOT_SHIPPED = {"krx_truth.py": "내부 검증 전용·data.krx.co.kr — 설계 §4.2 허용 목록 밖이라 안 싣고, 없으면 대조만 꺼진다"}


def _shipped_py() -> list[Path]:
    return sorted((REPO / "src/sectorflow").rglob("*.py")) + [REPO / src for src in FORCE if src.endswith(".py")]


def _third_party_allowed() -> set[str]:
    """pyproject 가 선언한 의존성(필수·pg)의 import 이름. numpy 는 pandas 의 필수 의존성이라 늘 같이 깔린다."""
    reqs = PY["project"]["dependencies"] + PY["project"]["optional-dependencies"]["pg"]
    names = {re.match(r"[A-Za-z0-9_.\-]+", r).group(0).lower() for r in reqs}
    return {n.removesuffix("-binary").replace("-", "_") for n in names} | {"numpy"}


def _is_shipped_module(dotted: str) -> bool:
    """``sectorflow.a.b`` 가 모듈·패키지이거나, 그 앞 경로가 모듈이고 끝이 그 안의 이름이다."""
    parts = dotted.split(".")
    while len(parts) >= 1:
        p = REPO / "src" / Path(*parts)
        if p.with_suffix(".py").is_file() or (p / "__init__.py").is_file():
            return True
        parts = parts[:-1]
    return False


def test_shipped_code_import_closure_stays_inside_the_wheel():
    """싣는 코드의 import 는 표준 라이브러리·선언 의존성·실린 ``sectorflow`` 모듈뿐이고, 옆 파일 참조는 실렸거나 NOT_SHIPPED 다."""
    allowed = _third_party_allowed()
    shipped = {Path(d).name for d in FORCE.values() if d.endswith(".py")}
    bad: list[str] = []
    for f in _shipped_py():
        tree = ast.parse(f.read_text("utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                mods = [f"{n.module}.{a.name}" for a in n.names]
            else:
                mods = []
            for m in mods:
                top = m.split(".")[0]
                if top == "sectorflow":
                    if not _is_shipped_module(m):
                        bad.append(f"{f.name}: {m} 가 sectorflow 안에 없다")
                elif top not in sys.stdlib_module_names and top != "__future__" and top not in allowed:
                    bad.append(f"{f.name}: {m} 는 표준 라이브러리도 선언 의존성도 아니다")
            # ``os.path.join(here, "x.py")`` · ``os.path.join(here, "templates", "x.html")`` — 옆 파일을 부르는 모양.
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "join":
                for a in n.args:
                    if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
                        continue
                    if a.value.endswith(".py") and a.value not in shipped | set(NOT_SHIPPED):
                        bad.append(f"{f.name}: 옆 스크립트 {a.value} 가 wheel 에 없다")
                    if a.value.endswith(".html") and any(
                            isinstance(x, ast.Constant) and x.value == "templates" for x in n.args) \
                            and a.value not in T.TEMPLATES:
                        bad.append(f"{f.name}: 템플릿 {a.value} 가 wheel 에 없다")
    assert not bad, bad
    # 헛돌지 않는지 — 지금 코드에 실제로 잡히는 모양이어야 한다.
    text = (REPO / "scripts/sector_numbers.py").read_text("utf-8")
    assert '"sector_flow.py"' in text and '"templates", "sector_numbers.html"' in text
    for name in NOT_SHIPPED:
        assert name not in shipped, f"{name} 를 실었으면 NOT_SHIPPED 에서 지워라"


def test_krx_truth_load_failure_never_breaks_the_report(tmp_path):
    """모듈이 없으면(설치본) 꺼진 것과 같고, 못 읽으면 skip 블록이다 — 어느 쪽도 예외를 올리지 않는다."""
    spec = importlib.util.spec_from_file_location("_sf_pkg", REPO / "scripts/sector_flow.py")
    sf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sf)
    absent: dict = {"today": {"date": "2026-09-04"}}
    sf._attach_krx_truth(absent, None, None, path=str(tmp_path / "없다.py"))
    assert absent == {"today": {"date": "2026-09-04"}}
    broken = tmp_path / "krx_truth.py"
    broken.write_text("def (\n", "utf-8")
    bad: dict = {"today": {"date": "2026-09-04"}}
    sf._attach_krx_truth(bad, None, None, path=str(broken))
    assert bad["today"]["krx_truth"]["status"] == "skip" and "로드 실패" in bad["today"]["krx_truth"]["reason"]


@pytest.fixture(scope="module")
def wheel_site(tmp_path_factory) -> tuple[Path, list[str]]:
    """레포의 진짜 wheel 을 빌드해 푼 폴더. 빌드 훅의 임시 사본이 쌓이지 않게 TMPDIR 을 여기로 둔다."""
    td = tmp_path_factory.mktemp("wheel")
    p = subprocess.run([sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(td / "dist")],
                       cwd=REPO, capture_output=True, text=True, env={**os.environ, "TMPDIR": str(td)})
    assert p.returncode == 0, p.stdout + p.stderr
    whl = next((td / "dist").glob("*.whl"))
    with zipfile.ZipFile(whl) as z:
        names = z.namelist()
        z.extractall(td / "site")
    return td / "site", names


def test_built_wheel_makes_a_report_from_its_own_copy(wheel_site, tmp_path):
    """빌드한 wheel 의 사본만으로 진입점 네 개가 합성 DB 에서 리포트 단계를 끝까지 돈다(레포 ``scripts/`` 를 안 본다)."""
    from tests.test_pipeline_e2e import REPORT_DAYS, build_db

    site, names = wheel_site
    for shipped in FORCE.values():
        assert shipped in names, f"{shipped} 가 wheel 에 없다"
    for t in T.TEMPLATES:
        assert f"sectorflow/_scripts/templates/{t}" in names, f"{t} 가 wheel 에 없다"
    for name in NOT_SHIPPED:
        assert f"sectorflow/_scripts/{name}" not in names, f"{name} 는 wheel 에 실리면 안 된다"
    db = tmp_path / "fixture.db"
    build_db(db)
    out = tmp_path / "out"
    out.mkdir()
    env = {**os.environ, "PYTHONPATH": str(site), "KR_QUANT_KRX_TRUTH": "1"}
    env.pop("KR_QUANT_DB", None)

    def run(fn: str, *args: str) -> str:
        code = ("import sectorflow.report_cli as r, pathlib; "
                f"assert pathlib.Path(r.script_path('sector_flow.py')).is_relative_to({str(site)!r}), r.__file__; "
                f"r.{fn}()")
        p = subprocess.run([sys.executable, "-c", code, *args], cwd=tmp_path, env=env,
                           capture_output=True, text=True, timeout=300)
        assert p.returncode == 0, f"{fn} {args} 실패\n{p.stdout[-1500:]}\n{p.stderr[-3000:]}"
        return p.stdout + p.stderr

    made = run("payload", "--db", str(db), "--days", str(REPORT_DAYS), "--json", str(out / "payload.json"))
    assert "설치본이라 건너뛴다" in made        # 대조를 켰어도 설치본은 말하고 넘어간다
    run("payload", "--from-json", str(out / "payload.json"), "--html", str(out / "viewer.html"))
    run("numbers", "--payload", str(out / "payload.json"), "--html", str(out / "numbers.html"))
    payload = json.loads((out / "payload.json").read_text("utf-8"))
    assert "krx_truth" not in payload["today"]
    html = (out / "numbers.html").read_text("utf-8")
    assert not T.AUTO_FETCH.search(html), "설치본 페이지가 원격 자원을 부른다"
    assert not T.AUTO_FETCH.search((out / "viewer.html").read_text("utf-8"))
    run("gate", "--asof", payload["dates"][-1], "--db", str(db))
    run("verify", "--dir", str(out), "--db-check", "--db", str(db), "--layers", "A")


def test_built_wheel_draws_a_byo_map_from_its_own_copy(wheel_site, tmp_path):
    """``sector-flow-byo map`` 이 wheel 사본(sector_flow.py·sector_map.py·원격 글꼴 뗀 템플릿)만으로 지도 전용 sqlite 에서 선다."""
    from sectorflow import demo
    from tests.test_flows_only import map_only_db

    site, names = wheel_site
    assert "sectorflow/_scripts/sector_map.py" in names and "sectorflow/byo.py" in names
    demo.build_db(tmp_path / "full.db")
    db = map_only_db(tmp_path / "full.db", tmp_path / "map.db")
    out = tmp_path / "map"
    env = {**os.environ, "PYTHONPATH": str(site)}
    env.pop("KR_QUANT_DB", None)
    code = ("import sys, pathlib, sectorflow.byo as b; "
            f"assert pathlib.Path(b.script_path('sector_map.py')).is_relative_to({str(site)!r}); "
            "sys.exit(b.main(sys.argv[1:]))")
    p = subprocess.run([sys.executable, "-c", code, "map", "--db", str(db), "--out", str(out), "--no-card"],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-3000:]
    html = (out / "map.html").read_text("utf-8")
    assert "const D = {" in html and not T.AUTO_FETCH.search(html), "설치본 지도가 원격 자원을 부른다"
