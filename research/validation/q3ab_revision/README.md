# Q3a 잠정치 민감도 · Q3b 종가 환산 근사 (첫 검증, 재현용)

결과 문서: [`docs/validation/2026-11-08-results.md`](../../../docs/validation/2026-11-08-results.md) Q3a·Q3b 행.
사전등록: `2026-11-08-prereg.md`(bd27017).

- **Q3a**: 게시본(기준일 T 잠정치로 만든 18:10 판) vs 오늘 DB(확정치)로 같은 기준일을 다시 만든 것 — 가속·G 순위가 얼마나 흔들리나.
- **Q3b**: 기관 금액 = 순매매 수량 × supply_demand 종가(리포트) 가 × VWAP·× 정규장 종가와 얼마나 다른가.

리포트 코드는 첫 검증 당시 판(bd27017 — 결함 1~3 포함)을 `git show` 로 `out/report_code/` 에 꺼내 쓴다(복사본을 두지 않는다).

## 돌리기 (레포 뿌리)

```bash
V=research/validation/run.sh; D=research/validation/q3ab_revision
export KR_QUANT_REPORTS=<게시본 폴더>     # 기준일 2026-08-27 ~ 11-06 의 48개 폴더 (아래 "입력")
$V $D/long_load.py                 # 48 기준일을 덮는 307거래일 한 번 적재 → out/long.pkl (~20초)
$V $D/slice_build.py               # 기준일마다 잘라 payload → build (8 프로세스) → out/slices/<asof>.num.pkl
$V $D/pub_build.py                 # 게시 payload 에 같은 build, 게시 numbers.html 의 표 → out/slices_pub · slices_html
$V $D/q3a_compare.py               # 블록별 flip·ρ·G 통과 집합 차이 → out/q3a_*.csv/pkl
$V $D/q3a_summary.py               # 판정 숫자
$V $D/q3b.py                       # Q3b → out/q3b_blocks.csv
$V $D/fresh_build.py 2026-10-13 fresh_2026-10-13.json   # 충실성: sector_flow.main() 을 기준일 컷오프로 통째로(슬라이스와 같은가)
```

## 입력 — 지금 reports/ 그대로는 안 된다

48개 기준일 폴더의 `payload.json`(그날 18:10 게시)·`numbers.html`. 지금 `reports/` 는 2026-11-09 에 48개 전부 다시 만들어 이 판이 아니다.
운영 머신의 `reports/.archive/pre-rebuild-2026-11-08/` 47개(numbers.html 은 11-08 01:37 에 고치기 전 코드로 다시 그린 판 — `html` 쌍이
비교한 것이 이 판이다)와, 11-06 은 옛 18:10 판 `reports/.archive/2026-11-06-provisional/` 을 한 폴더에 심볼릭 링크로 모아
`KR_QUANT_REPORTS` 로 준다. 이 입력으로는 다시 돌려 보지 않았다(2026-11-09 에 Q2 만 확인).

## 기대값

- Q3a(`q3a_summary.py`): 20일 블록 세 시장 동시 통과 기준일 몫 **0.625 (30/48)** → 합격선 0.90 불합격. 가속 ρ 중앙 0.999,
  G ρ 하위 10% 0.79~0.97.
- Q3b(`q3b.py`): 11-06 J_v(VWAP) 상대차 중앙 전체 0.9%·코스닥 3.8%. 48일 중 통과 거래소 100%, 전체 98%, 코스닥 71%.

2026-11-08 에 long_load → slice_build(10-13·11-06) → q3b 를 다시 돌려 두 슬라이스와 q3b 출력 전체가 처음 실행과 같음을 확인했다.
pub_build·q3a_compare 는 다시 돌리지 않았다.
