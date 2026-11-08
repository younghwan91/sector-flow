#!/usr/bin/env bash
# freshness_check.sh — scripts/freshness_check.py 를 크론 환경에서 부른다(uv·.env 는 daily_report.sh 와 같은 _env.sh).
# 종료코드는 파이썬 그대로: 0 정상 · 75 경고(오늘 수급 없음·알림 웹훅 없음) · 1 오류(데이터는 왔는데 리포트가 안 걸림·DB 실패).
# 크론은 평일 20:30 에 cron_run.sh 로 감싸 부른다(deploy/crontab.simnode).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
. "$REPO/scripts/_env.sh"
exec "${UVR[@]}" python scripts/freshness_check.py "$@"
