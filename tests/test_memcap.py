"""메모리 상한(``scripts/memcap.py``) — 빠지면 변이 하나가 호스트를 OOM 으로 몬다(2026-11-09).

여기 검사는 **상한을 읽기만 한다.** 상한이 빠진 채 진짜로 할당하는 검사를 두면 그 검사의 변이가 곧 사고다 — 11:05·11:25 의
41GB 가 그렇게 났다. ``ulimit -d`` 나 바깥 하네스의 상한을 물려받아도 판별되게 그보다 낮은 값을 환경변수로 건다.
"""
from __future__ import annotations

import os
import resource
import shutil
import subprocess
import sys
from pathlib import Path

from scripts import memcap

REPO = Path(__file__).resolve().parent.parent
LOW = 1024 ** 3
READ = ("import resource as r; print(r.getrlimit(r.RLIMIT_DATA)[0], r.getrlimit(r.RLIMIT_AS)[0])")


def _limits_in_child(preexec=memcap.cap_memory) -> tuple[int, int]:
    out = subprocess.run([sys.executable, "-c", READ], capture_output=True, text=True, check=True,
                         preexec_fn=preexec).stdout
    data, addr = out.split()
    return int(data), int(addr)


def test_cap_limits_data_not_address_space(monkeypatch):
    """node·Chromium 은 가상 주소를 크게 예약한다 — ``RLIMIT_AS`` 면 멀쩡한 브라우저 검사가 죽는다."""
    monkeypatch.setenv(memcap.LIMIT_ENV, "1024")
    data, addr = _limits_in_child()
    assert data == LOW
    assert addr == resource.getrlimit(resource.RLIMIT_AS)[0]


def test_cap_never_raises_a_tighter_outer_limit(monkeypatch):
    """바깥 ``ulimit -d`` 가 더 낮으면 그대로 둔다 — 올리려 들면 ``ValueError`` 로 자식이 뜨지도 못한다."""
    monkeypatch.setenv(memcap.LIMIT_ENV, "1024")
    tighter = LOW // 2

    def outer_then_cap():
        resource.setrlimit(resource.RLIMIT_DATA, (tighter, tighter))
        memcap.cap_memory()

    assert _limits_in_child(outer_then_cap)[0] == tighter


def test_limit_env_changes_the_value_but_cannot_turn_it_off(monkeypatch):
    monkeypatch.setenv(memcap.LIMIT_ENV, "2048")
    assert memcap.limit_bytes() == 2048 * 1024 ** 2
    for off in ("0", "-1", ""):
        monkeypatch.setenv(memcap.LIMIT_ENV, off)
        assert memcap.limit_bytes() == memcap.MEM_LIMIT
    monkeypatch.delenv(memcap.LIMIT_ENV)
    assert memcap.limit_bytes() == memcap.MEM_LIMIT


def test_pytest_session_caps_itself(tmp_path):
    """하네스 없이 그냥 ``pytest`` 를 돌려도 상한이 걸린다 — 진짜 ``tests/conftest.py`` 를 작은 트리에 옮겨 본다."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "scripts").mkdir()
    shutil.copy(REPO / "tests" / "conftest.py", tmp_path / "tests" / "conftest.py")
    shutil.copy(REPO / "scripts" / "memcap.py", tmp_path / "scripts" / "memcap.py")
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\ntestpaths = ["tests"]\n')
    (tmp_path / "tests" / "test_probe.py").write_text(
        f"import resource\n\n\ndef test_probe():\n    assert resource.getrlimit(resource.RLIMIT_DATA)[0] == {LOW}\n")
    env = {**os.environ, memcap.LIMIT_ENV: "1024", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTHONPATH", None)
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-cov"], cwd=tmp_path,
                       env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


def test_hit_cap_tells_exhaustion_from_a_plain_failure():
    # 역슬래시를 떼어 둔다 — 이 소스 줄이 실패 출력에 그대로 찍히면 하네스가 그 글자를 메모리 고갈로 읽어 변이를 판정 불가로 낸다.
    in_repr = "stderr='Traceback" + chr(92) + "n" + "MemoryError'"
    for rc, out in ((-9, ""), (137, ""), (1, "FAILED tests/t.py::x - MemoryError"), (1, "E   MemoryError"),
                    (1, "MemoryError\n"), (1, "E   builtins.MemoryError: x"), (1, in_repr),
                    (1, "OSError: [Errno 12] Cannot allocate memory"), (1, "terminate: std::bad_alloc"),
                    (134, "FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory")):
        assert memcap.hit_cap(rc, out), (rc, out)
    # 검사 실패·소스 줄·단언 메시지 속 인용·성공은 메모리 탓이 아니다.
    for rc, out in ((1, "FAILED tests/t.py::x - AssertionError"), (1, "    raise MemoryError"),
                    (1, "E   assert 'MemoryError' in y"), (0, "E   MemoryError"), (2, "ERROR collecting"), (-15, "")):
        assert not memcap.hit_cap(rc, out), (rc, out)
