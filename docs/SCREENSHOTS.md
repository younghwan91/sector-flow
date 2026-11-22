# 스크린샷 원장

README 가 쓰는 `docs/images` 그림(`*.png`, 타임랩스 `map-demo.gif`)이 **어느 코드로 찍혔나**를 적는다. `tests/test_screenshots_fresh.py` 가
이 표의 커밋과 지금 HEAD 에서 감시 경로의 git 트리를 비교해, 화면 코드가 바뀌었는데 그림이 그대로면
`스크린샷 재촬영` 으로 실패한다. 날짜가 아니라 트리를 비교한다 — 화면과 무관한 커밋으로는 울지 않는다.

그림은 전부 **합성 데모**다(실종목·실숫자 없음, 제목 줄과 웹 맨 위에 `[합성 데모]`). 다시 찍는 법:

```
uv run --with playwright --with pillow --with imageio-ffmpeg python scripts/demo_shots.py
```

감시 경로를 커밋한 뒤 돌린다 — 도구가 그림 8장과 GIF 하나를 찍고 이 표를 HEAD 로 고친다. 터미널은 tmux 160×45
`capture-pane -e` → ANSI → HTML → Chromium(128색), 웹은 Chromium 1440·390(DPR 2, 64색)이고 그림당 약 150KB 안이다(폰 그림은 요약 문장과 시장 4주체까지라 길다).
GIF 는 `scripts/map_video.py`(가짜 시계로 타임랩스를 30fps 로 찍어 ffmpeg 로 960폭·15fps·128색)라 ffmpeg 가 있어야 하고(없으면 아무것도
찍기 전에 멈춘다), 기준일로 끝나는 마지막 11거래일 8초, 4MB 안이다(`tests/test_map_video.py`).

| 이미지 | 감시 경로 | 찍은 커밋 |
|---|---|---|
| docs/images/kqflow-today.png | src/sectorflow/tui/ | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/kqflow-today.png | src/sectorflow/demo.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/kqflow-table.png | src/sectorflow/tui/ | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/kqflow-table.png | src/sectorflow/demo.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/kqflow-names.png | src/sectorflow/tui/ | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/kqflow-names.png | src/sectorflow/demo.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/kqflow-combined.png | src/sectorflow/tui/ | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/kqflow-combined.png | src/sectorflow/demo.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/kqledger-table.png | src/sectorflow/tui/ | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/kqledger-table.png | src/sectorflow/demo.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/web-today.png | scripts/templates/ | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/web-today.png | src/sectorflow/demo.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/web-today-mobile.png | scripts/templates/ | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/web-today-mobile.png | src/sectorflow/demo.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/map-demo.png | scripts/templates/ | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/map-demo.png | scripts/sector_map.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/map-demo.png | src/sectorflow/flowmap.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/map-demo.png | src/sectorflow/demo.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/map-demo.gif | scripts/templates/ | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/map-demo.gif | scripts/sector_map.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/map-demo.gif | scripts/map_video.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/map-demo.gif | src/sectorflow/flowmap.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/images/map-demo.gif | src/sectorflow/demo.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |

## 소셜 미리보기

`docs/brand/social-preview.png`(1280×640, GitHub 레포 링크 카드)는 따로 찍는다 — 지도 페이지·데모·워드마크·브랜드 가이드(한 줄 소개)가
바뀌면 위 표처럼 `스크린샷 재촬영` 으로 실패한다. 합성 데모 지도의 그림 칸(Chromium 900폭, DPR 2)을 워드마크·한 줄 소개 옆에 놓고
색을 줄이지 않는다(1MB 안, `tests/test_social_preview.py`). 레포 설정에 올리는 것은 소유자가 웹에서 한다(Settings → Social preview).

```
uv run --with playwright --with pillow python scripts/social_preview.py
```

| 이미지 | 감시 경로 | 찍은 커밋 |
|---|---|---|
| docs/brand/social-preview.png | scripts/social_preview.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/brand/social-preview.png | scripts/templates/sector_map.html | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/brand/social-preview.png | scripts/sector_map.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/brand/social-preview.png | src/sectorflow/flowmap.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/brand/social-preview.png | src/sectorflow/demo.py | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/brand/social-preview.png | docs/brand/wordmark-dark.svg | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
| docs/brand/social-preview.png | docs/brand/README.md | 7cc1b51209685ea68c264eff2f3e2c6c7fa84490 |
