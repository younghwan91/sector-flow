"""``scripts/rebuild_history.py`` — 지난 기준일을 차례로 재구성하는 운전기.

세 층으로 돌린다: 함수를 직접(요약 읽기·평일·코드 rev), 가짜 리포트 스크립트로 제어 흐름(상태 분류·--force·
--jobs·같은 루트 재실행), 그리고 **진짜** ``daily_report.sh`` 를 sqlite 픽스처 위에서(휴장 3·관문 막힘·REBUILT).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "rebuild_history.py"


def _mod(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


RH = _mod("rebuild_history", SCRIPT)


# ── 함수 ──────────────────────────────────────────────────────────────────

def test_weekdays_skip_weekends_and_keep_order():
    assert RH.weekdays("2026-10-09", "2026-10-13") == ["2026-10-09", "2026-10-12", "2026-10-13"]


def test_verify_summary_counts_failures_and_claims_separately(tmp_path):
    assert RH.verify_summary(tmp_path) == ("-", "")                       # VERIFY.txt 없음
    (tmp_path / "VERIFY.txt").write_text(
        "…\n검사 176건 · 실패 1건 · 문서 주장 어긋남 2건\n실패 목록: H/키\n", "utf-8")
    assert RH.verify_summary(tmp_path) == ("176건 중 실패 1 · 주장 2", "H/키")
    (tmp_path / "VERIFY.txt").write_text("검사 10건 · 실패 0건 · 문서 주장 어긋남 0건\n전부 통과\n", "utf-8")
    assert RH.verify_summary(tmp_path) == ("10건 중 실패 0", ""), "주장 0건을 적었다"
    (tmp_path / "VERIFY.txt").write_text("깨진 파일", "utf-8")
    assert RH.verify_summary(tmp_path)[0] == "형식 불명"


#: 임시 레포의 git 은 사용자 설정에 기대지 않는다 — CI·깨끗한 HOME 에는 전역 신원이 없고, 전역 훅·서명이 끼어들면
#: 안 된다(tests/test_verify_merge.py 와 같은 방식). 신원은 그 임시 레포의 **로컬 설정**에만 둔다(시험 픽스처).
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], env=GIT_ENV, capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """origin/main 을 흉내 낸 작은 체크아웃. 커밋은 commit-tree 로 만든다(훅·신원 설정을 건드리지 않는다)."""
    r = tmp_path / "co"
    r.mkdir()
    _git(r, "init", "-q")
    for k, v in (("user.name", "rebuild-history test"), ("user.email", "test@example.invalid"),
                 ("commit.gpgsign", "false"), ("core.hooksPath", os.devnull)):
        _git(r, "config", k, v)
    (r / "a.txt").write_text("1")
    _git(r, "add", "a.txt")
    c1 = _git(r, "commit-tree", _git(r, "write-tree"), "-m", "one")
    _git(r, "update-ref", "refs/heads/main", c1)
    _git(r, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(r, "update-ref", "refs/remotes/origin/main", c1)
    _git(r, "reset", "-q", "--hard", "main")
    monkeypatch.setattr(RH, "REPO", r)
    monkeypatch.delenv("KR_QUANT_CODE_REV", raising=False)
    return r, c1


def test_code_rev_is_the_full_sha_of_a_clean_origin_main_commit(checkout):
    r, c1 = checkout
    assert RH.code_rev() == c1 and len(c1) == 40


def test_code_rev_refuses_unmerged_dirty_or_non_git_code_unless_allowed(checkout, tmp_path, monkeypatch):
    """운영 재구성 48일이 어느 브랜치에도 없는 사본 커밋으로 지어졌다 — REBUILT.txt 의 코드는 다시 볼 수 있어야 한다."""
    r, c1 = checkout
    c2 = _git(r, "commit-tree", _git(r, "write-tree"), "-p", c1, "-m", "two")   # origin/main 에 없는 커밋
    _git(r, "update-ref", "refs/heads/main", c2)
    with pytest.raises(SystemExit, match="origin/main 에 없다"):
        RH.code_rev()
    assert RH.code_rev(allow_unmerged=True) == f"{c2}-unmerged"
    _git(r, "update-ref", "refs/heads/main", c1)
    (r / "a.txt").write_text("고침")                                         # 커밋 안 한 변경
    with pytest.raises(SystemExit, match="커밋 안 한 변경"):
        RH.code_rev()
    assert RH.code_rev(allow_unmerged=True) == f"{c1}-dirty"
    monkeypatch.setattr(RH, "REPO", tmp_path / "사본")                        # git archive 사본
    (tmp_path / "사본").mkdir()
    with pytest.raises(SystemExit, match="git 체크아웃이 아니다"):
        RH.code_rev()
    monkeypatch.setenv("KR_QUANT_CODE_REV", "abc")
    assert RH.code_rev(allow_unmerged=True) == "abc"
    with pytest.raises(SystemExit):                                          # 막히면 아무것도 짓지 않는다
        RH.main(["--root", str(tmp_path / "r"), "--dates", "2026-10-12", "--report-script", "/bin/false"])
    assert not (tmp_path / "r").exists(), "막혔는데 루트를 만들었다"


def test_needs_a_date_range(tmp_path):
    with pytest.raises(SystemExit):
        RH.main(["--allow-unmerged", "--root", str(tmp_path / "r")])
    assert not (tmp_path / "r").exists(), "날짜가 없는데 루트부터 만들었다"


# ── 가짜 리포트 스크립트로 제어 흐름 ───────────────────────────────────────

FAKE = r"""#!/bin/sh
echo "$KR_QUANT_ASOF|${KR_QUANT_FORCE:-}" >> "$CALLS"
d="$KR_QUANT_REPORTS/$KR_QUANT_ASOF"
case "$KR_QUANT_ASOF" in
  *-09) exit 3 ;;
  2026-10-13) echo "[..] 재구성 2026-10-13 에 수급이 없는데 업종지수·일봉은 9행 있다 — 수집 공백이다" >&2; exit 1 ;;
  2026-10-14) echo "Traceback: boom" >&2; exit 1 ;;
esac
mkdir -p "$d"; : > "$d/numbers.html"; : > "$d/payload.json"; echo "원본은 x" > "$d/REBUILT.txt"
printf '검사 10건 · 실패 0건 · 문서 주장 어긋남 1건\n전부 통과\n' > "$d/VERIFY.txt"
"""


@pytest.fixture
def fake(tmp_path):
    f = tmp_path / "daily_report.sh"
    f.write_text(FAKE)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return f


def _rows(root: Path) -> dict:
    lines = (root / ".rebuild" / "summary.tsv").read_text("utf-8").splitlines()
    assert lines[0].split("\t") == ["date", "rc", "state", "verify", "failed", "secs"]
    return {r.split("\t")[0]: r.split("\t") for r in lines[1:]}


def test_states_force_and_claims_reach_the_summary(tmp_path, fake, monkeypatch, capsys):
    calls = tmp_path / "calls"
    monkeypatch.setenv("CALLS", str(calls))
    root = tmp_path / "root"
    rc = RH.main(["--allow-unmerged", "--report-script", str(fake), "--root", str(root), "--force",
                  "--dates", "2026-10-14", "2026-10-12", "2026-10-13", "2026-10-09"])
    assert rc == 1                                                         # 공백·오류가 있다
    seen = [ln.split("|") for ln in calls.read_text().splitlines()]
    assert [s[0] for s in seen] == ["2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"], "--dates 도 오래된 날부터"
    assert all(s[1] == "1" for s in seen), "--force 를 KR_QUANT_FORCE 로 안 넘겼다"
    by = _rows(root)
    assert by["2026-10-09"][2] == "휴장" and by["2026-10-09"][1] == "3"
    assert by["2026-10-12"][2] == "재구성" and by["2026-10-12"][3] == "10건 중 실패 0 · 주장 1"
    assert by["2026-10-13"][2] == "수집 공백", "수급 공백을 오류·휴장으로 적었다"
    assert by["2026-10-14"][2] == "오류"
    out = capsys.readouterr().out
    assert "| 2026-10-12 | 0 | 재구성 | 10건 중 실패 0 · 주장 1 |" in out and "문제 2건" in out


def test_jobs_refuse_a_root_that_already_has_those_days(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("CALLS", str(tmp_path / "calls"))
    root = tmp_path / "root"
    (root / "2026-10-12").mkdir(parents=True)
    with pytest.raises(SystemExit):
        RH.main(["--allow-unmerged", "--report-script", str(fake), "--root", str(root), "--jobs", "2",
                 "--from", "2026-10-12", "--to", "2026-10-16"])
    assert not (tmp_path / "calls").exists(), "거절하기 전에 짓기 시작했다"


def test_jobs_rerun_in_the_same_root_keeps_every_first_pass(tmp_path, fake, monkeypatch):
    """같은 루트에서 같은 초에 다시 돌려도 덩어리 첫날의 첫 판이 서로를 덮지 않는다. 휴장뿐인 덩어리는 건너뛴다."""
    monkeypatch.setenv("CALLS", str(tmp_path / "calls"))
    monkeypatch.setattr(RH.time, "strftime", lambda fmt: "20261108120000")
    root = tmp_path / "root"
    (root / "2026-10-08").mkdir(parents=True)                              # 구간 앞의 리포트
    (root / "2026-10-08" / "payload.json").write_text("{}")
    args = ["--allow-unmerged", "--report-script", str(fake), "--root", str(root), "--jobs", "2", "--dates",
            "2026-10-09", "2026-10-12"]                                     # 덩어리 0 = 휴장뿐
    assert RH.main(args) == 0
    kept = sorted(p.name for p in (root / ".rebuild" / "first-pass").iterdir())
    assert kept == ["2026-10-12.20261108120000"]
    shutil.move(str(root / "2026-10-12"), str(tmp_path / "moved"))          # 그날을 비우고 같은 초에 다시
    assert RH.main(args) == 0
    kept = sorted(p.name for p in (root / ".rebuild" / "first-pass").iterdir())
    assert kept == ["2026-10-12.20261108120000", "2026-10-12.20261108120000.1"], "첫 판을 덮었다"
    assert (root / "latest").resolve() == (root / "2026-10-12").resolve()


# ── 진짜 daily_report.sh 를 sqlite 위에서 ─────────────────────────────────

FLOW = _mod("daily_report_flow", REPO / "tests" / "test_daily_report_flow.py")
FAKE_VERIFY_COUNTS = """
import sys
print("검사 12건 · 실패 0건 · 문서 주장 어긋남 0건")
print("전부 통과")
"""


@pytest.fixture
def real_repo(tmp_path):
    r = tmp_path / "sector-flow"
    (r / "scripts").mkdir(parents=True)
    for name in ("daily_report.sh", "_env.sh", "completeness_gate.py", "finalize_done.py"):
        shutil.copy(REPO / "scripts" / name, r / "scripts" / name)
    (r / "scripts" / "sector_flow.py").write_text(FLOW.FAKE_SECTOR_FLOW)
    (r / "scripts" / "sector_numbers.py").write_text(FLOW.FAKE_NUMBERS)
    (r / "scripts" / "verify_report.py").write_text(FAKE_VERIFY_COUNTS)
    (r / "scripts" / "trust_page.py").write_text(FLOW.FAKE_TRUST)
    (r / ".env").write_text(f"KR_QUANT_DB={tmp_path / 'db.sqlite'}\nKR_QUANT_DIGEST=0\n")
    uv = tmp_path / "uv"
    uv.write_text(f'#!/bin/sh\nshift 5\nexec "{sys.executable}" "$@"\n')
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    return r


def _finalize_runs(root: Path, *ended: str) -> Path:
    """실행마다 따로 둔 daily_finalize 기록(끝난 때는 KST) — 창(달력 3일)이 기준일을 덮어야 확정이다."""
    for i, when in enumerate(ended):
        run = root / "dag_id=daily_finalize" / f"run_id=scheduled__{i}"
        for task in FLOW.FINALIZE_TASKS:
            f = run / f"task_id={task}" / "attempt=1.log"
            f.parent.mkdir(parents=True, exist_ok=True)
            FLOW.write_attempt(f, when)
    return root


@pytest.mark.skipif(sys.platform == "win32", reason="bash 스크립트")
def test_real_daily_report_rebuilds_in_order_skips_holidays_and_names_gate_blocks(real_repo, tmp_path):
    """진짜 daily_report.sh: 평일만 부르고, 토·일은 아예 안 부르며, 관문에 막힌 날(원본 있음)은 '수집 미완' 이다."""
    db = tmp_path / "db.sqlite"
    FLOW._seed(db, today_kosdaq=1)                                          # 마지막 날(11-02) 코스닥이 비었다
    origin = tmp_path / "old"
    (origin / FLOW.TODAY).mkdir(parents=True)
    root = tmp_path / "root"
    (root / FLOW.TODAY).mkdir(parents=True)                                 # 원본이 있는 날 — INCOMPLETE.txt 를 안 남긴다
    (root / FLOW.TODAY / "numbers.html").write_text("<옛 정의>")
    env = FLOW._env(real_repo, today="2026-11-08",
                    KR_QUANT_AIRFLOW_LOGS=str(_finalize_runs(tmp_path / "airflow", "2026-10-31 07:20", "2026-11-04 07:20")))
    p = subprocess.run([sys.executable, str(SCRIPT), "--allow-unmerged", "--report-script", str(real_repo / "scripts" / "daily_report.sh"),
                        "--root", str(root), "--from", "2026-10-29", "--to", FLOW.TODAY,
                        "--origin-root", str(origin), "--origin-label", "reports/.archive/x"],
                       capture_output=True, text=True, env=env, timeout=300)
    assert p.returncode == 1, p.stdout + p.stderr                           # 11-02 관문 막힘
    by = _rows(root)
    assert list(by) == ["2026-10-29", "2026-10-30", "2026-11-02"], "주말(10-31·11-01)을 불렀다"
    assert by["2026-10-29"][2] == by["2026-10-30"][2] == "재구성"
    assert by["2026-10-29"][3] == "12건 중 실패 0"
    assert by["2026-11-02"][2] == "수집 미완", p.stdout
    assert (root / FLOW.TODAY / "numbers.html").read_text() == "<옛 정의>", "막힌 날의 원본을 건드렸다"
    note = (root / "2026-10-30" / "REBUILT.txt").read_text()
    assert "확정 데이터" in note and "원본은 " in note
    assert (root / "2026-10-30" / "FINAL.txt").is_file() and not (root / "2026-10-30" / "INCOMPLETE.txt").exists()
    # 수집을 메운 뒤 다시 돌리면 이미 만든 날은 건너뛰고(이어서) 막혔던 날만 만든다.
    FLOW._seed(db, today_kosdaq=50)
    p = subprocess.run([sys.executable, str(SCRIPT), "--allow-unmerged", "--report-script", str(real_repo / "scripts" / "daily_report.sh"),
                        "--root", str(root), "--from", "2026-10-29", "--to", FLOW.TODAY],
                       capture_output=True, text=True, env=env, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "이미 재구성" in (root / ".rebuild" / "2026-10-29.log").read_text()
    assert (root / FLOW.TODAY / "REBUILT.txt").is_file()
    assert os.path.realpath(root / "latest") == str((root / FLOW.TODAY).resolve())


KEPT_FAKE = r"""#!/bin/sh
d="$KR_QUANT_REPORTS/$KR_QUANT_ASOF"
r="$KR_QUANT_REPORTS/.rejected/$KR_QUANT_ASOF.20261119000000.1"
mkdir -p "$d" "$r"; : > "$d/numbers.html"; echo "원본은 x" > "$d/REBUILT.txt"
printf '검사 10건 · 실패 0건 · 문서 주장 어긋남 0건\n전부 통과\n' > "$d/VERIFY.txt"
printf '검사 10건 · 실패 2건 · 문서 주장 어긋남 0건\n실패 목록: B/키\n' > "$r/VERIFY.txt"
echo "[..] ⚠️ 새 판이 검증에 걸려 게시하지 않았다 — 검증을 통과한 $d 와 latest 를 그대로 둔다. 걸린 판: $r/VERIFY_FAILED.txt" >&2
exit 1
"""


def test_a_rebuild_kept_aside_is_a_verify_failure_not_a_rebuild(tmp_path):
    """새 판이 검증에 걸려 통과한 게시본을 남겼으면 폴더는 옛 판이다 — 표시 파일만 보면 '재구성' 으로 적혔다."""
    f = tmp_path / "daily_report.sh"
    f.write_text(KEPT_FAKE)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    root = tmp_path / "root"
    rc = RH.main(["--allow-unmerged", "--report-script", str(f), "--root", str(root), "--dates", "2026-10-15"])
    assert rc == 1
    row = _rows(root)["2026-10-15"]
    assert row[2] == "검증 실패(게시본 유지)", row
    assert row[3] == "10건 중 실패 2" and row[4] == "B/키", "걸린 판이 아니라 남긴 게시본의 검증을 적었다"
