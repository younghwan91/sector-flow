#!/usr/bin/env bash
# 섹터 자금흐름 일일 리포트 — 날짜 폴더에 재워둔다.
#
# 폴더 이름은 **데이터 기준일**(supply_demand 의 MAX(date))이지 실행일이 아니다.
# 휴장일에 돌면 전 거래일 폴더가 이미 있으므로 건너뛴다 — 같은 데이터로 폴더가
# 여러 개 생기는 걸 막는다.
#
# 종료코드: 0 = 게시했거나 건너뜀 · 75 = 수집 미완이라 안 만듦(재실행하면 된다) ·
# 그 밖 = 오류·검증 실패. 몇 번을 돌려도 안전하다 — 크론이 재시도로 같은 명령을 다시 부른다.
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
UV="${KR_QUANT_UV:-/home/young/.local/bin/uv}"
# ⚠️ extra 를 명시한다. `uv run` 은 기본 의존성만 맞추므로, 누가 `uv sync --extra dev`
# 를 돌리면 psycopg2(pg extra)가 사라지고 이 배치가 조용히 죽는다 — 실제로 그렇게
# 한 번 빠졌다. 여기서 못박아 두면 실행 시점에 항상 복원된다.
UVR=("$UV" run --quiet --extra pg)

cd "$REPO"
# 접속정보의 사본을 레포마다 두지 않는다 — 없으면 kr_quant 의 정본(quant-airflow/.env)을 불러 쓴다.
ENV_FILE="$REPO/.env"
[ -f "$ENV_FILE" ] || ENV_FILE="$REPO/../quant-airflow/.env"
# 워크트리(~/orca/workspaces/...)에는 옆자리 quant-airflow 가 없다.
[ -f "$ENV_FILE" ] || ENV_FILE="$HOME/git/quant-airflow/.env"
set -a; . "$ENV_FILE"; set +a

ASOF="$("${UVR[@]}" python - <<'PY' 2>/dev/null
import os
from sectorflow.storage import connect, db_default
con = connect(os.environ.get("KR_QUANT_DB") or db_default())
cur = con.cursor(); cur.execute("SELECT max(date) FROM supply_demand")
print(cur.fetchone()[0]); con.close()
PY
)"

# 빈 테이블이면 파이썬이 "None" 을 찍는다 — 그대로 두면 reports/None 폴더가 생긴다.
case "$ASOF" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
  *)
    echo "[$(date '+%F %T')] 기준일을 못 읽었다(${ASOF:-빈 값}) — DB 접속 실패로 본다. 중단." >&2
    exit 1
    ;;
esac

DEST="$OUT_ROOT/$ASOF"
# 건너뛰기는 **데이터**가 그대로일 때를 위한 것이다(휴장일 재실행). 리포트에 실리는
# 값이 늘어났을 때는 데이터가 같아도 다시 만들어야 하는데, 그럴 방법이 없었다 —
# 열을 추가하고 나서 사용자 화면에 새 열이 통째로 비어 있었고, 원인은 코드가 아니라
# 폴더가 이미 있어서 이 배치가 조용히 건너뛴 것이었다. 다음 거래일까지 그 상태로
# 남는다. KR_QUANT_FORCE=1 로 그 문을 연다.
#
# 수집 미완(INCOMPLETE.txt 만 있는 폴더)과 검증 실패(VERIFY_FAILED.txt)는 건너뛰지 않는다 —
# 다음 재시도가 그 기준일을 다시 만들 유일한 기회다. 기준일이 넘어가면 아무도 안 만든다.
if [ -f "$DEST/numbers.html" ] && [ ! -f "$DEST/VERIFY_FAILED.txt" ] && [ -z "${KR_QUANT_FORCE:-}" ]; then
  echo "[$(date '+%F %T')] $ASOF 리포트가 이미 있다 — 건너뛴다(휴장일이거나 재실행)."
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
      echo "다음 크론(19:10, 다음날 07:30)이 다시 시도하고, 성공하면 이 폴더가 통째로 바뀐다."
      echo "$GATE_OUT"
    } > "$DEST/INCOMPLETE.txt"
  fi
  echo "[$(date '+%F %T')] ⚠️ $ASOF 수집 미완 — 게시하지 않는다(latest 유지): $DEST/INCOMPLETE.txt" >&2
  exit 75
elif [ "$GATE_RC" -ne 0 ]; then
  echo "[$(date '+%F %T')] 완전성 관문 오류(rc=$GATE_RC) — 중단." >&2
  exit "$GATE_RC"
fi

# 강제 재생성은 **옆에 짓고 마지막에 바꿔 끼운다.** 제자리에 덮어쓰면 5분 남짓
# 동안 폴더 안에 새 payload 와 옛 numbers.html 이 섞여 있고, 그 사이에 sw-flow 를
# 띄운 사람은 어느 쪽도 아닌 화면을 본다. 실패하면 옛 리포트가 그대로 남는다.
BUILD="$DEST"
SWAP=""
if [ -d "$DEST" ]; then
  BUILD="$DEST.building.$$"
  SWAP="1"
  rm -rf "$BUILD"
  echo "[$(date '+%F %T')] $ASOF 리포트를 **다시** 만든다 — 옆에 짓고 바꿔 낀다"
fi
DEST_FINAL="$DEST"
DEST="$BUILD"

mkdir -p "$DEST"
echo "[$(date '+%F %T')] 생성 시작 — 기준일 $ASOF, 구간 ${DAYS}거래일"
# 페이로드를 **한 번만** 만들고 둘 다 그걸 쓴다. 월별 시총 계산이 수 분이라
# 예전처럼 세 번 돌리면 배치가 15분을 넘고 그만큼 실패 창이 넓어진다.
"${UVR[@]}" python scripts/sector_flow.py    --days "$DAYS" --json "$DEST/payload.json"
"${UVR[@]}" python scripts/sector_flow.py    --from-json "$DEST/payload.json" --html "$DEST/viewer.html"
"${UVR[@]}" python scripts/sector_numbers.py --payload "$DEST/payload.json" --html "$DEST/numbers.html"

# 저장한 것을 그 자리에서 검증한다 — 실패하면 폴더에 VERIFY_FAILED 를 남긴다.
# 조용히 틀린 리포트가 쌓이는 것이 검증 없이 도는 것보다 나쁘다.
VERIFY_RC=0
if "${UVR[@]}" python scripts/verify_report.py --dir "$DEST" --db-check \
     > "$DEST/VERIFY.txt" 2>&1; then
  echo "[$(date '+%F %T')] 검증 통과 — $DEST/VERIFY.txt"
else
  cp "$DEST/VERIFY.txt" "$DEST/VERIFY_FAILED.txt"
  echo "[$(date '+%F %T')] ⚠️ 검증 실패 — $DEST/VERIFY_FAILED.txt 확인" >&2
  tail -5 "$DEST/VERIFY.txt" >&2
  VERIFY_RC=1
fi

if [ -n "$SWAP" ]; then
  OLD="$DEST_FINAL.replaced.$$"
  mv "$DEST_FINAL" "$OLD"
  mv "$DEST" "$DEST_FINAL"
  rm -rf "$OLD"
  DEST="$DEST_FINAL"
  echo "[$(date '+%F %T')] 바꿔 끼웠다 — $DEST"
fi

ln -sfn "$DEST" "$OUT_ROOT/latest"
echo "[$(date '+%F %T')] 완료 — $DEST"
ls -lh "$DEST" | tail -n +2 | awk '{print "   ", $9, $5}'
# 검증에 걸린 리포트도 게시한다(화면이 ⚠ 로 말한다). 다만 크론에는 실패로 돌려 알림이 가게 한다.
exit "$VERIFY_RC"
