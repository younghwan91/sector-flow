"""자료 범위 표지 — 이 리포트의 숫자가 **어느 거래소 체결**인가. 화면(TUI 도움말)과 웹(두 페이지 notes)이 이 한 곳을 쓴다.

근거(2026-11-08 실측, 읽기 전용)는 ``docs/formulas.md`` §1.0. 요약: 수급·일봉·조정 일봉은 키움 TR 을 접미사 없는
6자리 코드로 부른다(키움 규약: KRX ``005930`` · NXT ``005930_NX`` · 통합 SOR ``005930_AL``). 그래서 넥스트레이드
체결은 어디에도 없다 — NXT 가 섞였다면 2025-03 개장·확대 뒤 Σ``daily_bars.trade_value`` ÷ KRX 종합지수 거래대금
비가 계단처럼 올라야 하는데 2024-09~2026-11 내내 평평하다(코스닥 0.994~1.000, 거래소 0.960~0.975).
"""
from __future__ import annotations

import math

#: 화면·웹에 그대로 나가는 한 줄. 바꾸면 TUI 도움말·웹 두 페이지가 같이 바뀐다(``tests/test_data_basis.py``).
DATA_BASIS = "자료 범위: KRX 단독(수급·거래량·종가) — 넥스트레이드(NXT) 체결 제외"

#: 웹 템플릿의 자리표시 — ``sector_flow.py``·``sector_numbers.py`` 가 ``__DATA__`` 와 같이 갈아 끼운다.
BASIS_SLOT = "__BASIS__"

#: 첫 화면(TUI '오늘의 수급')과 웹 '오늘' 패널 머리에 붙는 짧은 표지 — 도움말·notes 까지 안 내려가도 보이게
#: (2026-11-08 리뷰 3차 D1-m5). 뜻은 :data:`DATA_BASIS` 와 같다.
DATA_BASIS_SHORT = "자료 범위: KRX 단독·NXT 제외"
#: 수치표 템플릿의 짧은 표지 자리표시 — ``sector_numbers.py`` 가 갈아 끼운다.
BASIS_SHORT_SLOT = "__BASIS_SHORT__"

#: TUI 도움말 끝에 붙는 절(``sw-flow``·``sw-ledger`` 공통). 폭 80 에서 안 잘리게 짧게 끊는다.
BASIS_HELP = [
    ("", ""),
    ("", "── 자료 범위 ──"),
    ("", "· " + DATA_BASIS),
    ("", "  키움 KRX 코드(접미사 없음) 응답이라 시장 전체 체결이 아니라 KRX 몫이다."),
    ("", "  2026-09-14~ 종가·거래량은 KRX 애프터마켓(~20:00) 포함 — NXT 가 아니다."),
]

#: 합성 데모(``sw-flow --demo``)의 상시 표지 — TUI 제목 줄 맨 앞과 웹 두 장 맨 위. 짧은 쪽이 제목 줄, 긴 쪽이 웹 띠다.
DEMO_MARK = "[합성 데모]"
DEMO_NOTICE = "합성 데모 — 난수로 만든 가짜 숫자·가짜 이름이다. 실제 시장·종목과 무관하다."

# ── 값 표시 규칙 — TUI·다이제스트가 같은 글자를 내도록 이 한 곳에 둔다(``flow_view`` 를 부르지 않는 다이제스트도 쓴다).

def rhu(v: float, nd: int = 0) -> float:
    """반올림 — 0.5 는 0 에서 먼 쪽으로. **웹 JS 의 ``RHU`` 와 같은 IEEE 연산**이다.

    파이썬 ``f"{x:.0f}"`` 는 은행가 반올림(96.5 → 96)이고 JS ``Math.round`` 는 위로(97)라, 같은 칸이
    화면 96 · 웹 97 로 갈렸다(2026-11-08 리뷰 M6). ``f"{x:+.2f}"`` 도 이진 표현을 따라 −26.415 를 −26.41 로 적어
    다이제스트만 TUI·웹(−26.42)과 달랐다. 두 언어에서 똑같이 ``floor(|v|·10ⁿ + 0.5 + 1e-9)`` 를 한다 —
    ``1e-9`` 는 1.005×100 = 100.4999… 같은 이진 잔차를 십진 의도(올림)로 되돌린다.
    """
    m = 10 ** nd
    q = math.floor(abs(v) * m + 0.5 + 1e-9) / m
    return q if v >= 0 else -q


#: 수익률 라벨 — 2026-09-14 부터 일별 종가가 애프터마켓(~20:00) 포함 통합값이라 확정 리포트의 수익률은
#: "전일 20:00 → 당일 20:00" 이다(정규장 15:30 종가가 아니다). KRX 지수(정규장)와 같은 날 크게 갈릴 수 있다.
RET_LABEL = "전일 20:00→당일 20:00 (애프터마켓 포함 통합 종가)"
RET_LABEL_SHORT = "~20시 통합종가"
#: 잠정 리포트(18:10)의 마지막 날은 **수집 시각**(~16:00~18:10) 스냅숏이다 — 애프터마켓은 20:00 까지 돈다
#: (quant-airflow CLAUDE.md §3). 확정 때 바뀐다(실측 11-05 거래소 기관 +8,855 → +9,658억).
RET_LABEL_PROV = "전일 20:00→당일 수집 시각 (애프터마켓 진행 중)"
RET_LABEL_PROV_SHORT = "~수집시각 잠정"


def ret_label(d: dict, short: bool = False) -> str:
    """이 리포트의 수익률 라벨 — 마감 값이면 ~20:00, 아니면(잠정·장중) 수집 시각까지.

    마감 값 = 확정, 또는 daily_finalize 창 밖이라 확정 기록은 없지만 두 달 지난 날의 값(재구성 ``finalize_unverified``).
    그런 날을 ``final`` 만 보고 "애프터마켓 진행 중" 이라 적으면 거짓이다.
    """
    if d.get("final") or d.get("finalize_unverified"):
        return RET_LABEL_SHORT if short else RET_LABEL
    return RET_LABEL_PROV_SHORT if short else RET_LABEL_PROV


def status_label(d: dict) -> str:
    """값의 지위 — 마감·확정 / 마감·확정기록없음 / 마감·잠정 / 장중·미확정, 재구성이면 꼬리표. TUI·다이제스트가 같이 쓴다.

    ``final`` 은 다음날 아침 확정치로 다시 만든 리포트에만 있다. ``finalized`` 는 시계만 본다(수집 창이 지났나) —
    값이 확정됐다는 뜻이 아니다. ``rebuilt`` 은 지난 기준일을 지금 코드·확정 데이터로 다시 만든 리포트다(그날 게시본이 아니다).
    """
    base = ("마감·확정" if d.get("final") else "마감·확정기록없음" if d.get("finalize_unverified")
            else "마감·잠정" if d.get("finalized") else "장중·미확정")
    return base + "·재구성" if d.get("rebuilt") else base


#: 첫 화면·다이제스트의 면책 — 무엇을 검정했고 무엇을 안 했는가. 하루 값·1년 %ile·연속 일수는 앞날을 검정한 적이 없다
#: (검정 범위는 ``docs/validation/`` — 섹터 20·60일 수급 지표). 1년 %ile 을 "검정함 — 증거 없음" 에 묶어 적으면
#: 검정하지 않은 것을 검정했다고 말하는 셈이다.
NOT_TESTED_NOTE = "1일 값·1년 %ile·연속 일수는 앞날 검정 안 함"
