# sector-flow 운영 프로세스 — 추적하며 찾은 문제

기준: `67fa2a5` (main). 크론 `deploy/crontab.simnode` → `scripts/cron_run.sh` → `scripts/daily_report.sh` 와 감시·웹·배포를 코드로 따라가며 확인한 것만 적었다. 레포 밖 파일(`~/git/quant-airflow/scripts/alert.sh`, `~/git/dotfiles/bin/pull-all`)은 읽기만 했다.
그림: `docs/architecture/ops-pipeline.html`

요약: MAJOR 2 · MINOR 6 (CRITICAL 없음)

---

## MAJOR

### 1. 검증에 실패한 재빌드가 이미 검증을 통과한 폴더를 지우고 latest 로 게시한다
- 근거
  - `scripts/daily_report.sh:327-340` — 검증에 실패해도 `VERIFY_FAILED.txt` 와 배너만 붙이고 계속 진행한다.
  - `scripts/daily_report.sh:407-418` — 재구성이 아닌 실행은 옛 폴더를 `mv "$DEST" "$OLD"` 한 뒤 `rm -rf "$OLD"` 로 지운다. 옛 폴더를 `.archive/` 로 옮기는 것은 재구성일 때뿐이다(`:411-414`).
  - `scripts/daily_report.sh:436-437` — 재구성이 아니면 `latest` 를 무조건 새 폴더로 옮긴다.
  - 같은 기준일을 다시 짓는 경로: 08:20·10:20 확정 모드(`:226-229`), 검증 실패 재시도(`:241`), `KR_QUANT_FORCE=1` 손 실행(`:241`, CLAUDE.md 가 "리포트 다시 만들기" 로 안내).
- 실패 시나리오: 18:10 잠정 리포트가 검증을 통과해 게시됐다. 다음날 08:20 확정 재빌드가 데이터 이상이나 새 코드 회귀로 검증에 걸린다. 이때 통과본은 `rm -rf` 로 사라지고 `latest` 는 실패본을 가리킨다. 10:20 은 `FINAL.txt`+`VERIFY_FAILED.txt` 를 보고 같은 입력으로 다시 지어 같은 실패를 낸다. 되돌릴 길이 없다. 코드를 바꾼 뒤 `KR_QUANT_FORCE=1` 로 다시 만들 때도 같다.
- 고칠 방법: 옛 폴더에 `VERIFY_FAILED.txt` 가 없고 새 빌드는 검증에 실패하면 바꿔 끼우지 않는다. 새 빌드는 `<기준일>.rejected.<ts>` 로 남기고 rc 1 로 알린다. 아니면 재구성과 같이 옛 폴더를 언제나 `.archive/` 로 옮기고 되돌리는 명령을 런북에 적는다. 이 경로를 `tests/test_ops_ledger.py` 계열 검사와 `tests/mutations.toml` 변이로 고정한다.

### 2. 저녁 재시도는 19:10 한 번뿐이고, 관문을 통과한 뒤에는 다시 보지 않는다
- 근거
  - `deploy/crontab.simnode:7,28` — 수집이 끝나는 시각은 고정이 아니다(18:47 까지 간 날이 있다). 저녁 크론은 18:10·19:10 둘뿐이다.
  - `scripts/daily_report.sh:241-251` — `numbers.html` 이 있고 `VERIFY_FAILED` 가 없으면 0 으로 건너뛴다.
  - `scripts/completeness_gate.py:21-24,51` — 관문은 시장별 97% 에서 통과한다. 통과는 "수집이 끝났다" 가 아니라 "97% 이상 들어왔다" 이다.
  - `scripts/freshness_check.py:99-108,119-124` — 20:30 감시는 `latest == 오늘` 인지와 `VERIFY_FAILED` 만 본다. 부분 리포트도 OK 다. 오늘 리포트가 없으면 rc 1 로 알리고 손 실행을 안내할 뿐 다시 만들지 않는다.
- 실패 시나리오
  - (a) 수집이 19:15 에 끝난다. 18:10·19:10 은 관문에서 75 로 끝나고, 20:30 은 rc 1 로 알린다. 사람이 손으로 돌리지 않으면 그날 저녁 리포트는 없고 다음날 08:20 확정본이 첫 게시가 된다. SLO 18:30(`ops_ledger`)을 넘긴다.
  - (b) 18:10 에 수집이 98% 인 채 관문을 통과해 게시된다. 19:10 은 "이미 있다" 며 건너뛴다. 그날 저녁 내내 최대 3%(거래소 약 24·코스닥 약 54종목)가 빠진 리포트가 latest 로 남고, 20:30 감시는 OK 다. 작은 섹터에서는 이 빠짐이 흐름 숫자를 크게 바꿀 수 있다.
- 고칠 방법: 저녁 재시도를 19:40·20:10 에 더 둔다. 리포트가 있는 날은 `max(date)` 한 번이라 비용이 거의 없다. 관문 판정 수치(시장별 종목 수)를 폴더에 남긴다. 다음 실행은 DB 의 지금 종목 수가 그때보다 늘었으면 건너뛰지 말고 다시 짓는다. 또는 "관문 통과 시점의 커버리지 < 100%" 를 표지 파일로 남겨 재시도 대상으로 삼는다.

---

## MINOR

### 3. 바꿔 끼우기와 latest 갱신이 원자적이지 않고, 건너뜀 경로는 latest 를 고치지 않는다
- 근거: `scripts/daily_report.sh:298` 이 `BUILD` 를 정한 뒤 다시 비우지 않는다. EXIT 트랩은 `$BUILD` 를 `rm -rf` 한다(`:47-50`). 바꿔 끼우기는 `mv` 두 번으로 한다(`:407-410`). `ln -sfn` 은 그 뒤 `sha256sum` 다음에 온다(`:420-437`). 건너뜀(`:241-251`)은 `latest` 를 보지 않는다.
- 실패 시나리오
  - 크론의 `timeout 40m` TERM 이 `:409` 와 `:410` 사이에 오면 트랩이 다 지은 `$BUILD` 를 지운다. `<기준일>` 폴더는 사라지고 `<기준일>.replaced.<pid>` 만 남는다. `latest` 는 끊긴다. `/healthz` 는 503 을 내고 `serve.sh` 는 5분마다 서버를 다시 띄운다.
  - TERM 이 `:420` 과 `:437` 사이에 오면 새 날 폴더는 게시됐는데 `latest` 는 어제에 남는다. 19:10 은 `numbers.html` 을 보고 0 으로 건너뛴다. 20:30 감시가 rc 1 로 알릴 때까지, 손으로 고치지 않으면 다음날 08:20 까지 그대로다.
- 고칠 방법: `mv "$BUILD" "$DEST"` 직후 `BUILD=""` 로 비운다. 두 `mv` 사이에서는 TERM 트랩을 미룬다. 건너뜀 경로에서 `latest` 가 `$DEST` 보다 이전 날을 가리키면 링크만 고친다(재구성이 아닐 때). 바꿔 끼우기는 `renameat2(RENAME_EXCHANGE)` 방식(`mv --exchange`, coreutils ≥ 9.5)으로 하나로 만들 수 있다.

### 4. 남겨진 `.building.<pid>`·`.replaced.<pid>` 폴더를 치우는 단계가 없다
- 근거: `scripts/daily_report.sh:56-58` 주석이 KILL 로 끝나면 `.building.N` 이 남는다고 적었다(2026-11-09 재현). `:299` 는 자기 pid 의 경로만 지운다. `.asof_err.*`(`:97`)·`.cron_run.*`(`cron_run.sh:46-53`)에는 하루 지난 것을 쓸어내는 단계가 있지만, 이 두 폴더에는 없다. `docs/runbook.md:88` 은 백업에서 이 둘을 제외만 한다.
- 실패 시나리오: OOM·`timeout -k 2m` 의 KILL·3번의 TERM 창마다 리포트 한 벌 크기의 폴더가 `reports/` 에 쌓인다. 디스크가 차면 `mktemp`(`:98`)와 원장 쓰기가 실패한다.
- 고칠 방법: 잠금을 잡은 뒤 `find "$OUT_ROOT" -maxdepth 1 -name '*.building.*' -o -name '*.replaced.*'` 중 pid 가 살아 있지 않고 하루 지난 것을 지운다(`cron_run.sh:46-53` 과 같은 규칙). 변이 검사를 함께 넣는다.

### 5. 부속 산출물(다이제스트·검증 페이지) 실패는 75 로 알리기만 하고 다시 만들지 않는다
- 근거: `scripts/daily_report.sh:352-359,399-406` 에서 실패하면 `SIDE_FAIL` 이 되고 `:452-455` 에서 rc 75 로 끝난다. 다음 크론의 건너뜀 조건(`:219-225` 이미 확정, `:241-251` 이미 있음)은 `digest.png`·`trust.html` 이 없는지 보지 않는다.
- 실패 시나리오: 18:10 에 Playwright 캐시 문제로 `digest.png` 를 못 찍었다(`UV_OFFLINE=1`). 19:10 은 건너뛴다. 08:20 확정본에서 또 실패하면 10:20 도 "이미 확정" 이라 건너뛴다. 그날 `/latest/digest.png`·`trust.html` 은 404 이고, 경고 한 번 뒤로는 아무도 다시 만들지 않는다.
- 고칠 방법: `SIDE_FAIL` 일 때 폴더에 `SIDE_FAILED.txt` 를 남긴다. 건너뜀 조건이 그 파일을 보고 부속만 다시 만드는 가벼운 경로(빌드 없이 digest·trust 만)를 탄다.

### 6. `.serve.misses` 는 "연속" 횟수가 아니어서 거짓 웹 실패 알림이 난다
- 근거: `scripts/serve.sh:14-18` 은 Tailscale IP 를 못 얻으면 잠금이나 건강 검사보다 먼저 `--soft-fail` 을 센다. `:53-56`(pid 모름)도 센다. 건강할 때(`:43-45`)는 `.serve.restarts` 만 지운다. `.serve.misses` 는 서버가 새로 바인드할 때만 지워진다(`scripts/serve_reports.py:470-474`). 12의 배수일 때 rc 1 이다(`:269-284`). `docs/runbook.md:55` 는 이 알림을 "한 시간이 지났다" 로 설명한다.
- 실패 시나리오: 서버는 며칠째 잘 떠 있다. 그 사이 `tailscale ip -4` 가 가끔 순간 실패한다(tailscaled 재시작 등). 실패할 때마다 미스가 하나씩 쌓이고, 12번째에 `sector-flow-web 실패 (rc=1)` error 알림이 나간다. 실제로는 끊긴 적이 없거나 5분 남짓 끊겼을 뿐이다. 운영자는 런북대로 "한 시간 지속" 으로 읽는다.
- 고칠 방법: `/healthz` 200 분기(`serve.sh:43-45`)에서 `.serve.misses` 도 지운다. 아니면 IP 조회 실패라도 잠금이 잡혀 있고 healthz 가 200 이면 soft-fail 을 세지 않는다.

### 7. 알림 통로가 아예 없거나(`alert.sh` 없음) 웹훅이 비어도 하트비트는 녹색이다
- 근거: `scripts/freshness_check.py:516-521` 은 `alert.sh` 가 실행 가능하지 않으면 `webhook=""` 로 둔다. `:526` 의 `dead_hook = bool(webhook) and wrc != 0` 이 그때 False 가 되어, 데이터가 깨끗한 날 하트비트를 보낸다(`:532-533`). `cron_run.sh:88-97` 은 그 경우 "알림 통로 없음" 을 로그에만 적는다. 크론 주석(`deploy/crontab.simnode:34-36`)은 "웹훅이 있는데 죽었으면" 만 막는다.
- 실패 시나리오: quant-airflow 체크아웃이 옮겨졌거나 `.env` 의 `ALERT_WEBHOOK_URL=` 줄이 실수로 지워졌다. 모든 75·1 알림이 로그에만 남는데 외부 감시는 매일 하트비트를 받아 정상으로 본다. 알림 통로가 끊긴 것을 아는 신호는 그 rc 75 하나인데, 그 rc 는 끊긴 통로로 가니 아무도 못 본다.
- 고칠 방법: `alert.sh` 가 없으면 `dead_hook=True` 와 같이 다룬다. "하트비트 전용" 은 `SECTOR_FLOW_HEARTBEAT_ONLY=1` 같은 명시 설정으로만 허용하고, 그때만 빈 웹훅을 정상으로 본다.

### 8. 손으로 하는 즉시 배포(`git pull --ff-only`)가 실행 중인 배치와 맞물리지 않는다
- 근거: 크론 주석은 "07:50 배포 뒤라 도중에 코드가 바뀌지 않는다" 를 전제로 둔다(`deploy/crontab.simnode:12-13`). 그런데 `CLAUDE.md:26` 과 `docs/runbook.md:61-62` 는 당장 필요하면 `git -C ~/git/sector-flow pull --ff-only` 를 하라고 안내한다. 배치는 단계마다 새 파이썬 프로세스로 코드를 읽는다(`scripts/daily_report.sh:321-328,354,399`). `.report.lock` 은 `daily_report.sh` 끼리만 막는다(`:80-86`).
- 실패 시나리오: 18:12 에 손으로 pull 한다. 같은 실행 안에서 `sector_flow.py` 는 옛 코드로 payload 를 만들고 `verify_report.py`·`trust_page.py` 는 새 코드로 돈다. 형식이 바뀐 커밋이면 검증 실패로 게시되거나(1번과 겹치면 통과본이 사라진다), 다른 버전끼리 만든 폴더가 잘못 통과한다. 원장 `commit`(`:52`)은 끝난 시점의 HEAD 라 이 섞임이 드러나지 않는다.
- 고칠 방법: 런북의 즉시 배포 명령을 `flock reports/.report.lock git -C … pull --ff-only` 로 바꾼다. 또는 `daily_report.sh` 가 시작할 때 `git rev-parse HEAD` 를 고정해 두고 끝에서 비교한다. 다르면 rc 75 로 알리고 원장에 시작 sha 를 싣는다.

---

### 살펴봤지만 문제로 올리지 않은 것
- 08:20 확정이 75 로 끝난 날의 사후 경로: 10:20 재시도, 20:30 `check_prev_final` 이 75 로 알리고, 그 뒤 날은 재구성으로 다시 만든다. 손이 필요하지만 알림 없이 묻히지는 않는다.
- 크론끼리의 겹침: 40분 상한 안에서 18:10·19:10·08:20·10:20 은 겹치지 않는다. 겹치면 `flock -n` 이 75 로 알린다.
- `_bounded` 의 TERM 전달과 바깥 `timeout` 의 124: 2026-11-09 수정 이후 트랩이 안쪽 그룹까지 끊는다.
- 137 알림 솎기와 `serve.sh` 재시작 알림의 이중 발송(첫 회): 주석(`serve.sh:63-64`)이 일부러 둘 다 보낸다고 적었다.
