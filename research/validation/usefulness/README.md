# 수급 정보 쓸모 — 12칸 발견·확인 (정본)

결과 문서: [`docs/validation/2026-11-08-usefulness-results.md`](../../../docs/validation/2026-11-08-usefulness-results.md).
사전등록: `2026-11-08-prereg-usefulness.md`(b702dc4). 발견 = 기준일 ≤ 2022-12-31, 확인 = 2023-01-01 ~.

섹터 지표 S1~S3(섹터 순매수 ÷ 섹터 시총)와 종목 지표 K1~K3(종목 순매수 ÷ 시총, 섹터 안 순위)의 앞날 5·20일
순위 IC 를 5거래일 격자에서 재고, 합산 IC + Newey-West t 로 판정한다. 정의는 고친 정의(PR #5) — `../q3cd/load_long.py` 와 같다.

## 돌리기

레포 뿌리에서:

```bash
V=research/validation/run.sh; D=research/validation/usefulness
$V $D/load.py        # DB → out/panels.pkl (종목 × 거래일, ~50초, 360MB)
$V $D/build.py       # → out/sector.csv.gz · stock.csv.gz (~25초)
$V $D/discovery.py   # 발견 구간만 읽어 판정·부호 고정 → out/discovery.json (~10초)
$V $D/confirm.py     # discovery.json 의 부호로 확인 구간 판정 + 기술 표 → out/confirmation.json · spread.csv · ic_by_*.csv
$V $D/samples.py     # 진단 + haiku-verifier 표본 → out/sample_S.csv · sample_K.csv
```

`discovery.py` 는 확인 구간 행을 읽지 않는다. `confirm.py` 는 발견 부분이 discovery.json 과 1e-12 안에서 같은지 먼저 확인한다(assert).
순서가 사전등록의 눈가림이다 — confirm 을 discovery 보다 먼저 돌리지 않는다.

리뷰 재계산(quant-reviewer, 결과 문서 "확인된 것의 뜻" 1~4):

```bash
$V $D/review_features.py        # out/review_K.pkl: 과거 20·5일 수익률, t+1 진입 수익률, log 시총
$V $D/review_ic.py base         # 과거 20일 수익률·log 시총 통제 IC (뜻 2)
$V $D/review_ic.py nz           # K1≠0, K1>0 만 (뜻 1·2)
$V $D/review_ic.py skip         # t+1 종가 진입 (뜻 2)
$V $D/review_ic.py neg          # K1<0 만
$V $D/review_zero.py            # K1=0 몫, 섹터 안 K1·과거 20일 순위상관 (뜻 2 "+0.23")
$V $D/review_quintile.py        # 섹터 대비 초과수익 K1 5분위 표, 순매수 상위 회피 (뜻 1·4)
```

## 기대값 (2026-11-08 DB 로 다시 돌려 확인 — discovery.json·confirmation.json·sector/stock CSV 가 처음 실행과 같았다)

| 칸 | 발견 IC (NW t) | 확인 IC (NW t) |
|---|---|---|
| K1 h5 | −0.0231 (−7.38) | −0.0204 (−6.14) |
| K1 h20 | **−0.0320** (−7.96) | −0.0289 (−6.58) |
| K2 h5 / h20 | −0.0150 (−4.29) / −0.0231 (−5.08) | −0.0074 (−2.06) / −0.0081 (−2.17) |
| S1~S3, K3 | 전부 발견 탈락(\|t\| < 2) | — |

m(발견 통과 칸) = 4, 확인 α = 0.05/4 = 0.0125. K1 두 칸 확인, K2 미확인.

## 한계

- 현재 상장 종목만(상장폐지 종목 없음) — 결과 문서 "생존편향이 이 결과에 유리한 방향일 수 있다".
- 독립 재계산 지시문은 `VERIFY_RECIPE.md`(haiku-verifier 15건에 준 정의). 섹터 앞날 수익률 정의는 `../q3cd/VERIFY_RECIPE.md`.
- 데이터가 쌓이면 확인 구간 끝이 늘어 숫자가 바뀐다. 같은 숫자는 2026-11-08 DB(마지막 거래일 2026-11-06)에서만 확인했다.
