#!/usr/bin/env bash
# cron_run.sh <이름> -- <명령...>
#
# 크론 줄 하나를 감싼다(quant-airflow scripts/cron_run.sh 와 같은 규약). 출력은 그대로 stdout 으로
# 흘려 크론의 `>> log 2>&1` 이 지금처럼 동작하고, 종료코드가 0 이 아니면 꼬리 30줄을 alert.sh 로
# 보낸 뒤 **원래 종료코드 그대로** 끝난다. 스크립트가 제 핸들러를 돌리기 전에 죽는 경우
# (`.env` 없음·uv 경로 깨짐)도 여기서 잡힌다.
#
# 75 는 경고다 — error 가 아니라 warn. 뜻은 스크립트마다 다르고 본문(꼬리)이 말한다:
#   daily_report.sh   오늘 기준일 수집 미완 · 확정치 미확인(08:20) · 다른 실행과 겹침
#   freshness_check.sh 장이 열렸는데 수급이 없다 · 전 거래일 미확정 · 알림 웹훅 미설정·죽음
# 124 는 크론 줄의 timeout 이 끊은 것이다. 137 은 KILL 이다 — 웹 서버는 serve.sh 가 대답 없는 서버를 끈 것이고
# 그 재시작 연속 횟수(SECTOR_FLOW_KILL_STREAK 파일)로 알림을 솎는다(아래).
# 알림 구현은 복사하지 않고 quant-airflow 것을 쓴다. 없는 체크아웃에서는 로그에만 남는다.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ALERT="${SECTOR_FLOW_ALERT:-$REPO/../quant-airflow/scripts/alert.sh}"

name="${1:-}"
[ -n "$name" ] || { echo "cron_run.sh: 이름이 없다 — cron_run.sh <이름> -- <명령...>" >&2; exit 2; }
shift
[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || { echo "cron_run.sh: 명령이 없다 — cron_run.sh <이름> -- <명령...>" >&2; exit 2; }

# **크론 로그 크기 상한.** 크론 줄의 `>> reports/cron.log`(웹은 serve.log)가 끝없이 자란다. 그 파일(이 셸의
# stdout)이 LOG_MAX_BYTES 를 넘으면 `.1` 로 복사하고 비운다 — 옮기지(mv) 않는 것은 며칠씩 도는 웹 서버가 같은 파일을
# 열어 두고 있어서다(옮기면 서버는 `.1` 에 계속 쓰고, 다음 회전에 지워진 파일로 디스크만 먹는다). 크론의 `>>` 는
# O_APPEND 라 비운 뒤의 쓰기는 앞에서부터 이어진다. O_APPEND 가 아닌 리다이렉트(`>`)는 비우면 구멍이 생겨 건드리지 않는다.
LOG_MAX_BYTES=$((5 * 1024 * 1024))
log="$(readlink "/proc/$$/fd/1" 2>/dev/null || true)"
if [ -n "$log" ] && [ -f "$log" ] && [ "$(stat -c %s "$log" 2>/dev/null || echo 0)" -gt "$LOG_MAX_BYTES" ] \
   && [ $(( 0$(sed -n 's/^flags:[[:space:]]*//p' "/proc/$$/fdinfo/1" 2>/dev/null) & 02000 )) -ne 0 ]; then
    cp -p "$log" "$log.1" 2>/dev/null && : > "$log"
fi

# 꼬리를 담을 임시 파일은 **디스크(reports/)에 먼저** 만든다. /tmp 는 사용자 할당량이 걸린 tmpfs 라 다른 세션이
# 채우면 mktemp 가 실패하거나(→ 예전엔 `tail: cannot open ''` 로 본문이 빈 알림), 빈 파일은 만들어지고 쓰기만
# 조용히 실패한다(2026-11-08 크론 리허설). 둘 다 안 되면 출력은 그대로 흘리고 알림 본문에 그 사실을 적는다.
# KILL 로 끝나 trap 이 못 지운 지난 꼬리 파일(하루 넘은 것)은 여기서 쓸어 낸다. 이름에 만든 cron_run 의 pid 를 넣어
# **아직 살아 있는 래퍼의 것은 둔다** — 웹 서버를 감싼 래퍼는 며칠씩 돈다. 그 꼬리를 지우면 서버가 죽었을 때
# 본문이 "임시 파일을 못 만들었다" 로 나갔다(2026-11-08 운영 검토).
find "$REPO/reports" -maxdepth 1 -name '.cron_run.*' -mtime +0 2>/dev/null | while IFS= read -r old; do
    owner="${old##*/.cron_run.}"
    owner="${owner%%.*}"
    if [[ "$owner" =~ ^[0-9]+$ ]] && tr '\0' ' ' < "/proc/$owner/cmdline" 2>/dev/null | grep -q 'cron_run\.sh'; then
        continue
    fi
    rm -f "$old"
done
tmp="$(mktemp -p "$REPO/reports" ".cron_run.$$.XXXXXX" 2>/dev/null || mktemp 2>/dev/null || true)"
trap '[ -n "$tmp" ] && rm -f "$tmp"' EXIT

if [ -n "$tmp" ]; then
    "$@" 2>&1 | tee "$tmp"
    rc=${PIPESTATUS[0]}
else
    "$@" 2>&1
    rc=$?
fi

if [ "$rc" -ne 0 ]; then
    if [ "$rc" -eq 75 ]; then
        level=warn title="$name 경고 (rc=75) — 본문의 ⚠️ 줄 참조"
    elif [ "$rc" -eq 124 ]; then
        level=error title="$name 시간 초과 (rc=124) — 크론 줄의 timeout 이 끊었다"
    else
        level=error title="$name 실패 (rc=$rc)"
    fi
    # **KILL(137) 알림을 솎는다.** serve.sh 는 대답 없는 서버를 TERM 으로 끄고, 5초 버티면 KILL 한다 — 그 서버를 띄웠던
    # 이 래퍼는 137 로 끝난다. TERM 을 무시하는 서버면 5분마다 되풀이돼 시간당 12건이 된다. serve.sh 가 이미 그
    # 재시작을 연속 횟수(.serve.restarts)로 세어 연속 첫 회와 KILL_ALERT_EVERY 회마다 한 번 알리므로, 그 파일을
    # 크론 줄이 SECTOR_FLOW_KILL_STREAK 로 넘겨 주면 같은 박자로만 알린다. 파일이 없으면(다른 무엇이 KILL 했다) 늘 알린다.
    KILL_ALERT_EVERY=12   # serve.sh 의 RESTART_ALERT_EVERY 와 같다(tests/test_runbook.py 가 대조한다)
    if [ "$rc" -eq 137 ] && [ -n "${SECTOR_FLOW_KILL_STREAK:-}" ]; then
        streak="$(cat "$SECTOR_FLOW_KILL_STREAK" 2>/dev/null || true)"
        if [[ "$streak" =~ ^[0-9]+$ ]] && [ "$streak" -gt 1 ] && [ $((streak % KILL_ALERT_EVERY)) -ne 0 ]; then
            echo "[cron_run] $title — serve.sh 재시작 연속 ${streak}회째라 알리지 않는다(첫 회와 ${KILL_ALERT_EVERY}회마다만)"
            exit "$rc"
        fi
    fi
    if [ -x "$ALERT" ]; then
        if [ -n "$tmp" ] && [ -s "$tmp" ]; then
            tail -n 30 "$tmp" | "$ALERT" "$level" "$title"
        else
            echo "출력 꼬리가 없다 — 임시 파일을 못 만들었거나(reports/·/tmp 용량을 보라: df -h /tmp; du -sh /tmp/* 2>/dev/null | sort -h | tail) 명령이 아무것도 안 찍었다. 전체는 크론 로그(reports/*.log)에 있다." \
                | "$ALERT" "$level" "$title"
        fi
    else
        echo "[cron_run] 알림 통로 없음($ALERT) — $title"
    fi
fi
exit "$rc"
