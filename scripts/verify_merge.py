#!/usr/bin/env python3
"""워크트리 머지 게이트 — "검사가 통과했다" 를 믿지 않고 **검사가 자기 버그를 잡는가**를 본다.

이 저장소가 실제로 겪은 일이 이 도구의 존재 이유다.

* 열 정렬 버그 5종을 주입했더니 기존 검사 21개가 **전부 초록**이었다. 렌더가 마지막에
  줄 전체를 화면 폭으로 패딩하므로 셀이 통째로 빠져도 모든 행의 표시 폭은 여전히
  정확히 ``width`` 다 — "모든 행의 폭이 같은가" 는 그 부류를 구조적으로 못 잡는다.
* 그 뒤 새로 쓴 pty 검사 셋이 **자기 버그를 못 잡았다.** 좀비 검사는 배수 스레드와
  fd 경합으로 앱이 아니라 테스트 사정으로 끝났고, 폭 검사는 잡아야 할 케이스를 스스로
  ``continue`` 로 건너뛰었고, 리사이즈 검사는 ``ioctl`` 만으로는 SIGWINCH 가 안 가서
  ``KEY_RESIZE`` 가 아예 도달하지 않았다.
* 별개 세션이 **독립적으로 같은 폭 불변식 함정을 밟았다.**

즉 "테스트가 초록" 의 정보량은 거의 0 이다. 그래서 이 하네스의 중심은 §3 주입 검증이다 —
워크트리가 **자기 변이 목록을 선언**하고, 하네스가 그 변이를 소스에 실제로 넣어
선언한 검사가 **정말 빨개지는지** 확인한다. GUARDRAILS §5 각주의 교훈과 같다:
"규칙을 추가할 때는 그 규칙이 잡아야 할 위반을 실제로 주입해봐야 한다."

리포터-not-판정기(GUARDRAILS §8)의 예외로 이 도구는 PASS/REJECT 를 낸다 — 알파 판정이
아니라 머지 판정이고, 근거를 항목별로 다 적는다. 애매한 것은 ``WARN`` 으로 남겨
사람이 읽게 한다.

사용::

    python scripts/verify_merge.py <워크트리 경로>
    python scripts/verify_merge.py <워크트리> --only mutations,render
    python scripts/verify_merge.py <워크트리> --mutations /경로/보고서에서_옮긴.toml
    python scripts/verify_merge.py --self-test          # 하네스가 정말 REJECT 를 내는가
    python scripts/verify_merge.py . --only mutations --shard 2/4 --budget-min 12   # CI 한 샤드

주입 검증의 속도와 범위 (CI 시간이 PR 마다 4~10분씩 늘던 것을 잡으려고)::

    --shard i/n        변이를 예상 비용으로 n 몫에 나눠 i 번째만 돈다(같은 목록이면 늘 같은 몫).
                       변이 미선언 새 검사 판정은 1번 몫에서만 한다.
    --budget-min M     주입 검증이 M 분을 넘으면 REJECT, 75% 를 넘으면 WARN. 2M 분이면 남은 변이를
                       돌리지 않고 멈춘다(잡 시간 초과로 이유 없이 죽지 않게). 느린 변이 10개를 늘 적는다.
                       M 은 **CI 샤드 한 몫**의 예산이다 — --shard 없이(ci-local 전체 범위처럼) 돌면
                       ci.yml matrix 의 샤드 수(못 읽으면 4)를 곱해 쓴다. 변이가 늘어 전체가 12분을
                       넘자 로컬 pre-push 가 전부 막혔다(2026-11-09) — 전체 실행은 몫 n 개의 일이다.
    --scope all|changed|auto
                       changed = merge-base 이후 바뀐 파일을 대상·가드로 갖거나, 가드가 바뀐 src 모듈·
                       스크립트를 import·문자열로 참조하거나, 선언이 새로 생긴·바뀐 변이만. 의존성·하네스·
                       sector_flow.py·템플릿이 바뀌면 전체. auto(기본)는 ``CI`` 가 있고 ``GITHUB_ACTIONS`` 가 없을 때(로컬 pre-push
                       ci-local) ``--only mutations`` 이면 changed, 그 밖은 all. **전체는 GitHub 이 돈다.**
                       로컬 changed 모드에선 샤드를 무시한다(ci-local 은 매트릭스를 펼치지 않는다).

가드의 주입 전 초록 확인은 **pytest 한 번**으로 모든 가드를 함께 돈다(예전엔 변이마다 한 번 —
pytest 가 변이당 두 번이었다). 주입 후 실행은 ``-x`` 로 첫 실패에서 멈추고, **잡힘은 종료코드 1(검사
실패) 뿐**이다 — 수집 오류·내부 오류·시간 초과는 "판정 불가" REJECT 다.

새 검사(merge-base 이후 추가·수정된 ``def test_*``)는 그 검사를 가드로 둔 변이가 없으면 **REJECT** 다.
변이로 증명할 수 없는 검사(픽스처가 헛돌지 않는지 보는 검사 등)는 함수 안이나 바로 위에
``# mutation-exempt: <이유>`` 를 단다 — 이유 없는 표시는 면제가 아니다.

변이 선언 형식 — 워크트리의 ``tests/mutations.toml`` (또는 ``--mutations``)::

    [[mutation]]
    id      = "accel-cell-shows-wrong-field"
    why     = "가속 열이 다른 값을 그린다"
    guards  = ["tests/test_tui_flow_view.py::test_data_cells_sit_under_their_headers"]
    file    = "src/sectorflow/tui/flow_view.py"
    find    = "    Col(\"가속[%p]\", 9, True, _num(\"accel\")),"
    replace = "    Col(\"가속[%p]\", 9, True, _num(\"ret\")),"

``find`` 는 파일 안에서 **정확히 한 번** 나와야 한다(여러 번이면 무엇을 바꿨는지
모호해진다). **한 번도 안 나오면 REJECT 다** — 앵커를 잃은 변이는 아무것도 증명하지
않는다. 주입 후에는 원본 바이트로 되돌리고 ``git status`` 로 확인한다.
선언 자체의 정합성(앵커 1회·가드 존재·id 중복)은 ``tests/test_mutation_spec.py`` 가
DB 없이 pytest 에서 먼저 본다. 기준에 있던 변이를 지우려면 ``[[removed]]`` 에 ``id``·``why`` 를 남긴다(없으면 REJECT).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:                                    # 3.11+ 는 표준, 3.10 은 tomli (pytest 의존성)
    import tomllib
except ModuleNotFoundError:             # pragma: no cover — 인터프리터에 달렸다
    import tomli as tomllib

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_BASE = "main"
DEFAULT_REPORT = str(REPO / "reports" / "latest")

PASS, REJECT, WARN, SKIP = "PASS", "REJECT", "WARN", "SKIP"


class Section:
    """항목 하나의 판정과 근거.

    dataclass 를 쓰지 않는다 — ``spec_from_file_location`` 으로 이 파일을 불러오면
    (레포의 `test_research_imports` 가 그렇게 한다) 모듈이 `sys.modules` 에 없어
    dataclasses 가 `AttributeError: 'NoneType' object has no attribute '__dict__'`
    로 죽는다. 하네스가 레포 자신의 검사를 깨면 그 하네스는 못 쓴다.
    """

    def __init__(self, name: str, status: str = PASS,
                 lines: list[str] | None = None):
        self.name = name
        self.status = status
        self.lines: list[str] = lines if lines is not None else []

    def say(self, msg: str) -> None:
        self.lines.append(msg)

    def fail(self, msg: str) -> None:
        self.status = REJECT
        self.lines.append("REJECT — " + msg)

    def warn(self, msg: str) -> None:
        if self.status != REJECT:
            self.status = WARN
        self.lines.append("WARN — " + msg)

    def skip(self, msg: str) -> None:
        if self.status == PASS:
            self.status = SKIP
        self.lines.append("SKIP — " + msg)


# --------------------------------------------------------------------------- 공통

def run(cmd: list[str], cwd: Path, env: dict | None = None,
        timeout: int = 1200) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          timeout=timeout, env=env)


def git(cwd: Path, *args: str) -> str:
    p = run(["git", *args], cwd, timeout=60)
    return p.stdout.strip()


def porcelain(cwd: Path) -> list[str]:
    """``git status --porcelain`` 줄 — **strip 하지 않는다.** :func:`git` 은 앞뒤 공백을
    걷어 첫 줄의 ``" M"`` 이 ``"M"`` 이 되고, ``ln[3:]`` 이 경로 첫 글자를 먹었다
    (``cripts/verify_merge.py`` — 첫 줄이 고친 검사 파일이면 새 검사 목록에서 빠진다)."""
    return run(["git", "status", "--porcelain"], cwd, timeout=60).stdout.splitlines()


def tail(text: str, n: int = 2000) -> str:
    text = text.strip()
    return text if len(text) <= n else "…\n" + text[-n:]


def py_env(worktree: Path) -> dict:
    """워크트리 소스를 **확실히** 임포트하게 하는 환경.

    함정: 이 저장소의 ``.venv`` 는 ``.pth`` 로 메인 저장소의 ``src`` 를 가리킨다.
    그대로 돌리면 워크트리 안에서 테스트를 돌려도 임포트되는 건 **메인 소스**라,
    워크트리를 한 줄도 안 고쳐도 초록이 나온다(이 저장소의 상습 실패 모드 —
    "기능은 있고 쓰는 쪽이 안 씀" 의 검증판). PYTHONPATH 가 site-packages 보다
    앞서므로 이걸로 이기고, §임포트출처 검사가 실제로 이겼는지 확인한다.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(worktree / "src") + os.pathsep + str(worktree)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONHASHSEED", None)
    return env


def interpreter(worktree: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    for cand in (REPO / ".venv/bin/python", worktree / ".venv/bin/python"):
        if cand.exists():
            return str(cand)
    return sys.executable


# --------------------------------------------------------------------------- 1. 워크트리

def sec_worktree(wt: Path, base: str, py: str) -> tuple[Section, list[str]]:
    """워크트리가 검증 가능한 상태인가 + 임포트가 정말 워크트리에서 오는가."""
    s = Section("worktree")
    if not (wt / ".git").exists():
        s.fail(f"{wt} 는 git 워크트리가 아니다")
        return s, []
    dirty = porcelain(wt)
    if dirty:
        s.warn("작업 트리가 깨끗하지 않다 — 커밋 안 된 변경 위에서 판정한다:\n"
               + "\n".join("      " + ln for ln in dirty[:20]))
    head = git(wt, "rev-parse", "--short", "HEAD")
    mb = git(wt, "merge-base", base, "HEAD")
    if not mb:
        s.fail(f"기준 {base} 와의 merge-base 를 못 찾았다")
        return s, []
    commits = git(wt, "log", "--format=%H", f"{mb}..HEAD").splitlines()
    s.say(f"HEAD={head}  기준={base}({git(wt, 'rev-parse', '--short', mb)})  "
          f"새 커밋 {len(commits)}건")
    for line in git(wt, "log", "--format=  %h %s", f"{mb}..HEAD").splitlines():
        s.say(line)
    changed = git(wt, "diff", "--name-only", f"{mb}..HEAD").splitlines()
    if dirty:
        changed += [ln[3:] for ln in dirty]
    s.say(f"바뀐 파일 {len(set(changed))}개: " + ", ".join(sorted(set(changed))[:12]))

    # 임포트 출처 — 여기서 틀리면 아래 모든 초록이 거짓말이다.
    p = run([py, "-c", "import sectorflow, sys; print(sectorflow.__file__)"],
            wt, py_env(wt), timeout=120)
    got = p.stdout.strip()
    if not got.startswith(str(wt.resolve())):
        s.fail(f"sectorflow 가 워크트리 밖에서 임포트된다: {got or p.stderr.strip()}\n"
               f"      (이 상태로 돌린 테스트는 워크트리가 아니라 다른 소스를 검사한다)")
    else:
        s.say(f"임포트 출처 확인: {got}")
    return s, commits


# --------------------------------------------------------------------------- 2. 신원

def _ok_identity(addr: str, want: str) -> bool:
    """레포 설정이거나, **GitHub 이 스스로 찍는 주소**면 통과.

    PR 을 웹에서 머지하면 GitHub 이 author 를 계정 프라이버시 주소
    (``…@users.noreply.github.com``), committer 를 ``noreply@github.com`` 으로
    찍는다. 사람이 덮어쓴 게 아니라 GitHub 이 만든 커밋이다 — 안 봐주면
    **머지된 브랜치를 판정할 때마다 REJECT** 가 난다.

    허용은 이 둘로 좁힌다. 임의 도메인을 열면 규칙이 사라진다.
    """
    return (addr == want
            or addr == "noreply@github.com"
            or addr.endswith("@users.noreply.github.com"))


def sec_identity(wt: Path, commits: list[str]) -> Section:
    """새 커밋의 author/committer 가 레포 설정과 같은가.

    공개 레포라 의도치 않은 주소가 이력에 박히면 되돌리기 어렵다.
    **기준 브랜치 이후의 새 커밋만** 본다.

    ⚠️ 같은 규칙이 ``.github/workflows/commit-identity.yml`` 에도 있다. 봐주는 주소를 바꿀 때는
    둘을 같이 고쳐야 한다. 한쪽만 고쳐서 PR 검사가 계속 빨갛던 적이 있다.
    """
    s = Section("identity")
    want_mail = git(wt, "config", "--get", "user.email")
    want_name = git(wt, "config", "--get", "user.name")
    if not want_mail:
        s.warn("레포에 user.email 설정이 없어 비교 기준이 없다")
        return s
    if not commits:
        s.say("새 커밋 없음 — 검사할 대상이 없다")
        return s
    s.say(f"기준 신원: {want_name} <{want_mail}>")
    for sha in commits:
        line = git(wt, "log", "-1", "--format=%h|%an|%ae|%cn|%ce", sha)
        h, an, ae, cn, ce = line.split("|")
        if _ok_identity(ae, want_mail) and _ok_identity(ce, want_mail):
            pass
        elif True:
            s.fail(f"{h} 신원이 다르다 (author={ae} committer={ce}) — "
                   f"`git -c user.email=…` 금지, 레포 설정을 그대로 쓴다")
        elif an != want_name or cn != want_name:
            s.warn(f"{h} 이름이 다르다 (author={an} committer={cn})")
        else:
            s.say(f"  {h} ok")
    return s


# --------------------------------------------------------------------------- 3. 기본

#: CI 와 **같은 범위**를 본다. `src/ tests/` 만 보고 "통과" 라고 적었다가 CI 가
#: 빨간 채로 남은 적이 있다 — 범위가 다르면 통과 보고가 거짓말이 된다.
RUFF_PATHS = ("src/", "tests/", "scripts/", "research/")


def _ruff(py: str, cwd: Path, env: dict) -> set[str]:
    ruff = Path(py).parent / "ruff"
    cmd = ([str(ruff)] if ruff.exists() else [py, "-m", "ruff"]) + \
        ["check", "--output-format", "concise", *RUFF_PATHS]
    p = run(cmd, cwd, env, timeout=300)
    out = set()
    for ln in p.stdout.splitlines():
        m = re.match(r"^(\S+?):\d+:\d+: (\S+) (.*)$", ln.strip())
        if m:                      # 줄번호는 뺀다 — 무관한 편집으로도 밀린다
            out.add(f"{m.group(1)}: {m.group(2)} {m.group(3)}")
    return out


def sec_lint(wt: Path, py: str, base_sha: str, scratch: Path) -> Section:
    """ruff — **이 브랜치가 새로 넣은** 위반만 REJECT.

    기준이 이미 빨간 상태일 수 있다(실제로 그랬다: 79e14df 에 8건이 남아 있었고
    뒤늦게 별도 커밋으로 고쳤다). 그걸 워크트리에 물리면 남의 빚으로 남을
    REJECT 하게 된다 — 판정이 틀리는 것보다 나쁜 건, 판정을 안 믿게 되는 것이다.
    """
    s = Section("lint")
    env = py_env(wt)
    now = _ruff(py, wt, env)
    base_dir = scratch / "lint_base"
    base_dir.mkdir(parents=True, exist_ok=True)
    tar = subprocess.run(["git", "archive", base_sha], cwd=str(wt),
                         capture_output=True, timeout=120)
    subprocess.run(["tar", "-x", "-C", str(base_dir)], input=tar.stdout, timeout=120)
    was = _ruff(py, base_dir, py_env(base_dir))
    new, gone, kept = now - was, was - now, now & was
    if new:
        s.fail(f"이 브랜치가 새로 넣은 ruff 위반 {len(new)}건\n"
               + "\n".join("      " + v for v in sorted(new)))
    else:
        s.say(f"새 위반 없음 (검사 경로: {' '.join(RUFF_PATHS)})")
    if kept:
        s.warn(f"기준에서 물려받은 위반 {len(kept)}건 — 이 워크트리의 책임이 "
               f"아니지만 머지 후에도 남는다:\n"
               + "\n".join("      " + v for v in sorted(kept)))
    if gone:
        s.say(f"고쳐진 위반 {len(gone)}건")
    return s


def sec_tests(wt: Path, py: str) -> Section:
    s = Section("tests")
    p = run([py, "-m", "pytest", "-q"], wt, py_env(wt), timeout=1800)
    last = [ln for ln in p.stdout.strip().splitlines() if ln.strip()][-1:] or [""]
    if p.returncode != 0:
        s.fail("pytest 실패\n" + tail(p.stdout + p.stderr, 3000))
    else:
        s.say(last[0])
    return s


# --------------------------------------------------------------------------- 4. 주입 검증

class _DeadEdit(Exception):
    """다중 편집 변이에서 앞 편집 뒤 다음 앵커가 사라졌다."""


#: 면제 표시 — ``# mutation-exempt: <이유>``. **주석 토큰만** 본다(문자열 안의 같은 글자는 표시가 아니다).
EXEMPT_RE = re.compile(r"^#\s*mutation-exempt:\s*(.*?)\s*$")
#: 이유가 이보다 짧거나 자리표시(TODO 등)면 표시가 없는 것으로 본다 — "나중에" 는 이유가 아니다.
EXEMPT_MIN_REASON = 10
EXEMPT_PLACEHOLDER = re.compile(r"^\W*(todo|fixme|tbd|xxx|later|나중에)\b", re.I)


def _comments(lines: list[str]) -> dict[int, str]:
    """줄 번호 → 그 줄의 주석(``tokenize`` 의 COMMENT 토큰). 토큰화가 안 되는 파일이면 빈 표."""
    import io
    import tokenize
    out: dict[int, str] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO("\n".join(lines) + "\n").readline):
            if tok.type == tokenize.COMMENT:
                out[tok.start[0]] = tok.string
    except (tokenize.TokenError, SyntaxError):
        return {}
    return out


def _exempt_reason(lines: list[str], lo: int, hi: int,
                   comments: dict[int, str] | None = None) -> str | None:
    """``lines[lo-1 .. hi-1]``(함수 본문) 또는 그 바로 위에 붙은 주석 줄들의 면제 이유.

    위쪽은 빈 줄을 만나면 멈춘다 — 앞 함수의 면제가 다음 함수로 번지면 안 된다."""
    comments = _comments(lines) if comments is None else comments
    cand = list(range(lo, hi + 1))
    i = lo - 1
    while i >= 1 and lines[i - 1].lstrip().startswith("#"):
        cand.append(i)
        i -= 1
    for n in cand:
        m = EXEMPT_RE.search(comments.get(n, ""))
        if m and len(m.group(1)) >= EXEMPT_MIN_REASON and not EXEMPT_PLACEHOLDER.match(m.group(1)):
            return m.group(1)
    return None


def guard_names_test(guard: str, test: str) -> bool:
    """변이의 가드 nodeid 가 이 새 검사를 가리키는가 — **파일까지** 같아야 한다.

    함수 이름만 맞대면 다른 파일의 동명 가드(지금도 9쌍 있다)가 새 검사의 변이 미선언을 덮는다. 새 검사 쪽은
    ``파일::함수`` 이고 가드는 클래스·매개변수가 붙을 수 있다(``파일::Class::test_x[a]``)."""
    g_file, _, g_rest = guard.partition("::")
    t_file, _, t_name = test.partition("::")
    return g_file == t_file and g_rest.split("::")[-1].split("[")[0] == t_name


def changed_files(wt: Path, base_sha: str) -> set[str]:
    """merge-base 이후 커밋 + 작업 트리(커밋 안 된 것 포함)에서 바뀐 경로."""
    out = set(git(wt, "diff", "--name-only", f"{base_sha}..HEAD").splitlines())
    out |= {ln[3:] for ln in porcelain(wt)}
    return {f for f in out if f}


def new_test_functions(wt: Path, base_sha: str) -> dict[str, str | None]:
    """기준 이후 **추가·수정된** 검사 함수 nodeid → 면제 이유(없으면 None).

    diff 의 변경 줄 번호를 새 파일의 AST 에 겹쳐 그 줄을 품은 ``def test_*`` 를 찾는다.
    파일 단위로 세면 "한 줄 고친 파일의 검사 40개" 가 전부 미선언으로 뜬다.
    """
    out: dict[str, str | None] = {}
    files = [f for f in changed_files(wt, base_sha)
             if f.startswith("tests/") and f.endswith(".py")]
    for f in sorted(set(files)):
        path = wt / f
        if not path.exists():
            continue
        diff = git(wt, "diff", "-U0", base_sha, "--", f)
        touched: set[int] = set()
        for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", diff, re.M):
            start, n = int(m.group(1)), int(m.group(2) or 1)
            touched.update(range(start, start + max(n, 1)))
        text = path.read_text("utf-8")
        if not touched:                      # 새 파일이면 전부 새 검사다
            touched = set(range(1, len(text.splitlines()) + 2))
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = text.splitlines()
        comments = _comments(lines)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name.startswith("test"):
                lo = min([node.lineno] + [d.lineno for d in node.decorator_list])
                hi = node.end_lineno or lo
                if touched & set(range(lo, hi + 1)):
                    out[f"{f}::{node.name}"] = _exempt_reason(lines, lo, hi, comments)
    return out


def removed_mutations(base_text: str, spec_doc: dict) -> list[str]:
    """기준에는 있었는데 지금 없는 변이 — ``[[removed]]`` 에 이유가 없으면 그 이유로 REJECT 할 문장들.

    버그가 표현 불가능해져 지우는 것은 정상이다. 다만 이유가 diff 에 남아야 한다 — 검사를 약하게 만든 PR 이 변이를
    같이 지우면 아무 신호 없이 통과했다."""
    if not base_text:
        return []
    try:
        base_ids = {m.get("id") for m in tomllib.loads(base_text).get("mutation", [])}
    except tomllib.TOMLDecodeError:
        return []
    kept = {m.get("id") for m in spec_doc.get("mutation", [])}
    why = {r.get("id"): (r.get("why") or "").strip() for r in spec_doc.get("removed", [])}
    out = []
    for mid in sorted(base_ids - kept):
        reason = why.get(mid, "")
        if len(reason) < EXEMPT_MIN_REASON or EXEMPT_PLACEHOLDER.match(reason):
            out.append(f"변이 [{mid}] 가 기준 이후 사라졌다 — 지운 이유를 mutations.toml 의 "
                       f'[[removed]] (id = "{mid}", why = "…")에 적어라. 앵커를 잃었으면 지우지 말고 앵커를 고친다')
    return out


def mutation_edits(m: dict) -> list[dict]:
    """한 변이의 편집 목록 — 단일 편집(file·find·replace)도 목록 하나로."""
    return m.get("edits") or [{"file": m.get("file"), "find": m.get("find"),
                               "replace": m.get("replace", "")}]


#: 샤드 나누기용 **예상** 비용(초, 대략). 맞을 필요는 없고 무거운 변이가 한 몫에 몰리지 않으면 된다.
#: 틀리면 샤드 사이 시간이 벌어질 뿐이고, 그건 샤드마다 찍는 실측 시간으로 보인다. 가장 비싼 가드가
#: 비용이다(주입 후 실행은 ``-x`` 라 보통 첫 가드에서 끝나지만 보수적으로).
GUARD_COST = {
    "tests/test_pipeline_e2e.py::test_pipeline_report_verifies_with_no_failures": 25.0,
    "tests/test_pipeline_e2e.py::test_layer_subset_marks_equal_the_full_run": 25.0,
    "tests/test_pipeline_e2e.py": 4.0,
    "tests/test_tui_smoke.py": 6.0,
    "tests/test_serve_reports.py::test_serve_sh_restarts_a_server_that_stops_answering": 40.0,
    "tests/test_serve_reports.py::test_serve_sh_leaves_a_healthy_server_alone": 30.0,
    "tests/test_verify_report.py": 3.0,
    "tests/test_serve_reports.py": 2.0,
    "tests/test_daily_report_flow.py": 2.0,
}


def mutation_cost(m: dict) -> float:
    cost = 1.0
    for g in m.get("guards") or []:
        cost = max(cost, GUARD_COST.get(g, GUARD_COST.get(g.split("::")[0], 1.0)))
    return cost + 1.0                         # pytest 기동


def parse_shard(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", text or "")
    if not m or not 1 <= int(m.group(1)) <= int(m.group(2)):
        raise ValueError(f"샤드는 i/n (1 ≤ i ≤ n) 이어야 한다: {text!r}")
    return int(m.group(1)), int(m.group(2))


def shard_of(muts: list[dict], i: int, n: int) -> list[dict]:
    """예상 비용이 큰 것부터 지금 가장 가벼운 몫에 넣는다(LPT). 결정적이다 — 같은 목록이면 샤드마다
    같은 배정이 나와야 몫이 겹치거나 빠지지 않는다(id 로 동률을 깬다)."""
    load = [0.0] * n
    pick: list[list[dict]] = [[] for _ in range(n)]
    for m in sorted(muts, key=lambda m: (-mutation_cost(m), str(m.get("id")))):
        k = min(range(n), key=lambda j: (load[j], j))
        load[k] += mutation_cost(m)
        pick[k].append(m)
    order = {id(m): x for x, m in enumerate(muts)}
    return sorted(pick[i - 1], key=lambda m: order[id(m)])   # 선언 순서대로 돈다


#: 이게 바뀌면 모든 가드의 동작이 바뀔 수 있다 — changed 모드도 전체를 돈다. 하네스 자신, e2e 픽스처의
#: 생산자(sector_flow.py 는 거의 모든 리포트 검사의 입력을 만든다), 템플릿은 어디에 닿는지 좁힐 수 없다.
FULL_TRIGGERS = ("pyproject.toml", "uv.lock", "tests/conftest.py", "scripts/verify_merge.py",
                 "scripts/sector_flow.py")
FULL_TRIGGER_DIRS = ("scripts/templates/",)
_MOD_REF = re.compile(r"sectorflow(?:\.\w+)+")


def module_refs(text: str) -> set[str]:
    """파이썬 소스가 참조하는 ``sectorflow`` 점 경로 — import 문(``from a.b import c`` → ``a.b.c``)과
    문자열 안의 점 경로(``python -c "from sectorflow.tui.flow_app import main"``). 파싱이 안 되면 글자로."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set(_MOD_REF.findall(text))
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("sectorflow"):
            out |= {f"{n.module}.{a.name}" for a in n.names}
        elif isinstance(n, ast.Import):
            out |= {a.name for a in n.names if a.name.startswith("sectorflow")}
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            out |= set(_MOD_REF.findall(n.value))
    return out


def _refs_hit(refs: set[str], mods: set[str]) -> bool:
    """참조가 바뀐 모듈 자체이거나 그 안의 이름(``sectorflow.tui.flow_view.State``)이면 닿는다."""
    return any(r == m or r.startswith(m + ".") for r in refs for m in mods)


def affected_guard_files(wt: Path, changed: set[str], guard_files: set[str]) -> set[str]:
    """바뀐 ``src/``·``scripts/`` 에 **닿는** 가드 파일 — import·문자열 참조를 따라간다(정적, 넉넉하게).

    src 모듈은 그걸 참조하는 src 모듈로 닫고, 그 모듈을 참조하는 scripts 의 이름까지 넓힌 뒤, 가드 파일
    본문이 그 모듈명이나 스크립트 이름을 언급하면 닿는다고 본다. 서브프로세스로 띄우는 스크립트·
    ``python -c "from sectorflow…"`` 도 글자로 남으므로 잡힌다."""
    def read(rel: str) -> str:
        try:
            return (wt / rel).read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

    def mod_of(rel: str) -> str:
        parts = rel[len("src/"):-len(".py")].split("/")
        return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)

    src_files = [p.relative_to(wt).as_posix() for p in (wt / "src").rglob("*.py")] \
        if (wt / "src").is_dir() else []
    mods = {mod_of(f) for f in changed if f.startswith("src/") and f.endswith(".py")}
    refs = {f: module_refs(read(f)) for f in src_files}
    grew = bool(mods)
    while grew:                                 # 바뀐 모듈을 참조하는 src 모듈로 닫는다
        more = {mod_of(f) for f, r in refs.items() if _refs_hit(r, mods)} - mods
        mods |= more
        grew = bool(more)
    stems = {Path(f).stem for f in changed if f.startswith("scripts/")}
    if mods and (wt / "scripts").is_dir():
        stems |= {p.stem for p in (wt / "scripts").glob("*.py")
                  if _refs_hit(module_refs(read(p.relative_to(wt).as_posix())), mods)}
    out = set()
    for g in guard_files:
        text = read(g)
        if (mods and _refs_hit(module_refs(text), mods)) or \
                any(re.search(rf"\b{re.escape(s)}\b", text) for s in stems):
            out.add(g)
    return out


def changed_scope(muts: list[dict], changed: set[str], base_muts: dict[str, dict],
                  wt: Path | None = None) -> tuple[list[dict], str]:
    """로컬 pre-push 용 — 이 브랜치가 건드린 것과 닿는 변이만.

    고르는 기준: 편집 대상 파일이 바뀌었다 · 가드 파일이 바뀌었다 · 가드가 바뀐 src 모듈·스크립트를
    (import·문자열로) 참조한다(:func:`affected_guard_files`) · 선언이 기준에 없거나 달라졌다.
    정적 참조라 동적 import 는 놓칠 수 있다 — 그래서 **전체는 GitHub 이 돈다.**"""
    hit = sorted({f for f in changed if f in FULL_TRIGGERS or f.startswith(FULL_TRIGGER_DIRS)})
    if hit:
        return list(muts), f"{', '.join(hit)} 가 바뀌어 전체"
    touched: set[str] = set()
    if wt is not None:
        touched = affected_guard_files(wt, changed, {g.split("::")[0] for m in muts
                                                     for g in m.get("guards") or []})
    out = []
    for m in muts:
        files = {e.get("file") for e in mutation_edits(m)}
        gfiles = {g.split("::")[0] for g in m.get("guards") or []}
        if files & changed or gfiles & (changed | touched) or base_muts.get(m.get("id")) != m:
            out.append(m)
    return out, f"바뀐 파일 {len(changed)}개에 닿는 변이만(가드 파일 {len(touched)}개가 바뀐 코드를 참조)"


def auto_scope(env: dict, only: set[str]) -> str:
    """로컬 ci-local(``CI`` 는 있고 ``GITHUB_ACTIONS`` 는 없다)이 주입 검증만 부르면 changed."""
    if env.get("CI") and not env.get("GITHUB_ACTIONS") and only == {"mutations"}:
        return "changed"
    return "all"


#: 기준선 실행에 붙이는 pytest 플러그인 — 검사마다 결과·시간을 JSON 으로 남긴다.
BASELINE_PLUGIN = r'''
import json, os
_R = {}


def pytest_collectreport(report):
    if report.failed:
        _R["<collect>" + report.nodeid] = {"outcome": "failed", "dur": 0.0,
                                           "msg": str(report.longrepr)[-1500:]}


def pytest_runtest_logreport(report):
    r = _R.setdefault(report.nodeid, {"outcome": "passed", "dur": 0.0, "msg": ""})
    r["dur"] += report.duration
    if report.failed:
        r["outcome"] = "failed"
        r["msg"] = str(report.longrepr)[-1500:]
    elif report.skipped and r["outcome"] == "passed":
        r["outcome"] = "skipped"


def pytest_sessionfinish(session, exitstatus):
    # 다 쓴 뒤에 이름을 바꾼다 — 쓰다 죽으면 반쪽 파일 대신 파일이 없다(하네스가 출력과 함께 보고한다).
    path = os.environ["VERIFY_MERGE_BASELINE"]
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "exit": int(exitstatus), "results": _R}, f)
    os.replace(path + ".tmp", path)
'''


def guard_items(results: dict, guard: str) -> dict:
    """가드 nodeid 에 해당하는 실행 결과들 — 파라미터(``[...]``)·클래스 하위까지."""
    return {k: v for k, v in results.items()
            if k == guard or k.startswith(guard + "[") or k.startswith(guard + "::")}


def run_baseline(wt: Path, py: str, guards: list[str], scratch: Path,
                 timeout: int = 1800) -> tuple[dict, list[str], str]:
    """모든 가드를 **pytest 한 번**에 — 결과, 못 찾은 nodeid, 출력 꼬리.

    없는 nodeid 가 하나라도 있으면 pytest 는 아무것도 안 돌리고 끝난다(종료코드 4). 그 이름을
    걷어내고 다시 돈다."""
    plug_dir = scratch / "baseline_plugin"
    plug_dir.mkdir(parents=True, exist_ok=True)
    (plug_dir / "vm_baseline.py").write_text(BASELINE_PLUGIN, "utf-8")
    env = py_env(wt)
    env["PYTHONPATH"] = str(plug_dir) + os.pathsep + env["PYTHONPATH"]
    env["VERIFY_MERGE_BASELINE"] = str(scratch / "baseline.json")
    missing: list[str] = []
    todo = list(dict.fromkeys(guards))
    out = ""
    path = scratch / "baseline.json"
    broken: dict = {}                         # 수집 오류 — 다시 돌 때도 잃지 않는다
    for _ in range(5):
        if not todo:
            break
        # 수집 오류 파일 하나가 세션 전체를 멈추면(pytest 기본) 다른 가드가 전부 "못 찾음" 이 된다.
        p = run([py, "-m", "pytest", "-q", "-p", "vm_baseline", "-p", "no:cacheprovider",
                 "--continue-on-collection-errors", *todo], wt, env, timeout=timeout)
        out = p.stdout + p.stderr
        try:
            res = json.loads(path.read_text("utf-8"))["results"] if path.exists() else {}
        except (ValueError, KeyError):
            res = {}
        path.unlink(missing_ok=True)
        broken.update({k: v for k, v in res.items() if k.startswith("<collect>")})
        # 이름을 대어 부른 nodeid 가 없으면(오타·수집 오류 파일) pytest 는 아무것도 안 돌리고 4 로 끝난다.
        named = re.findall(r"ERROR: (?:not found:|found no collectors for) (\S+)", out)
        nofile = re.findall(r"ERROR: file or directory not found: (\S+)", out)
        gone = [g for g in todo
                if any(x == g or x.endswith("/" + g) for x in named)
                or any(g.split("::")[0] == x or x.endswith("/" + g.split("::")[0])
                       for x in nofile)]
        if p.returncode == 4 and gone:
            missing += gone
            todo = [g for g in todo if g not in gone]
            continue
        if p.returncode not in (0, 1):
            # 1 = 어떤 검사가 실패했다(가드별로 판정한다). 그 밖(중단·내부 오류·사용법)은 결과를 믿지 않는다 —
            # 반쯤 돈 결과로 판정하면 안 돈 가드가 "못 찾음" 경고로 묻힌다.
            return {}, missing, out + f"\n(pytest 종료코드 {p.returncode})"
        return {**res, **broken}, missing, out
    return dict(broken), missing, out


#: 임시 폴더가 찼다 — 검사가 아니라 **환경**이 실패한 것이다. /tmp(tmpfs) 사용자 할당량이 다른 세션 때문에
#: 바닥나 Errno 122 가 "가드가 주입 전에도 실패한다"·"잡힘" 으로 읽혔다(2026-11-08). 그렇게 읽으면
#: 멀쩡한 가드를 고치러 가거나, 못 잡는 가드를 잡았다고 적는다.
#: 줄 머리의 예외만 본다 — 검사의 단언 메시지 **안에** 인용된 같은 글자(이 하네스 자신의 검사가 그렇다)는
#: 환경 실패가 아니다.
ENV_FULL_RE = re.compile(r"^(?:E\s+|(?:FAILED|ERROR) \S+ - )?\w*Error: \[Errno (?:28|122)\]", re.M)


def env_full(text: str) -> str | None:
    """출력에 임시 폴더 용량 오류가 있으면 사람에게 줄 설명."""
    if not ENV_FULL_RE.search(text or ""):
        return None
    return (f"환경: 임시 폴더 용량(Errno 28/122) — 검사 판정이 아니다. TMPDIR={tempfile.gettempdir()} "
            f"를 비우거나 `export TMPDIR=<디스크의 폴더>` 로 다시 돌려라")


#: ci.yml 의 주입 검증 matrix — 샤드 수를 읽는다(:func:`effective_budget`). tests/test_ci_workflow.py 가
#: 이 목록이 1..n 연속임을 지키므로 길이가 곧 샤드 수다.
_CI_SHARDS_RE = re.compile(r"^\s+shard: \[([\d, ]+)\]", re.M)


def ci_shard_count(wt: Path, fallback: int = 4) -> int:
    try:
        text = (wt / ".github/workflows/ci.yml").read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return fallback
    m = _CI_SHARDS_RE.search(text)
    return len(m.group(1).split(",")) if m else fallback


def effective_budget(budget_min: float | None, shard: tuple[int, int] | None,
                     wt: Path) -> tuple[float | None, str]:
    """실제로 적용할 예산(분)과 그 이유 한 줄.

    ``--budget-min`` 은 CI 샤드 **한 몫**의 예산이다. 샤드 없이 돌면(ci-local 이 sector_flow·템플릿을
    건드린 푸시에서 전체 범위로 돈다) 한 실행이 몫 n 개의 일을 하므로 n 을 곱한다 — 안 곱하면 변이가
    늘 때마다 로컬 pre-push 가 전부 REJECT 로 막힌다(2026-11-09, 692개 12.3분 > 12분)."""
    if not budget_min or shard is not None:
        return budget_min, ""
    n = ci_shard_count(wt)
    return budget_min * n, (f"예산 {budget_min * n:g}분 = --budget-min {budget_min:g}분 × 샤드 {n} — "
                            f"샤드 없는 실행은 CI 한 몫이 아니라 전체 몫의 일이다(ci.yml matrix)")


def _first_failure(stdout: str) -> str:
    return next((ln for ln in stdout.splitlines()
                 if ln.startswith("FAILED") or ln.startswith("ERROR") or " failed" in ln), "").strip()


def sec_mutations(wt: Path, py: str, base_sha: str, spec_path: Path | None,
                  shard: tuple[int, int] | None = None, budget_min: float | None = None,
                  scope: str = "all") -> Section:
    """**핵심.** 워크트리가 선언한 변이를 실제로 주입해 선언한 검사가 빨개지는가."""
    import time
    t_start = time.monotonic()
    s = Section("mutations")
    budget_min, budget_why = effective_budget(budget_min, shard, wt)
    if budget_why:
        s.say(budget_why)
    spec = spec_path or (wt / "tests/mutations.toml")
    new_tests = new_test_functions(wt, base_sha)
    if new_tests:
        s.say(f"기준 이후 추가·수정된 검사 {len(new_tests)}개")

    if not spec.exists():
        if new_tests:
            s.fail(f"변이 선언({spec}) 이 없는데 새 검사가 {len(new_tests)}개 있다 — "
                   f"이 검사들이 자기 버그를 잡는지 아무도 확인하지 않았다:\n"
                   + "\n".join("      " + t for t in sorted(new_tests)))
        else:
            s.say("변이 선언도 없고 새 검사도 없다")
        return s

    try:
        muts = tomllib.loads(spec.read_text("utf-8")).get("mutation", [])
    except tomllib.TOMLDecodeError as e:
        s.fail(f"{spec} 파싱 실패: {e}")
        return s
    if not muts:
        s.fail(f"{spec} 에 [[mutation]] 이 하나도 없다")
        return s

    # 지운 변이는 사유를 남겨야 한다 — 지우기만 하면 하네스가 볼 것이 줄어 초록이 된다(샤드 1 이 판정).
    if spec_path is None and (not shard or shard[0] == 1):
        spec_doc = tomllib.loads(spec.read_text("utf-8"))
        for msg in removed_mutations(git(wt, "show", f"{base_sha}:tests/mutations.toml"), spec_doc):
            s.fail(msg)
    # 새 검사의 변이 미선언 판정은 **선언 전체**를 본다(내 몫만 보면 다른 몫의 가드가 미선언으로 뜬다).
    all_guarded = {g for m in muts for g in m.get("guards") or []}
    declared = len(muts)
    if scope == "changed":
        try:
            rel = spec.resolve().relative_to(wt.resolve()).as_posix()
            base_text = git(wt, "show", f"{base_sha}:{rel}")
            base_muts = {m.get("id"): m for m in
                         (tomllib.loads(base_text).get("mutation", []) if base_text else [])}
        except (ValueError, tomllib.TOMLDecodeError):
            base_muts = {}
        muts, why = changed_scope(muts, changed_files(wt, base_sha), base_muts, wt)
        s.say(f"범위: changed — {why}: {len(muts)}/{declared}개 "
              f"(로컬 pre-push 용. 전체는 GitHub CI 가 돈다. --scope all 로 전체)")
        if shard:
            s.say(f"샤드 {shard[0]}/{shard[1]} 은 무시한다(changed 모드는 한 번에 돈다)")
            shard = None
    if shard:
        muts = shard_of(muts, *shard)
        s.say(f"샤드 {shard[0]}/{shard[1]}: 변이 {len(muts)}/{declared}개 "
              f"(예상 비용 {sum(mutation_cost(m) for m in muts):.0f})")

    before = porcelain(wt)
    env = py_env(wt)

    # ⓪ 선언 정합성 — 필드·대상·앵커(정확히 1번).
    runnable: list[tuple[dict, list[dict]]] = []
    for m in muts:
        mid = m.get("id", "?")
        # 한 버그가 두 군데를 동시에 바꿔야 재현되는 경우가 있다(예: 리사이즈가
        # 도움말을 닫던 버그는 `else: help=False` 와 KEY_RESIZE 가드가 한 쌍이다).
        # 한 곳만 바꾸면 다른 곳이 막아서 "검사가 못 잡는다" 는 **거짓 판정**이 난다.
        edits = mutation_edits(m)
        if "guards" not in m or any(not e.get("file") or e.get("find") is None
                                    for e in edits):
            s.fail(f"[{mid}] 필드 누락 — guards 와 (file, find) 가 필요하다")
            continue
        bad = False
        for e in edits:
            target = wt / e["file"]
            if not target.exists():
                s.fail(f"[{mid}] 대상 파일이 없다: {e['file']}")
                bad = True
                break
            n = target.read_text("utf-8").count(e["find"])
            if n == 0:
                # 예전엔 경고였다 — "리팩터인지 오타인지는 사람이 봐야 갈린다". 그 사이
                # 변이 셋이 앵커를 잃고 **조용히 죽었다**(경고는 아무도 안 읽었다).
                # 어느 쪽이든 그 회귀 검사는 지금 아무것도 증명하지 않으므로 REJECT 다.
                # 고치는 법은 같다 — 앵커를 지금 코드에 맞추거나, 버그가 표현 불가능해졌으면
                # 변이를 지운다. 같은 규칙을 tests/test_mutation_spec.py 가 pytest 에서 본다.
                s.fail(f"[{mid}] 앵커가 {e['file']} 에 없어 이 변이는 "
                       f"**돌지 않았다** — 앵커를 지금 코드에 맞추거나, 버그가 표현 "
                       f"불가능해졌으면 변이를 지워라 ({m.get('why', '')})")
                bad = True
                break
            if n != 1:
                s.fail(f"[{mid}] find 가 {e['file']} 에서 {n}번 나온다 — "
                       f"정확히 1번이어야 무엇을 바꿨는지가 확정된다")
                bad = True
                break
        if not bad:
            runnable.append((m, edits))

    # ① 깨끗한 상태에서 가드가 초록인가 — 늘 빨간 검사는 아무것도 증명하지 않는다.
    #    예전엔 변이마다 pytest 를 따로 돌렸다(302개면 302번). 가드를 모아 **한 번**에 돈다.
    scratch = Path(tempfile.mkdtemp(prefix="verify_merge.mut."))
    try:
        guards_all = [g for m, _ in runnable for g in m["guards"]]
        t0 = time.monotonic()
        results, missing, base_out = run_baseline(wt, py, guards_all, scratch) \
            if guards_all else ({}, [], "")
        if guards_all:
            s.say(f"기준선: 가드 {len(set(guards_all))}개를 pytest 한 번에 "
                  f"({time.monotonic() - t0:.0f}초)")
        if guards_all and not results and not missing:
            # 세션째 죽었다 — 임시 폴더가 찼으면 그렇게 적는다(가드별 실패는 아래에서 가드마다 가린다).
            s.fail((env_full(base_out) or "기준선 pytest 가 결과를 남기지 않았다")
                   + "\n" + tail(base_out, 3000))
            return s
        broken_files = {k[len("<collect>"):].split("::")[0]: v["msg"]
                        for k, v in results.items() if k.startswith("<collect>")}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    timings: list[tuple[float, str]] = []
    stopped = 0
    for m, edits in runnable:
        mid = m.get("id", "?")
        guards = list(m["guards"])
        if budget_min and time.monotonic() - t_start > 2 * budget_min * 60:
            stopped += 1
            continue
        gfiles_broken = [g for g in guards if g.split("::")[0] in broken_files]
        if gfiles_broken:
            s.fail(f"[{mid}] 가드 파일이 **주입 전에도** 수집되지 않는다: "
                   f"{gfiles_broken[0].split('::')[0]}\n"
                   + tail(broken_files[gfiles_broken[0].split("::")[0]], 800))
            continue
        lost = [g for g in guards if g in missing or not guard_items(results, g)]
        if lost:
            # 기준에서 물려받은 변이인데 워크트리가 그보다 먼저 갈라져 나가
            # 그 검사가 아직 없는 것일 수 있다. 오타와 구분이 안 되므로 경고다.
            s.warn(f"[{mid}] 가드를 이 워크트리에서 **찾지 못했다** — nodeid "
                   f"오타이거나, 기준이 그 검사를 워크트리 분기 뒤에 추가한 "
                   f"것이다. 머지하면 붙으므로 머지 후 다시 돌려라: {', '.join(lost)}")
            continue
        red = {k: v for g in guards for k, v in guard_items(results, g).items()
               if v["outcome"] == "failed"}
        if red:
            k, v = next(iter(red.items()))
            why = env_full(v.get("msg", ""))
            s.fail(f"[{mid}] {why}: {k}" if why else
                   f"[{mid}] 가드가 **주입 전에도** 실패한다 (이미 깨진 검사다): {k}\n"
                   + tail(v.get("msg", ""), 1200))
            continue
        if all(v["outcome"] == "skipped" for g in guards
               for v in guard_items(results, g).values()):
            s.fail(f"[{mid}] 가드가 주입 전에 **건너뛰어졌다** — 돌지 않는 검사는 아무것도 증명하지 "
                   f"않는다: {', '.join(guards)}")
            continue

        # ② 주입 → 빨개져야 한다.
        t0 = time.monotonic()
        saved: dict[Path, bytes] = {}
        try:
            for e in edits:
                target = wt / e["file"]
                # 한 파일을 두 번 고칠 수 있다 — **첫 스냅샷만** 원본이다.
                # 편집마다 새로 읽어 쌓으면 두 번째 스냅샷이 이미 변이본이라
                # 복구가 변이를 되살린다(원상복구 검사가 실제로 이걸 잡았다).
                saved.setdefault(target, target.read_bytes())
                text = target.read_text("utf-8")
                if text.count(e["find"]) != 1:
                    # 같은 파일의 앞 편집이 이 앵커를 지웠거나 하나 더 만들었다.
                    # 그대로 두면 replace 가 아무것도 안 바꾸고 "가드가 초록" 으로 끝난다.
                    raise _DeadEdit(e["file"])
                target.write_text(text.replace(e["find"], e["replace"], 1),
                                  encoding="utf-8")
            # 기준선과 같은 pytest 에 두 가지만 다르다: -x(가드 하나라도 빨개지면 잡힌 것이다 — 나머지를
            # 기다릴 이유가 없다)와 수집 오류 계속 끄기(기준선은 한 파일 오류가 다른 가드를 가리지 않게 켠다.
            # 여기서는 수집 오류 = 종료코드 2 = 판정 불가로 따로 낸다). 잡힘은 **종료코드 1 뿐**이다.
            hit = run([py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *guards],
                      wt, env, timeout=600)
        except _DeadEdit as dead:
            s.fail(f"[{mid}] 앞 편집을 넣은 뒤 {dead} 의 앵커가 정확히 1번이 아니다 — "
                   f"편집 순서나 앵커를 고쳐라")
            continue
        except subprocess.TimeoutExpired:
            hit = None
        finally:
            for target, original in saved.items():   # 무슨 일이 있어도 원상복구
                target.write_bytes(original)
        dt = time.monotonic() - t0
        timings.append((dt, mid))
        if hit is not None and hit.returncode != 0 and env_full(hit.stdout + hit.stderr):
            s.fail(f"[{mid}] {env_full(hit.stdout + hit.stderr)} — 이 변이는 판정하지 못했다")
        elif hit is None:
            # 멈춘 검사는 버그를 잡은 게 아니다 — CI 에서는 잡 시간 상한으로 죽는다.
            s.fail(f"[{mid}] 판정 불가 — 주입 후 가드가 600초 안에 끝나지 않았다. 변이가 무한 루프를 만들면 "
                   f"검사에 시간 상한을 두거나 변이를 고쳐라")
        elif hit.returncode not in (0, 1):
            # 2 수집 오류(변이가 import 를 깨뜨렸다 — 동작 버그가 아니다)·3 내부 오류·4 사용법·5 검사 없음.
            s.fail(f"[{mid}] 판정 불가 — 주입 후 pytest 종료코드 {hit.returncode}(검사 실패 1 이 아니다). "
                   f"변이가 문법·import 를 깨뜨렸으면 동작을 바꾸는 변이로 고쳐라\n"
                   + tail(hit.stdout + hit.stderr, 800))
        elif hit.returncode == 0:
            s.fail(f"[{mid}] 변이를 넣었는데 가드가 **초록이다** — 이 검사는 "
                   f"{m.get('why', '이 버그')} 를 못 잡는다\n"
                   f"      guards: {', '.join(guards)}")
        else:
            s.say(f"  [{mid}] 잡힘 {dt:.1f}초 — {m.get('why', '')} ({_first_failure(hit.stdout)})")

    after = porcelain(wt)
    if after != before:
        s.fail("주입 후 워크트리가 원상복구되지 않았다:\n"
               + "\n".join("      " + ln for ln in after[:20]))
    else:
        s.say("원상복구 확인 (git status 가 주입 전과 같다)")

    # ③ 새 검사인데 그 검사를 가드로 둔 변이가 없다 → REJECT (면제 표시에 이유가 있으면 통과).
    #    예전엔 경고였고 자기 검증도 PASS 를 못박았다 — 그 사이 경고 14건이 쌓였다(아무도 안 읽었다).
    if shard and shard[0] != 1:
        s.say("새 검사의 변이 선언 판정은 샤드 1 이 한다")
    else:
        uncovered = {t: why for t, why in new_tests.items()
                     if not any(guard_names_test(g, t) for g in all_guarded)}
        exempt = {t: why for t, why in uncovered.items() if why}
        missing_mut = sorted(t for t, why in uncovered.items() if not why)
        for t in sorted(exempt):
            s.say(f"  변이 면제: {t} — {exempt[t]}")
        if missing_mut:
            s.fail(f"변이를 하나도 선언하지 않은 새 검사 {len(missing_mut)}개 — 자기 버그를 잡는지 "
                   f"미확인. tests/mutations.toml 에 이 검사를 가드로 둔 변이를 넣거나, 변이로 증명할 "
                   f"수 없으면 검사 안·바로 위에 `# mutation-exempt: <이유>` 를 단다:\n"
                   + "\n".join("      " + t for t in missing_mut))

    # ④ 시간 — 실측을 늘 적고, 예산을 넘으면 REJECT.
    elapsed = time.monotonic() - t_start
    s.say(f"주입 검증 {len(timings)}건 · {elapsed / 60:.1f}분"
          + (f" (예산 {budget_min:g}분)" if budget_min else ""))
    for dt, mid in sorted(timings, reverse=True)[:10]:
        s.say(f"    느린 변이 {dt:6.1f}초  {mid}")
    verdict, msg = budget_verdict(elapsed, budget_min, stopped)
    if verdict == REJECT:
        s.fail(msg)
    elif verdict == WARN:
        s.warn(msg)
    return s


def budget_verdict(elapsed_s: float, budget_min: float | None, stopped: int) -> tuple[str, str]:
    """시간 예산 판정 — 멈췄거나 넘으면 REJECT, 75% 를 넘으면 WARN."""
    if not budget_min:
        return PASS, ""
    if stopped:
        return REJECT, (f"시간 예산의 두 배({2 * budget_min:g}분)를 넘어 변이 {stopped}개를 **돌리지 않고** "
                        f"멈췄다 — 샤드를 늘리거나(ci.yml matrix) 느린 가드를 줄여라")
    if elapsed_s > budget_min * 60:
        return REJECT, (f"시간 예산 초과 {elapsed_s / 60:.1f}분 > {budget_min:g}분(샤드 실행은 한 몫, "
                        f"샤드 없는 실행은 --budget-min × 샤드 수) — 샤드를 늘리거나"
                        f"(ci.yml matrix) 위 느린 변이의 가드를 줄여라. 넘긴 채로 두면 다음 PR 이 잡 시간 "
                        f"상한에 걸려 07:50 배포 관문이 막힌다")
    if elapsed_s > 0.75 * budget_min * 60:
        return WARN, (f"시간 예산의 75% 를 넘었다({elapsed_s / 60:.1f}/{budget_min:g}분) — 다음 몇 PR 안에 "
                      f"샤드를 늘려야 한다")
    return PASS, ""


# --------------------------------------------------------------------------- 5. 렌더 스모크

RENDER_SMOKE = r'''
"""실데이터 렌더 스모크 — 예외·행폭·헤더정렬·숫자잘림."""
import bisect, pathlib, sys
from collections import Counter
from sectorflow.tui.flow_app import load
from sectorflow.tui import flow_view as V

report = sys.argv[1]
d = load(report)
bad = []

# 값 기대표 — 헤더 이름 → (원본 키, 서식). **선언이지 구현이 아니다.**
# 열의 뜻이 바뀌면(예: 미실현이 숫자에서 발산 막대로) 이 표가 낡는 것이지
# 화면이 틀린 게 아니다. 그래서 표에 없는 헤더는 **구조 검사만** 받는다
# (폭·구분칸·자릿수 중간 잘림). 워크트리별 갱신은 --mutations 파일의
# [render.expect] 로 넣는다.
EXPECT = {
    "섹터": ("sector", "str"), "종목[수]": ("n_all", "int"),
    "임펄스[억]": ("flow", "amt"), "가속[%p]": ("accel", "pct2"),
    "수익률[%]": ("ret", "pct2"), "미실현[%p]": ("x", "pct1"),
    "G": ("G", "rhu2"), "선정": ("pick", "f2"),
    "종목": ("name", "clip"), "코드": ("code", "raw"),
    "순매수[억]": ("flow", "amt"), "시총대비[%p]": ("a", "pct2"),
}
if len(sys.argv) > 2 and sys.argv[2]:
    import json
    over = json.loads(pathlib.Path(sys.argv[2]).read_text("utf-8"))
    for k, v in over.items():
        if v is None:
            EXPECT.pop(k, None)          # 이 워크트리에서 뜻이 바뀐 열
        else:
            EXPECT[k] = (v[0], v[1])


def want(r, name):
    spec = EXPECT.get(name)
    if spec is None:
        return None                      # 선언이 없으면 구조 검사만
    key, how = spec
    v = r.get(key)
    if how in ("str", "clip"):
        return v if v is not None else "—"
    if how == "raw":
        return v if v is not None else ""
    if how == "int":
        return str(v if v is not None else "—")
    if how == "amt":
        return V.fmt_amt(v)
    if how == "pct1":
        return V.fmt_pct(v, 1) if v is not None else "—"
    if how == "pct2":
        return V.fmt_pct(v) if v is not None else "—"
    if how == "f2":
        return f"{v:.2f}" if v is not None else "—"
    if how == "rhu2":
        # TUI G 열은 웹과 같은 반올림(`rhu`, 0.5 는 0 에서 먼 쪽)이다(리뷰 M6). `.2f` 로 기대하면 0.595(이진
        # 0.59499…)를 0.59 로 보아, 맞게 그린 0.60 을 어긋남으로 냈다(2026-10-21 재구성 종합 G). rhu 없는 옛 워크트리는 .2f.
        if v is None:
            return "—"
        return f"{V.rhu(v, 2):.2f}" if hasattr(V, "rhu") else f"{v:.2f}"
    return None

def W(s):
    return sum(V.cell_width(c) for c in s)

_CELLS = {}

def cell_starts(line):
    """문자 i 의 시작 표시칸. 줄마다 한 번만 만든다 — 열마다 줄을 다시 훑으면
    조합 4,620개 × 열 12개 × 줄길이 200 이라 검증이 분 단위로 늘어진다."""
    v = _CELLS.get(line)
    if v is None:
        st, c = [], 0
        for ch in line:
            st.append(c)
            c += V.cell_width(ch)
        st.append(c)
        v = _CELLS[line] = st
        if len(_CELLS) > 20000:
            _CELLS.clear()
    return v

def slice_cells(line, start, width, starts=None):
    """[start, start+width) 표시칸에 **시작점이 들어오는** 문자들."""
    st = starts if starts is not None else cell_starts(line)
    lo = bisect.bisect_left(st, start, 0, len(line))
    hi = bisect.bisect_left(st, start + width, 0, len(line))
    return line[lo:hi]

def col3(c):
    """열 정의에서 (헤더, 폭, 우측정렬) 만 꺼낸다.

    워크트리마다 열 표현이 다르다 — 튜플 3칸일 수도, `Col(header,width,right,fn)`
    처럼 필드가 더 붙은 namedtuple 일 수도 있다. 3칸으로 언패킹하면 하네스가
    **워크트리의 리팩터 때문에** 터진다(실제로 터졌다). 앞 세 칸만 본다.
    """
    return c[0], c[1], c[2]

def check_block(tag, lines, width, cols, nhead, rows, want_of, state=None):
    for i, ln in enumerate(lines):
        if cell_starts(ln)[-1] != width:
            bad.append(f"{tag} 행{i} 표시폭 {cell_starts(ln)[-1]} != {width}: "
                       f"{ln[:60]!r}")
            return
    head = lines[nhead - 1]
    for c in cols:
        name, w, right = col3(c)
        span = V.col_span(cols, name)
        if span is None or span[0] + w > width:
            continue
        # 종목 목록 헤더는 창·주체마다 짧은 증거 표지를 단다(`name_head`, 3차 리뷰 D2-M1) — 화면이 그리는 글자로 본다.
        shown = V.name_head(name, state) if state is not None and hasattr(V, "name_head") else name
        if slice_cells(head, *span) != V.pad(shown, w, right):
            bad.append(f"{tag} 헤더 '{shown}' 이 자기 칸에 없다")
    spans = {c[0]: V.col_span(cols, c[0]) for c in cols}
    for r, ln in zip(rows, lines[nhead:]):
        st = cell_starts(ln)
        for c in cols:
            name, w, right = col3(c)
            span = spans[name]
            if span is None:
                continue
            start = span[0]
            # ① 열 사이 구분 공백이 살아 있는가 — 셀이 하나 빠지면 여기가 먼저 깨진다.
            if 0 < start <= width and slice_cells(ln, start - 1, 1, st) not in ("", " "):
                bad.append(f"{tag} '{name}' 앞 구분칸이 공백이 아니다: {ln[:70]!r}")
            got = slice_cells(ln, start, min(w, max(width - start, 0)), st)
            val = want_of(r, name)
            if val is None:
                continue
            if EXPECT.get(name, (None, None))[1] == "clip" and hasattr(V, "clip"):
                val = V.clip(val, w)         # 이름 열은 넘치면 `…` 로 끝난다(flow_view.clip)
            if start + w <= width:
                # ② 헤더와 셀이 같은 칸에 있는가 (strip 금지 — 여백이 어긋남을 먹는다)
                if got != V.pad(val, w, right):
                    bad.append(f"{tag} '{name}' 셀이 헤더와 어긋났다: "
                               f"{got!r} != {V.pad(val, w, right)!r}")
            elif got.strip():
                # ③ 온전히 안 들어가는 열은 **아무것도** 안 보여야 한다.
                #    반쪽 숫자는 안 보이는 것보다 나쁘다(-1,360 이 -1 로 보였다).
                bad.append(f"{tag} '{name}' 이 {got!r} 로 잘려 보인다 (참값 {val!r})")

st = V.State(d)
widths = [40, 80, 100, 132, 150, 170, 200]
n = 0
seen_sort_keys = set()

# SORT_COL·NAME_SORT_COL 이 실제 열 이름과 이어져 있는가. 헤더 이름 한 글자만
# 바꿔도 여기가 끊기고, 그래도 모든 행의 폭은 그대로라 폭 검사는 초록이다.
all_headers = set()
for _w in widths:
    for _wi in range(len(V.WINDOWS)):
        # 탐색 모드에서만 서는 열(x 계열·G)도 열이다 — 기본·탐색 둘 다 본다.
        for _ex in (False, True):
            st.wi = _wi
            if hasattr(st, "explore"):
                st.explore = _ex
            all_headers |= {c[0] for c in V.table_cols(st, _w)}
st.wi = 0
if hasattr(st, "explore"):
    st.explore = False
for _k, _h in V.SORT_COL.items():
    if _h not in all_headers:
        bad.append(f"SORT_COL[{_k}]={_h!r} 라는 열이 어느 폭에도 없다")
for _k, _h in V.NAME_SORT_COL.items():
    _ncols = V.names_cols(True) if "explore" in V.names_cols.__code__.co_varnames else V.names_cols()
    if _h not in {c[0] for c in _ncols}:
        bad.append(f"NAME_SORT_COL[{_k}]={_h!r} 라는 열이 종목 목록에 없다")
for width in widths:
    for wi in range(len(V.WINDOWS)):
        for mi in range(len(st.markets)):
            for ai in range(len(V.ACTORS)):
                for si in range(len(V.SORTS)):
                    st.wi, st.mi, st.ai, st.si = wi, mi, ai, si
                    st.row = 0
                    n += 1
                    try:
                        rows = st.rows()
                        lines, thin, nhead = V.table_lines(st, width, 40)
                        hdr = V.header_lines(st, width)
                        det = V.detail_lines(st, width)
                    except Exception as e:
                        bad.append(f"w{width} {V.WINDOWS[wi]}/{st.market}/"
                                   f"{V.ACTORS[ai][1]}/{V.SORTS[si][1]} 예외 {e!r}")
                        continue
                    if len(thin) != len(lines):
                        bad.append(f"w{width} thin 길이 {len(thin)} != 행 {len(lines)}")
                    for ln in hdr + det:
                        if W(ln) != width:
                            bad.append(f"w{width} 헤더/상세 폭 {W(ln)} != {width}")
                    cols = V._fit(V.table_cols(st, width), width)
                    tag = (f"w{width} {V.WINDOWS[wi]}/{st.market}/"
                           f"{V.ACTORS[ai][1]}/{V.SORTS[si][1]}")
                    check_block(tag, lines, width, cols, nhead, rows, want)
                    # 주체 일관성 — 화면의 임펄스·가속이 **선택된 주체**의 숫자인가.
                    # 위 want() 는 렌더가 쓴 것과 같은 투영 행에서 값을 다시 만드므로
                    # "기관 값이 그대로 남았다" 부류에는 동어반복이다. 그래서 여기서는
                    # **생 페이로드**로 되짚는다(투영을 거치지 않은 원본).
                    raw = ((d.get("blocks") or {}).get(
                        f"{V.WINDOWS[wi]}|{st.market}") or {}).get("rows") or []
                    by_sec = {x.get("sector"): x for x in raw}
                    akey = V.ACTORS[ai][0]
                    for r in rows:
                        src = by_sec.get(r.get("sector"))
                        if src is None:
                            continue
                        if r.get("flow") != src.get(akey):
                            bad.append(f"{tag} '{r.get('sector')}' 임펄스가 "
                                       f"{r.get('flow')} — 이 주체의 값은 "
                                       f"{src.get(akey)} 다 (다른 주체 값이 남았나)")
                            break
                        cap, fl = src.get("cap"), src.get(akey)
                        exp = (fl / cap * 100) if (cap and fl is not None) else None
                        if r.get("accel") != exp:
                            bad.append(f"{tag} '{r.get('sector')}' 가속이 "
                                       f"{r.get('accel')} != {exp}")
                            break
                    # 정렬 하이라이트가 **실제 정렬 열**을 가리키는가
                    span = V.sort_span(st, width)
                    key, _f = st.effective_sort(rows)
                    name = V.SORT_COL.get(key)
                    seen_sort_keys.add(key)
                    fitted = [c[0] for c in cols]
                    if span is None and name in fitted and V.WINDOWS[wi] != "종합":
                        # 헤더 이름만 바뀌어도 SORT_COL 조회가 조용히 깨진다 —
                        # 그러면 "무엇으로 줄세웠나" 표시가 사라지는데 폭 검사는
                        # 전부 초록이다(열은 여전히 제자리다).
                        bad.append(f"{tag} 정렬 열 '{name}' 이 화면에 있는데 "
                                   f"하이라이트가 없다 (SORT_COL 이 끊겼나)")
                    if span is not None:
                        if name is None:
                            bad.append(f"{tag} 정렬키 {key} 에 SORT_COL 항목이 없다")
                        elif slice_cells(lines[0], *span).strip() != name.strip():
                            bad.append(f"{tag} 정렬 하이라이트가 다른 열을 가리킨다")
                    # 종목 목록 화면
                    if si == 0:
                        # 고를 섹터가 없는 화면(실측: 종합/코스닥에 G 통과가 0개인 날)의 종목
                        # 목록은 헤더 없는 안내 한 줄이다. 그 줄을 헤더로 읽고 "헤더가 제자리에
                        # 없다" 고 하면 폭마다 거짓 위반이 3,080건 쌓인다(2026-11-02 리포트).
                        if st.selected() is None:
                            nl, nh = V.names_lines(st, width)
                            if nh != 1 or len(nl) != 1 or W(nl[0]) != width:
                                bad.append(f"{tag} 빈 종목 목록 안내가 한 줄·폭 {width} 가 아니다")
                            continue
                        for nsi in range(len(V.NAME_SORTS)):
                            st.nsi = nsi
                            try:
                                nl, nh = V.names_lines(st, width)
                            except Exception as e:
                                bad.append(f"{tag} 종목목록 예외 {e!r}")
                                continue
                            ncols = V.fit_names(width) if hasattr(V, "fit_names") else V._fit(V.names_cols(), width)
                            names = st.names()
                            check_block(tag + " 종목", nl, width, ncols, nh,
                                        names, want, st)

# 탐색 모드(`e`) — G·선정 열이 끼는 배치. 기본 모드보다 열이 많아 위치가 다 밀린다.
# 조합을 다 돌면 분 단위가 두 배가 되므로 폭 둘·정렬 하나만 본다.
if hasattr(st, "explore"):
    st.explore = True
    for width in (80, 200):
        for wi in range(len(V.WINDOWS)):
            for mi in range(len(st.markets)):
                st.wi, st.mi, st.ai, st.si, st.nsi, st.row = wi, mi, 0, 0, 0, 0
                n += 1
                tag = f"w{width} 탐색 {V.WINDOWS[wi]}/{st.market}"
                try:
                    rows = st.rows()
                    lines, thin, nhead = V.table_lines(st, width, 40)
                except Exception as e:
                    bad.append(f"{tag} 예외 {e!r}")
                    continue
                check_block(tag, lines, width, V._fit(V.table_cols(st, width), width),
                            nhead, rows, want)
                if st.selected() is None:
                    continue
                nl, nh = V.names_lines(st, width)
                check_block(tag + " 종목", nl, width, V.fit_names(width, True), nh,
                            st.names(), want, st)
    st.explore = False

for line in bad[:40]:
    print("  " + line)
# 앞 40줄만 보면 폭 40 만 틀린 것처럼 읽힌다(실제로 그렇게 오독했다). 폭별 건수를 같이 적는다.
if bad:
    by_width = Counter(line.split(" ", 1)[0] for line in bad)
    print("폭별 위반: " + " ".join(f"{k}={v}" for k, v in sorted(by_width.items())))
print(f"조합 {n}개 · 정렬키 {len(seen_sort_keys)}종 · 위반 {len(bad)}건")
sys.exit(1 if bad else 0)
'''


def sec_render(wt: Path, py: str, report: str, scratch: Path,
               spec: Path | None) -> Section:
    """실데이터 렌더 스모크.

    값 기대표는 선언이라 낡는다 — 열의 뜻이 바뀌면(미실현이 숫자에서 발산 막대로
    바뀐 것처럼) 화면이 아니라 표가 틀린 것이다. 워크트리는 변이 선언 파일의
    ``[render.expect]`` 로 갱신한다: ``"미실현[%p]" = []`` 는 "이 열은 이제
    뜻이 달라 값 검사에서 빼라"(구조 검사는 그대로 받는다)는 뜻이다.
    """
    s = Section("render")
    rpt = Path(os.path.expanduser(report))
    if not (rpt / "numbers.html").exists():
        s.skip(f"실데이터가 없다: {rpt}/numbers.html — 렌더 스모크를 못 돌린다")
        return s
    over: dict = {}
    if spec and spec.exists():
        try:
            raw = tomllib.loads(spec.read_text("utf-8")).get("render", {})
            for k, v in (raw.get("expect") or {}).items():
                over[k] = None if not v else list(v)
        except tomllib.TOMLDecodeError:
            pass                       # §mutations 가 같은 파일을 파싱하며 보고한다
    over_path = ""
    if over:
        over_path = str(scratch / "expect.json")
        Path(over_path).write_text(json.dumps(over, ensure_ascii=False), "utf-8")
        s.say(f"값 기대표 갱신 {len(over)}건: " + ", ".join(sorted(over)))
    script = scratch / "render_smoke.py"
    script.write_text(RENDER_SMOKE, encoding="utf-8")
    p = run([py, str(script), str(rpt), over_path], wt, py_env(wt), timeout=1200)
    if p.returncode != 0:
        s.fail("실데이터 렌더 위반\n" + tail(p.stdout + p.stderr, 3000))
    else:
        s.say(p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "ok")
    return s


# --------------------------------------------------------------------------- 6. pty 스모크

PTY_SMOKE = r'''
"""실제 curses 경로 — 뜨는가·q 로 나가는가·stderr 가 비었는가·40x10 에서
사는가·터미널이 사라지면 나가는가."""
import os, pty, subprocess, sys, threading, time

report = sys.argv[1]
LAUNCH = "from sectorflow.tui.flow_app import main; main()"
bad = []


def drain(primary, box):
    """pty 를 계속 읽어 버린다. 안 읽으면 버퍼(~64KB)가 차는 순간 앱이
    refresh() 의 write 에서 막히고, 앱은 q 를 못 읽는다 — 그러면 '앱이 종료하지
    않았다' 는 **앱을 가리키는 엉뚱한 메시지**로 실패한다."""
    buf = []
    try:
        while True:
            c = os.read(primary, 65536)
            if not c:
                break
            buf.append(c)
    except OSError:
        pass
    box.append(b"".join(buf).decode("utf-8", "replace"))


def launch(lines, cols, stderr=subprocess.PIPE):
    primary, replica = pty.openpty()
    env = {**os.environ, "TERM": "xterm-256color", "LINES": lines, "COLUMNS": cols}
    proc = subprocess.Popen([sys.executable, "-c", LAUNCH, "--dir", report],
                            stdin=replica, stdout=replica, stderr=stderr, env=env)
    os.close(replica)
    return primary, proc


def drive(tag, keys, lines="24", cols="120", want_screen=None):
    primary, proc = launch(lines, cols)
    box = []
    t = threading.Thread(target=drain, args=(primary, box), daemon=True)
    t.start()
    try:
        os.write(primary, keys)
        _o, err = proc.communicate(timeout=40)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.communicate(); os.close(primary); t.join(timeout=2)
        bad.append(f"{tag}: q 를 눌렀는데 종료하지 않았다")
        return
    os.close(primary); t.join(timeout=2)
    screen = box[0] if box else ""
    if proc.returncode != 0:
        bad.append(f"{tag}: 종료코드 {proc.returncode}\n"
                   + err.decode("utf-8", "replace")[-1200:])
    if err.strip():
        bad.append(f"{tag}: stderr 가 비어 있지 않다\n"
                   + err.decode("utf-8", "replace")[-1200:])
    if want_screen and want_screen not in screen:
        bad.append(f"{tag}: 화면에 {want_screen!r} 가 없다")


drive("기본 80x24", b"wmas\x1b[B\n\x1b" b"qq", cols="80", want_screen="섹터 자금")
drive("좁은 40x10", b"wmas?\x1b\n\x1bqq", lines="10", cols="40")
drive("넓은 200x50", b"wwmmaassjjkk\n\x1b" b"?" b"q" b"q", lines="50", cols="200")
# 버퍼 압력 — 키를 많이 넣으면 출력이 64KB 를 넘긴다.
drive("긴 키 시퀀스", b"wwwwmmmaaaassss" * 8 + b"?\x1b" + b"\n\x1b" + b"q")

# 터미널이 사라지면(SSH 끊김) 나가는가. 여기서는 **배수 스레드를 쓰지 않는다** —
# 읽는 중에 같은 fd 를 닫으면 경합이라 앱이 아니라 테스트 사정으로 끝날 수 있다.
primary, proc = launch("24", "120", stderr=subprocess.DEVNULL)
time.sleep(0.6)
os.close(primary)
try:
    proc.communicate(timeout=12)
except subprocess.TimeoutExpired:
    ticks = "?"
    try:
        stat = open(f"/proc/{proc.pid}/stat").read().split()
        ticks = int(stat[13]) + int(stat[14])
    except OSError:
        pass
    proc.kill(); proc.communicate()
    bad.append(f"터미널 소멸: 앱이 계속 돈다 — CPU 좀비 ({ticks} 틱)")

for b in bad:
    print("  " + b)
print(f"pty 시나리오 5개 · 위반 {len(bad)}건")
sys.exit(1 if bad else 0)
'''


def sec_pty(wt: Path, py: str, report: str, scratch: Path) -> Section:
    s = Section("pty")
    if sys.platform == "win32":
        s.skip("pty 는 POSIX 전용")
        return s
    rpt = Path(os.path.expanduser(report))
    if not (rpt / "numbers.html").exists():
        s.skip(f"실데이터가 없다: {rpt}/numbers.html")
        return s
    script = scratch / "pty_smoke.py"
    script.write_text(PTY_SMOKE, encoding="utf-8")
    p = run([py, str(script), str(rpt)], wt, py_env(wt), timeout=600)
    if p.returncode != 0:
        s.fail("pty 스모크 위반\n" + tail(p.stdout + p.stderr, 3000))
    else:
        s.say(p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "ok")
    return s


# --------------------------------------------------------------------------- 판정

ALL = ("worktree", "identity", "lint", "tests", "mutations",
       "render", "pty")


def verify(wt: Path, base: str, report: str, only: set[str], py: str,
           spec: Path | None, shard: tuple[int, int] | None = None,
           budget_min: float | None = None,
           scope: str = "all") -> tuple[str, list[Section]]:
    secs: list[Section] = []
    scratch = Path(tempfile.mkdtemp(prefix="verify_merge."))
    try:
        s0, commits = sec_worktree(wt, base, py)
        secs.append(s0)
        if s0.status == REJECT:
            return REJECT, secs
        base_sha = git(wt, "merge-base", base, "HEAD")
        table = {
            "identity": lambda: sec_identity(wt, commits),
            "lint": lambda: sec_lint(wt, py, base_sha, scratch),
            "tests": lambda: sec_tests(wt, py),
            "mutations": lambda: sec_mutations(wt, py, base_sha, spec, shard,
                                               budget_min, scope),
            "render": lambda: sec_render(wt, py, report, scratch, spec),
            "pty": lambda: sec_pty(wt, py, report, scratch),
        }
        for name in ALL[1:]:
            if name in only:
                secs.append(table[name]())
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    verdict = REJECT if any(s.status == REJECT for s in secs) else PASS
    return verdict, secs


def render_report(wt: Path, verdict: str, secs: list[Section]) -> str:
    out = ["", "=" * 78, f"머지 검증 — {wt}", "=" * 78]
    for s in secs:
        out.append("")
        out.append(f"[{s.status:6}] {s.name}")
        for ln in s.lines:
            out.append("    " + ln.replace("\n", "\n    "))
    warns = [s.name for s in secs if s.status == WARN]
    skips = [s.name for s in secs if s.status == SKIP]
    out += ["", "-" * 78]
    if warns:
        out.append(f"애매한 항목(사람이 읽어야 함): {', '.join(warns)}")
    if skips:
        out.append(f"못 돌린 항목: {', '.join(skips)}")
    out.append(f"판정: {verdict}")
    out.append("주의 — 이 하네스는 '선언된 변이가 잡히는가' 까지만 본다. "
               "선언 자체가 얕으면(자기 구현을 베낀 동어반복 검사) 통과한다. "
               "코드와 보고서는 사람이 읽어야 한다.")
    out.append("-" * 78)
    return "\n".join(out)


# --------------------------------------------------------------------------- 자기 검증

SELF_SCENARIOS = """일부러 깨진 워크트리를 만들어 하네스가 정말 REJECT 를 내는지 본다.
늘 PASS 하는 하네스는 장식이다."""

#: 자기 검증이 주입하는 변이 — 가속 열이 다른 값을 그린다. 앵커가 지금 코드에 있어야
#: 시나리오가 **맞는 이유로** 판정된다(예전 앵커 ``pad(fmt_pct(...accel...))`` 는 리팩터로
#: 사라져, 눈먼 검사 시나리오가 "가드가 초록" 이 아니라 "앵커 없음" 으로 걸렸다).
#: ``tests/test_mutation_spec.py`` 가 이 앵커도 본다.
SELF_TEST_FILE = "src/sectorflow/tui/flow_view.py"
SELF_TEST_FIND = '    Col("가속[%p]", 9, True, _num("accel")),'
SELF_TEST_REPLACE = '    Col("가속[%p]", 9, True, _num("ret")),'
SELF_TEST_GUARD = "tests/test_tui_flow_view.py::test_data_cells_sit_under_their_headers"


def _self_spec(guard: str, find: str = SELF_TEST_FIND) -> str:
    return ("[[mutation]]\n"
            'id = "accel-cell-shows-wrong-field"\n'
            'why = "가속 열이 다른 값을 그린다"\n'
            f"guards = [{json.dumps(guard)}]\n"
            f"file = {json.dumps(SELF_TEST_FILE)}\n"
            f"find = {json.dumps(find, ensure_ascii=False)}\n"
            f"replace = {json.dumps(SELF_TEST_REPLACE, ensure_ascii=False)}\n")


def _scratch_repo(dst: Path, base: str, mail: str, name: str) -> None:
    """기준 커밋의 트리를 새 git 저장소로 복제한다(원본 워크트리를 안 건드린다)."""
    dst.mkdir(parents=True, exist_ok=True)
    tar = subprocess.run(["git", "archive", base], cwd=str(REPO),
                         capture_output=True, timeout=120)
    subprocess.run(["tar", "-x", "-C", str(dst)], input=tar.stdout, timeout=120)
    for a in (["init", "-q", "-b", "wt"], ["config", "user.email", mail],
              ["config", "user.name", name], ["add", "-A"],
              ["commit", "-q", "-m", "base"]):
        subprocess.run(["git", *a], cwd=str(dst), capture_output=True, timeout=120)
    subprocess.run(["git", "branch", "-f", base.replace("/", "-") or "base"],
                   cwd=str(dst), capture_output=True, timeout=60)


def self_test(py: str, report: str) -> int:
    print(SELF_SCENARIOS)
    mail = git(REPO, "config", "--get", "user.email") or "a@b.c"
    name = git(REPO, "config", "--get", "user.name") or "x"
    root = Path(tempfile.mkdtemp(prefix="verify_merge.selftest."))
    results: list[tuple[str, str, str, bool]] = []

    def scenario(tag: str, only: set[str], want: str, setup,
                 by: str | None = None) -> None:
        """`by` = 그 판정을 내야 하는 항목. 엉뚱한 이유로 난 REJECT 는 통과가 아니다
        (예: 하네스 자신이 레포 검사를 깨서 pytest 가 빨개진 것)."""
        wt = root / tag
        _scratch_repo(wt, "HEAD", mail, name)
        base_branch = "base"
        subprocess.run(["git", "branch", "-f", base_branch], cwd=str(wt),
                       capture_output=True, timeout=60)
        setup(wt)
        got, secs = verify(wt, base_branch, report, only, py, None)
        detail = "; ".join(f"{s.name}={s.status}" for s in secs)
        right_place = by is None or any(
            s.name == by and s.status in (REJECT, WARN) for s in secs)
        ok = got == want and right_place
        results.append((tag, f"{want}@{by or '*'}", f"{got}@{detail}", ok))
        print(f"  {tag}: 기대 {want}({by or '전체'}) · 실제 {got}  [{detail}]")
        if not ok:
            for s in secs:
                for ln in s.lines:
                    print("      " + ln[:200])

    def commit(wt: Path, msg: str, mail_override: str | None = None) -> None:
        subprocess.run(["git", "add", "-A"], cwd=str(wt), capture_output=True)
        env = dict(os.environ)
        if mail_override:
            env |= {"GIT_AUTHOR_EMAIL": mail_override,
                    "GIT_COMMITTER_EMAIL": mail_override}
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=str(wt),
                       capture_output=True, env=env)

    # ① 대조군 — 손 안 댄 기준 트리는 PASS 여야 한다(늘 REJECT 도 장식이다).
    scenario("clean", {"identity", "lint", "tests", "mutations"},
             PASS, lambda wt: None)

    # ② 테스트 실패
    def break_test(wt: Path) -> None:
        (wt / "tests/test_zz_broken.py").write_text(
            "def test_broken():\n    assert False, '일부러 깨뜨림'\n", "utf-8")
        commit(wt, "broken test")
    scenario("broken-test", {"tests"}, REJECT, break_test, by="tests")

    # ③ 신원 위반 — 회사 이메일이 새어 들어간 커밋
    def bad_identity(wt: Path) -> None:
        (wt / "README.md").write_text(
            (wt / "README.md").read_text("utf-8") + "\n", "utf-8")
        commit(wt, "leak", mail_override="someone@corp.example.com")
    scenario("bad-identity", {"identity"}, REJECT, bad_identity,
             by="identity")

    # ④ 변이를 못 잡는 검사 — 이 하네스의 존재 이유
    def blind_guard(wt: Path) -> None:
        (wt / "tests/test_zz_blind.py").write_text(
            '"""폭만 보는 검사 — 셀이 통째로 빠져도 초록이다."""\n'
            "from sectorflow.tui.flow_view import cell_width\n\n\n"
            "def test_blind():\n"
            "    assert cell_width('가') == 2\n", "utf-8")
        (wt / "tests/mutations.toml").write_text(
            _self_spec("tests/test_zz_blind.py::test_blind"), "utf-8")
        commit(wt, "blind guard")
    scenario("blind-guard", {"mutations"}, REJECT, blind_guard,
             by="mutations")

    # ⑤ 진짜 잡는 검사 — 같은 변이를 기존 정렬 검사에 걸면 REJECT 가 아니어야 한다.
    def real_guard(wt: Path) -> None:
        (wt / "tests/mutations.toml").write_text(
            _self_spec(SELF_TEST_GUARD), "utf-8")
        commit(wt, "real guard")
    scenario("real-guard", {"mutations"}, PASS, real_guard)

    # ⑤-2 앵커가 사라진 변이 — 예전엔 경고로 넘어가 변이 셋이 조용히 죽었다. REJECT 여야 한다.
    def dead_anchor(wt: Path) -> None:
        (wt / "tests/mutations.toml").write_text(
            _self_spec(SELF_TEST_GUARD, find="이 앵커는 어디에도 없다"), "utf-8")
        commit(wt, "dead anchor")
    scenario("dead-anchor", {"mutations"}, REJECT, dead_anchor, by="mutations")

    def _one_mutation(wt: Path) -> None:
        """⑥·⑦ 은 새 검사 판정만 본다 — 변이 302개를 시나리오마다 다시 주입할 이유가 없다."""
        (wt / "tests/mutations.toml").write_text(_self_spec(SELF_TEST_GUARD), "utf-8")

    # ⑥ 변이 미선언 새 검사 → REJECT. 예전엔 경고였고(이 시나리오가 PASS 를 못박았다) 그 사이
    #    미선언 검사 14개가 쌓였다.
    def undeclared(wt: Path) -> None:
        _one_mutation(wt)
        (wt / "tests/test_zz_new.py").write_text(
            "def test_new_but_undeclared():\n    assert True\n", "utf-8")
        commit(wt, "new test, no mutation")
    scenario("undeclared-test", {"mutations"}, REJECT, undeclared,
             by="mutations")

    # ⑦ 같은 새 검사에 이유 있는 면제 표시 → PASS. 이유 없는 표시는 ⑥ 과 같다(REJECT).
    def exempted(wt: Path) -> None:
        _one_mutation(wt)
        (wt / "tests/test_zz_new.py").write_text(
            "# mutation-exempt: 픽스처 자체를 보는 검사라 주입할 소스가 없다\n"
            "def test_new_but_exempt():\n    assert True\n", "utf-8")
        commit(wt, "new test, exempt")
    scenario("exempt-test", {"mutations"}, PASS, exempted)

    def bare_exempt(wt: Path) -> None:
        _one_mutation(wt)
        (wt / "tests/test_zz_new.py").write_text(
            "def test_new_bare_exempt():  # mutation-exempt:\n    assert True\n", "utf-8")
        commit(wt, "new test, exempt without reason")
    scenario("bare-exempt-test", {"mutations"}, REJECT, bare_exempt, by="mutations")

    shutil.rmtree(root, ignore_errors=True)
    ok = all(r[3] for r in results)
    print("\n자기 검증: " + ("전 시나리오 기대대로" if ok else "**기대와 다름**"))
    for tag, want, got, good in results:
        print(f"  {'ok ' if good else 'BAD'} {tag}: 기대 {want} 실제 {got}")
    return 0 if ok else 1


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("worktree", nargs="?", help="검증할 워크트리 경로")
    ap.add_argument("--base", default=DEFAULT_BASE, help="기준 브랜치/커밋")
    ap.add_argument("--report-dir", default=DEFAULT_REPORT, help="실데이터 리포트 폴더")
    ap.add_argument("--only", default="", help="돌릴 항목 (쉼표): " + ",".join(ALL[1:]))
    ap.add_argument("--python", default=None, help="쓸 인터프리터")
    ap.add_argument("--mutations", default=None,
                    help="변이 선언 파일 (기본: 워크트리의 tests/mutations.toml)")
    ap.add_argument("--self-test", action="store_true",
                    help="일부러 깨진 워크트리로 하네스가 REJECT 를 내는지 확인")
    ap.add_argument("--shard", default="",
                    help="주입 검증을 n 몫으로 나눠 i 번째만: i/n (CI matrix). changed 범위에선 무시")
    ap.add_argument("--budget-min", type=float, default=None,
                    help="주입 검증 시간 예산(분). 넘으면 REJECT, 75%% 넘으면 WARN, 두 배면 멈춘다")
    ap.add_argument("--scope", choices=("auto", "all", "changed"), default="auto",
                    help="주입할 변이 범위. auto: CI 이고 GITHUB_ACTIONS 가 아니며(로컬 ci-local) "
                         "--only mutations 이면 changed(merge-base 이후 바뀐 파일에 닿는 변이만), "
                         "그 밖은 all. 전체는 GitHub CI 가 돈다")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test(interpreter(REPO, a.python), a.report_dir)
    if not a.worktree:
        ap.error("워크트리 경로가 필요하다 (또는 --self-test)")

    wt = Path(a.worktree).resolve()
    only = set(x.strip() for x in a.only.split(",") if x.strip()) or set(ALL[1:])
    unknown = only - set(ALL)
    if unknown:
        ap.error(f"모르는 항목: {', '.join(sorted(unknown))}")
    spec = Path(a.mutations).resolve() if a.mutations else None
    scope = auto_scope(os.environ, only) if a.scope == "auto" else a.scope
    shard = None
    if a.shard:
        try:
            shard = parse_shard(a.shard)
        except ValueError as e:
            # ci-local 은 ``${{ matrix.shard }}`` 를 못 풀어 글자 그대로 넘긴다 — changed 범위에선
            # 샤드를 어차피 안 쓰므로 넘어간다. 전체 범위(GitHub)에서 틀린 샤드는 오류다.
            if scope != "changed":
                ap.error(str(e))
    verdict, secs = verify(wt, a.base, a.report_dir, only,
                           interpreter(wt, a.python), spec, shard, a.budget_min, scope)
    print(render_report(wt, verdict, secs))
    return 0 if verdict == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
