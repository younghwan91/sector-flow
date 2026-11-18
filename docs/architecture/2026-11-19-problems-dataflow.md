# 리포트 한 번 짓기 데이터 흐름 — 추적 중 찾은 문제

기준: `67fa2a5` (main). 코드는 읽기만 했고 DB 는 열지 않았다. 실데이터 근거는 `/home/young/git/sector-flow/reports/2026-11-10 … 2026-11-18` 의 이미 게시된 파일(`payload.json`·`digest.json`·`digest.md`·`cron.log`)을 읽어 확인했다.
그림: `docs/architecture/report-build-dataflow.html`.

| # | 심각도 | 한 줄 |
|---|---|---|
| P1 | MAJOR | 1년 %ile 극단(≥95·≤5)을 가르는 규칙이 둘이라, 같은 리포트 안에서 목록이 서로 다르다 |
| P2 | MAJOR | digest·trust.html 은 검증이 끝난 뒤에 지어져 검증기를 한 번도 거치지 않는다 |
| P3 | MAJOR | 알림 통로가 비어 있다 — 검증 실패·수집 미완·확정 미확인이 로그에만 남는다 |
| P4 | MINOR | 잠정·확정 빌드에서 sector_flow.py 가 관문을 통과한 기준일에 묶이지 않는다 |
| P5 | MINOR | 섹터 시총이 없으면 뷰어는 가속을 0 으로, 수치표는 결측으로 그린다 |
| P6 | MINOR | 가장 큰 주체를 고르는 동률 규칙이 digest 와 summary·TUI 에서 다르다 |
| P7 | MINOR | 섹터·시장 분류와 유니버스는 지금의 `stocks` 를 쓴다 — 재구성에서 앞날을 읽는다(문서화돼 있음) |
| P8 | MINOR | `iret` 는 어떤 화면도 읽지 않는데 계산·내장되고, 검증 실패 사유가 될 수 있다. 주석은 반대로 말한다 |
| P9 | MINOR | payload 의 55.6% 가 `names` 이고, 같은 블롭이 폴더 안에 세 벌 있다(주석은 "≈200KB") |
| P10 | MINOR(잠재) | '얇은 섹터' 판정이 두 가지 종목 수로 갈린다 — 지금은 0건, 옛 결함이 다시 날 수 있는 구조 |

확인했지만 문제가 없던 것: 수량→억 환산(`sector_flow.py:110-114`), `daily_bars.trade_value` 백만원 → ×1e6(`sector_flow.py:1761`, 단위 띠는 `verify_report.py:755-786` 이 본다), 시총의 원 종가 × as-of 주식수(`storage.py:372-466`), 모든 DB 읽기의 `≤ 기준일` 묶음(재구성). 시장 합계는 `_market_daily`(종목에서 바로, `sector_flow.py:528-554`)와 `streak_block`(섹터 배열 합, `sector_flow.py:676-705`) 두 길로 세지만 차이는 반올림(최대 약 0.14억)뿐이다. 화면이 payload 가 보장하지 않는 필드를 읽는 경우는 P5·P8 말고 찾지 못했다(모든 선택 필드는 `|| {}`·`.get` 으로 막혀 있다).

---

## P1 · MAJOR — 극단 문턱 규칙이 둘이다(날값 대 보이는 정수)

**근거**
- 날값(소수 1자리 `pct`) `≥ 95` 로 비교하는 곳:
  - 웹 수치표 '오늘 1년%ile 극단' 카드 `scripts/templates/sector_numbers.html:867-868` (`t[2]>=PCT_HI`)
  - TUI 배너 `src/sectorflow/tui/flow_view.py:1093`, `1322-1340` (`p >= PCT_HI`, `PCT_HI=95.0`)
  - 잠정→확정 '들고 남' `scripts/sector_flow.py:1246-1247`, `1252-1271` (`_extremes`, `PCT_HI=95.0`)
- 보이는 정수(`rhu(p)`) `≥ 95` 로 비교하는 곳:
  - `today.day_change.extreme`(며칠째 표지) `scripts/sector_flow.py:713-714`, `722-742` (`DAY_PCT_HI=95`, `_visible_pct`)
  - 요약 문장 `src/sectorflow/summary.py:55-64` (`band`·`is_extreme`)
  - 다이제스트 `scripts/digest.py:162-166` (`pct_int(p) >= PCT_HI`)
- 웹과 TUI 목록끼리만 대조한다: `scripts/verify_report.py:4060-4065`. 둘 다 날값 규칙이라 통과한다.

**실제로 일어났다.** 2026-11-18 게시본에서 `today.sectors.전체.출판/매체복제.inst.pct = 94.8`.
`day_change.extreme.전체.inst.hi` 에는 `출판/매체복제: 1` 이 있고, `digest.md:30` 은 "기관 상단: 오락/문화 98·신규 · 출판/매체복제 95·신규" 라고 적는다. 같은 폴더의 numbers.html 과 TUI 의 '≥95' 목록은 날값 94.8 < 95 라서 오락/문화만 싣는다. 11-10~11-18 일곱 게시본 중 여섯에서 이 경계(94.5≤p<95 또는 5<p<5.5)에 든 칸이 날마다 2~4개 있었다.

**실패 장면.** 같은 기준일에 웹·TUI 는 "기관 ≥95: 오락/문화" 라고 하고, 다이제스트·요약 줄은 출판/매체복제까지 넣는다. 화면은 그 섹터를 "95" 로 적는데 '≥95' 목록에서는 빼므로, 읽는 사람은 목록이 틀렸다고 본다. 잠정→확정 '들어옴/나감'도 날값이라, `day_change` 의 며칠째 표지와 같은 섹터를 두고 서로 다른 말을 할 수 있다.

**고칠 방향.** 판정 함수를 하나 두고 모두 그것을 쓴다(`sectorflow.basis` 에 `is_hi(p)`·`is_lo(p)` = 보이는 정수 기준, JS 사본 한 곳). 그런 다음 `flow_view.today_extremes`·`sector_numbers.html` t-ext·`_extremes` 가 그 함수를 부르게 바꾼다. 검사로는 W/T 층에 "≥95 목록의 섹터 집합 = `day_change.extreme[...]['hi']` 의 키" 를 넣고, `tests/mutations.toml` 에 경계값 변이를 더한다.

## P2 · MAJOR — digest·trust 는 검증 뒤에 지어지고 아무도 검사하지 않는다

**근거**
- 순서: `verify_report.py` 가 `scripts/daily_report.sh:328-340` 에서 돌고, 그 뒤에 digest(`:354`)와 trust_page(`:399-403`)가 돈다.
- `scripts/verify_report.py` 에는 `digest` 라는 글자가 없다. 층 목록은 `:5158-5169`(A·A3·B·C·D·E·G·H·T·W·F)뿐이다.
- digest 는 값을 옮기기만 하는 게 아니다. 순위(`digest.py:121-126`), 가장 큰 주체(`:133-139`), 극단 문턱(`:162-166`), 상·하위 고르기(`:171-173`)를 따로 한 번 더 정한다.

**실패 장면.** P1 의 어긋남이 2026-11-18 `digest.md` 에 그대로 실렸고, VERIFY.txt 는 "전부 통과" 였다. digest.py 가 바뀌어 순위·부호·반올림이 틀어져도 `/latest/digest.md`·`digest.png` 는 검증 통과 폴더의 일부로 나간다. digest 가 `VERIFY_FAILED` 일 때 만들기를 거부하는 장치(`digest.py:762-763`)는 리포트 숫자를 지키는 것이지 digest 자체를 지키지 않는다.

**고칠 방향.** digest 를 verify 앞으로 옮기고(검증 실패면 지우는 것은 그대로 둔다), verify 에 `digest.json` 의 사실을 payload 와 `sectorflow.summary` 로 다시 세어 맞추는 층(예: `X`)을 넣는다. 또는 digest 뒤에 그 층만 다시 돌린다. digest 가 다시 정하는 규칙은 summary/basis 의 함수를 import 하게 바꿔 두 벌을 없앤다. 새 검사에는 변이도 같이 넣는다.

## P3 · MAJOR — 알림 통로가 비어 있다(운영 단일 실패점)

**근거**
- `reports/cron.log:4224-4225`(2026-11-18 20:30)에 "ALERT_WEBHOOK_URL 이 비었다 — 경고·오류가 로그에만 남고 아무에게도 안 간다", "HEARTBEAT_URL 없음 — 외부 감시가 꺼져 있다" 가 찍혔다. 같은 경고가 로그에 8번 있고, 첫 번째는 `:3730` 이다.
- 이상 종료를 알리는 길은 `scripts/cron_run.sh:17` → quant-airflow `alert.sh` 하나뿐이다.
- 검증에 걸려도 게시하고 `latest` 를 옮기며(`daily_report.sh:434-438`) 크론에는 1 을 돌려준다(`:457-458`). 이 설계는 알림이 사람에게 닿는다는 것을 전제로 한다.

**실패 장면.** VERIFY_FAILED 리포트가 latest 가 되거나, 08:20·10:20 이 모두 확정을 확인하지 못해(75) 그 기준일이 영영 잠정으로 남거나, 관문이 지난 날을 막아도(1) 아무도 모른다. 웹 띠와 TUI ⚠ 는 사람이 열어 봐야만 보인다.

**고칠 방향.** `~/git/quant-airflow/.env` 에 `ALERT_WEBHOOK_URL=`(그리고 `HEARTBEAT_URL=`)을 채운다. 이것은 사용자가 할 일이다. 코드 쪽에서는 `freshness_check.py:416` 의 WARN 이 이미 있다. 웹훅이 빈 날이 N일 이어지면 웹 첫 화면 띠에도 띄우는 것을 생각해 볼 만하다.

## P4 · MINOR — 평소 빌드는 sector_flow.py 를 관문을 통과한 기준일에 묶지 않는다

**근거**
- 셸이 `ASOF = max(date)` 를 읽고(`scripts/daily_report.sh:104-116`) 관문은 그 날을 본다(`:257`).
- `--asof` 는 재구성일 때만 넘긴다(`:315-321`). 평소 빌드의 `load()` 는 `recent_dates(con, days)` 로 최신일을 **다시** 고른다(`scripts/sector_flow.py:84`).
- 검증 B 층도 재구성이 아니면 "payload 기준일 = DB 최신일" 만 본다(`scripts/verify_report.py:729-732`). 폴더 이름(ASOF)과 맞는지는 보지 않는다.

**실패 장면.** 셸이 ASOF 를 읽은 뒤 sector_flow.py 가 읽기 전에 새 거래일의 첫 행이 들어오면(예: 16:00 수집 시작 무렵에 손으로 `KR_QUANT_FORCE=1` 을 돌린 경우), 수집 중인 새 날의 부분 데이터가 **어제 폴더**에 어제 이름으로 들어간다. 그날 데이터는 관문을 거치지 않았는데 검증은 통과한다. 창은 좁다. 크론 시각(18:10·19:10·08:20·10:20)에는 날짜가 넘어가지 않는다.

**고칠 방향.** 언제나 `sector_flow.py --asof "$ASOF"` 와 `verify_report.py --asof "$ASOF"` 를 넘긴다. `load` 는 이미 `dates[-1] != asof` 면 멈춘다(`sector_flow.py:88-89`). 확정 의미를 붙이는 `--rebuilt`·`--final` 은 지금처럼 따로 둔다.

## P5 · MINOR — 시총이 없는 섹터: 뷰어는 0, 수치표는 결측

**근거**
- 뷰어: `scripts/templates/sector_flow.html:409-411` `windowVal` 과 `:429` `accelOf` 는 `c ? t/c*100 : 0` 이다.
- 수치표: `scripts/sector_numbers.py:169-170` 은 "시총이 없으면 가속도는 **결측**이다 — 0.0 은 '유입이 없었다' 로 읽힌다" 라고 적고 `None` 을 낸다.
- 검증기의 뷰어 기대값도 0 으로 폴백한다(`scripts/verify_report.py:3767-3772`). 그래서 이 차이를 못 잡는다.
- 시총 커버리지 검사는 섹터 수 이상이면 통과한다(`verify_report.py:673-675`, 54칸 중 27칸).

**실패 장면.** 종목이 있는 (시장, 섹터)인데 그날 주식수 스냅샷이나 원 종가가 없어 `cap` 이 빠지면, 뷰어의 '가속 %p' 랭킹에 그 섹터가 `+0.00%p` 로 끼어 "조용한 섹터" 로 읽힌다. 같은 칸을 수치표는 `—` 로 맨 뒤에 둔다.

**고칠 방향.** 뷰어에서 `null` 을 내고 `—`·정렬 맨 뒤로 보낸다. 검증기의 기대값도 `None` 으로 바꾸고, W 층에 시총 결측 합성 칸 사례와 변이를 넣는다.

## P6 · MINOR — '가장 큰 주체' 동률 규칙이 두 가지다

**근거**
- `src/sectorflow/summary.py:100-103` 과 `src/sectorflow/tui/flow_view.py:1551-1556` 은 `max((|v|, key))` 를 쓴다. 동률이면 키 문자열이 큰 쪽이다.
- `scripts/digest.py:133-139` 는 `(|v|, -index)` 를 쓴다. 동률이면 `NET_ACTORS` 에서 앞선 쪽이다.
- digest 는 두 결과를 한 글에 같이 싣는다. 요약 문장은 `summary_items`(`digest.py:248`), 주체 줄과 연속 줄은 자기 `dom`(`:154-158`, `:240-246`)에서 온다.

**실패 장면.** 시장 4주체 중 둘의 |순매수| 가 0.01억 단위까지 같으면 한 다이제스트 안에서 "가장 큰 주체" 가 둘로 갈린다. 드물다.

**고칠 방향.** digest 가 `summary.dominant` 를 import 해서 쓴다.

## P7 · MINOR — 분류·유니버스는 지금의 `stocks`(재구성의 앞날 읽기)

**근거** `scripts/sector_flow.py:101-102`(`SELECT code, name, sector, market FROM stocks` 와 inner join), `scripts/completeness_gate.py:72`. 문서화돼 있다: `sector_flow.py:78-83`, `daily_report.sh:391`(REBUILT.txt 의 "묶지 못한 것").

**실패 장면.** 지난 기준일을 재구성하면 그 뒤에 마스터에서 빠진 종목(폐지 등)의 수급이 섹터 합계·종목 목록에서 사라진다(생존 편향). 그 뒤의 섹터 재분류도 소급해서 적용된다. 재구성 리포트와 그날 저녁 게시본의 섹터 합계가 데이터 수정이 아니라 분류 때문에 달라진다.

**고칠 방향.** 단기: 재구성할 때 `.archive/` 의 원본 payload `names[code].sector/market` 이 있으면 그것을 우선한다. 장기: quant-airflow 에 `stocks` 이력(SCD)을 두고 as-of 로 읽는다.

## P8 · MINOR — 화면이 읽지 않는 `iret`, 반대로 말하는 주석

**근거**
- `iret` 는 `index_returns` 가 짓고(`scripts/sector_flow.py:1957-1961`) payload 에 실린다(`:401`). 2026-11-18 기준 94.8KB, 2.7% 다.
- 읽는 곳은 검증기(`scripts/verify_report.py:674`, `:834`)와 BYO 대조(`src/sectorflow/collect/equiv.py:362`)뿐이다. 템플릿·TUI·digest·summary 에는 한 번도 나오지 않는다.
- 주석 `sector_flow.py:373-374` 는 "화면의 수익률은 KRX 업종지수를 쓴다 — 아래 값은 … 폴백" 이라고 적는다. 실제로는 화면이 자체 바구니 `ret`/`retw` 를 쓴다(`sector_numbers.py:147-148`, `sector_flow.html:412-427`).
- `today.min_live`(`sector_flow.py:620`)도 읽는 곳이 없다.

**실패 장면.** 업종지수 수집이 비면 `chk("B", "업종지수 커버리지", idx_n >= 60%)`(`verify_report.py:676`)가 리포트를 VERIFY_FAILED 로 만든다. 그러면 어떤 화면도 쓰지 않는 값 때문에 두 HTML 에 ⚠ 띠가 붙고 digest 도 안 만들어진다. 주석을 믿고 `iret` 를 고치는 사람은 화면이 바뀌리라 기대하지만 바뀌지 않는다.

**고칠 방향.** 주석을 고친다. `iret` 커버리지 검사는 경고(`warn`)로 낮추거나, `iret` 를 viewer 내장에서 빼고 검증 전용 사이드카로 옮긴다.

## P9 · MINOR — payload 크기: `names` 가 절반이 넘고 세 벌 실린다

**근거**
- 2026-11-18 `payload.json` 은 3,493,303B 이고, 그중 `names` 가 1,941,226B(55.6%)다(2,646종목 × 5창 × 8값, 키가 반복된다).
- 주석은 "≈ 200KB"·"크기는 미미하다" 라고 적는다(`scripts/sector_flow.py:325-327`, `:349`).
- 같은 블롭이 `numbers.html` 에 다시 들어가고(`scripts/sector_numbers.py:300`, 파일 2.6MB), `viewer.html` 은 payload 를 통째로 넣는다(`sector_flow.py:1949`, 3.6MB). 폴더 하나에 같은 종목 데이터가 세 벌 있다.

**실패 장면.** 창이나 값 하나를 더할 때마다 세 파일이 같이 커진다. TUI(`flow_app.py:59`)와 검증기(`verify_report.py:5146`)는 2.6MB HTML 을 정규식으로 잘라 파싱하고, revision_stats 는 `final_diff.json` 이 없으면 3MB payload 를 연다(`sector_flow.py:1461`). 웹은 gzip 이라 전송은 견딜 만하다.

**고칠 방향.** 주석을 실측으로 고친다. `names.win` 을 열 지향 배열(창별 `[codes], [inst], …`)로 바꾸면 키 반복이 빠진다. 또는 `names.json` 하나로 두고 두 HTML 이 같이 읽게 한다. 다만 `perf` 변경이라 리포트가 바이트 동일한지 확인해야 한다.

## P10 · MINOR(잠재) — '얇은 섹터' 종목 수가 두 가지

**근거**
- 260일 동안 한 번이라도 거래된 종목 수 `n_by_sector` 로 자르는 곳: `streak_block`·`day_change_block`·`concentration_block`(`scripts/sector_flow.py:646-647`, `697`, `770`, `806`), 요약 칸 수(`src/sectorflow/summary.py:106-111`).
- 그 창에 거래된 종목 수로 자르는 곳: 표·드릴다운(`scripts/sector_numbers.py:269-291`, TUI 1일 표 `flow_view.py:499-506`). `sector_numbers.py:272-282` 는 n_by_sector 가 "죽은 이름" 을 세서 얇은 섹터 경고를 지웠던 사고(2026-08-28, 49건)를 적어 두고 있다.

**지금 상태.** 11-10~11-18 게시본에서 두 수가 10 경계를 사이에 두고 갈린 (시장, 섹터, 창)은 0건이었다.

**실패 장면.** 수급 보고가 끊긴 종목이 `stocks` 에 남는 날이 다시 오면, 표에서는 `~`(얇음)인 섹터가 연속·부호 바뀜·집중 줄과 요약 칸 수에는 들어간다.

**고칠 방향.** 하루 단위 블록(연속·어제 대비·집중·요약)은 `names[code].win["1"]` 이 있는 종목 수로 센다. 또는 payload 에 `n_live_by_sector` 를 싣고 모두 그것을 쓴다. T 층에서 두 수를 대조한다.
