"""CI 워크플로의 잡·예산·예약 설정이 **조용히 빠지지 않는가** — yaml 의존성 없이 글자로 본다.

머지당 청구 분이 상한을 넘는 변경은 여기서 빨개진다. 잡을 늘리거나 예산을 올리려면 ``scripts/ci_plan.py`` 표를 같이 고쳐야 한다.
"""
from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CI = (REPO / ".github" / "workflows" / "ci.yml").read_text("utf-8")
SELFTEST = (REPO / ".github" / "workflows" / "harness-selftest.yml").read_text("utf-8")
PAGES = (REPO / ".github" / "workflows" / "pages.yml").read_text("utf-8")


def _job(text: str, name: str) -> str:
    """최상위 jobs 아래 ``  <name>:`` 블록(다음 두 칸 들여쓴 잡 전까지)."""
    m = re.search(rf"^  {name}:\n(.*?)(?=^  \S|\Z)", text, re.S | re.M)
    assert m, f"잡 {name} 이 없다"
    return m.group(1)


def _load_plan():
    spec = importlib.util.spec_from_file_location("ci_plan_t", REPO / "scripts" / "ci_plan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _jobs(text: str) -> set[str]:
    return set(re.findall(r"^  ([\w-]+):\n", text.split("\njobs:\n", 1)[1], re.M))


def _timeout(job: str) -> int:
    return int(re.search(r"^    timeout-minutes: (\d+)", job, re.M).group(1))


def test_code_merge_stays_under_the_minutes_ceiling():
    """정지 재발 방지 — 머지 한 번(PR + main)의 청구 분 표가 상한 이하이고, 머리말의 숫자가 그 표와 같다."""
    plan = _load_plan()
    assert plan.MERGE_MINUTES_MAX == 80
    assert plan.merge_minutes() <= plan.MERGE_MINUTES_MAX, plan.MERGE_JOBS
    assert f"코드 머지당 **{plan.merge_minutes()}분 이하**(상한 {plan.MERGE_MINUTES_MAX}" in CI, "ci.yml 머리말이 표와 다르다"
    assert "37.3분" not in CI.split("on:\n", 1)[0] or "이전:" in CI.split("on:\n", 1)[0]


def test_every_job_is_on_the_minutes_table():
    """새 잡을 표에 안 올리면 청구 분이 몰래 는다. 표에만 있는 잡은 표를 부풀린다. 신원 검사도 머지마다 돈다."""
    plan = _load_plan()
    identity = (REPO / ".github" / "workflows" / "commit-identity.yml").read_text("utf-8")
    both = {"plan", "check-identity"}
    assert _jobs(CI) | _jobs(identity) | _jobs(PAGES) == set(plan.MERGE_JOBS["pull_request"]) | set(plan.MERGE_JOBS["push"])
    assert set(plan.MERGE_JOBS["pull_request"]) == both | {"test", "mutations-pr"}
    assert set(plan.MERGE_JOBS["push"]) == both | {"gate", "pages"}


def test_full_gate_is_one_job_and_its_budget_adds_up():
    """전체 검증을 잡으로 나누면 설치·기준선이 몫마다 돈다(8샤드 합 기준선 약 20분). 표·하네스 예산·잡 상한의 산수를 맞춘다."""
    plan = _load_plan()
    gate = _job(CI, "gate")
    assert "strategy:" not in gate and "matrix" not in gate, "gate 를 matrix 로 나누면 머지당 분이 표와 달라진다"
    run = re.search(r"run: (uv run --no-sync python scripts/verify_merge\.py .*--only mutations.*)", gate).group(1)
    budget = float(re.search(r"--budget-min (\d+)", run).group(1))
    assert "--scope" not in run and "--shard" not in run         # 전체 범위. GITHUB_ACTIONS 에서 auto 는 all 이다
    assert budget < plan.MERGE_JOBS["push"]["gate"], "표가 하네스 예산보다 작다 — 넘기 전에 안 빨개진다"
    assert 2 * budget < _timeout(gate), "하네스가 멈추기 전에 잡이 이유 없이 죽는다"
    assert "--full-baseline" in run, "기준선이 전체 pytest 를 겸하지 않으면 main 에 검사 한 벌이 없다"
    assert int(re.search(r"--workers (\d+)", run).group(1)) == 2, "러너 2코어(PRIVATE) — 표의 예상이 동시 2 다"
    assert re.search(r'python-version: "(.*)"', gate).group(1) == plan.PY_GATE
    pr = _job(CI, "mutations-pr")
    pr_budget = float(re.search(r"--budget-min (\d+)", pr).group(1))
    assert 2 * pr_budget + 1 <= plan.MERGE_JOBS["pull_request"]["mutations-pr"], "PR 범위는 예산의 두 배에서 멈춘다 — 표가 그만큼이어야 한다"
    assert 2 * pr_budget < _timeout(pr)
    test = _job(CI, "test")
    assert plan.MERGE_JOBS["pull_request"]["test"] <= _timeout(test) * len(plan.PY_PR)


def test_gate_and_test_conditions_are_exact_complements():
    """둘 다 안 도는 main 커밋이 있으면 체크는 초록인데 아무 검증도 없다 — 머리 커밋이 전체 변이를 안 지나는 구멍이다."""
    assert "if: ${{ github.event_name == 'pull_request' || needs.plan.outputs.kind == 'docs' }}" in _job(CI, "test")
    assert "if: ${{ github.event_name != 'pull_request' && needs.plan.outputs.kind != 'docs' }}" in _job(CI, "gate")


def test_screen_layers_run_on_a_demo_built_from_this_code():
    """실 리포트가 없는 러너에서 화면 층이 SKIP 이면 새 표지·새 열을 아무도 안 본다. 옛 사본을 쓰면 새 표지가 가려진다."""
    for job in ("test", "gate"):
        assert re.search(r"verify_merge\.py \. .*--only render,pty --demo", _job(CI, job)), job
    assert "${{" in re.search(r"run: .*--only render,pty.*", _job(CI, "test")).group(0), "ci-local 이 화면 층을 두 번 돈다"
    assert "${{" not in re.search(r"run: .*--only render,pty.*", _job(CI, "gate")).group(0), "ci-local 이 화면 층을 안 돈다"


def test_floor_python_syntax_runs_before_merge():
    """3.11 전체 pytest 는 머지 뒤로 옮겼다. 3.11 전용 실패의 흔한 부류(새 문법)만은 머지 전에 본다."""
    step = next(st for st in re.split(r"^      - ", _job(CI, "test"), flags=re.M) if st.startswith("name: Floor python syntax"))
    assert "--python 3.11" in step and "compileall" in step and "src scripts tests" in step


def test_ci_memory_cap_fits_the_runner():
    """PRIVATE 러너는 2코어·8GB 다. 트리 상한(프로세스 상한의 두 배)이 러너를 넘으면 러너째 죽어 잡 로그가 안 남는다."""
    mb = int(re.search(r'^  SECTOR_FLOW_MEM_LIMIT_MB: "(\d+)"', CI, re.M).group(1))
    assert 2500 <= mb <= 3500, "평소 피크 약 2GB 보다 넉넉하고 트리 합이 8GB 러너 안에 든다"
    assert "SECTOR_FLOW_TREE_LIMIT_MB" not in CI                  # 기본(두 배)을 쓴다
    assert 2 * mb < 7000
    diag = next(st for st in re.split(r"^      - ", _job(CI, "gate"), flags=re.M) if st.startswith("name: Memory diagnosis"))
    assert "failure()" in diag and "continue-on-error: true" in diag and "dmesg" in diag
    assert "${{" in diag.split("run: |", 1)[1], "ci-local 이 이 스텝을 돌면 sudo 가 비밀번호를 묻는다"


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
    for job in ("test", "gate", "mutations-pr"):
        assert "needs: plan" in _job(CI, job), job


def test_skipping_fails_closed_and_main_runs_every_mutation():
    """plan 출력이 비어도 안 건너뛴다. 문서 판정이 아니면 main 은 전체 변이다 — 범위 옵션이 끼면 관문의 보호가 줄어든다."""
    main, pr = _job(CI, "gate"), _job(CI, "mutations-pr")
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
    assert "${{" not in re.search(r"run: (uv run --no-sync python scripts/verify_merge\.py .*--only mutations.*)", _job(CI, "gate")).group(1)


def test_every_job_in_our_workflows_has_a_time_limit():
    """상한이 없으면 GitHub 기본은 6시간 — 멈춘 잡 하나가 분을 다 태운다."""
    n = 0
    for f in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        body = f.read_text("utf-8").split("\njobs:\n", 1)[1]
        for m in re.finditer(r"^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)", body, re.S | re.M):
            n += 1
            assert re.search(r"^    timeout-minutes: \d+", m.group(2), re.M), f"{f.name}: {m.group(1)}"
    assert n >= 6, "잡을 못 찾았다 — 정규식이 헛돈다"


# ── 공개 랜딩(pages.yml) — 합성 데모만, 검사 뒤 배포, 랜딩 사정으로 배치 배포를 멈추지 않는다 ────────────────────
def _steps(text: str) -> list[str]:
    return re.split(r"^      - ", text, flags=re.M)[1:]


def test_pages_deploys_only_from_main_and_only_a_fresh_demo():
    """PR 에서는 배포하지 않는다. 사이트는 이 코드로 새로 지은 데모로만 — 받은·리포트 폴더를 넘길 자리(--demo)가 없다."""
    on = re.search(r"^on:\n(.*?)(?=^\S)", PAGES, re.S | re.M).group(1)
    assert "branches: [main]" in on and "pull_request" not in on
    build = next(st for st in _steps(PAGES) if "site/build.py" in st)
    assert "--demo" not in build and "reports" not in PAGES, "공개 사이트에 폴더를 넘기는 길이 생겼다"
    job = _job(PAGES, "pages")
    assert "pages: write" in job and "id-token: write" in job
    assert re.search(r"^permissions:\n  contents: read", PAGES, re.M), "워크플로 기본 권한은 읽기만"


def test_pages_checks_the_public_surface_before_upload_and_the_check_can_turn_red():
    """검사가 올리기보다 먼저고, 검사·짓기의 실패는 빨갛다(데모가 아닌 것이 나갈 뻔했으면 배포를 멈출 일이다)."""
    steps = _steps(PAGES)
    idx = {k: next(i for i, st in enumerate(steps) if k in st)
           for k in ("site/build.py", "tests/test_public_surface.py", "upload-pages-artifact", "deploy-pages")}
    assert idx["site/build.py"] < idx["tests/test_public_surface.py"] < idx["upload-pages-artifact"] < idx["deploy-pages"]
    for k in ("site/build.py", "tests/test_public_surface.py", "upload-pages-artifact"):
        assert "continue-on-error" not in steps[idx[k]], k
    assert "path: _site" in steps[idx["upload-pages-artifact"]]


def test_pages_outage_warns_instead_of_blocking_the_batch_deploy():
    """배포 관문은 머리 커밋의 체크가 다 초록이어야 받는다 — Pages 설정·장애로 배치 배포까지 멈추면 안 된다. 대신 경고를 남긴다."""
    steps = _steps(PAGES)
    deploy = next(st for st in steps if "deploy-pages" in st)
    assert "continue-on-error: true" in deploy and "id: deploy" in deploy
    warn = next((st for st in steps if "steps.deploy.outcome == 'failure'" in st), None)
    assert warn and "::warning" in warn
    assert re.search(r"^concurrency:\n  group: pages\n  cancel-in-progress: false", PAGES, re.M), \
        "머리의 배포가 끊기면 cancelled 라 관문이 보류한다"


def _identity_script() -> tuple[str, dict]:
    """commit-identity.yml 의 검사 스텝을 PR 이벤트로 풀어 낸다 — 셸 본문과 env(허용 목록·소유자 이름)."""
    import textwrap
    wf = (REPO / ".github" / "workflows" / "commit-identity.yml").read_text("utf-8")
    step = wf.split("- name: 커밋 신원 검사\n", 1)[1]
    env_txt, run_txt = step.split("        run: |\n", 1)
    env = {}
    for key in ("ALLOWED_EMAILS", "OWNER_NAMES"):
        block = env_txt.split(f"          {key}: |\n", 1)[1]
        lines = []
        for ln in block.splitlines():
            if not ln.startswith("            "):
                break
            lines.append(ln.strip())
        env[key] = "\n".join(lines) + "\n"
    return textwrap.dedent(run_txt), env


def test_identity_check_flags_owner_leaks_but_not_contributors(tmp_path):
    """소유자 이름에 허용 밖 주소면 빨강, 바깥 기여자는 통과 — 기여자 PR 이 required 검사에 영원히 막히던 것(#111)."""
    import subprocess
    script, env = _identity_script()
    repo = tmp_path / "r"
    repo.mkdir()

    def g(*a, name="x", email="x@example.com"):
        # 전역 설정·훅(신원 허용 목록 pre-commit)을 끊는다 — 이 저장소는 가짜 신원을 일부러 만든다
        e = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
             "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
        return subprocess.run(["git", *a], cwd=repo, env=e, check=True, capture_output=True, text=True).stdout.strip()

    g("init", "-q")
    g("commit", "-q", "--allow-empty", "-m", "base", name="Younghwan Chae", email="chyohw97@gmail.com")
    base = g("rev-parse", "HEAD")

    def verdict(name: str, email: str) -> int:
        g("checkout", "-q", "--detach", base)
        g("commit", "-q", "--allow-empty", "-m", "c", name=name, email=email)
        head = g("rev-parse", "HEAD")
        body = (script.replace("${{ github.event_name }}", "pull_request")
                .replace("${{ github.event.pull_request.base.sha }}", base)
                .replace("${{ github.event.pull_request.head.sha }}", head))
        return subprocess.run(["bash", "-c", body], cwd=repo, env={**os.environ, **env},
                              capture_output=True, text=True).returncode

    assert verdict("Younghwan Chae", "chyohw97@gmail.com") == 0
    assert verdict("Younghwan Chae", "someone@corp.example") == 1, "소유자 이름에 다른 주소 — 2026-08-29 사고"
    assert verdict("A Contributor", "123+contrib@users.noreply.github.com") == 0, "기여자 신원은 그 사람 것이다"
