# SLO — 언제까지 나와야 "제때"인가

리포트 배치(README §3)의 서비스 목표다. 측정은 전부 운영 사건 원장 `reports/events.jsonl` 로 한다 —
폴더 바꿔 끼우기가 흔적(INCOMPLETE·mtime)을 지우고 `cron.log` 는 5MB 에서 돌아, 원장 없이는 나중에 잴 수 없다.
형식·통계는 [`scripts/ops_ledger.py`](../scripts/ops_ledger.py) 머리말, 쓰는 쪽은 `daily_report.sh`·`cron_run.sh`·`serve.sh`([`scripts/_ledger.sh`](../scripts/_ledger.sh))와
`freshness_check.py` 다. 원장은 웹 허용 목록에 없다 — 밖에 안 보인다.

| 항목 | 목표 | 근거 |
|---|---|---|
| 잠정 리포트 게시 | 거래일 당일 **18:30 KST** 까지 | 18:10 크론 + 빌드 2~5분. 19:10 은 재시도라 그때 게시되면 이미 늦은 날이다 |
| 확정 재생성 | 다음날 **08:40 KST** 목표, **10:40 KST** SLO | 08:20 확정 모드 + 빌드. `daily_finalize` 가 늦은 날은 10:20 재시도가 받는다 — 10:40 을 넘기면 위반 |
| 웹 가용 | 5분 프로브(`/healthz`)가 **200** | `serve.sh` 크론이 5분마다 묻고 원장에 `probe` 사건을 남긴다. 서버가 내려가 있던 주기는 줄 자체가 없다(그 주기는 재기동) — 가용률은 "프로브 중 200 비율"이고 공백은 따로 본다 |

코드의 정본 상수는 `ops_ledger.PROV_SLO_KST`·`FINAL_TARGET_KST`·`FINAL_SLO_KST` 다(이 표와 같다 —
`tests/test_ops_ledger.py` 가 대조한다).

**감시**: 평일 20:30 `freshness_check.py` 가 전 거래일을 원장으로 재서 기한을 넘긴 **기록이 있으면** 경고(75)한다
(`⚠️ [SLO]` — 할 일은 `docs/runbook.md`). 원장에 그날 기록이 없으면(도입 전·백필 공백) 재지 않는다고만 적는다 —
게시·확정이 아예 없는 날은 기존 신선도·확정 점검이 잡는다.

**집계**: `uv run python scripts/ops_ledger.py`(최근 30일, `--days 90`·`--json`) — 게시 준시성, 관문 차단, 검증 실패,
확정 지연, 재구성·정정. `--errata` 는 첫 게시 뒤 숫자가 바뀐 날(재구성·잠정→확정 칸 변경)만 나열한다.

**백필(한 번)**: 원장 도입 전의 과거는 남아 있는 것(cron.log·폴더 표시 파일·`.archive`)에서
`uv run python scripts/ops_ledger.py --backfill` 로 복원했다. 그 사건들은 `source: "backfill"` 이 붙는다 —
시각이 mtime 기반인 줄도 있어(재생성마다 리셋) 통계가 출처를 따로 센다. 두 번 돌리면 거부한다(`--force` 로만 추가).
