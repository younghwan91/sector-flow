# Q2 계산 — 문서 정의만으로 numbers.html 을 다시 계산 (첫 검증, 재현용)

결과 문서: [`docs/validation/2026-11-08-results.md`](../../../docs/validation/2026-11-08-results.md) Q2 행과 "문서가 코드와 다르다"·
"통계적으로 약한 정의" 절. 사전등록: `2026-11-08-prereg.md`(bd27017).

`recompute.py` 는 `scripts/sector_numbers.py` 를 보지 않고 README §7·`docs/kqflow-formulas.pdf` 정의만으로 쓴 독립 구현이다.
게시 `payload.json` 을 입력으로 표를 다시 만들어 같은 날 게시 `numbers.html` 의 `const D` 와 셀마다 맞춘다. DB 는 쓰지 않는다.
문서가 정하지 않은 자리는 변형(`V`)으로 갈라 둔다.

## 돌리기

```bash
KR_QUANT_REPORTS=<게시본 폴더> uv run python research/validation/q2_calc/recompute.py                 # 문서 문언(기본)
KR_QUANT_REPORTS=<게시본 폴더> uv run python research/validation/q2_calc/recompute.py S_mode=pass     # G 를 통과 섹터끼리 순위(코드 동작)
KR_QUANT_REPORTS=<게시본 폴더> uv run python research/validation/q2_calc/recompute.py piece_cap=month S_mode=pass   # 그 밖의 변형
KR_QUANT_REPORTS=<게시본 폴더> uv run python research/validation/q2_calc/recompute.py fragility       # 얇은 섹터 포함/제외 k, G 통과 수
```

몇 초. 불일치 목록은 `out/mism_<S_mode>_<rank_ties>.json`.

## 입력 — 지금 reports/ 그대로는 안 된다

기준일 2026-09-15·10-15·11-06 의 `payload.json`·`numbers.html`. 지금 `reports/` 는 2026-11-09 에 48개 전부 다시 만들어 이 판이 아니다.
옛 판은 운영 머신의 `reports/.archive/` 에 있다. 09-15·10-15 는 `pre-rebuild-2026-11-08/`(numbers.html 은 11-08 01:37 에 고치기 전
코드로 다시 그린 판 — Q2 가 비교한 것이 이 판이다), 11-06 은 **18:10 잠정 게시본**이어야 하므로 `2026-11-06-provisional/` 이다.
세 폴더를 한 곳에 심볼릭 링크로 모아 `KR_QUANT_REPORTS` 로 준다.

```bash
A=~/git/sector-flow/reports/.archive; R=$(mktemp -d)
ln -s $A/pre-rebuild-2026-11-08/2026-09-15 $A/pre-rebuild-2026-11-08/2026-10-15 $R/
ln -s $A/2026-11-06-provisional $R/2026-11-06
KR_QUANT_REPORTS=$R uv run python research/validation/q2_calc/recompute.py
```

## 기대값 (2026-11-09, 위 `.archive` 입력으로 다시 돌려 accel·G 가 처음 실행과 같았다)

기본 변형: k·b·t 36/36, inst·forgn·indiv·etc·tv·cap·ret·x·U 100%, **accel 888/972 (91.36%)**, **G 413/972 (42.49%)**, 전체 94.10%.
G 불일치는 문서가 관문(a>0·x>0·ẍ>0)을 적지 않아서, accel 불일치는 시장에 없는 섹터를 0.0 으로 내는 결함 4 와 조각 분모 차이.
`fragility`: 10-15 `20|전체` k −0.59 → 얇은 섹터 제외 +14.42, 11-06 `120|전체` 3.39 → 8.14.
