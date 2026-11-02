#!/usr/bin/env bash
# cron_run.sh <이름> -- <명령...>
#
# 크론 줄 하나를 감싼다(quant-airflow scripts/cron_run.sh 와 같은 규약). 출력은 그대로 stdout 으로
# 흘려 크론의 `>> log 2>&1` 이 지금처럼 동작하고, 종료코드가 0 이 아니면 꼬리 30줄을 alert.sh 로
# 보낸 뒤 **원래 종료코드 그대로** 끝난다. 스크립트가 제 핸들러를 돌리기 전에 죽는 경우
# (`.env` 없음·uv 경로 깨짐)도 여기서 잡힌다.
#
# 75 는 수집 미완이다(daily_report.sh) — 다음 크론이 다시 하므로 error 가 아니라 warn.
# 알림 구현은 복사하지 않고 quant-airflow 것을 쓴다. 없는 체크아웃에서는 로그에만 남는다.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ALERT="${SECTOR_FLOW_ALERT:-$REPO/../quant-airflow/scripts/alert.sh}"

name="${1:-}"
[ -n "$name" ] || { echo "cron_run.sh: 이름이 없다 — cron_run.sh <이름> -- <명령...>" >&2; exit 2; }
shift
[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || { echo "cron_run.sh: 명령이 없다 — cron_run.sh <이름> -- <명령...>" >&2; exit 2; }

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

"$@" 2>&1 | tee "$tmp"
rc=${PIPESTATUS[0]}

if [ "$rc" -ne 0 ]; then
    if [ "$rc" -eq 75 ]; then
        level=warn title="$name 수집 미완 — 게시 안 함, 다음 크론이 재시도"
    else
        level=error title="$name 실패 (rc=$rc)"
    fi
    if [ -x "$ALERT" ]; then
        tail -n 30 "$tmp" | "$ALERT" "$level" "$title"
    else
        echo "[cron_run] 알림 통로 없음($ALERT) — $title"
    fi
fi
exit "$rc"
