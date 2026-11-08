# sector-flow — Claude 작업 지침

코스피·코스닥 섹터 자금흐름 일일 리포트(`scripts/daily_report.sh`)와 터미널 화면(`sw-flow`·`sw-ledger`). 2026-11-02 swing-it 에서
히스토리째 분리했다. 수집은 quant-airflow, 알파 심사·전략은 swing-it 소관이다. 여기는 관측만 한다.

## 명령
- 테스트: `uv run --extra dev pytest -q` — DB·네트워크 불요.
- 린트: `uv run --extra dev ruff check src/ tests/ scripts/ research/` 와 `uv run python -m compileall -q research` (CI 의 Lint·Research compiles 와 같다)
- TUI 수정 뒤: `uv run python scripts/verify_merge.py <워크트리> --only render,pty` (수 분). 인자 없이 돌리면 변이 전부까지라 약 47분(바뀐 범위만은 `--scope changed`, `--help`).
  변이 범위: 로컬 pre-push 는 `changed`(바뀐 파일에 닿는 것), PR CI 는 `direct`(직접 건드린 것), main push 는 전체 4샤드, 문서만 바뀌면 안 돈다. 그 이유와 `pull-all --ci-gate` 와의 관계는 `.github/workflows/ci.yml` 머리말.
- 리포트 다시 만들기: `KR_QUANT_FORCE=1 scripts/daily_report.sh`. 다른 곳에 쓰려면 `KR_QUANT_REPORTS=<폴더>`.
- 지난 기준일 재구성(지금 코드·확정 데이터, 읽기는 그날 이하): `KR_QUANT_ASOF=YYYY-MM-DD scripts/daily_report.sh`, 여러 날은
  `scripts/rebuild_history.py --root <폴더> --from … --to …`(오래된 날부터 — `change` 가 직전 폴더를 읽는다). 폴더에 `REBUILT.txt`(`commit:` 전체 sha·`finalize:`).
  origin/main 의 깨끗한 체크아웃에서만 돈다(`--allow-unmerged` 는 리허설). daily_finalize 창(3일) 밖의 날은 `finalize: unverified`
  — FINAL.txt 없이 '확정 기록 없음' 으로 표시한다.
- 화면: `uv run sw-flow`, `uv run sw-ledger` (`reports/latest` 를 연다).
- 검증·품질 페이지: `uv run python scripts/trust_page.py` → `reports/latest/trust.html`(웹 `/latest/trust.html`). 배치에 붙이는 법은 그 머리말.
- DB(TimescaleDB `kr_quant`, **읽기 전용**): `set -a; . ~/git/quant-airflow/.env; set +a` 뒤 `$KR_QUANT_DB`, psycopg2 는 `--extra pg`.
  `daily_report.sh` 는 `.env` 가 없으면 같은 파일을 불러 쓴다. 접속정보 사본을 이 레포에 두지 않는다.
  임시 조회엔 `statement_timeout`.

## 운영
- 크론 정본 `deploy/crontab.simnode` — dotfiles `cron-install` 이 `~/git/*/deploy/crontab.<호스트>` 를 훑어 설치한다. `crontab -e` 금지.
  07:50 `pull-all --ci-gate` 가 ff 한 뒤 설치하니 main 푸시가 곧 다음날 배포다. 당장은 `git -C ~/git/sector-flow pull --ff-only`.
- 산출물은 `$REPO/reports/<기준일>`(gitignore) + `latest` 링크. 폴더 이름은 실행일이 아니라 데이터 기준일이다.
- 크론은 평일 18:10·19:10, 화~토 08:20·10:20(확정 모드 `KR_QUANT_FINAL=1`, 10:20 은 재시도)에 같은 `daily_report.sh` 를 부른다. 08:20 은 `daily_finalize` 성공을
  Airflow 로그로 확인하고(`scripts/finalize_done.py`) 전날을 확정치로 다시 만든다(`FINAL.txt`). 확인 못 하면 75, 이미 확정이면 0 으로 건너뛴다. 완전성 관문(`scripts/completeness_gate.py`)이 막으면
  폴더에 `INCOMPLETE.txt` 만 남기고 75 로 끝나며(`latest` 유지) 다음 실행이 그 기준일을 다시 만든다. `KR_QUANT_FORCE` 도 관문은 못 끈다.
  이상 종료는 `scripts/cron_run.sh` 가 quant-airflow `alert.sh` 로 알리고, 크론 로그가 5MB 를 넘으면 `.1` 로 돌린다. 평일 20:30 `scripts/freshness_check.sh` 가 결과(`latest`·알림 웹훅)를 본다.
  상한: 크론 `timeout 40m`, `reports/.report.lock`(겹치면 75), DB 접속 10초·문장 10분(`storage.connect`). 알림별 대응은 README §3.1.
- 이 레포는 PRIVATE 다. 공개 전환은 사용자가 정한다.

## 데이터 함정 (kr_quant)
- `supply_demand` 의 주체 열은 금액이 아니라 순매매 **수량**(× 종가는 근사). `flu_rt` 는 등락률 ×100 이고 09-14 부터 이어 곱할 수 없다 — 수익률은 `daily_bars_adjusted` 종가 비로. 네 주체 합 ≈ 0 이라 `etc_corp` 를 빼면 안 된다. 0 은 "0 또는 미보고", NULL 은 모름.
- `daily_bars.trade_value` 단위는 **백만원**이다.
- 수급·일봉·지수는 모두 **KRX 단독**이다 — 키움 TR 을 접미사 없는 코드로 불러 넥스트레이드(`_NX`) 체결이 없다. 2026-09-14~ 섞인 것은 NXT 가 아니라
  KRX 애프터마켓이다(swing-it 의 "NXT 오염"은 오기). 근거 `docs/formulas.md` §1.0, 화면 표지 `sectorflow.basis.DATA_BASIS`.
- 2026-09-14 이후 `daily_bars` 의 고가·저가·종가·거래량은 애프터마켓 포함 통합값이다(시가만 정규장). 정의는 quant-airflow `CLAUDE.md` §3·`docs/schema.md`.
  그래서 리포트의 하루 수익률은 전일 20:00 → 당일 20:00(통합 종가)이고 KRX 지수(정규장 15:30)와 같은 날 갈린다(11-06 −4.37% vs −6.02%).
  화면·웹이 `KRX 지수(정규장)` 를 나란히 적는 이유다. 정규장 수익률이라고 쓰지 않는다.
- 시총 = **원 종가**(`supply_demand.close`, 없으면 `daily_bars_raw`) × 그 시점 상장주식수(as-of 스냅샷 뒤의 분할·병합·감자를 다시 센다). `daily_bars` 는 키움 수정주가라
  시총에 쓰면 과거가 배수로 부푼다(`storage.market_cap_asof_bulk`, 2026-11-08 검증 `docs/validation/`). swing-it 사본도 같은지 볼 것.
- `storage.py` 는 swing-it 에서 복사한 독립 사본이다. 공유 라이브러리로 묶지 않는다.

## 화면·리포트 규칙
- 화면은 수급 사실만 말한다. 리포트에는 구간 수익률 4개뿐이라 경로를 모른다. 그래서 눌림목·반전 **판정**을 점수화하는
  제안은 하지 않는다(`reversal_flags` 의 ⚠ 는 판정 불가 라벨이다).
- 비율 열은 분모 게이트를 같이 설계한다. 분모가 작으면 숫자 대신 결측을 낸다. 문턱은 목록 안 몫으로 잡는다.
  이 결함이 다섯 번 나왔다(`flow_view.py` 최근집중 주석).
- 검정 결과(`docs/validation/`)를 화면이 따른다: G·종목 선정·곱·종합은 탐색 모드(`e`, 기본 꺼짐)에만 두고 첫 화면은 사실 요약이다.
  새 열·지표는 증거 표지를 같이 단다 — TUI `flow_view.EVIDENCE`·`_EV_SECTOR`·`_EV_NAMES`, 웹 `const EV`(검사가 대조한다).
- 섹터 지표(가속·임펄스·미실현·G·투신+연금5일 등)를 앞날 수익률의 신호로 소개하지 않는다 — 문서·도움말·커밋 메시지 모두.
  검정 결과 앞날 증거 없음 또는 판정 불가다. "유망"·"매수 후보"·"추천" 같은 말을 화면·README 에 넣지 않는다.
- 수식 정본은 `docs/formulas.md` 다(옛 PDF 판은 2026-11-08 지웠다 — git 이력에 있다). 코드를 옮기면 그 `파일:줄` 인용도 고친다
  — `tests/test_docs_refs.py` 가 줄 범위와 짝 지은 기호(±3줄)를 본다. 병합 뒤 빨개지면 `uv run python scripts/docs_refs.py --fix`.
- 수급 신뢰 순서: 기관 순매수만으로 충분하고 외국인 동반은 요구하지 않는다. 기관 안에서는 투신·연기금(`INST_DETAIL`)을 우선한다.
- 새 검사를 넣으면 `tests/mutations.toml` 에 변이도 넣는다(`verify_merge.py` 가 주입해 검사가 죽었는지 본다).
- `perf` 변경은 리포트가 바이트 동일함을 확인하고 메시지에 적는다.

## 커밋
- 한국어 conventional(`fix(flow): ...`). 신원은 레포 설정 그대로, 에이전트는 `Co-Authored-By` 로만.
