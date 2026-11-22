# 자기 키로 첫 수급 지도까지

키움증권 REST API 키가 있으면 자기 기기에서 코스피·코스닥 수급 지도(`map.html`)를 띄울 수 있다. 서버·DB 는 필요 없다.
수집한 데이터는 자기 기기의 sqlite 파일 하나에 쌓이고, 지도는 브라우저로 여는 HTML 파일 하나다.

> **자기 데이터를 남과 나누는 일은 각자 약관을 따른다.** 키움 Open API 로 받은 시세·수급은 본인 이용을 전제로 한다.
> 받은 sqlite·`payload.json`·`map.html`·카드를 남에게 넘기거나 공개하지 않는다. 남에게 보여 줄 때는 합성 데모
> (`sector-flow-demo`)를 쓴다. 이 도구는 투자 조언이 아니다. 지도는 그날까지 누가 어느 섹터를 순매수·순매도했는지만 그린다.

## 증권사 고르기 (`--broker`)

아래 1~6 은 **키움** 기준이다. 다른 증권사 키로도 지도 전용 수집을 할 수 있다 — 명령에 `--broker <이름>` 을 붙이고 키 파일의 변수 이름만
바꾼다. 키움 밖 어댑터는 공개 문서의 응답 모양으로만 만들어 **미검증**이다(지도 머리에 "미검증 어댑터" 배지). 키움 키도 있으면 대조해서
[검증 이슈](https://github.com/younghwan91/sector-flow/issues/new?template=broker-verify.yml)로 올려 주면 고마운 일이다(`docs/byo/brokers.md` §6.2).

| `--broker` | 키 발급(한 줄) | 키 파일 변수 | IP 등록 | 속도(도구 기본) | 약관에서 주의할 것 |
|---|---|---|---|---|---|
| `kiwoom` | 키움 REST API 포털(`openapi.kiwoom.com`)에서 신청 → 앱키·시크릿 | `KIWOOM_APP_KEY`·`KIWOOM_APP_SECRET` | 필수(최대 10개) | TR당 0.9/초, 초 5·분 100·시간 1,000 | 본인 이용만, 제3자 제공 금지 |
| `kis` | 한국투자 KIS Developers(`apiportal.koreainvestment.com`) 가입·계좌 인증 → **실전** 앱키(이 수급 TR 은 모의투자 미지원) | `KIS_APP_KEY`·`KIS_APP_SECRET` | 미확인 | 5/초(공지 18 을 넘지 않는다) | 1년 이용 실적이 없으면 해지 사유 |
| `ls` | LS증권 계좌 → 홈페이지 고객센터 > 매매시스템 > OPEN API > 사용등록 | `LS_APP_KEY`·`LS_APP_SECRET` | 미확인 | 1/초(t1702 상한) | **3개월 주문체결 20회 이하는 해지 사유** — `--i-understand-ls-terms` 없이는 멈춘다 |
| `toss` | 토스증권 WTS 로그인 → 설정 > Open API 에서 client_id·client_secret | `TOSS_CLIENT_ID`·`TOSS_CLIENT_SECRET` | **필수**(없으면 403) | 5/초 | 본인 매매 목적에만 · 수급이 **KRX+NXT 통합**이라 KRX 단독 sqlite 와 섞지 않는다 |
| `db` | DB증권 OPEN API 포털(`openapi.dbsec.co.kr`)에서 신청 | `DB_APP_KEY`·`DB_APP_SECRET` | 미확인 | 2/초 | 약관 미확인 · 주체가 개인·외국인·기관계 셋뿐 |
| `nh` | NH투자증권 PLUG(`nhplug.com`) — 계좌 필수 | `NH_APP_KEY`·`NH_APP_SECRET` | 미확인 | 4/초(공지 5 아래) | 약관 미확인 · 주체 셋뿐 · 토큰을 실행마다 한 번 받는다(보안 알림이 갈 수 있다) |

순서는 키움과 같다 — 종목 목록 → 지도 전용 백필 → 지도. 예(한국투자):

```bash
# kis.env — KIS_APP_KEY=…, KIS_APP_SECRET=…
sector-flow-collect --env-file kiwoom.env universe --db data/byo.db                 # 목록·업종: 키움 키가 있으면
sector-flow-collect sectors --db data/byo.db --csv krx.csv --add-missing            # 없으면: 각자 내려받은 KRX '업종분류 현황' CSV
sector-flow-collect --env-file kis.env backfill --db data/byo.db --broker kis --map-only --sectors "운송/창고,오락/문화"
sector-flow-byo map --db data/byo.db --out data/map
```

- **종목 목록**: 키움·LS·토스·DB 는 `universe --broker <이름>` 으로 받는다. 한국투자·NH 는 목록 API 가 없어 키움 키로 받거나
  `sectors --csv … --add-missing` 으로 넣는다.
- **섹터**: 키움 밖 어댑터의 목록에는 업종이 없거나(LS·DB·토스 — 토스 섹터는 한 종목이 여러 섹터라 쓰지 않는다) 체계가 다르다.
  섹터는 sqlite 에 한 벌로 남고 목록을 다시 받아도 지워지지 않는다. 키움 업종명이 없으면 `sectors --csv` 의 자기 표를 쓴다.
- **LS**: `sector-flow-collect --env-file ls.env universe --db data/byo.db --broker ls --i-understand-ls-terms` — 시작할 때 약관 조항을 찍는다.
- **토스**: 수급이 KRX+NXT 통합이라 키움·한국투자·LS 로 받은 sqlite 에는 넣지 않는다(다른 `--db`). 지도 표지가 `KRX+NXT 통합` 으로 바뀐다.
- **매일**: `sector-flow-collect --env-file kis.env daily --db data/byo.db --broker kis --map-only`.
- 키움 밖 증권사는 지도 전용(`--map-only`)만 받는다 — 일봉·주식수가 없어 수치표·터미널 화면은 짓지 않는다.

## 0. 준비물

- 키움증권 계좌와 키움 REST API 이용 신청(앱키·시크릿키)
- [uv](https://docs.astral.sh/uv/) (Python 은 uv 가 알아서 받는다)
- 브라우저

## 1. 키움 REST API 키 발급과 IP 등록

1. 키움증권 계좌로 키움 REST API 포털(`openapi.kiwoom.com`)에 로그인해 API 이용을 신청한다.
2. **앱키(App Key)·시크릿키(Secret Key)** 를 발급받는다.
3. **허용 IP** 에 이 도구를 돌릴 기기의 공인 IP 를 등록한다. 등록하지 않은 IP 에서는 요청이 거절된다.
   집 인터넷의 공인 IP 는 바뀔 수 있다 — 어느 날 갑자기 거절되면 IP 부터 다시 본다.

메뉴 이름과 순서는 키움 쪽 사정으로 바뀔 수 있다. 포털 안내를 따른다.

키는 파일 하나에 적어 둔다. 이 파일은 git·클라우드 공유 폴더에 두지 않는다.

```bash
# kiwoom.env — 값은 따옴표 없이
KIWOOM_APP_KEY=발급받은_앱키
KIWOOM_APP_SECRET=발급받은_시크릿키
```

환경변수 `KIWOOM_APP_KEY`·`KIWOOM_APP_SECRET` 가 있으면 그쪽이 먼저다. 도구는 키·토큰을 화면이나 로그에 찍지 않는다.

## 2. 설치

```bash
uv tool install git+https://github.com/younghwan91/sector-flow
```

`sector-flow-collect`(수집)·`sector-flow-byo`(지도 짓기)가 깔린다. 고친 판으로 다시 깔 때는 `uv tool install --force git+https://github.com/younghwan91/sector-flow`.

키와 IP 가 맞는지 먼저 본다. TR 6개를 한 번씩 불러 응답 **모양**만 찍는다(값·토큰 없음, 호출 7건).

```bash
sector-flow-collect --env-file kiwoom.env smoke
```

`"error"` 가 없으면 된다.

## 3. 종목·업종 목록

```bash
mkdir -p data
sector-flow-collect --env-file kiwoom.env universe --db data/byo.db
```

코스피·코스닥 보통주 전체와 업종(키움 업종명)을 `data/byo.db` 의 `stocks` 에 쓴다. 시장마다 1회(목록이 길면 연속조회 쪽이 붙는다)라 호출은 보통 2건이다.
목록이 잘려 오면 아무것도 쓰지 않고 실패로 끝난다 — 일부만 담긴 목록은 섹터 합계를 조용히 줄이기 때문이다.

## 4. 지도 전용 수집

지도는 수급만 쓴다. 그래서 종목 목록·수급 두 TR(`ka10099`·`ka10059`)만 부르는 빠른 길이 있다. 일봉·주식수·업종지수는 받지 않는다.

**먼저 섹터 두 개로 해 본다**(수 분):

```bash
sector-flow-collect --env-file kiwoom.env backfill --db data/byo.db --map-only --sectors "운송/창고,오락/문화"
```

**전 시장**(몇 시간 — 아래 표):

```bash
sector-flow-collect --env-file kiwoom.env --max-calls 6000 backfill --db data/byo.db --map-only --resume
```

- 기본은 끝날까지 **120거래일**이다(`--days` 로 바꾼다). 끝날은 KRX 휴장 달력이 정한다 — 애프터마켓이 끝나는 20:00(KST) 전이면 앞 거래일이다.
- `--max-calls` 는 한 번 실행의 호출 상한이다(기본 3,000). 전 시장 120거래일은 약 5,000건이라 올려 준다.
- 키움 호출 상한(앱키 하나에 초 5 · 분 100 · 시간 1,000)을 도구가 지킨다. 창이 차면 멈추지 않고 기다린다.
- 중간에 끊기면 **같은 명령을 다시** 친다. `--resume` 은 같은 끝날에 이미 끝낸 종목을 건너뛴다.
  끝날이 바뀐 뒤(다음 거래일 20:00 이후)에 다시 치면 처음부터 받는다 — 같은 날 안에 마치는 편이 빠르다.
- 같은 앱키를 매매 봇 등 다른 프로그램과 같이 쓰면 시작할 때 경고가 나온다. 그 프로그램이 도는 시간을 `--busy-file` 로 알려 주면 그 창에서는 요청하지 않고 멈춘다(종료코드 75, 다시 치면 이어 받는다).

## 5. 지도 짓기

```bash
sector-flow-byo map --db data/byo.db --out data/map
```

`data/map/payload.json`(수급만) → `data/map/map.html` 을 짓고 마지막 줄에 `file://…/map.html` 주소를 찍는다. 브라우저로 연다.
키움을 부르지 않는다. `--asof 2026-11-20` 처럼 지난 기준일로도 짓는다.
공유 카드(`map.png`)는 카드 생성기와 Playwright 가 있는 설치본에서만 만들고, 없으면 건너뛴다(`--no-card` 로 끈다). 지도는 그대로다.

지도 전용 수집에는 수익률·시총이 없다. 그래서 수치표·터미널 화면(`sw-flow`·`sw-ledger`)은 이 폴더를 열면
"수익률 없음(지도 전용 수집)" 이라고 적고 끝난다. 수익률까지 보려면 `--map-only` 없이 백필한다(종목당 호출이 약 3배다).

## 6. 매일 이어 받기

거래일 20:00(KST) 뒤에 한 번:

```bash
sector-flow-collect --env-file kiwoom.env --max-calls 3000 daily --db data/byo.db --map-only
sector-flow-byo map --db data/byo.db --out data/map
```

종목마다 마지막 저장일부터 그날까지 받는다(마지막 저장일은 다시 받아 덮는다). 이미 최신이면 키움을 부르지 않는다.
같은 섹터에 새로 상장한 종목은 따라 받는다.

## 예상 호출 수와 시간

수급(`ka10059`) 한 번은 약 100거래일이다. 시간은 호출 상한(시간당 1,000건)이 정한다. 전 시장은 보통주 약 2,500종목으로 잡았다.

| 단계 | 호출 수(대략) | 시간(대략) |
|---|---:|---:|
| `smoke` | 7 | 수 초 |
| `universe` | 2 + 연속조회 쪽 | 수 초 |
| `backfill --map-only`, 두 섹터(약 100종목), 120거래일 | 약 200 | 수 분 |
| `backfill --map-only`, 전 시장, 120거래일(기본) | 약 5,000(종목당 2) | 약 5시간 |
| `backfill --map-only`, 전 시장, `--days 100` | 약 2,500(종목당 1) | 약 2.5시간 |
| `daily --map-only`, 전 시장, 하루 | 약 2,500(종목당 1) | 약 2.5시간 |
| 지도 짓기(`sector-flow-byo map …`) | 0 | 수십 초 |

`--days 100` 으로 받으면 지도의 120일 창 버튼이 빠진다(창이 저장된 날 수보다 길면 감춘다).

## 막혔을 때

| 증상 | 할 것 |
|---|---|
| `KIWOOM_APP_KEY / KIWOOM_APP_SECRET 가 … 없다` | `--env-file` 경로와 변수 이름을 본다 |
| 요청이 거절된다(인증 오류) | 허용 IP 에 지금 공인 IP 가 있는지 본다 |
| `호출 상한 … 에 닿았다` | `--max-calls` 를 올리고 같은 명령(`--resume`)을 다시 친다 |
| 종료코드 75 | 운영 창(`--busy-file`)에 걸렸다. 창이 끝난 뒤 다시 친다 |
| `저장된 수급이 없다` | `daily` 전에 `backfill --map-only` 를 먼저 한다 |
| `수급만이 아닌 payload.json 이 있다` | `--out` 이 다른 리포트 폴더다. 빈 폴더를 준다 |

수집기의 내부 규칙(운영 창·호출 상한·잘린 응답 처리)은 `src/sectorflow/collect/` 의 모듈 머리말에 있다.
