#!/usr/bin/env bash
# research/validation 스크립트를 DB 접속정보(읽기 전용으로 쓴다)를 불러 레포 뿌리에서 돌린다.
#   research/validation/run.sh research/validation/q3cd/load_long.py
# KR_QUANT_DB 가 이미 있으면 그대로, 없으면 ~/git/quant-airflow/.env 를 불러 쓴다(값은 찍지 않는다).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
if [ -z "${KR_QUANT_DB:-}" ] && [ -f "$HOME/git/quant-airflow/.env" ]; then
  set -a; . "$HOME/git/quant-airflow/.env"; set +a
fi
cd "$REPO"
exec uv run -q --extra pg --with scipy python "$@"
