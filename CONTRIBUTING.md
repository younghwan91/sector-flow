# 기여 안내

sector-flow 에 관심을 가져 주셔서 고맙습니다. 버그 제보·문서 수정·코드 기여 모두 받습니다.
이 프로젝트는 코스피·코스닥의 투자 주체별 섹터 순매매를 **관측**하는 도구입니다. 종목을 고르거나 앞날을 말하는 기능은 받지 않습니다.

## 먼저 — 실데이터를 붙이지 마세요

- 이슈·PR·댓글에 **실제 리포트 폴더, 실제 종목별 수급 숫자, 실데이터로 찍은 스크린샷**을 올리지 마세요.
  KRX·키움 데이터 이용 범위상 제3자에게 재배포할 수 없습니다.
- 문제는 **합성 데모로 재현**해 주세요. 숫자와 이름이 전부 난수라 마음 놓고 올릴 수 있습니다.

  ```bash
  uv sync
  uv run sw-flow --demo                     # 터미널
  uv run sector-flow-demo --out ~/sf-demo   # 리포트 폴더 꼴 그대로 — 웹·검증 재현용
  ```

- 데모로 재현되지 않는 문제라면 숫자 대신 **꼴**(어느 화면·어느 열·무엇이 어떻게 틀렸나)만 적어 주세요.
- 투자 질문(무엇을 살까, 이 섹터가 오를까)은 받지 않습니다.

## 처음이라면

[`good first issue`](https://github.com/younghwan91/sector-flow/labels/good%20first%20issue) 목록에서 시작하세요.
이슈마다 볼 파일·줄, 완료 기준, 로컬 테스트 명령이 적혀 있습니다.

## 개발 환경

Python 3.11+ 와 [uv](https://docs.astral.sh/uv/) 가 필요합니다. 테스트는 DB·네트워크 없이 돕니다.

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src/ tests/ scripts/ research/
uv run python -m compileall -q research
```

화면(TUI·웹)을 고쳤다면 실제 렌더와 pty 구동도 봅니다(수 분, 실 리포트가 없으면 합성 데모로 돕니다).

```bash
uv run python scripts/verify_merge.py <워크트리> --only render,pty
```

## 바꿀 때 지키는 것

- **말**: 화면·README 의 글은 금칙어 표(`src/sectorflow/wording.py`)를 통과해야 합니다. "추천·유망·신호" 같은 말을 쓰지 않고,
  섹터 지표를 앞날 수익률의 신호로 소개하지 않습니다 — 문서·도움말·커밋 메시지 모두.
- **새 검사**를 넣으면 `tests/mutations.toml` 에 그 검사가 잡아야 할 변이를 같이 넣습니다(검사가 정말 무는지 기계가 봅니다).
- **수식**의 정본은 `docs/formulas.md` 입니다. 코드를 옮기면 그 `파일:줄` 인용도 고칩니다(`uv run python scripts/docs_refs.py --fix`).
- **화면 그림**을 바꾸면 합성 데모로 다시 찍습니다(`scripts/demo_shots.py`).
- **커밋**은 conventional 꼴을 씁니다(`fix(flow): …`). 한국어를 권하지만 영어도 받습니다.

## PR

1. 이슈로 먼저 이야기하면 방향이 어긋날 일이 줄어듭니다(작은 수정은 바로 PR 도 좋습니다).
2. PR 하나에 한 가지 변경. CI 가 초록이어야 머지합니다.
3. PR 템플릿의 확인 항목을 채워 주세요.

기여한 코드는 이 저장소의 [MIT 라이선스](LICENSE)를 따릅니다. 모든 참여자는 [행동 강령](CODE_OF_CONDUCT.md)을 지킵니다.
