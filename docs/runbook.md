# 운영 런북 — 알림이 오면 할 일

일일 배치(`README.md` §3)가 보내는 알림과 증상별로 원인과 할 일을 적는다. 화면만 보는 사람은 읽을 필요가 없다.
아래의 `§` 는 README 의 절이다.

알림 제목은 `<작업> 실패 (rc=N)`·`<작업> 경고 (rc=75)`·`<작업> 시간 초과 (rc=124)` 이고 본문은 로그 꼬리 30줄이다.
작업은 `sector-flow-report`·`sector-flow-fresh`·`sector-flow-web` 이고 아래 표에서는 앞의 `sector-flow-` 를 뗐다. 명령은 저장소 루트
(`~/git/sector-flow`)에서 친다. 기록은 `reports/cron.log`, 웹은 `reports/serve.log`.

| 알림·증상 | 원인 | 할 일 |
|---|---|---|
| `report 경고 (rc=75)`, 본문 `수집 미완` · `[FAIL]` 줄 | 오늘 수집이 아직 안 끝났다. 폴더에 `INCOMPLETE.txt`, `latest` 는 전날. `[FAIL]` 줄이 모자란 것을 말한다(`조정 종가` 면 quant-airflow `daily_price_adjust` 가 아직 안 돌았다, `시장 목록이 비었다` 면 `stocks` 표가 비었다) | 기다린다 — 19:10·다음날 08:20 이 다시 만든다. 수집이 끝났으면 `scripts/daily_report.sh` |
| `report 실패 (rc=1)`, 본문 `오늘이 아닌데 수집 미완` | 지난 기준일이 아직 미완이다. 다음 거래일 수집이 시작되면 기준일이 넘어가 그 날은 영영 안 만들어진다 | quant-airflow 에서 수집을 메운 뒤 바로 `scripts/daily_report.sh` |
| `report 실패 (rc=1)`, 본문 `검증 실패` · `게시했지만 검증에 걸렸다` · 폴더에 `VERIFY_FAILED.txt` | 게시는 됐지만 자기 검증(§9)에 걸렸다. 화면·HTML 에 ⚠ | `grep FAIL reports/latest/VERIFY_FAILED.txt` 로 걸린 검사를 본다. 원인을 고친 뒤 `scripts/daily_report.sh`(검증 실패 폴더는 다시 만든다. 확정 리포트는 확정으로 남는다) |
| `report 실패 (rc=1)`, 본문 `DB 에서 기준일을 못 읽었다` · `fresh 실패 (rc=1)`, 본문 `DB 에서 오늘 수급을 못 셌다` · `report 시간 초과 (rc=124)` · 본문 `statement timeout` | DB 서버가 죽었거나 접속정보가 틀렸거나 락에 걸렸다 | quant-airflow 의 TimescaleDB 를 살핀다. 접속 확인: `set -a; . ~/git/quant-airflow/.env; set +a; uv run --extra pg python -c "import os; from sectorflow.storage import connect; connect(os.environ['KR_QUANT_DB']).close(); print('ok')"`. 복구 뒤 `scripts/daily_report.sh` |
| `report 실패 (rc=1)`, 본문 `기준일이 없다` | 접속은 됐는데 `supply_demand` 가 비었다 — `KR_QUANT_DB` 가 빈 사본이나 다른 DB 를 가리킨다 | `~/git/quant-airflow/.env`(이 레포에 `.env` 가 있으면 그쪽이 먼저다)의 `KR_QUANT_DB` 를 본다 |
| `report 실패 (rc=1)`, 본문 `DB 접속정보가 없다` | `.env` 도 `~/git/quant-airflow/.env` 도 `KR_QUANT_DB` 도 없다 — quant-airflow 체크아웃이 옮겨졌거나 지워졌다 | `ls ~/git/quant-airflow/.env`. 이 레포에 사본을 두지 않는다 — 정본을 되살린다 |
| `report 실패 (rc=127)`·`fresh 실패 (rc=127)`, 본문 `uv 를 찾지 못했다` | 크론 PATH 에 `uv` 가 없고 `~/.local/bin/uv` 도 없다 — uv 를 지웠거나 다른 곳에 깔았다 | `ls ~/.local/bin/uv`. 다시 깔거나 크론 줄에 `KR_QUANT_UV=<경로>` 를 준다 |
| `report 실패 (rc=N)`, 본문 `완전성 관문 오류(rc=N)` | 관문(`scripts/completeness_gate.py`)이 판정 전에 죽었다 — DB 가 끊겼거나 문장 시간 초과거나 코드 오류. 위 꼬리의 `Traceback` 마지막 줄이 원인이다 | `set -a; . ~/git/quant-airflow/.env; set +a; uv run --extra pg python scripts/completeness_gate.py --asof <기준일>` 로 재현한다. 고친 뒤 `scripts/daily_report.sh` |
| `실패 (rc=1)`, 본문에 `Traceback` | 파이썬 스크립트가 예외로 죽었다. 마지막 줄이 예외, 그 위 `File "…/scripts/…", line N` 이 자리다 | 같은 명령을 손으로 돌려 재현한다(DB 가 필요하면 위의 `.env` 불러오기부터). 고친 뒤 `scripts/daily_report.sh` |
| `<작업> 실패 (rc=N)`, 본문 `임시 파일을 못 만들었다` | 알림 꼬리나 DB 오류를 담을 임시 파일을 `reports/` 에도 `/tmp` 에도 못 만들었다 — 디스크가 찼거나(`/tmp` 는 사용자 할당량 tmpfs) 권한이 바뀌었다. 본문이 빠진 알림이라 출력은 크론 로그에 있다 | `df -h ~/git/sector-flow/reports /tmp`, `du -sh /tmp/* 2>/dev/null \| sort -h \| tail`, `ls -ld reports`. 비운 뒤 그 작업을 다시 |
| `report 경고 (rc=75)` 08:20·10:20, 본문 `확정치를 확인하지 못했다` · `[확정치] … 아직 안 돌았거나 도는 중` · `성공으로 끝나지 않았다` | `daily_finalize` 가 늦거나 실패했다. 잠정 리포트는 그대로 둔다. 08:20 이 놓친 날은 10:20 이 한 번 더 본다 | quant-airflow `daily_finalize` 를 본다. 끝났으면 `KR_QUANT_FINAL=1 scripts/daily_report.sh`(10:20 이 이미 돌았거나 기다릴 수 없을 때) |
| `report 경고 (rc=75)` 08:20·10:20, 본문 `확정치를 확인하지 못해 잠정치로 만들었다` | 그 기준일 리포트가 아직 없었다(전날 저녁 수집 미완) — 마지막 기회라 잠정치로라도 만들어 걸었다. 확정은 위와 같은 이유로 못 했다 | 위와 같다. 확정이 끝나면 `KR_QUANT_FINAL=1 scripts/daily_report.sh` 가 확정치로 다시 만든다 |
| `freshness 경고 (rc=75)` 20:30, 본문 `[외부 참값] 전 거래일(…) 확정 검증: …` | 확정 리포트의 정규장 종가 대조(자체 바구니 vs KRX 지수)가 3번 연속 건너뛰어졌다 — `regular_close` 가 아직 안 채워졌거나(다음 거래일 수급이 들어와야 채워진다) 그 날 비교 종목에 상장폐지 종목이 끼었다 | 그 날 `VERIFY.txt` 의 `SKIP 정규장 종가 대조` 줄에서 이유를 본다. 데이터 지연이면 다음 확정 실행에서 풀린다. 며칠 이어지면 quant-airflow 의 `regular_close` 뷰를 본다 |
| `report 경고 (rc=75)`, 본문 `[확정치] … 기록이 없다` · `로그 경로 형식이 바뀌었을 수 있다` · `태스크 이름이 바뀐 것이다` | 이쪽이 Airflow 로그를 못 읽는다 — 로그 경로나 `daily_finalize` 태스크 이름이 바뀌었다 | `uv run python scripts/finalize_done.py --asof <기준일>` 로 이유를 본다. `finalize_done.py` 의 경로 규칙·`TASKS` 를 quant-airflow `dags/daily_finalize.py` 에 맞춘다 |
| `report 경고 (rc=75)`, 본문 `[확정치] … 덮지 않는다` | 기준일 뒤의 `daily_finalize` 실행 창이 그날을 덮지 않는다 — 확정 실행을 놓친 지난 날이다 | 그날 확정치는 재수집 없이는 없다. 잠정으로 두거나 quant-airflow 에서 그 날짜를 재수집한 뒤 지난 기준일 재구성 |
| (재구성, 손 실행) `KR_QUANT_ASOF=<날> scripts/daily_report.sh` 가 3 으로 끝남, `거래일이 아니다 … 휴장일이다` | 그날 수급도 업종지수·일봉도 없다 — 휴장일이다. `scripts/rebuild_history.py` 요약에는 `휴장` 으로 남는다 | 할 일 없음 |
| (재구성) rc=1, `수급 수집 공백` | 그날 수급만 비고 업종지수·일봉은 있다 — 휴장이 아니라 수급 수집이 빠졌다. 요약에 `수집 공백` | quant-airflow 에서 그 날짜 수급을 재수집한 뒤 다시 재구성 |
| (재구성) rc=1, `지금 DB 에서도 수집 미완` | 그날 수급이 지금도 완전성 관문 기준에 못 미친다. 원본 폴더는 그대로 두고 요약에 `수집 미완` | 수집을 메운 뒤 `KR_QUANT_ASOF=<날> scripts/daily_report.sh` |
| (재구성) rc=75, `확정치를 확인하지 못해 잠정으로 재구성했다` | daily_finalize 창(3일) 안의 날인데 확정 실행이 아직 그날을 덮지 않았다 — 잠정으로 만들었다(`REBUILT.txt` 의 `finalize: pending`, FINAL.txt 없음) | 확정이 끝난 뒤 같은 명령을 다시 — 확정으로 바뀐다 |
| 웹 목록·칩에 `확정 기록 없음` · `REBUILT.txt` 에 `finalize: unverified` | 창 밖의 지난 날을 재구성했다 — 마감 값으로 만들었지만 그날을 덮은 확정 실행 기록이 없다. 경고가 아니라 사실 표시다(종료코드 0) | 할 일 없음. 확정치가 꼭 필요하면 quant-airflow 에서 그 날짜를 재수집한 뒤 다시 재구성 |
| (다이제스트, `KR_QUANT_DIGEST=1` 일 때만) 본문 `다이제스트를 못 만들었다` | `scripts/digest.py` 가 실패했거나 5분 상한(`KR_QUANT_DIGEST_TIMEOUT`)을 넘겼다 — 금칙어·길이 초과·카드 렌더 실패가 흔한 이유다. 리포트 게시는 그대로 됐다 | `UV_OFFLINE=1 uv run --with 'playwright==1.48.0' python scripts/digest.py --dir reports/latest --out /tmp/digest-재현` 으로 재현한다. stderr 의 `[digest] 만들지 않았다 — <이유>` 가 원인이다 |
| (재구성, 손 실행) `KR_QUANT_ASOF 가 날짜 꼴(YYYY-MM-DD)이 아니다` | 날짜를 잘못 줬다 | `KR_QUANT_ASOF=2026-10-06` 꼴로 다시 |
| `scripts/rebuild_history.py` 가 멈춤, `git 체크아웃이 아니다` · `origin/main 에 없다` · `커밋 안 한 변경` | 재구성은 origin/main 의 깨끗한 체크아웃에서만 돈다 — `REBUILT.txt` 의 `commit:` 을 나중에 다시 볼 수 있어야 한다 | `git -C ~/git/sector-flow pull --ff-only` 뒤 거기서 돌린다. 리허설은 `--allow-unmerged`(sha 뒤에 `-dirty`·`-unmerged` 가 붙는다) |
| `report 경고 (rc=75)`, 본문 `다른 리포트 실행이 돌고 있다` | 손으로 돌린 실행과 크론이 겹쳤다 | 앞 실행이 끝난 뒤 필요하면 같은 명령을 다시 |
| `fresh 실패 (rc=1)`, 본문 `오늘 리포트가 안 걸렸다` | 수급은 들어왔는데 리포트가 없다. 크론 줄이 빠졌거나 배포가 깨졌거나 관문이 막았다 | `grep "$(date +%F)" reports/cron.log`, `crontab -l \| grep sector-flow`. 고친 뒤 `scripts/daily_report.sh` |
| `fresh 경고 (rc=75)`, 본문 `supply_demand 가 비었다` | 장중 틱(`ticks`, scalp-it)은 있는데 수급이 없다 — 수집 실패. 수급·틱이 모두 없는 평일은 휴장으로 보고 알리지 않는다. 본문이 `휴장일이면 무시` 면 틱 표를 못 읽어 못 가른 것이다 | quant-airflow `daily_collection` 을 본다. 휴장일이면 무시한다 |
| `fresh 경고 (rc=75)`, 본문 `전 거래일(…)이 확정되지 않았다` | 그날 08:20·10:20 확정 모드가 안 돌았거나 `daily_finalize` 를 확인하지 못했다. 다음 거래일 수집이 들어와 확정 모드는 그 날을 다시 안 본다 | `uv run python scripts/finalize_done.py --asof <그날>` 로 이유를 본다. 확정이 끝났으면 지난 기준일 재구성으로 그 날을 다시 만든다(`KR_QUANT_ASOF=<그날> scripts/daily_report.sh`, 여러 날은 `scripts/rebuild_history.py`) |
| `fresh 경고 (rc=75)`, 본문 `ALERT_WEBHOOK_URL 이 비었다`(평일마다) · `alert.sh 가 없다` · 크론 로그에 `알림 통로 없음` | 알림 웹훅이 없거나 `~/git/quant-airflow/scripts/alert.sh` 가 없어 위의 모든 알림이 로그에만 남는다 | 웹훅을 만들어 `~/git/quant-airflow/.env` 의 `ALERT_WEBHOOK_URL=` 에 넣는다. `ls -l ~/git/quant-airflow/scripts/alert.sh` |
| 알림이 안 온다 · `alerts.log` 에 `[alert.py] failed` · `fresh 경고`, 본문 `웹훅이 폐기됐거나 틀렸다` · `웹훅이 HTTP N 로 답했다` · `200 으로 답했지만 JSON 이 아니다` · `초 안에 닿지 못했다` | 웹훅 전달 실패 — 채널에서 웹훅이 지워졌거나 주소가 틀렸거나(Discord 웹훅 주소가 아니다) 네트워크가 끊겼다. 알림 본문은 `~/logs/quant-airflow/alerts.log` 에 다 남아 있다 | `grep -A30 'failed' ~/logs/quant-airflow/alerts.log \| tail -n 60` 으로 놓친 알림을 읽는다. 웹훅을 새로 만들어 `ALERT_WEBHOOK_URL=` 을 바꾼다. `닿지 못했다` 가 하루뿐이면 네트워크가 잠깐 끊긴 것이다 |
| `fresh 경고 (rc=75)`, 본문 `[SLO] 전 거래일(…) 잠정 리포트가 기한` | 잠정 리포트가 18:30(KST)을 넘겨 게시됐다 — 18:10 실행이 늦었거나 수집이 늦게 끝나 19:10 재시도가 만들었다. 기한은 `docs/slo.md` | 한 번이면 할 일 없음(이미 게시는 됐다). 이어지면 `grep '생성 시작' reports/cron.log \| tail` 로 시작 시각을, quant-airflow `daily_collection` 의 끝나는 시각을 본다 |
| `fresh 경고 (rc=75)`, 본문 `[SLO] 전 거래일(…) 확정이 기한` | 확정 재생성이 다음날 10:40(KST)을 넘겼다 — 08:20·10:20 둘 다 제때 못 끝냈다(그 뒤 손 실행·재구성으로 확정됐다) | 그날 `reports/cron.log` 의 08:20·10:20 줄과 quant-airflow `daily_finalize` 의 끝나는 시각을 본다. 이어지면 daily_finalize 가 늦어지는 원인을 그쪽에서 고친다 |
| 크론 로그·알림 꼬리에 `[원장] 운영 사건을 기록하지 못했다` · `⚠️ [원장] … 기록하지 못했다` | 운영 사건 원장(`reports/events.jsonl`)에 못 썼다 — 디스크가 찼거나 권한이 바뀌었다. 리포트·점검은 그대로 계속됐다(원장은 리포트를 못 멈춘다) | `df -h ~/git/sector-flow/reports`, `ls -l reports/events.jsonl*`. 그 사이 사건은 비었고 복원하지 않는다 — 통계(`scripts/ops_ledger.py`)에 공백으로 남는다 |
| (외부 감시) 하트비트가 안 왔다는 알림 | 20:30 점검에서 리포트·수급·확정 중 하나가 깨끗하지 않았거나, 정해 둔 알림 웹훅이 죽었거나(그날 경고는 아무에게도 안 갔다), 점검이 아예 안 돌았다 — 머신·크론이 통째로 죽은 날도 여기서 잡힌다 | `tail -n 40 reports/cron.log` 의 `[하트비트]` 줄이 안 보낸 이유다. 켜는 법: 외부 감시(healthchecks.io 등, 평일 20:30 + 유예)를 만들어 `~/git/quant-airflow/.env` 에 `HEARTBEAT_URL=<주소>` 한 줄 — 주소만 넣으면 동작한다. 웹훅 없이 하트비트만 써도 된다 |
| `sector-flow-web 재시작 — /healthz 실패(pid N, 연속 n회)` | 떠 있는 서버가 5초 안에 대답하지 않거나 `/healthz` 가 503(리포트 폴더를 못 읽거나 목록이 안 그려진다)이라 `serve.sh` 가 끄고 다시 띄웠다. 본문이 이유다. 같은 일이 이어지면 5분마다 다시 띄우되 알림은 연속 첫 회와 12회(1시간)마다 | 본문의 이유를 본다. `curl -sS http://$(tailscale ip -4):8765/healthz`, `ls -ld reports`, `tail reports/serve.log` |
| `web 실패 (rc=137)` | `serve.sh` 가 TERM 을 5초 버틴 서버를 KILL 했다 — 그 서버를 띄웠던 래퍼의 알림이다. 위 재시작 알림과 같은 박자(연속 첫 회·12회마다)로만 온다 | 위 재시작 알림의 이유를 본다. 매번 KILL 까지 가면 서버가 TERM 에 끝나지 않는 것이다 — `tail reports/serve.log` |
| `web 실패 (rc=1)`, 본문 `누구인지(.serve.pid) 몰라 못 끈다` | 잠금(`reports/.serve.lock`)을 쥔 프로세스가 `/healthz` 에 대답하지 않는데, `.serve.pid` 가 없거나 그 pid 가 이 서버가 아니라 안전하게 못 끈다. 한 시간 이어질 때 알린다 | `cat reports/.serve.pid`, `fuser -v reports/.serve.lock` 로 잠금 주인을 찾아 끈다. 다음 5분 크론이 다시 띄운다 |
| `web 실패 (rc=1)`, 본문 `Tailscale 주소가 없다` · `에 못 묶었다` · 웹이 안 열린다 | Tailscale 이 없거나 포트가 막힌 채 한 시간이 지났다(`reports/.serve.misses` 가 센다). 서버가 죽었다(rc 가 서버 종료코드) | `tail reports/serve.log`, `tailscale ip -4`, `ss -ltnp \| grep 8765`. 손으로 띄우려면 `scripts/serve.sh`. 대답 없는 서버는 크론이 5분 안에 끄고 다시 띄운다 |
| `report` 로그·본문에 `[KRX 공식 대조] SKIP` | (선택 기능) KRX 공식 거래대금을 못 받았다 — 망·로그인 장벽(`LOGOUT`)·형식 변화·캐시 기준일 불일치. 리포트는 그대로 만들어졌다(실패 아님) | 꼬리의 이유를 본다. `LOGOUT` 이면 KRX 가 익명 조회를 막은 것 — 풀릴 때까지 SKIP 으로 둔다. 받아지면 `KR_QUANT_KRX_TRUTH=1 KR_QUANT_FORCE=1 scripts/daily_report.sh` 로 그 기준일을 다시 만든다 |
| `report` 로그·본문에 `[KRX 공식 대조] 근사 오차 큼` | 수량×종가 근사가 공식 거래대금과 문턱(5%)을 넘게 다르다 — 값의 버그가 아니라 근사의 한계 측정이다(VERIFY 의 재계산이 통과했으면 수치는 맞다) | 그날 금액 해석에 반영한다. 며칠 이어지면 `reports/<기준일>/krx_truth.json` 과 `payload.json` 의 `today.krx_truth` 로 어느 주체·시장인지 본다 |
| `report` 로그에 `[KRX 공식 대조] 내부 오류` | krx_truth 모듈 버그 — 대조만 빠지고 리포트는 그대로 만들어졌다 | `uv run python scripts/krx_truth.py --payload reports/latest/payload.json --fetch` 로 재현해 Traceback 을 본다 |
| `report` 로그에 `krx_truth.json 을 못 재웠다` | 공식값은 받아 대조했는데 캐시 파일을 못 썼다 — 디스크·권한 | `df -h ~/git/sector-flow/reports`, `ls -ld reports/<기준일>*`. 다음 빌드가 다시 받는다 |
| 고친 코드가 다음날에도 안 돈다 · `morning-sync` 알림에 `hold sector-flow` | 07:50 `pull-all --ci-gate` 가 CI 가 초록이 아닌 커밋을 받지 않았다 | GitHub Actions 를 초록으로 고친다. 확인했고 당장 필요하면 `git -C ~/git/sector-flow pull --ff-only` |

크론 로그(`reports/cron.log`·`serve.log`)는 5MB 를 넘으면 `scripts/cron_run.sh` 가 `.1` 로 하나만 남기고 비운다.

## 운영 사건 원장

**운영 사건 원장(`reports/events.jsonl`)** — 폴더 바꿔 끼우기가 INCOMPLETE·검증 실패 흔적과 mtime 을 지우고
크론 로그는 5MB 에서 돌아, "언제 게시됐나·몇 번 막혔나·게시 뒤 숫자가 바뀐 날" 을 나중에 셀 수 없었다. 그래서
실행·잡·웹 프로브·신선도 판정마다 JSON 한 줄을 **덧붙이기만** 한다(자르지 않는다 — 50MB 를 넘으면 `events.jsonl.2` 로
이어 쓴다). 형식·통계는 [`scripts/ops_ledger.py`](../scripts/ops_ledger.py)(`uv run python scripts/ops_ledger.py`,
`--days 90`·`--json`·`--errata`), 기한(잠정 18:30 · 확정 다음날 10:40 · 웹 5분 프로브 200)은 [`docs/slo.md`](slo.md) 가
정의하고 20:30 신선도 점검이 전 거래일을 그 기한에 대 본다. 원장 기록 실패는 리포트를 멈추지 않고, 원장은 웹
허용 목록에 없어 밖에 보이지 않는다.

## (선택) KRX 공표치 대조

`KR_QUANT_KRX_TRUTH=1` 로 만들면 KRX 정보데이터시스템(data.krx.co.kr)이 공표한 투자자별 **거래대금**(순매수)과 시장 1일
순매수 금액(수량×종가 근사)을 대조해 상대 오차를 `today.krx_truth` 에 적고 공표치를 `krx_truth.json` 으로 재운다
([`scripts/krx_truth.py`](../scripts/krx_truth.py)) — **내부 검증용으로만** 받아 기준일당 한 번 캐시하고, 비공개 리포트 밖으로
재배포하지 않는다(이용 범위는 KRX 질의에 포함 — `docs/strategy/inquiries/krx_market_data.md`).
실패(망·로그인 장벽)는 SKIP 이다 — 리포트는 그대로 만든다.
