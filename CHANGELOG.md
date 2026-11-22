# Changelog

판마다 무엇이 바뀌었나. 태그(`v*`)를 밀면 `.github/workflows/release.yml` 이 이 파일의 그 판 절을 Release 본문으로 쓴다.
숫자는 동시기 관측이며 예측이 아니다 — 이 기록도 기능만 적는다.

## [0.2.0] - 2026-11-23

### 수급 지도
- `map.html` — 넘긴 쪽 주체 → 섹터 → 넘겨받은 쪽 주체. 띠 하나가 (주체, 섹터) 한 칸이고, 섹터끼리·주체끼리는 잇지 않는다.
- 주체를 누르면 그 주체의 섹터별 순매도·순매수, 기관은 세부(투신·연기금 먼저)로 펼친다.
- 두 번째 보기 '시간 흐름' — 주체 하나의 섹터별 순매수를 날짜축에 쌓는다(하루·5일 합·누적).
- 타임랩스(최근 60거래일), 공유 카드 PNG(`scripts/map_card.py`), 데모 GIF(`scripts/map_video.py`).
- 일일 배치가 리포트 폴더에 지도와 카드를 짓고 비공개 웹이 잇는다. 검증 층 M 이 지도 숫자를 페이로드에서 다시 센다.

### BYO — 자기 키움 키로 첫 지도까지
- `sector-flow-collect universe`·`backfill --map-only`·`daily`, 분·시간 호출 상한.
- `sector-flow-byo map` — sqlite 에서 지도까지 한 명령. 안내 `docs/byo/quickstart.md`.

### 공개·브랜드
- 공개 그림은 전부 합성 데모(`tests/test_public_surface.py`). 랜딩(GitHub Pages), 로고·워드마크·소셜 미리보기, 영어 README.
- 오픈소스 후원(GitHub Sponsors), 기여 안내·행동 강령·이슈 템플릿·고지.

## [0.1.0]

- 일일 리포트(`scripts/daily_report.sh`)와 터미널 화면 `sw-flow`·`sw-ledger`, 수치표·흐름 뷰어 웹, 자기 검증(`verify_report.py`).
