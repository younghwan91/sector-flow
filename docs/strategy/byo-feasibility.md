# BYO(자기 키) 수집기 타당성·설계 — `sector-flow-collect`

작성 2026-11-08. 읽기 전용 조사(레포 수정·커밋·외부 게시 없음). 근거는 `파일:줄` 로 단다. 숫자 가운데 실측이 아닌 것은 **추정**이라고 적는다.
출발점은 [수익화 전략 리포트](reports/섹터%20수급%20리포트%20수익화%20전략.md)의 결론이다. 지금 준비할 수 있는 모델은 사용자가 **자기 키움 계좌의 API 키로 자기 PC에서 돌리는 도구**뿐이다(같은 리포트 49·58행).

**완료 조건(이 계획의 합격 바).** (a) 같은 기준일에 BYO sqlite로 만든 `payload.json` 이 kr_quant(Postgres)로 만든 것과 섹터×주체 순매수 금액 상대오차 0.5% 이내, 섹터 수익률 차 0.05%p 이내로 맞는다. (b) 완전성 관문(`scripts/completeness_gate.py`)을 그대로 통과한다. (c) 비공개 베타 10명 중 8명 이상이 Windows에서 연속 5거래일 동안 사람 손 없이 리포트를 만든다.

---

## 0. 결론 먼저

- **기술적으로는 된다.** sector-flow가 읽는 8개 표 가운데 7개는 사용자 키움 키 하나로 채울 수 있다. 쓰는 TR은 6개다(ka10099·ka10059·ka10081·ka10001·ka20003·ka20006). 업종지수(`sector_index`)도 KRX가 아니라 키움 ka20006에서 온다. 그러니 KRX 계정은 필요 없다.
- **일일 부하**는 호출 약 7,960건이다. TR별 버킷이 서로 독립이라 병렬로 돌리면 **약 50분**이 걸린다. quant-airflow 실측 48분과 같은 구조다.
- **첫 실행 백필**은 약 13,300건, **약 2.4시간**이다. ka10059 3페이지 × 2,632종목이 병목이다.
- **사용자 키로 못 채우는 것은 상장주식수 이력 하나다.** ka10001은 현재 스냅샷만 준다. 그런데 이것이 비면 섹터 수익률이 **조용히 0**이 된다(`scripts/sector_flow.py:349-350`의 `if w else 0.0`). 대안은 두 가지다. 무료 DART 키로 분기 시점값을 받거나(정확), 현재 주식수를 수정주가 이벤트로 거꾸로 되돌린 근사 시드를 쓴다(키 하나로 해결). 이 대안은 반드시 들어가야 한다.
- **가장 큰 미지수는 기술이 아니라 키움 약관이다.** 제3자 유료 소프트웨어 허용 여부, "과도한 조회" 기준, IP 등록, 제휴 필요 여부를 확인해야 한다(§4). openapi.kiwoom.com 메뉴에 **"제휴 > 제휴현황"** 이 따로 있다. 상업용 서드파티 앱에는 제휴 계약을 요구할 수 있다는 신호로 읽어야 한다.
- **노력은 약 35인일, 버퍼를 넣으면 45인일이다**(§6). 첫 마일스톤은 동등성 시험이다. 내 키로 받은 sqlite 리포트가 kr_quant 리포트와 맞는지 본다. 이것이 통과하기 전에는 패키징하지 않는다.

---

## 1. sector-flow가 읽는 표·열, 각 열의 생산자와 원천

### 1.1 읽는 곳 (grep 근거)

| 표 | 읽는 열 | 읽는 곳 |
|---|---|---|
| `supply_demand` | `code, date, close, flu_rt, acc_trde_qty, individual, foreign_, institution, etc_corp, fnnc_invt, insrnc, invtrt, bank, penfnd_etc, samo_fund`(`natn` 제외) · `source='kiwoom'` | `scripts/sector_flow.py:76-84`(load), `src/sectorflow/storage.py:261-273`(recent_dates — 거래일 달력 자체가 이 표다), `storage.py:352`(시총 가격), `scripts/completeness_gate.py:69`, `scripts/verify_report.py:708,743,1514-1768`, `scripts/freshness_check.py:80` |
| `stocks` | `code, name, sector, market` | `sector_flow.py:87`, `completeness_gate.py:72`, `verify_report.py:1600` |
| `daily_bars_adjusted` | `code, date, close`(검증은 `high, low`도) | `sector_flow.py:119`(섹터 수익률의 원천), `completeness_gate.py:83`, `verify_report.py:717,1620` |
| `daily_bars` | `code, date, volume, trade_value, source` | `sector_flow.py:775`(금액 근사 오차 VWAP), `verify_report.py:743`(단위 검사) |
| `daily_bars_raw` | `code, date, close` | `storage.py:354`(시총 가격 폴백 — `supply_demand.close`가 없을 때만), `verify_report.py:1566` |
| `shares_outstanding_history` | `code, date, shares_outstanding` | `storage.py:355`, `verify_report.py:715,1568` |
| `corporate_actions` | `code, date, upd_stkpc_tp, upd_rt` | `storage.py:358`(분할·병합·감자 `SHARE_EVENTS`=8\|16\|64, `storage.py:311`), `verify_report.py:1574` |
| `sector_index` | `code, name, date, close` | `sector_flow.py:158`(업종지수 수익률 기준선), `sector_flow.py:739`(001 코스피·101 코스닥 종합) |
| `delisted_stocks` | `code, market` | `verify_report.py:1761` **한 곳**(생존편향 모양 검사). sqlite `SCHEMA`(`storage.py:51-124`)에는 이 표가 없다 → BYO 모드에서는 이 검사를 건너뛰게 해야 한다 |

그 밖의 의존:

- `scripts/finalize_done.py:36-38`은 DB가 아니라 **quant-airflow의 Airflow 로그**(`~/git/quant-airflow/logs/dag_id=daily_finalize`)를 읽어 확정 여부를 판정한다. BYO에서는 수집기 원장으로 바꿔야 한다(§2.4).
- sqlite `SCHEMA`(`storage.py:51-124`)는 이미 위 표 8개를 정의한다(`delisted_stocks` 제외). 따라서 **BYO 수집기는 이 스키마에 그대로 쓰면 되고, 리포트 코드는 손댈 곳이 거의 없다.** `connect()`도 경로가 `postgres://`가 아니면 sqlite를 연다(`storage.py:139-168`).

### 1.2 각 표의 생산자와 원천(quant-airflow)

유니버스는 보통주만이다. ka10099의 `marketName`이 거래소·코스닥이고 코드가 `0`으로 끝나는 종목이다(`collectors/supply_demand.py:52-63`, `kiwoom_cli.py:75-76`). 2026-11-06 실측으로 키움 수급 2,632종목, `stocks` 2,640(거래소 805·코스닥 1,835), 업종지수 코드 65개, 섹터 27개다(kr_quant 읽기 전용 조회, 2026-11-08).

| 표 | quant-airflow 생산자(DAG → 수집기) | 키움 TR / 외부 API | 하루 호출(전 유니버스) | 비고 |
|---|---|---|---|---|
| `stocks` | 모든 키움 수집기의 `build_universe` → `fetch_stock_list` (`collectors/supply_demand.py:65-80`) | **ka10099** 종목정보 리스트(`mrkt_tp` 0·10) | 2 | `sector` = 키움 `upName`(KRX 업종명) |
| `supply_demand` | `daily_collection`(평일 16:00, `dags/daily_collection.py:22`) → `collectors.combined --sd-days 15`(`:45-46`) · 확정은 `daily_finalize`(화~토 06:30, `dags/daily_finalize.py:53,74-75`) | **ka10059** 투자자기관별종목별(`amt_qty_tp=2` 수량, `combined.py:142-144`) | 2,632 | 한 번 호출에 약 100거래일. 연속조회 키가 **HTTP 응답 헤더**에 있어 `kiwoom-client` 래퍼로는 1페이지만 받는다(`collectors/supply_demand.py:9-20`) — 백필에는 헤더를 읽는 경로가 필요하다(`_fetch_investor_flow_pages`, `:110-`) |
| `daily_bars` | 같은 `combined`(`combined.py:126-128`, `upd_stkpc_tp="1"` 수정주가) | **ka10081** 주식일봉차트 | 2,632 | 한 번 호출에 약 600봉(`daily_bars.py:3`). `trade_value` 단위 백만원 |
| `corporate_actions` | `daily_bars` 백필·`daily_bars_raw` DAG(`collectors/daily_bars.py:275`) | ka10081 응답 행의 `upd_stkpc_tp`·`upd_rt` | 0(일봉 응답에 포함) | 1년간 `upd_rt≠0` 이벤트 512건·440종목(kr_quant 실측) |
| `daily_bars_raw` | `daily_bars_raw`(평일 19:00, `dags/daily_bars_raw.py:32,46`) → `collectors.daily_bars --raw --days 5` | ka10081 `upd_stkpc_tp="0"` | 2,632 | sector-flow는 폴백으로만 쓴다(`storage.py:317,368`) → **BYO에서는 생략할 수 있다** |
| `daily_bars_adjusted` | `daily_price_adjust`(평일 16:55, `dags/daily_price_adjust.py:75`) · `daily_finalize.rebuild_adjusted` → **swing-it** `swing_it.price_adjust --rebuild-db`(`daily_finalize.py:79-88`) | 호출 없음(DB 계산) | 0 | 전량 재계산 6분 23초·RSS 5.19GB(`daily_price_adjust.py` docstring). 이음매 보정이 필요한 이유는 "여러 번 나눠 받은 수정주가를 이어 붙였기 때문"이다(`~/git/swing-it/src/swing_it/price_adjust.py:5-10`) |
| `shares_outstanding_history` | `weekly_listed_shares`(화 10:10, `dags/weekly_listed_shares.py:44`) → `collectors.listed_shares` | **ka10001** 종목기본정보, `flo_stk`×1,000(`listed_shares.py:47-48`) | 2,632(주 1회) | **현재 스냅샷만**(`listed_shares.py:17-26`). 과거는 DART `stockTotqySttus` 백필(`collectors/dart_shares.py:11-21`) — kr_quant에 dart 23,712행(2016-03~2026-06)·kiwoom 52,517행(2026-07-09~) |
| `sector_index` | `daily_collection.collect_sector`(`daily_collection.py:65-68`) · `finalize_sector` | **ka20003** 전업종지수(001·101 두 범위, `collectors/sector_index.py:30`) + **ka20006** 업종일봉 × 65 | 67 | 실측 71초(`daily_collection.py:61`) |

외부 비키움 원천은 DART 하나다. 상장주식수 과거 이력에만 쓴다. KRX `MDCSTAT01501` 경로는 회원 로그인이 걸린 뒤 0행을 돌려준다(`collectors/dart_shares.py:7-10`, `krx_shares.py`). 그러니 BYO에서 KRX 직접 호출은 없다.

### 1.3 호출 제한과 런타임

**제한(비공식 실측).** 키움 REST는 **TR(api_id)마다 따로** 제한한다. 지속 안전 속도는 TR당 약 1 req/s이고 버스트는 2건이다. 넘으면 HTTP 429와 `return_code 5`가 온다(`~/git/kiwoom-client/README.md:243-254`; 외부 실측 글 [AlgoLab](https://algolab.co.kr/blog/kiwoom-rest-api-429-rate-limit-2026)도 같은 수치). 공식 문서에서는 호출 한도를 찾지 못했다(openapi.kiwoom.com 메인에 수치 없음 — §4 확인 항목). quant-airflow는 `--rate 0.9`로 돈다(`collectors/kiwoom_cli.py:46`).

**토큰 제약.** 같은 앱키로 따로 로그인하면 나중 로그인이 앞 토큰을 무효화한다(`8005`). 실측으로 40런 중 4런이 이렇게 죽었다(`dags/daily_collection.py:54-62`, quant-airflow `CLAUDE.md` §4). 따라서 수집기는 **한 프로세스·한 토큰**으로 TR별 작업을 병렬로 돌린다(`AsyncKiwoomAPI`의 TR별 버킷, kiwoom-client `README.md:153-154`). 사용자가 같은 키로 다른 REST 봇을 돌리면 서로 죽인다는 점을 설치 안내에 적는다.

| 실행 | ka10059 | ka10081 | ka10001 | ka10099+ka20003+ka20006 | 합계 | 벽시계(TR 병렬, 0.9 req/s) | 직렬로 돌리면 |
|---|---|---|---|---|---|---|---|
| **매일 증분** | 2,632 | 2,632 | 2,632(매일 돌려도 벽시계 추가 0) | 2+2+65 | **≈7,965** | **≈49분**(가장 느린 TR 2,632/0.9 s) | ≈2.5시간 |
| **첫 실행 백필(§2.2)** | 7,896(3페이지) | 2,632(600봉 1회) | 2,632 | 69 | **≈13,230** | **≈2.4시간**(ka10059 7,896/0.9 s) | ≈4.1시간 |
| (선택) DART 주식수 백필 | — | — | — | DART 약 1만~1.3만(추정: 분기보고서 4~5개 × 2,632) | — | DART 키 1개 하루 한도 20,000 안(`collectors/config.py:19`) | |

사용자 PC 부하는 네트워크 대기가 대부분이고 CPU는 거의 놀린다. 리포트 생성(`sector_flow` + `sector_numbers` + `verify_report`)은 Postgres 기준 배치 전체 2~5분이다(`storage.py:132-136` 주석, cron.log 실측). sqlite 판은 측정 전이다(Phase 1 측정 항목). 메모리는 수급 약 69만 행(2,632 × 261) pandas 프레임이라 수백 MB로 **추정**한다. sqlite 파일 크기는 수급 69만 행 + 조정 일봉 158만 행(600봉 × 2,632)을 넣어 **약 150~300MB로 추정**하며 측정이 필요하다.

---

## 2. 최소 독립 수집기 설계 — `sector-flow-collect`

### 2.1 범위와 구성

```
sector-flow-collect  (새 패키지 모듈: src/sectorflow/collect/)
  auth.py      앱키 로드(OS 키체인 → .env), 토큰 1개, 로그 마스킹
  universe.py  ka10099 ×2 → stocks (보통주 필터 = quant-airflow 규칙 그대로)
  fetch.py     AsyncKiwoomAPI, TR별 큐 4개(ka10059·ka10081·ka10001·ka20006) 병렬
  write.py     sqlite upsert (storage.SCHEMA 그대로) + collect_runs 원장
  adjust.py    daily_bars_adjusted 생성(아래 2.3)
  shares.py    ka10001 스냅샷 + 과거 시드(아래 3) + 선택 DART
  run.py       backfill / daily / catchup / status 서브커맨드
```

- **스키마는 새로 만들지 않는다.** `storage.SCHEMA`(`storage.py:51-124`)를 그대로 쓰고, 아래 두 표만 sqlite에 더한다. 하나는 `collect_runs(run_id, started_at, finished_at, kind, asof, tr, calls, rows, failed, finalized)`이고, 다른 하나는 `PRAGMA user_version`으로 매기는 스키마 버전이다. sector-flow `CLAUDE.md` 규칙대로 `storage.py`는 독립 사본으로 둔다.
- **쓰지 않는 것**: `daily_bars_raw` 수집은 하지 않는다. 시총 가격은 `supply_demand.close`가 먼저이므로(`storage.py:317`) 표만 비워 둔다. 네이버 폐지 백필과 `delisted_stocks`도 쓰지 않는다. 리포트가 `sources=("kiwoom",)`로 이미 좁혀 읽기 때문이다(`sector_flow.py:83`).

### 2.2 첫 실행 백필 길이

- 리포트는 최근 **260거래일**을 싣는다(`sector_flow.py:45`, `daily_report.sh:22`). 백분위도 **같은 260일 안에서** 잰다(`sector_flow.py:387,449`, `sector_numbers.py:59,85`). 롤링 합 백분위의 분모는 `260 − win + 1`개다. 120일 창이면 141개다.
- 첫날 수익률을 내려면 그 전날 조정 종가가 하나 더 있어야 한다(`sector_flow.py:116`). 완전성 관문 기준선 5일은 이 창 안에 들어 있다(`completeness_gate.py:52`).
- **필요량은 261거래일이다**(2026-11-06 기준으로 2025-10-15~, 달력 약 13개월. kr_quant 실측).
- **권장 기본값은 300거래일**이다. 휴장 오차와 관문 기준선을 넉넉히 덮는다. 그래도 ka10059 3페이지(약 300행) 안에 들어가 호출 수가 같다.
- 만약 "모든 창의 백분위가 1년 전체 분포에서" 나오도록 사양을 바꾸면 260 + 119 = 379거래일이 필요하다. 그러면 ka10059가 4페이지가 되어 백필이 약 3.2시간으로 늘어난다. 지금 사양에는 필요 없다.
- ka10081은 1회 600봉이므로 1페이지면 된다. ka20006 업종일봉도 기간형이라 1회면 된다.

### 2.3 조정 일봉(`daily_bars_adjusted`)을 swing-it 없이 만드는 법

quant-airflow의 조정 표가 복잡한 이유는 **여러 시점에 받은 수정주가를 이어 붙인 이음매** 때문이다(`price_adjust.py:5-10`). BYO에서는 그 원인을 만들지 않는다.

1. 매일 ka10081(`upd_stkpc_tp=1`)이 종목마다 600봉을 **한 번에** 준다. 한 응답 안의 수정주가는 같은 기준이라 이음매가 없다.
2. 겹치는 날(직전 저장분과 새 응답이 겹치는 최근 15일)에 조정 종가를 비교한다. 같으면 새 행만 덧붙인다. 다르면 그 사이 기업행동이 반영된 것이므로 **그 종목의 300일을 새 응답으로 통째로 덮는다.** 1년에 약 440종목이니 하루 1~2종목꼴이고, 추가 호출은 없다.
3. `daily_bars`에는 같은 응답의 `volume`·`trade_value`를 쓴다. 금액 근사 오차와 단위 검사에 쓰인다(`sector_flow.py:775`, `verify_report.py:743`).
4. 남는 차이도 있다. 키움 수정주가가 배당락까지 반영한 계단을 swing-it은 되돌리지만(`price_adjust.py:46-47`, 연말 12칸·2~12%) BYO는 키움 값 그대로 둔다. 동등성 시험(§6 P1)에서 섹터 수익률 차가 허용 범위 안인지 확인한다.

### 2.4 매일 증분·다음 날 확정·밀린 날

- **기본 일정은 평일 18:10(잠정)과 다음 날 아침(확정)이다.**
  - T일 16:00 직후 값은 잠정이다. 2026-10-07 실측으로 종가 10/40 일치, 수급 전 주체 불일치였다(`daily_finalize.py:3-14`).
  - 05:40 재조회에서는 이미 확정값이었다(`daily_finalize.py` "왜 06:30 인가").
  - 확정 실행은 T+1 06:00 이후 아무 때나 하면 된다.
- **확정에 별도 로직이 필요 없다.** 매일 실행이 ka10059 1페이지(약 100거래일)와 ka10081 600봉을 다시 받아 최근 15일을 덮는다. 다음 실행이 곧 전날 확정이다. quant-airflow도 `--sd-days 15` 창으로 같은 일을 한다(`daily_collection.py:46`).
- **확정 표시**: `collect_runs`에 "T+1 05:40 KST 이후 시작해 성공한 실행이 T를 덮었다"는 기록이 있으면 T를 확정으로 본다. `scripts/finalize_done.py`의 Airflow 로그 판정(`:36-38`)을 이 원장 조회로 바꾸면 된다. 리포트의 "잠정/확정" 표기 규약은 그대로 둔다.
- **PC가 꺼져 있던 날**: 시작할 때 `catchup`이 `max(date)`와 오늘 사이 빈 거래일을 확인한다. 빈 날이 100거래일보다 적으면 평소 1회 실행으로 저절로 메워진다(ka10059 100일·ka10081 600봉). 그보다 길면 백필 경로로 간다.
- **완전성 관문은 그대로 쓴다.** `completeness_gate.py`의 시장별 97% 규칙(`:51-52`)이 부분 수집을 막는다. 막히면 `INCOMPLETE.txt`를 남기고 다음 실행을 기다린다.

### 2.5 실패·재시도

| 실패 | 처리 |
|---|---|
| 429 / `return_code 5` | kiwoom-client 백오프·재시도(`max_retries=5`, `kiwoom_cli.py:59`). 기본 속도는 0.9 req/s |
| `8005` 토큰 무효(다른 프로그램이 같은 키로 로그인) | 1회 재로그인 후 재시도한다. 반복되면 "같은 키를 쓰는 다른 프로그램이 있다"고 안내하고 종료한다 |
| 종목 단위 예외 | quant-airflow처럼 종목별로 격리해 이어 간다(`combined.py:150-155`). **실패율 20%를 넘으면 실행 실패**로 친다(`combined.py:210`) |
| 빈 응답 | 성공으로 넘기지 않는다. quant-airflow `CLAUDE.md` §5의 반복 사고("rows=0 무실패")를 그대로 막는다. 장 열린 날 ka10059 전 종목 0행이면 실패다 |
| 중단(전원·네트워크) | 100종목 청크마다 커밋한다(`combined.py:52`). 재실행은 `collect_runs`에서 종목 진행분을 이어받는다. 업서트가 멱등이라 다시 써도 안전하다 |
| 동시 실행 | 파일 잠금(Windows `msvcrt.locking` / POSIX `fcntl`)을 쓴다. 지금 `daily_report.sh:35`의 `flock`은 Windows에 없다 |

### 2.6 자격증명

- **기본 저장소는 OS 키체인이다.** Python `keyring`을 쓰고, Windows에서는 자격 증명 관리자, macOS에서는 Keychain이 된다. 첫 실행 마법사가 앱키·시크릿을 받아 저장한다.
- **대체 경로는 `%APPDATA%\sector-flow\.env`(권한 사용자 한정)**다. quant-airflow `collectors/config.py:35-67`의 `load_keys` 규약과 변수 이름(`KIWOOM_APP_KEY`·`KIWOOM_APP_SECRET`)을 그대로 쓴다.
- 로그에 키·토큰이 남지 않게 quant-airflow `mask_secrets`(`config.py:88-110`) 패턴을 옮긴다. 진단 출력(`diag`)은 키가 "있다/없다"만 찍는다.
- **실전 키여야 한다.** 모의서버는 실제 시세·수급을 주지 않는다(`daily_collection.py:31-32`). 그러니 사용자는 키움 실계좌와 REST 사용신청을 갖춰야 한다. 이것이 온보딩 장벽 1순위다.
- DART 키(선택)는 같은 키체인 항목에 넣는다.

### 2.7 Windows 지원 — 사용자 자리가 Windows PC

지금 코드에서 그대로는 안 도는 곳:

| 위치 | 문제 | 해결 |
|---|---|---|
| `scripts/daily_report.sh` 전체(bash·`flock`·`timeout`·`ln -sfn` — `:35,215`) | Windows에 없음 | 파이썬 오케스트레이터 `sector-flow run`으로 옮긴다. `latest`는 심볼릭 링크 대신 `latest.txt` 포인터나 디렉터리 접합점(junction)으로. Windows 심링크는 관리자·개발자 모드가 필요하다 |
| TUI `import curses`(`src/sectorflow/tui/flow_app.py:20`, `ledger_app.py:25`) | Windows CPython에 `curses` 없음 | `windows-curses` 의존을 플랫폼 조건으로 추가한다. 비개발자에게는 정적 `viewer.html`/`numbers.html`을 브라우저로 여는 쪽을 기본으로 둔다 |
| 크론(`deploy/crontab.simnode`) | — | 작업 스케줄러(`schtasks`) 등록을 설치기가 한다. "예약 시각을 놓치면 가능한 한 빨리 실행"·"절전 해제 후 실행"을 켠다 |
| 콘솔 인코딩 | cp949 콘솔에서 이모지·한글 출력이 `UnicodeEncodeError` | `PYTHONUTF8=1` 강제, 로그는 파일 UTF-8 |
| 시간대 | Windows `zoneinfo`에 tzdata 없음 | `tzdata` 의존 추가. 코드는 이미 KST 고정 오프셋을 쓴다(`sector_flow.py:67`) |
| 패키지 구성 | `pyproject.toml`의 wheel이 `src/sectorflow`만 담는다 — `scripts/sector_flow.py` 등 리포트 생성기가 **wheel에 안 들어간다** | 리포트 생성기를 `sectorflow.report.*`로 옮기고 콘솔 진입점을 만든다 |
| `finalize_done.py`(Airflow 로그 경로) | 없음 | §2.4 원장 판정으로 교체 |

---

## 3. 사용자 키로 얻을 수 없는 데이터와 대안

| 데이터 | 사용자 키움 키로? | 대안 | 영향 |
|---|---|---|---|
| 업종지수(`sector_index`) | **된다** — ka20003/ka20006 | — | 없음. KRX 직접 수집은 필요 없다 |
| 투자자별 수급(11분류) | 된다 — ka10059 | — | 없음 |
| 수정·무수정 일봉, 기업행동 | 된다 — ka10081 | — | 없음 |
| **상장주식수 과거 이력** | **안 된다** — ka10001은 현재 스냅샷만(`listed_shares.py:17-26`) | (A) **DART** `stockTotqySttus` 발행주식총수 `istc_totqy`(`dart_shares.py:23-29`). 키는 OpenDART 가입으로 받으며 **무료**이고, 키당 하루 20,000콜(`collectors/config.py:19`)이라 백필 1회에 맞는다. 분기말 시점값이고 기준일과 공시일이 다르다(`dart_shares.py:31-38`).<br>(B) **시드 근사**: 현재 주식수에 백필 구간의 분할·병합·감자 이벤트를 **역으로** 적용해 구간 시작일 스냅샷 1행(`source='seed'`)을 만든다. 그러면 `market_cap_asof_bulk`가 앞으로 이벤트를 다시 곱해 오늘과 맞는다(`storage.py:375-404`). 유상증자·CB 전환·자사주 소각은 못 잡는다.<br>(C) 첫 실행 뒤로는 매일 ka10001 스냅샷이 쌓인다. 다른 TR과 병렬이라 벽시계가 늘지 않는다 | 이 대안이 없으면 과거 구간 시총이 NaN이 되고, 섹터 일별 수익률이 **0으로** 나온다(`sector_flow.py:348-350`, 가중치 합 0 → `0.0`). 월말 섹터 시총(가속의 분모, `sector_flow.py:205-243`)도 빈다. **(B)를 기본값으로, (A)를 선택 업그레이드로** 두고, 화면에 "과거 시총 가중치: 근사(시드)/DART" 표지를 단다 |
| 상장폐지 종목(생존편향 보정) | 안 된다 — 키움은 폐지 코드를 안 준다(quant-airflow `docs/schema.md` 028) | 리포트가 이미 키움 소스만 쓴다(`sector_flow.py:83`) | `verify_report.py:1761` 검사 한 개를 BYO 모드에서 건너뛴다 |
| KRX 정보데이터시스템(MDCSTAT) | 회원 로그인이 걸려 0행(`dart_shares.py:7-10`) | 쓰지 않는다 | 없음 |
| WICS 섹터 | 무관(리포트는 키움 `upName`, 즉 KRX 업종을 쓴다) | — | 없음 |

DART 쪽 확인할 점: OpenDART 이용약관이 영리 목적 도구 안에서 사용자 본인 키로 호출하는 것을 허용하는지 원문 대조가 필요하다. 미확인이다.

---

## 4. 라이선스 — 키움에 서면으로 물을 것, 도구가 하지 말 것

### 4.1 키움 확인 항목(서면 답을 받는다)

창구는 세 곳이다.
- openapi.kiwoom.com의 [게시판(Q&A)](https://openapi.kiwoom.com/main/home)
- 서비스약관 목록 [kiwoom.com 서비스약관](https://www.kiwoom.com/h/customer/financeguide/VClauseListView)(오픈API 이용약관 본문이 여기에 있다. 동적 로딩이라 브라우저로 직접 확인해야 한다)
- [OpenAPI 안내](https://www.kiwoom.com/h/customer/download/VOpenApiInfoView), 키움금융센터 1544-9000

전화 답은 근거가 되지 않으므로 Q&A 게시판이나 이메일로 **문서 답**을 받는다.

1. **서드파티 유료 소프트웨어**: 제3자가 판매하는 프로그램을 고객이 **자기 앱키로** 실행해 자기 PC에 시세·수급을 저장·가공하는 것이 약관상 허용되는가?
2. **제휴 필요 여부**: openapi.kiwoom.com에 "제휴 > 제휴현황" 메뉴가 따로 있다. 영리 목적 서드파티 앱에 제휴 계약이 필요한가?
3. **조회량**: 하루 약 8천 건(첫날 약 1.3만 건)의 조회가 "과도한 조회에 따른 접속 차단"(OpenAPI 안내 문구, 수익화 리포트 35행)이나 알고리즘 계좌 등록 대상에 해당하는가? 공식 호출 한도는 얼마인가?
4. **IP 등록**: REST 사용신청에 허용 IP 등록이 필요한가? 가정용 유동 IP에서는 어떻게 되는가? (미확인, 온보딩 장벽이 될 수 있음)
5. **토큰 정책**: 앱키 1개 = 동시 세션 1개인가(`8005` 무효화)? 사용자가 앱키를 2개 받을 수 있는가?
6. **로컬 저장**: 조회 결과를 사용자 PC에 기간 제한 없이 저장해도 되는가?
7. **상표 표기**: 판매 페이지에 "키움 REST API 지원"이라고 써도 되는가?
8. (KRX 쪽은 수익화 리포트 §90일 계획의 KRX 질의와 묶는다) 사용자 자기 데이터로 만든 파생 통계(섹터 집계·백분위)를 사용자 자신만 보는 경우 KRX 권리 주장 대상인가?

### 4.2 도구가 하지 말 것(설계 불변식)

- **중앙 서버 중계 금지**: 판매자 서버가 키움·DART를 대신 호출하거나, 사용자 데이터를 받아 가공해 돌려주지 않는다.
- **공유 캐시·시드 DB 배포 금지**: 첫 실행을 빠르게 하려고 미리 받은 sqlite·parquet·"샘플 데이터"를 설치본에 넣지 않는다. 테스트 픽스처와 데모 화면은 **합성 데이터**만 쓴다(`verify_report.py:2157` `_synth_flow` 같은 생성기). 판매 페이지 스크린샷도 합성 데이터로 만든다.
- **사용자 사이 공유 기능 금지**: 클라우드 동기화, "친구 DB 가져오기", 리포트 공개 게시·링크 공유 기능을 넣지 않는다. 로컬 HTML 내보내기는 "개인 열람용" 고지와 함께만 둔다.
- **텔레메트리 금지**: 네트워크 목적지는 키움·DART(선택)·업데이트 확인(선택, 기본 꺼짐) 세 곳뿐이다. 이 허용 목록을 문서에 적는다. 사용 통계·오류 자동 보고를 하지 않는다. 지원은 사용자가 직접 보내는 `sector-flow diag` 출력(행 수·날짜·버전만, 값·키 없음)으로 한다.
- **판매자 운영 데이터와 분리**: quant-airflow는 "개인 비영리 연구"를 전제로 "수집 데이터를 다시 공급하는 공개 API"를 넘지 않는 선으로 둔다(quant-airflow `CLAUDE.md:93-94`). BYO 도구는 kr_quant와 어떤 연결도 갖지 않는다(DSN·백업·사본 없음).
- **조언 기능 금지**: 수익화 리포트 39·68행 그대로다. 종목 추천, 매수 신호, 알림 문구의 판정 표현, 주문 연동을 넣지 않는다. kiwoom-client에 주문 API가 있지만 도구에서는 import하지 않는다(정적 검사로 막는다).
- **라이선스 정리**: `pyproject.toml`과 `LICENSE`가 지금 **MIT**다. 유료 확장을 둘 거라면 공개 전에 코어(MIT)와 유료 모듈(독점)의 경계를 정한다. 공개 전환은 사용자가 결정한다(sector-flow `CLAUDE.md`).

---

## 5. 패키징 — 비개발자 한국 개인투자자 기준

| 방식 | 장점 | 단점 | 판단 |
|---|---|---|---|
| **pip / `uv tool install`** (+ PowerShell 한 줄 설치 스크립트로 uv가 파이썬까지 설치) | 만들기 가장 쉽다. 업데이트는 `uv tool upgrade`. 코드를 볼 수 있어 신뢰를 준다 | 터미널을 쓸 줄 알아야 한다. PATH·버전 문제를 지원해야 한다 | **베타(10명)용 1안** — 초기 사용자가 개발자·퀀트 성향(수익화 리포트 58행) |
| **PyInstaller(onedir) exe + Inno Setup 설치기** | 더블클릭 설치, 작업 스케줄러 자동 등록, 시작 메뉴. 비개발자에게 맞다 | 서명이 없으면 SmartScreen 경고가 뜬다. PyInstaller 산출물은 백신 오탐이 잦다. 코드서명 인증서가 연간 비용(금액 미조사). 빌드가 Windows CI에 묶인다 | **유료 출시용** |
| Docker | 환경이 같다 | Windows 개인에게는 Docker Desktop·WSL2 설치가 무겁고, 작업 스케줄러·브라우저 열기 연동이 어색하다 | 기각(서버형 사용자에게만 문서로 안내) |

- **업데이트**: 기본은 수동이다(`sector-flow update` 또는 설치기 재실행). 확인 기능을 켜면 GitHub Releases의 최신 태그 한 건만 조회하고 데이터는 보내지 않는다. sqlite 스키마는 `PRAGMA user_version`으로 전진 마이그레이션만 하고, 마이그레이션 전에 DB 파일을 `.bak`으로 복사한다.
- **텔레메트리 없음**: §4.2 허용 목록. 설치기도 수집하지 않는다.
- **결제·라이선스 키**: 서면 답이 오기 전에는 만들지 않는다(§6 P3). 만든다면 오프라인 서명 검증 키로 하고, 서버 핑을 하지 않는다.

---

## 6. 노력 추정과 단계 계획

### 6.1 인일(1인 기준, 추정)

| 묶음 | 일 | 내용 |
|---|---|---|
| 키움 수집기(비동기·TR별 큐·ka10059 헤더 연속조회·ka20006) | 4 | quant-airflow `combined.py`·`supply_demand._fetch_investor_flow_pages`·`sector_index.py`를 옮겨 sqlite 전용으로 줄인다 |
| sqlite 기록·원장·스키마 버전 | 2 | `storage.SCHEMA` + `collect_runs` |
| 조정 일봉(§2.3 겹침 비교·통째 덮기) | 2 | |
| 주식수(ka10001 + 시드 + 선택 DART) | 3 | DART는 `krx-fundamentals-client`의 `DartScraper` 재사용 여부 확인 |
| 증분·확정·밀린 날·관문 연결 | 1.5 | `finalize_done.py` 교체 포함 |
| `daily_report.sh` → 파이썬 오케스트레이터(잠금·latest·타임아웃) | 2 | |
| `verify_report` BYO 모드(`delisted_stocks` 검사 건너뛰기) + `tests/mutations.toml` 변이 | 1.5 | 새 검사엔 변이 의무(sector-flow `CLAUDE.md`) |
| **동등성 시험**: 내 키로 BYO 백필 → 같은 기준일 payload를 kr_quant 판과 대조 | 3 | 완료 조건 (a). `perf` 규칙처럼 차이를 표로 남긴다 |
| Windows(windows-curses·인코딩·tzdata·작업 스케줄러·keyring) | 3 | 실제 Windows PC에서 확인 — 사용자 자리 |
| 패키징(uv 설치 스크립트 → PyInstaller·Inno Setup) | 3 | 서명은 비용 결정 뒤 |
| 온보딩 문서(키움 REST 신청·실전 키·IP, 첫 실행 마법사) | 2 | |
| 베타 운영(지원·`diag`·수정) | 3 | 3주에 걸쳐 |
| 라이선스 키·결제 연동 | 2 | **서면 답 뒤에만** |
| **합계** | **≈34.5** | 버퍼 30%를 넣으면 **≈45인일** |

### 6.2 단계(수익화 리포트의 90일 계획 안에 맞춘다)

| 단계 | 기간(추정) | 끝났다는 증거 |
|---|---|---|
| **P0 서면 질의 발송** | 1주(병행) | §4.1 항목 1~7을 키움에, DART 약관 대조를 보냄. 발송 기록 |
| **P1 내 키로 동등성** | 2주 | 사용자 본인 키로 300거래일 백필 → 같은 기준일 payload가 kr_quant 판과 완료 조건 (a) 안. 관문 통과. sqlite 런타임·메모리·파일 크기 실측값 기록 |
| **P2 Windows 무인 운전** | 1.5주 | 사용자 Windows PC에서 작업 스케줄러로 연속 5거래일, 사람 손 없이 잠정 18:10 → 확정 아침 → `latest` 갱신. 끈 날 다음 기동 때 자동 보충 확인 |
| **P3 비공개 베타 10명** | 3주 | **키움 답이 1·3번에 부정적이 아닐 때만** 시작한다. 무료, 각자 키로. 10명 중 8명이 연속 5거래일 무인 생성(완료 조건 c). 설치 실패 원인 목록 |
| **P4 가격 실험** | 서면 답 + 사업자·통신판매 정리 뒤 | 6.3 |

### 6.3 사전등록 가격 실험

**현실부터 적는다.** 베타 10명으로는 어떤 가격 비교도 검정력이 없다. 수익화 리포트의 안(9,900원 vs 19,000원 × 체험 7일 vs 21일, 79행)을 2×2로 돌려 전환율 5% 대 2.5%를 가려내려면(α 0.05, 검정력 0.8) **칸당 약 900명, 합계 약 3,600명의 체험 시작자**가 필요하다(정규근사 계산). 개인 운영 수급 글 조회수가 1천대인 시장(같은 리포트 17·54행)에서는 몇 년이 걸린다. 그래서 다음처럼 사전등록한다.

1. **베타(n=10)는 가격 실험이 아니다.** 측정하는 것은 셋이다.
   - 활성화: 설치 24시간 안에 첫 리포트를 만든 비율.
   - 유지: 15거래일 중 10일 이상 자동 생성된 비율.
   - 지불의사: Van Westendorp 4문항, 그리고 "출시 시 첫 달을 지금 예약 결제하겠는가"(환불 가능)에 대한 예/아니오.
   - 진행 기준은 유지 ≥ 7/10이고 예약 의사 ≥ 3/10이다. 못 미치면 가격 실험 전에 제품을 고친다.
2. **출시 실험은 가격 한 축만 둔다.** 체험 기간은 14일로 고정한다. 칸이 2개뿐이라야 표본이 모인다. 체험 기간 축은 체험 시작자가 칸당 900명을 넘을 때 다시 연다.
   - 배정: 방문자 단위로 무작위 50:50, 9,900원 / 19,000원(월).
   - 1차 지표: **방문자당 매출**(가격 × 체험 후 유료 전환율). 전환율 자체가 아니다.
   - 판정 규칙(결과 보기 전에 고정): 칸당 체험 시작자 200명 또는 출시 후 90일 중 먼저 오는 때에 한 번 본다. 베타분포 사후확률로 P(방문자당 매출 9,900 > 19,000) ≥ 0.8이면 9,900을 택하고, 아니면 19,000을 택한다. 중간에 엿보고 멈추지 않는다.
   - 성공 바: 90일 안에 두 가격 합산 유료 30명 이상, 첫 달 해지율 20% 미만. 못 미치면 수익화 리포트의 결론대로 오픈코어·평판 자산으로 돌린다. 이 경우 유료 전환을 중단하는 것은 실패가 아니라 사전에 정한 갈래다.
3. 파일 위치: 이 설계를 `docs/strategy/prereg/pricing-YYYY-MM-DD.md`로 고정하고, 결과는 같은 파일에 덧붙인다(선택 근거를 결과 뒤에 바꾸지 않는다).

---

## 부록 — 확인하지 못한 것(추정·미확인 목록)

- 키움 공식 호출 한도, IP 등록 요건, 서드파티 유료 앱 정책, 제휴 필요 여부(§4.1). openapi.kiwoom.com 메인에는 수치·약관 본문이 없었다(2026-11-08 조회).
- OpenDART 영리 이용 조항, DART 분당 호출 한도.
- sqlite 기준 리포트 생성 시간·메모리·DB 크기(Postgres 실측 2~5분만 있음).
- 키움 수정주가와 swing-it 조정 표의 배당락 계단 차이가 섹터 수익률에 주는 크기(P1에서 측정).
- 시드 주식수 근사(§3 B)의 시총 오차. kr_quant의 DART·키움 이력과 대조해 P1에서 잰다.
- 코드서명 인증서 비용, 사업자·통신판매·부가세(수익화 리포트 41행의 세무 확인 목록).

참고: [AlgoLab — 키움 REST API 429 트러블슈팅](https://algolab.co.kr/blog/kiwoom-rest-api-429-rate-limit-2026), [키움 REST API 포털](https://openapi.kiwoom.com/main/home), [키움 서비스약관](https://www.kiwoom.com/h/customer/financeguide/VClauseListView), [키움 OpenAPI 안내](https://www.kiwoom.com/h/customer/download/VOpenApiInfoView).
