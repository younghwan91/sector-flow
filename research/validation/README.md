# 검증 연구 재현 스크립트

`docs/validation/` 결과 문서의 숫자를 다시 만드는 스크립트. 리포트·화면 코드가 아니고 CI(ruff·pytest 범위) 밖이다.
DB 는 읽기 전용 세션으로만 연다.

| 폴더 | 재현하는 것 | 결과 문서 | 정의 |
|---|---|---|---|
| [`q3cd/`](q3cd/) | Q3c 추정 안정성, **Q3d 앞날 순위 IC 재검** (+ 첫 실행 `--variant v1`) | `2026-11-08-results.md` | 정본(고친 정의). v1 은 옛 정의 — 재현용 |
| [`usefulness/`](usefulness/) | 수급 정보 쓸모 12칸 발견·확인, 리뷰 재계산 | `2026-11-08-usefulness-results.md` | 정본(고친 정의) |
| [`q1_data/`](q1_data/) | Q1 게시 payload ↔ 원자료, 결함 2·3 크기 | `2026-11-08-results.md` | 첫 검증 — 재현용 |
| [`q2_calc/`](q2_calc/) | Q2 문서 정의만으로 numbers.html 재계산 | `2026-11-08-results.md` | 첫 검증 — 재현용 |
| [`q3ab_revision/`](q3ab_revision/) | Q3a 잠정치 민감도, Q3b 종가 환산 근사 | `2026-11-08-results.md` | 첫 검증 — 재현용(bd27017 코드) |

## 공통

- 레포 뿌리에서 `research/validation/run.sh <스크립트> [인자]` — `KR_QUANT_DB` 가 없으면 `~/git/quant-airflow/.env` 를 불러
  `uv run --extra pg --with scipy python` 으로 돌린다. DB 를 안 쓰는 스크립트(q2_calc)는 `uv run python` 으로 충분하다.
- 산출물은 `<폴더>/out/`(gitignore). `SF_VALIDATION_OUT=<경로>` 를 주면 `<경로>/<폴더 이름>/` 로 간다.
- 게시본은 `KR_QUANT_REPORTS`(없으면 `<레포>/reports`). 첫 검증 셋(q1·q2·q3ab)은 **그때 게시본**이 입력이라
  지금 `reports/` 와 다를 수 있다 — 각 README 의 "입력" 을 본다.
- 리포트 코드는 복사하지 않는다. 정본은 작업 트리의 `scripts/sector_numbers.py` 를 import 하고, 옛 판이 필요한 곳은
  `vcommon.report_code(rev)` 가 `git show <rev>:…` 로 `out/report_code/` 에 꺼낸다.
- haiku-verifier 가 독립 재계산할 때 받은 정의는 `q3cd/VERIFY_RECIPE.md`·`usefulness/VERIFY_RECIPE.md` 에 있다
  (verifier 가 쓴 일회용 스크립트는 옮기지 않았다).
- 숫자는 DB 가 바뀌면(뒤 날짜 추가·주식수 정정) 달라진다. 각 README 의 기대값은 2026-11-08 DB 상태 기준이다.
