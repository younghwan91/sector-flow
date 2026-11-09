"""검사·변이 실행의 메모리 상한 — 폭주한 파이썬 하나가 운영 DB 를 죽이지 않게 한다.

2026-11-09 변이 실행의 파이썬 한 프로세스가 41GB 까지 커져 시스템 OOM 이 났고, SSH 세션 자식이 물려받은
``oom_score_adj=-900`` 때문에 커널은 그 프로세스보다 운영 DB·cron 을 먼저 죽였다. 커널에 맡기면 늦으니 상한을 자기가 건다.

- ``RLIMIT_DATA`` 다. ``RLIMIT_AS`` 는 node·Chromium 이 가상 주소를 크게 예약하는 것까지 세어 멀쩡한 검사를 죽인다.
  ``RLIMIT_DATA`` 는 실제로 쓰려고 잡은 사설 쓰기 영역만 센다(리눅스 4.7+).
- 프로세스마다 따로 세고 자식이 물려받는다. 이미 더 낮은 상한(바깥 ``ulimit -d``)은 올리지 않는다.
- 끄는 값은 없다. :data:`LIMIT_ENV` 는 값을 바꿀 뿐이다.
- 프로세스별 상한은 트리를 못 본다 — 3GB 자식 셋이면 8GB 러너가 죽는다. :func:`watch_tree` 가 별도 프로세스로 트리 합(RssAnon)을 재고
  상한(:data:`TREE_ENV`, 기본 프로세스 상한의 두 배)을 넘으면 가장 큰 자손을 죽이고 **이유를 stderr 에 남긴다**(러너가 통째로 죽으면 잡 로그가
  없다). systemd 에 기대지 않는다 — 러너엔 사용자 매니저가 없고 로컬과 같은 경로여야 시험된다. 한계: 0.5초 표본이라 커널 상한이 아니다.
- 커널 OOM 까지 가면 검사 쪽이 먼저 죽게 ``oom_score_adj`` 를 :data:`OOM_SCORE_ADJ` 로 올린다. SSH 세션이 물려준 −900 은 그대로면 운영 DB
  보다 늦게 죽는다(11-09 사고). 올리는 것은 권한이 필요 없고 자식이 물려받는다.

상한에 닿은 실패는 검사가 버그를 잡은 것이 아니다 — :func:`hit_cap` 으로 가려 '잡힘' 으로 세지 않는다.
"""
from __future__ import annotations

import os
import re
import resource
import signal
import subprocess
import sys
import tempfile
import time

#: 상한을 MB 로 바꾼다. 비었거나 0 이하면 기본값.
LIMIT_ENV = "SECTOR_FLOW_MEM_LIMIT_MB"
#: 프로세스 하나의 상한(바이트). 근거는 전체 pytest 실측 — ``docs/merge-rehearsal.md``.
MEM_LIMIT = 6 * 1024 ** 3
#: 프로세스 트리 합의 상한(MB). 비었거나 0 이하면 프로세스 상한의 두 배.
TREE_ENV = "SECTOR_FLOW_TREE_LIMIT_MB"
#: 감시자가 죽인 기록 파일 — 자식이 물려받는다. 켜져 있으면 감시자가 이미 도는 것이다.
TREE_LOG_ENV = "SECTOR_FLOW_TREE_LOG"
#: 커널 OOM 이 검사 프로세스를 먼저 고르게 한다(운영 DB·cron 은 0 이하).
OOM_SCORE_ADJ = 500
TREE_POLL_S = 0.5
#: 커널이 죽인 프로세스의 종료코드 — SIGKILL, 셸을 거치면 137.
KILLED = (-9, 137)
#: 메모리 고갈의 흔적. ``MemoryError`` 는 줄 머리(검사 요약·``E`` 줄·자식의 트레이스백)와 repr 속 줄바꿈 뒤만 본다
#: — 검사 소스나 단언 메시지에 인용된 글자는 세지 않는다.
EXHAUSTED_RE = re.compile(r"(?:^(?:E\s+|(?:FAILED|ERROR) \S+ - )?|\\n)(?:\w+\.)*MemoryError\b"
                          r"|\[Errno 12\]|std::bad_alloc|JavaScript heap out of memory", re.M)


def limit_bytes() -> int:
    mb = int(os.environ.get(LIMIT_ENV) or 0)
    return mb * 1024 ** 2 if mb > 0 else MEM_LIMIT


def tree_limit_bytes() -> int:
    mb = int(os.environ.get(TREE_ENV) or 0)
    return mb * 1024 ** 2 if mb > 0 else 2 * limit_bytes()


def raise_oom_score() -> None:
    """이미 더 높으면 두고, 없는 플랫폼·막힌 환경이면 넘어간다 — 상한의 부가 장치이지 검사를 막을 이유가 아니다."""
    try:
        with open("/proc/self/oom_score_adj", "r+", encoding="ascii") as f:
            if int(f.read()) < OOM_SCORE_ADJ:
                f.seek(0)
                f.write(str(OOM_SCORE_ADJ))
    except (OSError, ValueError):
        pass


def cap_memory() -> None:
    """지금 프로세스에 상한을 건다 — ``preexec_fn`` 으로 넘기거나 pytest 세션이 자기에게 부른다."""
    n = limit_bytes()
    soft, _hard = resource.getrlimit(resource.RLIMIT_DATA)
    if soft != resource.RLIM_INFINITY:
        n = min(n, soft)
    resource.setrlimit(resource.RLIMIT_DATA, (n, n))
    raise_oom_score()


def describe() -> str:
    return (f"메모리 상한: 프로세스 {limit_bytes() // 1024 ** 2}MB · 트리 {tree_limit_bytes() // 1024 ** 2}MB · "
            f"oom_score_adj {OOM_SCORE_ADJ} · 트리 상한이 죽인 프로세스 {tree_kills()}개")


def _proc_table() -> dict[int, tuple[int, int, str]]:
    """pid → (부모, RssAnon 바이트, 이름). 표본 사이에 사라진 프로세스는 빠진다."""
    out: dict[int, tuple[int, int, str]] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/status", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        ppid = re.search(r"^PPid:\s+(\d+)", text, re.M)
        rss = re.search(r"^RssAnon:\s+(\d+) kB", text, re.M)
        comm = re.search(r"^Name:\s+(.*)$", text, re.M)
        if ppid:
            out[int(name)] = (int(ppid.group(1)), int(rss.group(1)) * 1024 if rss else 0, comm.group(1) if comm else "?")
    return out


def tree_of(root: int, table: dict[int, tuple[int, int, str]]) -> set[int]:
    """``root`` 와 그 자손."""
    kids: dict[int, list[int]] = {}
    for pid, (ppid, _rss, _name) in table.items():
        kids.setdefault(ppid, []).append(pid)
    seen, todo = set(), [root]
    while todo:
        pid = todo.pop()
        if pid not in seen and pid in table:
            seen.add(pid)
            todo += kids.get(pid, [])
    return seen


def pick_victim(root: int, tree: set[int], table: dict[int, tuple[int, int, str]], limit: int) -> "tuple[int, int] | None":
    """합이 상한을 넘으면 (죽일 pid, 합). 가장 큰 자손을 고르고, 자손이 없을 때만 뿌리다."""
    total = sum(table[p][1] for p in tree)
    if total <= limit:
        return None
    rest = tree - {root} or tree
    return max(rest, key=lambda p: table[p][1]), total


def _alive(pid: int) -> bool:
    """좀비는 끝난 것이다 — 부모가 감시자가 쥔 stderr 파이프가 닫히길 기다리며 아직 거두지 않았을 수 있다(그러면 서로를 기다린다)."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def watch(root: int, limit: int, log_path: str, poll: float = TREE_POLL_S) -> None:
    """``root`` 트리가 끝날 때까지 잰다. 자기 자신은 뺀다(부모 트리 안에 있다)."""
    me = os.getpid()
    while _alive(root):
        table = _proc_table()
        table.pop(me, None)
        hit = pick_victim(root, tree_of(root, table), table, limit) if root in table else None
        if hit:
            pid, total = hit
            line = (f"memcap: 프로세스 트리 {total // 1024 ** 2}MB > 상한 {limit // 1024 ** 2}MB — "
                    f"pid {pid}({table[pid][2]}, {table[pid][1] // 1024 ** 2}MB)를 죽인다")
            print(line, file=sys.stderr, flush=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(poll)
    try:
        os.unlink(log_path)
    except OSError:
        pass


def watch_tree() -> None:
    """이 프로세스 트리를 재는 감시자를 띄운다 — 스레드가 아니라 프로세스다(하네스의 ``preexec_fn`` 이 스레드와 못 섞인다).
    이미 조상이 띄웠으면(자식 하네스) 아무것도 안 한다. 감시자는 뿌리가 끝나면 스스로 끝난다."""
    if os.environ.get(TREE_LOG_ENV) or not os.path.isdir("/proc"):
        return
    fd, path = tempfile.mkstemp(prefix="memcap.", suffix=".log")
    os.close(fd)
    os.environ[TREE_LOG_ENV] = path
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--watch", str(os.getpid()),
                      str(tree_limit_bytes()), path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)


def tree_kills() -> int:
    """감시자가 지금까지 죽인 수 — 하네스가 '이 변이 도는 동안 죽었나' 를 본다."""
    path = os.environ.get(TREE_LOG_ENV)
    if not path:
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def hit_cap(returncode: int, output: str) -> bool:
    """이 실패가 메모리 때문인가. 성공(0)은 아니다. 자식의 트레이스백을 검사가 삼키면 출력만으로는 못 가린다."""
    return returncode != 0 and (returncode in KILLED or bool(EXHAUSTED_RE.search(output)))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--watch"]:
        watch(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
