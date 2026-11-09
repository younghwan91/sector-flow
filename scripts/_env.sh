# _env.sh — daily_report.sh·freshness_check.sh 가 `source` 하는 공용 머리말. 혼자 돌리지 않는다.
#
# 부르는 쪽이 REPO 를 정해 둔다. 여기서 정하는 것: UV·UVR(`uv run` 명령 배열), ENV_FILE(불러 쓴 .env)과
# 그 안의 변수(KR_QUANT_DB·ALERT_WEBHOOK_URL 등). 실패하면 이유를 찍고 부른 스크립트째 끝낸다.

# 크론의 PATH 에는 ~/.local/bin 이 없다 — PATH 에 없으면 uv 기본 설치 자리를 본다.
UV="${KR_QUANT_UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"
if ! command -v "$UV" >/dev/null 2>&1; then
  echo "[$(date '+%F %T')] uv 를 찾지 못했다($UV) — 설치하거나 KR_QUANT_UV 로 경로를 지정하라: https://docs.astral.sh/uv/" >&2
  exit 127
fi
# ⚠️ extra 를 명시한다. `uv run` 은 기본 의존성만 맞추므로, 누가 `uv sync --extra dev`
# 를 돌리면 psycopg2(pg extra)가 사라지고 이 배치가 조용히 죽는다 — 실제로 그렇게
# 한 번 빠졌다. 여기서 못박아 두면 실행 시점에 항상 복원된다.
UVR=("$UV" run --quiet --extra pg)

# 접속정보의 사본을 레포마다 두지 않는다 — 없으면 kr_quant 의 정본(quant-airflow/.env)을 불러 쓴다.
ENV_FILE="$REPO/.env"
[ -f "$ENV_FILE" ] || ENV_FILE="$REPO/../quant-airflow/.env"
# 워크트리(~/orca/workspaces/...)에는 옆자리 quant-airflow 가 없다.
[ -f "$ENV_FILE" ] || ENV_FILE="$HOME/git/quant-airflow/.env"
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
elif [ -z "${KR_QUANT_DB:-}" ]; then
  echo "[$(date '+%F %T')] DB 접속정보가 없다 — .env 도 quant-airflow/.env 도 KR_QUANT_DB 도 없다." >&2
  echo "    cp .env.example .env 뒤 KR_QUANT_DB 를 채워라. 화면만 볼 때는 이 배치가 필요 없다 — 합성 데이터는 uv run sw-flow --demo, 받은 폴더는 --dir <폴더>(README §4.3)." >&2
  exit 1
fi
