"""세션 전체에 거는 두 가지 — 시간 상한과, 같은 체크아웃에서 겹친 ``pytest --cov`` 거절.

**시간 상한.** ``test`` 잡에는 pytest 쪽 상한이 없어 멈춘 검사가 잡 시간(6시간)을 통째로 쓴다.
``faulthandler.dump_traceback_later(exit=True)`` 는 어느 스레드에 멈춰 있든(C 안이어도) 스택을 찍고 프로세스를 끝낸다 —
종료코드가 0 이 아니라 잡이 빨개진다. 평소 5~8분이고 바쁜 머신에서 20분까지 봤다. :data:`LIMIT_ENV` 로 바꾼다(0 = 끔).

**겹친 커버리지.** ``pytest --cov`` 는 시작할 때 ``.coverage.*`` 를 지우고, 끝날 때 남은 ``.coverage.*`` 를 합치며 지운다.
같은 폴더의 두 실행은 서로의 **서브프로세스 측정**을 지운다 — 예외 없이 커버리지만 낮게 나온다(2026-11-09 재현: 혼자면
``report_cli.py`` 82.6%, 도중에 다른 ``--cov`` 가 시작하면 47.8%). pytest-cov 의 삭제는 이 파일이 불리기 전이라 막지 못한다.
그래서 먼저 돌던 쪽에 표시를 남겨 그쪽도 실패로 끝낸다 — 틀린 숫자로 끝나느니 다시 돈다.
"""
from __future__ import annotations

import faulthandler
import os
import sys
from pathlib import Path

import pytest

LIMIT_ENV = "SECTOR_FLOW_PYTEST_LIMIT_S"
#: 세션 상한(초) — 평소의 네 배쯤. 샌드박스·바쁜 머신을 빨갛게 만들 값이 아니라 멈춘 잡을 끝내는 값이다.
SESSION_LIMIT_S = 1800
#: ``.coverage.*`` 와 겹치지 않는 이름(pytest-cov 가 같이 지우지 않게). ``.gitignore`` 에 있다.
LOCK_NAME, CLOBBER_NAME = ".covrun.lock", ".covrun.clobbered"

_lock_fd: "int | None" = None


def _cov_active(config: pytest.Config) -> bool:
    return bool(getattr(config.option, "cov_source", None)) and not getattr(config.option, "no_cov", False)


def pytest_configure(config: pytest.Config) -> None:
    global _lock_fd
    limit = float(os.environ.get(LIMIT_ENV) or SESSION_LIMIT_S)
    if limit > 0 and not hasattr(config, "workerinput"):
        # 이 시점엔 fd 2 캡처가 풀려 있다. 사본을 넘겨야 검사 도중(fd 2 = 캡처 파일)에도 스택이 잡 로그로 간다.
        faulthandler.dump_traceback_later(limit, exit=True, file=os.dup(sys.__stderr__.fileno()))
    if not _cov_active(config):
        return
    import fcntl
    root = Path(config.rootpath)
    fd = os.open(root / LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        (root / CLOBBER_NAME).write_text(f"pid {os.getpid()}\n", "utf-8")
        pytest.exit(f"이 체크아웃({root})에서 다른 pytest --cov 가 돌고 있다 — 서로의 측정 파일을 지운다. "
                    "끝난 뒤 다시 돌리거나 다른 클론에서 돌려라", returncode=3)
    _lock_fd = fd
    (root / CLOBBER_NAME).unlink(missing_ok=True)


def pytest_unconfigure(config: pytest.Config) -> None:
    faulthandler.cancel_dump_traceback_later()


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    if _lock_fd is None:
        return
    marker = Path(session.config.rootpath) / CLOBBER_NAME
    if marker.exists():
        marker.unlink(missing_ok=True)
        pytest.exit("도중에 같은 체크아웃에서 다른 pytest --cov 가 시작했다 — 서브프로세스 측정이 지워졌으니 이 커버리지는 "
                    "믿을 수 없다. 다시 돌려라", returncode=3)
