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
| `report 경고 (rc=75)` 08:20·10:20, 본문 `[확정치] … 수집 실패가 문턱을 넘었거나 완료 줄이 없다` | `daily_finalize` 의 종목 실패가 1% 를 넘었거나(평소 최대 0.49%, 수집기 종료코드는 20% 까지 0 이다) 기록의 완료 줄 모양이 바뀌었다. 잠정 리포트는 그대로 둔다 | 줄 끝 경로의 기록을 본다. 수집 장애면 quant-airflow 에서 다시 받은 뒤 `KR_QUANT_FINAL=1 scripts/daily_report.sh`. 모양이 바뀌었으면 `finalize_done._DONE_LINE` 을 맞춘다 |
| `report 경고 (rc=75)` 08:20·10:20, 본문 `확정치를 확인하지 못해 잠정치로 만들었다` | 그 기준일 리포트가 아직 없었다(전날 저녁 수집 미완) — 마지막 기회라 잠정치로라도 만들어 걸었다. 확정은 위와 같은 이유로 못 했다 | 위와 같다. 확정이 끝나면 `KR_QUANT_FINAL=1 scripts/daily_report.sh` 가 확정치로 다시 만든다 |
| `freshness 경고 (rc=75)` 20:30, 본문 `[외부 참값] 전 거래일(…) 확정 검증: …` | 확정 리포트의 정규장 종가 대조(자체 바구니 vs KRX 지수)가 3번 연속 건너뛰어졌다 — `regular_close` 가 아직 안 채워졌거나(다음 거래일 수급이 들어와야 채워진다) 그 날 비교 종목에 상장폐지 종목이 끼었다 | 그 날 `VERIFY.txt` 의 `SKIP 정규장 종가 대조` 줄에서 이유를 본다. 데이터 지연이면 다음 확정 실행에서 풀린다. 며칠 이어지면 quant-airflow 의 `regular_close` 뷰를 본다 |
| `report 경고 (rc=75)`, 본문 `[확정치] … 기록이 없다` · `로그 경로 형식이 바뀌었을 수 있다` · `태스크 이름이 바뀐 것이다` | 이쪽이 Airflow 로그를 못 읽는다 — 로그 경로나 `daily_finalize` 태스크 이름이 바뀌었다 | `uv run python scripts/finalize_done.py --asof <기준일>` 로 이유를 본다. `finalize_done.py` 의 경로 규칙·`TASKS` 를 quant-airflow `dags/daily_finalize.py` 에 맞춘다 |
| `report 경고 (rc=75)`, 본문 `[확정치] … 덮지 않는다` | 기준일 뒤의 `daily_finalize` 실행 창이 그날을 덮지 않는다 — 확정 실행을 놓친 지난 날이다 | 그날 확정치는 재수집 없이는 없다. 잠정으로 두거나 quant-airflow 에서 그 날짜를 재수집한 뒤 지난 기준일 재구성 |
| (재구성, 손 실행) `KR_QUANT_ASOF=<날> scripts/daily_report.sh` 가 3 으로 끝남, `거래일이 아니다 … 휴장일이다` | 그날 수급도 업종지수·일봉도 없다 — 휴장일이다. `scripts/rebuild_history.py` 요약에는 `휴장` 으로 남는다 | 할 일 없음 |
| (재구성) rc=1, `수급 수집 공백` | 그날 수급만 비고 업종지수·일봉은 있다 — 휴장이 아니라 수급 수집이 빠졌다. 요약에 `수집 공백` | quant-airflow 에서 그 날짜 수급을 재수집한 뒤 다시 재구성 |
| (재구성) rc=1, `지금 DB 에서도 수집 미완` | 그날 수급이 지금도 완전성 관문 기준에 못 미친다. 원본 폴더는 그대로 두고 요약에 `수집 미완` | 수집을 메운 뒤 `KR_QUANT_ASOF=<날> scripts/daily_report.sh` |
| (재구성) rc=75, `확정치를 확인하지 못해 잠정으로 재구성했다` | daily_finalize 창(3일) 안의 날인데 확정 실행이 아직 그날을 덮지 않았다 — 잠정으로 만들었다(`REBUILT.txt` 의 `finalize: pending`, FINAL.txt 없음) | 확정이 끝난 뒤 같은 명령을 다시 — 확정으로 바뀐다 |
| 웹 목록·칩에 `확정 기록 없음` · `REBUILT.txt` 에 `finalize: unverified` | 창 밖의 지난 날을 재구성했다 — 마감 값으로 만들었지만 그날을 덮은 확정 실행 기록이 없다. 경고가 아니라 사실 표시다(종료코드 0) | 할 일 없음. 확정치가 꼭 필요하면 quant-airflow 에서 그 날짜를 재수집한 뒤 다시 재구성 |
| `report 경고 (rc=75)`, 본문 `부속 산출물을 못 만들었다` · `검증·품질 페이지를 못 만들었다` · `다이제스트 카드(digest.png)를 못 찍었다` | 리포트는 게시됐고 숫자와 무관하다. 웹의 `/latest/trust.html` 또는 `/latest/digest.*` 만 그 판에 없다. 같은 폴더 `VERIFY.txt` 끝줄에 무엇이 빠졌는지 적힌다 | 페이지는 `uv run python scripts/trust_page.py --root reports --out /tmp/trust-재현/trust.html` 로 재현한다(금칙어면 2 로 끝나며 그 말을 적는다). 고친 뒤 `KR_QUANT_FORCE=1 scripts/daily_report.sh` — 게시된 폴더의 파일 하나만 고쳐 쓰는 길은 없다 |
| (다이제스트, 끄려면 `KR_QUANT_DIGEST=0`) 본문 `다이제스트를 못 만들었다` | `scripts/digest.py` 가 실패했거나 5분 상한(`KR_QUANT_DIGEST_TIMEOUT`)을 넘겼다 — 금칙어·길이 초과·카드 렌더 실패가 흔한 이유다. 리포트 게시는 그대로 됐다 | `UV_OFFLINE=1 uv run --with 'playwright==1.48.0' python scripts/digest.py --dir reports/latest --out /tmp/digest-재현` 으로 재현한다. stderr 의 `[digest] 만들지 않았다 — <이유>` 가 원인이다 |
| (재구성, 손 실행) `KR_QUANT_ASOF 가 날짜 꼴(YYYY-MM-DD)이 아니다` | 날짜를 잘못 줬다 | `KR_QUANT_ASOF=2026-10-06` 꼴로 다시 |
| `scripts/rebuild_history.py` 가 멈춤, `git 체크아웃이 아니다` · `origin/main 에 없다` · `커밋 안 한 변경` | 재구성은 origin/main 의 깨끗한 체크아웃에서만 돈다 — `REBUILT.txt` 의 `commit:` 을 나중에 다시 볼 수 있어야 한다 | `git -C ~/git/sector-flow pull --ff-only` 뒤 거기서 돌린다. 리허설은 `--allow-unmerged`(sha 뒤에 `-dirty`·`-unmerged` 가 붙는다) |
| `report 경고 (rc=75)`, 본문 `다른 리포트 실행이 돌고 있다` | 손으로 돌린 실행과 크론이 겹쳤다 | 앞 실행이 끝난 뒤 필요하면 같은 명령을 다시 |
| `fresh 실패 (rc=1)`, 본문 `오늘 리포트가 안 걸렸다` | 수급은 들어왔는데 리포트가 없다. 크론 줄이 빠졌거나 배포가 깨졌거나 관문이 막았다 | `grep "$(date +%F)" reports/cron.log`, `crontab -l \| grep sector-flow`. 고친 뒤 `scripts/daily_report.sh` |
| `fresh 실패 (rc=1)`, 본문 `달력상 거래일인데 supply_demand 가 비었다` | KRX 달력(`scripts/krx_calendar.py`)에 없는 평일에 수급이 없다 — 수집 실패다. 틱 유무는 본문에 사실로만 적는다(수집과 scalp-it 이 같이 죽으면 틱도 없다) | quant-airflow `daily_collection` 을 본다. 정말 임시 휴장이었으면 `scripts/krx_calendar.py` 에 그날을 넣는다 |
| `fresh 경고 (rc=75)`, 본문 `supply_demand 가 비었다 … 휴장일이면 무시` · `[달력] <해>년이 scripts/krx_calendar.py 에 없다` | 달력이 그 해를 다루지 않아 휴장과 수집 실패를 못 가른다. 12월이면 다음 해가 없을 때 미리 알린다 | KRX 휴장일 공지(보통 전해 12월)로 표를 넣고 `PROVISIONAL` 에서 뺀다 |
| `fresh 경고 (rc=75)`, 본문 `[달력] 오늘(…)은 달력에 휴장` | 달력이 휴장이라는데 수급이 들어왔다 — 달력이 틀렸다. SLO 분모에서 이 날이 빠진다 | KRX 공지와 대조해 `scripts/krx_calendar.py` 를 고친다 |
| `fresh 경고 (rc=75)`, 본문 `자기 검증에 걸린 채 latest` · `확정 리포트가 자기 검증에 걸렸다` | 검증에 걸린 리포트가 게시돼 있다(`VERIFY_FAILED.txt`). 게시는 됐지만 그날 하트비트는 안 나간다 | 그 파일을 본다. 원인을 고친 뒤 `KR_QUANT_FORCE=1 scripts/daily_report.sh`(전 거래일은 `KR_QUANT_ASOF=<그날> scripts/daily_report.sh`) |
| `fresh 경고 (rc=75)`, 본문 `전 거래일(…)이 확정되지 않았다` | 그날 08:20·10:20 확정 모드가 안 돌았거나 `daily_finalize` 를 확인하지 못했다. 다음 거래일 수집이 들어와 확정 모드는 그 날을 다시 안 본다 | `uv run python scripts/finalize_done.py --asof <그날>` 로 이유를 본다. 확정이 끝났으면 지난 기준일 재구성으로 그 날을 다시 만든다(`KR_QUANT_ASOF=<그날> scripts/daily_report.sh`, 여러 날은 `scripts/rebuild_history.py`) |
| `fresh 경고 (rc=75)`, 본문 `ALERT_WEBHOOK_URL 이 비었다`(평일마다) · `alert.sh 가 없다` · 크론 로그에 `알림 통로 없음` | 알림 웹훅이 없거나 `~/git/quant-airflow/scripts/alert.sh` 가 없어 위의 모든 알림이 로그에만 남는다 | 웹훅을 만들어 `~/git/quant-airflow/.env` 의 `ALERT_WEBHOOK_URL=` 에 넣는다. `ls -l ~/git/quant-airflow/scripts/alert.sh` |
| 알림이 안 온다 · `alerts.log` 에 `[alert.py] failed` · `fresh 경고`, 본문 `웹훅이 폐기됐거나 틀렸다` · `웹훅이 HTTP N 로 답했다` · `200 으로 답했지만 JSON 이 아니다` · `초 안에 닿지 못했다` | 웹훅 전달 실패 — 채널에서 웹훅이 지워졌거나 주소가 틀렸거나(Discord 웹훅 주소가 아니다) 네트워크가 끊겼다. 알림 본문은 `~/logs/quant-airflow/alerts.log` 에 다 남아 있다 | `grep -A30 'failed' ~/logs/quant-airflow/alerts.log \| tail -n 60` 으로 놓친 알림을 읽는다. 웹훅을 새로 만들어 `ALERT_WEBHOOK_URL=` 을 바꾼다. `닿지 못했다` 가 하루뿐이면 네트워크가 잠깐 끊긴 것이다 |
| `fresh 경고 (rc=75)`, 본문 `[SLO] 전 거래일(…)이 미게시다` | 그 거래일 리포트가 끝내 게시되지 않았다. 괄호가 원장의 사건이다(`incomplete×2` 관문 차단, `기준일 못 읽음×2` DB 끊김) | 그날 `reports/cron.log` 를 본다. 수집을 메운 뒤 `KR_QUANT_ASOF=<그날> scripts/daily_report.sh` |
| `fresh 경고 (rc=75)`, 본문 `[SLO] 전 거래일(…) 잠정 리포트가 기한` | 그날 첫 게시(모드 무관)가 18:30(KST)을 넘겼다 — 18:10 실행이 늦었거나 수집이 늦게 끝나 19:10 재시도가 만들었거나, 관문에 막혀 다음날 08:20 이 처음 게시했다. 기한은 `docs/slo.md` | 한 번이면 할 일 없음(이미 게시는 됐다). 이어지면 `grep '생성 시작' reports/cron.log \| tail` 로 시작 시각을, quant-airflow `daily_collection` 의 끝나는 시각을 본다 |
| `fresh 경고 (rc=75)`, 본문 `[SLO] 전 거래일(…) 확정이 기한` | 확정 재생성이 다음날 10:40(KST)을 넘겼다 — 08:20·10:20 둘 다 제때 못 끝냈다(그 뒤 손 실행·재구성으로 확정됐다) | 그날 `reports/cron.log` 의 08:20·10:20 줄과 quant-airflow `daily_finalize` 의 끝나는 시각을 본다. 이어지면 daily_finalize 가 늦어지는 원인을 그쪽에서 고친다 |
| 크론 로그·알림 꼬리에 `[원장] 운영 사건을 기록하지 못했다` · `⚠️ [원장] … 기록하지 못했다` · `fresh 경고 (rc=75)`, 본문 `실행 기록이 원장에 없다` · `18:10 뒤 실행 기록이 원장에 없다` · `이 점검의 판정을 원장에 못 썼다` | 운영 사건 원장(`reports/events.jsonl`)에 못 썼거나 크론이 안 돌았다 — 디스크가 찼거나 권한이 바뀌었다. 리포트·점검은 그대로 계속됐다(원장은 리포트를 못 멈춘다). 원장 공백이 있는 날은 하트비트도 안 나간다 | `df -h ~/git/sector-flow/reports`, `ls -l reports/events.jsonl*`, `grep "$(date +%F)" reports/cron.log`, `crontab -l \| grep sector-flow`. 그 사이 사건은 비었고 복원하지 않는다 — 통계(`scripts/ops_ledger.py`)에 미게시·공백으로 남는다 |
| `fresh 경고 (rc=75)`, 본문 `[웹] /healthz 프로브가 N분째 없다` | 5분 프로브가 한 시간 넘게 끊겼다 — 서버가 못 뜨거나 웹 크론이 안 돈다. 내려가 있던 주기는 줄 자체가 없어 응답률에 안 보인다 | `tail reports/serve.log`, `crontab -l \| grep serve.sh`, `tailscale ip -4`. `scripts/ops_ledger.py` 의 `공백` 칸이 그동안의 빈 칸 수다 |
| (외부 감시) 하트비트가 안 왔다는 알림 | 20:30 점검에서 리포트·수급·확정 중 하나가 깨끗하지 않았거나, 정해 둔 알림 웹훅이 죽었거나(그날 경고는 아무에게도 안 갔다), 점검이 아예 안 돌았다 — 머신·크론이 통째로 죽은 날도 여기서 잡힌다 | `tail -n 40 reports/cron.log` 의 `[하트비트]` 줄이 안 보낸 이유다. 켜는 법: 외부 감시(healthchecks.io 등, 평일 20:30 + 유예)를 만들어 `~/git/quant-airflow/.env` 에 `HEARTBEAT_URL=<주소>` 한 줄 — 주소만 넣으면 동작한다. 웹훅 없이 하트비트만 써도 된다 |
| `sector-flow-web 재시작 — /healthz 실패(pid N, 연속 n회)` | 떠 있는 서버가 5초 안에 대답하지 않거나 `/healthz` 가 503(리포트 폴더를 못 읽거나 목록이 안 그려지거나 `latest` 가 끊겼거나 그 안에 `numbers.html`·`viewer.html` 이 없다)이라 `serve.sh` 가 끄고 다시 띄웠다. 본문이 이유다. 같은 일이 이어지면 5분마다 다시 띄우되 알림은 연속 첫 회와 12회(1시간)마다 | 본문의 이유를 본다. `curl -sS http://$(tailscale ip -4):8765/healthz`, `ls -ld reports`, `tail reports/serve.log` |
| `web 실패 (rc=137)` | `serve.sh` 가 TERM 을 5초 버틴 서버를 KILL 했다 — 그 서버를 띄웠던 래퍼의 알림이다. 위 재시작 알림과 같은 박자(연속 첫 회·12회마다)로만 온다 | 위 재시작 알림의 이유를 본다. 매번 KILL 까지 가면 서버가 TERM 에 끝나지 않는 것이다 — `tail reports/serve.log` |
| `web 실패 (rc=1)`, 본문 `누구인지(.serve.pid) 몰라 못 끈다` | 잠금(`reports/.serve.lock`)을 쥔 프로세스가 `/healthz` 에 대답하지 않는데, `.serve.pid` 가 없거나 그 pid 가 이 서버가 아니라 안전하게 못 끈다. 한 시간 이어질 때 알린다 | `cat reports/.serve.pid`, `fuser -v reports/.serve.lock` 로 잠금 주인을 찾아 끈다. 다음 5분 크론이 다시 띄운다 |
| `web 실패 (rc=1)`, 본문 `Tailscale 주소가 없다` · `에 못 묶었다` · 웹이 안 열린다 | Tailscale 이 없거나 포트가 막힌 채 한 시간이 지났다(`reports/.serve.misses` 가 센다). 서버가 죽었다(rc 가 서버 종료코드) | `tail reports/serve.log`, `tailscale ip -4`, `ss -ltnp \| grep 8765`. 손으로 띄우려면 `scripts/serve.sh`. 대답 없는 서버는 크론이 5분 안에 끄고 다시 띄운다 |
| `report` 로그·본문에 `[네이버 대조] SKIP` | 네이버 시장값을 못 받았다 — 망·공표 전·휴장·형식 변화·캐시 기준일 불일치. 리포트는 그대로 만들어졌다(실패 아님) | 꼬리의 이유를 본다. 받아지면 `KR_QUANT_KRX_TRUTH=1 KR_QUANT_FORCE=1 scripts/daily_report.sh` 로 그 기준일을 다시 만든다 |
| `freshness 경고 (rc=75)` 20:30, 본문 `[네이버 대조] 전 거래일(…) 검증: …` | 네이버 대조가 3번 연속 건너뛰어졌다 — 그동안 화면 칩이 네이버 시장값 기준으로 판정하지 못했다 | 그 날 `VERIFY.txt` 의 `SKIP 네이버 대조` 줄에서 이유를 본다. 네이버 응답 형식이 바뀌었으면 `scripts/krx_truth.py` 의 `_parse_naver` 를 고친다 |
| `report` 로그·본문에 `[네이버 대조] 차이 큼` | 시장 합계가 네이버 시장값과 문턱(5%)을 넘게 다르다 — 값의 버그가 아니라 정의 차이(우선주 등 미수집·분봉 밖 대량 체결)와 환산 근사의 측정이다. 거래소는 평소에도 절반 넘는 날 뜬다(48일 중 28일) | 그날 금액 해석에 반영한다. 큰 날은 `today.block_suspects` 로 어느 종목인지 본다. 네이버 수치는 `reports/<기준일>/krx_truth.json` 에만 있다 |
| `report` 로그에 `[네이버 대조] 내부 오류` | krx_truth 모듈 버그 — 대조만 빠지고 리포트는 그대로 만들어졌다 | `uv run python scripts/krx_truth.py --payload reports/latest/payload.json --fetch --out "$(mktemp -d -p ~/.cache)"` 로 재현해 Traceback 을 본다 |
| `report` 로그에 `krx_truth.json 을 못 재웠다` | 공식값은 받아 대조했는데 캐시 파일을 못 썼다 — 디스크·권한 | `df -h ~/git/sector-flow/reports`, `ls -ld reports/<기준일>*`. 다음 빌드가 다시 받는다 |
| 고친 코드가 다음날에도 안 돈다 · `morning-sync` 알림에 `hold sector-flow` · `fresh 경고 (rc=75)`, 본문 `[배포] 운영 체크아웃이 origin/main 보다 커밋 N개 뒤처졌다` | 07:50 `pull-all --ci-gate` 가 CI 가 초록이 아닌 커밋을 받지 않았다. 마지막 07:50 전에 올라온 커밋만 경고한다(오늘 낮의 머지는 내일 내려온다) | GitHub Actions 를 초록으로 고친다. 확인했고 당장 필요하면 `git -C ~/git/sector-flow pull --ff-only` |
| GitHub Actions 잡이 시작도 안 하고 실패, 로그에 `The job was not started because recent account payments have failed or your spending limit needs to be increased` · 위 `[배포]` 경고가 며칠 이어짐 | GitHub 결제 정지 — CI 가 안 돌아 어떤 커밋도 초록이 안 되고, `pull-all --ci-gate` 가 전부 멈춘다. 코드 탓이 아니다 | Settings → Billing 에서 결제·spending limit 을 푼다. **복구 뒤**: 멈춘 커밋의 CI 는 저절로 안 돈다 — `gh run list -R younghwan91/sector-flow -L 5` 로 찾아 `gh run rerun <id> --failed` 또는 빈 커밋을 올린다. 다음 07:50 이 받는다(당장은 로컬 `verify_merge.py` 를 통과한 것을 확인하고 `git -C ~/git/sector-flow pull --ff-only`) |
| `fresh 경고 (rc=75)`, 본문 `[배포] origin/main 을 N시간째 안 받았다` | 07:50 동기화가 안 돌아 받아 둔 origin/main 이 오래됐다 — 뒤처짐 경고도 믿을 수 없다 | `crontab -l \| grep pull-all`, 그 로그. `pull-all --check` 로 손으로 본다 |

크론 로그(`reports/cron.log`·`serve.log`)는 5MB 를 넘으면 `scripts/cron_run.sh` 가 `.1` 로 하나만 남기고 비운다.

## 디스크 소실·새 머신 복구

`reports/` 는 gitignore 이고 **지금 백업 대상이 아니다.** 코드는 GitHub 에, 원천 데이터는 DB(복구는 quant-airflow 소관)에 있으니 리포트는 DB 에서
다시 만들 수 있지만, 다음은 디스크와 함께 사라진다.

| 잃는 것 | 왜 다시 못 만드나 | 그 뒤의 모습 |
|---|---|---|
| `reports/events.jsonl` | 운영 사건 기록이다 | 과거 SLO·정오표가 비고 새 원장은 복구 시점부터 쌓인다. 통계는 그 앞을 `원장 도입 전`·`기록 없음(복원뿐)` 으로 적는다 |
| `reports/.archive/` | 그날 저녁 18:10 게시본이다(재구성은 확정 데이터로 만든다) | 잠정 대비를 영영 못 잰다 |
| `reports/.revision_seed/` | 위 아카이브에서 나온다 | `scripts/seed_revisions.py` 는 `아카이브 폴더가 없다` 며 시드 0일·rc 1 로 끝난다. payload 의 `revision_stats` 가 비어 수정 폭 칸이 줄어든다 |

**복구 순서**

1. 새 머신이면 먼저 기반: Tailscale 로그인, `~/git/quant-airflow` 체크아웃과 그 `.env`(접속정보·웹훅의 정본), DB 복구(그 레포 런북), dotfiles 의 `cron-install`·`pull-all`.
2. 코드: `git clone git@github.com:younghwan91/sector-flow.git ~/git/sector-flow && cd ~/git/sector-flow && uv sync --extra pg`.
3. 크론: `cron-install` 뒤 `crontab -l | grep sector-flow`. 크론은 이 레포의 `deploy/crontab.simnode` 를 읽는다(호스트 이름이 다르면 그 이름의 파일을 만든다). 웹 크론은 빈 `reports/` 에서도 뜨고 `/healthz` 는 200 이다. 리포트 크론이 겹치면 재구성이 75 로 물러나니 평일 18:10·08:20 근처는 피한다.
4. 리포트 — 백업이 있으면 받는다: `rclone copy "gdrive:2.4. 트레이딩/3. stocks 주식/simnode-files/sector-flow-reports" reports/` 뒤 `ln -sfn "$PWD/reports/<가장 늦은 기준일>" reports/latest`(링크는 rclone 이 안 옮긴다. 없으면 `/healthz` 가 503 `latest 링크가 없다`).
   없으면 DB 에서: `uv run --extra pg python scripts/rebuild_history.py --root reports --from <첫 기준일> --to <전 거래일>` — origin/main 의 깨끗한 체크아웃에서만 돈다. 폴더마다 `REBUILT.txt` 가 남아 화면이 `재구성` 이라고 말한다.
5. 확인: `curl -sS http://$(tailscale ip -4):8765/healthz` 가 `ok`, `uv run sw-flow` 가 열린다, `uv run python scripts/ops_ledger.py`. 다음 평일 20:30 점검이 원장 공백을 75 로 말하면 복구 전 날짜인지 본다.

**백업에 넣는 줄**(`~/git/quant-airflow/scripts/backup_to_gdrive.sh` 의 `sync_host_files` specs — 그 레포 소관이다):
`"sector-flow-reports|$REPO/../sector-flow/reports|/latest /*.log /*.log.1 /.*.lock /.serve.* /.cron_run.* /.asof_err.* /.rebuild/** /*.building.*/** /*.replaced.*/**"`

**리허설 실측**(2026-11-09, 임시 폴더, DB 읽기만):

| 단계 | 걸린 시간 | 비고 |
|---|---|---|
| clone·`uv sync` | 0초·1초 | 로컬 clone, uv 캐시 적중 — 새 머신은 내려받는 만큼 더 걸린다 |
| `rebuild_history.py` 3일(11-04~06, `--jobs 1`) | 339초(하루 98~140초) | 3일 다 재구성·검증 실패 0. 11-06 payload 는 운영본과 `revision_stats` 만 다르다(시드가 없어서) |
| 서버 기동~`/healthz` 200 | 1초 | `latest` 를 재구성이 걸었다 |
| 백업 사본 복원(`cp -a` 46MB, 5일치 + 원장 + 시드) | 1초 미만 | 링크 전 503, `ln -sfn` 뒤 200 · 통계가 원장을 그대로 읽었다. rclone 내려받기는 못 쟀다(백업이 아직 없다) |

폴더 50개를 `--jobs 1` 로 다시 만들면 약 95분이다(위 하루치로 잰 추정). 2026-11-08 운영 재구성 48일은 `--jobs` 병렬로 약 19분 걸렸다(`REBUILT.txt` 시각).

## 운영 사건 원장

**운영 사건 원장(`reports/events.jsonl`)** — 폴더 바꿔 끼우기가 INCOMPLETE·검증 실패 흔적과 mtime 을 지우고
크론 로그는 5MB 에서 돌아, "언제 게시됐나·몇 번 막혔나·게시 뒤 숫자가 바뀐 날" 을 나중에 셀 수 없었다. 그래서
실행·잡·웹 프로브·신선도 판정마다 JSON 한 줄을 **덧붙이기만** 한다(자르지 않는다 — 50MB 를 넘으면 `events.jsonl.2` 로
이어 쓴다). 형식·통계는 [`scripts/ops_ledger.py`](../scripts/ops_ledger.py)(`uv run python scripts/ops_ledger.py`,
`--days 90`·`--json`·`--errata`), 기한(잠정 18:30 · 확정 다음날 10:40 · 웹 5분 프로브 200)은 [`docs/slo.md`](slo.md) 가
정의하고 20:30 신선도 점검이 전 거래일을 그 기한에 대 본다. 원장 기록 실패는 리포트를 멈추지 않고, 원장은 웹
허용 목록에 없어 밖에 보이지 않는다.

## 네이버 대조

크론 리포트(`KR_QUANT_KRX_TRUTH=1`)는 네이버 시장값(개인·외국인·기관, KRX 원표와 같은지 미확인)과 시장 1일 순매수 금액을 대조해 오차 %만
`today.krx_truth` 에 적고, 네이버 수치는 `krx_truth.json` 에만 둔다([`scripts/krx_truth.py`](../scripts/krx_truth.py)) — 약관을 확인하지 못해
**내부 대조 전용**이다. 화면에 네이버 수치를 싣지 않고 비공개 리포트 밖으로 재배포하지 않는다. 시장별 GET 한 번(타임아웃 10초, 망 오류 재시도 1회),
기준일당 최대 2회다. KRX 표는 익명 조회가 막혀 있다(`KR_QUANT_KRX_TRUTH_SOURCE=krx` 는 풀렸을 때를 위해 남겼다).
실패(망·공표 전·형식 변화)는 SKIP 이다 — 리포트는 그대로 만들고, 3회 이어지면 다음날 신선도 점검이 경고한다.
