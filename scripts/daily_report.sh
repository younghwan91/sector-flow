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
EV_ASOF="" EV_OUTCOME="error" EV_GATE="" EV_VERIFY="" EV_CHECKS="" EV_FAILS="" EV_FINAL="" EV_DIFF=""
_dr_event() {
  local rc="$1"
  # 옆에 짓던 폴더 청소 — 성공해 옮겼으면($BUILD 없음) 아무 일도 없다. 원장 기록과 한 트랩에 묶는다.
  [ -z "${BUILD:-}" ] || rm -rf "$BUILD" 2>/dev/null || true
  local commit
  commit="${KR_QUANT_CODE_REV:-$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)}"
  sf_event_append "$OUT_ROOT" "{\"ts\":\"$(date -u +%FT%TZ)\",\"event\":\"report\",\"asof\":$(sf_q "$EV_ASOF"),\"mode\":\"$EV_MODE\",\"rc\":$rc,\"outcome\":\"$EV_OUTCOME\",\"gate\":$(sf_q "$EV_GATE"),\"verify\":$(sf_q "$EV_VERIFY"),\"verify_checks\":${EV_CHECKS:-null},\"verify_fails\":${EV_FAILS:-null},\"final\":${EV_FINAL:-false},\"diff\":${EV_DIFF:-null},\"commit\":$(sf_q "$commit"),\"started\":\"$EV_STARTED\"}"
}

# **겹쳐 돌지 않는다.** 크론(18:10·19:10·08:20)끼리는 timeout 40m 이라 안 겹치지만, 손으로 돌린
# KR_QUANT_FORCE 와 크론이 겹치면 같은 기준일을 둘이 옆에 지어 바꿔 끼우다 서로의 폴더를 지운다.
# 잠금을 못 잡으면 75(경고)로 끝난다 — 0 으로 조용히 끝내면, 08:20 확정 모드가 손으로 돌린 잠정 실행에
# 밀렸을 때 그 기준일이 영영 잠정으로 남는데 아무도 모른다. 크론끼리는 안 겹치므로 이 경고는 드물다.
# 자식(파이썬)도 fd 9 를 물려받아, 셸이 죽어도 자식이 다 끝날 때까지 잠금이 남는다.
mkdir -p "$OUT_ROOT"
trap '_dr_event $?' EXIT
LOCK="$OUT_ROOT/.report.lock"
exec 9>>"$LOCK"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] ⚠️ 다른 리포트 실행이 돌고 있다($LOCK) — 이번 실행은 건너뛴다. 그쪽이 끝난 뒤 필요하면 다시 돌려라." >&2
  EV_OUTCOME="locked"
  exit 75
fi

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
if [ -f "$DEST/numbers.html" ] && [ ! -f "$DEST/VERIFY_FAILED.txt" ] && [ -z "${KR_QUANT_FORCE:-}" ] \
   && [ -z "$FINAL_REBUILD" ]; then
  echo "[$(date '+%F %T')] $ASOF 리포트가 이미 있다 — 건너뛴다(휴장일이거나 재실행)."
  # 달력이 없어 휴장과 수집 실패를 못 가른다 — 갈라야 할 사람에게 사실을 준다.
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
echo "[$(date '+%F %T')] 생성 시작 — 기준일 $ASOF, 구간 ${DAYS}거래일"
# 페이로드를 **한 번만** 만들고 둘 다 그걸 쓴다. 월별 시총 계산이 수 분이라
# 예전처럼 세 번 돌리면 배치가 15분을 넘고 그만큼 실패 창이 넓어진다.
FINAL_ARGS=()
if [ -n "$FINAL_OK" ]; then
  FINAL_ARGS=(--final)
  echo "[$(date '+%F %T')] 확정치로 만든다 — 제목이 '마감·확정' 이 된다"
fi
VERIFY_ARGS=()
if [ -n "$REBUILD" ]; then
  FINAL_ARGS+=(--asof "$ASOF" --rebuilt)
  [ "$FINALIZE_STATE" != "unverified" ] || FINAL_ARGS+=(--finalize-unverified)
  VERIFY_ARGS=(--asof "$ASOF")
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
# 선택(기본 꺼짐): 오늘의 수급 다이제스트 digest.md·json·png(scripts/digest.py) — 보내지 않고 폴더에만 쓴다. 실패해도 리포트는 게시한다.
if [ -n "${KR_QUANT_DIGEST:-}" ]; then "${UVR[@]}" --with playwright python scripts/digest.py --dir "$BUILD" || echo "[$(date '+%F %T')] ⚠️ 다이제스트를 못 만들었다 — 리포트는 그대로 게시한다" >&2; fi

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
if [ -d "$DEST" ]; then
  OLD="$DEST.replaced.$$"
  mv "$DEST" "$OLD"
  mv "$BUILD" "$DEST"
  if [ -n "$REBUILD" ]; then
    mkdir -p "$OUT_ROOT/.archive"
    mv "$OLD" "$ARCHIVED"
    echo "[$(date '+%F %T')] 바꿔 끼웠다 — $DEST (옛 폴더: $ARCHIVED)"
  else
    rm -rf "$OLD"
    echo "[$(date '+%F %T')] 바꿔 끼웠다 — $DEST"
  fi
else
  mv "$BUILD" "$DEST"
fi
# 게시됐다 — 원장의 결말(검증에 걸렸어도 게시는 됐다. verify 필드가 그걸 말한다).
[ -z "$FINAL_OK" ] || EV_FINAL=true
if [ -n "$REBUILD" ]; then
  EV_OUTCOME="rebuilt"
elif [ -n "$FINAL_OK" ]; then
  EV_OUTCOME="final"
else
  EV_OUTCOME="published"
fi

# 재구성은 latest 를 뒤로 돌리지 않는다 — 지금 latest 보다 늦거나 같은 날일 때만 옮긴다.
CUR_LATEST="$(basename "$(readlink "$OUT_ROOT/latest" 2>/dev/null || echo none)")"
if [ -z "$REBUILD" ] || [ ! -d "$OUT_ROOT/$CUR_LATEST" ] || [[ ! "$ASOF" < "$CUR_LATEST" ]]; then
  ln -sfn "$DEST" "$OUT_ROOT/latest"
fi
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
ls -lh "$DEST" | tail -n +2 | awk '{print "   ", $9, $5}'
# 검증에 걸린 리포트도 게시한다(화면·HTML 이 ⚠ 로 말한다). 다만 크론에는 실패로 돌려 알림이 가게 한다.
exit "$VERIFY_RC"
