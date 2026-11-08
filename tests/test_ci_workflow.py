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
