## 무엇을 바꾸나

<!-- 한두 줄. 관련 이슈가 있으면 #번호 -->

## 확인

- [ ] `uv run --extra dev pytest -q` 초록
- [ ] 코드를 고쳤다면 `uv run --extra dev ruff check src/ tests/ scripts/ research/` 초록
- [ ] 화면을 고쳤다면 `uv run python scripts/verify_merge.py <워크트리> --only render,pty` 를 돌렸고, 그림은 합성 데모로 다시 찍었다
- [ ] 새 검사를 넣었다면 `tests/mutations.toml` 에 변이를 넣었다
- [ ] 실데이터·리포트 폴더·실데이터 스크린샷이 이 PR(코드·문서·그림·본문)에 없다
- [ ] 화면·문서의 글이 금칙어(`src/sectorflow/wording.py`)를 통과하고, 지표를 앞날 수익률의 근거처럼 소개하지 않는다
