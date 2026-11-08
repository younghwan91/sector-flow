#!/usr/bin/env bash
# 섹터 자금흐름 일일 리포트 — 날짜 폴더에 재워둔다.
#
# 폴더 이름은 **데이터 기준일**(supply_demand 의 MAX(date))이지 실행일이 아니다.
# 휴장일에 돌면 전 거래일 폴더가 이미 있으므로 건너뛴다 — 같은 데이터로 폴더가
# 여러 개 생기는 걸 막는다.
#
# 종료코드: 0 = 게시했거나 건너뜀 · 75 = 오늘 기준일이 수집 미완이라 안 만듦(재실행하면 된다) ·
# 그 밖 = 오류·검증 실패·**지난** 기준일이 아직 수집 미완(1 — 재시도 창이 끝나 간다).
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

# **겹쳐 돌지 않는다.** 크론(18:10·19:10·08:20)끼리는 timeout 40m 이라 안 겹치지만, 손으로 돌린
# KR_QUANT_FORCE 와 크론이 겹치면 같은 기준일을 둘이 옆에 지어 바꿔 끼우다 서로의 폴더를 지운다.
# 잠금을 못 잡으면 75(경고)로 끝난다 — 0 으로 조용히 끝내면, 08:20 확정 모드가 손으로 돌린 잠정 실행에
# 밀렸을 때 그 기준일이 영영 잠정으로 남는데 아무도 모른다. 크론끼리는 안 겹치므로 이 경고는 드물다.
# 자식(파이썬)도 fd 9 를 물려받아, 셸이 죽어도 자식이 다 끝날 때까지 잠금이 남는다.
mkdir -p "$OUT_ROOT"
LOCK="$OUT_ROOT/.report.lock"
exec 9>>"$LOCK"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] ⚠️ 다른 리포트 실행이 돌고 있다($LOCK) — 이번 실행은 건너뛴다. 그쪽이 끝난 뒤 필요하면 다시 돌려라." >&2
  exit 75
fi

# 실패 사유는 보여 준다 — 버리면 `set -e` 때문에 아래 안내까지 못 가고 빈 알림으로 끝난다.
# libpq 가 접속 문자열을 되읊을 수 있어 비밀번호 자리는 가린다.
ASOF_ERR="$(mktemp)"
ASOF_RC=0
ASOF="$("${UVR[@]}" python - <<'PY' 2>"$ASOF_ERR"
import os
from sectorflow.storage import connect, db_default
con = connect(os.environ.get("KR_QUANT_DB") or db_default())
cur = con.cursor(); cur.execute("SELECT max(date) FROM supply_demand")
print(cur.fetchone()[0]); con.close()
PY
)" || ASOF_RC=$?
if [ "$ASOF_RC" -ne 0 ]; then
  echo "[$(date '+%F %T')] DB 에서 기준일을 못 읽었다(rc=$ASOF_RC) — 접속정보(KR_QUANT_DB)와 DB 서버를 확인하라. 중단." >&2
  tail -n 3 "$ASOF_ERR" | sed -E 's#://[^@ ]*@#://***@#g' >&2
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
TODAY="$(TZ=Asia/Seoul date +%F)"

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
if [ -f "$DEST/FINAL.txt" ]; then
  FINAL_OK=1
  if [ -n "${KR_QUANT_FINAL:-}" ] && [ -z "${KR_QUANT_FORCE:-}" ] && [ ! -f "$DEST/VERIFY_FAILED.txt" ]; then
    echo "[$(date '+%F %T')] $ASOF 는 이미 확정치로 만들었다 — 건너뛴다."
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
  exit 0
fi

# 완전성 관문 — 만들기 **전에** 본다. 빈 시장으로 만든 리포트를 검사해서 거르는 것보다 안 만드는
# 편이 낫다: 10-27 의 리포트는 검사에 걸리고도 latest 가 됐다. 문턱과 근거는 그 스크립트 머리말.
# KR_QUANT_FORCE 는 건너뛰기만 끄고 이 관문은 못 끈다 — 부분 수집 위에 멀쩡한 리포트를 덮으면 안 된다.
GATE_RC=0
GATE_OUT="$("${UVR[@]}" python scripts/completeness_gate.py --asof "$ASOF")" || GATE_RC=$?
echo "$GATE_OUT"
if [ "$GATE_RC" -eq 75 ]; then
  # 이미 리포트가 있는 폴더(강제 재생성·검증 실패 재시도)에는 표시를 안 남긴다 — 옛 리포트는 그대로다.
  if [ ! -f "$DEST/numbers.html" ]; then
    mkdir -p "$DEST"
    { echo "[$(date '+%F %T')] $ASOF 수집 미완 — 리포트를 만들지 않았다. latest 는 그대로다."
      if [[ "$ASOF" < "$TODAY" ]]; then
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
  # 아무도 안 만든다 — 다음날 07:30 의 포기가 경고로 묻히지 않게 오류로 끝낸다.
  if [[ "$ASOF" < "$TODAY" ]]; then
    echo "[$(date '+%F %T')] ⚠️ $ASOF 는 오늘이 아닌데 수집 미완이다 — 수집을 메운 뒤 이 스크립트를 다시 돌려라. 기준일이 넘어가면 이 날은 안 만들어진다." >&2
    exit 1
  fi
  exit 75
elif [ "$GATE_RC" -ne 0 ]; then
  echo "[$(date '+%F %T')] 완전성 관문 오류(rc=$GATE_RC) — 중단." >&2
  exit "$GATE_RC"
fi

# **언제나 옆에 짓고 마지막에 옮긴다.** 제자리에 쓰면 5분 남짓 동안 폴더 안에 새 payload 와 옛
# numbers.html 이 섞이고, 처음 만드는 날에 도중에 죽으면 numbers.html 만 남아 다음 재시도가
# "이미 있다" 며 건너뛴다 — latest 는 어제에 머무는데 알림도 없다.
BUILD="$DEST.building.$$"
rm -rf "$BUILD"
trap 'rm -rf "$BUILD"' EXIT
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
"${UVR[@]}" python scripts/sector_flow.py    --days "$DAYS" --json "$BUILD/payload.json" "${FINAL_ARGS[@]}"
"${UVR[@]}" python scripts/sector_flow.py    --from-json "$BUILD/payload.json" --html "$BUILD/viewer.html"
"${UVR[@]}" python scripts/sector_numbers.py --payload "$BUILD/payload.json" --html "$BUILD/numbers.html"

# 저장한 것을 그 자리에서 검증한다 — 실패하면 폴더에 VERIFY_FAILED 를 남긴다.
# 조용히 틀린 리포트가 쌓이는 것이 검증 없이 도는 것보다 나쁘다.
VERIFY_RC=0
if "${UVR[@]}" python scripts/verify_report.py --dir "$BUILD" --db-check \
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

if [ -n "$FINAL_OK" ]; then
  echo "[$(date '+%F %T')] daily_finalize 확인 뒤 확정치로 만들었다." > "$BUILD/FINAL.txt"
  # 잠정 → 확정에서 바뀐 머리 숫자(sector_flow.py --final 이 남긴다).
  if [ -f "$BUILD/final_diff.txt" ]; then cat "$BUILD/final_diff.txt" >> "$BUILD/FINAL.txt"; fi
fi
if [ -d "$DEST" ]; then
  OLD="$DEST.replaced.$$"
  mv "$DEST" "$OLD"
  mv "$BUILD" "$DEST"
  rm -rf "$OLD"
  echo "[$(date '+%F %T')] 바꿔 끼웠다 — $DEST"
else
  mv "$BUILD" "$DEST"
fi
trap - EXIT

ln -sfn "$DEST" "$OUT_ROOT/latest"
if [ "$VERIFY_RC" -eq 0 ]; then
  echo "[$(date '+%F %T')] 완료 — $DEST 를 latest 로 게시했다"
  if [ -n "$FINAL_UNCONFIRMED" ]; then
    echo "[$(date '+%F %T')] ⚠️ 확정치를 확인하지 못해 잠정치로 만들었다 — daily_finalize 를 확인하고 끝났으면: KR_QUANT_FINAL=1 $0" >&2
    VERIFY_RC=75
  fi
else
  echo "[$(date '+%F %T')] ⚠️ 게시했지만 검증에 걸렸다 — $DEST (latest). 화면과 HTML 이 ⚠ 로 표시하고, 같은 기준일이면 다음 크론이 다시 만든다."
fi
ls -lh "$DEST" | tail -n +2 | awk '{print "   ", $9, $5}'
# 검증에 걸린 리포트도 게시한다(화면·HTML 이 ⚠ 로 말한다). 다만 크론에는 실패로 돌려 알림이 가게 한다.
exit "$VERIFY_RC"
