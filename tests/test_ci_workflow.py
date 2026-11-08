"""CI 워크플로의 샤드·예약 설정이 **조용히 빠지지 않는가** — yaml 의존성 없이 글자로 본다.

샤드 수를 두 곳(matrix 목록과 ``--shard i/n`` 의 n)에 적으면 한쪽만 줄일 때 몫 하나가 아무 말 없이
안 돈다(4/4 가 빠진 3잡이 모두 초록). 분모는 ``strategy.job-total`` 로 matrix 를 따르게 하고, 여기서
목록이 1..n 연속이고 matrix 축이 shard 하나뿐인지(축이 둘이면 job-total 이 곱이 된다) 본다.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CI = (REPO / ".github" / "workflows" / "ci.yml").read_text("utf-8")
SELFTEST = (REPO / ".github" / "workflows" / "harness-selftest.yml").read_text("utf-8")


def _job(text: str, name: str) -> str:
    """최상위 jobs 아래 ``  <name>:`` 블록(다음 두 칸 들여쓴 잡 전까지)."""
    m = re.search(rf"^  {name}:\n(.*?)(?=^  \S|\Z)", text, re.S | re.M)
    assert m, f"잡 {name} 이 없다"
    return m.group(1)


def test_mutation_shards_cover_1_to_n_and_the_denominator_follows_the_matrix():
    job = _job(CI, "mutations")
    matrix = re.search(r"^      matrix:\n((?:        .*\n)+)", job, re.M)
    assert matrix, "mutations 잡에 matrix 가 없다"
    axes = re.findall(r"^        (\w[\w-]*):", matrix.group(1), re.M)
    assert axes == ["shard"], f"matrix 축이 shard 하나여야 job-total 이 샤드 수다: {axes}"
    shards = [int(x) for x in re.search(r"shard: \[([\d, ]+)\]", matrix.group(1)).group(1).split(",")]
    assert shards == list(range(1, len(shards) + 1)), f"샤드 목록이 1..n 연속이 아니다: {shards}"
    assert "SHARD: ${{ matrix.shard }}/${{ strategy.job-total }}" in job
    assert '--shard "$SHARD"' in job and "--budget-min" in job
    assert "fail-fast: false" in job          # 한 몫이 실패해도 나머지 몫의 결과를 본다


def test_weekly_selftest_can_never_leave_a_red_check():
    """예약 실행은 main 머리 커밋에 체크로 붙고, ci-gate 는 빨간 체크 하나에 배포를 멈춘다."""
    job_timeout = int(re.search(r"^    timeout-minutes: (\d+)", SELFTEST, re.M).group(1))
    steps = re.split(r"^      - ", SELFTEST, flags=re.M)[1:]
    for st in steps:
        if st.startswith("uses: actions/checkout"):
            continue                           # 체크아웃 실패는 GitHub 쪽 사정이다
        assert "continue-on-error: true" in st, f"실패가 잡을 빨갛게 만드는 스텝: {st.splitlines()[0]}"
    selftest = next(st for st in steps if "--self-test" in st)
    step_timeout = int(re.search(r"timeout-minutes: (\d+)", selftest).group(1))
    assert step_timeout < job_timeout - 10, (step_timeout, job_timeout)


def test_weekly_selftest_runs_the_browser_smoke_strictly_without_blocking():
    """주간 잡이 진짜 Chromium 웹 스모크를 돈다 — 건너뛰지 않게(REQUIRED), 실패는 이슈로, 종료코드는 tee 가 안 삼키게.

    PR CI 에는 브라우저가 없어 브라우저 검사가 늘 건너뛴다. 이 스텝이 빠지거나 REQUIRED 가 빠지면 아무도 브라우저로
    페이지를 열어 보지 않는데 모든 체크가 초록이다. 스텝이 잡을 빨갛게 만들 수 없는 것은 위 검사가 본다.
    """
    steps = re.split(r"^      - ", SELFTEST, flags=re.M)[1:]
    smoke = next((st for st in steps if "pytest -q tests/test_web_smoke.py" in st), None)
    assert smoke, "주간 잡에 웹 스모크 스텝이 없다"
    assert "playwright install --with-deps chromium" in smoke
    assert 'SF_WEB_SMOKE_REQUIRED: "1"' in smoke, "REQUIRED 없이는 Playwright 가 안 깔려도 건너뛰고 초록이다"
    assert "set -o pipefail" in smoke, "| tee 가 pytest 의 실패 종료코드를 삼킨다"
    issue = next(st for st in steps if "gh issue create" in st)
    assert "steps.smoke.outcome == 'failure'" in issue, "스모크 실패가 이슈로 안 간다"
    total = sum(int(x) for x in re.findall(r"^        timeout-minutes: (\d+)", SELFTEST, re.M))
    job_timeout = int(re.search(r"^    timeout-minutes: (\d+)", SELFTEST, re.M).group(1))
    assert total < job_timeout - 10, (total, job_timeout)   # 스텝 상한의 합이 잡 상한에 닿으면 잡이 시간 초과로 빨개진다


def test_every_tee_step_in_our_workflows_sets_pipefail():
    """``cmd | tee log`` 의 종료코드는 tee 의 것이다 — GitHub 기본 bash(``bash -e {0}``)는 pipefail 이 꺼져 있어
    앞 명령이 실패해도 스텝이 초록이다. 주간 자기 검증이 그렇게 조용히 통과하고 있었다(2026-11-08).
    ``| tee`` 를 쓰는 run 블록은 ``set -o pipefail``(또는 ``set -euo pipefail``) 을 tee 줄보다 **앞에** 둔다.
    """
    def run_blocks(text: str):
        """``run: |`` 블록 — ``run:`` 키보다 깊게 들여쓴 줄(빈 줄 포함)까지."""
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            m = re.match(r"^(\s*)(?:- )?run: \|\s*$", ln)
            if not m:
                continue
            ind, body = len(m.group(1)), []
            for nxt in lines[i + 1:]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= ind:
                    break
                body.append(nxt)
            yield "\n".join(body)

    wf = sorted((REPO / ".github" / "workflows").glob("*.yml"))
    n = 0
    for f in wf:
        for block in run_blocks(f.read_text("utf-8")):
            lines = [ln.split("#", 1)[0] for ln in block.splitlines()]
            tee = [i for i, ln in enumerate(lines) if re.search(r"\|\s*tee\b", ln)]
            if not tee:
                continue
            n += 1
            pf = [i for i, ln in enumerate(lines) if re.search(r"\bset -\w*o pipefail\b|\bset -o pipefail\b", ln)]
            assert pf and pf[0] < tee[0], f"{f.name}: | tee 를 쓰는 run 블록에 그보다 앞선 set -o pipefail 이 없다:\n{block}"
    assert n >= 2, "| tee 스텝을 하나도 못 찾았다 — 정규식이 헛돈다"


# ── 소모를 줄인 범위 나누기가 배포 관문(`pull-all --ci-gate`)을 뚫지 않는가 ─────────────────────────────
# 관문은 머리 커밋의 체크만 본다: success·skipped·neutral 이면 통과, 0개·진행 중이면 보류. 설계는 ci.yml 머리말.


def test_ci_has_no_workflow_level_filter_and_plan_always_runs():
    """``paths`` 로 워크플로째 건너뛰면 머리엔 신원 검사만 남아, 빨간 코드 위에 문서 커밋을 얹어 관문을 지나간다."""
    on = re.search(r"^on:\n(.*?)(?=^\S)", CI, re.S | re.M).group(1)
    assert "push:" in on and "pull_request:" in on
    assert "branches: [main]" in on                              # 머지 커밋에 체크가 붙는 자리
    for word in ("paths", "paths-ignore", "branches-ignore", "tags"):
        assert word not in on, f"{word} 를 on: 에 쓰면 체크 없는 main 커밋이 생긴다"
    plan = _job(CI, "plan")
    assert not re.search(r"^    if:", plan, re.M), "plan 을 건너뛰면 그 뒤 잡이 다 건너뛰어 초록이다"
    assert "needs: plan" not in plan
    for job in ("test", "mutations", "mutations-pr"):
        assert "needs: plan" in _job(CI, job), job


def test_skipping_fails_closed_and_main_runs_every_mutation():
    """plan 출력이 비어도 안 건너뛴다. 문서 판정이 아니면 main 은 전체 변이다 — 범위 옵션이 끼면 관문의 보호가 줄어든다."""
    main, pr = _job(CI, "mutations"), _job(CI, "mutations-pr")
    assert "if: ${{ github.event_name != 'pull_request' && needs.plan.outputs.kind != 'docs' }}" in main
    assert "if: ${{ github.event_name == 'pull_request' && needs.plan.outputs.kind != 'docs' }}" in pr
    assert "== 'code'" not in CI, "출력이 비면 건너뛰게 되는 조건이다"
    assert "--scope" not in main                                 # GITHUB_ACTIONS 에서 auto 는 all 이다
    assert "--scope direct" in pr and "--budget-min" in pr
    # 잡 상한 안에서 하네스가 먼저 멈춰야 한다(예산의 두 배에서 멈춘다) — 이유 없는 시간 초과로 죽지 않게.
    budget = float(re.search(r"--budget-min (\d+)", pr).group(1))
    assert 2 * budget < int(re.search(r"^    timeout-minutes: (\d+)", pr, re.M).group(1))


def test_main_docs_judgement_compares_to_the_last_green_main_commit():
    """직전 커밋과 견주면 '빨간 코드 → 문서' 가 초록으로 보인다. 초록 기록을 묻는 조회와 그 권한이 있어야 한다."""
    plan = _job(CI, "plan")
    for flag in ("--workflow ci.yml", "--branch main", "--event push", "--status success"):
        assert flag in plan, flag
    assert 'scripts/ci_plan.py' in plan and '--verified "$verified"' in plan
    assert "actions: read" in plan                               # 없으면 조회가 실패해 늘 전체를 돈다 — 조용한 비용 누수


def test_superseding_a_run_cannot_cancel_the_head():
    """끊기는 것은 같은 이벤트의 앞 커밋뿐이어야 한다 — 머리의 실행이 cancelled 면 관문이 보류한다."""
    assert re.search(r"^concurrency:\n  group: ci-\$\{\{ github\.event_name \}\}-\$\{\{ github\.ref \}\}\n  cancel-in-progress: true",
                     CI, re.M)


def test_local_ci_keeps_its_python_and_skips_ci_only_steps():
    """dotfiles ci-local 은 이 파일을 읽어 돈다. matrix 가 식이면 판을 못 읽어 3.13 으로 바뀌고, 식 없는 run 은 로컬에서도 돈다."""
    assert re.search(r'^env:\n(?:  #.*\n)*  PYTHON_VERSION: "3\.12"', CI, re.M)
    assert "${{" in re.search(r"- name: Plan\n.*", _job(CI, "plan"), re.S).group(0).split("run: |", 1)[1]
    assert "${{" in re.search(r"run: .*--scope direct.*", _job(CI, "mutations-pr")).group(0)
    assert "${{" not in re.search(r"run: (uv run --no-sync python scripts/verify_merge\.py .*)", _job(CI, "mutations")).group(1)


def test_every_job_in_our_workflows_has_a_time_limit():
    """상한이 없으면 GitHub 기본은 6시간 — 멈춘 잡 하나가 분을 다 태운다."""
    n = 0
    for f in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        body = f.read_text("utf-8").split("\njobs:\n", 1)[1]
        for m in re.finditer(r"^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)", body, re.S | re.M):
            n += 1
            assert re.search(r"^    timeout-minutes: \d+", m.group(2), re.M), f"{f.name}: {m.group(1)}"
    assert n >= 6, "잡을 못 찾았다 — 정규식이 헛돈다"
