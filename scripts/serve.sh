#!/usr/bin/env bash
# serve.sh — 리포트 웹 서비스(scripts/serve_reports.py)를 **안 떠 있으면** 띄운다.
#
# 크론이 5분마다 부른다(deploy/crontab.simnode). 이미 떠 있으면 잠금을 못 잡고 0 으로 바로 끝난다 —
# 그래서 재부팅·서버 사망·배포(서버가 제 코드가 바뀌면 스스로 끝난다) 뒤 5분 안에 다시 뜬다.
#
# 주소는 Tailscale 에만 묶는다. 리포트는 비공개 레포의 산출물이고 집 LAN·공인망에 열 이유가 없다.
# Tailscale 이 아직 없거나 포트가 막혔으면 띄우지 않고 다음 크론을 기다린다. 한 시간 넘게 이어지면 한 번 알린다.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${SECTOR_FLOW_PORT:-8765}"
HOST="${SECTOR_FLOW_HOST:-$(tailscale ip -4 2>/dev/null | head -n 1)}"
mkdir -p "$REPO/reports"
if [ -z "$HOST" ]; then
  # 부팅 직후엔 흔하다 — 0 으로 끝내되, 한 시간 넘게 이어지면 한 번 알린다(1).
  exec python3 "$REPO/scripts/serve_reports.py" --root "$REPO/reports" \
    --soft-fail "Tailscale 주소가 없다(SECTOR_FLOW_HOST 로 지정 가능)"
fi
# -E 0: 이미 떠 있으면(잠금 충돌) 오류가 아니다. 서버가 죽어서 끝나면 그 종료코드가 그대로 나가
# cron_run.sh 가 알린다. SIGTERM(재부팅·손으로 끔)은 서버가 0 으로 끝낸다.
exec flock -n -E 0 "$REPO/reports/.serve.lock" python3 "$REPO/scripts/serve_reports.py" \
  --root "$REPO/reports" --host "$HOST" --port "$PORT"
