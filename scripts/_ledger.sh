# _ledger.sh — 운영 사건 원장(reports/events.jsonl) 쓰기. daily_report.sh·cron_run.sh·serve.sh 가 `source` 한다.
#
# 폴더 바꿔 끼우기(재구성·재시도)가 운영 흔적(INCOMPLETE·VERIFY_FAILED·mtime)을 지우고 cron.log 는 돌아서,
# "언제 게시됐나·몇 번 막혔나" 를 나중에 셀 수 없었다. 그래서 사건마다 JSON 한 줄을 **덧붙이기만** 한다.
# 형식·회전 규칙·읽기·통계의 정본은 scripts/ops_ledger.py 머리말이고, SLO 는 docs/slo.md 다.
#
# 규칙:
# * 한 사건 = 한 줄. `>>`(O_APPEND) 에 printf 한 번 — 짧은 줄(<4KB)은 쓰기 하나로 나가 동시 기록이 섞이지 않는다.
# * 절대 자르지 않는다. 쓰던 파일이 SF_LEDGER_MAX_BYTES(기본 50MB)를 넘으면 events.jsonl.2, .3 … 으로 이어 쓴다.
# * 기록 실패는 리포트·점검을 멈추지 않는다 — stderr 한 줄만 남기고 0 으로 돌아간다.
# * 값은 이 레포가 만든 날짜·숫자·열거형·sha 뿐이다 — 따옴표·역슬래시가 없어 이스케이프가 필요 없다.
#   자유 문장을 실으려면 ops_ledger.append_event(파이썬, json.dumps)를 쓴다.

SF_LEDGER_MAX_BYTES="${SF_LEDGER_MAX_BYTES:-$((50 * 1024 * 1024))}"

# sf_ledger_file <root> — 지금 덧붙일 파일. 가장 높은 연속 파일이 상한을 넘었으면 다음 번호를 시작한다.
sf_ledger_file() {
  local root="$1" n=1 cur next
  cur="$root/events.jsonl"
  next="$root/events.jsonl.2"
  while [ -e "$next" ]; do
    n=$((n + 1))
    cur="$next"
    next="$root/events.jsonl.$((n + 1))"
  done
  if [ -e "$cur" ] && [ "$(stat -c %s "$cur" 2>/dev/null || echo 0)" -ge "$SF_LEDGER_MAX_BYTES" ]; then
    cur="$next"
  fi
  printf '%s\n' "$cur"
}

# sf_event_append <root> <json 한 줄> — 늘 0 으로 돌아간다(원장이 리포트를 죽이면 안 된다).
sf_event_append() {
  local root="$1" line="$2" f
  f="$(sf_ledger_file "$root" 2>/dev/null)" || f="$root/events.jsonl"
  if ! { printf '%s\n' "$line" >> "$f"; } 2>/dev/null; then
    echo "[$(date '+%F %T')] [원장] 운영 사건을 기록하지 못했다($f) — 리포트·점검은 계속한다. 디스크·권한을 보라(README §3.1)." >&2
  fi
  return 0
}

# sf_q <값> — JSON 문자열 또는 null(빈 값). 값에 따옴표·역슬래시가 없다는 전제는 머리말 참조.
sf_q() {
  if [ -n "${1:-}" ]; then printf '"%s"' "$1"; else printf 'null'; fi
}
