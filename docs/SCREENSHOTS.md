# 스크린샷 원장

README 가 쓰는 `docs/images/*.png` 가 **어느 코드로 찍혔나**를 적는다. `tests/test_screenshots_fresh.py` 가
이 표의 커밋과 지금 HEAD 에서 감시 경로의 git 트리를 비교해, 화면 코드가 바뀌었는데 그림이 그대로면
`스크린샷 재촬영` 으로 실패한다. 날짜가 아니라 트리를 비교한다 — 화면과 무관한 커밋으로는 울지 않는다.

그림은 전부 **합성 데모**다(실종목·실숫자 없음, 제목 줄과 웹 맨 위에 `[합성 데모]`). 다시 찍는 법:

```
uv run --with playwright --with pillow python scripts/demo_shots.py
```

감시 경로를 커밋한 뒤 돌린다 — 도구가 그림 7장을 찍고 이 표를 HEAD 로 고친다. 터미널은 tmux 160×45
`capture-pane -e` → ANSI → HTML → Chromium(128색), 웹은 Chromium 1440·390(DPR 2, 64색)이고 그림당 약 150KB 안이다(폰 그림은 요약 문장과 시장 4주체까지라 길다).

| 이미지 | 감시 경로 | 찍은 커밋 |
|---|---|---|
| docs/images/kqflow-today.png | src/sectorflow/tui/ | 9bdb1bfaa432cafe1b1d9e667d77579b0ca80d13 |
| docs/images/kqflow-today.png | src/sectorflow/demo.py | 9bdb1bfaa432cafe1b1d9e667d77579b0ca80d13 |
| docs/images/kqflow-table.png | src/sectorflow/tui/ | 9bdb1bfaa432cafe1b1d9e667d77579b0ca80d13 |
| docs/images/kqflow-table.png | src/sectorflow/demo.py | 9bdb1bfaa432cafe1b1d9e667d77579b0ca80d13 |
| docs/images/kqflow-names.png | src/sectorflow/tui/ | 9bdb1bfaa432cafe1b1d9e667d77579b0ca80d13 |
| docs/images/kqflow-names.png | src/sectorflow/demo.py | 9bdb1bfaa432cafe1b1d9e667d77579b0ca80d13 |
| docs/images/kqflow-combined.png | src/sectorflow/tui/ | 9bdb1bfaa432cafe1b1d9e667d77579b0ca80d13 |
| docs/images/kqflow-combined.png | src/sectorflow/demo.py | 9bdb1bfaa432cafe1b1d9e667d77579b0ca80d13 |
| docs/images/kqledger-table.png | src/sectorflow/tui/ | 9bdb1bfaa432cafe1b1d9e667d77579b0ca80d13 |
| docs/images/kqledger-table.png | src/sectorflow/demo.py | 9bdb1bfaa432cafe1b1d9e667d77579b0ca80d13 |
| docs/images/web-today.png | scripts/templates/ | 9bdb1bfaa432cafe1b1d9e667d77579b0ca80d13 |
| docs/images/web-today.png | src/sectorflow/demo.py | 9bdb1bfaa432cafe1b1d9e667d77579b0ca80d13 |
| docs/images/web-today-mobile.png | scripts/templates/ | 9bdb1bfaa432cafe1b1d9e667d77579b0ca80d13 |
| docs/images/web-today-mobile.png | src/sectorflow/demo.py | 9bdb1bfaa432cafe1b1d9e667d77579b0ca80d13 |
