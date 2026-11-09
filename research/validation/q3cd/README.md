# Q3c·Q3d — 추정 안정성과 앞날 순위 IC (재검 정본 + 첫 실행)

결과 문서: [`docs/validation/2026-11-08-results.md`](../../../docs/validation/2026-11-08-results.md) 의 Q3c 행·Q3d 행과
"Q3d 재검 — 고친 정의로" 절. 사전등록: `2026-11-08-prereg.md`(bd27017) + `2026-11-08-prereg-q3d-rerun.md`(6ca326a).

과거 2018~2026 을 기준일마다 260거래일 창으로 잘라 리포트의 `sector_numbers.build()` 를 그대로 돌리고(5거래일 격자),
20일 블록의 가속·x·임펄스·G 와 섹터 앞날 5·20일 수익률의 순위 IC 를 잰다.

## 돌리기

레포 뿌리에서(`run.sh` 가 `~/git/quant-airflow/.env` 를 불러 `uv run --extra pg --with scipy python` 으로 돌린다):

```bash
V=research/validation/run.sh; D=research/validation/q3cd
$V $D/load_long.py                       # DB → out/long.pkl              (~50초, 60MB)
$V $D/slice_build.py --code-rev e8d8d12  # → out/rows.csv.gz·blocks.csv·fwd.csv (~40초)
$V $D/analyze.py                         # Q3c 표 + Q3d 사전등록 판정 → out/q3c_*.csv·q3d_*.csv (~1분, 널 1000회)
$V $D/analyze.py --mkt 거래소            # 보조: 시장별 → out/거래소/  (코스닥도 같게)
$V $D/extra.py                           # 기술 민감도(합산 NW, 관문 없는 G, 2018-23 vs 2024+)
$V $D/hac.py                             # 리뷰 재계산: 합산 IC 의 HAC t·부트스트랩·−ret20·연도별
KR_QUANT_REPORTS=~/git/sector-flow/reports/.archive/pre-rebuild-2026-11-08 $V $D/panel_check.py   # 리뷰 재계산: 11-06 패널 = 확정 게시본
```

`--code-rev` 를 빼면 이 작업 트리의 `scripts/sector_numbers.py` 를 쓴다(복사본 없음). 문서 숫자는 e8d8d12(PR #5) 판으로 냈다.
DB 는 읽기 전용 세션(`default_transaction_read_only=on`, `statement_timeout` 120초)으로만 연다.

## 기대값 (2026-11-08 DB 로 다시 돌려 확인 — rows·blocks·fwd·q3d_ic_summary 가 처음 실행과 비트 단위로 같았다)

Q3d 재검, offset 0(사전등록 1차), `out/q3d_ic_summary.csv`:

| 지표 | h | N | mean_ic | null_p_2s |
|---|---|---|---|---|
| accel20 | 5 | 430 | **0.0140** (0.013955) | 0.185 |
| x20 | 5 | 430 | 0.0118 | 0.233 |
| impulse20 | 5 | 430 | 0.0114 | 0.246 |
| accel20 | 20 | 107 | 0.0089 | 0.662 |
| x20 | 20 | 107 | −0.0134 | 0.496 |
| impulse20 | 20 | 107 | 0.0217 | 0.305 |
| G20 | 5 | 82 | 0.0154 | 0.730 |
| G20 | 20 | 19 | 0.0405 | 0.686 |

- Q3c(`out/q3c_k_t.csv`·`q3c_x_vs_negret_summary.csv`): 문서의 "x↔−Δv ρ 0.82~0.93" 은 이 표의 `rho_all_med`.
  (Q3c 행의 k |t|<2 몫 42%·47% 는 첫 실행 숫자다 — 아래 v1.)
- `hac.py`: x20 h20 합산 IC 0.036, HAC t 1.8~2.2, −ret20 합산 0.034, x·−ret20 IC 시계열 상관 0.88.
- `panel_check.py`: 11-06 `20|전체` 27섹터 a·x·inst·ret·G 최대차 ~4e-12, k 14.927·t 3.86.
  이 검사는 `reports/2026-11-06` 이 확정 모드 판(11-08 11:37)일 때의 숫자다. 그 판은 지금 `reports/.archive/pre-rebuild-2026-11-08/2026-11-06` 에 있다
  (`reports/` 는 2026-11-09 에 다시 만들었다).

데이터가 쌓이면(뒤 날짜 추가·주식수 정정) 숫자가 바뀐다. 비트 단위 재현은 2026-11-08 DB 상태에서만 확인했다.

## 첫 실행 (v1) — 옛 정의(결함 2·3 포함) — 재현용

결과 문서 Q3d 재검 표의 "IC(첫 실행)" 열과 Q3c 행(k |t|<2 42%·47%)을 낸 판이다. 정본이 아니다.

```bash
$V $D/load_long_v1.py                    # 수익률 = flu_rt, 시총 = 수정주가 × 주식수 → out/v1/long.pkl
$V $D/slice_build.py --variant v1        # 리포트 코드 bd27017 판을 git 에서 꺼내 씀(out/v1/report_code/)
$V $D/analyze.py --variant v1            # → out/v1/
```

2026-11-08 에 다시 돌려 rows·blocks·q3c_k_t 가 처음 실행과 같았다: 1차 IC accel20 h5 0.014 · x20 h5 0.010 · impulse20 h5 0.009 ·
accel20 h20 0.001 · x20 h20 −0.012 · impulse20 h20 0.018 · G20 h5 −0.017 · G20 h20 0.166, 20|전체 k |t|<2 42%(겹침)·47%(비겹침).

## 정의와 한계

- 유니버스: `supply_demand.source` 가 kiwoom + `kiwoom_backfill_2026-10-11/12` (리포트는 kiwoom 만 — 최근 260일에서는 같다).
  현재 상장 종목·현재 섹터 라벨(생존편향). 앞날 수익률 정의는 `VERIFY_RECIPE.md`(haiku-verifier 독립 재계산 지시문).
- 앞날 창 첫날 수익률 close(t)→close(t+1) 은 저녁 신호로 잡을 수 없다(문서 한계 그대로).
