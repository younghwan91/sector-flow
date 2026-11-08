# sw-flow · sw-ledger 화면 값의 수식 정의
<!-- refs-verified-at: f8aa763d4079ac070e900d4fda1aabbf8c2356cf -->

두 터미널 화면(`sw-flow`·`sw-ledger`)에 뜨는 모든 값의 정의를 코드 위치와 함께 모은 **정본**이다. 식마다 그것을
계산하는 `파일:줄` 이 붙어 있고, 지표마다 **근거 수준**(앞날 수익률에 대해 검증된 것이 있나)을 적었다.

- 줄 번호는 맨 위 `refs-verified-at` 커밋의 코드 기준이다. `tests/test_docs_refs.py` 가 모든 `파일:줄` 이 있는지, 인용 바로
  앞(`` `기호` `파일:줄` ``)이나 바로 뒤(`` `파일:줄`(`기호`) ``)에 적힌 기호가 그 줄 ±3 안에 있는지 CI 에서 본다. 코드가 움직였으면
  `uv run python scripts/docs_refs.py --fix` — 그 커밋과 지금 코드를 diff 해 옮기고 표시를 갱신한다. 새 인용은 기호를 같이 적는다.
- 대상 소스 — `scripts/sector_flow.py` · `scripts/sector_numbers.py` · `src/sectorflow/storage.py` ·
  `src/sectorflow/tui/flow_view.py` · `src/sectorflow/tui/ledger_view.py` · `scripts/daily_report.sh`.
- 옛 PDF 판(2026-08-31, `docs/kqflow-formulas.pdf`)은 2026-11-08 지웠다(git 이력에 있다). 그 판 대비 바뀐 자리는
  [부록 A](#부록-a-2026-08-31-pdf-대비-바뀐-것).
- 화면의 `?` 도움말이 열마다 한 줄 정의를 싣는다. 이 문서는 그 뒤의 식과 결측 규칙과 근거다.

## 근거 수준 — 먼저 읽을 것

모든 값은 **동시기(同時期) 측정값**이다. 같은 구간의 유입과 같은 구간의 수익률을 맞댄다. 앞날 수익률에 대한 검정은
[2026-11-08 지표 검증](validation/2026-11-08-results.md)(Q3d 재검)과 [수급 쓸모](validation/2026-11-08-usefulness-results.md)
두 번 있었고, 결과는 이렇다.

| 지표 | 근거 수준 | 출처 |
|---|---|---|
| 임펄스 · 가속 (섹터) | 원자료와 맞고 잠정치에도 안정(가속 순위 게시본↔확정치 ρ 0.999). **앞날 관계 증거 없음**(20일, h5·h20) | 결과 Q1·Q3a·Q3d 재검 |
| 섹터 수급(투신+연기금·외국인·기관 60일) | 발견 구간에서 전부 탈락 — **앞날 증거 없음** | 쓸모 S1~S3 |
| 수익률 Δv | 정의를 고쳤다(`flu_rt` → 조정 종가 비, §1.3) | 결과 결함 2 |
| k (강성) | 추정치. 20일·전체에서 \|t\|<2 가 42~47%, 코스닥 60~70% | 결과 Q3c |
| 미실현 x | **사실상 섹터 20일 반전(−Δv)** 이다 — x↔−Δv 순위상관 0.82~0.93. h20 의 약한 신호도 수급이 아니라 −ret20 이 낸다(다중검정 미달) | 결과 Q3c·Q3d 재검 |
| 포텐셜 U · 풀림 ẍ · dW/dt · 1년%ile · 추이 | 따로 검정된 적 없다. U 는 \|x\| 의 단조 변환이고 ẍ 는 G 의 입력이다 | — |
| 선정 G (섹터) | **판정 불가**(검정력 없음 — 관문 통과 5섹터 이상인 날이 h5 82일·h20 19일뿐). 잠정치에 흔들려 08:20 확정 모드를 만들었다 | 결과 Q3a·Q3d 재검 |
| 종목 투신+연기금 | 종목 20일 투신+연기금 ÷ 시총의 **섹터 안 순위 IC 는 음으로 확인**됐다(t −6). 쓸 수 있는 형태로는 못 보였다 — 정보가 매도 쪽에 몰리고 절반가량은 단기 반전이다 | 쓸모 K1 |
| 종목 선정 · 곱 | 검정된 적 없다. 선정의 `rank(투신+연기금)` 축은 큰 순매수를 좋게 보는데, K1 의 섹터 안 IC 부호는 그 반대다 | — |
| 원장(잔여·몫·동시성) | 회계 항등식과 실측 비율. 예측을 주장하지 않는다. 동시성의 널 대조는 리포트를 열 때 잰다 | §4 |

앞날 관계의 기각·미확인은 측정값으로서의 유효성과 별개다. 이 화면은 "지금 누가 무엇을 사고파나" 를 보여주는 관측으로
쓴다. 알파 심사는 swing-it 소관이다.

---

## 0. 이 문서가 다루는 것

### 0.1 파이프라인

화면의 숫자는 전부 리포트 폴더 하나에서 나온다. 화면이 DB 를 읽는 경로는 없다.

| 단계 | 하는 일 | 산출 |
|---|---|---|
| `scripts/sector_flow.py` | DB(수급·종목마스터·조정 종가·시총)를 읽어 일별 (시장, 섹터, 주체) 순매수 금액과 종목별 프리셋 구간 집계를 만든다 | `payload.json` |
| `scripts/sector_numbers.py` | 페이로드를 4구간 × 시장으로 집계해 블록을 만들고 블록마다 횡단면 회귀·파생량·선정(G)을 낸다 | `numbers.html` 안의 JSON |
| `sw-flow` (`flow_view.py`) | `numbers.html` 의 JSON 을 읽어 섹터 표·종목 목록·전 종목 화면을 그린다. 주체 투영·종목 파생량·종목 선정은 여기서 낸다 | 터미널 |
| `sw-ledger` (`ledger_view.py`) | `payload.json` 을 직접 읽는다(`sector_numbers` 를 거치지 않는다). 원장·전개·동시성·한계 | 터미널 |

출처 — `scripts/sector_numbers.py:283`(`build`), `src/sectorflow/tui/ledger_view.py:121`(`load`), `scripts/daily_report.sh:186-188`.
두 앱은 같은 폴더를 본다. 같은 (시장, 섹터, 구간)에 대해 두 화면이 다른 수를 말하면 정의가 갈린 것이지 데이터가 다른 것이 아니다.

### 0.2 기호

| 기호 | 뜻 | 단위 · 비고 |
|---|---|---|
| $c$ | 종목(code) | |
| $s$ | 섹터 — 벤더 분류 `stocks.sector`. KRX 업종 분류가 아니다 | 빈 값은 `(미분류)` |
| $m$, $M$ | 시장(거래소·코스닥), 화면에서 고른 시장 집합. `전체` 는 둘의 합 | |
| $A$ | 주체 — 개인 · 외국인 · 기관 · 기타법인 | |
| $i$, $N$ | 거래일 인덱스, 페이로드 길이. $i=N-1$ 이 기준일 | |
| $W$ | 구간 길이(거래일). sw-flow 5·20·60·120 / sw-ledger 5·20·60·120·260 | |
| $f^A_{m,s,i}$ | 일별 순매수 금액 | 억원 |
| $F^A_s$ | 구간 누적 순매수(임펄스) | 억원 |
| $C_s$ | 섹터 시가총액, **페이로드 기준일** 시점 | 억원 |
| $a^A_s$ | 가속 $=F^A_s/C_s\times100$ | %p |
| $\Delta v_s$ | 섹터 구간 수익률 | % |
| $k, b, t$ | 횡단면 회귀의 기울기·절편·t값. 블록마다 다시 적합한다 | |
| $x_s$ | 미실현 변위 | %p |
| $U_s$ | 포텐셜 $\tfrac12 k x^2$ | 무차원 취급 |
| $\dot x, \ddot x$ | 풀림 속도·가속 | %p/일, %p/일² |
| $G_s$ | 선정 점수 | 0~1 |

### 0.3 결측 규약

두 앱이 공유하는 규칙: **뜻을 잃은 값은 고쳐 쓰지 않고 결측(`—`)으로 둔다. 결측은 정렬에서 어느 방향으로도 맨 뒤다** —
값이 없는 것은 작은 값이 아니다. 구현 — `src/sectorflow/tui/flow_view.py:635-643`(섹터 정렬), `src/sectorflow/tui/flow_view.py:725-733`(종목 정렬),
`src/sectorflow/tui/ledger_view.py:637`(원장 정렬), `src/sectorflow/tui/ledger_view.py:717-718`(동시성 nan 순서).

적용되는 자리: 포텐셜의 $k\le0$ 블록(§2.9), 수익률이 없는 종목·날(§1.3), 최근집중의 작은 분모·작은 몫(§3.6), 잔여몫·최대일몫의
작은 분모(§4.3·§4.4), 상관의 분산 0 쌍(§4.7), 누적 기여율의 비-순매수 정렬(§3.3), 관문을 못 넘은 선정(§2.12·§3.9).

---

## 1. 원자료와 단위

### 1.0 자료 범위: KRX/NXT

**모든 원자료가 KRX 단독이다 — 넥스트레이드(NXT, 2025-03-04 개장) 체결은 어느 열에도 없다.** 시기에 따라 바뀐 것은
거래소가 아니라 세션이다(2026-09-14~ KRX 애프터마켓 16:00~20:00 포함, §1.3). 화면 표지는 `sectorflow.basis.DATA_BASIS`.

| 열 | 키움 TR(quant-airflow) | 범위 |
|---|---|---|
| `supply_demand` 4주체·기관세부 수량, `close`, `acc_trde_qty` | ka10059 `stk_cd`=6자리, `amt_qty_tp=2` (`collectors/combined.py`) | KRX. `close`·`acc_trde_qty` 는 09-14~ 애프터마켓 포함 |
| `daily_bars` `close`·`volume`·`trade_value`, `daily_bars_adjusted` | ka10081 `stk_cd`=6자리, `upd_stkpc_tp=1` (`collectors/daily_bars.py`) | KRX. 09-14~ 고가·저가·종가·거래량·대금은 애프터마켓 포함, 시가만 정규장 |
| `sector_index` | ka20006 `inds_cd` (`collectors/sector_index.py`) | KRX 지수. 종가는 정규장 |

근거(2026-11-08, 읽기 전용):

- **코드.** 어느 수집기도 거래소 접미사를 붙이지 않는다. 키움 REST 는 같은 TR 에서 KRX `005930` · NXT `005930_NX` ·
  통합(SOR) `005930_AL` 로 거래소를 고른다 — scalp-it `leader_scan` 이 ka10027 을 `stex_tp=3`(통합)으로 부르면 응답 코드가
  `043200_AL` 로 온다. 거래소구분 인자(`stex_tp`·주문의 `dmst_stex_tp`)는 순위·조건검색·주문 TR 의 것이고, 이 표의 TR 은 코드로만 고른다.
- **총액 대조.** Σ`daily_bars.trade_value`(시장별, `source='kiwoom'`) ÷ `sector_index` 종합지수(001·101) 거래대금의 월평균이
  2024-09~2026-11 내내 평평하다 — 코스닥 0.994~1.000, 거래소 0.960~0.975. NXT 가 섞였다면 2025-03(개장 뒤 한 달에 걸쳐 종목 확대) 뒤
  1 위로 계단이 생겨야 한다. 없다.
  지수 거래대금도 KRX 애프터마켓을 포함하는 것으로 보인다 — 2026-08-17~09-11 → 09-14~11-06 일평균 비가 코스닥 0.9991 → 0.9999,
  거래소 0.9631 → 0.9591 로 오르지 않았는데, 애프터마켓(16:00~) 몫은 분봉 거래대금의 3.5%(10-26~11-05, 2,538종목)라 지수가 빼고 있었다면 비가 그만큼 올랐어야 한다.
- **세션.** 005930 분봉(ka10080, 같은 코드)은 2025-08~2026-09-11 동안 09:00 전·15:36 뒤 봉이 0개다(2025-11-13 수능일 1시간 지연분 제외).
  NXT 프리마켓 08:00~08:50·애프터마켓 15:40~20:00 은 그때도 열려 있었다. 09-14 뒤 생긴 봉은 16:00~ 로 KRX 애프터마켓이다.
- **같은 응답.** `supply_demand.acc_trde_qty`·`close` = `daily_bars_raw.volume`·`close` 가 2024-12~2026-10 종목-일 100% 일치.
  4주체 수량도 같은 ka10059 행이라 범위가 같다(수량 자체를 KRX 공표치와 대조하지는 못했다).

그래서 거래량·순매수는 **시장 전체가 아니라 KRX 몫**이다. NXT 거래가 많은 대형주일수록 실제 손바뀜보다 작게 잡힌다.

### 1.1 순매매 금액 — 종가 환산 근사

DB(`supply_demand`)는 주체별 순매매 **수량**만 준다. 금액은 그날 종가를 곱해 복원한다.

**(1)** $\quad f^A_{c,i} = q^A_{c,i}\times|P_{c,i}|\,/\,10^8$ [억원]

`scripts/sector_flow.py:93-94` · 종가 절댓값 `scripts/sector_flow.py:90`(DB 가 상하한 표시를 음수 부호로 싣는 경우가 있다) ·
`EOK = 1e8` `scripts/sector_flow.py:44`. $P$ 는 `supply_demand.close`(키움, 원 종가)다.

이 근사가 문서 전체의 하한이다. 참값은 VWAP 가중이다. 방향과 상대 크기를 보는 값이지 원 단위 정확도를 주장하지 않는다
(지표 검증 Q3b: 거래소는 안정, 코스닥은 48일 중 71% 만 상대차 중앙 5% 이내).

**금액 근사 오차.** 시장 $M$ 의 기준일 $N-1$ 에서, 종목 $c$ 의 4주체 $|$순매수 수량$|$ 합 $n_c=\sum_A |q^A_{c,N-1}|$,
VWAP $V_c = \text{trade\_value}_c\times10^6/\text{volume}_c$(`daily_bars`, 둘 다 양수인 종목만 — 양쪽 합에서 같은 집합)으로

**(2a)** $\quad g_M = \dfrac{\big|\sum_{c\in M} n_c P_c - \sum_{c\in M} n_c V_c\big|}{\sum_{c\in M} n_c V_c}\times100$ [%]

`approx_gap` `scripts/sector_flow.py` → `today.market[M].approx_gap`(`pct`·`close`·`vwap`[억]·`cover`[%] = VWAP 가 있는 종목의 금액 몫).
2026-09-14 이후 `daily_bars` 의 거래량·거래대금과 `supply_demand.close` 는 모두 애프터마켓 포함 통합값이라 세 값의 세션 정의가 같다 —
$V$ 는 통합 세션 VWAP 다. 5% 를 넘으면 화면·웹이 `금액 근사 오차 큼(N%)` 칩을 띄운다. 시장 합에서는 종목별 차가 부호가 갈려 상쇄되므로
Q3b(섹터 단위 상대차)보다 작게 나온다(2026-10-26~11-06 실측 0.15~3.1%).

섹터·시장 단위 일별 값은 종목 합이다.

**(2)** $\quad f^A_{m,s,i} = \sum_{c\in(m,s)} f^A_{c,i}$

`scripts/sector_flow.py:258-267`. 섹터가 비어 있는 종목은 `(미분류)` 로 묶인다(`scripts/sector_flow.py:91`).
기관 세부 6개(금투·보험·투신·은행·연기금·사모)도 같은 식으로 환산해 `detail` 에 싣는다(`scripts/sector_flow.py:95-96`, `scripts/sector_flow.py:269-278`).
`natn`(국가)은 90일 내내 0 이라 뺐다(`scripts/sector_flow.py:57-59`).

### 1.2 거래대금

**(3)** $\quad tv_{c,i} = Q_{c,i}\times|P_{c,i}|\,/\,10^8,\quad Q=$ `acc_trde_qty`

`scripts/sector_flow.py:97`. `daily_bars.trade_value` 가 아니라 **수급 표의 누적 거래량 × 수급 표의 종가**다. 섹터 합은 (2)와 같다.

### 1.3 종목 일별 수익률 — 조정 종가 비

**(4)** $\quad r_{c,i} = \left(\dfrac{P^{adj}_{c,i}}{P^{adj}_{c,\,\text{직전}}}-1\right)\times100$ [%]

`adjusted_returns` `scripts/sector_flow.py:102-128`(직전 거래일 종가 확보 `scripts/sector_flow.py:117`, 비율 `scripts/sector_flow.py:125`).
$P^{adj}$ 는 `daily_bars_adjusted.close`, "직전" 은 그 종목의 바로 앞 조정 종가 행이다. 분할·병합은 그 표에서 백조정돼 있다.

- **그날 조정 종가가 없으면 $r=$ NaN** 이다 — 0 으로 채우지 않는다. 완전성 관문이 기준일 조정 종가 커버리지 97% 를
  요구한다(`scripts/completeness_gate.py:51`(`MIN_COVERAGE`)).
- 예전엔 `supply_demand.flu_rt / 100` 을 썼다. 2026-09-14 부터 `flu_rt` 의 분자는 애프터마켓 포함 종가, 기준가는 전일 정규장
  종가라 이어 곱할 수 없다(20일 섹터 수익률 차 중앙 1.9%p, 최대 8.9%p).
- **세션.** 2026-09-14 부터 `daily_bars(_adjusted)` 의 종가는 KRX 애프터마켓(16:00~20:00) 포함 통합값이다(quant-airflow
  `docs/schema.md`). 그래서 $r$ 과 그것으로 만든 모든 수익률(섹터·시장·종목)은 **전일 20:00 → 당일 20:00** 이다. KRX 지수(정규장
  15:30)와 같은 날 갈린다 — 11-06 거래소 자체 바구니 −4.37% vs 코스피 −6.02%. 정규장 종가(`regular_close`)로 다시 재면 0.01%p
  안에서 맞고, 09-14 전 차이는 ~0.01%p, 뒤로는 평균 ~0.8%p·최대 2.3%p 다. 화면·웹은 수익률에 `~20시`(통합 종가) 라벨을 달고
  첫 화면·오늘 패널에 `KRX 지수(정규장)` 를 나란히 적는다(§2.18). 09-14 이전 날이 섞인 긴 구간은 그 날들만 정규장 종가다.
- 단위 함정: $r$ 은 **퍼센트**다. $1+r$ 을 곱하면 하루 +2.5% 가 +250% 가 된다(`scripts/sector_flow.py:330-332`).

### 1.4 시가총액

**(5)** $\quad \mathrm{cap}_{c,i} = |P^{raw}_{c,i}|\times S_{c,\sigma(i)}\times\exp\big(\Gamma_c(i)-\Gamma_c(\sigma(i))\big)\,/\,10^8$ [억원]

`market_cap_asof_bulk` `src/sectorflow/storage.py:314-405`.

- $P^{raw}$ — `supply_demand.close`(키움, 원 종가), 없거나 0 이하면 `daily_bars_raw.close`(`src/sectorflow/storage.py:352-354`,
  `src/sectorflow/storage.py:367-368`). **`daily_bars`(수정주가)는 쓰지 않는다** — 뒤에 병합이 있으면 과거 가격이 배수로 커진다.
- $S_{c,\sigma(i)}$ — `shares_outstanding_history` 에서 $i$ 이하 최신 행, $\sigma(i)$ 는 그 행의 날짜(앞을 보지 않는다, `src/sectorflow/storage.py:374-375`).
- $\Gamma_c(x)=\sum_{e\le x}-\ln(1+u_e/100)$, $u_e$ 는 이벤트 $e$ 의 `corporate_actions.upd_rt` — 스냅샷 **뒤** 분할(8)·병합(16)·감자(64) 만큼 주식수를
  다시 센다(`SHARE_EVENTS` `src/sectorflow/storage.py:311`, 누적 `src/sectorflow/storage.py:389`, 배율 `src/sectorflow/storage.py:397-399`).
  권리락(256)은 넣지 않는다. 078130(2026-05-20 10:1 병합)의 6월 시총이 4.5조로 나오던 것이 4,515억이 됐다.
- 가격이나 주식수가 없으면 NaN. 주식수 0 이하도 NaN(`src/sectorflow/storage.py:403`).

세 가지 시총이 다른 용도로 쓰인다. 섞지 않는다.

| 값 | 정의 | 쓰이는 곳 |
|---|---|---|
| $C_s$ (섹터 시총) | 페이로드 기준일의 그 (시장, 섹터) 종목 시총 합. 시총이 NaN 인 종목은 빠진다 | 가속(§2.3)·조각별 α(§2.10·§2.11)·동시성 정규화(§4.7) |
| $C_c$ (종목 시총) | 페이로드 기준일의 그 종목 시총 | 시총대비·시총 열(§3.8) |
| $C_{c,i-1}$ (전일 시총) | 종목별 일별 시총의 1일 지연 | 섹터 수익률의 가중치(§2.6) |

`sector_cap` `scripts/sector_flow.py:185-203` · `attach_daily_cap` `scripts/sector_flow.py:131-143`(`cap_lag` `scripts/sector_flow.py:142`) ·
페이로드 `cap` `scripts/sector_flow.py:280-282`(`caps`) · `names[c]["cap"]` `scripts/sector_flow.py:307`.
블록은 언제나 페이로드 기준일에서 끝나므로(§2.1) $C_s$ 는 곧 **구간말** 시총이다.

전일 시총으로 가중하는 이유: 같은 날 거래대금이나 같은 날 시총으로 가중하면 그날 급등한 종목이 가중치도 커서 섹터 수익률이
부풀려진다(실측: 3종목짜리 부동산이 20거래일 +102%).

### 1.5 유니버스와 그 대가

**(6)** `read_supply_demand(..., sources=("kiwoom",), allow_individual_survivorship=True, require_delisted=False)`

`scripts/sector_flow.py:78-86`. 개인과 기관 세부는 키움에만 있다. 소스를 안 좁히면 지표마다 유니버스가 달라진다. 대가는
생존편향이다(§7.4). 종목 메타는 `stocks`(현재 상장 마스터)와 inner join 한다(`scripts/sector_flow.py:88-89`).

**「기관 = 6개 세부의 합」은 이 DB 에서 거짓이다.** 화면의 기관 열은 벤더가 주는 기관 합계다. 260일 실측으로 행의 8.9% 에서
둘이 다르고, 차이는 기관 gross 의 2.9% 다. 투신·연기금(§3.4)을 더해 기관 열을 검산하면 안 맞는다.

### 1.6 종목별 프리셋 집계

종목 단위 값은 프리셋 구간(5·20·60·120)에서만 있다. 임의 구간 드릴다운을 포기하고 전 종목을 정확히 보는 쪽을 택했다
(일별 배열은 ~4MB, 프리셋 합계는 ~200KB).

이하 $\nu^W_c[\kappa]$ 는 페이로드의 `names[c]["win"][str(W)][κ]` 를 뜻한다(`penfnd` 는 키 `penfnd_etc`).

**(7)** $\quad \nu^W_c[\kappa] = \sum_{i=N-W}^{N-1} f^\kappa_{c,i},\quad \kappa\in\{\mathrm{inst},\mathrm{forgn},\mathrm{indiv},\mathrm{etc},\mathrm{tv},\mathrm{invtrt},\mathrm{penfnd}\}$

**(8)** $\quad \nu^{W}_c[\mathrm{ret}] = \Big(\prod_{i=N-W}^{N-1}(1+r_{c,i}/100)-1\Big)\times100$ [%]

`scripts/sector_flow.py:320-339`(기하 누적 `scripts/sector_flow.py:333-334`) · `NAME_WINDOWS` `scripts/sector_flow.py:50`.
**결측일($r$=NaN)은 1 배로 본다**(그날 0%). 조정 종가가 하루 비어도 다음 유효일의 수익률이 직전 유효 종가에서 재므로 구간
누적은 보존된다. 그 구간에 한 번도 거래가 없으면 그 창의 키 자체가 없다 — 이것이 §2.13·§3.1 의 "그 구간에 거래된 종목" 이다.

---

## 2. sw-flow — 섹터 표

### 2.1 구간과 시장

**(9)** $\quad i_0 = N-W,\quad i_1 = N-1,\quad W\in\{5,20,60,120\}$ (화면은 1 도 — 블록이 아니라 `today` 에서, §2.19)

`scripts/sector_numbers.py:295-298` · `WINDOWS` `scripts/sector_numbers.py:22`. $W>N$ 인 창은 만들지 않는다. 시장은 `전체`(모든
시장의 합)와 시장별 단독이고 블록 키는 `"{창}|{시장}"` 이다(`scripts/sector_numbers.py:299-300`, `scripts/sector_numbers.py:461`).
그 시장에 종목이 하나도 없는 섹터 행(종목 수 0, 시총 0)은 화면에서 뺀다(`absent_row` `src/sectorflow/tui/flow_view.py:149-157`).

### 2.2 임펄스[억]

**(10)** $\quad F^A_s = \sum_{m\in M}\sum_{i=i_0}^{i_1} f^A_{m,s,i}$ [억원]

블록 생성 `scripts/sector_numbers.py:123-129`(`agg` 의 `s()`) · 주체 투영 `src/sectorflow/tui/flow_view.py:491-494` · 열 `src/sectorflow/tui/flow_view.py:1563`.
부호를 명시하고 천단위 구분을 넣은 정수로 적는다(`def fmt_amt` `src/sectorflow/tui/flow_view.py:245-248`).

### 2.3 가속[%p]

**(11)** $\quad a^A_s = F^A_s / C_s \times 100$ [%p]

블록 `scripts/sector_numbers.py:159` · 화면 `src/sectorflow/tui/flow_view.py:495` · 열 `src/sectorflow/tui/flow_view.py:1562`.
금액만 보면 대형 섹터가 늘 이기므로 "어디로 쏠렸나" 는 규모로 정규화된 이쪽이 정직하다. 그래서 **기본 정렬이 가속**이다
(`SORTS` 첫 항목 `src/sectorflow/tui/flow_view.py:163`(`SORTS`)). $C_s=0$ 이면 블록과 화면 모두 결측이고, 그런 행은 `absent_row` 로 빠진다.

이 "가속" 과 풀림(ẍ, §2.10)은 이름만 같다. 여기 $a$ 는 자금 축의 0차 비율이고 ẍ 는 가격 갭의 2차 시간미분이다.

### 2.4 1년[%ile]

이 표에서 유일한 시계열 맥락이다 — 나머지 열은 섹터 사이의 상대 순위다.

**(12)** $\quad S_j = \sum_{u=j}^{j+W-1} f^A_{s,u},\quad j=0,\dots,n-W,\quad n=\min(N, 260)$

**(13)** $\quad \text{pct} = 100\times\dfrac{\#\{j: S_j<S_{\text{last}}\} + \tfrac12\big(\#\{j:S_j=S_{\text{last}}\}-1\big)}{|S|-1}$

`rolling_pct` `scripts/sector_numbers.py:77-101` · `PCT_LOOKBACK = 260` `scripts/sector_numbers.py:61` · 주체별로 따로 싣는다
`scripts/sector_numbers.py:311-319` · 열 `src/sectorflow/tui/flow_view.py:1564-1565`. 동률은 중간순위다. $W>n$ 이거나 $|S|<2$ 면 결측.
z-점수를 안 쓰는 이유: 창이 겹쳐 자기상관이 크고 260일 안의 비겹침 20일 창은 13개뿐이다.

### 2.5 추이[8]

(i) 페이로드 단계에서 구간을 최대 8개의 비겹침 조각으로 나눠 합을 낸다.

**(14)** $\quad g_j = \sum_{u=i_0+\lfloor jn/k\rfloor}^{i_0+\lfloor (j+1)n/k\rfloor-1} f^A_{s,u},\quad n=i_1-i_0+1,\ k=\min(8,n)$

`spark_segs` `scripts/sector_numbers.py:104-119` · `SPARK_SEGS = 8` `scripts/sector_numbers.py:58`. 조각 합의 총합이 임펄스와 정확히 같다.

(ii) 화면 단계에서 누적해 4단계 브라유(`⣀⣤⣶⣿`)로 그린다.

**(15)** $\quad c_j=\sum_{u\le j} g_u,\quad \ell_j=\min\!\big(3,\lfloor 3(c_j-lo)/(hi-lo)+0.5\rfloor\big),\quad lo=\min(0,c),\ hi=\max(0,c)$

`spark` `src/sectorflow/tui/flow_view.py:1488-1515` · 열 `src/sectorflow/tui/flow_view.py:1566`. 오른쪽으로 올라가면 계속 유입,
내려가면 유출, 평평하면 멈춤. 높이는 그 행 안에서만 정규화하므로 행끼리 비교되지 않는다. 조각이 8개보다 적으면 왼쪽을 `⠀`
로 비운다. $hi=lo$ 면 전 칸이 최저 단계다.

### 2.6 수익률[%]

그 섹터 **자체 바구니**의 구간 수익률이다. KRX 업종지수가 아니다.

**(16)** $\quad \bar r_{m,s,i} = \dfrac{\sum_{c\in(m,s),\,V} C_{c,i-1}\,r_{c,i}}{\sum_{c\in(m,s),\,V} C_{c,i-1}},\quad V=\{c:\ r_{c,i}\ne\text{NaN}\ \wedge\ C_{c,i-1}\ne\text{NaN}\}$

**(17)** $\quad \Delta v_s = \Big(\prod_{i=i_0}^{i_1}\big(1+\bar r_{s,i}/100\big)-1\Big)\times100$ [%], 여기서 $\bar r_{s,i}$ 는 고른 시장들의 가중합 $\sum_m w_{m,s,i}\bar r_{m,s,i}/\sum_m w_{m,s,i}$

일별 가중평균 `scripts/sector_flow.py:343-352`(유효 종목만 `scripts/sector_flow.py:349`) · 구간 기하누적 `scripts/sector_numbers.py:138-151` ·
열 `src/sectorflow/tui/flow_view.py:1567`.

- **수익률이 없는 종목은 분자에서도 가중치에서도 뺀다.** 남기면 평균이 0 쪽으로 끌린다.
- 그날 유효 가중합이 0 이면 그날 기여는 0(1 배)이다. 구간 안에 한 번도 유효하지 않았으면 $\Delta v$ 는 결측이다(`scripts/sector_numbers.py:161`).
- KRX 업종지수를 버린 이유: 분자(자금)·분모(시총)는 `stocks.sector` 바구니인데 수익률만 다른 바구니면 짝이 어긋난다(부동산 20일
  업종지수 +6.0% vs 자체 바구니 +74.9%). 페이로드의 `iret` 은 대조용으로만 실린다.

### 2.7 횡단면 회귀 k · b · t, 그리고 예상Δv

블록(창 × 시장)마다 그 안의 섹터들을 표본으로 절편 포함 OLS 를 한 번 적합한다. 설명변수는 기관 가속이다.

**(18)** $\quad \mathcal U=\{s:\ \Delta v_s\ne\varnothing\ \wedge\ C_s>0\ \wedge\ \neg\,\text{thin}_s\},\quad \alpha_s=F^{\text{기관}}_s/C_s\times100$

**(19)** $\quad k=\dfrac{\sum_{\mathcal U}(\alpha_s-\bar\alpha)(\Delta v_s-\overline{\Delta v})}{\sum_{\mathcal U}(\alpha_s-\bar\alpha)^2},\quad b=\overline{\Delta v}-k\bar\alpha$ (평균은 $\mathcal U$ 위)

**(20)** $\quad t=k/\mathrm{se}(k),\quad \mathrm{se}(k)=\sqrt{\dfrac{\mathrm{SSE}/(n-2)}{\sum_{\mathcal U}(\alpha_s-\bar\alpha)^2}},\quad n=|\mathcal U|$

`scripts/sector_numbers.py:346-361`(표본 `scripts/sector_numbers.py:346`) · 블록에 저장 `scripts/sector_numbers.py:461-465`(k·b 소수 3자리, t 2자리) ·
헤더 `block_meta` `src/sectorflow/tui/flow_view.py:646-658`.

- **얇은 섹터(종목 10개 미만, §2.13)는 적합에서 뺀다.** 한두 종목의 등락이 섹터 수익률이 되는 점이 큰 지렛대를 가져 k 의 부호를
  바꾼 날이 있었다(2026-10-15 20|전체: 포함 −0.59 · 제외 +14.42).
- $n\le2$ 면 $k=b=t=0$ 이다(`scripts/sector_numbers.py:360-361`). 그때 $x=-\Delta v$, U·ẍ 는 결측이 된다.
- 원점통과 회귀를 안 쓰는 이유: 절편이 없으면 시장 전체 드리프트가 전부 잔차로 밀려 부호가 장세를 따라간다.

**(21)** $\quad \widehat{\Delta v}_s = k\,\alpha_s + b$ [%]

`scripts/sector_numbers.py:366`. 예상Δv 는 표의 열이 아니다 — 미실현의 중간항이고 도움말에만 있다.

$k$ 를 상수처럼 읽지 마라. 블록마다 다시 적합되고 창이 바뀌면 값도 t 도 바뀐다. 소스 주석에 남은 $k$ 예시
(`scripts/sector_numbers.py:467`)는 다른 날의 값이다. 오늘의 $k$·$t$ 는 화면 헤더에 찍힌다.

### 2.8 미실현[%p]

**(22)** $\quad x_s = \widehat{\Delta v}_s - \Delta v_s$ [%p]

`scripts/sector_numbers.py:367` · 열 `src/sectorflow/tui/flow_view.py:1568`(소수 1자리). $x>0$ 은 "그 유입이면 갔어야 할 만큼에서 덜 간 폭",
$x<0$ 은 "이미 더 갔다" 다. OLS 잔차의 부호 반전이라 **적합 표본 $\mathcal U$ 안에서** 합이 0 이다. 얇은 섹터에도 같은 직선으로
$x$ 를 내지만 그 값은 $\sum=0$ 에 들지 않는다. $\Delta v$ 나 $C$ 가 없는 섹터는 $\alpha,\widehat{\Delta v},x,U$ 가 전부 결측이다
(`scripts/sector_numbers.py:363`, `scripts/sector_numbers.py:387`).

### 2.9 포텐셜[½kx²] — $k\le0$ 인 블록에서 결측

**(23)** $\quad U_s=\begin{cases}\tfrac12 k x_s^2 & k>0\\ \varnothing & k\le0\end{cases}$

`scripts/sector_numbers.py:385` · 열 `src/sectorflow/tui/flow_view.py:1576-1577`. $k$ 가 블록당 상수라 $U$ 의 뜻은 하나뿐이다 — $|x|$ 의
단조 변환. $k<0$ 이면 $|x|$ 의 순감소 변환이라 "포텐셜 큰 순" 이 정반대가 된다(2026-08-28 실측: 5일·코스닥 $k=-0.587$, $t=-0.24$, 22개 섹터
전부 $U<0$, $\rho(|x|,U)=-1$). 처방은 $\tfrac12|k|x^2$ 가 아니라 결측이다 — 라벨과 계산이 갈리지 않게. $k>0$ 일 때도 제곱 때문에
부호가 죽으므로 하단 상세 패널에서는 뺐다(`src/sectorflow/tui/flow_view.py:1864-1868`). 정렬 열로만 남는다.

### 2.10 풀림[%p/일²]과 ẋ

구간을 셋으로 갈라 조각마다 $x$ 를 다시 구하고 중앙차분한다. 조각 안에서 $k$ 를 다시 추정할 표본이 없으므로 블록의 $k,b$ 를 쓴다.

**(24)** $\quad n=i_1-i_0+1,\quad \Delta=\lfloor n/3\rfloor,\quad$ 조각 $[i_0,\,i_0+\Delta-1],\ [i_0+\Delta,\,i_0+2\Delta-1],\ [i_0+2\Delta,\,i_1]$

**(25)** $\quad x_p=(k\,\alpha^{(p)}+b)-\Delta v^{(p)},\quad \alpha^{(p)}=F^{\text{기관},(p)}_s/C_s\times100,\quad p=1,2,3$

**(26)** $\quad \dot x=-\dfrac{x_3-x_1}{2\Delta}$ [%p/일], $\quad \ddot x=-\dfrac{x_3-2x_2+x_1}{\Delta^2}$ [%p/일²]

`release` `scripts/sector_numbers.py:217-249` · 대입 `scripts/sector_numbers.py:389-397` · 열 `src/sectorflow/tui/flow_view.py:1569`(소수 3자리).
$\dot x$ 는 페이로드에만 있고 표에 열이 없다(`SORTS` 에도 없다).

- 조각별 $\alpha^{(p)}$ 의 **분모는 조각 끝이 아니라 블록 기간말 섹터 시총 $C_s$** 다.
- **셋째 조각은 $i_1$ 까지** 간다 — $n$ 이 3 의 배수가 아니면 셋째 조각이 길다(20일 창: 6·6·8일). 그래도 나누는 값은 $\Delta$ 다.
- 부호를 뒤집는 이유: $x$ 가 줄어드는 것이 풀리는 것이다. $\ddot x>0$ 은 "풀리는 속도가 빨라지고 있다" 다.
- $n<9$ 면 $(\varnothing,\varnothing)$(`scripts/sector_numbers.py:236-237`). **5일 창에는 ẍ 가 없다** — §2.16 의 "종합 축에 5일이 없다" 의 원인이다.
- 조각 하나라도 $\Delta v$ 나 $C$ 가 없으면 결측, 블록 $k=0$ 이어도 결측(`scripts/sector_numbers.py:390`). 2차 차분이라 짧은 조각에서 노이즈가 크다.

### 2.11 dW/dt[%p/일]

구간을 반으로 갈라 $W=\alpha\cdot\Delta v$ 의 변화를 낸다.

**(27)** $\quad \text{mid}=i_0+\lfloor (i_1-i_0)/2\rfloor,\quad W_{\text{전}}=\alpha_{[i_0,\text{mid}]}\,\Delta v_{[i_0,\text{mid}]},\quad W_{\text{후}}=\alpha_{[\text{mid}+1,i_1]}\,\Delta v_{[\text{mid}+1,i_1]}$

**(28)** $\quad \dfrac{dW}{dt}=\dfrac{W_{\text{후}}-W_{\text{전}}}{\max(1,\,i_1-\text{mid})}$ [%p/일]

`power` `scripts/sector_numbers.py:195-214` · 대입 `scripts/sector_numbers.py:388` · 열 `src/sectorflow/tui/flow_view.py:1578`.
$\alpha$ 의 분모는 역시 $C_s$ 다. 반쪽에서 $\Delta v$ 나 $C$ 가 없거나 반쪽이 안 나오는 짧은 구간($\text{mid}\le i_0$)이면 결측.
$k$ 를 쓰지 않으므로 5일 창에서도 나온다 — ẍ 와 다른 점이다.

### 2.12 G[0~1]과 `*` 마커 — 탐색 모드(`e`)에서만

**관문이 탈락을, 점수가 순서를 정한다.** 관문을 하나라도 어기면 점수가 없다.

**(29)** $\quad \mathcal S=\{s:\ \neg\,\text{thin}_s\ \wedge\ \alpha_s>0\ \wedge\ x_s>0\ \wedge\ \ddot x_s>0\}$ (세 값 모두 결측 아님)

**(30)** $\quad G_s=\begin{cases}\tfrac13\big(R_\alpha(s)+R_x(s)+R_{\ddot x}(s)\big),\ \ R_\kappa(s)=\dfrac{\mathrm{rank}_{\mathcal S}(\kappa_s)}{|\mathcal S|-1} & s\in\mathcal S,\ |\mathcal S|\ge2\\[4pt] 0.5 & \mathcal S=\{s\}\\[2pt] \varnothing & s\notin\mathcal S\end{cases}$

**(31)** $\quad \mathrm{pass}_s=[\,G_s\ne\varnothing\,]$ → 표의 `*` 마커(페이로드 키 `G_pass`)

관문 `scripts/sector_numbers.py:421-424` · 단독 통과 0.5 `scripts/sector_numbers.py:425-430` · 순위(오름차순, 0-기반, 동률은 중간순위)
`scripts/sector_numbers.py:431-454` · `G_pass` `scripts/sector_numbers.py:455-459` · 마커 열 `src/sectorflow/tui/flow_view.py:1559-1561` ·
G 열 `_TABLE_COLS` (`src/sectorflow/tui/flow_view.py`). 열 이름은 2026-11-08 `선정` → `G` 로 되돌렸다 — 종목 층의
`선정`(§3.9)과 설계가 같지 않은데(단독 후보·숨은 관문) 같은 이름을 쓰고 있었다. G·`*`·종합 G 는 `e` 로 켜는 탐색 모드에서만 보인다.

- **순위의 모집단은 관문 통과 섹터뿐이다.** 예전 정의(PDF 식 27)는 얇지 않은 전 섹터를 순위에 넣고 통과(`*`)를 따로 냈다.
- 통과 섹터가 하나뿐이면 상대가 없으므로 0.5 다. 예전엔 점수를 안 줘서 세 관문을 다 통과한 섹터가 표시 없이 사라졌다.
- 순위를 쓰는 이유: $a$ 는 0~5 %p, $x$ 는 ±50 %p, $\ddot x$ 는 $10^{-2}$ 스케일이라 그대로 더하면 $x$ 가 지배한다.
- 마커는 별도 1칸 열이다. 얇은 섹터는 `~` 가 우선한다. 웹 수치표(`numbers.html`)는 같은 통과를 `●` 로 적는다.
- 근거 수준: **판정 불가.** 관문 통과가 날짜당 2~7개(중앙 2)라 순위가 0 아니면 1 인 날이 많고, 앞날 검정에 쓸 만한(통과 5섹터 이상) 날이 h5 82일·h20 19일뿐이었다.
  $x$ 가 $-\Delta v$ 와 얽혀 있어 사실상 "많이 빠졌고 + 돈은 들어온" 섹터를 고르는 평균회귀형 점수다. 그래서 기본 화면에 없다.

### 2.13 종목[수]와 `~` 마커

**(32)** $\quad n_s=\#\{c:\ \text{sector}(c)=s\ \wedge\ \text{market}(c)\in M\ \wedge\ \nu^{W}_c\ne\varnothing\},\qquad \text{thin}_s=[\,n_s<10\,]$ → `~` 마커

`names_count` `scripts/sector_numbers.py:258-280` · `MIN_NAMES = 10` `scripts/sector_numbers.py:255` · 창별로 다시 셈
`scripts/sector_numbers.py:305-307` · 열 `src/sectorflow/tui/flow_view.py:1581` · 마커 `src/sectorflow/tui/flow_view.py:1559-1561`.

「상장 종목 수」가 아니라 「그 구간에 거래된 종목 수」다. `Enter` 로 여는 목록의 길이와 정확히 같다. `thin` 은 k 적합(§2.7)과
G 관문(§2.12) 둘 다에서 쓰인다. 얇은 행은 회색으로 그린다. 마커를 글자로 두는 이유는 무색 터미널·색맹·`--dump` 에서 색이 사라지기 때문이다.

### 2.14 순매수상위 · 순매도상위

**(33)** $\quad \text{buy}=\text{상위 3}\{c: \phi_c>0\},\quad \text{sell}=\text{하위 3}\{c:\phi_c<0\},\quad \phi_c=\nu^{W'}_c[A]$

`State._leads` `src/sectorflow/tui/flow_view.py:503-533`(부호가 맞는 종목만 `src/sectorflow/tui/flow_view.py:529-530`) · 표의 열(1개씩)
`src/sectorflow/tui/flow_view.py:1574-1575` · 상세 패널(3개씩) `src/sectorflow/tui/flow_view.py:1850-1857`(`def side`). $A$ 는 **고른 주체**, $W'$ 는
현재 창(종합이면 20일)이다. 페이로드의 `top`(`drivers`, 기관 전용)은 화면이 쓰지 않는다. 정렬이 시총 대비가 아니라 금액인 이유는
섹터 합계가 금액의 합이라 기여도가 금액으로만 정의되기 때문이다.

### 2.15 투신+연금5일[억]

**(34)** $\quad T_s=\sum_{c\in(M,s)}\big(\nu^{5}_c[\mathrm{invtrt}]+\nu^{5}_c[\mathrm{penfnd}]\big)$ [억원]

`trusted_recent` `src/sectorflow/tui/flow_view.py:846-877` · 열 `src/sectorflow/tui/flow_view.py:1579-1580`. 고른 창·주체와 무관하게
**최근 5일**의 투신+연기금이다. 표의 임펄스는 현재 창 누적이라 "아직도 들어오나" 를 말하지 않는다. 좋다/나쁘다 부호는 붙이지
않는다 — 최근 유출이 눌림목인지 추세전환인지는 가격 경로가 있어야 갈린다.

### 2.16 종합 축 — 창별 G 와 통과[구간]

**(35)** $\quad \mathcal W=\{W:\ \exists s,\ G^{(W)}_s\ne\varnothing\},\quad |\mathcal W|\ge2$ 일 때만 종합 축을 만든다

**(36)** $\quad G^{\text{종합}}_s=\dfrac{1}{|\mathcal W_s|}\sum_{W\in\mathcal W_s}G^{(W)}_s,\quad \mathcal W_s=\{W\in\mathcal W:\ G^{(W)}_s\ne\varnothing\}$

**(37)** $\quad \text{통과[구간]}=|\mathcal W_s|\,/\,|\mathcal W|$ (예 2/3)

`scripts/sector_numbers.py:472-505`(창 고르기 `scripts/sector_numbers.py:476-479`, 평균 `scripts/sector_numbers.py:497-498`) ·
열 구성 `table_cols` `src/sectorflow/tui/flow_view.py:1593-1612`(통과 `src/sectorflow/tui/flow_view.py:1609-1610`) · 창별 G `_per_win`
`src/sectorflow/tui/flow_view.py:1585-1590`(`def _per_win`).

- G 가 이제 관문 통과 섹터에만 있으므로 **종합 G 는 그 섹터가 통과한 창들만의 평균**이다. 한 창만 통과한 섹터가 세 창 모두
  통과한 섹터보다 위에 올 수 있다(도움말 `src/sectorflow/tui/flow_view.py:2278` 이 그렇게 적는다).
- 그날 통과 섹터가 하나도 없는 창은 $\mathcal W$ 에서 빠진다. 5일 창은 ẍ 가 없어 늘 빠진다(§2.10). 남는 창이 2개 미만이면 종합이
  없고 화면이 그 사유를 적는다(`src/sectorflow/tui/flow_view.py:610-614`(`def empty_reason`)). "20·60·120 세 창" 은 관측 결과이지 상수가 아니다.
- 종합의 `*` 는 판정할 수 있었던 창(얇지 않고 a·x·ẍ 가 다 있다) **모두**에서 통과했을 때 붙는다(`G_pass` `scripts/sector_numbers.py:500`).
- 종합에는 주체별 값과 정렬이 없다(`정렬없음[G 순]`, 페이로드 순서 = $G^{\text{종합}}$ 내림차순 `scripts/sector_numbers.py:504`).
  견인주·종목 목록·4주체 줄은 대표 창 20일을 쓴다(`COMBINED_WIN` `src/sectorflow/tui/flow_view.py:588`).

### 2.17 헤더 · 주체 투영 · 정렬

**헤더 두 줄** — `header_lines` `src/sectorflow/tui/flow_view.py:1422-1461`.

| 칩 | 내용 | 근거 |
|---|---|---|
| 날짜 + 상태 | `마감·확정` / `마감·잠정` / `장중·미확정` + `⚠ …` 경고(§5) | `status_chip` `src/sectorflow/tui/flow_view.py:1366-1375` |
| 구간[·] 시장[·] 주체[·] | 현재 선택 | `src/sectorflow/tui/flow_view.py:1448-1449` |
| 정렬[열▼/▲] / 정렬없음[G 순] | 종합에는 정렬이 없다 | `sortable` `src/sectorflow/tui/flow_view.py:539-553`, `src/sectorflow/tui/flow_view.py:1437-1448` |
| 날짜 ~ 날짜 · k=… (t=…) | 블록 구간과 그날 적합한 회귀. 종합이면 `구간 20·60·120일 등가중` | `block_meta` `src/sectorflow/tui/flow_view.py:646-658` |
| ※ 미실현·포텐셜·dW/dt·풀림·G 는 기관 기준 | 주체가 기관이 아니고 종합이 아닐 때 | `src/sectorflow/tui/flow_view.py:1452-1454` |

**주체 투영** — 주체를 바꾸면 임펄스·가속·1년%ile·추이·견인주가 그 주체 값으로 바뀐다(`_project` `src/sectorflow/tui/flow_view.py:479-501`).
바뀌지 않는 것은 $k\cdot b$ 에서 파생된 값이다.

**(38)** `INST_ONLY = ("exp", "x", "U", "P", "xdot", "xddot", "G", "G_pass")` — `src/sectorflow/tui/flow_view.py:144`(`INST_ONLY`)

$k$ 는 (18)에서 기관 유입에 회귀한 것이라 다른 주체로 재계산할 수 없다. 지우지 않고 남기되 헤더가 출처를 밝힌다.

**정렬 폴백** — 선택한 정렬 키가 그 블록에서 전멸이면(5일 창의 G·풀림) `accel → flow → ret` 순으로 대체하고 헤더에
`※ 이 구간에 … 값이 없다` 를 적는다(`SORT_FALLBACK` `src/sectorflow/tui/flow_view.py:536`, `effective_sort` `src/sectorflow/tui/flow_view.py:555-568`).
정렬 키 목록 `SORTS` `src/sectorflow/tui/flow_view.py:163-165` — 거래대금·시총은 열 없이 정렬 키로만 있다.

### 2.18 시작 요약(배너) — 오늘의 수급

`sw-flow` 를 띄우면 표 앞에 한 번 뜬다. 점수·순위·추천을 싣지 않고 페이로드 `today`·`change`·`final_diff` 의 값을 옮긴다
(`banner_lines` `src/sectorflow/tui/flow_view.py`). 웹 수치표 맨 위 **오늘** 패널(`renderToday`
`scripts/templates/sector_numbers.html`)이 같은 사실을 같은 순서로 적는다. 시장은 화면 선택(기본 전체), 주체는 기관과
투신+연기금($\mathrm{invpen}=\mathrm{invtrt}+\mathrm{penfnd}$)이다.

- **시장 수익률 1·20일** — 자체 바구니 $\prod(1+\bar r_{m,i}/100)-1$(시장 단위 (16), `today.market[m].ret`)와 KRX 종합지수
  $P^{idx}_{N-1}/P^{idx}_{N-1-w}-1$(`sector_index` 코드 001 코스피·101 코스닥, `today.krx_index`, `krx_index_block`
  `scripts/sector_flow.py`). 지수의 날짜는 이 페이로드의 거래일에 맞추고 없는 날은 메우지 않는다(결측). 둘은 세션이 다르다(§1.3).
- **오늘 시장 순매수** — `today.market[시장].net[A]` 의 값과 1년%ile(§2.19). 4주체는 합 ≈ 0 이라 하나도 자르지 않는다(좁으면 두 줄).
- **금액 근사 오차 칩** — §1.1 (2a) 가 `APPROX_GAP_WARN = 5` 를 넘는 시장에만.
- **상위·하위 3** — 오늘 섹터 순매수 $f^A_{s,N-1}$ 금액 순, 각자의 1년%ile.
- **1년%ile 극단** — 오늘 값의 백분위 $p\ge95$ 또는 $\le5$(문턱 포함). 직전 리포트와 견주지 않는다 — 오늘 값이 1년 중 어디인지다.
- **직전 리포트 대비** — `change.top[시장][A]` 의 `entered`·`exited`: 직전 거래일 리포트가 게시한 20일 순매수 순위의 상위 5 와
  오늘 상위 5 의 차집합. 직전 리포트가 없으면 `change_reason`.
- **부호 반전** — 전 섹터에서 $\mathrm{sign}(J^{20}-J^{5})\ne\mathrm{sign}(J^{5})$ 인 것(`reversal_flags`, 선택 주체). 눌림인지
  전환인지는 판정하지 않는다(판정 불가 라벨).
- **잠정→확정 변경: N칸** — 확정 리포트에서만. `final_diff.n_changed / n_cells`. 바뀐 칸 목록은 도움말 맨 끝(`final_diff_help`)과
  웹 패널의 접힌 목록에 있다.

옛 리포트(`today` 없음)면 그렇다고 적고 20일 블록의 기관·투신+연기금 상·하위만 적는다.

### 2.19 1일 창(`w` → 1)

블록(`blocks`)에 1일 창은 없다. 화면이 `today.sectors[시장]` 을 블록 행 모양으로 옮긴다(`State._today_rows`).

**(38a)** $\quad F^A_s = f^A_{s,N-1},\qquad p^A_s = \text{pct}\big(\{f^A_{s,u}\}_{u=N-n}^{N-1}\big),\ n=\min(N,260)$

백분위는 (13)의 1일 창과 같은 midrank 정의이고, $\#\{u: f^A_{s,u}\ne0\} < 0.25\,n$ 이면 결측이다(`day_pct`
`scripts/sector_flow.py`, `TODAY_MIN_LIVE`). 수익률은 그날 $\bar r_{s,N-1}$, 종목[수] 는 그날 1일 집계가 있는 종목 수(Enter 목록 길이)이고
10 미만이면 `~`. **가속·미실현·풀림·G·추이·포텐셜·dW/dt 는 결측이다** — 시총 대비·회귀·차분·누적 경로가 하루에서 정의되지 않는다
(가속도 파생값이라 싣지 않는다). 기본 정렬 키(가속)가 전멸이므로 §2.17 폴백으로 임펄스 순이 되고 헤더가 그렇다고 적는다.
드릴다운은 `names[code].win["1"]` 이고 최근집중은 결측이다(분모가 하루).
`today` 가 없는 리포트에서는 `w` 가 이 창을 건너뛴다.

---

## 3. sw-flow — 종목 목록(`Enter`)과 전 종목 화면(`t`)

### 3.1 목록의 집합

**(39)** $\quad L=\{c:\ \text{sector}(c)=s^\star\ \wedge\ \text{market}(c)\in M\ \wedge\ \nu^{W'}_c\ne\varnothing\},\quad W'=\begin{cases}20 & \text{종합}\\ W & \text{그 밖}\end{cases}$

`State.names` `src/sectorflow/tui/flow_view.py:665-735`(`def names`). $|L|$ 은 (32)의 $n_s$ 와 정확히 같다. 제목 줄이 `섹터 · 종목 N개 · 20일 기준 · 정렬[…]`
형태로 창을 밝힌다(`src/sectorflow/tui/flow_view.py:1998-2000`). 투신·연기금 키가 아예 없는 옛 페이로드면 제목에 `옛 리포트(새 열 없음)` 를
붙인다 — 값이 0 인지로 판정하면 조용한 날에 거짓말을 한다(`legacy_names` `src/sectorflow/tui/flow_view.py:944-960`).

### 3.2 순매수[억]

**(40)** $\quad \phi_c=\nu^{W'}_c[A]$ [억원]

`src/sectorflow/tui/flow_view.py:687` · 열 `src/sectorflow/tui/flow_view.py:1710`. 기본 정렬은 **절댓값** 순이고 역순은 부호 오름차순(가장 많이 판 쪽
먼저)이다(`src/sectorflow/tui/flow_view.py:718-729`). 섹터를 움직인 건 산 쪽과 판 쪽 둘 다다.

### 3.3 누적[%]과 `-` 마커

**(41)** $\quad T=\sum_{c\in L}|\phi_c|,\quad \text{cum}_j=\dfrac{\sum_{u\le j}|\phi_{(u)}|}{T}\times100,\quad \text{cut}_j=[\text{cum}_j\ge80\ \wedge\ \forall u<j:\ \text{cum}_u<80]$

`_attach_cum` `src/sectorflow/tui/flow_view.py:1003-1021` · `CUM_CUT = 80.0` `src/sectorflow/tui/flow_view.py:242` · 열 `src/sectorflow/tui/flow_view.py:1711-1713`.
**순매수 절댓값 정렬에서만** 채운다 — 다른 순서의 누적은 뜻 없는 톱니다. 마커는 80% 를 처음 넘긴 행 하나에, 행 안 1칸 열로 긋는다.
실측: 전기/전자 386종목 중 7개가 80% 를 설명한다.

### 3.4 투신[억] · 연기금[억]

**(42)** $\quad \nu^{W'}_c[\mathrm{invtrt}],\quad \nu^{W'}_c[\mathrm{penfnd}]$

`src/sectorflow/tui/flow_view.py:690` · 열 `src/sectorflow/tui/flow_view.py:1714-1715`. 선택 주체와 무관하게 늘 기관 세부다. 기관 총액이 같아도
투신·연기금이 채운 것과 금투(헤지·차익이 섞인 자기매매)가 채운 것은 다른 이야기다. 「기관 대비 몫 %」는 분모가 상쇄로 0 근처에
가면 폭발해 버렸다. §1.5 때문에 두 열을 더해 기관 열을 검산하면 안 맞는다.

### 3.5 상대수익[%p]

**(43)** $\quad \text{rrel}_c=\nu^{W'}_c[\mathrm{ret}]-\Delta v_{s^\star}$ [%p]

`src/sectorflow/tui/flow_view.py:709-710` · 기준선 `_sector_ret` `src/sectorflow/tui/flow_view.py:962-979` · 열 `src/sectorflow/tui/flow_view.py:1716`.
기준선은 표의 `수익률[%]`(식 17)과 같은 값이다. 종합이면 20일 블록에서 꺼낸다. 섹터의 $x$ 를 종목에 내리지 않는다 — 그 $k,b$ 는 섹터
횡단면에서 적합된 것이라 종목 위에서는 적합된 적이 없다.

### 3.6 최근집중[%]

**(44)** $\quad \text{conc}_c=\dfrac{\nu^{5}_c[A]}{\phi_c}\times100$ [%]

`_conc` `src/sectorflow/tui/flow_view.py:981-1001` · `CONC_WIN = "5"` `src/sectorflow/tui/flow_view.py:192` · 열 `src/sectorflow/tui/flow_view.py:1717`.
분자는 늘 5일, 분모는 지금 보는 창이다. 20일 화면에서 ≈25 면 고르게 분산, ≈100 이면 최근 5일에 몰림, >100 이면 앞에서 팔다 방향을 튼 것,
음수면 최근 5일이 구간 전체와 반대다.

| 조건 | 처리 | 근거 |
|---|---|---|
| $W'=5$ · $W'=1$ | 결측(5일은 분자 = 분모라 늘 100, 1일은 분모가 분자보다 짧다) | `src/sectorflow/tui/flow_view.py:998` |
| 분모 절댓값 < 1억 | 결측 — 0.1억 반올림 오차가 비율에 그대로 실린다 | `CONC_MIN_DEN` `src/sectorflow/tui/flow_view.py:200` |
| 목록 전체 Σ절댓값(순매수) 중 그 줄의 몫 < 0.5% | 결측 — 볼 이유가 없는 줄 | `CONC_MIN_SHARE` `src/sectorflow/tui/flow_view.py:224`, `_gate_conc` `src/sectorflow/tui/flow_view.py:927-941` |
| 절댓값 > 999 | `>999` / `<-999` 로 적는다 | `CONC_CAP` `src/sectorflow/tui/flow_view.py:227`, `fmt_conc` `src/sectorflow/tui/flow_view.py:251-259` |

### 3.7 참여율[%]

**(45)** $\quad \text{part}_c=\phi_c\,/\,\nu^{W'}_c[tv]\times100$ [%]

`src/sectorflow/tui/flow_view.py:696` · 열 `src/sectorflow/tui/flow_view.py:1728`. 거래대금이 0 이거나 순매수가 결측이면 결측. "그 거래의 몇 %가 한 방향이었나" 다.
비율이라 유동성을 못 말하므로 거래대금과 짝이다 — 폭이 모자라 거래대금이 떨어지면 참여율도 같이 뺀다(`COL_PAIRS`
`src/sectorflow/tui/flow_view.py:1753`(`COL_PAIRS`), `fit_names` `src/sectorflow/tui/flow_view.py:1756-1766`).

### 3.8 시총대비[%p] · 시총[억] · 거래대금[억]

**(46)** $\quad a_c=\phi_c/C_c\times100$ [%p], $\quad C_c=\mathrm{cap}_c$, $\quad tv_c=\nu^{W'}_c[tv]$

`src/sectorflow/tui/flow_view.py:697` · 열 `src/sectorflow/tui/flow_view.py:1729-1734`. 시총이 없으면 결측. $C_c$ 는 식 (5)의 기준일 값이다.
시총대비는 섹터를 고른 뒤에는 대체로 작은 종목을 올리는 재정렬이라 열 순서 뒤쪽에 있다. `시총[억]`·`거래대금[억]` 은 섹터 표에서는
정렬 키(섹터 합계)이고 드릴다운에서는 종목 값이다.

### 3.9 선정[0~1](종목)

섹터 표 G 의 "관문 + 순위 평균" 구조를 종목 층에 옮긴 것이다(`_attach_pick` `src/sectorflow/tui/flow_view.py:750-834`).

**(47)** 후보 = $|\phi|$ 내림차순으로 훑어 **누적 80% 를 처음 넘긴 행까지** 중에서

$\quad \phi_c>0\ \wedge\ \text{invtrt}_c\ge0\ \wedge\ \text{penfnd}_c\ge0\ \wedge\ tv_c\ge100\text{억}\ \wedge\ \text{conc}_c,\ \text{rrel}_c,\ \text{part}_c\ \text{모두 결측 아님}$

**(48)** $\quad \text{pick}_c=\tfrac14\big(R(\text{invtrt}+\text{penfnd})+R(\text{conc})+R(-\text{rrel})+R(\text{part})\big)$, 후보가 2개 이상일 때만

후보 경계 `src/sectorflow/tui/flow_view.py:789-807` · `PICK_MIN_TV = 100.0` `src/sectorflow/tui/flow_view.py:748` · 후보 2개 미만이면 전부 결측
`src/sectorflow/tui/flow_view.py:808-809` · 점수 `src/sectorflow/tui/flow_view.py:829-834` · 열 `src/sectorflow/tui/flow_view.py:1708-1709`.
순위 $R$ 은 (30)과 같은 중간순위 $[0,1]$ 정규화다.

- 모집단 경계는 화면 정렬과 무관하게 $|\phi|$ 순서를 다시 만들어 정한다 — 선정점수로 정렬해도 모집단이 안 사라지게.
- `conc` 가 있어야 하므로 **5일 창에서는 선정이 하나도 안 나오고**, 몫 0.5% 미만 줄도 빠진다.
- 상대수익만 부호를 뒤집는다 — 섹터만큼 안 간 것을 좋게 본다.
- 근거 수준: 검정된 적 없는 탐색 점수다. 기본 정렬은 순매수다.

### 3.10 상세 패널

**섹터 표에서** — 제목(`섹터 · 종목 N개 · Enter 로 전체`), 4주체 줄, 순매수/순매도 상위 3개(`detail_lines` `src/sectorflow/tui/flow_view.py:1842-1877`).

**(49)** $\quad$ 4주체 줄 $=[\,F^{\text{개인}}_s,\ F^{\text{외국인}}_s,\ F^{\text{기관}}_s,\ F^{\text{기타법인}}_s\,]$ [억]

`_actors_line` `src/sectorflow/tui/flow_view.py:1889-1920`. 화면이 한 주체만 보여주므로 "누가 받았나" 를 이 줄이 답한다. 폭이 모자라면 뒤에서
자르되 고른 주체는 남긴다. 종합에서는 **20일 블록의 섹터 합**을 그대로 쓰고(`_actor_sums` `src/sectorflow/tui/flow_view.py:590-608`) 줄 끝에
`· 20일 기준` 을 단다. 잔여(−Σ4주체)는 여기 안 적는다 — 원장의 일이다.

**종목 목록에서** — 고른 종목의 투신·연기금을 겹치지 않는 구간으로 차분해 보인다(`_stock_detail_lines` `src/sectorflow/tui/flow_view.py:1809-1839`).

**(50)** $\quad$ `120-60` $=\nu^{120}_c-\nu^{60}_c$, `60-20`, `20-5`, `5` $=\nu^{5}_c$ — 한쪽이 결측이면 결측

`TREND_SEGS` `src/sectorflow/tui/flow_view.py:79-80` · `segment_flows` `src/sectorflow/tui/flow_view.py:93-112`. 중첩 창 넷을 나란히 두면 최근 5일이 네 번
세어지므로 차분한다. 부호에 좋다/나쁘다를 붙이지 않는다.

### 3.11 전 종목 화면(`t`) — 곱

**(51)** $\quad \text{곱}_c=G_{s(c)}\times\text{pick}_c$ — 둘 다 있을 때만, 곱 내림차순 고정

`all_picks` `src/sectorflow/tui/flow_view.py:879-925` · 열 `_ALL_COLS` `src/sectorflow/tui/flow_view.py:1926-1940` · 화면 `all_lines` `src/sectorflow/tui/flow_view.py:1947-1982`.
층 사이에서 평균을 안 쓰는 이유: 평균이면 아무도 안 사는 섹터의 1등이 모두가 사는 섹터의 3등을 이긴다. 곱은 둘 다 높을 때만 산다.
종합에서는 섹터 점수가 종합 G, 종목 점수가 20일 목록이라 두 층이 다른 구간을 보고, 제목이 그 사실을 적는다.

---

## 4. sw-ledger

원장은 `sector_numbers` 를 거치지 않고 `payload.json` 을 직접 읽는다. 관측된 것은 (날짜, 시장, 섹터, 주체)의 순매수 금액뿐이다. 그래서
주체 ↔ 섹터 이분 그래프만 그리고 섹터→섹터·주체→주체는 그리지 않는다 — 둘 다 주변합이 쌍별 흐름을 결정하지 않는다(4주체 주변합의
독립 3개로는 쌍별 6개가 3차원만큼 남는다). 그 문장이 모든 화면 최하단에 상주한다(`BANNER_TIERS` `src/sectorflow/tui/ledger_view.py:54-60`).

### 4.1 원장 표

구간은 5·20·60·120·260 거래일이고(`WINDOWS` `src/sectorflow/tui/ledger_view.py:46`), 페이로드보다 길면 잘린다(`src/sectorflow/tui/ledger_view.py:438-439`(`def window`)).

**(52)** $\quad d^A_{s,i}=\sum_{m\in M}f^A_{m,s,i},\qquad \Phi^A_s=\sum_{i=N-W}^{N-1}d^A_{s,i}$ [억원]

`daily` `src/sectorflow/tui/ledger_view.py:519-537` · `rows` `src/sectorflow/tui/ledger_view.py:590-643` · 열 `ledger_cols` `src/sectorflow/tui/ledger_view.py:828-847`.
금액 열 넷은 주체 선택과 무관하게 늘 나온다 — 주체 선택은 막대·최대일몫·전개·동시성에만 쓰인다.

### 4.2 잔여[억]

**(53)** $\quad \text{resid}_s=-\sum_{A\in\{\text{개인, 외국인, 기관, 기타법인}\}}\Phi^A_s$ [억원]

`residual` `src/sectorflow/tui/ledger_view.py:167-177` · `src/sectorflow/tui/ledger_view.py:610`. 오차가 아니라 **다섯째 주체**다 — 우리가 안 싣는
KRX 주체(기타외국인 등)의 순매수 추정치에 결측과 식 (1)의 환산 오차가 섞인 것. 4주체에 안분해 0 으로 만들지 않는다.
다섯을 다 담을 수 없는 폭(<71칸)에서는 표 대신 안내를 낸다(`LEDGER_MIN_W` `src/sectorflow/tui/ledger_view.py:823`, `src/sectorflow/tui/ledger_view.py:927-931`).

### 4.3 잔여몫[%]

**(54)** $\quad \text{gross}_s=\sum_A\sum_i|d^A_{s,i}|,\quad \text{rabs}_s=\sum_i\Big|\sum_A d^A_{s,i}\Big|,\quad \text{잔여몫}_s=\begin{cases}\text{rabs}_s/\text{gross}_s\times100 & \text{gross}_s\ge1\text{억}\\ \varnothing & \text{그 밖}\end{cases}$

`src/sectorflow/tui/ledger_view.py:619-621` · `_MIN_RATIO_GROSS = 1.0` `src/sectorflow/tui/ledger_view.py:115`. 합계가 아니라 **일별 절댓값**으로 잰다 — 구간
합계는 부호가 엇갈려 상쇄된다(20일·전시장 실측: 전기/전자 11배, IT 서비스 48배 차이).

### 4.4 최대일몫[%]과 `!` 마커

**(55)** $\quad \text{agross}_s=\sum_i|d^{A^\star}_{s,i}|,\quad \text{최대일몫}_s=\begin{cases}\max_i|d^{A^\star}_{s,i}|/\text{agross}_s\times100 & \text{agross}_s\ge1\text{억}\\ \varnothing & \text{그 밖}\end{cases}$

**(56)** $\quad \text{균등}=100/W$ [%], $\qquad \mathtt{!}=[\,\text{최대일몫}_s\ge3\times\text{균등}\,]$

`src/sectorflow/tui/ledger_view.py:622-628` · `uniform_spike` `src/sectorflow/tui/ledger_view.py:583-588` · `SPIKE_MARK_MULT = 3` `src/sectorflow/tui/ledger_view.py:820` ·
마커 `src/sectorflow/tui/ledger_view.py:950`. $A^\star$ 는 고른 주체다. 잔여몫의 분모는 4주체 전체 gross, 최대일몫의 분모는 고른 주체 gross 다 — 둘 다
하한은 1억이다. 금액 열이 억 단위 정수라 분모가 1억 미만이면 그 비율을 이루는 금액이 화면에 하나도 안 보인다.

### 4.5 종목[수] · 막대 · 절대크기

**(57)** $\quad n_s=\#\{c:\ \text{market}(c)\in M\ \wedge\ \text{sector}(c)=s\ \wedge\ \nu^{W}_c\ne\varnothing\}$, `~` $=[n_s<10]$

`n_stocks`·`_count_names` `src/sectorflow/tui/ledger_view.py:539-578` · `THIN_N = 10` `src/sectorflow/tui/ledger_view.py:103`. sw-flow 의 (32)와 같은 집합이다.
페이로드에 그 창의 종목 집계가 없는 구간(260일)은 페이로드 전 구간이 곧 그 창이므로 그 (시장, 섹터)의 종목을 다 센다. `names` 자체가
없는 옛 페이로드만 `n_by_sector` 로 떨어진다.

**(58)** $\quad \text{abs}_s=\sum_A|\Phi^A_s|$ — 정렬 전용(`src/sectorflow/tui/ledger_view.py:611`)

정렬은 절대크기 · 선택주체 · 섹터명 · 최대일몫 · 종목수 다섯이고 역순 토글은 없다(`SORTS` `src/sectorflow/tui/ledger_view.py:48-49`, `src/sectorflow/tui/ledger_view.py:630-637`).

**(59)** 오른쪽 끝 막대: $\ \text{scale}=\max_{s\in\text{화면}}|\Phi^{A^\star}_s|$, 반칸 단위 $=\min\!\big(\mathrm{round}(|\Phi|/\text{scale}\cdot2h),\,2h\big)$, $h=\min\!\big(12,\lfloor(\text{남는 칸}-2)/2\rfloor\big)$

`signed_bar` `src/sectorflow/tui/ledger_view.py:244-263` · 폭 `src/sectorflow/tui/ledger_view.py:936` · 눈금 `src/sectorflow/tui/ledger_view.py:944`. 0 이 가운데이고
표시 폭은 늘 $2h$ 다. 한쪽 끝이 지금 화면 안의 최대 절댓값이라 화면이 바뀌면 눈금도 바뀐다.

### 4.6 전개 — 누적[억] · 눈금[억] · 스파크라인

**(60)** $\quad \text{cum}_j=\sum_{i\le j}d^{A^\star}_{s,i}$ (구간 안), $\quad$ 누적[억] $=\text{cum}_{\text{마지막}}=\Phi^{A^\star}_s$

**(61)** 다운샘플: $\ \text{show}_j=\text{cum}\big[\lfloor (j+1)k/n\rfloor-1\big],\ j=0..n-1,\ k=|\text{cum}|,\ n=\min(W,\ \text{남는 폭})$

**(62)** $\quad$ 눈금[억] $=\max_j|\text{show}_j|$ (그려지는 점들로 잰다)

**(63)** $\quad \ell_j=\begin{cases}+2\ (⠛) & \text{show}_j/\text{눈금}>0.5\\ +1\ (⠒) & 0<\cdot\le0.5\\ 0\ (⠐) & \text{show}_j=0\ \text{또는 눈금}\le0\\ -1\ (⠤) & -0.5\le\cdot<0\\ -2\ (⣤) & \cdot<-0.5\end{cases}$

`timeline_lines` `src/sectorflow/tui/ledger_view.py:986-1031`(폭 `src/sectorflow/tui/ledger_view.py:1006`) · `downsample` `src/sectorflow/tui/ledger_view.py:224-241` ·
`spark_scale`·`spark` `src/sectorflow/tui/ledger_view.py:180-210` · 글자표 `src/sectorflow/tui/ledger_view.py:88`(`SPARK_SIGNED`). 뒤만 남기는 `values[-n:]` 는 시작점을 잃어
쓰지 않는다. 경계는 정수 산술이다(부동소수 반올림이 마지막 날을 떨어뜨린 적이 있다). **세로 한가운데가 0** 이다 — sw-flow 의
추이(§2.5)와 다른 점이고, 회계 화면이라 부호가 전부여서다.

### 4.7 동시성 — 평균상관 · 널 · β제거

**(64)** $\quad y_{s,i}=d^{A^\star}_{s,i}/C_s\times100$ [%p], $C_s>0$ 인 섹터만

`_norm_series` `src/sectorflow/tui/ledger_view.py:650-657`. $C_s$ 는 페이로드 기준일 섹터 시총이다(`def cap` `src/sectorflow/tui/ledger_view.py:580-581`).

**(65)** β제거(선택, 기본 꺼짐): $\ M_i=\dfrac{\sum_s C_s y_{s,i}}{\sum_s C_s},\quad \beta_s=\dfrac{\sum_i(M_i-\bar M)(y_{s,i}-\bar y_s)}{\sum_i(M_i-\bar M)^2},\quad \tilde y_{s,i}=y_{s,i}-\bar y_s-\beta_s(M_i-\bar M)$

`_detrended` `src/sectorflow/tui/ledger_view.py:659-679` · 키 `d`. 기본이 꺼짐인 이유: β제거 뒤 가장 음의 상관 쌍이 전부 전기/전자 ↔ 나머지라
구성적 인공물과 구분되지 않는다. 켜면 화면이 경고를 띄운다(`src/sectorflow/tui/ledger_view.py:1188-1189`).

**(66)** $\quad r_{ab}=\begin{cases}\dfrac{\sum_i(y_{a,i}-\bar y_a)(y_{b,i}-\bar y_b)}{\sqrt{\sum_i(y_{a,i}-\bar y_a)^2\sum_i(y_{b,i}-\bar y_b)^2}} & \text{두 분산 모두}>0\\ \text{NaN} & \text{그 밖(잴 수 없다)}\end{cases}$

**(67)** $\quad \bar r_a=$ NaN 이 아닌 $r_{ab}\ (b\ne a)$ 의 평균, $\quad$ 평균 열 $=\bar r_a\times100$

`_corr` `src/sectorflow/tui/ledger_view.py:266-273`(식은 `_dev` `src/sectorflow/tui/ledger_view.py:276-285`·`_corr_of` `src/sectorflow/tui/ledger_view.py:288-293`) · `_corr_ordered_of` `src/sectorflow/tui/ledger_view.py:345-365` · 열 `src/sectorflow/tui/ledger_view.py:1207-1210`.
분산 0 에서 0.0 이 아니라 NaN 을 돌려주는 것이 요점이다 — 0.0 은 `|r|<0.05` 와 같은 빈칸이라 "무상관" 과 "잴 수 없다" 가 구분되지 않았다.
행·열 순서는 평균상관 내림차순이고 NaN 인 섹터는 맨 뒤다. 그래서 이 화면에는 정렬이 없다(헤더 칩이 `순서[평균상관]`, `src/sectorflow/tui/ledger_view.py:799`).

**히트맵 한 칸** — 2글자, 첫 글자가 부호, 둘째가 세기. $\mathrm{lv}(r)=\mathrm{clamp}(\mathrm{round}(10r),-5,+5)$
(`heat_level` `src/sectorflow/tui/ledger_view.py:1046-1050`, `heat_cell` `src/sectorflow/tui/ledger_view.py:1058-1064`, `HEAT_RAMP` `src/sectorflow/tui/ledger_view.py:1043`).

| 조건 | 칸 |
|---|---|
| r = NaN | ` ?` |
| lv = 0 (곧 \|r\| < 0.05) | 빈칸 |
| 대각선 | 빈칸 |
| 그 밖 | `+`/`-` 뒤에 `⡀⣀⣤⣶⣿`(점 1·2·4·6·8 = \|lv\| 1~5). 0.5 에서 포화 |

**널 대조와 판정줄**

**(68)** $\quad \text{negfrac}(\{r\})=\dfrac{\#\{r<0,\ r\ne\text{NaN}\}}{\#\{r\ne\text{NaN}\}}$

**(69)** $\quad$ 널 $=\text{negfrac}\big(\{r(\sigma_{t_a}y_a,\ \sigma_{t_b}y_b)\}\big)$, $t\sim\mathrm{Unif}\{0..W-1\}$ 순환이동, 20회, seed 11

**(70)** $\quad$ 판정 $=\begin{cases}\text{"로테이션은 우연보다 드물다"} & \text{관측}<\text{널}-0.03\\ \text{"널과 다르지 않다 — 무늬를 읽지 마라"} & \text{그 밖}\end{cases}$

`neg_frac` `src/sectorflow/tui/ledger_view.py:330-342` · `_null_neg_frac_of` `src/sectorflow/tui/ledger_view.py:368-380`(seed `src/sectorflow/tui/ledger_view.py:370`) ·
`_NULL_SHIFTS = 20` `src/sectorflow/tui/ledger_view.py:116` · 판정 `comove_verdict_tiers` `src/sectorflow/tui/ledger_view.py:1076-1098`. $W<20$ 이면 상관을 아예 내지
않는다(`_MIN_CORR_N` `src/sectorflow/tui/ledger_view.py:104`). NaN 을 분자에서도 분모에서도 빼는 것이 (68)의 요점이다 — 관측·널·근거 스크립트
(`scripts/ledger_numbers.py`)가 같은 함수를 본다. 셋째 줄이 평균상관이 가장 낮은 4개 섹터를 이름과 값으로 남긴다(`comove_outliers`
`src/sectorflow/tui/ledger_view.py:1101-1120`(`def comove_outliers`)).

### 4.8 헤더 · 상태줄 · 한계 화면

**헤더** — `자금 원장 · 기준일 상태칩 · 시작~끝 (W거래일)` / `화면[·] 구간[·] 시장[·] 주체[·] 정렬[·]`(동시성이면 `순서[평균상관] β제거[켬/끔]`)
(`header_lines` `src/sectorflow/tui/ledger_view.py:789-805`). 상태칩은 sw-flow 와 같은 함수다(§5).

**상태줄** — 표를 봐도 알 수 없는 것만 적는다. ① `최대일몫(주체) 17.9% · 균등 5.0%`(값과 앵커는 붙어 있어야 비교가 된다) ② 잔여·잔여몫 —
그 열이 지금 화면에 없을 때만, 둘은 따로 판정 ③ 종목 수, 4주체 금액 — 역시 화면에 없을 때만. `status_line` `src/sectorflow/tui/ledger_view.py:1739-1796` ·
판정 `visible_columns` `src/sectorflow/tui/ledger_view.py:1699-1712`. 커서가 없는 화면(동시성·한계)에서는 줄을 비운다.

**한계 화면(`v`)** — 여덟 항목의 산문(`LIMITS` `src/sectorflow/tui/ledger_view.py:1239-1293`): 섹터→섹터 미관측 · 주체→주체 미식별 · 순매수는 소유권
이전이지 자금 유입이 아니다 · 종가 환산 근사 · 잔여의 정체와 크기 · 상관은 동시성이지 이동이 아니다 · 생존편향 · 주체 4분류의 거칢.
6번의 관측 범위(`{C3}` 등)는 글에 박지 않고 리포트를 열 때 시장 × 구간(≥20일) 전수로 잰다(`comove_ranges` `src/sectorflow/tui/ledger_view.py:746-784`,
`comove_claims` `src/sectorflow/tui/ledger_view.py:1362-1375`).

---

## 5. 리포트 상태 값

| 표시 | 뜻 | 근거 |
|---|---|---|
| `장중·미확정` | 기준일이 오늘이고 KST 17시 전 | `_finalized` `scripts/sector_flow.py:62-69`, `FINALIZED_KST_HOUR = 17` `scripts/sector_flow.py:53` |
| `마감·잠정` | 수집 창이 지났다(시계만 본다). 당일 수급은 잠정치다 | `src/sectorflow/tui/flow_view.py:1366-1376`(`status_chip`) |
| `마감·확정` | 화~토 08:20 확정 모드가 `daily_finalize`(`finalize_both`·`rebuild_adjusted`) 성공을 확인하고 확정치로 다시 만든 리포트. 페이로드에 `final: true`, 폴더에 `FINAL.txt` | `scripts/daily_report.sh:100-122`(`FINAL_OK`), `scripts/daily_report.sh:207-211`, `scripts/finalize_done.py:75-119`(`def finalize_done`), `scripts/sector_flow.py:859-861` |
| `⚠ 검증 실패(VERIFY_FAILED.txt)` | 저장 직후 자기 검증에 걸렸다. 게시는 하고 다음 크론이 다시 만든다 | `report_warnings` `src/sectorflow/tui/flow_view.py:1378-1396`, `scripts/daily_report.sh:192-205`(`VERIFY_RC`) |
| `⚠ MM-DD 수집 미완` | 더 뒤 기준일 폴더에 `INCOMPLETE.txt` — 완전성 관문이 막아 `latest` 가 전날에 머문다 | `src/sectorflow/tui/flow_view.py:1394-1395`, `scripts/daily_report.sh:137-165`(`GATE_RC`) |
| `⚠ N일 지난 리포트` | 기준일이 달력 4일 이상 지났다 | `STALE_DAYS = 4` `src/sectorflow/tui/flow_view.py:1401`, `staleness_warning` `src/sectorflow/tui/flow_view.py:1404-1410` |

잠정 리포트의 G 는 확정치에서 자주 바뀐다(지표 검증 Q3a: 게시본과 확정치의 G 순위가 크게 갈린 날 37%). 가속 순위는 ρ 0.999 로 안정적이다.

---

## 6. 실측 수치의 유효 범위

인용한 실측치는 전부 어느 날, 어느 범위에서 잰 것이다. 범위를 안 적으면 시장=전체에서만 잰 값이 "전 블록" 으로 일반화된다(PDF 판이
§2.9 에서 그랬다).

| 주장 | 값 | 잰 범위 |
|---|---|---|
| $\rho(\|x\|,U)$, $k>0$ | +1.0000 | 2026-08-28 리포트, 12블록 중 $k>0$ 인 11블록 |
| $k\le0$ 반례 | $\rho=-1.0000$ | 같은 날 5일·코스닥($k=-0.587$, $t=-0.24$, 22섹터 전부 $U<0$) |
| $x$ 와 $-\Delta v$ 의 순위상관 | 0.82~0.93(하위 5% 0.58~0.73) | 2026-11-08 지표 검증 Q3c. 옛 값 0.94~1.00 은 옛 정의(flu_rt·수정주가 시총) |
| k 의 \|t\|<2 비율 | 20일·전체 42~47%, 코스닥 60~70% | 같은 검증 Q3c |
| 가속 순위 게시본↔확정치 | ρ 중앙 0.999 | 같은 검증 Q3a |
| 기관 ≠ 세부 6개 합 | 행 8.9%, gross 2.9% | 260일, 674,480행 |
| 잔여몫 합계 vs 일별 | 11배 · 48배 | 20일·전시장, 전기/전자 · IT 서비스 |
| 잔여의 크기 | 거래대금 대비 0.0076% · gross 대비 0.0402% · 셀 gross 가중 평균 0.330% · gross 100억 이상 칸 최악 37.7% | 260일 |
| 음의 상관 쌍 비율 | 리포트를 열 때 잰다 | 한계 화면 6번이 그날 값과 범위를 적는다 |
| 생존편향의 크기 | 거래대금 0.151% · 기관 gross 0.203% | 2026-08-28, 72종목 |

$k$ 는 이 표에 없다. 매일 다시 적합되는 값이라 적는 순간 상수가 된다.

---

## 7. 한계

### 7.1 G 는 판정 불가, 종목 선정·곱은 검정 부호와 어긋난다

G 는 세 순위의 산술평균이다(식 30). 가중치 1/3 에 근거가 없고 세 성분은 물리 비유에서 왔다. 앞날 관계는 판정 불가(검정력 없음)다.
`*` 마커는 "세 부호 조건을 만족" 이라는 사실 진술이지 신호가 아니다. 종목 선정은 앞날 검정을 안 했고 그 `rank(투신+연기금)` 축은
K1(섹터 안 순위 IC 음)과 부호가 반대다. 그래서 셋 다 탐색 모드(`e`)에만 있다.

### 7.2 x 와 −Δv 가 거의 같은 것을 잰다

$x$ 는 OLS 잔차의 부호 반전이고 회귀 설명력이 낮다. 순위상관 0.82~0.93 이라 미실현·포텐셜·풀림·G 는 사실상 "많이 빠진 순" 을
다르게 부른 것에 가깝다. h20 에서 보인 약한 신호도 섹터 20일 반전이다.

### 7.3 관측 지표는 전부 동시기다

(19)의 회귀는 같은 구간의 유입과 수익률을 맞댄다. "사면 오른다" 는 동시기 진술이다. 예측 가설 한 형태(20일 가속 변화 상위 10%,
월리밸 롱온리)는 swing-it 에서 기각됐고([VERDICT](https://github.com/younghwan91/swing-it/blob/main/research/logs/inst_flow_accel/VERDICT.md)),
섹터 단위 수급 일반도 2026-11-08 검정에서 증거가 없었다.

### 7.4 생존편향이 남아 있다

페이로드가 소스를 키움으로 좁힌다(식 6). 키움 수급은 폐지 종목에 빈 응답을 '성공' 으로 준다. 폐지 종목의 마지막 자금흐름이 빠져
섹터 총량이 과소 측정되는 방향이다. 여기 적힌 숫자로 백테스트를 대신하면 안 된다.

### 7.5 회계 항등식이 닫히지 않는다

4주체 합이 0 이 아니다. 잔여에는 미분류 주체 · 결측 · 환산 오차가 구분 없이 섞여 있다. 전체로 뭉갠 0.0076% 는 안심을 주지만 한
칸에서는 37.7% 까지 간다.

### 7.6 이 앱이 애초에 답할 수 없는 것

섹터 → 섹터 이동, 주체 → 주체 이전은 관측되지 않는다. '순매수' 는 소유권 이전이다. 주체는 4분류뿐이다. 값 0 은 '관망' 이 아니라
'0 또는 미보고' 다. 기타법인은 동시성조차 널과 구분되지 않는다.

---

## 부록 A. 2026-08-31 PDF 대비 바뀐 것

| 자리 | PDF(2026-08-31) | 지금 |
|---|---|---|
| 앱·경로 이름 | `kq-flow`·`kq-ledger`, `src/kr_quant/tui/` | `sw-flow`·`sw-ledger`, `src/sectorflow/tui/` |
| 시총 (1.4) | 종가 × 그때 상장주식수(실제로는 `daily_bars` 수정주가 사용) | **원 종가**(`supply_demand.close`, 없으면 `daily_bars_raw`) × as-of 주식수, 스냅샷 뒤 분할·병합·감자를 다시 센다 |
| 종목 수익률 (1.3) | `flu_rt / 100` | **`daily_bars_adjusted` 종가 비.** 없으면 NaN, 가중치에서도 뺀다 |
| 거래대금 (1.2) | 종가 × `acc_trde_qty`(출처 불분명) | `acc_trde_qty × supply_demand.close` 로 명시 |
| k 의 표본 (2.7) | $\Delta v$·$C$ 가 있는 전 섹터 | **얇은(10종목 미만) 섹터 제외.** $\sum x=0$ 은 적합 표본 위에서만 |
| 조각별 α·조각 경계 (2.10) | 미기재 | 분모는 블록 기간말 시총, 셋째 조각은 $i_1$ 까지 |
| G (2.12) | 얇지 않은 전 섹터를 순위에 넣고 `*` 를 따로 판정 | **세 관문 통과 섹터만 순위.** 단독 통과는 0.5, `*` ≡ G 있음. 열 이름은 `G` → `선정` → `G`(2026-11-08). 탐색 모드에서만 |
| 종합 G (2.16) | 창별 G 의 등가중 평균, 통과 = 전 창 통과 | **통과한 창만의 평균**, 통과[구간] = 통과 창 수 / 섞은 창 수 |
| 새 열·화면 | — | 투신+연금5일(2.15), 시작 요약 배너(2.18), 종목 선정(3.9), 종목 구간별 추이 패널(3.10), 전 종목 곱 화면(3.11) |
| 4주체 줄(종합) | 종목 값을 더함 | 20일 블록의 섹터 합(반올림 차이 제거) |
| 상태 칩 (5) | `확정`/`장중·미확정` | `마감·확정`/`마감·잠정`/`장중·미확정` + 검증 실패·수집 미완·N일 지난 경고 |
| x↔−Δv 상관 | 0.94~1.00 | 0.82~0.93(2026-11-08, 고친 정의) |
| 근거 수준 | "검증된 적 없다" | 2026-11-08 검정 결과를 지표별로(맨 위 표) |

## 부록 B. 코드와 문서가 아직 갈리는 지점

화면·도움말·README 쪽은 맞췄다. 남은 것은 `scripts/sector_numbers.py`·`flow_view.py` 안의 주석이다.

1. **종목 선정의 단독 후보.** 섹터 G 는 단독 통과에 0.5 를 주는데(`scripts/sector_numbers.py:425-430`) 종목 선정은 후보가 하나면 점수가
   없다(`src/sectorflow/tui/flow_view.py:808-809`). 그런데 주석 `scripts/sector_numbers.py:399-400` 은 둘을 "같은 설계" 라 적는다.
2. **낡은 수치·참조.** x↔−Δv ρ 0.94~1.00 이 주석 `scripts/sector_numbers.py:418` 에 남아 있다(지금 정의로는 0.82~0.93).
   `docs/GUARDRAILS.md` 를 가리키는 주석(`src/sectorflow/tui/flow_view.py:70`, `src/sectorflow/tui/flow_view.py:1471`)은 swing-it 의 파일이다.
