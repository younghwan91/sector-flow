# BYO Phase 3 — 자기 키움 키로 첫 지도까지, 실측

실측 2026-11-23(월) 21:07~21:20 KST. `docs/byo/quickstart.md` 의 명령을 그대로 돌렸다(소유자 키, 운영 수집과 같은 예산 —
`deploy/kiwoom_busy.simnode.toml` 창 밖, `--max-calls` 로 상한). 실데이터 숫자는 적지 않는다 — 일치 여부·호출 수·시간만.

## 한 일

| 단계 | 명령 | 호출 | 시간 |
|---|---|---|---|
| 종목 목록 | `sector-flow-collect --env-file … --busy-file deploy/kiwoom_busy.simnode.toml --max-calls 50 universe --db byo.db` | 토큰 1 + ka10099 2 | 1초 남짓 |
| 지도 전용 수집(2섹터) | `… --max-calls 600 backfill --db byo.db --map-only --sectors "운송/창고,오락/문화"` | 토큰 1 + ka10099 2 + ka10059 192 | 3분 34초 |
| 지도 | `sector-flow-byo map --db byo.db --out map` | 0 | 수 초 |
| 대조 조각(kr_quant 읽기 전용) | `python -m sectorflow.collect.equiv slice --sectors … --like byo.db --out krq.db` | — | — |
| 행 대조 | `python -m sectorflow.collect.equiv rowdiff byo.db krq.db` | — | — |

- 종목 목록: 거래소·코스닥 2,637종목, 업종 26개(업종 없는 36종목은 노트로 남는다).
- 수집: 96종목 × 120거래일 = `supply_demand` 11,520행, 실패 0.
- 지도: `map.html`·`payload.json`(수급만) 생성, 브라우저로 열린다. Playwright 가 없으면 카드는 건너뛴다(안내가 뜬다).

## 결과 — 지난 날은 kr_quant 와 같다

`supply_demand` 11,520행이 양쪽에 다 있다(한쪽에만 있는 행 0). 열별 차이는 **전부 수집한 그날(2026-11-23) 하루에만** 있고,
그 앞 119거래일은 모든 열이 같다(주체 순매수 수량·종가·등락률·거래량).

그날의 차이는 수집 시각 차이다 — kr_quant 는 16시 `daily_collection` 이 받은 값이고 BYO 는 21시에 받았다. 그 사이 들어온 KRX
애프터마켓 체결이 21시 값에만 있다(`CLAUDE.md` 데이터 함정: 09-14 뒤 통합값). 그래서 BYO 사용자는 **장 마감 당일 값을 잠정으로**
보고, 다음 날 `daily` 가 다시 받는 값(확정)을 쓰는 것이 맞다 — 운영 배치의 잠정(18시)·확정(다음 날 08시)과 같은 구조다.

## 판정

- Task 52 완료 조건 "문서 명령 그대로 두 섹터 조각에서 지도가 뜨고, kr_quant 판과 데이터 차 0" — **지난 날 기준 충족**, 당일은 잠정 차이(설명됨).
- 다른 증권사 어댑터(`--broker kis|ls|toss|db|nh`)는 이 대조를 할 키가 없어 여전히 미검증이다(`docs/byo/brokers.md` §6.2).
