# sector-flow — Claude 작업 지침

코스피·코스닥 섹터 자금흐름 일일 리포트(`scripts/daily_report.sh`)와 터미널 화면(`sw-flow`·`sw-ledger`). 2026-11-02 swing-it 에서
히스토리째 분리했다. 수집은 quant-airflow, 알파 심사·전략은 swing-it 소관이다. 여기는 관측만 한다.

## 명령
- 테스트: `uv run --extra dev pytest -q` — DB·네트워크 불요.
- 린트: `uv run --extra dev ruff check src/ tests/ scripts/` (CI 와 같은 범위)
- TUI 수정 뒤: `uv run python scripts/verify_merge.py <워크트리>` (수 분).
- 리포트 다시 만들기: `KR_QUANT_FORCE=1 scripts/daily_report.sh`. 다른 곳에 쓰려면 `KR_QUANT_REPORTS=<폴더>`.
- 화면: `uv run sw-flow`, `uv run sw-ledger` (`reports/latest` 를 연다).
- DB(TimescaleDB `kr_quant`, **읽기 전용**): `set -a; . ~/git/quant-airflow/.env; set +a` 뒤 `$KR_QUANT_DB`, psycopg2 는 `--extra pg`.
  `daily_report.sh` 는 `.env` 가 없으면 같은 파일을 불러 쓴다. 접속정보 사본을 이 레포에 두지 않는다.
  임시 조회엔 `statement_timeout`.

## 운영
- 크론 정본 `deploy/crontab.simnode` — dotfiles `cron-install` 이 `~/git/*/deploy/crontab.<호스트>` 를 훑어 설치한다. `crontab -e` 금지.
  07:50 `pull-all --ci-gate` 가 ff 한 뒤 설치하니 main 푸시가 곧 다음날 배포다. 당장은 `git -C ~/git/sector-flow pull --ff-only`.
- 산출물은 `$REPO/reports/<기준일>`(gitignore) + `latest` 링크. 폴더 이름은 실행일이 아니라 데이터 기준일이다.
- 이 레포는 PRIVATE 다. 공개 전환은 사용자가 정한다.

## 데이터 함정 (kr_quant)
- `supply_demand` 의 주체 열은 금액이 아니라 순매매 **수량**(× 종가는 근사). `flu_rt` 는 등락률 ×100. 네 주체 합 ≈ 0 이라 `etc_corp` 를 빼면 안 된다. 0 은 "0 또는 미보고", NULL 은 모름.
- `daily_bars.trade_value` 단위는 **백만원**이다.
- 2026-09-14 이후 `daily_bars` 의 고가·저가·종가·거래량은 애프터마켓 포함 통합값이다(시가만 정규장). 정의는 quant-airflow `CLAUDE.md` §3·`docs/schema.md`.
- 시총 = `daily_bars` 종가 × 그 시점 상장주식수(as-of). `supply_demand.close` 와 다른 시계열이다(`storage.market_cap_asof_bulk`).
- `storage.py` 는 swing-it 에서 복사한 독립 사본이다. 공유 라이브러리로 묶지 않는다.

## 화면·리포트 규칙
- 화면은 수급 사실만 말한다. 리포트에는 구간 수익률 4개뿐이라 경로를 모른다. 그래서 눌림목·반전 **판정**을 점수화하는
  제안은 하지 않는다(`reversal_flags` 의 ⚠ 는 판정 불가 라벨이다).
- 비율 열은 분모 게이트를 같이 설계한다. 분모가 작으면 숫자 대신 결측을 낸다. 문턱은 목록 안 몫으로 잡는다.
  이 결함이 다섯 번 나왔다(`flow_view.py` 최근집중 주석).
- 수급 신뢰 순서: 기관 순매수만으로 충분하고 외국인 동반은 요구하지 않는다. 기관 안에서는 투신·연기금(`INST_DETAIL`)을 우선한다.
- 새 검사를 넣으면 `tests/mutations.toml` 에 변이도 넣는다(`verify_merge.py` 가 주입해 검사가 죽었는지 본다).
- `perf` 변경은 리포트가 바이트 동일함을 확인하고 메시지에 적는다.

## 커밋
- 한국어 conventional(`fix(flow): ...`). 신원은 레포 설정 그대로, 에이전트는 `Co-Authored-By` 로만.
