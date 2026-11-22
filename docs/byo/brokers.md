# 한국 증권사 개인용 Open API 비교 — sector-flow BYO 수집기 확장 검토

조사일 2026-11-21. 어떤 API 도 호출하지 않았고 계정도 만들지 않았다. 공개 개발자 포털·공식 GitHub·약관 원문을 먼저 보고, 블로그는 보조로만 썼다(표에 "(블로그)" 로 표시).

> 이 문서는 조사 기록이다. 어댑터 검증 현황은 아래 Task 63 이 붙이는 표를 본다. 계획: `docs/superpowers/plans/2026-11-21-flow-map-brand.md` Phase 6b.
확인하지 못한 것은 **미확인** 으로 적었다. 기준(키움)은 sector-flow `src/sectorflow/collect/` 가 지금 쓰는 ka10099·ka10059 다.

---

## 0. 한눈에

| 순위 | 증권사 | 형태 (리눅스·맥) | 종목별·일별 투자자 TR | 주체 구분 (키움 대비) | 거래소 선택 | 상한 (해당 TR) | BYO 가치 |
|---|---|---|---|---|---|---|---|
| 기준 | **키움** REST | REST+WS ✅ | `ka10059` | 개인·외국인·기관계 + 금융투자·보험·투신·**기타금융**·은행·연기금등·사모·국가 + 기타법인 + 내외국인 | 코드 접미사 `_NX`·`_AL` | 공식 수치 미확인 (sector-flow 는 TR당 0.9 req/s) | — |
| 1 | **한국투자 KIS** | REST+WS ✅ | `FHPTJ04160001` 종목별 투자자매매동향(일별) | 키움과 거의 같다(증권=금융투자, 종금=기타금융, 기금=연기금). 국가 칸 없음(기타·기타단체의 뜻은 미확인) | `J`/`NX`/`UN` | 실전 18건/초·모의 1건/초 (블로그가 포털 공지를 인용, 원문 미확인). 이 TR 은 **모의투자 미지원** | 높음 |
| 2 | **LS증권** | REST+WS ✅ | `t1702` 외인기관종목별동향 | **1:1에 가장 가깝다** — 사모·증권·보험·투신·은행·종금·기금·기타법인·개인·등록외국인·미등록외국인·국가외·기관계 | `K`/`N`/`U` | **t1702 초당 1건**(법인 3) | 높음 — 단, 약관상 "3개월 주문체결 20회 이하"가 해지 사유 |
| 3 | **토스증권** | REST+WS ✅ | `GET /api/v1/stocks/{symbol}/investor-trading` | 개인·등록외국인·기관(7 세부)·기타법인. 국가·미등록외국인 없음 | **KRX+NXT 통합만** | 초당 10회 (그룹 `STOCK_TRADING_TREND`) | 중간 — 통합 기준이라 현 리포트(KRX 단독)와 숫자가 다르다 |
| 4 | **DB증권** | REST+WS ✅ | `DAYSTOCKTJJ` 일별종목별투자자조회 | 개인·외국인(등록)·기관계만 | `J`/`NJ`/`UJ` | 초당 2건 | 낮음 — 기관 세부·기타법인 없음 |
| 5 | **NH투자증권** PLUG | REST+WS ✅ | `POST /krstock/quote/v1/currentInvestor` | 개인·외국인·기관계만 (+프로그램·거래원) | `KRX`/`NXT`/`UNT` | REST 초당 5건 | 낮음 — 같은 이유. 전 종목 목록 REST 없음(.mst 파일) |
| 6 | 대신증권 CYBOS Plus / 크레온 Plus | **Windows 32bit COM** ❌ | `CpSysDib.CpSvr7254` | 키움과 같은 13구분 | 미확인 | 미확인 | 제외 — 리눅스·맥 불가 |
| 7 | 유안타증권 티레이더 Open API | **Windows DLL/COM** ❌ | 미확인 | 미확인 | 미확인 | 미확인 | 제외 |
| 8 | 신한투자증권 신한i인디 | **Windows OCX** ❌ (블로그) | 미확인 | 미확인 | 미확인 | 미확인 | 제외 |
| — | KB증권 | 토큰 기반, REST 로 보임 (블로그·보도) | 미확인 (API 문서가 로그인 뒤에 있다) | 미확인 | 미확인 | 미확인 | 보류 — 2026-07 개인 오픈베타, 11월까지 확대 예정 |
| — | 미래에셋증권 | 개인용 공개 API 를 찾지 못함 | — | — | — | — | — |
| — | 삼성증권 | 개인용 공개 API 를 찾지 못함 | — | — | — | — | — |

"찾지 못함" 은 2026-11-21 검색 기준이다. 없다고 확인한 것은 아니다.

---

## 1. 모든 증권사에 공통인 것 (BYO 설계에 바로 걸린다)

1. **약관은 전부 "본인 이용·제3자 제공 금지"다.** 키움·KIS 는 문구까지 거의 같다(아래 §3). BYO(각자 자기 키로 자기 기기에서) 구조는 이 조항과 맞는다. 하지만 **실데이터로 그린 지도·스크린샷을 남에게 보여 주는 것**은 어느 증권사든 제3자 제공에 걸릴 수 있다 → 지금처럼 문서·그림은 데모(`--demo`)로만 만든다는 규칙을 BYO 안내에도 그대로 적는다.
2. **과부하는 이용 중지·해지 사유다.** 키움 제8·9조("비정상적인 데이터 조회에 의한 부하" → 1개월 중지, 3회 → 해지), KIS 제9·10조, LS 제9조. 2,500여 종목 × 수백 일 백필은 증권사마다 TR 상한 안에서 천천히 돌려야 한다.
3. **섹터 분류가 증권사마다 다르다.** 키움 `upName` 은 sector-flow 가 쓰는 벤더 분류 26개(KRX 업종 분류가 아님, `docs/formulas.md` §1), KIS·LS·DB·NH 는 KRX 업종지수 코드 계열로 보이고, 토스는 **자체 분류이며 한 종목이 여러 섹터에 속한다**. 증권사마다 섹터를 그 증권사 것으로 쓰면 같은 날 지도가 증권사마다 달라진다 → 섹터 표는 증권사와 떼어 한 벌로 두고(지금의 `stocks.sector` + `sector_override.py`), 수급만 증권사에서 받는 구조가 안전하다.
4. **거래소 기준.** sector-flow 수급은 KRX 단독이다(`CLAUDE.md` 데이터 함정, `sectorflow.basis.DATA_BASIS`). 키움·KIS·LS·DB·NH 는 KRX 만 고를 수 있다. 토스만 통합(KRX+NXT)만 준다.
5. **IP 등록.** 키움(최대 10개)·토스(허용 IP 관리)는 허용 IP 에서만 호출된다. 가정용 유동 IP·노트북 BYO 에는 안내가 필요하다. KIS·LS·NH·DB 의 IP 요구는 미확인.

### 섹터 출처 — 정한 것(2026-11-23, Task 56)

수급만 증권사에서 받고 섹터는 sqlite 의 `stocks.sector` 한 벌로 둔다. 행마다 출처가 `stocks.sector_source` 에 남고(`kiwoom` 키움 업종명 ·
증권사 이름 · `csv:<이름>` 사용자 표), 지도 머리에 `섹터: …` 로 적힌다. 출처가 둘 이상이면 종목 수와 "(분류 체계 섞임)" 을 붙인다.

| 길 | 어떻게 | 상태 |
|---|---|---|
| (a) 그 증권사가 주는 업종 | 어댑터의 종목 목록이 업종을 주면 출처가 그 증권사다(KRX 업종지수 계열로 보인다 — 키움 26개 분류와 다르다) | 어댑터마다 |
| (b) 가진 다른 키 | 키움 키가 있으면 `sector-flow-collect universe --db data/byo.db --broker kiwoom` 으로 업종을 받아 두고, 수급은 다른 증권사로 받는다 | 된다 |
| (c) 공개 출처 | 공공데이터 `금융위원회_KRX상장종목정보`(data.go.kr 15094775): 응답이 기준일자·단축코드·ISIN·시장구분·종목명·법인등록번호·법인명뿐이라 **업종 필드가 없다**. 이용허락범위도 "제한 없음" 이 아니라 **제4유형(출처표시+상업적 이용금지+변경금지)** 이다(2026-11-23 포털 확인) | 섹터 출처로 못 쓴다 |
| (d) 자기 표 | `sector-flow-collect sectors --db data/byo.db --csv my_sectors.csv`(머리줄 `code,sector`, 또는 KRX 정보데이터시스템 '업종분류 현황' 을 각자 내려받은 CSV 의 `종목코드,업종명` — CP949 도 읽는다) — 출처 `csv:<파일 이름>`, `--label` 로 바꾼다. 받은 표는 각자 쓴다(레포에 싣지 않는다) | 된다 |

규칙(`collect/write.py` `resolve_sector`): 사용자 표의 섹터는 증권사 목록이 덮지 않는다 · 섹터를 주는 목록은 그 섹터로 바꾼다 ·
섹터를 안 주는 목록은 다른 출처가 준 섹터를 지우지 않는다. 키움 업종명 체계의 덮어쓰기 표(`sectorflow/sector_override.py`)는 마스터에
키움 밖 출처가 섞이면 쓰지 않는다(빈 섹터는 `(미분류)`). 레포에는 키움 업종표 사본을 싣지 않는다(재배포). 토스 섹터는 한 종목이 여러
섹터에 들어 쓰지 않는다.

---

## 2. 증권사별 상세

### 2.1 키움 REST API (기준)

| 항목 | 내용 | 출처 |
|---|---|---|
| 형태 | REST(`https://api.kiwoom.com`, 모의 `https://mockapi.kiwoom.com`) + WebSocket. OS 무관 | 공식 명세 JSON `kiwoom/_data/kiwoom_api_spec.json` |
| 계좌·절차 | 키움 계좌 필수. API 사용신청 → 약관·개인(신용)정보 동의 → **IP 등록(최대 10개)** → 계좌 등록(비밀번호·SMS) → App Key/Secret 1회 다운로드 → 토큰 | openapi.kiwoom.com/intro/serviceInfo, 약관 제6조 |
| 비용 | 약관 제10조는 매매 위탁수수료만 정한다. 데이터 이용료 언급 없음 → 별도 이용료는 **미확인** | 약관 PDF |
| 투자자 TR | `ka10059` 종목별투자자기관별요청, `POST /api/dostk/stkinfo` | 공식 명세 |
| 주체 | 개인(`ind_invsr`)·외국인(`frgnr_invsr`)·기관계(`orgn`)·금융투자·보험·투신·**기타금융(`etc_fnnc`)**·은행·연기금등·사모펀드·국가(`natn`)·기타법인·내외국인(`natfor`) | 공식 명세 |
| 단위 | `amt_qty_tp` 1=금액(백만원)/2=수량, `unit_tp` 1000=천주/1=단주. sector-flow 는 수량(주) | 공식 명세, `collect/fetch.py` |
| 과거 | `dt` 기준일부터 과거로, `cont-yn`·`next-key` 연속조회. 1쪽 ≈ 100거래일(sector-flow 실측, `collect/run.py`) | 공식 명세 |
| KRX/NXT | `stk_cd` "KRX:039490, NXT:039490_NX, SOR:039490_AL". sector-flow 는 접미사 없이 → KRX 단독 | 공식 명세 |
| 종목·업종 | `ka10099` 종목정보 리스트(`mrkt_tp` 0 코스피/10 코스닥) → `upName`(업종명)·`upSizeName`·`nxtEnable` 등. 업종 집계는 `ka10051` 업종별투자자순매수, `ka20003` 전업종지수 | 공식 명세 |
| 상한 | 포털 문서에 TR별 "TR 호출 한도" 칸이 있으나(토큰발급 `au10001` = 초당 5회) ka10059·ka10099 값은 **미확인**. 오류 1700 "허용된 API 요청 개수를 초과", 1701 전체, 1702 그룹. sector-flow 는 TR당 0.9 req/s | openapi.kiwoom.com/guide/apiguide, 공식 명세 error_codes |
| 모의 | 모의투자 App Key 별도. ka10059 모의 지원 여부 미확인 | serviceInfo |
| 약관 (오픈 API 서비스 이용약관, 2026-06-01 개정) | 제5조③ "고객은 회사에서 제공하는 시세 정보를 고객이 직접 사용할 목적의 프로그램 개발 등 개인의 업무에 한하여 이용해야 하며, 제3자에게 제공해서는 아니 된다." / 제8조① 2호 "비정상적인 데이터 조회에 의한 부하가 발생하는 경우" → 1개월 중단 / 제9조① 2호 "…부하가 3회 이상 발생하는 경우" → 해지 / 제11조 유량 제어 정책은 포털 공지에 게시. 자동화 프로그램 자체를 막는 조항은 없다 | download.kiwoom.com/deploy/AG001/pdf/AG001_110_20260601.pdf |
| Python | 공식 `Kiwoom-Securities/Kiwoom-REST-API` — **키움 독자 라이선스**("회사의 사전 서면 동의 없이 … 복제, 배포 … 수정 … 금지") → sector-flow 에 포함·재배포 불가. 비공식 `bamjun/kiwoom-rest-api`(MIT). sector-flow 는 자체 클라이언트(`collect/kiwoom.py`) | github.com/Kiwoom-Securities/Kiwoom-REST-API LICENSE.md |

### 2.2 한국투자증권 KIS Developers

| 항목 | 내용 | 출처 |
|---|---|---|
| 형태 | REST + WebSocket. OS 무관 | apiportal.koreainvestment.com |
| 계좌·절차 | 약관 제7조 "회사 홈페이지에서 약관 동의, 본인인증, 계좌인증 등을 통해 가입". 앱키는 계좌 단위("계좌의 Appkey, App secret 으로 토큰을 발급"). 만 19세 미만 불가(제8조) | KIS 약관, 포털 howto-use |
| 비용 | 약관 제11조 "수수료를 부과한다. 단, 수수료가 0원인 경우는 해당하지 않는다" — 금액은 포털 게시. 구형 eFriend Expert 안내는 "API 사용에 따른 별도 이용료는 없음". KIS Developers 데이터 이용료는 **미확인** | KIS 약관, securities.koreainvestment.com/main/customer/systemdown/OpenAPI.jsp |
| 투자자 TR ① | **`FHPTJ04160001` 종목별 투자자매매동향(일별)**, `GET /uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily`. HTS [0416] 화면과 같다 | 포털 API 메타, 공식 GitHub `examples_llm/domestic_stock/investor_trade_by_stock_daily` |
| 주체 ① | 개인·외국인(등록/비등록 따로 + 합)·기관계·증권·투자신탁·사모펀드·은행·보험·종금·기금·기타·기타법인·기타단체 — 각각 매수·매도·순매수 수량과 대금 | 공식 GitHub `chk_investor_trade_by_stock_daily.py` |
| 단위 ① | "※ 단위 : 금액(백만원) 수량(주)" — 한 응답에 둘 다 | 포털 API 메타 |
| 과거 ① | `FID_INPUT_DATE_1` 기준 + `tr_cont` 연속조회("N : 다음 데이터 조회 (output header의 tr_cont가 M일 경우)"). 한 번에 몇 건·몇 년까지인지 **미확인**. 당일분은 15:40 이후 | 포털 API 메타 |
| 모의 ① | "모의투자 미지원" (`virtualTrId`) | 포털 API 메타 |
| 투자자 TR ② | `FHKST01010900` 주식현재가 투자자 `inquire-investor` — 개인·외국인·기관계만(순매수·매수·매도 수량·대금). "외국인은 외국인(…등록 고유번호가 있는 경우)+기타 외국인". 모의 지원. 건수 미확인 | 포털 API 메타 |
| KRX/NXT | `FID_COND_MRKT_DIV_CODE` "J:KRX, NX:NXT, UN:통합" | 포털 API 메타 |
| 종목·업종 | 전 종목 REST 없음 → 마스터 파일 `kospi_code.mst`·`kosdaq_code.mst`(지수업종 대·중·소분류 코드 포함) + `idxcode.mst`(업종코드 이름). 종목별은 `search_stock_info` | 공식 GitHub `stocks_info/kis_kospi_code_mst.py`, `sector_code.py` |
| 상한 | 공식 README: "모의투자 계좌는 REST API 호출 제한이 낮습니다", 초과 시 `EGW00201`. 수치는 블로그가 포털 공지 "API 호출 유량 안내 (REST, 웹소켓) (2026.04.20 기준)"를 인용해 **실전 1초당 18건, 모의 1초당 1건, 토큰 1초당 1건, 신규 계좌 첫 3일은 3건** — 원문 미확인 | 공식 GitHub README, algolab.co.kr/blog/kis-egw00201-rate-limit-fix-2026 (블로그) |
| 약관 (오픈 API 서비스 이용 약관(고객), 2022-10-01 시행) | 제5조③ "고객은 회사에서 제공하는 시세(국내주식, 해외주식, 국내선물/옵션 등)정보를 고객이 직접 개발한 프로그램 등 개인의 업무에 한하여 이용해야 하며, 제3자에게 제공해서는 아니 된다." / 제9조①4호·제10조①6호 서버 부하 → 중지·해지 / 제10조①4호 "최근 1년간 오픈 API 서비스 이용 실적이 없는 경우" 해지. 주문 실적을 요구하는 조항은 없다 | apiportal.koreainvestment.com/api/terms/public?termsType=MARKET (포털 하단 '이용약관') |
| Python | 공식 `koreainvestment/open-trading-api`(저장소 루트에 라이선스 표기 없음, MCP 하위 폴더는 MIT). 대표 오픈소스 `python-kis`(MIT), `pykis`(Apache-2.0) | GitHub, PyPI |

### 2.3 LS증권 OPEN API (구 이베스트)

| 항목 | 내용 | 출처 |
|---|---|---|
| 형태 | REST(`https://openapi.ls-sec.co.kr:8080`) + WebSocket. OS 무관. (별도로 Windows 전용 xingAPI 도 있음) | LS 포털 API 메타, openapi.ls-sec.co.kr/intro |
| 계좌·절차 | 약관 제7조① "회사에 개설된 계좌를 보유하고 있어야 한다", ② 홈페이지(고객센터>매매시스템>OPEN API>사용등록/해지)에서 약관동의·본인인증·계좌인증. 만 19세 미만 불가(제8조) | LS 약관 |
| 비용 | 제11조 매매 시 API 매매수수료. 시세 이용료는 해외선물(CME 등)만 명시(제10조). 국내 시세 이용료 언급 없음 | LS 약관 |
| 투자자 TR | **`t1702` 외인기관종목별동향**, `POST /stock/frgr-itt` (같은 묶음 `t1717`·`t1716`). 포털 설명 "종목별 외인/기관 거래현황을 추정할 수 있는 서비스입니다.(실시간정보 아님)" | LS 포털 API 메타 |
| 주체 | `tjj0000` 사모펀드·`0001` 증권·`0002` 보험·`0003` 투신·`0004` 은행·`0005` 종금·`0006` 기금·`0007` 기타법인·`0008` 개인·`0009` 등록외국인·`0010` 미등록외국인·`0011` 국가외·`0018` 기관·`0016` 외인계(등록+미등록)·`0017` 기타계(기타+국가) | programgarden `t1702/blocks.py`, LsApiHelper 명세(포털 미러) — 필드 이름표는 2차 자료 |
| 단위 | `volvalgb` 0=금액·1=수량·2=단가, `msmdgb` 0=순매수·1=매수·2=매도, `gubun` 0=일간·1=누적. 금액 단위 미확인 | 같음 |
| 과거 | `fromdt`~`todt` 범위 지정. 1회 건수·연속조회 방식 **미확인** | 같음 |
| KRX/NXT | `exchgubun` K=KRX·N=NXT·U=통합 | 같음 |
| 종목·업종 | `t8436` 주식종목조회(업종 칸 없음), `t8424` 업종전체조회(`upcode`·`hname`), `t1516` 업종별종목시세(`upcode` → 종목). 업종 체계는 KRX 업종지수 코드로 보이나 미확인 | LsApiHelper `specs/blocks.json`(포털 미러) |
| 상한 | **t1702·t1717·t1716 개인 초당 1건, 법인 3건** (`ThroughputQuotaRule`) | openapi.ls-sec.co.kr/api/apis/public/90378c39-f93e-4f95-9670-f76e5c924cc6 |
| 모의 | 모의투자 환경 있음(포털 소개). t1702 모의 지원 미확인 | openapi.ls-sec.co.kr/intro |
| 약관 (OPEN API 서비스 이용약관, 2025-03-31 개정) | 제9조① 중지·해지 사유: "OPEN API 서비스를 이용해 얻을 수 있는 투자정보, 시세, 계좌, 체결내역, 잔고 등의 데이터를 타인에게 양도하거나 매도하는 경우", "본인이 직접 사용하는 것이 아닌 다른 서비스를 위하여 시세를 재사용할 경우", 서버 부하, "고객이 OPEN API 사용등록일로부터 3개월 이상 로그인이 없는 경우", **"고객이 3개월간 상품별 구분 없이 OPEN API를 통한 주문체결이 20회 이하인 경우"** / 제12조 데이터 저작권 / 제13조 유량제어 | www.ls-sec.co.kr/guide/agreement/INAgreement.jsp?content_no=3643 |
| Python | 포털 소개에 `ls-openapi` SDK 가 언급되나 PyPI 에서 찾지 못함(미확인). 비공식 `LsApiHelper`(MIT), `programgarden`(AGPL-3.0) | GitHub |

> LS 의 주문 20회 조항: 수집만 하는 BYO 사용자는 문구상 해지 사유에 걸린다("시정조치를 요구할 수 있고 … 시정하지 않을 시" — 즉시 해지는 아니다). 실제 운용 여부는 미확인. 넣는다면 안내문에 이 조항을 그대로 적어야 한다.

### 2.4 토스증권 Open API

| 항목 | 내용 | 출처 |
|---|---|---|
| 형태 | REST(`https://openapi.tossinvest.com`) + WebSocket, OpenAPI 3.0 JSON·AsyncAPI 공개 | developers.tossinvest.com/llms.txt, openapi-docs/latest/openapi.json (v1.2.28) |
| 계좌·절차 | "토스증권 WTS에 로그인 후 설정 > Open API 메뉴에 진입하여 client_id 와 client_secret 을 발급", **허용 IP 등록 필수**(아니면 403). 시세 API 는 토큰만으로 호출. 2026-08 정식 오픈·전체 고객 확대(보도) | openapi-docs/overview.md, economist.co.kr 2026-08-13 기사 |
| 비용 | 미확인 | — |
| 투자자 TR | **`GET /api/v1/stocks/{symbol}/investor-trading`** 투자자별 매매동향 조회 | openapi.json |
| 주체 | 개인·외국인(**등록외국인만**)·기관 합계 + `breakdown` 7개(금융투자·보험·투신·사모펀드·은행·기타금융·연기금)·기타법인. 국가·미등록외국인 없음. 외국인 보유·CFD 잔고도 같이 | openapi.json `StockInstitutionTradingBreakdown` |
| 단위 | 매수·매도·순매수 **수량(주)만**. "거래대금(금액) 축은 제공하지 않습니다." | openapi.json |
| 과거 | `count` 최대 100, `until`·`nextUntil` 로 과거 페이지. 몇 년까지인지 미확인. 당일 잠정치에는 개인·기관 세부·기타법인이 null, 확정치는 "해당 일자 저녁에 반영" | openapi.json |
| KRX/NXT | **"거래량은 KRX·NXT 통합 기준"** — 거래소 선택 없음 | openapi.json |
| 종목·업종 | `/api/v1/stocks/all?market=KOSPI|KOSDAQ`(보통주 필터 `commonShare`), `/api/v1/sectors`(대·중·소 3단), `/api/v1/sectors/{id}/stocks`. "섹터는 토스증권 자체 산업 분류로, **한 종목이 여러 섹터에 속할 수 있습니다**." | openapi.json |
| 상한 | `STOCK_TRADING_TREND` 초당 10회, `STOCK_ALL` 1회, `SECTOR` 5회. 응답 헤더 `X-RateLimit-*`. "비정상 사용이 의심되는 일부 IP 대역에서는 … 초당 최대 1회로 제한" | overview.md |
| 모의 | 모의 환경 언급 없음(미확인) | — |
| 이용 정책 | "API를 통해 제공받는 정보는 투자자 본인의 매매 목적에 한하여 이용하여야 합니다. 상업적 이용이 제외될 뿐만 아니라 비상업적인 용도라 하더라도 제 3자에게 배포하는 행위는 엄격히 금지됩니다." (약관 원문은 미확인 — FAQ 의 데이터 이용 정책) | openapi-docs/faq.md |
| Python | 공식 SDK 확인 못함. OpenAPI JSON 이 있어 생성은 쉽다 | — |

### 2.5 DB증권 (구 DB금융투자) OPEN API

| 항목 | 내용 | 출처 |
|---|---|---|
| 형태 | REST(`https://openapi.dbsec.co.kr:8443`) + WebSocket. "별도 프로그램 설치 없이 웹 표준 REST" (보도) | openapi.dbsec.co.kr/apiservice, smarttoday 2026-09 기사 |
| 계좌·비용·약관 | 미확인(약관 API 가 세션 없이 열리지 않음). 헤더 `mac_address` "제휴사인 경우 필수 세팅, 개인은 세팅 X" → 개인 이용 전제 | 포털 TR 속성 |
| 투자자 TR | `DAYSTOCKTJJ` 일별종목별투자자조회, `/api/v1/quote/kr-stock/inquiry/daily-investor` | openapi.dbsec.co.kr/api/apis/public/2a750d3d-5ea2-4bb6-b3d6-411956d42e75 |
| 주체 | 기관계·외국인(`FrgnReg`)·개인 — 매수·매도 수량과 금액. 기관 세부·기타법인 없음 | 포털 TR 속성 |
| 단위 | 수량(주)·금액(예시로 보아 천원 단위로 보임, 미확인) | 포털 응답 예시 |
| 과거 | `InputDate1`~`InputDate2`, `cont_yn`·`cont_key` 연속. 건수 미확인 | 포털 TR 속성 |
| KRX/NXT | `InputCondMrktDivCode` J=KRX·NJ=NXT·UJ=통합 | 포털 TR 속성 |
| 종목·업종 | `JCODES` 주식종목 조회, `USTOCKCOND` 업종분류코드(1001 코스피종합, 1005 음식료·담배 … — KRX 업종지수 체계로 보임), `USTOCKCONDLIST` 업종구성종목, 섹터분류코드·섹터구성종목 별도. 업종 집계 `일별업종별투자자조회` 도 있음 | 포털 API 목록 |
| 상한 | DAYSTOCKTJJ 초당 2건, JCODES 3건, 업종 2건 (`ThroughputQuotaRule`) | 포털 API 메타 |
| Python | 비공식 `pydbsec`(MIT) | PyPI |

### 2.6 NH투자증권 PLUG (나무·N2 공용, 2026-08 출시)

| 항목 | 내용 | 출처 |
|---|---|---|
| 형태 | REST(`https://api.nhplug.com:8443`, 모의 `moapi`) + WebSocket. 기존 QV Open API 와 별개 | www.nhplug.com/llms.txt (문서 개정 2026-11-19) |
| 계좌·비용 | "API 사용 신청 시 당사 계좌 보유 필수"(공식 블로그). 매매수수료 국내 1bp. 데이터 이용료 미확인. 약관 미확인 | mynamuhbegin.com 블로그(공식), llms.txt |
| 투자자 TR | `POST /krstock/quote/v1/currentInvestor` 주식현재가 투자자 | openapi-docs/krstock/openapi.json |
| 주체 | 개인(`person`)·기관계(`gigwan`)·외국인(`frgn_ntby_qty`, `invest`)·거래원(`account`)·프로그램 — **순매수 수량만**. 기관 세부·기타법인 없음 | 같음 |
| 과거 | `array_cnt` 요청건수(길이 3, 필수), 헤더 `cts`·`cts_flag` 연속. 깊이 미확인 | 같음, llms.txt |
| KRX/NXT | `market_cd` KRX/NXT/UNT | 같음 |
| 종목·업종 | "전 종목 목록·종목명을 조회하는 REST API 는 없습니다" → 마스터 `m_new_stock.mst`(업종 대·중분류, K=코스피/Q=코스닥 접두) | llms.txt, nhplug-sdk `instruments/headers/m_new_stock.h` |
| 상한 | "REST 1초당 최대 5건" | llms.txt |
| Python | 공식 `PLUG-OpenAPI/nhplug-sdk`(MIT) | GitHub |

### 2.7 대신증권 CYBOS Plus / 크레온 Plus

| 항목 | 내용 | 출처 |
|---|---|---|
| 형태 | **Windows COM(`cpsysdib.dll`), 32bit**, 관리자 권한 실행 필요(블로그). 대신 계좌는 CYBOS, 크레온 계좌는 크레온 Plus | CYBOS Plus 도움말 미러 github.com/cybosplus/cybosplus.github.com, pkg.go.dev/github.com/hspan/creon |
| 투자자 TR | `CpSysDib.CpSvr7254` [7254 매매입체분석(투자주체별 현황)], Request/Reply, 연속 O | 도움말 `cpsysdib_rtf_1_/cpsvr7254.htm` |
| 주체 | 개인·외국인·기관계("기존 기관계 + 국가지자체")·금융투자·보험·투신·은행·기타금융·연기금·기타법인("기존 기타계 - 국가지자체")·기타외인·사모펀드·국가지자체 | 같음 |
| 단위·과거 | 전체 조회 시 순매수(단위 미확인), 개별 투자자 조회 시 매도·매수 수량·금액(백만). 1회 약 17행 + 연속(블로그) | 같음, freshrimpsushi.github.io/en/posts/2172 |
| 종목·업종 | `CpUtil.CpCodeMgr.GetStockIndustryCode`(증권전산업종코드)·`GetIndustryList`·`GetIndustryName` | 도움말 `cputil_rtf_1_/cpcodemgr.htm` |
| 상한·약관·NXT | 미확인 | — |

리눅스·맥에서 못 돈다. 주체 구분은 키움과 같으니 Windows 사용자용 별도 수집기로는 가능하지만 이번 범위(REST) 밖이다.

### 2.8 그 밖

| 증권사 | 확인한 것 | 출처 |
|---|---|---|
| 유안타증권 | "유안타 티레이더 Open API" — "DLL과 COM 2가지 버전으로 제공", Windows 7 이상, "온라인ID 등록 필수", "상품에 따라 API 이용에 따른 별도 이용료 부과". 투자자 TR 미확인 | www.myasset.com/myasset/trading/apiSvc/TR_1604001_P1.cmd |
| 신한투자증권 | 신한i인디(OCX, 32/64bit) — 블로그 자료만. 공식 신청·TR·약관 미확인 | wikidocs.net/book/2167 (블로그) |
| KB증권 | 2026-07-20 개인 대상 Open API 오픈베타, 국내·해외주식 시세·주문·잔고·체결, "11월까지 … 이용 대상을 단계적으로 확대"(보도). 포털 `openapi.kbsec.com`, API 문서는 로그인 뒤 — 투자자 TR·상한 미확인 | mt.co.kr 2026-08-04 기사, algolab.co.kr/blog/kb-securities-open-api-personal-2026 (블로그) |
| 미래에셋증권 | 개인용 공개 API 를 찾지 못함. "트레이딩 Open API" 명세 작성 아르바이트 공고(2026-07)만 확인 | cse.snu.ac.kr/community/notice/25298 |
| 삼성증권 | 개인용 공개 API 를 찾지 못함 | — |
| (범위 밖) 메리츠증권 | "AI 주문용 오픈 API 정식 출시" 보도만 봤다. 미조사 | 검색 요약 |

---

## 3. 약관 문구 비교 (원문 짧게)

| 증권사 | 개인 이용·제3자 제공 | 자동화 | 활동 요건 |
|---|---|---|---|
| 키움 | 제5조③ "직접 사용할 목적의 프로그램 개발 등 개인의 업무에 한하여 … 제3자에게 제공해서는 아니 된다" | 금지 조항 없음. 부하만 제재(제8·9조) | 없음 (REST 약관 기준) |
| KIS | 제5조③ "직접 개발한 프로그램 등 개인의 업무에 한하여 … 제3자에게 제공해서는 아니 된다" | 금지 조항 없음. 부하 제재(제9·10조) | "최근 1년간 … 이용 실적이 없는 경우" 해지 가능 |
| LS | 제9조① "데이터를 타인에게 양도하거나 매도", "다른 서비스를 위하여 시세를 재사용" → 중지·해지 | 금지 조항 없음. 부하 제재 | "3개월 이상 로그인이 없는 경우", "3개월간 … 주문체결이 20회 이하인 경우" |
| 토스 | "투자자 본인의 매매 목적에 한하여 … 비상업적인 용도라 하더라도 제 3자에게 배포하는 행위는 엄격히 금지" (FAQ) | 미확인 | 미확인 |
| DB·NH·KB·유안타·신한·대신 | 미확인 | 미확인 | 미확인 |

토스의 "본인의 매매 목적에 한하여" 는 다른 곳보다 좁다. 섹터 지도 관측이 "매매 목적" 인지는 해석의 문제라 안내문에 원문을 싣는 편이 낫다.

---

## 4. 주체 대응표 — sector-flow 11칸 ← 각 증권사

sector-flow `storage.INVESTOR_COLUMNS` 는 11칸이다(키움 `etc_fnnc` 기타금융·`natfor` 는 받지 않는다).

| sector-flow 칸 | 키움 ka10059 | KIS FHPTJ04160001 | LS t1702 | 토스 | DB | NH | 대신 7254 |
|---|---|---|---|---|---|---|---|
| individual 개인 | `ind_invsr` | `prsn_ntby_qty` | `tjj0008` | `individual` | `Prsn` 매수−매도 | `person` | 개인 |
| foreign_ 외국인 | `frgnr_invsr` | `frgn_ntby_qty`(등록+비등록) | `tjj0016` 외인계 | `foreigner` (**등록만**) | `FrgnReg` (등록) | `frgn_ntby_qty` | 외국인(+기타외인 별도) |
| institution 기관계 | `orgn` | `orgn_ntby_qty` | `tjj0018` | `institution` | `Orgn` | `gigwan` | 기관계(국가지자체 포함) |
| fnnc_invt 금융투자 | `fnnc_invt` | `scrt_` 증권 | `tjj0001` 증권 | `financialInvestment` | — | — | 금융투자 |
| insrnc 보험 | `insrnc` | `insu_` | `tjj0002` | `insurance` | — | — | 보험 |
| invtrt 투신 | `invtrt` | `ivtr_` | `tjj0003` | `trust` | — | — | 투신 |
| bank 은행 | `bank` | `bank_` | `tjj0004` | `bank` | — | — | 은행 |
| penfnd_etc 연기금등 | `penfnd_etc` | `fund_` 기금 | `tjj0006` 기금 | `pensionFund` | — | — | 연기금 |
| samo_fund 사모 | `samo_fund` | `pe_fund_` | `tjj0000` | `privateEquityFund` | — | — | 사모펀드 |
| natn 국가 | `natn` | 없음 (`etc_orgt` 기타단체·`etc` 기타의 뜻 미확인) | `tjj0011` 국가외 | 없음 | — | — | 국가지자체 |
| etc_corp 기타법인 | `etc_corp` | `etc_corp_` | `tjj0007` | `otherCorporation` | 없음 | 없음 | 기타법인 |
| (참고) 기타금융 | `etc_fnnc` | `mrbn_` 종금 | `tjj0005` 종금 | `otherFinancialInstitution` | — | — | 기타금융 |

지도에서 맞추는 한 줄:
- **KIS**: 기관 세부는 이름만 바꿔 1:1(증권→금융투자, 기금→연기금, 종금→기타금융). 국가는 결측으로 두고 `etc_orgt`·`etc` 를 국가로 넣지 않는다 — 뜻이 확인될 때까지.
- **LS**: 1:1. `tjj0011` 국가외 → natn, 외국인은 `tjj0016`(등록+미등록).
- **토스**: 기관 세부 7개는 1:1, 국가는 결측. 외국인은 등록외국인만이라 키움 외국인과 기준이 다르다고 화면에 표시하고, 거래소 표지를 "KRX+NXT 통합" 으로 바꾼다(KRX 단독 리포트와 섞지 않는다).
- **DB·NH**: 기관 세부 없음 → 기관계만, 투신·연기금 기반 화면(`INST_DETAIL`)은 결측. 기타법인 없음 → 잔여(−(개인+외국인+기관))로 메우지 않는다. 잔여에는 미등록외국인·국가·기타법인이 섞이고 네 주체 합 ≈ 0 가정이 깨진다. 결측으로 둔다.

---

## 5. sector-flow BYO 에 넣을 가치 — 순위와 이유

판정 기준: (a) 리눅스·맥에서 도는 REST인가, (b) 주체 구분이 키움 11칸과 맞는가, (c) KRX 단독을 고를 수 있는가, (d) 종목 목록·업종을 API 로 받는가, (e) 상한·약관이 백필·수집 전용 사용을 견디는가.

1. **한국투자 KIS (`FHPTJ04160001`)** — (a) REST ✅ (b) 기관 세부·기타법인까지, 국가만 불확실 (c) `J` ✅ (d) 마스터 파일(.mst)로 업종 코드 (e) 상한이 가장 넉넉하고(블로그 인용 18건/초) 약관에 주문 실적 요건이 없다. 개인 사용자층도 가장 넓다. 약점은 이 TR 이 모의 미지원이라 실전 키가 필요하다는 것, 한 번에 받는 건수가 미확인이라는 것.
2. **LS증권 (`t1702`)** — 주체 구분은 가장 정확하게 맞는다(국가외·미등록외국인까지 따로). 하지만 초당 1건이라 키움과 같은 속도이고, **약관 제9조의 "3개월간 주문체결 20회 이하" 해지 사유**가 수집 전용 사용자에게 직접 걸린다. 기술 적합도로는 1위, 운영 위험 때문에 2위.
3. **토스증권** — 문서 품질·상한(초당 10회)·전 종목·섹터 API 가 가장 좋다. 그러나 수급이 KRX+NXT 통합만이라 현 리포트(KRX 단독)와 같은 날 숫자가 다르고, 섹터가 자체 분류·중복 소속이라 지금의 "한 종목 한 섹터" 합계와 맞지 않는다. 외국인은 등록외국인만, 국가 없음. 별도 기준 표지를 달고 받는다면 가치 있다.
4. **DB증권** — REST·KRX 선택·업종 API 는 있으나 주체가 개인·외국인·기관계 셋뿐이다. 기관 세부(투신·연기금 우선 규칙)를 못 그린다.
5. **NH PLUG** — DB 와 같은 이유에 더해 수량만, 종목 목록 REST 없음.
6. 대신·크레온, 유안타, 신한 — Windows 전용이라 제외. (대신 7254 는 주체 구분이 완전하므로 Windows 용 별도 수집기라면 후보.)
7. KB — 문서가 공개되면 다시 본다(2026-11 확대 예정 보도).

---

## 6. 확인하지 못한 것 (다음에 볼 것)

- 키움 ka10059·ka10099 의 공식 TR 호출 한도 값.
- KIS 공식 유량 공지 원문(블로그 인용 18/1건), `FHPTJ04160001` 1회 건수와 과거 깊이, `etc`·`etc_orgt` 의 뜻, 데이터 이용료.
- LS t1702 1회 건수·연속조회 방식·금액 단위, t1702 모의 지원, 주문 20회 조항의 실제 운용, `ls-openapi` SDK.
- 토스 약관 원문(FAQ 의 데이터 이용 정책만 확인), 과거 깊이, 이용료.
- DB·NH·KB·유안타·신한 약관, DB 금액 단위, KB 투자자 TR.
- 대신 CYBOS 상한·NXT 구분.
- KIS·LS·NH·DB 의 허용 IP 요구.
- 키움 `frgnr_invsr` 가 등록외국인만인지 기타외국인 포함인지.

---

## 6.1 어댑터 — 구현한 것(`src/sectorflow/collect/brokers/`)

키움 밖 어댑터는 공개 문서의 응답 모양을 보고 손으로 쓴 최소 표본으로만 시험했다 — 실제 키로 키움과 대조하기 전까지 **미검증**이다.
수집은 지도 전용(`--map-only`, 종목 목록 + 종목별 일별 투자자 순매매)만 한다. 주지 않는 주체 칸은 NULL(모름)이다.

| `--broker` | 키 환경변수 | 수급 TR | 주체(11칸 중) | 거래소 | 기본 속도 | 종목 목록 | 검증 |
|---|---|---|---|---|---|---|---|
| `kiwoom` | `KIWOOM_APP_KEY`·`KIWOOM_APP_SECRET` | `ka10059` | 11 | KRX | TR당 0.9/초 + 초 5·분 100·시간 1,000 | `ka10099`(업종 포함) | 기준(Phase 1 차 0) |
| `kis` | `KIS_APP_KEY`·`KIS_APP_SECRET` | `FHPTJ04160001` | 10 — 국가 없음 | KRX(`J`) | 5/초(공지 상한 18 을 넘지 않는다) | 없음 → 저장된 `stocks` | 미검증 |
| `ls` | `LS_APP_KEY`·`LS_APP_SECRET` | `t1702` | 11 | KRX(`K`) | 1/초(t1702 개인 상한 — 넘지 않는다) | `t8436`(업종 없음 → 저장된 섹터) | 미검증 |
| `toss` | `TOSS_CLIENT_ID`·`TOSS_CLIENT_SECRET` | `investor-trading` + 원 종가 일봉 `candles` | 10 — 국가 없음, 외국인은 등록외국인만 | **KRX+NXT 통합** | 5/초(그룹 상한 10 을 넘지 않는다) | `stocks/all`(보통주, 토스 섹터는 쓰지 않는다) | 미검증 |

- `kis`: 실전 앱키 전용(이 TR 은 모의투자 미지원 — 시작할 때 경고). 한 응답의 행 수가 미확인이라 응답 헤더 `tr_cont`(M·F) 연속조회와
  날짜 옮기기 두 길로 잇는다. 등락률은 키움 `flu_rt` 와 단위가 달라 싣지 않는다. 종목 목록 API 가 없어 먼저
  `sector-flow-collect universe --db data/byo.db --broker kiwoom`(키움 키가 있으면) 또는
  `sector-flow-collect sectors --db data/byo.db --csv krx.csv --add-missing`(KRX '업종분류 현황' 을 각자 내려받은 CSV)으로 목록을 넣는다.
- `ls`: 시작할 때 약관 제9조① "고객이 3개월간 상품별 구분 없이 OPEN API를 통한 주문체결이 20회 이하인 경우"(해지 사유)를 찍고,
  `--i-understand-ls-terms` 가 없으면 요청 전에 멈춘다(sqlite 도 만들지 않는다). 토큰은 폼(`appkey`·`appsecretkey`·`scope=oob`)으로 받는다.
  외국인은 외인계(`tjj0016`, 등록+미등록), 국가는 국가외(`tjj0011`). 연속조회 방식이 미확인이라 응답 머리 `tr_cont: Y`(+`tr_cont_key`)와
  `todt` 옮기기 두 길로 잇는다. 섹터가 없어 키움 키로 받은 업종이나 `sectors --csv` 의 표를 둔다.
- `toss`: 수급이 KRX+NXT 통합뿐이라 KRX 단독(나머지 어댑터·리포트)과 같은 날 숫자가 다르다. 그래서 KRX 단독 행이 든 sqlite 에는 받아
  넣지 않고(수집 전에 거부 — 다른 `--db` 를 준다), 지도 머리 표지가 `자료 범위: KRX+NXT 통합` 으로 바뀐다(`basis.DATA_BASIS_INTEGRATED`).
  매매동향에 종가가 없어 원 종가 일봉(`adjusted=false`)을 날짜로 붙인다 — 종목당 요청이 둘이다. 당일 잠정 기록의 개인·기관 세부·기타법인은
  null(NULL 로 둔다). 섹터는 자체 분류·다중 소속이라 쓰지 않는다. 허용 IP 등록이 필요하다.

---

## 7. 출처

키움
- 공식 명세·라이선스: https://github.com/Kiwoom-Securities/Kiwoom-REST-API (`kiwoom/_data/kiwoom_api_spec.json`, `LICENSE.md`)
- 서비스 안내: https://openapi.kiwoom.com/intro/serviceInfo
- API 가이드(TR 호출 한도 칸): https://openapi.kiwoom.com/guide/apiguide
- 약관(2026-06-01): https://download.kiwoom.com/deploy/AG001/pdf/AG001_110_20260601.pdf (목록 https://www.kiwoom.com/h/customer/financeguide/VClauseListView)
- 보조: https://algolab.co.kr/blog/kiwoom-rest-api-investor-ka10059-2026

한국투자
- 포털: https://apiportal.koreainvestment.com/apiservice
- API 메타(종목별 투자자매매동향(일별)): https://apiportal.koreainvestment.com/api/apis/public/141f0f11-ef30-4e52-9388-a6e78502d5c2
- API 메타(주식현재가 투자자): https://apiportal.koreainvestment.com/api/apis/public/e27baf2f-6ec0-4029-b4fd-4c873f340478
- 약관: https://apiportal.koreainvestment.com/api/terms/public?termsType=MARKET
- 공식 GitHub: https://github.com/koreainvestment/open-trading-api (`examples_llm/domestic_stock/investor_trade_by_stock_daily`, `inquire_investor`, `stocks_info/`)
- 구형 eFriend Expert 안내: https://securities.koreainvestment.com/main/customer/systemdown/OpenAPI.jsp
- 보조(유량 수치): https://algolab.co.kr/blog/kis-egw00201-rate-limit-fix-2026
- python-kis: https://github.com/Soju06/python-kis

LS증권
- 포털: https://openapi.ls-sec.co.kr/intro
- API 메타(외인/기관, 상한): https://openapi.ls-sec.co.kr/api/apis/public/90378c39-f93e-4f95-9670-f76e5c924cc6
- 약관: https://www.ls-sec.co.kr/guide/agreement/INAgreement.jsp?content_no=3643
- 필드 이름표(2차): https://github.com/programgarden/programgarden (programgarden 저장소의 t1702 blocks.py), https://github.com/xorrhks0216/LsApiHelper (`specs/`)

토스증권
- https://developers.tossinvest.com/llms.txt
- https://openapi.tossinvest.com/openapi-docs/latest/openapi.json
- https://openapi.tossinvest.com/openapi-docs/overview.md
- https://openapi.tossinvest.com/openapi-docs/faq.md
- 출시 보도: https://economist.co.kr/article/view/ecn202608130015

DB증권
- 포털 API 목록: https://openapi.dbsec.co.kr/apiservice
- API 메타(일별종목별투자자조회): https://openapi.dbsec.co.kr/api/apis/public/2a750d3d-5ea2-4bb6-b3d6-411956d42e75
- 보도: https://www.smarttoday.co.kr/ko-kr/articles/111303
- pydbsec: https://github.com/Jaeminyx-Stoa/pydbsec

NH투자증권
- https://www.nhplug.com/llms.txt
- https://www.nhplug.com/openapi-docs/krstock/openapi.json
- https://github.com/PLUG-OpenAPI/nhplug-sdk
- 공식 블로그: https://www.mynamuhbegin.com/blog/?p=716
- 보도: https://www.smarttoday.co.kr/ko-kr/articles/110284

대신증권
- CYBOS Plus 도움말 미러: https://github.com/cybosplus/cybosplus.github.com (`cpsysdib_rtf_1_/cpsvr7254.htm`, `cputil_rtf_1_/cpcodemgr.htm`)
- 대신 Q&A: https://money2.daishin.com/e5/mboard/ptype_basic/Basic_018/DW_Basic_Read_Page.aspx?boardseq=60&seq=22741&ismy=0&page=1&searchString=&p=8827&v=8636&m=9508
- 보조: https://freshrimpsushi.github.io/en/posts/2172 , https://pkg.go.dev/github.com/hspan/creon

그 밖
- 유안타: https://www.myasset.com/myasset/trading/apiSvc/TR_1604001_P1.cmd
- KB: https://www.mt.co.kr/stock/2026/08/04/2026080414364184356 , https://algolab.co.kr/blog/kb-securities-open-api-personal-2026 , https://blog.kbsec.com/about/kb%EC%A6%9D%EA%B6%8C-open-api-%EC%82%AC%EC%9A%A9%EB%B2%95/
- 신한(블로그): https://wikidocs.net/book/2167
- 미래에셋: https://cse.snu.ac.kr/community/notice/25298
