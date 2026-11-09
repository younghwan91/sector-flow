"""검사·변이 실행의 메모리 상한 — 폭주한 파이썬 하나가 운영 DB 를 죽이지 않게 한다.

2026-11-09 변이 실행의 파이썬 한 프로세스가 41GB 까지 커져 시스템 OOM 이 났고, SSH 세션 자식이 물려받은
``oom_score_adj=-900`` 때문에 커널은 그 프로세스보다 운영 DB·cron 을 먼저 죽였다. 커널에 맡기면 늦으니 상한을 자기가 건다.

- ``RLIMIT_DATA`` 다. ``RLIMIT_AS`` 는 node·Chromium 이 가상 주소를 크게 예약하는 것까지 세어 멀쩡한 검사를 죽인다.
  ``RLIMIT_DATA`` 는 실제로 쓰려고 잡은 사설 쓰기 영역만 센다(리눅스 4.7+).
- 프로세스마다 따로 세고 자식이 물려받는다. 이미 더 낮은 상한(바깥 ``ulimit -d``)은 올리지 않는다.
- 끄는 값은 없다. :data:`LIMIT_ENV` 는 값을 바꿀 뿐이다.

상한에 닿은 실패는 검사가 버그를 잡은 것이 아니다 — :func:`hit_cap` 으로 가려 '잡힘' 으로 세지 않는다.
"""
from __future__ import annotations

import os
import re
import resource

#: 상한을 MB 로 바꾼다. 비었거나 0 이하면 기본값.
LIMIT_ENV = "SECTOR_FLOW_MEM_LIMIT_MB"
#: 프로세스 하나의 상한(바이트). 근거는 전체 pytest 실측 — ``docs/merge-rehearsal.md``.
MEM_LIMIT = 6 * 1024 ** 3
#: 커널이 죽인 프로세스의 종료코드 — SIGKILL, 셸을 거치면 137.
KILLED = (-9, 137)
#: 메모리 고갈의 흔적. ``MemoryError`` 는 줄 머리(검사 요약·``E`` 줄·자식의 트레이스백)와 repr 속 줄바꿈 뒤만 본다
#: — 검사 소스나 단언 메시지에 인용된 글자는 세지 않는다.
EXHAUSTED_RE = re.compile(r"(?:^(?:E\s+|(?:FAILED|ERROR) \S+ - )?|\\n)(?:\w+\.)*MemoryError\b"
                          r"|\[Errno 12\]|std::bad_alloc|JavaScript heap out of memory", re.M)


def limit_bytes() -> int:
    mb = int(os.environ.get(LIMIT_ENV) or 0)
    return mb * 1024 ** 2 if mb > 0 else MEM_LIMIT


def cap_memory() -> None:
    """지금 프로세스에 상한을 건다 — ``preexec_fn`` 으로 넘기거나 pytest 세션이 자기에게 부른다."""
    n = limit_bytes()
    soft, _hard = resource.getrlimit(resource.RLIMIT_DATA)
    if soft != resource.RLIM_INFINITY:
        n = min(n, soft)
    resource.setrlimit(resource.RLIMIT_DATA, (n, n))


def hit_cap(returncode: int, output: str) -> bool:
    """이 실패가 메모리 때문인가. 성공(0)은 아니다. 자식의 트레이스백을 검사가 삼키면 출력만으로는 못 가린다."""
    return returncode != 0 and (returncode in KILLED or bool(EXHAUSTED_RE.search(output)))
