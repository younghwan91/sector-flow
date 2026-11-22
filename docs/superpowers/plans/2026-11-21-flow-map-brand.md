# 수급 지도·브랜딩·후원 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 리포트 폴더에 움직이는 수급 지도(`map.html`·`map.png`)를 더하고, 레포를 합성 데모로 브랜딩한 오픈소스 도구로 세우며, BYO 로 각자 자기 키에서 지도를 띄우게 한다.

**Architecture:** 순수 함수 모듈 `sectorflow.flowmap` 이 페이로드에서 이분 그래프(주체↔섹터) 데이터를 짓고, `scripts/sector_map.py` 가 그것을 템플릿 `sector_map.html`(바닐라 SVG 띠 + canvas 입자)에 심는다. 배치·데모·BYO 가 같은 경로를 탄다. 공개 표면(README·`site/`)은 데모에서만 짓고 검사가 지킨다.

**Tech Stack:** Python 3.11+ 표준 라이브러리(모듈), 바닐라 JS/SVG/canvas(외부 스크립트 없음), Playwright(카드·스모크, 기존 `playwright==1.48.0`), GitHub Actions/Pages.

**Spec:** `docs/superpowers/specs/2026-11-21-flow-map-brand-design.md` — 실행자는 계획과 함께 반드시 읽는다. 레포 `CLAUDE.md` 도.

**완료 조건(한 줄):** 아래 작업이 PR 로 CI 초록 머지되고, 데모·실 리포트에 `map.html`·`map.png` 가 생겨 `verify_report.py --layers M` 이 통과하며, Pages 랜딩이 데모로 뜨고 공개 표면 검사가 초록이다.

## Global Constraints

- 그리는 간선은 (주체, 섹터)뿐. 섹터→섹터·주체→주체 연결을 데이터·그림·글·애니메이션 어디서도 만들지 않는다(spec §3.1). 입자는 노드를 통과하지 않는다.
- 잔여 = `−Σ(indiv, forgn, inst, etc)` 를 별도 노드로 보인다. 4주체에 안분하지 않는다.
- 단위 억원, 표지 `sectorflow.basis.DATA_BASIS`(짧은 것은 `DATA_BASIS_SHORT`), 데모는 `DEMO_NOTICE`.
- 말: `wording.BANNED` 금지 + 지도·카드·랜딩·README 머리는 `wording.MAP_AVOID`(유입·유출·몰림·쏠림·회전·성지) 금지. 글은 주변합만("가상전자 — 개인 −2,927억 · 기관 +2,417억").
- 헤드라인·순위·극단은 `sectorflow.summary` 를 쓴다 — 다시 정하지 않는다.
- 외부 스크립트 없음. 글꼴은 Google Fonts `<link>`(wheel 은 `offline_templates.strip_remote` 가 뗀다). 분석·쿠키 없음.
- 공개 표면(README·`docs/images/`·`docs/brand/`·`site/`)에는 데모 산출물만. 실데이터 섹터명이 나오면 실패.
- 새 검사 → `tests/mutations.toml` 변이(없으면 `# mutation-exempt: <이유>`). 코드 인용 이동 → `scripts/docs_refs.py --fix`.
- 화면·웹 변경 → `uv run python scripts/verify_merge.py <워크트리> --only render,pty`. 그림 변경 → `scripts/demo_shots.py` 다시 찍기.
- 배치에 붙는 새 산출물은 실패해도 리포트를 막지 않는다(`SIDE_FAIL` 패턴, `daily_report.sh:397` 다이제스트와 같다).
- 커밋: 한국어 conventional, 레포 신원, `Co-Authored-By` 트레일러. PR 하나 = 작업 하나. 머지는 CI 초록일 때만.

## Review Focus

1. **한 주체만 있는 날·섹터**(예: 개인만 순매도, 나머지 0): 간선 0 인 주체 노드는 그리지 않고, 섹터 노드 높이는 0 이 아닌 쪽 합 — Task 6 `test_zero_edges_dropped`.
2. **잔여가 섹터 순매수보다 큰 칸**(얇은 섹터·결측): 잔여 노드가 받는 쪽에 서고 보존식이 맞는다 — Task 6 `test_resid_side_follows_sign`.
3. **창이 날짜 수보다 길 때**(데모 260일 중 120일은 되지만 BYO 20일 폴더): 그 창은 빠지고 UI 가 버튼을 감춘다 — Task 11 `test_windows_beyond_dates_skipped`, Task 18.
4. **모든 섹터가 꼬리로 들어가는 문턱**(작은 시장·코스닥 1일): 최소 `MIN_SHOWN=5` 섹터는 남는다 — Task 6 `test_tail_keeps_minimum`.
5. **글꼴이 없는 설치본**: 라벨 폭을 글꼴에 기대지 않아 390px 에서 겹치지 않는다 — Task 23 스모크 `--offline-fonts`.

---

## 진행 원장

| # | 작업 | 부서 | PR | 상태 |
|---|---|---|---|---|
| 1 | 설계·시안·계획 문서 | 기획 | #50 | 머지 |
| 2 | 후원·공개 정책 줄(CLAUDE.md·README) | 기획 | #51 | 머지 |
| 3 | `wording.MAP_AVOID` | 개발 | #52 | 머지 |
| 4 | 공개 표면 검사 | 개발 | #64, #87(3.11 glob) | 머지 |
| 5–12 | 데이터 모델 `flowmap` | 개발 | #57 | 머지 |
| 13–25 | 지도 페이지 | 개발+디자인 | #57, #85(시간 흐름 보기) | 머지 |
| 26–32 | 배치·검증 통합 | 개발 | #58, #60, #68(층 M), #78(카드), #84(스모크) | 머지 |
| 33–40 | 브랜드 | 브랜딩 | #62, #63, #66, #69, #70, #75, #77, #82, #83, #86, #81(README 머리) | #81 외 머지 |
| 41–46 | 후원·커뮤니티·랜딩 | 브랜딩 | #53–#56, #72, #79 | 머지 |
| 47–52 | BYO | 개발 | #61, #65, #67, #71, #73, #74 | 머지 — 52(실키 대조)는 소유자 확인 대기 |
| 55–64 | 증권사 어댑터 | 개발 | | 진행 중(2026-11-23) |
| 53–54 | 릴리스·마감 | 기획 | 54 = 이 원장 | 53 남음 |

레인: **개발**(5→32 순서 의존), **브랜딩**(33→46, 33·34 뒤 병렬 가능, 45 는 Task 25 뒤), **BYO**(47→52, 개발 레인과 독립, 50 은 Task 13 뒤). 레인끼리 병렬.

---

## Phase 0 — 기반

### Task 1: 설계·시안·계획 문서

**Files:** Create `docs/superpowers/specs/2026-11-21-flow-map-brand-design.md`, `docs/superpowers/plans/2026-11-21-flow-map-brand.md`, `docs/brand/concepts/{a-neon,b-editorial,c-bold}.{html,png}`

- [ ] **Step 1:** PNG 를 `pngquant`/Pillow 로 줄여 폴더 합계 1.5MB 이하. HTML 의 jsdelivr Pretendard `<link>` 는 둔다(시안 보존용, 템플릿 아님).
- [ ] **Step 2:** `uv run --extra dev pytest -q tests/test_doc_paths.py tests/test_docs_refs.py tests/test_wording.py` → PASS. 시안 HTML 이 금칙어 검사 대상이면 대상에서 빼는 대신 시안 문구를 고친다.
- [ ] **Step 3:** 커밋 `docs(map): 수급 지도·브랜딩 설계와 시안 3종, 구현 계획`

### Task 2: 공개·후원 정책 줄

**Files:** Modify `CLAUDE.md:34`, `README.md`(리포트 폴더 경고 블록 근처)

- [ ] **Step 1:** CLAUDE.md 줄을 다음으로: "이 레포는 PUBLIC 이다(2026-11-21 공개 전환). 코드는 오픈소스 후원(GitHub Sponsors)을 받는다. 실데이터(리포트 폴더·종목별 수급·섹터 합계 그림)는 KRX·키움 이용 범위상 공개하지 않는다 — 공개 표면은 합성 데모만(`tests/test_public_surface.py`)."
- [ ] **Step 2:** README 경고 블록에 "섹터 합계 그림(`map.png`)도 같다" 한 줄.
- [ ] **Step 3:** `pytest -q tests/test_first_reader.py tests/test_readme*` (있는 것) PASS → 커밋 `docs(policy): 오픈소스 후원은 받고 실데이터는 공개하지 않는다`

### Task 3: `wording.MAP_AVOID`

**Files:** Modify `src/sectorflow/wording.py`; Test `tests/test_wording.py`

**Interfaces — Produces:** `MAP_AVOID: tuple[str, ...] = ("유입", "유출", "몰림", "몰렸", "쏠림", "회전", "성지")`; `map_hits(text: str) -> list[str]` = `page_hits(text)` + MAP_AVOID 중 든 것(같은 정규화).

- [ ] **Step 1: 실패 테스트** `test_map_hits_catches_flow_words`: `map_hits("돈이 가상전자로 유입") == ["유입"]`; `map_hits("가상전자 — 개인 −2,927억") == []`; `map_hits("추천") == ["추천"]`; `test_map_avoid_not_in_banned`: 기존 화면을 깨지 않게 `set(MAP_AVOID).isdisjoint(BANNED)`.
- [ ] **Step 2:** FAIL 확인 → 구현 → PASS. 변이: `map_hits` 가 MAP_AVOID 를 안 보게 하는 항목을 `tests/mutations.toml` 에.
- [ ] **Step 3:** 커밋 `feat(wording): 지도·랜딩이 피할 말 MAP_AVOID`

### Task 4: 공개 표면 검사

**Files:** Create `tests/test_public_surface.py`; Modify `src/sectorflow/demo.py`(필요 시 `DEMO_SECTORS` 공개)

**Interfaces — Produces:** `PUBLIC_GLOBS = ("README.md", "README.en.md", "docs/images/**", "docs/brand/**", "site/**")`; 실데이터 섹터명 목록 = `tests/fixtures` 또는 `sector_override.py` 의 벤더 업종명(예: `전기/전자`, `운송/창고`).

- [ ] **Step 1: 테스트** `test_public_html_is_demo_only`: 공개 경로의 `.html` 은 `DEMO_NOTICE` 를 담고, 벤더 업종명을 담지 않는다. `test_public_png_has_demo_sidecar`: 공개 `.png` 마다 `docs/SCREENSHOTS.md` 원장에 데모 출처 줄이 있다(기존 원장 형식). `test_no_tracking`: 공개 HTML 에 `gtag|analytics|plausible|document.cookie` 없음. README 산문은 대상 아님(업종명을 설명에 쓸 수 있다) — 이미지·HTML 만.
- [ ] **Step 2:** 지금 상태에서 PASS 여야 한다(기존 그림이 데모인지 이 테스트가 처음 확인한다 — 실패하면 그 그림을 데모로 다시 찍는 것이 이 작업에 들어간다).
- [ ] **Step 3:** 변이: 판정 정규식을 무력화하는 항목. 커밋 `test(public): 공개 표면은 합성 데모만`

---

## Phase 1 — 데이터 모델 `src/sectorflow/flowmap.py` (표준 라이브러리만)

공통 타입(모두 JSON 직렬화 가능한 dict/list):
```python
ACTORS = ("indiv", "forgn", "inst", "etc")          # 페이로드 키
RESID = "resid"                                      # −Σ ACTORS
ACTOR_LABEL = {"indiv": "개인", "forgn": "외국인", "inst": "기관", "etc": "기타법인", "resid": "잔여"}
Sums = dict[str, dict[str, float]]                   # sector -> actor(+resid) -> 억원
```

### Task 5: `window_sums`

**Files:** Create `src/sectorflow/flowmap.py`; Test `tests/test_flowmap.py`

**Interfaces — Produces:** `window_sums(P: dict, markets: list[str], i0: int, i1: int) -> Sums` — `P["flows"][m][s][a][i0..i1]` 합, None 은 0, 섹터는 `P["sectors"]` 순서, `resid = -(합 4주체)`.

- [ ] **Step 1: 테스트** `test_window_sums_adds_markets_and_days`(작은 손 페이로드 2시장×2섹터×3일 — 기대값 손계산), `test_resid_is_negative_sum`, `test_none_counts_as_zero`.
- [ ] **Step 2:** FAIL → 구현 → PASS. 커밋 `feat(map): 창 합계와 잔여`

### Task 6: 보기 1 `exchange_graph`

**Files:** Modify `flowmap.py`; Test `tests/test_flowmap.py`

**Interfaces — Produces:**
```python
TAIL_SHARE = 0.03; MAX_SHOWN = 12; MIN_SHOWN = 5
def exchange_graph(sums: Sums) -> dict:
    # {"sectors": [{"name", "give": {actor: amt>0}, "take": {actor: amt>0}}...],   # 내림차순 by take 합(=노드 높이)
    #  "tail": {"names": [...], "give": {...}, "take": {...}} | None,
    #  "left": {actor: Σgive}, "right": {actor: Σtake}, "gross": float}
```
`give` = 음수 칸의 절댓값(잔여 포함), `take` = 양수 칸(잔여 포함). 섹터 몫 = max(Σgive, Σtake)/gross. 몫 < `TAIL_SHARE` 이거나 순위 > `MAX_SHOWN` 이면 꼬리, 단 보이는 섹터는 최소 `MIN_SHOWN`.

- [ ] **Step 1: 테스트** `test_conservation_per_sector`(각 섹터 Σgive == Σtake, 1e-9), `test_resid_side_follows_sign`, `test_zero_edges_dropped`(0 간선 키 없음), `test_tail_collapses_small`, `test_tail_keeps_minimum`, `test_no_sector_to_sector_keys`(반환 dict 를 재귀로 훑어 섹터명이 다른 섹터의 값 키로 나오지 않음).
- [ ] **Step 2:** FAIL → 구현 → PASS. 변이 2건(보존 깨기: 잔여 제외 / 꼬리 최소 무시). 커밋 `feat(map): 넘긴 쪽→섹터→넘겨받은 쪽 그래프`

### Task 7: 보기 2 `actor_graph`

**Interfaces — Produces:** `actor_graph(sums: Sums, actor: str) -> dict` → `{"actor", "gave": [[sector, amt>0]...], "took": [[sector, amt>0]...], "net": float, "tail_gave": [names, amt] | None, "tail_took": ...}` 꼬리 규칙은 Task 6 과 같은 상수, 몫의 분모는 그 주체의 gross.

- [ ] **Step 1: 테스트** `test_actor_graph_splits_by_sign`, `test_net_equals_took_minus_gave`, `test_unknown_actor_raises`(KeyError).
- [ ] **Step 2:** FAIL → 구현 → PASS → 커밋 `feat(map): 주체별 넘긴 섹터·넘겨받은 섹터`

### Task 8: 기관 세부

**Interfaces — Produces:** `inst_detail_sums(P, markets, i0, i1) -> dict[str, dict[str, float]]` sector → detail key(`P["detail_labels"]` 순) → 억원, 그리고 `"inst_other" = inst − Σdetail`(안분 안 함, 잔여와 같은 원칙). `actor_graph` 가 `actor="inst:<detail key>"` 를 받는다.

- [ ] **Step 1: 테스트** `test_detail_plus_other_equals_inst`, `test_actor_graph_accepts_detail_key`. 표시 순서는 투신(`invtrt`)·연기금(`penfnd_etc`) 먼저(수급 신뢰 순서 규칙) — `test_detail_order_puts_invtrt_penfnd_first`.
- [ ] **Step 2:** FAIL → 구현 → PASS → 커밋 `feat(map): 기관 세부 6종과 나머지`

### Task 9: 타임랩스 프레임

**Interfaces — Produces:** `frames(P: dict, markets: list[str], *, span: int = 1, n: int = 60) -> dict` → `{"dates": [...n], "order": [sector...], "frames": [exchange_graph(...)...]}`. `order` 는 n 일 전체 합 기준으로 고정(프레임마다 정렬하지 않는다 — 깜빡임 방지). 각 프레임 그래프는 `order` 순으로 재배열, 꼬리는 전체 기준 꼬리 집합을 쓴다.

- [ ] **Step 1: 테스트** `test_frames_order_fixed`, `test_frame_count_capped_by_dates`, `test_span5_is_rolling_sum`.
- [ ] **Step 2:** FAIL → 구현 → PASS → 커밋 `feat(map): 타임랩스 프레임`

### Task 10: 헤드라인

**Files:** Modify `flowmap.py`; 조사 도우미는 `src/sectorflow/josa.py`(새 파일)

**Interfaces — Produces:** `josa(word: str, pair: str) -> str` (`pair` ∈ `"은/는" "이/가" "을/를" "으로/로" "과/와"`, 받침·ㄹ 규칙, 숫자·영문 끝은 읽는 소리 기준 표 `{"0":받침...}`), `map_headline(P: dict, market: str, win: int) -> dict` → `{"lead": str, "facts": [str, str, str]}`. lead = 순매수 합계 절대값이 가장 큰 넘긴 주체와 넘겨받은 주체(`summary.dominant` 등 기존 함수 사용), facts = 간선 큰 순 3개를 "가상전자 — 개인 −2,927억" 꼴(`summary.fmt_amt`).

- [ ] **Step 1: 테스트** `test_josa_basic`(`josa("기관","이/가")=="기관이"`, `josa("외국인","은/는")=="외국인은"`, `josa("가상물류","으로/로")=="가상물류로"`), `test_headline_uses_summary`(summary 함수를 monkeypatch 해 다른 값이면 헤드라인도 따라감), `test_headline_wording_clean`(`map_hits(lead + facts) == []`, 데모 페이로드 fixture 로).
- [ ] **Step 2:** FAIL → 구현 → PASS → 변이 1 → 커밋 `feat(map): 요약에서 가져오는 헤드라인과 조사 처리`

### Task 11: `build_map_data`

**Interfaces — Produces:** `MAP_WINDOWS = (1, 5, 20, 60, 120)`; `build_map_data(P: dict) -> dict` → `{"asof", "demo": bool, "basis": DATA_BASIS_SHORT, "markets": ["전체", *P["markets"]], "windows": [가능한 창], "views": {market: {win: {"exchange": ..., "actors": {actor: actor_graph}, "headline": ...}}}, "timelapse": {market: frames(span=1, n=min(60,N))}}`. 크기 상한 `MAP_JSON_MAX = 600_000` 바이트(넘으면 timelapse n 을 줄여 맞춤).

- [ ] **Step 1: 테스트** `test_windows_beyond_dates_skipped`, `test_size_budget`(데모 페이로드로 `len(json.dumps) <= MAP_JSON_MAX`), `test_flows_only_payload_ok`(`ret` 등 없이 `flows/detail/sectors/markets/dates/n_by_sector` 만 있는 페이로드로 동작 — BYO Task 48 의 전제).
- [ ] **Step 2:** FAIL → 구현 → PASS → 커밋 `feat(map): 지도 데이터 묶음`

### Task 12: 수식 문서

**Files:** Modify `docs/formulas.md`(새 절 "수급 지도"), `docs/glossary.md`(넘긴 쪽·넘겨받은 쪽·잔여)

- [ ] **Step 1:** 절에 간선 정의·보존식·잔여·꼬리 규칙·그리지 않는 것(원장 설계 §2 인용)과 `flowmap.py:줄` 인용.
- [ ] **Step 2:** `uv run python scripts/docs_refs.py` 와 `pytest -q tests/test_docs_refs.py` PASS → 커밋 `docs(formulas): 수급 지도 정의`

---

## Phase 2 — 지도 페이지 `scripts/templates/sector_map.html` + `scripts/sector_map.py`

페이지 계약: 템플릿에 `const D = __DATA__;` 한 줄(기존 `rendered_page_hits` 의 `_DATA_LINE` 규칙과 같다), `__BASIS__` 슬롯은 `sector_numbers.py:534` 처럼 치환. 모양은 spec §3.2(시안 `docs/brand/concepts/a-neon.html` 바탕·입자 + `b-editorial.html` 헤드라인·숫자 띠·주석). 렌더는 `draw()` 를 ResizeObserver 로.

### Task 13: 생성기와 뼈대

**Files:** Create `scripts/sector_map.py`, `scripts/templates/sector_map.html`; Modify `src/sectorflow/report_cli.py`(`map` 진입점), `pyproject.toml`(`sector-flow-map`), `src/sectorflow/offline_templates.py:TEMPLATES`; Test `tests/test_map_page.py`, `tests/test_packaging.py`, `tests/test_entry_points.py`

**Interfaces — Produces:** `sector_map.py --payload <payload.json> --html <map.html>`; `build(P) -> dict` = `flowmap.build_map_data`; `render(data: dict, template: str) -> str`.

- [ ] **Step 1: 테스트** `test_map_html_embeds_data`(데모 페이로드 → HTML 에 `const D = ` 한 줄, JSON 파싱 가능, `asof` 일치), `test_map_html_no_remote_scripts`(`<script src=` 없음), `test_map_wording`(`map_hits(rendered_page_hits 대상 글)` 빈 목록), 패키징 테스트에 템플릿 추가.
- [ ] **Step 2:** 뼈대 템플릿: 제목·표지·빈 `<svg class=fig>`·`표로 보기` 표(보기 1 데이터를 행으로) — 표만으로도 정보가 온전해야 한다.
- [ ] **Step 3:** PASS → 커밋 `feat(map): 지도 페이지 생성기와 뼈대`

### Task 14: 브랜드 토큰

**Files:** Create `scripts/templates/_brand.css`(색·글꼴·간격 변수, 라이트/다크); Modify `scripts/sector_map.py`(빌드 때 `/*__BRAND__*/` 자리에 인라인)

토큰(spec §3.2): 넘긴 쪽 `--give` 파랑, 넘겨받은 쪽 `--take` 빨강, 주체 색 4 + 잔여 회색, 밤 바탕 `--night`, 종이 바탕 `--paper`. dataviz 대비 검사(라이트·다크 둘 다 텍스트 4.5:1, 띠 3:1)를 `tests/test_brand_tokens.py` 가 계산해 본다.

- [ ] 테스트 → 구현 → PASS → 커밋 `feat(brand): 색·글꼴 토큰`

### Task 15: 보기 1 렌더

**Files:** Modify `sector_map.html`

레이아웃(JS, 의존 없음): 왼쪽 주체 노드(순서 고정 개인·외국인·기관·기타법인·잔여), 가운데 섹터 노드(`exchange.sectors` 순 + 꼬리), 오른쪽 주체 노드. 노드 높이 ∝ 금액, 최소 높이는 라벨용으로 키우되 띠 두께는 금액 그대로(시안 A 약점 메모). 띠 = 3차 베지어 리본. 잔여는 점선.

- [ ] **Step 1:** `tests/test_web_render.py` 패턴(node 로 그리기)에 `test_map_view1_band_widths_proportional`(띠 두께 비 = 금액 비, 1px 허용) 추가.
- [ ] **Step 2:** 구현 → PASS → `verify_merge.py --only render,pty` → 커밋 `feat(map): 보기 1 띠 그리기`

### Task 16: 보기 2 렌더(주체 누르기)

- [ ] 테스트 `test_map_view2_after_actor_click`(node DOM 시뮬레이션: 주체 클릭 → 가운데 노드 하나, 양쪽 섹터, net 꼬리 글자 = `actor_graph.net` 포맷) → 구현 → 커밋 `feat(map): 주체별 보기`

### Task 17: 기관 펼치기

- [ ] 보기 2 에서 기관이면 세부 토글. 테스트 `test_inst_detail_toggle_lists_invtrt_first` → 커밋 `feat(map): 기관 세부 펼치기`

### Task 18: 시장·창 선택과 주소

- [ ] 버튼: 전체/코스피/코스닥(라벨은 `P["markets"]` 값 그대로) × `D.windows`. 없는 창 버튼은 안 그린다. 상태는 `#m-<i>-w-<win>-a-<actor>` 해시(ASCII). 테스트 `test_map_hash_roundtrip`, `test_missing_window_has_no_button` → 커밋 `feat(map): 시장·창 선택과 해시 주소`

### Task 19: 호버·탭·키보드·툴팁

- [ ] 노드·띠 hover/tap → 관련 띠만 진하게, 툴팁(억원, `fmt_amt` 와 같은 포맷 JS 포트). Tab 으로 노드 순회, Enter = 클릭. 44px 터치 목표. 테스트 `test_tooltip_text_matches_payload`, `test_keyboard_focus_order` → 커밋 `feat(map): 강조·툴팁·키보드`

### Task 20: 입자 층

- [ ] canvas 를 svg 위에. 간선마다 입자 독립: 왼쪽 띠 입자는 섹터 노드 가장자리에서 소멸, 오른쪽 띠 입자는 섹터 노드에서 생성(노드 통과 금지 — spec §3.1). 밀도 ∝ 금액(상한), `prefers-reduced-motion` 이면 canvas 를 그리지 않는다. 탭 숨김 시 정지(`visibilitychange`). 테스트 `test_particles_never_cross_node`(입자 경로 생성 함수를 순수 함수로 빼서 node 로 시험: 모든 경로의 x 범위가 한 간선 안), `test_reduced_motion_no_canvas` → 커밋 `feat(map): 빛 입자`

### Task 21: 타임랩스

- [ ] 날짜 막대(`input type=range`) + 재생/정지, 프레임 간 띠 두께 보간(300ms), 정렬 고정(`timelapse.order`). 테스트 `test_timelapse_frame_matches_data`(막대를 k 로 → 첫 섹터 노드 높이 = 프레임 k 값) → 커밋 `feat(map): 타임랩스`

### Task 22: 헤드라인·숫자 띠·주석

- [ ] B 문법: `headline.lead` 큰 제목, 주체 넷 순매수 합계 띠, `facts` 3개를 띠 옆 주석(자리 못 찾으면 아래 목록 — 시안 B 약점 메모). 테스트 `test_headline_rendered_from_data`, 페이지 전체 `map_hits` 빈 목록 → 커밋 `feat(map): 헤드라인과 주석`

### Task 23: 모바일

- [ ] 600px 미만: 세로 배치(위 넘긴 쪽 → 가운데 섹터 → 아래 넘겨받은 쪽)로 회전해 띠 구간을 확보(시안 A·C 모바일 약점). `scripts/web_smoke.py` 에 `--offline-fonts`(글꼴 요청 차단) 추가, 390·600·1440 폭에서 가로 스크롤 0·라벨 겹침 0(bbox 교차 검사). → 커밋 `feat(map): 휴대폰 세로 배치`

### Task 24: 라이트·다크

- [ ] 첫 화면은 밤(시안 A) 바탕을 기본으로, `prefers-color-scheme: light` 와 `:root[data-theme]` 를 따른다. 카드는 종이(시안 B). 대비 테스트는 Task 14 것이 두 테마 다 본다. → 커밋 `feat(map): 라이트·다크`

### Task 25: 표지·증거·데모 배지

- [ ] `DATA_BASIS_SHORT` 줄, 데모면 `DEMO_NOTICE` 배지, "예측이 아니다" 꼬리, 웹 `const EV` 에 `map` 항목(사실 관측) — 기존 EV 대조 검사(`tests/test_viewer_page.py` 류)가 새 항목도 보게. → 커밋 `feat(map): 표지와 증거 표지`

---

## Phase 3 — 배치·검증 통합

### Task 26: 배치가 지도를 짓는다

**Files:** Modify `scripts/daily_report.sh`(viewer 다음 줄, `SIDE_FAIL+="map "` 패턴), `scripts/rebuild_history.py`(산출물 목록), `src/sectorflow/demo.py`(데모 폴더에도)

- [ ] 테스트 `tests/test_daily_report_flow.py` 에 `test_map_built_and_failure_is_side_fail`(생성기 실패를 흉내 내도 종료 0·`numbers.html` 게시), `tests/test_demo.py` 에 `test_demo_has_map` → 커밋 `feat(batch): 리포트 폴더에 map.html`

### Task 27: 공유 카드

**Files:** Create `scripts/map_card.py`(`digest.py` 의 `_cached_chromium` 재사용 — 공용 함수로 옮기면 `docs_refs --fix`), Modify `daily_report.sh`

**Interfaces — Produces:** `map_card.py --dir <폴더>` → `map.png`(1080×1350), `map_wide.png`(1200×675); `map.html?card=tall|wide` 가 카드 배치를 그린다(정지, 입자 없음).

- [ ] 테스트 `test_card_sizes`(Playwright 없으면 skip — 기존 digest 테스트 패턴), `test_card_has_basis_line` → 커밋 `feat(map): 공유 카드 PNG`

### Task 28: 검증 층 M

**Files:** Modify `scripts/verify_report.py`(`layer_m(P, html)`, `LAYERS` 에 `"M"`); Test `tests/test_verify_report.py`

층 M 은 `flowmap` 을 **부르지 않고** 페이로드 일별 배열에서 다시 센다: 각 창·시장·섹터의 주체 합 = 지도 데이터 값(절대 허용오차 `TOL_FLOW`), 섹터 보존식, 꼬리 합 = 숨긴 섹터들의 합, 데이터에 섹터→섹터 구조 없음, 페이지 글 `map_hits` 빈 목록.

- [ ] 테스트: 지도 데이터 한 칸을 바꾼 HTML 로 `layer_m` 이 FAIL. 변이 3건(합 비교 끄기·보존 비교 끄기·글 검사 끄기). 배치에 `--layers M` 은 전 층 실행에 포함(지도 있을 때만, X 와 같은 규칙). → 커밋 `feat(verify): 지도 층 M`

### Task 29: 웹 스모크

- [ ] `scripts/web_smoke.py` 가 `map.html` 을 연다(폭 3종·콘솔 에러 0·라벨 겹침 0·카드 두 모드). → 커밋 `test(web): 지도 스모크`

### Task 30: 비공개 웹 링크

- [ ] `scripts/serve_reports.py` `FILES` 에 `map.html`·`map.png`·`map_wide.png`, `PAGES` 에 `("map.html", "수급 지도")`. 첫 화면은 그대로. `tests/test_serve_reports.py` → 커밋 `feat(web): 수급 지도 링크`

### Task 31: 화면 원장

- [ ] `scripts/demo_shots.py` 가 데모 지도·카드를 찍어 `docs/images/map-*.png`, `docs/SCREENSHOTS.md` 줄 추가, `tests/test_screenshots_fresh.py` 초록. → 커밋 `docs(images): 수급 지도 데모 그림`

### Task 32: 운영 문서

- [ ] `docs/runbook.md`(지도·카드 실패 알림과 대응 — `SIDE_FAIL map`), `tests/test_runbook.py` → 커밋 `docs(runbook): 지도 실패 대응`

---

## Phase 4 — 브랜드 (브랜딩 레인)

### Task 33: 브랜드 가이드

**Files:** Create `docs/brand/README.md`

- [ ] 이름 `sector-flow` + 설명어 "수급 지도", 한 줄 소개(금칙어·MAP_AVOID 통과), 말투(관측·사실·주변합), 색·글꼴(`_brand.css` 인용), 쓰는 말/안 쓰는 말 표, 데모만 공개 규칙. `tests/test_public_surface.py`·`test_wording` 대상 → 커밋 `docs(brand): 브랜드 가이드`

### Task 34: 워드마크·파비콘

- [ ] `docs/brand/wordmark.svg`(라이트/다크 2종, 텍스트는 아웃라인 path — 글꼴 의존 없음), `favicon.svg`. 지도 페이지 머리에 인라인. → 커밋 `feat(brand): 워드마크와 파비콘`

### Task 35: 기존 웹 머리 통일

- [ ] `numbers.html`·`viewer.html`·`trust.html` 머리에 워드마크만(본문 스타일 불변). `verify_merge --only render,pty` → 커밋 `style(web): 페이지 머리에 워드마크`

### Task 36: README 머리 다시 쓰기

- [ ] 위에서 아래로 처음 읽는 사람 기준(글로벌 규칙): 워드마크 → 한 줄 → 데모 지도 GIF → Quick start 3줄 → 후원 한 줄 → 기존 목차. 중복·내부 경위 삭제. `tests/test_first_reader.py` 초록 → 커밋 `docs(readme): 수급 지도 중심으로 머리 다시 쓰기`

### Task 37: 영어 README

- [ ] `README.en.md`(머리·Quick start·데이터 정책·후원만, 상세는 한국어 링크). 한국어 README 에 언어 링크. → 커밋 `docs(readme): English README`

### Task 38: 데모 영상

**Files:** Create `scripts/map_video.py`

**Interfaces — Produces:** `map_video.py --dir <폴더> --out <x.mp4> [--gif <x.gif>] [--seconds 8]` — Playwright 녹화(타임랩스 재생) + ffmpeg 변환. 실데이터 폴더도 받지만 출력은 그 사람 기기에만.

- [ ] 데모로 `docs/images/map-demo.gif`(≤4MB) 생성, 원장 줄. 테스트는 인자 검사·ffmpeg 없을 때 친절한 오류. → 커밋 `feat(map): 타임랩스 영상 뽑기`

### Task 39: 소셜 미리보기·레포 메타

- [ ] `docs/brand/social-preview.png`(1280×640, 데모) 생성 스크립트 `scripts/social_preview.py`. 레포 설명·토픽은 `gh repo edit --description … --add-topic korea --add-topic kospi --add-topic data-visualization --add-topic sankey --add-topic open-source`(설명 문구 MAP_AVOID 통과). 업로드는 소유자(웹 UI) — PR 본문에 적는다. → 커밋 `feat(brand): 소셜 미리보기`

### Task 40: 브랜드 시안 정리

- [ ] 최종 지도 데모 그림으로 `docs/brand/README.md` 갱신, 시안 3종은 `concepts/` 에 "버린 이유" 한 줄씩. → 커밋 `docs(brand): 시안 기록`

---

## Phase 5 — 후원·커뮤니티·랜딩

### Task 41: 후원

- [ ] `.github/FUNDING.yml`(`github: [younghwan91]`), README 후원 절(코드·유지보수 후원, 혜택 없음, 데이터 판매 아님). → 커밋 `chore(funding): GitHub Sponsors`

### Task 42: 기여 안내

- [ ] `CONTRIBUTING.md`(개발 명령은 CLAUDE.md 와 같은 것, 실데이터를 이슈·PR 에 붙이지 않기, 데모로 재현), `CODE_OF_CONDUCT.md`(Contributor Covenant 2.1). → 커밋 `docs: 기여 안내와 행동 강령`

### Task 43: 이슈·PR 템플릿

- [ ] `.github/ISSUE_TEMPLATE/{bug,feature}.yml` + `config.yml`(빈 이슈 끔), `.github/pull_request_template.md`. 버그 템플릿에 "실데이터·리포트 폴더 붙이지 마세요 — `--demo` 로 재현" 체크. 투자 질문은 받지 않는다는 줄. → 커밋 `chore(github): 이슈·PR 템플릿`

### Task 44: 고지

- [ ] `DISCLAIMER.md`(투자 조언 아님·관측이며 예측 아님·각자 데이터 이용 조건·권리자 연락은 GitHub 이슈 또는 보안 연락 경로로 즉시 조치), `SECURITY.md`. README·랜딩에서 링크. → 커밋 `docs: 고지와 보안 연락`

### Task 45: 랜딩 페이지

**Files:** Create `site/index.html`(브랜드 포스터: 워드마크·한 줄·데모 지도 iframe 대신 같은 페이지의 데모 `map.html` 링크·영상·"자기 키로 보는 법"·후원·고지), `site/build.py`(데모 폴더 → `site/map.html`·카드·영상 복사)

- [ ] `tests/test_public_surface.py` 가 `site/` 를 본다. 지도 페이지는 생성물이라 커밋하지 않고 Pages 워크플로가 짓는다(`site/.gitignore`). → 커밋 `feat(site): 랜딩 페이지`

### Task 46: Pages 워크플로

- [ ] `.github/workflows/pages.yml`: main 푸시 시 `uv sync` → `sector-flow-demo --out _demo` → `site/build.py` → 공개 표면 검사(`pytest tests/test_public_surface.py` 를 빌드 산출물에) → `actions/deploy-pages`. 머지당 분 상한 표(`scripts/ci_plan.py --minutes`, `tests/test_ci_workflow.py`)에 반영. 레포 Pages 설정은 `gh api -X POST repos/younghwan91/sector-flow/pages -f build_type=workflow`. → 커밋 `ci(pages): 데모 랜딩 배포`

---

## Phase 6 — BYO (BYO 레인)

### Task 47: 전 시장 종목 목록

- [ ] `sector-flow-collect universe --db x.db`: `ka10099` 두 시장 → `stocks`(업종 포함), 호출 2회 + 페이지. 테스트는 기존 `tests/test_collect_*` 의 가짜 전송 패턴. → 커밋 `feat(collect): 전 시장 종목·업종 목록`

### Task 48: 지도 전용 수집 + 수급만 페이로드

**Files:** Modify `src/sectorflow/collect/run.py`(`backfill --map-only`: TR `ka10099`·`ka10059` 만), `scripts/sector_flow.py`(`--flows-only`: 수익률·시총 없이 `flows/detail/sectors/markets/dates/n_by_sector/names` 만 짓고 `"flows_only": true`)

- [ ] 테스트 `test_map_only_calls_two_trs`(가짜 전송 호출 기록), `test_flows_only_payload_builds_map`(Task 11 `test_flows_only_payload_ok` 와 같은 모양을 실제 생성기에서). `sector_numbers.py`·TUI 는 `flows_only` 면 "수익률 없음(지도 전용 수집)" 안내로 끝낸다(조용히 비지 않게). → 커밋 `feat(collect): 지도 전용 수집과 수급만 페이로드`

### Task 49: 일일 증분

- [ ] `sector-flow-collect daily --db x.db [--map-only]`: 마지막 저장일 다음부터 오늘까지, `--resume` 의미 내장, 휴장일은 `scripts/krx_calendar.py`. 호출 상한(초 5·분 100·시간 1,000)은 기존 `--rate`·`--max-calls` + 분·시 창 상한을 `kiwoom.py` 에 추가하고 테스트(`test_hourly_cap_blocks_1001st`). → 커밋 `feat(collect): 일일 증분과 분·시간 호출 상한`

### Task 50: sqlite → 지도 한 줄

- [ ] `sector-flow-byo map --db x.db --out <폴더>`(새 진입점, `report_cli`): `--flows-only` 페이로드 → `map.html` → (Playwright 있으면) 카드. 테스트 `test_byo_map_end_to_end`(데모 sqlite 로) → 커밋 `feat(byo): sqlite 에서 지도까지 한 명령`

### Task 51: 온보딩 문서

- [ ] `docs/byo/quickstart.md`: 키움 REST 키 발급 → `uv tool install git+https://github.com/younghwan91/sector-flow` → `sector-flow-collect universe` → `backfill --map-only --days 120` → `sector-flow-byo map` → 브라우저. 예상 시간·호출 수 표, "자기 데이터의 공유는 각자 약관을 따른다". `tests/test_docs_cli_options.py` 가 문서의 옵션이 실제 CLI 에 있는지 본다 → 커밋 `docs(byo): 자기 키로 첫 지도까지`

### Task 52: 실측 확인

- [ ] 소유자 키로 두 섹터 조각(`--sectors "운송/창고,오락/문화"`), 배치 시간(18:00~20:30·08:00~10:30) 피해 `busy.py` 준수로 문서 명령을 그대로 실행, 지도가 뜨는지와 kr_quant 판 지도 데이터 차 0 을 `docs/byo/phase3-map.md` 에 기록(실데이터 숫자는 적지 않고 일치 여부·호출 수·시간만). → 커밋 `docs(byo): 지도 경로 실측`

---

## Phase 6b — 증권사 어댑터 (2026-11-21 추가, 조사 `docs/byo/brokers.md`)

소유자 요청: 각자 자기 증권사 키를 넣으면 지도가 뜨게. 소유자는 키움 외 실제 키가 없다 — 어댑터는 공개 문서의 응답 모양으로 만들고
가짜 전송으로 시험하며, 실제 키로 키움과 대조하기 전까지 **미검증** 표지를 단다. 검증은 키가 있는 사용자가 대조 명령을 돌려 이슈로 올린다.
벤더 명세 파일·공식 클라이언트 코드를 레포에 복사하지 않는다(키움 공식 클라이언트는 복제·수정 금지 라이선스). 시험용 응답은 문서를 보고 손으로 쓴 최소 표본이다.

### Task 55: 어댑터 구조

**Files:** Create `src/sectorflow/collect/brokers/{__init__,base}.py`; Modify `run.py`(`--broker`), `kiwoom.py`·`fetch.py`(키움을 첫 어댑터로 옮김 — 동작·호출 불변)

**Interfaces — Produces:**
```python
@dataclass(frozen=True)
class Capability:
    actors: frozenset[str]          # storage.INVESTOR_COLUMNS 중 주는 것
    venue: str                      # "KRX" | "KRX+NXT"
    verified: bool                  # 실제 키로 키움과 대조했나
    terms_note: str                 # 약관상 주의(한 줄)
class Broker(Protocol):
    name: str; capability: Capability
    def universe(self) -> list[dict]: ...                      # stocks 행 (sector 는 없을 수 있다)
    def investor_daily(self, code: str, start: str, end: str) -> list[dict]: ...  # supply_demand 행
BROKERS: dict[str, Callable[[Settings], Broker]]               # "kiwoom", "kis", "ls", "toss", "db", "nh"
```
- [ ] 테스트 `test_kiwoom_adapter_byte_identical`(기존 백필 가짜 전송 기록 = 옮긴 뒤 기록), `test_broker_registry_names`. 커밋 `refactor(collect): 증권사 어댑터 구조, 키움을 첫 어댑터로`

### Task 56: 섹터 표를 증권사와 떼기

수급만 증권사에서 받고 섹터는 한 벌로(조사 §1.3). 키움 외 사용자는 키움 업종을 받을 수 없으므로 섹터 출처를 정한다: (a) 그 증권사가 주는 업종(코드 체계가 다르면 표지), (b) 사용자가 가진 다른 키, (c) 공개 출처(공공데이터 `KRX상장종목정보` 는 "이용허락범위 제한 없음" — 업종 필드 유무 확인). 레포에 키움 업종표 사본을 싣지 않는다(재배포).
- [ ] `stocks.sector_source` 열, 지도 표지에 섹터 출처. 테스트 `test_sector_source_labelled`. 커밋 `feat(collect): 섹터 출처를 증권사와 떼어 표지`

### Task 57: KIS 어댑터
- [ ] 토큰 발급·갱신, `FHPTJ04160001`(KRX `J`), 필드 대응(증권→금융투자, 종금→기타금융, 기금→연기금, 국가 결측 — 기타·기타단체를 국가로 넣지 않는다), 상한 초 18 → 기본 보수적 5. 실전 키 전용 경고. 테스트: 손 표본 응답 → `supply_demand` 행 기대값, 연속조회. 커밋 `feat(collect): 한국투자 어댑터(미검증)`

### Task 58: LS 어댑터
- [ ] `t1702`(`K`), 1:1 대응, 초당 1건. 시작 시 약관 경고("3개월 주문체결 20회 이하 해지 사유")를 출력하고 `--i-understand-ls-terms` 없이는 멈춘다. 커밋 `feat(collect): LS 어댑터(미검증)`

### Task 59: 토스 어댑터
- [ ] `/api/v1/stocks/{symbol}/investor-trading`, `venue="KRX+NXT"`. 페이로드·지도 표지가 `DATA_BASIS` 대신 통합 기준 문구를 쓴다(`basis.py` 에 통합 문구 추가, KRX 단독과 섞이면 수집이 거부). 토스 섹터는 쓰지 않는다(다중 소속). 커밋 `feat(collect): 토스 어댑터(미검증, KRX+NXT 통합)`

### Task 60: DB·NH 어댑터(기관계만)
- [ ] `DAYSTOCKTJJ`, `currentInvestor`. 주는 주체만 채우고 나머지는 NULL(0 아님 — 데이터 함정: NULL 은 모름). 기타법인을 잔여로 메우지 않는다. 커밋 `feat(collect): DB·NH 어댑터(기관계만, 미검증)`

### Task 61: 능력에 따른 지도
- [ ] 페이로드에 `capability`(주체·거래소·검증 여부·섹터 출처). `flowmap` 은 없는 주체를 노드로 그리지 않고 "이 증권사는 X 를 주지 않는다" 표지, 잔여 노드는 4주체가 다 있을 때만. 미검증이면 지도 머리에 "미검증 어댑터" 배지. 테스트 `test_missing_actor_not_drawn`, `test_resid_only_with_all_four`. 커밋 `feat(map): 증권사 능력에 맞춘 지도`

### Task 62: 대조 명령
- [ ] `sector-flow-collect equiv --broker kis --db kis.db --against kiwoom.db [--sectors …]`: 겹치는 (날짜, 종목, 주체) 차와 섹터 합 상대오차(0.5% 바, Phase 1 과 같다) — 출력은 일치 여부·칸 수·최대 상대오차만(실데이터 숫자 없음, 이슈에 붙여도 안전하게). 커밋 `feat(collect): 증권사 대조 명령`

### Task 63: 검증 기여 길
- [ ] 이슈 템플릿 `.github/ISSUE_TEMPLATE/broker-verify.yml`(대조 명령 출력만 붙이게, 실데이터 금지 안내), `docs/byo/brokers.md` 에 검증 현황 표(검증자·날짜·결과). 커밋 `docs(byo): 증권사 어댑터 검증 기여 안내`

### Task 64: 온보딩에 증권사 고르기
- [ ] `docs/byo/quickstart.md`(Task 51)에 증권사별 키 발급·IP 등록·상한·약관 주의 한 줄씩, `--broker` 예시. `tests/test_docs_cli_options.py`. 커밋 `docs(byo): 증권사별 시작하기`

## Phase 7 — 릴리스·마감

### Task 53: 변경 기록과 릴리스

- [ ] `CHANGELOG.md`(이번 묶음), 태그 `v0.2.0` 시 GitHub Release 를 만드는 `.github/workflows/release.yml`(wheel 첨부, 데모 GIF). → 커밋 `chore(release): 변경 기록과 릴리스 워크플로`

### Task 54: 마감

- [ ] 원장 표의 PR 번호·상태 채우기, `graphify update .`, CLAUDE.md 명령 절에 `sector-flow-map`·`map_card.py`·`map_video.py`·`sector-flow-byo` 한 줄씩. → 커밋 `docs: 수급 지도 작업 마감`
