"""research/validation 공용 — 경로, 읽기 전용 DB 연결, 옛 리포트 코드 꺼내기.

각 연구 스크립트는 맨 위에서 이 파일의 폴더를 sys.path 에 넣고 가져온다(패키지로 묶지 않는다).

- 산출물: ``<연구 폴더>/out/`` (gitignore). ``SF_VALIDATION_OUT`` 을 주면 그 아래 ``<연구 이름>/`` 로 간다.
- 게시본: ``KR_QUANT_REPORTS`` (daily_report.sh 와 같은 이름), 없으면 ``<레포>/reports``.
- DB: ``KR_QUANT_DB`` — ``set -a; . ~/git/quant-airflow/.env; set +a`` 로 불러 쓴다. 값을 찍지 않는다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DB_OPTS = "-c statement_timeout=120000 -c default_transaction_read_only=on"
# 첫 검증(Q1~Q3, 첫 Q3d)이 돌린 리포트 코드 — 사전등록 커밋. 결함 1~3 이 든 판이다.
FIRST_RUN_REV = "bd27017"


def out_dir(study_file: str, *sub: str) -> Path:
    study = Path(study_file).resolve().parent
    base = os.environ.get("SF_VALIDATION_OUT")
    root = Path(base) / study.name if base else study / "out"
    p = root.joinpath(*sub)
    p.mkdir(parents=True, exist_ok=True)
    return p


def reports_dir() -> Path:
    return Path(os.environ.get("KR_QUANT_REPORTS") or REPO / "reports")


def connect_ro():
    import psycopg2

    if not os.environ.get("KR_QUANT_DB"):
        sys.exit("KR_QUANT_DB 없음 — set -a; . ~/git/quant-airflow/.env; set +a 뒤에 돌린다")
    return psycopg2.connect(os.environ["KR_QUANT_DB"], options=DB_OPTS)


def report_code(rev: str | None, dest: Path) -> Path:
    """리포트 코드(scripts/sector_flow.py·sector_numbers.py, src/sectorflow)가 든 폴더를 돌려준다.

    rev 가 None 이면 이 작업 트리 그대로, 아니면 그 커밋의 파일을 dest 에 꺼낸다. 돌려준 폴더들을
    sys.path 앞에 넣으면 ``import sector_numbers`` · ``from sectorflow import storage`` 가 그 판을 집는다.
    """
    if rev is None:
        return REPO / "scripts"
    files = ("scripts/sector_flow.py", "scripts/sector_numbers.py",
             "src/sectorflow/__init__.py", "src/sectorflow/storage.py")
    for f in files:
        txt = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{f}"], check=True,
                             capture_output=True, text=True).stdout
        tgt = dest / (f.split("/", 1)[1] if f.startswith("src/") else Path(f).name)
        tgt.parent.mkdir(parents=True, exist_ok=True)
        tgt.write_text(txt, encoding="utf-8")
    return dest


def use_report_code(rev: str | None, dest: Path) -> None:
    """report_code 의 폴더를 import 경로 맨 앞에 둔다(옛 판이면 설치된 sectorflow 도 가린다)."""
    p = report_code(rev, dest)
    sys.path.insert(0, str(p))
    if rev is None:
        sys.path.insert(1, str(REPO / "src"))
