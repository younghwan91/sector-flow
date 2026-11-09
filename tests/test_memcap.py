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


# 표를 함수 밖에 둔다 — 실패 출력에 찍히는 검사 소스에 이 글자들이 있으면 하네스가 메모리 고갈로 읽어 변이를 판정 불가로 낸다.
EXHAUSTED = ((-9, ""), (137, ""), (1, "FAILED tests/t.py::x - MemoryError"), (1, "E   MemoryError"),
             (1, "MemoryError\n"), (1, "E   builtins.MemoryError: x"), (1, r"stderr='Traceback\nMemoryError\n'"),
             (1, "OSError: [Errno 12] Cannot allocate memory"), (1, "terminate: std::bad_alloc"),
             (134, "FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory"))
#: 검사 실패·소스 줄·단언 메시지 속 인용·성공은 메모리 탓이 아니다.
PLAIN = ((1, "FAILED tests/t.py::x - AssertionError"), (1, "    raise MemoryError"),
         (1, "E   assert 'MemoryError' in y"), (0, "E   MemoryError"), (2, "ERROR collecting"), (-15, ""))


def test_hit_cap_tells_exhaustion_from_a_plain_failure():
    wrong = [c for c in EXHAUSTED if not memcap.hit_cap(*c)] + [c for c in PLAIN if memcap.hit_cap(*c)]
    assert not wrong


def test_children_raise_their_oom_score_so_the_kernel_kills_them_before_the_database(tmp_path):
    """SSH 세션의 −900 을 물려받으면 커널은 폭주한 검사보다 운영 DB 를 먼저 죽인다(2026-11-09). 올리는 것은 권한이 필요 없다."""
    code = "print(open('/proc/self/oom_score_adj').read())"

    def child_starting_at(adj: int) -> int:
        """이 pytest 자신이 이미 상한 아래라 500 을 물려받는다 — 낮은 값에서 시작해야 올리는 코드를 본다(낮추기는 내력 최솟값까지 허용된다)."""
        def start():
            with open("/proc/self/oom_score_adj", "w", encoding="ascii") as f:
                f.write(str(adj))
            memcap.cap_memory()
        return int(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, preexec_fn=start).stdout)

    assert child_starting_at(100) == memcap.OOM_SCORE_ADJ > 0
    assert child_starting_at(900) == 900                   # 더 높은 값을 내리지 않는다


def test_tree_limit_defaults_to_twice_the_process_limit(monkeypatch):
    monkeypatch.setenv(memcap.LIMIT_ENV, "3000")
    monkeypatch.delenv(memcap.TREE_ENV, raising=False)
    assert memcap.tree_limit_bytes() == 6000 * 1024 ** 2
    monkeypatch.setenv(memcap.TREE_ENV, "5000")
    assert memcap.tree_limit_bytes() == 5000 * 1024 ** 2
    assert "프로세스 3000MB" in memcap.describe() and "트리 5000MB" in memcap.describe()


def test_victim_is_the_largest_descendant_and_only_over_the_limit():
    table = {1: (0, 90, "other"), 10: (1, 300, "harness"), 11: (10, 40, "a"), 12: (10, 50, "b"), 13: (12, 70, "c")}
    tree = memcap.tree_of(10, table)
    assert tree == {10, 11, 12, 13}                        # 형제 프로세스(1)는 다른 트리다
    assert memcap.pick_victim(10, tree, table, 460) is None
    assert memcap.pick_victim(10, tree, table, 459) == (13, 460)
    assert memcap.pick_victim(10, {10}, table, 100) == (10, 300)  # 혼자면 뿌리다


def test_watcher_kills_a_runaway_tree_and_leaves_the_reason_in_the_job_log(tmp_path):
    """프로세스별 상한은 트리를 못 본다 — 8GB 러너에서 3GB 자식 셋이면 러너째 죽어 **이유가 안 남는다**. 작은 상한으로 진짜 감시자를 돌린다."""
    code = f"""
import subprocess, sys, time
sys.path.insert(0, {str(REPO / "scripts")!r})
import memcap
memcap.watch_tree()
hog = "b = bytearray(120 * 1024 ** 2); import time; time.sleep(30)"
kids = [subprocess.Popen([sys.executable, "-c", hog]) for _ in range(2)]
t0 = time.time()
while time.time() - t0 < 15 and all(k.poll() is None for k in kids):
    time.sleep(0.1)
print(sorted(str(k.poll()) for k in kids), memcap.tree_kills())
for k in kids:
    k.kill()
"""
    env = {**os.environ, memcap.TREE_ENV: "150", "TMPDIR": str(tmp_path)}
    env.pop(memcap.TREE_LOG_ENV, None)
    p = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)
    assert "['-9', 'None'] 1" in p.stdout, p.stdout + p.stderr
    assert "memcap: 프로세스 트리" in p.stderr and "상한 150MB" in p.stderr, p.stderr


def test_watcher_stands_down_when_an_ancestor_already_runs_one(monkeypatch):
    """자식 하네스마다 감시자가 뜨면 합을 여러 번 재고 한 폭주에 여럿을 죽인다."""
    monkeypatch.setenv(memcap.TREE_LOG_ENV, "/nonexistent/log")
    spawned = []
    monkeypatch.setattr(memcap.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    memcap.watch_tree()
    assert not spawned and memcap.tree_kills() == 0
