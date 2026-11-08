#!/usr/bin/env bash
# serve.sh — 리포트 웹 서비스(scripts/serve_reports.py)를 **안 떠 있으면** 띄운다.
#
# 크론이 5분마다 부른다(deploy/crontab.simnode). 이미 떠 있고 /healthz 가 대답하면 0 으로 바로 끝난다 —
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
LOCK="$REPO/reports/.serve.lock"
# **떠 있는데 대답이 없으면 죽이고 다시 띄운다.** 잠금만 보면 프로세스가 살아 있는 한 0 으로 끝나, 서버가
# 멈춘(스레드 고갈·디스크 대기) 채로 며칠이 가도 아무도 모른다. 잠금이 잡혀 있으면 /healthz 를 5초 안에
# 묻고, 대답이 없으면 서버가 적어 둔 pid(.serve.pid)를 끝낸다 — TERM, 5초 뒤에도 살아 있으면 KILL.
# 그 pid 가 정말 이 서버인지(/proc 의 명령줄) 확인하고, 모르겠으면 아무것도 안 죽이고 기동 실패로 센다
# (--soft-fail: 한 시간 이어질 때만 알린다). KILL 로 끝난 서버는 그 서버를 띄웠던 크론 래퍼가 rc 137 로 알린다.
# curl 이 없으면 예전처럼 잠금만 본다.
if ! flock -n "$LOCK" true; then
  if ! command -v curl >/dev/null 2>&1 \
     || curl -fsS -m 5 -o /dev/null "http://$HOST:$PORT/healthz" 2>/dev/null; then
    exit 0
  fi
  PID="$(cat "$REPO/reports/.serve.pid" 2>/dev/null || true)"
  if [ -z "$PID" ] || ! tr '\0' ' ' < "/proc/$PID/cmdline" 2>/dev/null | grep -q "serve_reports.py"; then
    exec python3 "$REPO/scripts/serve_reports.py" --root "$REPO/reports" \
      --soft-fail "서버가 떠 있는데 /healthz 가 대답하지 않고, 누구인지(.serve.pid) 몰라 못 끈다 — 손으로 확인하라"
  fi
  echo "[$(date '+%F %T')] 서버(pid $PID)가 떠 있는데 http://$HOST:$PORT/healthz 가 5초 안에 대답하지 않는다 — 끄고 다시 띄운다"
  kill "$PID" 2>/dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.5
  done
  kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
fi
# -E 0: 그 사이 다른 실행이 띄웠으면(잠금 충돌) 오류가 아니다. -w 10 은 방금 끈 서버의 flock 래퍼가 잠금을
# 놓을 때까지 기다린다. 서버가 죽어서 끝나면 그 종료코드가 그대로 나가 cron_run.sh 가 알린다.
# SIGTERM(재부팅·손으로 끔)은 서버가 0 으로 끝낸다.
exec flock -w 10 -E 0 "$LOCK" python3 "$REPO/scripts/serve_reports.py" \
  --root "$REPO/reports" --host "$HOST" --port "$PORT"
