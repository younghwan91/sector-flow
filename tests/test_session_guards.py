"""``tests/conftest.py`` 의 세션 장치 — 진짜 pytest 를 작은 프로젝트에 돌려 본다."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _project(tmp_path: Path, body: str) -> Path:
    (tmp_path / "tests").mkdir()
    shutil.copy(REPO / "tests" / "conftest.py", tmp_path / "tests" / "conftest.py")
    (tmp_path / "scripts").mkdir(exist_ok=True)
    shutil.copy(REPO / "scripts" / "memcap.py", tmp_path / "scripts" / "memcap.py")
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
                                             '[tool.coverage.run]\nsource = ["."]\n', "utf-8")
    (tmp_path / "tests" / "test_x.py").write_text(body, "utf-8")
    return tmp_path


def _env(**kw) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COV", "PYTEST"))}
    return {**env, **kw}


def _pytest(root: Path, *args: str, **env) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args], cwd=root,
                            env=_env(**env), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def test_a_hung_session_is_killed_red_with_a_stack(tmp_path):
    """멈춘 검사가 잡 시간을 통째로 쓰지 않는다 — 상한에서 스택을 찍고 0 아닌 코드로 끝난다."""
    root = _project(tmp_path, "import time\n\n\ndef test_hang():\n    time.sleep(120)\n")
    p = _pytest(root, SECTOR_FLOW_PYTEST_LIMIT_S="2")             # 기본 캡처 — 스택이 캡처 파일로 사라지면 안 된다
    try:
        out, _ = p.communicate(timeout=60)
    finally:
        p.kill()
    assert p.returncode not in (0, None), out
    assert "test_hang" in out and "Timeout" in out, out


def test_limit_zero_turns_the_limit_off(tmp_path):
    root = _project(tmp_path, "import time\n\n\ndef test_quick():\n    time.sleep(1.5)\n")
    p = _pytest(root, SECTOR_FLOW_PYTEST_LIMIT_S="0")
    out, _ = p.communicate(timeout=60)
    assert p.returncode == 0, out


def test_overlapping_coverage_runs_both_end_red(tmp_path):
    """같은 체크아웃의 겹친 ``--cov``: 늦은 쪽은 바로 거절되고, 측정을 잃은 먼저 쪽도 실패로 끝난다."""
    flag = tmp_path / "release"
    body = (f"import pathlib\nimport time\n\n\ndef test_wait():\n"
            f"    pathlib.Path(r'{tmp_path}/started').touch()\n"
            f"    for _ in range(600):\n        if pathlib.Path(r'{flag}').exists():\n            return\n"
            f"        time.sleep(0.05)\n    raise AssertionError('풀어 주지 않았다')\n")
    root = _project(tmp_path, body)
    first = _pytest(root, "--cov", "--cov-report=")
    try:
        for _ in range(600):
            if (tmp_path / "started").exists():
                break
            time.sleep(0.05)
        second = _pytest(root, "--cov", "--cov-report=")
        out2, _ = second.communicate(timeout=60)
        assert second.returncode == 3 and "다른 pytest --cov 가 돌고" in out2, out2
    finally:
        flag.touch()
    out1, _ = first.communicate(timeout=60)
    assert first.returncode == 3 and "믿을 수 없다" in out1, out1


def test_a_lone_coverage_run_is_untouched_even_after_a_crashed_overlap(tmp_path):
    """먼저 쪽이 죽어 남은 표시·자물쇠 파일이 다음 정상 실행을 빨갛게 만들지 않는다."""
    root = _project(tmp_path, "def test_ok():\n    assert True\n")
    (root / ".covrun.clobbered").write_text("pid 1\n", "utf-8")
    for _ in range(2):
        p = _pytest(root, "--cov", "--cov-report=")
        out, _x = p.communicate(timeout=60)
        assert p.returncode == 0, out
