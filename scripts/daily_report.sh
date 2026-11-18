#!/usr/bin/env bash
# 섹터 자금흐름 일일 리포트 — 날짜 폴더에 재워둔다.
#
# 폴더 이름은 **데이터 기준일**(supply_demand 의 MAX(date))이지 실행일이 아니다.
# 휴장일에 돌면 전 거래일 폴더가 이미 있으므로 건너뛴다 — 같은 데이터로 폴더가
# 여러 개 생기는 걸 막는다.
#
# **재구성(KR_QUANT_ASOF=YYYY-MM-DD).** 지난 기준일을 지금 코드와 지금 DB(그 사이 확정된 값)로 다시 만든다.
# 모든 읽기를 그날 이하로 묶고(sector_flow.py --asof), 확정 모드 의미로 만들며(FINAL.txt), 폴더에 REBUILT.txt 를
# 남겨 웹 목록·화면이 "재구성" 이라 말하게 한다. 바꿔 끼운 옛 폴더는 지우지 않고 $OUT_ROOT/.archive/ 로 옮긴다.
# latest 는 뒤로 돌리지 않는다. 여러 날은 scripts/rebuild_history.py 가 오래된 날부터 차례로 부른다
# (직전 대비 `change` 가 같은 루트의 직전 거래일 폴더를 읽는다).
#
# 종료코드: 0 = 게시했거나 건너뜀 · 75 = 오늘 기준일이 수집 미완이라 안 만듦(재실행하면 된다) ·
# 그 밖 = 오류·검증 실패·**지난** 기준일이 아직 수집 미완(1 — 재시도 창이 끝나 간다) · 3 = 재구성 기준일이 휴장일(KR_QUANT_ASOF).
# 재구성인데 그날 확정치(daily_finalize)를 확인하지 못하면 잠정으로 만들고(FINAL.txt 없음) 75 로 끝난다.
# 몇 번을 돌려도 안전하다 — 크론이 재시도로 같은 명령을 다시 부른다.
#
# **검증에 걸린 새 판은 검증을 통과한 게시본을 밀어내지 못한다.** 같은 기준일을 다시 지었는데(확정 08:20·10:20,
# KR_QUANT_FORCE, 재구성) 새 판이 검증에 걸리고 지금 게시본은 통과했으면(VERIFY_FAILED.txt 없음), 게시본과 latest 를
# 그대로 두고 새 판은 $OUT_ROOT/.rejected/<기준일>.<시각> 에 비켜 둔 채 1 로 끝난다(알림). 예전엔 통과본을 rm -rf
# 하고 걸린 판을 latest 로 걸어 되돌릴 길이 없었다. 표시 파일(FINAL.txt·REBUILT.txt·VERIFY_FAILED.txt)은 **그 폴더의
# 판**을 말한다 — 걸린 확정 판의 FINAL.txt 는 .rejected 안에 있고, 게시본은 잠정 그대로라(FINAL.txt 없음) 10:20 재시도와
# 20:30 확정 점검(75)이 평소처럼 돈다. 지금 게시본도 걸린 판이면 예전처럼 바꿔 낀다(더 나빠질 것이 없다).
#
# 리포트는 생성물이지 소스가 아니다 — git 추적 대상은 아니지만(.gitignore 의
# reports/), 접근 편의를 위해 저장소 **안**(`$REPO/reports`)에 둔다.
set -euo pipefail

# 레포 경로는 이 스크립트 위치에서 유도한다 — 2026-09-11 레포를
# ~/Documents/git 에서 ~/git 으로 옮기면서 여기 하드코딩돼 있던 옛 경로가
# 죽었다. set -euo pipefail 이라 `cd "$REPO"` 에서 즉사하는데, 이 배치는
# 평일 18:10 크론이라 09-12(토)엔 안 돌았고 첫 실패는 월요일이 될 뻔했다.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_ROOT="${KR_QUANT_REPORTS:-$REPO/reports}"
DAYS="${KR_QUANT_REPORT_DAYS:-260}"
cd "$REPO"
# uv 찾기·.env 불러오기는 freshness_check.sh 와 같이 쓴다.
. "$REPO/scripts/_env.sh"

# **운영 사건 원장(reports/events.jsonl).** 폴더 바꿔 끼우기가 INCOMPLETE·VERIFY_FAILED 흔적과 mtime 을 지우고
# cron.log 는 돌아서, "언제 게시됐나·몇 번 막혔나·게시 뒤 숫자가 바뀐 날" 을 나중에 셀 수 없었다. 그래서 실행마다
# 결말 한 줄을 EXIT 트랩에서 덧붙인다(형식·통계는 scripts/ops_ledger.py, SLO 는 docs/slo.md). 기록 실패는
# 리포트를 멈추지 않고(_ledger.sh), 헬퍼가 없는 부분 사본에서는 조용히 건너뛴다.
if [ -f "$REPO/scripts/_ledger.sh" ]; then . "$REPO/scripts/_ledger.sh"; fi
declare -F sf_event_append >/dev/null 2>&1 || { sf_event_append() { :; }; sf_q() { printf 'null'; }; }
EV_STARTED="$(date -u +%FT%TZ)"
EV_MODE="normal"
[ -z "${KR_QUANT_FINAL:-}" ] || EV_MODE="final"
[ -z "${KR_QUANT_ASOF:-}" ] || EV_MODE="rebuild"
EV_ASOF="" EV_OUTCOME="error" EV_GATE="" EV_VERIFY="" EV_CHECKS="" EV_FAILS="" EV_FINAL="" EV_DIFF="" EV_SHA=""
# 트랩이 읽는 변수는 트랩을 걸기 **전에** 비운다 — 환경에서 물려받은 BUILD 를 트랩이 rm -rf 한다(tests/test_ops_ledger.py 가 본다).
BUILD=""
_dr_event() {
  local rc="$1"
  # 옆에 짓던 폴더 청소 — 성공해 옮겼으면($BUILD 없음) 아무 일도 없다. 원장 기록과 한 트랩에 묶는다.
  [ -z "${BUILD:-}" ] || rm -rf "$BUILD" 2>/dev/null || true
  local commit
  commit="${KR_QUANT_CODE_REV:-$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)}"
  sf_event_append "$OUT_ROOT" "{\"ts\":\"$(date -u +%FT%TZ)\",\"event\":\"report\",\"asof\":$(sf_q "$EV_ASOF"),\"mode\":\"$EV_MODE\",\"rc\":$rc,\"outcome\":\"$EV_OUTCOME\",\"gate\":$(sf_q "$EV_GATE"),\"verify\":$(sf_q "$EV_VERIFY"),\"verify_checks\":${EV_CHECKS:-null},\"verify_fails\":${EV_FAILS:-null},\"final\":${EV_FINAL:-false},\"diff\":${EV_DIFF:-null},\"payload_sha\":$(sf_q "$EV_SHA"),\"commit\":$(sf_q "$commit"),\"started\":\"$EV_STARTED\"}"
}

# 부속 단계의 상한 — 안쪽 ``timeout`` 은 자기 프로세스 그룹을 만들어(상한에 걸리면 손자까지 끊으려고) 크론 줄의 바깥 ``timeout`` 이 보낸 TERM 이 안 간다.
# 전경으로 돌리면 셸은 그 자식을 기다리느라 TERM 트랩을 못 돌리고 KILL 로 죽는다: rc 137, 원장에 report 줄 없음, .building.N 잔류, 자식은 고아로
# 계속 돈다(2026-11-09 재현). 배경으로 띄워 ``wait`` 하면 트랩이 바로 돌고, 트랩이 안쪽 timeout 에 TERM 을 넘긴다(그가 자기 그룹에 전한다).
BOUNDED_PID=""
_bounded() {
  timeout -k 10s "$@" &
  BOUNDED_PID=$!
  wait "$BOUNDED_PID"
}
_bounded_stop() { [ -z "${BOUNDED_PID:-}" ] || kill -TERM "$BOUNDED_PID" 2>/dev/null || true; }

# **겹쳐 돌지 않는다.** 크론(18:10·19:10·08:20)끼리는 timeout 40m 이라 안 겹치지만, 손으로 돌린
# KR_QUANT_FORCE 와 크론이 겹치면 같은 기준일을 둘이 옆에 지어 바꿔 끼우다 서로의 폴더를 지운다.
# 잠금을 못 잡으면 75(경고)로 끝난다 — 0 으로 조용히 끝내면, 08:20 확정 모드가 손으로 돌린 잠정 실행에
# 밀렸을 때 그 기준일이 영영 잠정으로 남는데 아무도 모른다. 크론끼리는 안 겹치므로 이 경고는 드물다.
# 자식(파이썬)도 fd 9 를 물려받아, 셸이 죽어도 자식이 다 끝날 때까지 잠금이 남는다.
mkdir -p "$OUT_ROOT"
trap '_dr_event $?' EXIT
# 시그널로 끝나면 bash 는 EXIT 트랩을 돌리되 $? 가 직전 명령의 것(대개 0)이다 — 크론 timeout 이 끊은 실행이 원장에 rc:0, outcome:error
# 로 남았다. 관례대로 128+시그널 번호로 끝내 그 값이 트랩에 가게 한다.
# 부속 단계(_bounded)가 돌고 있으면 끊어 주고 끝난다.
_signal_traps() {
  trap '_bounded_stop; exit 129' HUP
  trap '_bounded_stop; exit 130' INT
  trap '_bounded_stop; exit 143' TERM
}
_signal_traps
LOCK="$OUT_ROOT/.report.lock"
exec 9>>"$LOCK"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] ⚠️ 다른 리포트 실행이 돌고 있다($LOCK) — 이번 실행은 건너뛴다. 그쪽이 끝난 뒤 필요하면 다시 돌려라." >&2
  EV_OUTCOME="locked"
  exit 75
fi
# **KILL·OOM 이 남긴 짓던 폴더 청소.** 잠금을 쥐었으니 이 루트에서 짓는 실행은 이것뿐이다 — 남은 <기준일>.building.N 은
# 끊긴 실행의 것이다. 하루 지난 것만 지운다(.asof_err.* 와 같은 규칙). <기준일>.replaced.N 은 바꿔 끼우기 도중에 끊긴 옛 게시본이라
# 제자리(<기준일>)가 비었으면 지우지 않고 되돌린다 — 그 폴더가 그날의 유일한 사본이다.
STALE=""
while IFS= read -r STALE; do
  [ -n "$STALE" ] || continue
  case "$STALE" in
    *.replaced.*)
      HOME_DIR="${STALE%.replaced.*}"
      if [ ! -e "$HOME_DIR" ]; then
        mv "$STALE" "$HOME_DIR" 2>/dev/null && echo "[$(date '+%F %T')] 끊긴 바꿔 끼우기의 옛 게시본을 제자리로 되돌렸다 — $HOME_DIR"
        continue
      fi ;;
  esac
  rm -rf "$STALE" 2>/dev/null && echo "[$(date '+%F %T')] 끊긴 실행이 남긴 폴더를 지웠다 — $STALE"
done < <(find "$OUT_ROOT" -mindepth 1 -maxdepth 1 -type d \( -name '*.building.*' -o -name '*.replaced.*' \) -mtime +0 2>/dev/null)

# 실패 사유는 보여 준다 — 버리면 `set -e` 때문에 아래 안내까지 못 가고 빈 알림으로 끝난다.
# libpq 가 접속 문자열을 되읊을 수 있어 비밀번호 자리는 가린다.
REBUILD="${KR_QUANT_ASOF:-}"
if [ -n "$REBUILD" ] && ! [[ "$REBUILD" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "[$(date '+%F %T')] KR_QUANT_ASOF 가 날짜 꼴(YYYY-MM-DD)이 아니다: $REBUILD — 중단." >&2
  exit 2
fi
# 임시 파일은 디스크에 — /tmp(사용자 할당량 tmpfs)가 차면 mktemp 가 죽고 set -e 로 이유 없이 끝났다(2026-11-08 리허설).
# KILL 로 끝나 못 지운 지난 것(하루 넘은 것)은 쓸어 낸다. 파일은 0600 이고, 접속 문자열은 쓰자마자 가린다(아래 sed -i).
find "$OUT_ROOT" -maxdepth 1 -name '.asof_err.*' -mtime +0 -delete 2>/dev/null || true
ASOF_ERR="$(mktemp -p "$OUT_ROOT" .asof_err.XXXXXX)" || {
  echo "[$(date '+%F %T')] 임시 파일을 못 만들었다($OUT_ROOT) — 디스크 용량·권한을 보라. 중단." >&2
  exit 1
}
ASOF_RC=0
# 재구성이면 그날 **이하**의 마지막 수급일 — 그날이 아니면 휴장일이다(아래에서 끊는다).
ASOF="$("${UVR[@]}" python - <<'PY' 2>"$ASOF_ERR"
import os
from sectorflow.storage import _is_pg, connect, db_default
con = connect(os.environ.get("KR_QUANT_DB") or db_default())
cur = con.cursor()
upto = os.environ.get("KR_QUANT_ASOF")
if upto:
    cur.execute("SELECT max(date) FROM supply_demand WHERE date <= " + ("%s" if _is_pg(con) else "?"), (upto,))
else:
    cur.execute("SELECT max(date) FROM supply_demand")
print(cur.fetchone()[0]); con.close()
PY
)" || ASOF_RC=$?
sed -i -E 's#://[^@ ]*@#://***@#g' "$ASOF_ERR" 2>/dev/null || true
if [ "$ASOF_RC" -ne 0 ]; then
  echo "[$(date '+%F %T')] DB 에서 기준일을 못 읽었다(rc=$ASOF_RC) — 접속정보(KR_QUANT_DB)와 DB 서버를 확인하라. 중단." >&2
  # 10줄 — uv 의 힌트 줄이 예외 뒤에 붙어 3줄이면 예외가 잘렸다(2026-11-08 모의 실행).
  tail -n 10 "$ASOF_ERR" | sed -E 's#://[^@ ]*@#://***@#g' >&2
  rm -f "$ASOF_ERR"
  exit 1
fi
rm -f "$ASOF_ERR"

# 빈 테이블이면 파이썬이 "None" 을 찍는다 — 그대로 두면 reports/None 폴더가 생긴다.
case "$ASOF" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
  *)
    echo "[$(date '+%F %T')] 기준일이 없다(${ASOF:-빈 값}) — 접속은 됐는데 supply_demand 가 비었다. KR_QUANT_DB 가 맞는 DB 인지 확인하라. 중단." >&2
    exit 1
    ;;
esac
EV_ASOF="$ASOF"
TODAY="$(TZ=Asia/Seoul date +%F)"
if [ -n "$REBUILD" ] && [ "$ASOF" != "$REBUILD" ]; then
  # 수급이 없다고 휴장은 아니다 — 같은 날 업종지수·일봉이 있으면 수급 수집이 빠진 것이다(오류로 알린다).
  OTHER_ROWS="$(KR_QUANT_ASOF="$REBUILD" "${UVR[@]}" python - <<'PY'
import os
from sectorflow.storage import _is_pg, connect, db_default
con = connect(os.environ.get("KR_QUANT_DB") or db_default())
cur = con.cursor()
ph = "%s" if _is_pg(con) else "?"
n = 0
for t in ("sector_index", "daily_bars"):
    cur.execute(f"SELECT count(*) FROM {t} WHERE date = {ph}", (os.environ["KR_QUANT_ASOF"],))
    n += cur.fetchone()[0]
print(n); con.close()
PY
)"
  if [ "${OTHER_ROWS:-0}" != "0" ]; then
    echo "[$(date '+%F %T')] 재구성 $REBUILD 에 수급이 없는데 업종지수·일봉은 ${OTHER_ROWS}행 있다 — 휴장이 아니라 수급 수집 공백이다. 메운 뒤 다시 돌려라." >&2
    exit 1
  fi
  echo "[$(date '+%F %T')] 재구성 $REBUILD 는 거래일이 아니다(그 이하 마지막 수급일 $ASOF) — 휴장일이다(종료코드 3). 만들지 않는다." >&2
  EV_ASOF="$REBUILD" EV_OUTCOME="holiday"
  exit 3
fi

DEST="$OUT_ROOT/$ASOF"
# 건너뛰기는 **데이터**가 그대로일 때를 위한 것이다(휴장일 재실행). 리포트에 실리는
# 값이 늘어났을 때는 데이터가 같아도 다시 만들어야 하는데, 그럴 방법이 없었다 —
# 열을 추가하고 나서 사용자 화면에 새 열이 통째로 비어 있었고, 원인은 코드가 아니라
# 폴더가 이미 있어서 이 배치가 조용히 건너뛴 것이었다. 다음 거래일까지 그 상태로
# 남는다. KR_QUANT_FORCE=1 로 그 문을 연다.
#
# 수집 미완(INCOMPLETE.txt 만 있는 폴더)과 검증 실패(VERIFY_FAILED.txt)는 건너뛰지 않는다 —
# 다음 재시도가 그 기준일을 다시 만들 유일한 기회다. 기준일이 넘어가면 아무도 안 만든다.
#
# **확정 모드(KR_QUANT_FINAL=1, 화~토 08:20 크론).** 18:10 리포트는 그날 잠정치로 만든다. 다음날
# 새벽 daily_finalize 가 확정치를 덮은 것이 확인되면(scripts/finalize_done.py) 그 기준일을 확정치로
# 다시 만들고 FINAL.txt 를 남긴다. 게시본의 G 가 잠정치에 흔들렸다(2026-11-08 지표 검증, 합격선 90% 에 62.5%).
#
# 확인이 안 되면 잠정 리포트는 그대로 두되 **75(경고)로 끝난다.** 예전엔 0 으로 조용히 끝나, 확정 작업이
# 깨진 날(또는 로그 경로가 바뀐 날)마다 그 기준일이 영영 잠정으로 남는데 아무도 몰랐다. 다음 거래일 수집이
# 시작되면 기준일이 넘어가 그 날은 다시 확정 모드를 안 탄다 — 그래서 알려야 한다. 손으로 고치는 법은
# 확인한 뒤 `KR_QUANT_FINAL=1 scripts/daily_report.sh`.
#
# FINAL_OK = 이번에 만들면 확정치다(--final·FINAL.txt). FINAL_REBUILD = 리포트가 있어도 확정치로 다시 만든다.
# 이미 FINAL.txt 가 있는 기준일은 확정이 확인된 날이다 — 검증 실패 재시도·강제 재생성이 그 날을 잠정으로
# 되돌리면 안 된다(휴장일 18:10 이 08:20 의 검증 실패 확정 리포트를 다시 만들며 잠정으로 내렸다).
FINAL_OK=""
FINAL_REBUILD=""
FINAL_UNCONFIRMED=""
if [ -n "$REBUILD" ]; then
  # 이미 확정치로 재구성한 날은 다시 안 만든다(rebuild_history 재실행이 이어서 돈다). 원본(REBUILT.txt 없음)과
  # 잠정으로 재구성한 날(FINAL.txt 없음)은 다시 만든다.
  # 창 밖이라 확정 기록이 없는 날(finalize: unverified)도 다시 봐도 달라질 것이 없다 — 건너뛴다.
  if [ -f "$DEST/REBUILT.txt" ] && [ -f "$DEST/numbers.html" ] \
     && { [ -f "$DEST/FINAL.txt" ] || grep -qx "finalize: unverified" "$DEST/REBUILT.txt"; } \
     && [ ! -f "$DEST/VERIFY_FAILED.txt" ] && [ -z "${KR_QUANT_FORCE:-}" ]; then
    echo "[$(date '+%F %T')] $ASOF 는 이미 재구성했다 — 건너뛴다(다시 만들려면 KR_QUANT_FORCE=1)."
    EV_OUTCOME="skipped"
    exit 0
  fi
  FINAL_REBUILD=1
  # 확정 의미는 **확인한 뒤에만** 붙인다 — 아직 daily_finalize 가 안 덮은 날(어제 것)을 확정이라 적으면
  # 08:20 확정 모드가 FINAL.txt 를 보고 그날을 영영 건너뛴다.
  REBUILD_FINAL_RC=0
  "${UVR[@]}" python scripts/finalize_done.py --asof "$ASOF" || REBUILD_FINAL_RC=$?
  # 창 밖 = 기준일이 오늘보다 daily_finalize 창(finalize_done.WINDOW_DAYS, 달력 일) 넘게 앞이다. 앞으로 도는 어떤
  # 확정 실행도 그날을 덮지 않으므로 기다릴 것이 없다 — 지금 DB 의 마감 값으로 만들되 확정이라 적지 않는다.
  WINDOW_PASSED="$(KR_TODAY="$TODAY" "${UVR[@]}" python -c 'import os, sys; sys.path.insert(0, "scripts")
from datetime import date, timedelta
import finalize_done as f
print(int(date.fromisoformat(os.environ["KR_QUANT_ASOF"]) < date.fromisoformat(os.environ["KR_TODAY"]) - timedelta(days=f.WINDOW_DAYS)))')"
  FINALIZE_STATE="verified"
  if [ "$REBUILD_FINAL_RC" -eq 0 ]; then
    FINAL_OK=1
  elif [ "$WINDOW_PASSED" = "1" ]; then
    FINALIZE_STATE="unverified"
    echo "[$(date '+%F %T')] $ASOF 는 daily_finalize 창 밖의 지난 날이다 — 마감 값으로 재구성하되 '확정 기록 없음' 으로 적는다(FINAL.txt 없음)."
  else
    FINALIZE_STATE="pending"
    echo "[$(date '+%F %T')] ⚠️ $ASOF 확정치를 확인하지 못했다 — 잠정으로 재구성한다(FINAL.txt 없음, 위 [확정치] 줄이 이유)." >&2
    FINAL_UNCONFIRMED=1
  fi
elif [ -f "$DEST/FINAL.txt" ]; then
  FINAL_OK=1
  if [ -n "${KR_QUANT_FINAL:-}" ] && [ -z "${KR_QUANT_FORCE:-}" ] && [ ! -f "$DEST/VERIFY_FAILED.txt" ]; then
    echo "[$(date '+%F %T')] $ASOF 는 이미 확정치로 만들었다 — 건너뛴다."
    EV_OUTCOME="skipped"
    exit 0
  fi
elif [ -n "${KR_QUANT_FINAL:-}" ]; then
  if "${UVR[@]}" python scripts/finalize_done.py --asof "$ASOF"; then
    FINAL_OK=1
    FINAL_REBUILD=1
  elif [ -f "$DEST/numbers.html" ]; then
    echo "[$(date '+%F %T')] ⚠️ 확정치를 확인하지 못했다 — $ASOF 잠정 리포트를 그대로 둔다(위 [확정치] 줄이 이유)." >&2
    echo "    daily_finalize 를 확인하고 끝났으면 손으로: KR_QUANT_FINAL=1 $0" >&2
    echo "    다음 거래일 수집이 들어오면 기준일이 넘어가 이 날은 잠정으로 남는다." >&2
    EV_OUTCOME="skipped"
    exit 75
  else
    # 아직 못 만든 기준일이다 — 잠정으로라도 만든다(마지막 기회). 다 만든 뒤 75 로 알린다.
    FINAL_UNCONFIRMED=1
  fi
fi
# **게시 뒤 수집이 더 들어왔으면 다시 만든다.** 관문은 97% 에서 통과한다 — 18:10 에 98% 로 통과한 리포트를 19:10 이
# "이미 있다" 며 건너뛰어 빠진 종목이 그날 저녁 내내 latest 에 남았다. 게시본의 GATE.txt 에 관문이 본 수집 행 수
# (completeness_gate.fill_line)가 있다 — 그날 저녁(기준일 = 오늘, 확정 전) 재시도는 그 줄만 다시 재서 다르면 다시 짓는다.
LATE_FILL=""
if [ -z "$REBUILD" ] && [ "$ASOF" = "$TODAY" ] && [ -f "$DEST/numbers.html" ] && [ -f "$DEST/GATE.txt" ] \
   && [ ! -f "$DEST/FINAL.txt" ]; then
  FILL_WAS="$(grep -m 1 '^수집 행 수 ' "$DEST/GATE.txt" 2>/dev/null || true)"
  FILL_NOW="$("${UVR[@]}" python scripts/completeness_gate.py --asof "$ASOF" --fill-only 2>/dev/null || true)"
  if [ -n "$FILL_WAS" ] && [ -n "$FILL_NOW" ] && [ "$FILL_WAS" != "$FILL_NOW" ]; then
    LATE_FILL=1
    echo "[$(date '+%F %T')] $ASOF 리포트를 만든 뒤 수집이 더 들어왔다(${FILL_WAS#*: } → ${FILL_NOW#*: }) — 다시 만든다"
  fi
fi
if [ -f "$DEST/numbers.html" ] && [ ! -f "$DEST/VERIFY_FAILED.txt" ] && [ -z "${KR_QUANT_FORCE:-}" ] \
   && [ -z "$FINAL_REBUILD" ] && [ -z "$LATE_FILL" ]; then
  echo "[$(date '+%F %T')] $ASOF 리포트가 이미 있다 — 건너뛴다(휴장일이거나 재실행)."
  # 여기서는 휴장과 수집 실패를 안 가른다(갈라 내는 것은 20:30 freshness_check 와 KRX 달력) — 지금 보는 사람에게 사실을 준다.
  if [[ "$ASOF" < "$TODAY" ]]; then
    echo "    최신 수급이 오늘($TODAY)이 아니다. 오늘이 거래일이면 수집이 안 된 것이다 — quant-airflow 를 확인하라."
  fi
  echo "    리포트 형식이 바뀌어 다시 만들려면: KR_QUANT_FORCE=1 $0"
  EV_OUTCOME="skipped"
  exit 0
fi

# 완전성 관문 — 만들기 **전에** 본다. 빈 시장으로 만든 리포트를 검사해서 거르는 것보다 안 만드는
# 편이 낫다: 10-27 의 리포트는 검사에 걸리고도 latest 가 됐다. 문턱과 근거는 그 스크립트 머리말.
# KR_QUANT_FORCE 는 건너뛰기만 끄고 이 관문은 못 끈다 — 부분 수집 위에 멀쩡한 리포트를 덮으면 안 된다.
GATE_RC=0
GATE_OUT="$("${UVR[@]}" python scripts/completeness_gate.py --asof "$ASOF")" || GATE_RC=$?
echo "$GATE_OUT"
if [ "$GATE_RC" -eq 75 ]; then
  EV_GATE="blocked" EV_OUTCOME="incomplete"
  # 이미 리포트가 있는 폴더(강제 재생성·검증 실패 재시도)에는 표시를 안 남긴다 — 옛 리포트는 그대로다.
  if [ ! -f "$DEST/numbers.html" ]; then
    mkdir -p "$DEST"
    { echo "[$(date '+%F %T')] $ASOF 수집 미완 — 리포트를 만들지 않았다. latest 는 그대로다."
      if [ -n "$REBUILD" ]; then
        echo "재구성(KR_QUANT_ASOF): 지금 DB 에서도 이 날 수집이 관문 기준에 못 미친다 — 메운 뒤 다시 재구성하라."
      elif [[ "$ASOF" < "$TODAY" ]]; then
        echo "재시도 창이 끝나 간다. 수집을 메운 뒤 scripts/daily_report.sh 를 손으로 돌려라 —"
        echo "다음 거래일 수집이 시작되면 기준일이 넘어가 이 날 리포트는 다시 못 만든다."
      else
        echo "다음 크론이 다시 시도하고, 성공하면 이 폴더가 통째로 바뀐다."
      fi
      echo "$GATE_OUT"
    } > "$DEST/INCOMPLETE.txt"
  fi
  echo "[$(date '+%F %T')] ⚠️ $ASOF 수집 미완 — 게시하지 않는다(latest 유지): $DEST/INCOMPLETE.txt" >&2
  # 오늘 것이면 수집이 도는 중이라 75(경고)다. 지난 날이면 기다릴 수집이 없고 기준일이 넘어가면
  # 아무도 안 만든다 — 다음날 08:20(확정 모드)의 포기가 경고로 묻히지 않게 오류로 끝낸다.
  if [ -n "$REBUILD" ]; then
    echo "[$(date '+%F %T')] ⚠️ 재구성 $ASOF — 지금 DB 에서도 수집 미완이다. 메운 뒤 KR_QUANT_ASOF=$ASOF $0" >&2
    exit 1
  fi
  if [[ "$ASOF" < "$TODAY" ]]; then
    echo "[$(date '+%F %T')] ⚠️ $ASOF 는 오늘이 아닌데 수집 미완이다 — 수집을 메운 뒤 이 스크립트를 다시 돌려라. 기준일이 넘어가면 이 날은 안 만들어진다." >&2
    exit 1
  fi
  exit 75
elif [ "$GATE_RC" -ne 0 ]; then
  echo "[$(date '+%F %T')] 완전성 관문 오류(rc=$GATE_RC) — 중단." >&2
  EV_GATE="error"
  exit "$GATE_RC"
fi
EV_GATE="pass"

# **언제나 옆에 짓고 마지막에 옮긴다.** 제자리에 쓰면 5분 남짓 동안 폴더 안에 새 payload 와 옛
# numbers.html 이 섞이고, 처음 만드는 날에 도중에 죽으면 numbers.html 만 남아 다음 재시도가
# "이미 있다" 며 건너뛴다 — latest 는 어제에 머무는데 알림도 없다.
BUILD="$DEST.building.$$"
rm -rf "$BUILD"
# 도중에 죽으면 EXIT 트랩(_dr_event)이 $BUILD 를 지우고 원장에 결말을 남긴다.
if [ -d "$DEST" ]; then
  echo "[$(date '+%F %T')] $ASOF 리포트를 **다시** 만든다 — 옆에 짓고 바꿔 낀다"
fi

mkdir -p "$BUILD"
# 관문 출력(시장별 판정 + 수집 행 수 줄)을 판과 함께 남긴다 — 위의 저녁 재시도와 20:30 신선도 점검이 이 줄과 지금 DB 를 견준다.
printf '%s\n' "$GATE_OUT" > "$BUILD/GATE.txt"
echo "[$(date '+%F %T')] 생성 시작 — 기준일 $ASOF, 구간 ${DAYS}거래일"
# 페이로드를 **한 번만** 만들고 둘 다 그걸 쓴다. 월별 시총 계산이 수 분이라
# 예전처럼 세 번 돌리면 배치가 15분을 넘고 그만큼 실패 창이 넓어진다.
# 읽기는 **언제나** 관문이 본 기준일 이하로 묶는다(--asof). 안 묶으면 sector_flow.py 가 max(date) 를 다시 골라, 관문과 빌드
# 사이에 새 거래일 행이 들어온 날(수집 중의 손 실행·늦은 확정 실행) 관문을 안 거친 그날 부분 수집이 앞 기준일 폴더에 실린다.
# 검증도 같은 날을 기준일로 본다 — 안 그러면 그 경우를 "DB 최신일과 다르다" 로 걸어 멀쩡한 판을 버린다.
FINAL_ARGS=(--asof "$ASOF")
VERIFY_ARGS=(--asof "$ASOF")
if [ -n "$FINAL_OK" ]; then
  FINAL_ARGS+=(--final)
  echo "[$(date '+%F %T')] 확정치로 만든다 — 제목이 '마감·확정' 이 된다"
fi
if [ -n "$REBUILD" ]; then
  FINAL_ARGS+=(--rebuilt)
  [ "$FINALIZE_STATE" != "unverified" ] || FINAL_ARGS+=(--finalize-unverified)
  echo "[$(date '+%F %T')] 재구성 — 지금 코드로 $ASOF 이하 데이터만 읽는다"
fi
"${UVR[@]}" python scripts/sector_flow.py    --days "$DAYS" --json "$BUILD/payload.json" "${FINAL_ARGS[@]}"
"${UVR[@]}" python scripts/sector_flow.py    --from-json "$BUILD/payload.json" --html "$BUILD/viewer.html"
"${UVR[@]}" python scripts/sector_numbers.py --payload "$BUILD/payload.json" --html "$BUILD/numbers.html"

# 저장한 것을 그 자리에서 검증한다 — 실패하면 폴더에 VERIFY_FAILED 를 남긴다.
# 조용히 틀린 리포트가 쌓이는 것이 검증 없이 도는 것보다 나쁘다.
VERIFY_RC=0
if "${UVR[@]}" python scripts/verify_report.py --dir "$BUILD" --db-check "${VERIFY_ARGS[@]}" \
     > "$BUILD/VERIFY.txt" 2>&1; then
  echo "[$(date '+%F %T')] 검증 통과 — $DEST/VERIFY.txt"
else
  cp "$BUILD/VERIFY.txt" "$BUILD/VERIFY_FAILED.txt"
  # 브라우저로만 보는 사람도 알아야 한다 — 화면 제목의 ⚠ 는 터미널에만 있다.
  for page in numbers.html viewer.html; do
    sed -i '0,/<header>/s//<div style="padding:10px 14px;margin:0 0 12px;background:#fff3cd;color:#664d03;border:1px solid #ffda6a;border-radius:6px;font-weight:600">⚠ 이 리포트는 자기 검증에 걸렸다 — 같은 폴더의 <a href="VERIFY_FAILED.txt">VERIFY_FAILED.txt<\/a> 를 보라<\/div>\n&/' "$BUILD/$page"
  done
  echo "[$(date '+%F %T')] ⚠️ 검증 실패 — $DEST/VERIFY_FAILED.txt 확인" >&2
  tail -5 "$BUILD/VERIFY.txt" >&2
  VERIFY_RC=1
fi
# 원장에 실을 검증 결과 — VERIFY.txt 요약 줄("검사 N건 · 실패 M건")의 숫자. 요약이 없으면(가짜 검증기) null.
EV_VERIFY="pass"
[ "$VERIFY_RC" -eq 0 ] || EV_VERIFY="fail"
EV_SUMMARY="$(sed -n 's/^검사 \([0-9][0-9]*\)건 · 실패 \([0-9][0-9]*\)건.*/\1 \2/p' "$BUILD/VERIFY.txt" 2>/dev/null | tail -n 1)" || true
if [ -n "$EV_SUMMARY" ]; then EV_CHECKS="${EV_SUMMARY%% *}" EV_FAILS="${EV_SUMMARY##* }"; fi
# 잠정→확정에서 바뀐 섹터 칸 수(final_diff.json 의 n_changed) — 비교가 없던 빌드(잠정·재구성)는 null.
if [ -f "$BUILD/final_diff.json" ]; then
  EV_DIFF="$(grep -o '"n_changed"[[:space:]]*:[[:space:]]*[0-9][0-9]*' "$BUILD/final_diff.json" 2>/dev/null | head -n 1 | grep -o '[0-9][0-9]*$')" || true
fi
# 부속 산출물 — 다이제스트·검증 페이지는 짓는 폴더 안에서 만들어 리포트와 **함께** 바꿔 낀다(게시된 폴더의 파일 하나를 고쳐 쓰는 길은 없다).
# 실패·멈춤에도 리포트는 게시한다. 다만 조용히 빠지면 안 된다 — VERIFY.txt 끝에 적고 맨 끝에서 75(경고)로 알린다.
SIDE_FAIL=""
# 오늘의 수급 다이제스트(scripts/digest.py, 끄려면 KR_QUANT_DIGEST=0) — 폴더에만 쓴다(밖으로 보내지 않는다). 상한 5분·오프라인·판 고정.
if [ "${KR_QUANT_DIGEST:-1}" != "0" ]; then UV_OFFLINE=1 _bounded "${KR_QUANT_DIGEST_TIMEOUT:-5m}" "${UVR[@]}" --with 'playwright==1.48.0' python scripts/digest.py --dir "$BUILD" || { echo "[$(date '+%F %T')] ⚠️ 다이제스트를 못 만들었다(실패 또는 시간 초과) — 리포트는 그대로 게시한다" >&2; SIDE_FAIL+="digest "; }; fi
# 다이제스트 검증(verify_report.py X 층) — 글의 시장 줄·요약·가장 큰 주체·상·하위·극단·부호 바뀜·20일 변동이 페이로드·화면과
# 같은가. 글은 위 검증 뒤에 지어져 위 검증이 못 본다(2026-11-18 글에만 실린 '출판/매체복제 95'). 걸리면 글·카드를 빼고
# 게시한다 — 화면과 다른 말을 하는 글보다 없는 것이 낫다. 기록은 VERIFY_DIGEST.txt(통과해도 남긴다).
if [ -f "$BUILD/digest.md" ]; then
  if ! _bounded "${KR_QUANT_DIGEST_VERIFY_TIMEOUT:-5m}" "${UVR[@]}" python scripts/verify_report.py --dir "$BUILD" --layers X \
       > "$BUILD/VERIFY_DIGEST.txt" 2>&1; then
    echo "[$(date '+%F %T')] ⚠️ 다이제스트가 페이로드·화면과 다른 말을 한다(X 층) — 글·카드를 빼고 게시한다: $DEST/VERIFY_DIGEST.txt" >&2
    grep -F '[FAIL]' "$BUILD/VERIFY_DIGEST.txt" >&2 || true
    rm -f "$BUILD/digest.md" "$BUILD/digest.json" "$BUILD/digest_card.html" "$BUILD/digest.png"
    SIDE_FAIL+="digest-verify "
  fi
fi
# 글은 됐는데 카드만 못 찍은 날 — 웹의 /latest/digest.png 가 404 다.
if [ "${KR_QUANT_DIGEST:-1}" != "0" ] && [ -f "$BUILD/digest.md" ] && [ ! -f "$BUILD/digest.png" ]; then
  echo "[$(date '+%F %T')] ⚠️ 다이제스트 카드(digest.png)를 못 찍었다 — 글은 만들었다" >&2
  SIDE_FAIL+="digest.png "
fi

if [ -n "$REBUILD" ] && [ -n "$FINAL_OK" ]; then
  echo "[$(date '+%F %T')] 재구성 — daily_finalize 확인 뒤 확정된 DB 값으로 만들었다(REBUILT.txt)." > "$BUILD/FINAL.txt"
  # 잠정 대비는 없다 — sector_flow.py --rebuilt 가 그 이유 한 줄을 남긴다.
  if [ -s "$BUILD/final_diff.txt" ]; then cat "$BUILD/final_diff.txt" >> "$BUILD/FINAL.txt"; fi
elif [ -z "$REBUILD" ] && [ -n "$FINAL_OK" ]; then
  echo "[$(date '+%F %T')] daily_finalize 확인 뒤 확정치로 만들었다." > "$BUILD/FINAL.txt"
  # 잠정 → 확정에서 바뀐 머리 숫자(sector_flow.py --final 이 남긴다).
  if [ -f "$BUILD/final_diff.txt" ]; then cat "$BUILD/final_diff.txt" >> "$BUILD/FINAL.txt"; fi
fi
if [ -n "$REBUILD" ]; then
  # 원본을 지우지 않는다 — 재구성은 옛 게시본을 대신하는 것이지 없애는 것이 아니다.
  ORIGIN="${KR_QUANT_REBUILD_ORIGIN:-}"
  ARCHIVED=""
  if [ -d "$DEST" ]; then
    ARCHIVED="$OUT_ROOT/.archive/$ASOF.$(date +%Y%m%d%H%M%S)"
    # 재구성을 다시 재구성하면 원본은 처음 것 그대로다.
    if [ -z "$ORIGIN" ] && [ -f "$DEST/REBUILT.txt" ]; then
      ORIGIN="$(sed -n '1s/.*; 원본은 //p' "$DEST/REBUILT.txt")"
    fi
    [ -n "$ORIGIN" ] || ORIGIN="$ARCHIVED"
  fi
  # 전체 sha — 짧은 sha 는 나중에 모호해지고, 어느 브랜치에도 없는 커밋이면 다시 볼 길이 없다(rebuild_history 가 막는다).
  REV="${KR_QUANT_CODE_REV:-$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo '?')}"
  case "$FINALIZE_STATE" in
    verified) KIND="확정 데이터" ;;
    unverified) KIND="마감 데이터(daily_finalize 창 밖 — 확정 기록 없음)" ;;
    *) KIND="잠정 데이터(확정치 미확인)" ;;
  esac
  { echo "$(TZ=Asia/Seoul date '+%F %H:%M') 현재 코드($REV)·${KIND}로 다시 만듦; 원본은 ${ORIGIN:-없음(이 루트에서 처음 만들었다)}"
    echo "모든 DB 읽기는 $ASOF 이하로 묶었다(수급·조정 종가·업종지수·주식수·수정주가 이벤트)."
    echo "묶지 못한 것: 섹터·시장 분류와 유니버스는 지금의 stocks 마스터다(이력이 없다)."
    echo "그날 저녁 게시본은 그때의 잠정치·그때의 정의로 만든 것이라 이 숫자와 다를 수 있다."
    echo "commit: $REV"
    echo "finalize: $FINALIZE_STATE"
  } > "$BUILD/REBUILT.txt"
fi
# 검증·품질 페이지(scripts/trust_page.py) — 이 판의 표시 파일(VERIFY·FINAL·REBUILT)을 다 쓴 뒤라야 그날 줄이 맞는다.
# 다른 기준일·원장·.archive 는 읽기만 한다. 재구성을 옆 루트에 지으면 KR_QUANT_TRUST_ROOT 가 운영 루트를 가리킨다(rebuild_history.py).
if ! _bounded "${KR_QUANT_TRUST_TIMEOUT:-5m}" "${UVR[@]}" python scripts/trust_page.py \
     --root "${KR_QUANT_TRUST_ROOT:-$OUT_ROOT}" --build "$BUILD" --asof "$ASOF"; then
  echo "[$(date '+%F %T')] ⚠️ 검증·품질 페이지를 못 만들었다 — 리포트는 그대로 게시한다" >&2
  SIDE_FAIL+="trust.html "
fi
if [ -n "$SIDE_FAIL" ]; then
  { echo; echo "부속 산출물 실패: ${SIDE_FAIL% } — 리포트 숫자와 무관하다. 다시 만들려면 KR_QUANT_FORCE=1 scripts/daily_report.sh"; } >> "$BUILD/VERIFY.txt" 2>/dev/null || true
fi
# 검증에 걸린 새 판이 검증을 통과한 게시본을 밀어내지 않는다(머리말) — 게시본·latest 를 그대로 두고 새 판은 비켜 둔다.
if [ "$VERIFY_RC" -ne 0 ] && [ -f "$DEST/numbers.html" ] && [ ! -f "$DEST/VERIFY_FAILED.txt" ]; then
  REJECTED="$OUT_ROOT/.rejected/$ASOF.$(date +%Y%m%d%H%M%S).$$"
  mkdir -p "$OUT_ROOT/.rejected"
  mv "$BUILD" "$REJECTED"
  BUILD=""
  EV_OUTCOME="rejected"
  echo "[$(date '+%F %T')] ⚠️ 새 판이 검증에 걸려 게시하지 않았다 — 검증을 통과한 $DEST 와 latest 를 그대로 둔다. 걸린 판: $REJECTED/VERIFY_FAILED.txt" >&2
  echo "    원인을 고친 뒤 같은 명령을 다시 돌린다. 걸린 판은 진단용이다 — 다 보면 지운다(rm -rf $OUT_ROOT/.rejected/$ASOF.*)." >&2
  exit 1
fi
# **바꿔 끼우기부터 latest 까지는 시그널에 끊기지 않는다.** 두 mv 사이에 크론 timeout 의 TERM 이 오면 트랩이 다 지은 $BUILD 를
# 지워 <기준일> 이 통째로 사라지고 latest 가 끊겼다(timeout 은 프로세스 그룹 전체에 보내 mv 자신도 죽는다). 그래서 이 구간은
# 시그널을 무시하는 서브셸에서 돌리고(무시는 자식 mv 에게 물려진다), 그 사이 이 셸에 온 시그널은 적어 두었다가 다 끝난 뒤
# 그 번호로 끝낸다. 재구성은 latest 를 뒤로 돌리지 않는다 — 지금 latest 보다 늦거나 같은 날일 때만 옮긴다. 링크는 옆에 새로
# 만들어 rename 으로 갈아 끼운다(ln -sfn 은 지웠다 다시 만들어 그 사이 latest 가 잠깐 없다).
CUR_LATEST="$(basename "$(readlink "$OUT_ROOT/latest" 2>/dev/null || echo none)")"
MOVE_LATEST=""
if [ -z "$REBUILD" ] || [ ! -d "$OUT_ROOT/$CUR_LATEST" ] || [[ ! "$ASOF" < "$CUR_LATEST" ]]; then
  MOVE_LATEST=1
fi
OLD="$DEST.replaced.$$"
HAD_DEST=""
[ ! -d "$DEST" ] || HAD_DEST=1
DEFER_SIG=""
trap 'DEFER_SIG=129' HUP
trap 'DEFER_SIG=130' INT
trap 'DEFER_SIG=143' TERM
(
  trap '' HUP INT TERM
  if [ -n "$HAD_DEST" ]; then
    mv "$DEST" "$OLD"
    mv "$BUILD" "$DEST"
    if [ -n "$REBUILD" ]; then
      mkdir -p "$OUT_ROOT/.archive"
      mv "$OLD" "$ARCHIVED"
    else
      rm -rf "$OLD"
    fi
  else
    mv "$BUILD" "$DEST"
  fi
  if [ -n "$MOVE_LATEST" ]; then
    ln -sfn "$DEST" "$OUT_ROOT/.latest.$$"
    mv -Tf "$OUT_ROOT/.latest.$$" "$OUT_ROOT/latest"
  fi
)
BUILD=""
_signal_traps
if [ -z "$HAD_DEST" ]; then
  :
elif [ -n "$REBUILD" ]; then
  echo "[$(date '+%F %T')] 바꿔 끼웠다 — $DEST (옛 폴더: $ARCHIVED)"
else
  echo "[$(date '+%F %T')] 바꿔 끼웠다 — $DEST"
fi
# 게시됐다 — 원장의 결말(검증에 걸렸어도 게시는 됐다. verify 필드가 그걸 말한다).
[ -z "$FINAL_OK" ] || EV_FINAL=true
# 게시한 숫자의 지문 — 같은 기준일을 다시 게시했을 때 정말 바뀌었는지를 --errata 가 가른다.
EV_SHA="$(sha256sum "$DEST/payload.json" 2>/dev/null | cut -d' ' -f1)" || true
if [ -n "$REBUILD" ]; then
  EV_OUTCOME="rebuilt"
elif [ -n "$FINAL_OK" ]; then
  EV_OUTCOME="final"
else
  EV_OUTCOME="published"
fi
[ -z "$DEFER_SIG" ] || exit "$DEFER_SIG"
if [ "$VERIFY_RC" -eq 0 ]; then
  echo "[$(date '+%F %T')] 완료 — $DEST 를 latest 로 게시했다"
  if [ -n "$FINAL_UNCONFIRMED" ] && [ -n "$REBUILD" ]; then
    echo "[$(date '+%F %T')] ⚠️ 확정치를 확인하지 못해 잠정으로 재구성했다 — daily_finalize 가 끝났으면: KR_QUANT_ASOF=$ASOF $0" >&2
    VERIFY_RC=75
  elif [ -n "$FINAL_UNCONFIRMED" ]; then
    echo "[$(date '+%F %T')] ⚠️ 확정치를 확인하지 못해 잠정치로 만들었다 — daily_finalize 를 확인하고 끝났으면: KR_QUANT_FINAL=1 $0" >&2
    VERIFY_RC=75
  fi
else
  echo "[$(date '+%F %T')] ⚠️ 게시했지만 검증에 걸렸다 — $DEST (latest). 화면과 HTML 이 ⚠ 로 표시하고, 같은 기준일이면 다음 크론이 다시 만든다."
fi
# 부속 산출물만 빠졌으면 경고다 — 이미 실패·미확인(75)이면 그쪽 알림에 본문으로 실린다.
if [ -n "$SIDE_FAIL" ] && [ "$VERIFY_RC" -eq 0 ]; then
  echo "[$(date '+%F %T')] ⚠️ 리포트는 게시했지만 부속 산출물을 못 만들었다(${SIDE_FAIL% }) — 위 ⚠️ 줄이 이유다. 고친 뒤 KR_QUANT_FORCE=1 $0" >&2
  VERIFY_RC=75
fi
ls -lh "$DEST" | tail -n +2 | awk '{print "   ", $9, $5}'
# 검증에 걸린 리포트도 게시한다(화면·HTML 이 ⚠ 로 말한다). 다만 크론에는 실패로 돌려 알림이 가게 한다.
exit "$VERIFY_RC"
