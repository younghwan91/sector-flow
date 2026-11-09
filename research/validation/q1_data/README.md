# Q1 데이터 — 게시 payload 를 원자료에서 다시 만들기 (첫 검증, 재현용)

결과 문서: [`docs/validation/2026-11-08-results.md`](../../../docs/validation/2026-11-08-results.md) Q1 세 행과 결함 2·3 의 크기.
사전등록: `2026-11-08-prereg.md`(bd27017).

`recompute.py` 는 리포트 코드를 쓰지 않고 kr_quant 에서 payload 데이터 층(주체별 흐름·거래대금·시총·월말 시총·섹터 수익률)을
다시 만든다. `diff.py` 가 게시 payload 와 셀마다 맞춘다(금액 0.1억, 수익률 0.001%p). 옵션은 리포트 당시 정의를 흉내 낸다:
`--tv sd`(거래대금 = 누적거래량 × supply_demand 종가), `--ret flu`(수익률 = flu_rt — 결함 2), 시총은 `daily_bars`(수정주가, 결함 3).

## 돌리기 (레포 뿌리, R = 게시본 폴더)

```bash
V=research/validation/run.sh; D=research/validation/q1_data; R=~/git/sector-flow/reports
# 11-06 게시본(18:10 잠정판) — 기준일 T 잠정치 몫
$V $D/recompute.py --asof 2026-11-06 --tv sd --ret flu --out mine_1106_v2.json
$V $D/diff.py <11-06 잠정 payload.json> mine_1106_v2.json bad_1106_v2.json
$V $D/provisional_check.py 2026-11-06            # T 시총 → 잠정 종가 역산이 최종 종가·15:30 분봉과 같은가

# 과거 게시본 3 — 구성은 그날 payload 의 names 로(--members), 원인 분리용 변형 넷
for d in 2026-09-15 2026-10-15 2026-11-02; do
  $V $D/recompute.py --asof $d --tv sd --ret flu --out mine_${d}_stocks.json                         # 오늘 섹터 라벨
  $V $D/recompute.py --asof $d --tv sd --ret flu --members $R/$d/payload.json --out mine_${d}_pm.json
  $V $D/recompute.py --asof $d --tv sd --ret flu --unadjust --members $R/$d/payload.json --out mine_${d}_pmu.json
done
X=research/validation/q1_data/inputs/backfill_20261102_inserted_rows.json   # 2026-11-02 DART 백필이 넣은 주식수 행
$V $D/recompute.py --asof 2026-09-15 --tv sd --ret flu --members $R/2026-09-15/payload.json --exclude-shares $X --add-legacy-shares --out mine_2026-09-15_pmx.json
$V $D/recompute.py --asof 2026-10-15 --tv sd --ret flu --members $R/2026-10-15/payload.json --exclude-shares $X --out mine_2026-10-15_pmx.json
$V $D/recompute.py --asof 2026-11-02 --tv sd --ret flu --members $R/2026-11-02/payload.json --exclude-shares $X --out mine_2026-11-02_pmx.json
for d in 2026-09-15 2026-10-15 2026-11-02; do for v in stocks pm pmu pmx; do
  $V $D/diff.py $R/$d/payload.json mine_${d}_$v.json bad_${d}_$v.json | grep -E 'TOTAL|CORE|bad by date'
done; done
KR_QUANT_REPORTS=$R $V $D/classify.py            # 불일치를 원인별로: T 잠정치·재수집 창·주식수 정정(b1)·미해명(b2)

# 손검산 표본, 결함 크기
$V $D/make_sample.py <11-06 잠정 payload.json>   # → out/sample_15cells.json (haiku-verifier 15개 입력)
$V $D/flu_bias.py                                # 결함 2: flu_rt 섹터 20일 수익률 vs 종가 비
$V $D/cap_adjusted_vs_raw.py                     # 결함 3: daily_bars ≠ daily_bars_raw 몫, 코스닥 종이/목재
```

상대 경로의 `--out`·mine·bad 는 `out/` 기준이다. recompute 한 번에 수십 초.
`inputs/backfill_20261102_inserted_rows.json` 은 quant-airflow 의 2026-11-02 DART 주식수 백필이 넣은 1,675행(code, date, 주식수,
knowledge_date, source) — DB 에서 "언제 들어온 행인가" 를 알 수 없어 그날 백필 기록을 그대로 옮겼다.

## 입력 — 지금 reports/ 그대로는 안 된다

지금 `reports/` 는 2026-11-09 에 48개 전부 다시 만들어 이 판이 아니다. 옛 판은 운영 머신의 `reports/.archive/` 에 있다.
11-06 은 18:10 잠정 게시본이어야 하므로 `2026-11-06-provisional/`, 09-15·10-15·11-02 는 `pre-rebuild-2026-11-08/` 이다.
위 명령의 `R` 을 그 폴더로 준다. 이 입력으로는 다시 돌려 보지 않았다(2026-11-09 에 Q2 만 확인).

## 기대값

- 11-06: `TOTAL 182740/183225 = 99.7353%`, 불일치 485셀 전부 기준일 T(`bad by date` 2026-11-06 438 + 2026-11 월말 시총 47).
  2026-11-08 에 스크래치 백업의 잠정판으로 다시 돌려 같은 숫자를 확인했다.
- 과거 게시본 3(`classify.py`): 주식수 정정(b1) 14~17천 셀, 미해명(b2) 16셀(09-15, 최대 0.026%p).
- `flu_bias.py`: 섹터 20일 수익률 |flu − 종가 비| 중앙 09-11 까지 0.014%p, 11-05 까지 1.876%p.
- `cap_adjusted_vs_raw.py`: 2025-10-15~2026-11-05 `daily_bars` 행의 7.8% 가 `daily_bars_raw` 와 다르다.

과거 게시본 비교는 오늘 DB 와 맞추므로 그 뒤 주식수·수정주가가 또 바뀌면 b1 셀 수가 달라진다(원인 분류는 같아야 한다).
과거 게시본 3 과 표본·결함 크기 스크립트는 이번 이관에서 다시 돌리지 않았다.
