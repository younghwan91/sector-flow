#!/usr/bin/env bash
# freshness_check.sh — scripts/freshness_check.py 를 크론 환경에서 부른다(uv·.env 는 daily_report.sh 와 같은 _env.sh).
# 종료코드는 파이썬 그대로: 0 정상(달력의 휴장 포함) · 75 경고(검증 실패 게시·전 거래일 미확정·SLO 위반·원장 공백·배포 지연·알림 웹훅 없음/죽음) ·
# 1 오류(데이터는 왔는데 리포트가 안 걸림·달력상 거래일인데 수급 없음·DB 실패).
# 크론은 평일 20:30 에 cron_run.sh 로 감싸 부른다(deploy/crontab.simnode).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
# alert.sh 는 .env 를 불러온 환경에서 돌지 않는다 — 크론 환경에 정의된 값(빈 값 포함), 없으면 quant-airflow/.env 의
# `^KEY=` 줄만 본다. 아래 _env.sh 가 .env 를 불러오면 둘이 섞이므로, 그 전의 크론 환경을 넘겨 두고
# 파이썬(freshness_check.alert_value)이 alert.sh 와 같은 값을 읽게 한다.
export SF_CRON_ENV=1
for k in ALERT_WEBHOOK_URL HEARTBEAT_URL SECTOR_FLOW_ALERT; do
  if [ -n "${!k+x}" ]; then export "SF_CRON_$k=${!k}"; fi
done
. "$REPO/scripts/_env.sh"
exec "${UVR[@]}" python scripts/freshness_check.py "$@"
