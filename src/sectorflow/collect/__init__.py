"""BYO(자기 키) 수집기 — 사용자 본인의 키움 REST 키로 리포트가 읽는 sqlite 를 채운다.

설계: ``docs/byo/phase1-equivalence.md``·``phase2-equivalence.md``(동등성 결과)와 BYO 타당성 문서 §2.
키움은 **읽기 전용 시세·수급 TR 6개만** 부른다(:data:`sectorflow.collect.kiwoom.TR_PATHS`). 주문 TR 은 경로
목록에 없고, 목록 밖 ``api-id`` 는 요청 전에 막힌다(``tests/test_collect_kiwoom.py`` 가 정적으로도 본다).

- :mod:`.auth` — 앱키 로드(환경변수 → ``.env``)와 로그 마스킹.
- :mod:`.kiwoom` — 토큰 1개, TR별 속도 제한, 429·``return_code 5`` 백오프, 헤더 연속조회, 호출 상한.
- :mod:`.fetch` — TR 응답 → ``storage.SCHEMA`` 행.
- :mod:`.write` — sqlite 업서트와 ``collect_runs`` 원장.
- :mod:`.shares` — 현재 주식수 + 수정주가 이벤트 역적용 시드.
- :mod:`.dart` — (선택) 본인 OpenDART 키로 분기 발행주식총수 이력. 목적지 ``opendart.fss.or.kr`` 경로 둘뿐.
- :mod:`.run` — ``smoke`` · ``backfill`` · ``reseed`` · ``dart-shares`` CLI.
- :mod:`.equiv` — kr_quant 조각 추출과 페이로드 대조(Phase 1 동등성 시험).
"""
