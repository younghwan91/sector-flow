"""섹터 자금흐름 TUI 의 **순수 렌더 로직** — curses 를 import 하지 않는다.

화면 그리기(curses)와 무엇을 그릴지(여기)를 나눈다. 이 파일은 데이터와 상태를
받아 문자열 행 목록을 돌려주므로 단위 테스트가 된다. curses 를 섞으면 렌더가
터미널 없이는 검증 불가능해지고, 열 정렬이 어긋나도 아무도 못 잡는다 —
이 저장소는 그 실수를 이미 두 번 했다(표 헤더와 셀 개수 불일치).
"""

from __future__ import annotations

import math
import os
import re
import unicodedata
from collections import namedtuple
from datetime import date

from sectorflow.basis import BASIS_HELP, DATA_BASIS_SHORT

#: East Asian Width 가 'A'(Ambiguous) 인 글자를 **두 칸으로 센다**.
#:
#: 이 화면들은 `·` `—` `↑` `↓` `→` `×` `÷` `²` `½` `Δ` `Σ` `β` `▲` `▼` `※` `≠`
#: `…` `─` 같은 'A' 글자를 쓴다. 유니코드는 이 글자들의 폭을 **정하지 않았고**,
#: 터미널이 정한다 — 대부분 1칸이지만 한국어권에서 흔한 "ambiguous=wide" 설정
#: (PuTTY 의 "Treat CJK ambiguous chars as wide", iTerm2·mintty 의 같은 옵션)
#: 에서는 2칸으로 그린다. 그러면 `cell_width` 가 1로 센 칸이 2칸을 먹어 **그 줄
#: 오른쪽이 통째로 밀리고**, `pad(..., width)` 로 폭에 딱 맞춘 줄은 넘쳐서 다음
#: 줄로 접힌다(화면 전체가 어긋난다).
#:
#: 글자를 ASCII 로 바꾸는 대신 **세는 규칙을 터미널에 맞추는** 쪽을 골랐다.
#: 이유는 세 가지다. (i) 'A' 글자가 31종·440여 곳이라 일부만 바꾸면 나머지가
#: 그대로 밀린다 — 부분 치환은 문제를 줄일 뿐 없애지 못한다. (ii) `Σ` `Δ` `β`
#: `÷` 처럼 뜻을 지고 있어 ASCII 로 옮기면 길어지거나 읽기 나빠지는 것이 있다.
#: (iii) 폭 계산이 전부 `cell_width` 한 곳을 지나므로, 여기만 고치면 표·푸터·
#: 도움말·원장까지 한 번에 맞는다(원장 블록 문자 ``▁▂▃█▒▓▌`` 도 'A' 다).
#:
#: 켜지 않은 기본값에서는 예전과 **한 글자도 다르게 세지 않는다.** 실측: 폭 80
#: 에서 그려지는 14,473 줄이 기준과 바이트 단위로 같다. 켜면 그 터미널에서
#: 폭을 넘던 6,144 줄(42.5%, 최대 120칸)이 0 이 된다.
#:
#: ⚠️ **아직 남은 것** — 켜면 줄이 밀리지는 않지만 내용이 좁아진다. `²` `½` 가
#: 든 헤더(`풀림[%p/일²]`·`포텐셜[½kx²]`)는 폭이 딱 맞게 잡혀 있어 한두 칸
#: 잘리고, 도움말·힌트바는 한 단계 짧은 문구로 내려간다. 원장의 막대·히트맵은
#: 블록 문자가 1칸이라고 **가정하고 칸을 세므로**(`heat_cell`·`signed_bar`)
#: 모드를 켜면 그림이 어긋난다 — 그건 이 커밋이 안 건드린 자리다.
#: 기본값이 꺼짐이라 오늘 아무도 이걸 밟지 않지만, 켜는 사람은 알아야 한다.
AMBIGUOUS_WIDE = os.environ.get("KQ_AMBIGUOUS_WIDE", "").strip().lower() \
    not in ("", "0", "false", "no", "off")

WINDOWS = ("1", "5", "20", "60", "120", "종합")
#: **1일 창** — 기준일 하루. 섹터 블록(`blocks`)에는 없고 페이로드 `today`(sector_flow.today_block)에서 만든다.
#: 하루치에는 사실(4주체 순매수·그 1년 백분위·수익률·거래대금)만 있다 — 가속·미실현·풀림·G·추이 같은 파생값은
#: 회귀·차분·누적 경로가 하루에서 정의되지 않아 `—` 다. `today` 가 없는 옛 리포트에서는 `w` 가 이 창을 건너뛴다.
ONE_DAY = "1"
#: 얇은 섹터 문턱 — ``sector_numbers.MIN_NAMES`` 와 같은 값. 1일 창은 블록이 없어 화면이 직접 센다.
THIN_MIN = 10
# 종목 목록은 **절대 순매수 금액** 순이다 — 섹터 합계가 금액의 합이므로 기여도는
# 금액으로만 정의된다. 시총 대비는 참고 열. (시총 대비로 줄세웠더니 스팩이 1위가
# 됐고, 그걸 막으려 유동성 하한을 넣었더니 작은 섹터가 통째로 비었다.)
MARKET_ORDER = ("거래소", "코스닥")

#: 중첩 누적창을 **겹치지 않는 구간**으로 자르는 표 — (라벨, 넓은창, 좁은창).
#:
#: 5⊂20⊂60⊂120 은 끝 날짜가 같고 시작만 다른 누적이다(리포트의 `from`/`to` 가
#: 그렇게 찍힌다). 네 창을 그대로 나란히 놓으면 **최근 5일치가 네 줄에 네 번**
#: 세어져, 네 숫자가 사실상 같은 것을 말한다. 빼면 겹치지 않는 기간이 된다.
#:
#: 이건 취향이 아니라 계량의 표준 처방이다 — 중첩 관측(overlapping observations)
#: 은 관측 수가 더 많은데도 비중첩보다 편향이 크고, 중첩 보정은 t 통계량을 크게
#: 부풀린다(Boudoukh·Richardson·Whitelaw, *The Myth of Long-Horizon
#: Predictability*, RFS 2008; Valkanov, JFE 2003). (swing-it 의 docs/GUARDRAILS.md §3 참고.)
#:
#: 마지막 칸(최근 5일)은 **따로 남긴다**. 수익률 쪽에서는 단기 반전이 표준 사실이라
#: 모멘텀이 최근 한 달을 건너뛰지만(Jegadeesh 1990; Jegadeesh & Titman 1993),
#: 그건 수익률 이야기지 **수급 이야기가 아니다** — 기관 수요는 오히려 지속된다는
#: 쪽이 문헌이다(Sias, *Institutional Herding*, RFS 2004). 그래서 이 화면은
#: 최근 구간을 **분리해서 보여주기만 하고 좋다/나쁘다 부호를 붙이지 않는다.**
#: 눌림목이냐 추세전환이냐는 가격 구조를 봐야 갈리는데, 이 리포트에는 구간
#: 수익률 네 개뿐이라 그 판정을 할 자료가 없다.
TREND_SEGS = (("120-60", "120", "60"), ("60-20", "60", "20"),
              ("20-5", "20", "5"), ("5", "5", None))

#: 구간 사이 화살표. **ASCII 만 쓴다** — `→` 는 East Asian Width 가 'A' 라
#: 한글 터미널에서 2칸으로 그려져 그 줄만 밀린다(이 파일 맨 위 주석 참고).
TREND_ARROW = "->"

#: 추이에서 보는 주체 — (페이로드 키, 화면 이름). **투신·연기금만** 본다.
#: 기관 총액은 금투(증권사 자기매매 — 헤지·차익이 섞여 방향성이 약하다)를 안고
#: 있어 "누가 샀나" 를 흐린다. 외국인·개인은 여기 안 넣는다 — 종목을 고르는 데
#: 쓰는 자금 성격은 이 둘이다.
TREND_ACTORS = (("invtrt", "투신"), ("penfnd_etc", "연기금"))


def segment_flows(win: dict, key: str) -> list[tuple[str, float | None]]:
    """중첩 누적창(`win`)을 겹치지 않는 구간으로 차분한다. 오래된 → 최근 순.

    반환은 `[(라벨, 값), …]`. **결측은 0 이 아니라 결측으로 번진다** — 신규상장·
    거래정지 종목은 긴 창이 비어 있는데, 0 으로 메우면 화면이 "그동안 아무도 안
    샀다" 로 읽힌다. 사실은 "그 기간이 없다" 다.
    """
    win = win or {}
    out: list[tuple[str, float | None]] = []
    for label, wide, narrow in TREND_SEGS:
        w = (win.get(wide) or {}).get(key)
        if w is None:
            out.append((label, None))
            continue
        if narrow is None:
            out.append((label, w))
            continue
        n = (win.get(narrow) or {}).get(key)
        out.append((label, None if n is None else w - n))
    return out


def sector_actor_win(d: dict, sector: str, markets, key: str) -> dict:
    """섹터의 창별 `key` 합계 — 섹터 row 에 투신·연기금이 **없어서** 종목에서 더한다.

    합산이 정당한지는 실측으로 확인했다(2026-09-04 전체 20일): 종목 합계가 섹터
    `inst` 와 0.1억 이내로 일치한다 — `names` 가 전수라서다. 다만 종목 **수**는
    `n_all` 과 몇 개 어긋나므로(유통 164 vs 166) 이 함수는 금액만 돌려준다.
    """
    mkts = set(markets or ())
    out = {w: 0.0 for w in ("5", "20", "60", "120")}
    for nm in (d.get("names") or {}).values():
        if nm.get("sector") != sector or nm.get("market") not in mkts:
            continue
        for w in out:
            v = ((nm.get("win") or {}).get(w) or {}).get(key)
            if v is not None:
                out[w] += v
    return out


#: k·b 가 기관 유입에 회귀해 나온 값들. 다른 주체로는 재계산할 수 없다.
INST_ONLY = ("exp", "x", "U", "P", "xdot", "xddot", "G", "G_pass")

ACTORS = (("inst", "기관"), ("forgn", "외국인"), ("indiv", "개인"), ("etc", "기타법인"))


def absent_row(r: dict) -> bool:
    """그 시장에 **종목이 하나도 없는** 섹터 행인가(예: 코스닥 보험).

    리포트는 시장마다 전 섹터 행을 싣는다. 종목이 없으면 순매수·시총이 0 이라
    표에 `+0`·1년 백분위 `50`·평평한 추이가 찍혀, 없는 섹터가 "조용한 섹터" 로
    읽혔다. 웹(수치표·뷰어)도 같은 기준으로 뺀다 — 한쪽만 빼면 두 화면의 섹터
    목록이 갈라진다. `n_all` 이 없는 옛 리포트는 빼지 않는다.
    """
    return r.get("n_all") == 0 and not r.get("cap")
#: 화면 정렬. **기본은 가속** — 규모로 정규화된 유입이라 대형 섹터가 늘 이기지
#: 않는다. 예전 기본은 G 였는데, G 는 세 순위의 평균인 **탐색 점수**(검정 결과 판정 불가)라
#: 화면 순서를 지배할 근거가 없다(20일 기본 화면 1위가 임펄스 +14억짜리 섹터였고,
#: 기관이 2.4조 판 전기/전자는 24위라 스크롤해야 보였다). G 는 보조로 남는다.
#: 순서는 **열 순서와 같다** — 정렬을 돌릴 때 눈이 왼쪽에서 오른쪽으로 따라간다.
#:
#: 미실현·풀림가속·dW/dt 는 x 계열이다(x ≈ 섹터 20일 반전·앞날 증거 없음, 나머지는 검정 안 한 [측정]; G 의 입력은 a·x·ẍ) — G 와 같이
#: **탐색 모드**에서만 닿는다(2026-11-08 리뷰 M1). 포텐셜(½kx²)은 |미실현| 의 단조 변환이라 지웠다.
SORTS = (("accel", "가속"), ("flow", "임펄스"), ("pct", "1년%"), ("ret", "수익률"),
         ("trust5", "투신+연기금5일"), ("trustw", "투신+연기금"),
         ("x", "미실현"), ("xddot", "풀림가속"), ("P", "dW/dt"), ("G", "G"),
         ("tv", "거래대금"), ("cap_idx", "시총"), ("n_all", "종목수"))
#: 종목 목록의 정렬. 기본은 **절대 순매수** — 섹터 합계가 금액의 합이므로
#: "누가 이 섹터를 움직였나" 는 금액으로만 정의된다. 나머지는 다른 질문에 답한다.
#: (`flow` 는 **절댓값** 순이다. 부호순으로 두면 판 종목이 목록 맨 끝으로 밀려
#: 누적 기여율이 뜻을 잃는다 — 섹터를 움직인 건 산 쪽과 판 쪽 둘 다다.)
NAME_SORTS = (("flow", "순매수"), ("pick", "선정점수"),
              ("invtrt", "투신"), ("penfnd", "연기금"),
              ("conc", "최근집중"), ("rrel", "상대수익"),
              ("part", "참여율"), ("a", "시총대비"),
              ("cap", "시총"), ("tv", "거래대금"), ("name", "종목명"))

#: **탐색 모드**(`e`)에서만 닿는 자리 — 종합 창(G 를 섞은 축)·섹터 선정(G)·종목 선정점수.
#: 검정 결과(docs/validation/2026-11-08-results.md·…-usefulness-results.md): 가속·임펄스·x 는
#: 앞날 증거 없음, G 는 검정력이 없어 판정 불가, 종목 투신+연기금 순위는 부호가 **음**이다
#: (선정점수는 그것을 클수록 좋다고 센다). 지우지 않고 기본 화면에서 숨긴다.
EXPLORE_WINDOWS = ("종합",)
EXPLORE_SORTS = ("G", "x", "xddot", "P")
EXPLORE_NAME_SORTS = ("pick",)
#: 탐색 모드에서만 싣는 열 헤더 — 섹터 표의 선정(G)·x 계열과 종목 목록의 선정점수.
EXPLORE_COLS = ("G", "선정", "미실현[%p]", "풀림가속[%p/일²]", "dW/dt[%p/일]")
#: 탐색 모드가 켜져 있을 때 화면이 늘 다는 표지. 줄이 좁으면 뒤의 것부터 쓴다.
EXPLORE_LABEL_TIERS = ("탐색 — 검정 결과 앞날 증거 없음(G 판정 불가) · 종목 투신+연기금 축은 검정 부호와 반대",
                       "탐색 — 검정 결과 앞날 증거 없음(G 판정 불가)",
                       "탐색 — 앞날 증거 없음", "탐색")

#: 최근집중의 **분자가 되는 창**. 5 ⊂ 20 ⊂ 60 ⊂ 120 으로 겹치는 것이 여기선
#: 자산이다 — 이미 실린 값의 비율일 뿐이라 페이로드가 한 바이트도 안 늘어난다.
#: (종목별 일별 배열을 실으면 ~4MB 가 붙는다고 `scripts/sector_flow.py` 가 경고한다.)
CONC_WIN = "5"
#: 최근집중의 분모 하한(억) — **반올림 때문에** 필요한 것.
#:
#: 페이로드는 종목 집계를 0.1억 단위로 반올림한다. 분모가 1억이면 ±0.05억, 즉
#: ±5% 의 오차가 그대로 비율에 실린다. 0.2억이면 ±25% 다 — 그 아래에서는 화면이
#: 반올림 잡음을 지속성으로 읽어 준다. 이건 다음 상수(:data:`CONC_MIN_SHARE`)와
#: **다른 이유**의 하한이다: 이쪽은 "그 비율을 계산할 수 있나", 저쪽은 "그 비율을
#: 볼 이유가 있나" 다. 둘 다 필요해서 둘 다 둔다.
CONC_MIN_DEN = 1.0
#: 최근집중을 보일 **최소 몫**(그 종목 |순매수| ÷ 화면 목록 전체의 Σ|순매수|, %).
#:
#: 왜 절대 금액이 아닌가: 섹터 규모가 세 자릿수 배로 갈린다. 실측(20일, 기관) —
#: 누적 80% 를 처음 넘긴 행의 |순매수| 가 거래소/제조는 0.8억인데 전기/전자는
#: 2,435억이다(46개 (시장,섹터), 중앙값 108억). 여기에 고정 임계 10억을 걸면
#: **자기 섹터를 실제로 움직인 행의 7.7%가 함께 지워지고** 얇은 섹터는 통째로
#: 빈다. 규모가 아니라 **그 목록 안에서의 몫**이 판단 기준이다.
#:
#: 0.5% 를 고른 근거(같은 실측):
#:   몫 임계   전체 행 중 빔   누적 80% 위 행 중 빔
#:    0.2%        70.6%             0.0%
#:    0.5%        79.0%             0.0%
#:    2.0%        87.8%             9.2%
#: 0.5% 는 노이즈 벌판을 79% 지우면서 "이 섹터를 움직였다" 고 화면이 이미 부르는
#: 행은 **한 줄도** 안 지운다. 2% 로 올리면 그쪽을 깎기 시작한다.
#:
#: 지적된 줄들이 정확히 이 아래에 있다: 금호건설 몫 0.049%(순매수 +3억에 −287%),
#: 계룡건설 0.063%, HJ중공업 0.178%. 값이 틀린 게 아니라 **볼 이유가 없는 줄이
#: 화면에서 가장 큰 숫자를 달고 있었다.**
#:
#: 가두기(−100~+200 클리핑)를 안 쓴 이유: 경계값이 진짜 값처럼 보이는 새 거짓말을
#: 만든다. 결측으로 빼면 정렬에서도 맨 뒤로 간다 — 이 화면의 다른 결측과 같은
#: 규칙이다("값이 없는 것은 작은 값이 아니다").
CONC_MIN_SHARE = 0.5
#: 최근집중 표시 상한(%). 넘으면 ``>999``·``<-999`` 로 적는다 — 자릿수가
#: 열을 밀지 않게 하면서 "매우 크다" 는 사실은 남긴다.
CONC_CAP = 999.0

#: 투신·연기금은 **억원 절대값**으로 적는다.
#:
#: "기관 순매수 대비 몫 %" 안을 같이 그려 비교했고 **버렸다.** 몫은 분모(기관
#: 총액)가 6개 세부의 합이라 서로 상쇄되면 0 근처로 내려가고, 그때 100% 를 넘거나
#: 부호가 뒤집힌다 — 실측(건설 20일): 금호건설 순매수 +3억에 투신 +123% · 연기금
#: +177%, 대명에너지 −8억에 투신 +111%, 계룡건설 −4억에 연기금 +102%. 정작 구성을
#: 알고 싶은 애매한 종목에서 깨지고 큰 종목에서만 멀쩡했다. 절대값은 크기와 구성을
#: 동시에 준다 — GS건설 +2,124 중 연기금 +1,047 이 그 자리에서 읽힌다.
#: 헤더 이름(열의 정체) — 증거 표지는 붙이지 않는다. 드릴다운 헤더가 **창·주체마다** 계산해 덧붙인다
#: (:func:`name_head`). 예전엔 ``[음]`` 을 박아 1·5·60일에도, 검정하지 않은 개별 열에도 달았다(2026-11-08 3차 리뷰 D2-M1).
COL_INVTRT, COL_PENFND = "투신[억]", "연기금[억]"

#: 누적 기여율의 가로줄을 그을 지점. 실측상 섹터 흐름의 80% 를 설명하는 종목이
#: 평균 6.2개인데 종목행의 51% 가 +0(|순매수|<0.5억) 이다 — 어디까지가 "그 섹터를
#: 움직인 종목" 인지 눈으로 끊어준다.
CUM_CUT = 80.0


def rhu(v: float, nd: int = 0) -> float:
    """반올림 — 0.5 는 0 에서 먼 쪽으로. **웹 JS 의 ``RHU`` 와 같은 IEEE 연산**이다.

    파이썬 ``f"{x:.0f}"`` 는 은행가 반올림(96.5 → 96)이고 JS ``Math.round`` 는 위로(97)라, 같은 칸이
    화면 96 · 웹 97 로 갈렸다(2026-11-08 리뷰 M6). 두 언어에서 똑같이 ``floor(|v|·10ⁿ + 0.5 + 1e-9)`` 를
    한다 — ``1e-9`` 는 1.005×100 = 100.4999… 같은 이진 잔차를 십진 의도(올림)로 되돌린다.
    """
    m = 10 ** nd
    q = math.floor(abs(v) * m + 0.5 + 1e-9) / m
    return q if v >= 0 else -q


def pct_int(p: float) -> int:
    """백분위 표시값(정수) — :func:`rhu` 와 같은 규칙."""
    return int(rhu(p))


def fmt_amt(v) -> str:
    if v is None:
        return "—"
    r = rhu(v)
    return ("+" if v >= 0 else "-") + f"{abs(r):,.0f}"


def fmt_conc(v) -> str:
    """최근집중 표시 — 자릿수가 열을 밀지 않게 ±999 에서 자른다."""
    if v is None:
        return "—"
    if v > CONC_CAP:
        return ">999"
    if v < -CONC_CAP:
        return "<-999"
    return f"{rhu(v):.0f}"


def fmt_pct(v, nd: int = 2) -> str:
    if v is None:
        return "—"
    return ("+" if v >= 0 else "-") + f"{abs(rhu(v, nd)):.{nd}f}"


#: 권리락이 지났는데 신주가 아직 상장주식수에 안 들어온 종목의 시총 칸 표지. 가격은 권리락 뒤인데 주식수가 옛 값이라
#: 시총이 작게, 시총대비가 크게(최대 약 2배) 나온다. 정의 ``storage.rights_pending``, 근거 ``docs/formulas.md`` §1.4.
CAP_PENDING_MARK = "!"


def _cap_mark(t: dict) -> str:
    return CAP_PENDING_MARK if t.get("cap_pending") else ""


def cap_pending_note(d: dict, mkts: list, sector: str) -> str:
    """이 시장·섹터에 표지 종목이 있으면 그 수를 말하는 글 — 섹터 시총(가속의 분모)도 그만큼 작게 나온다. 없으면 빈 글."""
    n = sum(1 for nm in (d.get("names") or {}).values()
            if nm.get("cap_pending") and nm.get("sector") == sector and nm.get("market") in mkts)
    return f" · {CAP_PENDING_MARK}권리락 신주 미반영 {n}종목" if n else ""


def cell_width(ch: str) -> int:
    """문자 하나의 터미널 표시 폭.

    ``ord(c) > 0x1100`` 같은 어림은 쓰지 않는다 — U+2212(−, 마이너스)가 그 범위에
    들어가 2칸으로 세어졌고, 음수 행이 한 칸씩 밀렸다. 유니코드 표준
    East Asian Width 가 'W'(Wide)·'F'(Fullwidth)인 것만 2칸이다.

    'A'(Ambiguous) 는 터미널이 정한다 — ``AMBIGUOUS_WIDE`` 주석을 보라.
    """
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("W", "F"):
        return 2
    return 2 if (AMBIGUOUS_WIDE and eaw == "A") else 1


def cell_len(text: str) -> int:
    """표시 칸 수 — 한글·전각은 두 칸. 문자 수가 아니다."""
    return sum(cell_width(c) for c in text)


def tier_for(tiers: tuple[str, ...], width: int) -> str:
    """폭에 **온전히** 들어가는 가장 자세한 단계. 하나도 안 들어가면 마지막 단계.

    긴 것부터 짧은 것까지 손으로 쓴 문구를 늘어놓고 폭에 맞는 것을 고르는 기법은
    이 화면들이 이미 네 군데에서 쓴다(푸터·드릴다운 푸터·도움말 제목·원장 배너,
    그리고 종합 힌트바). 네 벌이 각자 구현돼 있었고 이미 갈라져 있었다 —
    푸터는 못 맞으면 마지막 단계로 내려갔는데, 도움말 제목은 ``next()`` 를
    기본값 없이 써서 폭 6 이하에서 ``StopIteration`` 으로 **TUI 를 통째로
    죽였다**(실측: 폭 1·5·6 크래시, 7 부터 정상). 같은 사실을 네 번 적으면
    이렇게 갈라진다.

    폭을 넘겨 잘라내지 않는 이유는 푸터에 적힌 그대로다 — 잘린 안내문은
    "여기가 전부" 로 읽힌다. 줄이려면 **더 짧게 쓴 문장**으로 바꿔야 한다.

    호출자가 앞에 공백 따위를 덧붙인다면 그만큼 뺀 폭을 넘겨라.
    """
    return next((t for t in tiers if cell_len(t) <= width), tiers[-1])


def clip(text: str, width: int) -> str:
    """``width`` 칸에 안 들어가면 **말줄임표를 붙여** 자른다 — 이름 열 전용.

    `pad` 는 넘치면 소리 없이 자른다. 종목명이 그렇게 잘리면 `블리츠웨이엔터테`
    처럼 다른 이름으로 읽힌다(잘린 줄 모른다). 말줄임표 폭은 `cell_width` 에 묻는다 —
    `…` 는 'A' 글자라 터미널에 따라 두 칸이다.
    """
    if cell_len(text) <= width:
        return text
    ell = "…"
    room = width - cell_width(ell)
    out, used = "", 0
    for c in text:
        w = cell_width(c)
        if used + w > room:
            break
        out += c
        used += w
    return out + ell


def pad(text: str, width: int, right: bool = False) -> str:
    """표시 폭 기준 패딩(한글 2칸). 넘치면 자른다."""
    out = ""
    used = 0
    for c in text:
        cw = cell_width(c)
        if used + cw > width:
            break
        out += c
        used += cw
    space = " " * (width - used)
    return (space + out) if right else (out + space)


class State:
    """화면 상태 — 어떤 창·시장·주체·정렬로 무엇을 선택했나."""

    def __init__(self, data: dict):
        self.d = data
        seen = {k.split("|")[1] for k in data["blocks"]} - {"전체"}
        # set 순서는 PYTHONHASHSEED 마다 다르다. "m 두 번 = 코스닥" 손버릇이
        # 어느 날 조용히 깨지므로 고정 순서로 둔다.
        self.markets = ["전체"] + [m for m in MARKET_ORDER if m in seen] + \
            sorted(seen - set(MARKET_ORDER))
        self.wi = WINDOWS.index("20") if "20" in WINDOWS else 0
        self.mi = 0
        self.ai = 0
        self.si = 0
        self.row = 0
        self.drill = False      # 종목 목록 화면인가
        self.allv = False       # 전 종목(곱) 화면인가 — `t`
        self.arow = 0           # 전 종목 화면의 커서
        self.drow = 0           # 종목 목록에서 선택된 행
        self.nsi = 0            # 종목 정렬
        self.help = False       # 도움말 화면인가
        self.hrow = 0           # 도움말 스크롤
        # 정렬 역순. 없을 때 "순매도 상위" 로 가는 유일한 길이 G(맨 끝으로) 였는데
        # 그 키가 화면 어디에도 안 적혀 있었다 — 발견 불가능한 유일 경로.
        self.rev = False        # 섹터 표 역순(오름차순)
        self.nrev = False       # 종목 목록 역순
        # 탐색 모드 — G·선정·곱·종합. **기본은 꺼짐**이다. 이 점수들은 검정 결과 앞날
        # 증거가 없고(G 는 검정력이 없어 판정 불가) 종목 선정점수의 투신+연기금 축은
        # 검정 부호와 반대다(docs/validation/). 기본 화면은 수급 사실만 말한다.
        self.explore = False
        # 첫 화면(오늘의 수급) — 앱이 시작할 때 켜고, `o` 로 다시 연다(2026-11-08 리뷰 M8: 닫으면 다시 못 봤다).
        self.banner = False
        self.brow = 0           # 첫 화면 스크롤(화면보다 길 때)

    # --- 현재 선택 ---
    @property
    def window(self) -> str:
        return WINDOWS[self.wi]

    @property
    def market(self) -> str:
        return self.markets[self.mi]

    @property
    def actor(self) -> str:
        return ACTORS[self.ai][0]

    @property
    def sort_key(self) -> str:
        return SORTS[self.si][0]

    @property
    def name_sort(self) -> str:
        return NAME_SORTS[self.nsi][0]

    @staticmethod
    def _step(i: int, step: int, n: int, ok) -> int:
        """``i`` 에서 ``step`` 방향으로 ``ok(j)`` 인 다음 자리. 없으면 제자리."""
        for _ in range(n):
            i = (i + step) % n
            if ok(i):
                return i
        return i

    def cycle(self, what: str, step: int = 1) -> None:
        # 탐색 모드가 꺼져 있으면 탐색 전용 자리(종합 창·선정 정렬)를 **건너뛴다**.
        # 키를 눌러 닿을 수 있으면 기본 화면에서 숨긴 것이 아니다.
        if what == "w":
            self.wi = self._step(self.wi, step, len(WINDOWS),
                                 lambda j: (self.explore or WINDOWS[j] not in EXPLORE_WINDOWS)
                                 and (WINDOWS[j] != ONE_DAY or self.has_today))
        elif what == "m":
            self.mi = (self.mi + step) % len(self.markets)
        elif what == "a":
            self.ai = (self.ai + step) % len(ACTORS)
        elif what == "s":
            self.si = self._step(self.si, step, len(SORTS),
                                 lambda j: self.explore or SORTS[j][0] not in EXPLORE_SORTS)
        elif what == "ns":
            self.nsi = self._step(self.nsi, step, len(NAME_SORTS),
                                  lambda j: self.explore
                                  or NAME_SORTS[j][0] not in EXPLORE_NAME_SORTS)
            self.drow = 0
            return
        self.row = 0

    def goto_window(self, win: str) -> None:
        """그 창으로 바로 간다(`1` = 1일). 닿을 수 없는 창(옛 리포트의 1일·탐색 꺼진 종합)이면 그대로."""
        if win not in WINDOWS or (win == ONE_DAY and not self.has_today) \
                or (win in EXPLORE_WINDOWS and not self.explore):
            return
        self.wi = WINDOWS.index(win)
        self.row = 0

    def toggle_explore(self) -> None:
        """탐색 모드 켜기/끄기. 끌 때 탐색 전용 상태에 서 있으면 기본 자리로 돌린다.

        종합 창·선정 정렬·전 종목(곱) 화면에 선 채로 끄면, 끈 뒤에도 그 점수가 화면을
        정하고 있게 된다 — 꺼졌다고 적힌 것이 계속 보이는 셈이다.
        """
        self.explore = not self.explore
        if self.explore:
            return
        if self.window in EXPLORE_WINDOWS:
            self.wi = WINDOWS.index("20")
            self.row = 0
        if self.sort_key in EXPLORE_SORTS:
            self.si = 0
        if self.name_sort in EXPLORE_NAME_SORTS:
            self.nsi = 0
        self.allv = False

    # --- 데이터 ---
    @property
    def has_today(self) -> bool:
        """페이로드에 `today`(1일 창의 원천)가 있는가 — 옛 리포트에는 없다."""
        return bool(((self.d.get("today") or {}).get("sectors") or {}))

    def _today_rows(self) -> list[dict]:
        """1일 창의 섹터 행 — `today.sectors[시장]` 을 블록 행 모양으로 옮긴다(값은 그대로).

        * 4주체 = 그날 순매수, `pct1y` = 그 **일별 값**의 1년 백분위(수치표 `1년[%ile]` 의 1일 창과 같은 정의).
        * `ret` = 그날 섹터 자체 바구니 수익률, `tv` = 그날 거래대금.
        * `n_all` = 그날 집계가 있는 종목 수 — Enter 로 여는 1일 목록의 길이와 같다. 10개 미만은 `thin`.
        * `cap` 을 **싣지 않는다** — 가속(순매수÷시총)은 파생값이라 1일 창에서 `—` 다. 시총 정렬용
          `cap_idx` 만 같은 기준일의 5일 블록 행에서 가져온다(구간말 시총은 창과 무관하게 같다).
        """
        T = self.d.get("today") or {}
        secs = (T.get("sectors") or {}).get(self.market) or {}
        five = {r.get("sector"): r for r in
                ((self.d.get("blocks") or {}).get(f"5|{self.market}") or {}).get("rows") or []}
        mkts = set([self.market] if self.market != "전체" else self.markets[1:])
        count: dict = {}
        for nm in (self.d.get("names") or {}).values():
            if nm.get("market") in mkts and ONE_DAY in (nm.get("win") or {}):
                count[nm.get("sector")] = count.get(nm.get("sector"), 0) + 1
        rows = []
        for sec, v in secs.items():
            n = count.get(sec, 0)
            r = {"sector": sec, "n_all": n, "thin": n < THIN_MIN,
                 "ret": v.get("ret"), "tv": v.get("tv"),
                 "cap_idx": (five.get(sec) or {}).get("cap_idx"),
                 "pct1y": {k: (v.get(k) or {}).get("pct") for k, _ in ACTORS}}
            for k, _ in ACTORS:
                r[k] = (v.get(k) or {}).get("v")
            rows.append(r)
        return rows

    def _project(self, r: dict) -> dict:
        """행을 **선택된 주체 기준으로** 다시 쓴다.

        예전엔 임펄스 열만 주체를 따르고 가속·견인주·종목목록은 기관 값이
        그대로 남아, 한 행 안에 두 주체의 숫자가 섞였다. 정렬키도 문자열
        "inst" 라 외국인 화면에서 임펄스 열이 단조가 아니었다.

        k·b 는 기관 유입에 회귀한 것이라 x·U·P·ẍ·G 는 주체를 바꿔도
        재계산할 수 없다. 지우지 않고 남기되 헤더에 (기관) 을 붙여
        출처를 분리한다 — 값이 틀린 게 아니라 다른 주체의 값이다.
        """
        a = self.actor
        flow = r.get(a)
        cap = r.get("cap")
        out = dict(r)
        out["flow"] = flow
        out["accel"] = (flow / cap * 100) if (cap and flow is not None) else None
        # 1년 백분위·구간 모양도 **같은 주체**에서 꺼낸다. 오늘 고친 버그가 정확히
        # "한 행 안에 두 주체의 숫자가 섞이는" 것이었다 — 새 열에서 반복하지 않는다.
        out["pct"] = (r.get("pct1y") or {}).get(a)
        out["spark"] = (r.get("spark") or {}).get(a)
        out["top"] = self._leads().get(r.get("sector"))
        out["trust5"] = self.trusted_recent().get(r.get("sector"))
        out["trustw"] = self.trusted_window().get(r.get("sector"))
        return out

    def _leads(self) -> dict:
        """섹터 → 그 주체의 순매수/순매도 1위. 표의 견인주가 종목 목록과
        같은 집합에서 나오도록 `names` 에서 직접 뽑는다(페이로드의 top 은
        기관 전용이고 집계 대상도 미묘하게 달랐다)."""
        win = self.window if self.window != "종합" else self.COMBINED_WIN
        ck = (win, self.market, self.actor)
        if getattr(self, "_lead_ck", None) == ck:
            return self._lead_v
        mkts = [self.market] if self.market != "전체" else self.markets[1:]
        best: dict = {}
        for code, nm in (self.d.get("names") or {}).items():
            if nm.get("market") not in mkts:
                continue
            w = (nm.get("win") or {}).get(win)
            if not w:
                continue
            v = w.get(self.actor)
            if v is None:
                continue
            best.setdefault(nm.get("sector"), []).append(
                {"code": code, "name": nm.get("name", "—"), "flow": v})
        out = {}
        for sec, arr in best.items():
            arr.sort(key=lambda t: -t["flow"])
            # 부호가 맞는 종목만 싣는다. 파는 종목이 셋이 안 되는 섹터에서 `순매도 상위` 에
            # 순매수 종목이 끼면 "이 종목을 팔았다" 로 읽힌다.
            out[sec] = {"buy": [t for t in arr[:3] if t["flow"] > 0],
                        "sell": [t for t in arr[::-1][:3] if t["flow"] < 0],
                        "n": len(arr)}
        self._lead_ck, self._lead_v = ck, out
        return out

    #: 정렬키가 그 블록에서 전멸했을 때 대신 쓸 키 — 앞에서부터 값이 있는 것.
    SORT_FALLBACK = ("accel", "flow", "ret")

    @property
    def sortable(self) -> bool:
        """이 화면에 **정렬이 있는가.**

        종합 축은 5·20·60·120 을 G **순위**로 섞은 것이라 페이로드 순서를 그대로
        쓴다 — 줄세울 키가 없다. 그런데 `s`·`r` 은 눌리기는 해서 헤더 라벨과
        힌트바가 따라 움직였고, 표는 그대로였다. 사용자는 라벨이 바뀌는 것을
        보고 정렬이 됐다고 믿는다 — 이 저장소가 리포트 쪽에서 CI 로 막아 온
        부류다(README §7 D 계층: "파라미터를 바꿨는데 계산은 동일").

        판정을 여기 한 곳에 둔다. 키를 받는 쪽(`handle_key`)과 화면에 적는 쪽
        (`header_lines`)이 다른 답을 내면 그 순간 다시 거짓말이 된다.

        드릴다운은 **실제로** 정렬한다(`NAME_SORTS`) — 종합에서 들어가도 그렇다.
        """
        return self.drill or self.window != "종합"

    def effective_sort(self, rows: list[dict]) -> tuple[str, bool]:
        """실제로 줄세우는 데 쓸 키와, 그게 폴백인지.

        5일 창은 G·풀림이 27/27 전부 결측이다. 예전엔 그대로 정렬해서 전 행이
        동률이 되어 페이로드 원순서(가나다)로 남았는데, 헤더는 그 열을
        하이라이트하며 "이걸로 줄세웠다"고 말했다. 정렬이 없는데 있는 척한 것이다.
        """
        key = self.sort_key
        if any(r.get(key) is not None for r in rows):
            return key, False
        for alt in self.SORT_FALLBACK:
            if alt != key and any(r.get(alt) is not None for r in rows):
                return alt, True
        return key, False

    def rows(self) -> list[dict]:
        """화면에 나갈 행 — 선택 상태별로 캐시한다.

        렌더가 열 정의를 순회하면서 셀마다 횡단면 스케일(미실현 막대의 최댓값)을
        묻기 때문에, 캐시가 없으면 한 프레임에 rows() 가 수백 번 돈다.
        """
        # 캐시 키에는 **정렬 결과를 바꾸는 상태가 전부** 들어가야 한다.
        # rev 를 빠뜨렸더니 r 을 눌러도 캐시가 옛 순서를 돌려줬다 — 캐시와
        # 역순이 각각은 옳은데 합쳐서 깨진 자리다.
        ck = (self.wi, self.mi, self.ai, self.si, self.rev)
        if getattr(self, "_rows_ck", None) == ck:
            return self._rows_v
        out = self._rows_uncached()
        self._rows_ck, self._rows_v = ck, out
        return out

    #: 종합 화면이 4주체를 볼 때 쓰는 **대표 창**. 견인주(`_leads`)가 이미 같은
    #: 선택을 하고 있고 드릴다운도 그렇다 — 한 화면 안에서 규칙이 하나여야 한다.
    COMBINED_WIN = "20"

    def _actor_sums(self) -> dict:
        """섹터 → 4주체 순매수 합 [억]. **종합 화면 전용**이다.

        종합 블록(`combined`)에는 주체별 값이 없다 — 5·20·60·120 을 G **순위**로
        섞은 축이라 "그 구간의 순매수" 라는 것이 정의되지 않는다. 그래서 대표 창
        (:attr:`COMBINED_WIN`) 블록의 섹터 합을 그대로 쓴다.

        예전엔 종목 값을 더했다. 종목 값은 0.1억으로 반올림돼 있어 합이 1억 가까이
        어긋났고, 같은 "20일 외국인" 이 20일 화면에서 -60, 종합 화면에서 -61 로
        보였다(2026-11-08 웹·TUI 대조). 같은 숫자는 한 출처에서만 꺼낸다.
        """
        ck = (self.COMBINED_WIN, self.market)
        if getattr(self, "_asum_ck", None) == ck:
            return self._asum_v
        b = self.d["blocks"].get(f"{self.COMBINED_WIN}|{self.market}") or {}
        out = {r.get("sector"): {k: r[k] for k, _ko in ACTORS if r.get(k) is not None}
               for r in b.get("rows", [])}
        self._asum_ck, self._asum_v = ck, out
        return out

    def empty_reason(self) -> str:
        """표가 비었을 때 **왜** 비었는지. 웹 수치표와 같은 말을 쓴다."""
        if self.window == "종합" and not self.d.get("combined", {}).get(self.market):
            return "이 시장은 G 가 나온 구간이 둘 미만이라 종합이 없다 — w 로 다른 구간을 보라"
        return "데이터 없음"

    def _rows_uncached(self) -> list[dict]:
        if self.window == "종합":
            c = self.d.get("combined", {}).get(self.market)
            if not c:
                return []
            leads = self._leads()
            sums = self._actor_sums()
            return [dict(r, top=leads.get(r.get("sector")),
                         **sums.get(r.get("sector"), {})) for r in c["rows"]
                    if not absent_row(r)]
        if self.window == ONE_DAY:
            src = self._today_rows()
        else:
            b = self.d["blocks"].get(f"{self.window}|{self.market}")
            if not b:
                return []
            src = b["rows"]
        rows = [self._project(r) for r in src if not absent_row(r)]
        key, _fell = self.effective_sort(rows)
        def val(r):
            v = r.get(key)
            # 결측은 역순에서도 **맨 뒤**다 — '값이 없는 것'은 작은 값이 아니다.
            # 얇은 섹터(`~`, 10종목 미만)는 결측보다도 뒤다. 분모(시총)가 작아 가속이 쉽게
            # 튀어서, 5·60·120일에 3종목짜리 섹터가 가속 1등을 하고 있었다 — 회색으로
            # 죽인 줄이 표 맨 위를 차지하면 회색이 하는 말을 순서가 뒤집는다.
            return (bool(r.get("thin")), v is None,
                    (v if self.rev else -v) if v is not None else 0)
        rows.sort(key=val)
        return rows

    def block_meta(self) -> str:
        if self.window == "종합":
            c = self.d.get("combined", {}).get(self.market)
            return f"구간 {'·'.join(str(w) for w in c['windows'])}일 등가중" if c else ""
        if self.window == ONE_DAY:
            T = self.d.get("today") or {}
            return (f"{T.get('date', '?')} 하루 · 1년%ile=일별 값의 {T.get('lookback', '?')}일 분포"
                    f" · {ret_label(self.d, short=True)}") if T else ""
        b = self.d["blocks"].get(f"{self.window}|{self.market}")
        if not b:
            return ""
        span = f"{b.get('from', '?')} ~ {b.get('to', '?')}"
        # k·t 는 x 계열의 재료(동시기 횡단면 적합, |t|<2 인 날이 42~70% — 검정 Q3c)라 표지 없이 첫 화면에 두지 않는다.
        # x 계열이 사는 **탐색 모드**에서만 적는다(2026-11-08 3차 리뷰). 웹 수치표의 절편도 같다.
        return span + (f" · k={b.get('k', '?')} (t={b.get('t', '?')})" if self.explore else "")


    def selected(self) -> dict | None:
        rows = self.rows()
        return rows[min(self.row, len(rows) - 1)] if rows else None

    def names(self) -> list[dict]:
        """선택 섹터의 **전 종목** — 시총 대비 순매수(%p) 큰 순.

        페이로드가 프리셋 구간별 집계를 싣는다. 이전엔 (시장,섹터)당 12개만 미리
        뽑아 실어서 "그 12개 안에서의 순위"를 보여줬고, 20일 기준 각 섹터 상위 3 중
        32% 가 화면에 없었다.
        """
        r = self.selected()
        if not r:
            return []
        sec = r.get("sector")
        win = self.window if self.window != "종합" else "20"
        mkts = ([self.market] if self.market != "전체" else self.markets[1:])
        sec_ret = self._sector_ret(r)
        out = []
        for code, nm in (self.d.get("names") or {}).items():
            if nm.get("sector") != sec or nm.get("market") not in mkts:
                continue
            w = (nm.get("win") or {}).get(win)
            if not w:
                continue
            cap = nm.get("cap")
            v, tv = w.get(self.actor), w.get("tv")
            # 옛 리포트에는 아래 세 값이 없다. `.get` 으로 받아 `—` 로 남긴다 —
            # 새 열이 생겼다고 어제 만든 리포트로 화면이 깨지면 안 된다.
            iv, pf, sr = w.get("invtrt"), w.get("penfnd_etc"), w.get("ret")
            out.append({"code": code, "name": nm.get("name", "—"),
                        "flow": v, "tv": tv, "cap": cap, "cap_pending": nm.get("cap_pending"),
                        # 참여율 — 그 종목 거래대금 중 이 주체의 순매수가 차지한 몫.
                        # 거래대금 자체는 "얼마나 붐볐나" 일 뿐이고, 판단에 직결되는
                        # 것은 "그 거래의 몇 %가 한 방향이었나" 다.
                        "part": (v / tv * 100) if (tv and v is not None) else None,
                        "a": ((v or 0) / cap * 100) if cap else None,
                        # ── 자금 성격 ── 기관 총액이 같아도 투신·연금이 채운 것과
                        # 금투(증권사 자기매매 — 헤지·차익이 섞여 방향성이 약하다)가
                        # 채운 것은 다른 이야기인데 `기관` 한 덩어리에는 그게 없다.
                        # 헤더가 제 이름을 달고 있으므로 선택 주체와 섞이지 않는다
                        # (이름 없는 열이 다른 주체의 값을 조용히 이고 있던 사고와
                        # 다른 자리다 — 그건 `가속` 이 늘 기관이던 경우다).
                        "invtrt": iv, "penfnd": pf,
                        # ── 지속성 ──
                        "conc": self._conc(nm, v),
                        # ── 가격 ── 섹터는 갔는데 이건 안 갔나.
                        "ret": sr,
                        "rrel": (sr - sec_ret) if (sr is not None
                                                   and sec_ret is not None) else None})
        # ⚠️ 관문·점수를 **정렬 전에** 매긴다. 뒤에서 매기면 `선정점수` 는 전 행이 결측이라
        # 코드순으로 남고, `최근집중` 은 관문에 지워질 값으로 줄세워 `—` 가 맨 위에 온다.
        self._gate_conc(out)
        self._attach_pick(out)
        key = self.name_sort
        if key == "name":
            out.sort(key=lambda t: t["name"], reverse=self.nrev)
        elif key == "flow":
            # 기본은 **절댓값** 순. 부호순이면 판 종목이 목록 끝으로 밀려, 화면 앞쪽은
            # 산 종목 몇 개 + 0 의 벌판이 된다(종목행의 51%가 +0 이다).
            # 역순은 **부호 오름차순**(가장 많이 판 쪽 먼저)이다. 절댓값을 뒤집으면 위가 +0 벽이라
            # 도움말이 말하는 "순매도 상위" 로 갈 길이 없다.
            # 결측은 어느 방향에서도 맨 뒤 — '값이 없는 것'은 작은 값이 아니다.
            if self.nrev:
                out.sort(key=lambda t: (t["flow"] is None,
                                        t["flow"] if t["flow"] is not None else 0))
            else:
                out.sort(key=lambda t: (t["flow"] is None,
                                        -abs(t["flow"]) if t["flow"] is not None else 0))
        else:
            out.sort(key=lambda t: (t.get(key) is None,
                                    (t.get(key) * (1 if self.nrev else -1))
                                    if t.get(key) is not None else 0))
        self._attach_cum(out)
        return out

    #: 선정점수의 **모집단**은 `-` 마커(누적 :data:`CUM_CUT`)가 붙은 행**까지**다.
    #: 그 아래는 순매수가 사실상 0 이라(실측: 전기/전자 386종목 중 7개가 80% 를
    #: 설명한다) 순위에 넣으면 노이즈가 분위를 밀어낸다 — `sector_numbers` 가 얇은
    #: 섹터를 G 순위에서 빼는 것과 같은 이유다.
    #:
    #: ⚠️ **넘긴 행을 포함한다.** `cum > 80` 으로 자르면 80% 를 처음 넘긴 그 줄이
    #: 빠지는데, 도움말은 그 줄까지를 "이 섹터를 움직인 종목" 이라 부른다. 화면의
    #: 가로줄과 점수의 경계가 어긋나면 사용자는 마커 위 줄에 점수가 없는 것을 본다.
    #: 체결 관문 — 구간 거래대금 하한(억). 참여율이 아무리 높아도 이 아래는
    #: 내 주문이 곧 그 종목의 거래가 된다. 실측(2026-08-28, 20일): 참여율 ≥10% 인
    #: 169종목의 거래대금 중앙값이 70억이고 하위 10%가 5억이다.
    PICK_MIN_TV = 100.0

    def _attach_pick(self, rows: list[dict]) -> None:
        """선정점수 — 네 축의 **횡단면 순위 평균**과, 그 앞에 서는 관문 셋.

        섹터 표의 ``*``(부호 AND) + ``G``(순위 평균) 구조를 종목 층에 옮긴다. 다른 점이
        둘 있다: 최근집중·상대수익·참여율이 다 있어야 후보가 되고(그래서 5일 창에는 점수가
        없다), 후보가 하나뿐이면 점수가 없다(섹터 G 는 그때 0.5).
        관문은 **탈락**을 정하고 점수는 **순서**를 정한다 — 둘을 한 수로 섞으면
        "왜 빠졌는지" 와 "왜 앞인지" 가 같은 숫자에 뭉개진다.

        관문 셋(하나라도 어기면 점수 없음):

        1. ``순매수 > 0`` — 반대편은 후보가 아니다.
        2. ``투신 ≥ 0 and 연기금 ≥ 0`` — 기관 총액은 6개 세부의 **합**이라 금투
           (증권사 자기매매, 헤지·차익이 섞인다)가 만든 숫자일 수 있다. 실측:
           대한항공 20일 기관 +692억인데 투신 −144. 믿는 두 주체가 갈린 줄이다.
        3. ``거래대금 ≥ PICK_MIN_TV`` — 참여율은 비율이라 혼자서는 체결 가능성을
           못 답한다(참여율 11~14% 대에 거래대금 3억과 43,543억이 같이 있다).

        점수 = 네 순위의 평균, 각 순위는 ``pos/(N−1)`` 로 [0,1] 정규화:

        ``rank(투신+연기금)`` · ``rank(최근집중)`` · ``rank(−상대수익)`` · ``rank(참여율)``

        **순위를 쓰는 이유**는 G 와 같다 — 억원·%·%p 가 섞여 있어 그대로 더하면
        단위가 큰 축이 전부를 지배한다. 순위는 스케일에 불변이다.

        **상대수익만 부호를 뒤집는다.** 나머지 셋은 클수록 좋지만 상대수익은
        **작을수록** 좋다 — 섹터만큼 안 갔다는 뜻이라 자리가 남았다는 읽기다.

        ⚠️ **탐색 점수다** — 탐색 모드(`e`)에서만 보인다. 점수 자체는 앞날 검정을 안
        했고, ``rank(투신+연기금)`` 축은 검정 결과(docs/validation/2026-11-08-usefulness-
        results.md K1: 섹터 안 순위 IC 음, 확인)와 **부호가 반대**다. 지우지 않고 숨긴다.
        """
        for t in rows:
            t["pick"] = None
        # ⚠️ 모집단을 **화면 순서에서 읽지 않는다.** `cum`·`cut` 은 순매수 정렬에서만
        # 채워지므로(다른 정렬의 누적은 뜻이 없다), 그걸 쓰면 `선정점수` 로 정렬하는
        # 순간 모집단이 사라져 전 행이 `—` 가 된다 — 자기 정렬로 자기를 지운다.
        # 점수는 그 종목이 섹터 안에서 갖는 성질이지 지금 무엇으로 줄세웠나가
        # 아니므로, 여기서 |순매수| 순서를 **다시 만들어** 경계를 정한다.
        ranked = sorted((t for t in rows if t.get("flow") is not None),
                        key=lambda t: -abs(t["flow"]))
        tot = sum(abs(t["flow"]) for t in ranked)
        cand = []
        run = 0.0
        for t in ranked:
            if not tot or run >= CUM_CUT:
                break
            run += abs(t["flow"]) / tot * 100.0
            f, iv, pf = t.get("flow"), t.get("invtrt"), t.get("penfnd")
            if f is None or f <= 0 or iv is None or pf is None:
                continue
            if iv < 0 or pf < 0:
                continue
            if (t.get("tv") or 0) < self.PICK_MIN_TV:
                continue
            if t.get("conc") is None or t.get("rrel") is None or t.get("part") is None:
                continue
            cand.append(t)
        if len(cand) < 2:
            return
        def _rank(vals: list[float]) -> list[float]:
            """[0,1] 정규화 순위. **동률은 중간순위** — `1년[%ile]` 과 같은 규약.

            동률을 안 다루면 순서가 입력 순서에 따라 임의로 갈린다. 세 축이
            같은 두 종목이 그 축들에서 0 과 1 을 나눠 갖고, 정작 다른 네 번째
            축의 차이가 묻힌다(실측으로 밟았다).
            """
            order = sorted(range(len(vals)), key=lambda i: vals[i])
            out = [0.0] * len(vals)
            i = 0
            while i < len(order):
                j = i
                while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                    j += 1
                mid = (i + j) / 2.0 / (len(vals) - 1)
                for k in range(i, j + 1):
                    out[order[k]] = mid
                i = j + 1
            return out
        ra = _rank([t["invtrt"] + t["penfnd"] for t in cand])
        rc = _rank([t["conc"] for t in cand])
        rr = _rank([-t["rrel"] for t in cand])
        rp = _rank([t["part"] for t in cand])
        for t, a, c, r, p in zip(cand, ra, rc, rr, rp):
            t["pick"] = (a + c + r + p) / 4.0

    def combined_windows(self) -> list[str]:
        """종합축이 실제로 섞은 창 — 상수가 아니라 **데이터가 정한다.**

        `sector_numbers` 가 "G 가 하나라도 나오는 창" 만 고른다. 5일은 ẍ 가 2차
        차분이라 최소 9거래일을 요구해 점수가 안 나오므로 저절로 빠진다. 오늘
        3개인 것은 규칙이 아니라 관측 결과라, 화면이 세어서 말한다.
        """
        c = (self.d.get("combined") or {}).get(self.market) or {}
        return [str(w) for w in (c.get("windows") or [])]

    def trusted_recent(self) -> dict:
        """섹터 → **최근 5일 투신+연금 순매수[억]**. 섹터 row 에 없어서 종목에서 더한다.

        표의 `임펄스` 는 현재 창(보통 20일) 누적이라 "아직도 들어오는가" 를 말하지
        않는다. 곱순위가 G(섹터)×선정(종목) 이므로 섹터 다리가 살아 있는지는 종목
        고르기에 그대로 걸린다 — 그래서 섹터 표에 둔다.

        **5일은 최단창이라 차분이 필요 없다**(`TREND_SEGS` 의 마지막 칸이 곧 5일
        창 자체다). 여기서 좋다/나쁘다 부호는 안 붙인다 — 최근 유출이 눌림목인지
        추세전환인지는 가격 구조를 봐야 갈리는데 이 리포트에는 구간 수익률 넷뿐이다.

        ⚠️ **캐시한다.** 27개 섹터를 각각 훑으면 2,600여 종목을 27번 보게 된다.
        `names` 를 **한 번만** 지나며 전 섹터를 동시에 채운다. 캐시 키에는 값을
        바꾸는 상태가 전부 들어가야 한다 — `all_picks` 가 `rev` 를 빠뜨려 한 번
        밟은 자리다. 여기서는 시장(`mi`) 뿐이다(창·주체·정렬은 이 값을 안 바꾼다).
        """
        if getattr(self, "_trust_ck", None) == self.mi:
            return self._trust_v
        # 웹 수치표와 **같은 합**(:func:`_sector_trusted`) — 값이 하나도 없는 섹터는 0 이 아니라 결측이다.
        out = _sector_trusted(self.d, [self.market] if self.market != "전체" else self.markets[1:], CONC_WIN)
        self._trust_ck, self._trust_v = self.mi, out
        return out

    def trusted_window(self) -> dict:
        """섹터 → **이 창**의 투신+연기금 순매수[억](종목 값의 합). 1일 창이면 그날 값이다.

        5일 열(:meth:`trusted_recent`)과 나란히 둔다 — "지금도 들어오나" 와 "이 창 전체로는" 을 같이 본다.
        종합 화면은 대표 창(:attr:`COMBINED_WIN`)이다.
        """
        win = self.window if self.window != "종합" else self.COMBINED_WIN
        ck = (self.mi, win)
        if getattr(self, "_trustw_ck", None) == ck:
            return self._trustw_v
        mkts = [self.market] if self.market != "전체" else self.markets[1:]
        out = _sector_trusted(self.d, mkts, win)
        self._trustw_ck, self._trustw_v = ck, out
        return out

    def all_picks(self) -> list[dict]:
        """**전 섹터의 종목을 한 화면에** — `섹터G × 종목선정` 내림차순.

        섹터 표와 드릴다운은 "이 섹터 안에서 어느 종목" 만 답한다. 그런데 실제
        질문은 "오늘 국내 전체에서 어디" 다. 두 점수를 **곱**해서 그 답을 낸다.

        **왜 곱인가 — 여기서만 평균을 안 쓴다.** 각 점수 안에서는 축 하나가 낮아도
        나머지가 메우는 것이 맞다(그래서 평균이다). 하지만 층 사이에서는 그러면
        안 된다 — 섹터가 0.2 인데 종목이 0.9 라고 평균 0.55 로 올려 주면, 아무도
        안 사는 섹터의 1등이 모두가 사는 섹터의 3등을 이긴다. **곱은 둘 다 높을
        때만 살아남는다**(0.2 × 0.9 = 0.18 < 0.6 × 0.7 = 0.42). 사용자의 질문이
        "섹터도 높고 종목도 높은 것" 이므로 AND 를 뜻하는 연산이어야 한다.

        관문은 각 층이 이미 걸었다 — 어느 한쪽이라도 점수가 없으면 곱도 없다.
        그래서 이 목록에 뜨는 것은 **양쪽 관문을 다 통과한 종목**뿐이다.

        ⚠️ **캐시한다.** 이 목록은 27개 섹터를 돌며 섹터마다 `names()` 를 다시
        만든다(2,600여 종목 전수). 캐시가 없으면 이 화면은 **키를 누를 때마다,
        아니 프레임마다** 그걸 처음부터 다시 했다 — `rows()` 가 같은 이유로 이미
        캐시를 갖고 있다. 캐시 키에는 목록을 바꾸는 상태가 **전부** 들어가야
        한다(`rows()` 가 rev 를 빠뜨려 한 번 밟은 자리다).
        """
        ck = (self.wi, self.mi, self.ai, self.si, self.rev)
        if getattr(self, "_picks_ck", None) == ck:
            return self._picks_v
        out: list[dict] = []
        keep_row, keep_drill = self.row, self.drill
        try:
            for i, sec in enumerate(self.rows()):
                g = sec.get("G")
                if g is None:
                    continue
                self.row = i
                for t in self.names():
                    p = t.get("pick")
                    if p is None:
                        continue
                    t = dict(t)
                    t["sector"] = sec.get("sector")
                    t["gsec"] = g
                    t["both"] = g * p
                    out.append(t)
        finally:
            self.row, self.drill = keep_row, keep_drill
        out.sort(key=lambda t: -t["both"])
        self._picks_ck, self._picks_v = ck, out
        return out

    def _gate_conc(self, rows: list[dict]) -> None:
        """목록 안에서 몫이 :data:`CONC_MIN_SHARE` 미만인 행의 최근집중을 비운다.

        분모는 **화면에 지금 뜬 목록**의 Σ|순매수| 다 — `_attach_cum` 의 누적
        기여율이 쓰는 것과 같은 총량이라, "누적 80% 안" 과 "최근집중이 보임" 이
        같은 크기 축 위에서 정해진다. 시장이 `전체` 면 두 시장이 섞인 총량인데,
        그게 맞다: 질문은 "이 목록 안에서 이 줄이 볼 만한가" 다.
        """
        tot = sum(abs(t["flow"]) for t in rows if t.get("flow") is not None)
        for t in rows:
            f = t.get("flow")
            if t.get("conc") is None:
                continue
            if not tot or f is None or abs(f) / tot * 100.0 < CONC_MIN_SHARE:
                t["conc"] = None

    @property
    def legacy_names(self) -> bool:
        """이 리포트가 **투신·연기금·종목수익률이 생기기 전** 형식인가.

        옛 페이로드로 띄우면 새 열 넷이 통째로 `—` 다. 하위호환 경로가 제대로
        작동한 결과라 화면은 안 깨지는데, 보는 사람에게는 **빈 열이 넷 늘어난
        것**으로만 보인다 — "고장났나" 로 읽힌다. 그래서 화면이 스스로 말한다.

        키가 **아예 없는 것**과 있는데 0 인 것을 가른다. 전자는 producer 가 옛
        형식이라는 뜻이고 후자는 그냥 그 종목이 조용했다는 뜻이다. 값이 0 인지로
        판정하면 정말로 전부 0 인 조용한 날에 "옛 리포트" 라고 거짓말을 한다.
        """
        if getattr(self, "_legacy_v", None) is None:
            self._legacy_v = not any(
                "invtrt" in w
                for nm in (self.d.get("names") or {}).values()
                for w in (nm.get("win") or {}).values())
        return self._legacy_v

    def _sector_ret(self, r: dict) -> "float | None":
        """상대수익의 기준선 — 섹터 표의 `수익률[%]` 열과 **같은 값**.

        같은 값이어야 하는 이유: 사용자가 표에서 본 "이 섹터는 +8% 갔다" 와
        드릴다운의 뺄셈이 다른 수를 쓰면, 두 화면이 같은 이름으로 다른 것을
        말하게 된다. 그래서 새로 계산하지 않고 그 행에서 꺼낸다.

        종합 축은 창을 섞은 것이라 행에 `ret` 이 없다. 목록이 20일을 보므로
        (:attr:`COMBINED_WIN`) 기준선도 20일 블록에서 꺼낸다 — 다른 창끼리
        빼면 뺄셈이 뜻을 잃는다.
        """
        if self.window != "종합":
            return r.get("ret")
        b = (self.d.get("blocks") or {}).get(f"{self.COMBINED_WIN}|{self.market}")
        for row in (b or {}).get("rows") or []:
            if row.get("sector") == r.get("sector"):
                return row.get("ret")
        return None

    def _conc(self, nm: dict, den) -> "float | None":
        """최근집중 — 5일 순매수 ÷ **이 창**의 순매수 × 100 [%].

        ≈100/창×5 면 고르게 분산(20일 화면에서 25% 근처), ≈100 이면 이 구간
        순매수가 사실상 최근 5일에 몰린 것, >100 이면 앞에서는 팔다가 최근에
        방향을 튼 것이다. 섹터 표의 `추이[8]` 스파크라인이 하는 일을 숫자
        하나로 대신한다.

        **뜻을 잃는 구간을 비운다.** (i) 가장 짧은 창(5일)에서는 분자와 분모가
        같아 늘 100% 라 아무 말도 안 한다. (ii) 분모가 :data:`CONC_MIN_DEN`
        미만이면 비율이 폭발한다. 부호가 갈리는 경우(5일 +100, 20일 −50)는
        **음수로 남긴다** — 그건 사고가 아니라 "최근 5일이 구간 전체와 반대로
        움직였다" 는 참말이고, 이 화면이 지속성을 묻는 이상 가장 알고 싶은
        경우 중 하나다. 크기만 :data:`CONC_CAP` 에서 잘라 적는다.
        """
        win = self.window if self.window != "종합" else self.COMBINED_WIN
        # 1일 창의 분모는 하루치라 "5일 ÷ 1일" 은 지속성이 아니라 거꾸로 선 비율이다 — 비운다.
        if win in (CONC_WIN, ONE_DAY) or den is None or abs(den) < CONC_MIN_DEN:
            return None
        num = ((nm.get("win") or {}).get(CONC_WIN) or {}).get(self.actor)
        return None if num is None else num / den * 100.0

    def _attach_cum(self, rows: list[dict]) -> None:
        """누적 기여율 — |순매수| 의 화면 순서 누적 몫(%).

        **순매수 절댓값 정렬에서만** 채운다. 기여도는 |금액| 으로만 정의되는데, 시총·종목명·
        부호(순매수 역순) 순서로 누적하면 아무 뜻 없는 톱니가 되고, 그런데도 숫자가 단조증가라서
        읽는 사람은 뜻이 있다고 믿는다. 뜻이 없는 칸은 비워 두는 편이 정직하다.
        """
        tot = sum(abs(t["flow"]) for t in rows if t.get("flow") is not None)
        run, cut_done = 0.0, False
        for t in rows:
            if self.name_sort != "flow" or self.nrev or not tot:
                t["cum"] = None
                t["cut"] = False
                continue
            run += abs(t.get("flow") or 0.0)
            t["cum"] = run / tot * 100
            # 가로줄은 80% 를 **처음 넘긴 행** 하나에만 긋는다.
            t["cut"] = not cut_done and t["cum"] >= CUM_CUT
            cut_done = cut_done or t["cut"]


def _first_fitting(width: int, cands: list[str]) -> str:
    """폭에 들어가는 **첫 후보**. 다 넘치면 마지막(가장 짧은 것)을 준다.

    폭 임계값을 상수로 박는 대신 재 본다 — 이 파일은 같은 임계값(>=100·>=132·
    >=150)을 두 군데 적었다가 분기 모양까지 갈라진 적이 있다(`Col` 주석).
    """
    for c in cands:
        if cell_len(c) <= width:
            return c
    return cands[-1]


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
#: 오늘 표의 주체 이름 — 페이로드 `today.actors` 와 같다(sector_flow.TODAY_ACTORS).
TODAY_KO = (("inst", "기관"), ("invtrt", "투신"), ("penfnd_etc", "연기금"), ("invpen", "투신+연기금"),
            ("forgn", "외국인"), ("indiv", "개인"), ("etc", "기타법인"))

#: 시작 배너가 쓰는 창. 화면 기본 창(20일)과 같고, 반전 검사는 그 안의 최근 5일과 직전 15일을 견준다.
BANNER_WIN = "20"
#: 1년 백분위가 "극단" 인 문턱 — 위·아래 5%.
PCT_HI, PCT_LO = 95.0, 5.0


def _block_rows(d: dict, win: str, market: str) -> list[dict]:
    """그 (창, 시장) 블록의 섹터 행 — 종목이 하나도 없는 섹터(:func:`absent_row`)는 뺀다."""
    b = (d.get("blocks") or {}).get(f"{win}|{market}") or {}
    return [r for r in b.get("rows") or [] if not absent_row(r)]


def _sector_trusted(d: dict, markets, win: str) -> dict:
    """섹터 → 그 창의 **투신+연기금** 순매수 합[억]. 종목 값을 한 번만 훑어 더한다.

    섹터 행에는 기관 세부가 없다(:func:`sector_actor_win` 과 같은 이유). 한 번에 27개를 채운다.
    """
    mkts = set(markets or ())
    out: dict = {}
    for nm in (d.get("names") or {}).values():
        if nm.get("market") not in mkts:
            continue
        w = (nm.get("win") or {}).get(win) or {}
        vals = [w.get(k) for k, _ in TREND_ACTORS]
        if all(v is None for v in vals):
            continue
        sec = nm.get("sector")
        out[sec] = out.get(sec, 0.0) + sum(v for v in vals if v is not None)
    # 합의 이진 잔차를 지운다(백만분의 1억 아래) — 종목 값(0.1억 단위)이 상쇄돼 0 이어야 할 합이 더하는 순서에 따라
    # +2.7e-17 이나 −2.7e-17 이 되고, 부호를 원값에서 읽는 표시가 '+0'·'−0' 으로 갈렸다. 웹은 JS 객체 키 순서
    # (0 으로 시작하지 않는 숫자 꼴 코드가 먼저)로 더해 TUI 와 순서가 다르다(2026-09-01 종이/목재 재구성). 웹 `trusted` 와 같은 연산.
    return {s: rhu(v, 6) + 0.0 for s, v in out.items()}


def _sec_label(r: dict) -> str:
    """배너 목록의 섹터 이름 — 얇은 섹터는 표와 같은 `~` 를 단다(10종목 미만은 섹터로 읽지 않는다)."""
    return f"{r.get('sector', '—')}{'~' if r.get('thin') else ''}"


def _items_line(prefix: str, items: list[str], width: int, empty: str = "없음") -> str:
    """``prefix`` 뒤에 항목을 ``·`` 로 잇되 **항목 경계에서** 자른다 — 남은 수는 `외 N` 으로 적는다.

    줄을 그냥 자르면 `+3,79` 처럼 숫자가 자릿수 중간에서 끊겨 다른 값이 된다(표가 열을
    경계에서 떨어뜨리는 것과 같은 규칙).
    """
    if not items:
        return pad(prefix + empty, width)
    for n in range(len(items), 0, -1):
        tail = f" 외 {len(items) - n}" if n < len(items) else ""
        line = prefix + " · ".join(items[:n]) + tail
        if cell_len(line) <= width:
            return pad(line, width)
    return pad(prefix + f"{len(items)}개", width)


def reversal_flags(st: State) -> list[tuple[str, float, float]]:
    """최근 5일 순매수 부호가 **직전 15일**과 반대인 섹터 — (섹터, 직전 15일 값, 5일 값).

    직전 15일 = 20일 누적 − 5일 누적(:data:`TREND_SEGS` 의 `20-5` 와 같은 차분). 전 섹터를
    본다 — 예전엔 G 통과 섹터만 봤는데, G 는 검정 결과 판정 불가라 사실 요약의 필터가 될 수 없다.
    양쪽 방향(매수→매도·매도→매수)을 다 적는다. 둘 중 하나가 0 이면 부호가 없어 안 적는다.

    ⚠ 이것은 **판정 불가 라벨**이다 — 최근 반대 방향이 눌림목인지 추세 전환인지는 가격 경로를
    봐야 갈리는데 리포트에는 구간 수익률뿐이다. 사실(부호가 바뀌었다)만 말한다.
    주체는 화면의 선택 주체, 시장은 선택 시장이다. 창은 화면 창과 무관하게 20일·5일이다.
    """
    mk = st.market
    five = {r["sector"]: r for r in _block_rows(st.d, "5", mk)}
    cand = []
    for r in _block_rows(st.d, BANNER_WIN, mk):
        s = five.get(r.get("sector"))
        cur, f5 = r.get(st.actor), (s or {}).get(st.actor)
        if cur is None or f5 is None:
            continue
        cand.append((r, cur - f5, f5))
    # 크기 문턱 — 최근 5일 |금액| 이 목록 Σ|5일| 의 FLIP_MIN_SHARE 미만이면 부호가 잡음이다(리뷰: +2억이 −3억으로
    # 바뀐 섹터가 줄을 채웠다). 문턱은 목록 안 몫으로 잡는다(CLAUDE.md 화면·리포트 규칙).
    tot = sum(abs(f5) for _r, _p, f5 in cand)
    out = [(_sec_label(r), prior, f5) for r, prior, f5 in cand
           if prior * f5 < 0 and abs(f5) >= FLIP_MIN_SHARE * tot]
    out.sort(key=lambda t: -abs(t[2]))
    return out


#: 부호 반전 목록의 크기 문턱 — 최근 5일 |순매수| 가 그 목록 Σ|5일| 의 이 몫 이상인 섹터만.
FLIP_MIN_SHARE = 0.02


#: 오늘 표에서 배너가 상위·하위·극단을 적는 두 주체 — 기관 총액과, 기관 안에서 방향성이 있는 투신+연기금
#: (CLAUDE.md 수급 신뢰 순서). 키는 페이로드 `today.sectors[시장][섹터]` 의 키다.
BANNER_ACTORS = (("inst", "기관"), ("invpen", "투신+연기금"))
#: 시장 맥락 줄의 수익률 구간(거래일) — 페이로드 `today.market[시장].ret` 의 키.
CTX_WINDOWS = ("1", "20")


def _pct_tag(p) -> str:
    """백분위 꼬리표 `(97)` — 없으면 `(—)`. 분모 게이트(0 이 많은 줄)에 걸린 값은 결측이다."""
    return "(—)" if p is None else f"({pct_int(p)})"


def _fmt_ret(v) -> str:
    return "—" if v is None else fmt_pct(v) + "%"


def _ctx_lines(d: dict, markets: list[str], width: int) -> list[str]:
    """시장 맥락 — 시장별 자체 바구니 수익률(애프터마켓 포함 통합 종가)과 KRX 지수(정규장) 수익률.

    둘은 **세션이 다르다.** 2026-09-14 부터 리포트의 일별 종가는 20:00 애프터마켓까지 포함한 통합값이라
    수익률은 "전일 20:00 → 당일 20:00" 이다. KRX 지수는 정규장(15:30) 값이다. 같은 날 둘이 2%p 넘게 갈릴
    수 있어(11-06 거래소 −4.37% vs 코스피 −6.02%) 나란히 적고 라벨로 가른다. 첫 줄이 라벨, 시장마다 한 줄.
    """
    T = d.get("today") or {}
    idx = T.get("krx_index") or {}
    out = [pad(_first_fitting(width, [
        f" 수익률 {'·'.join(CTX_WINDOWS)}일 — 자체 바구니 {ret_label(d)} | KRX 지수(정규장 15:30)",
        f" 수익률 {'·'.join(CTX_WINDOWS)}일 — 자체 바구니({ret_label(d, True)}) | KRX 지수(정규장)",
        f" 수익률 {'·'.join(CTX_WINDOWS)}일 — 바구니({ret_label(d, True)}) | KRX(정규장)"]), width)]
    for m in markets:
        own = (((T.get("market") or {}).get(m) or {}).get("ret")) or {}
        k = idx.get(m) or {}
        kr = k.get("ret") or {}
        mine = " · ".join(_fmt_ret(own.get(w)) for w in CTX_WINDOWS)
        krx = " · ".join(_fmt_ret(kr.get(w)) for w in CTX_WINDOWS)
        out.append(pad(_first_fitting(width, [
            f"   {m} {mine}  |  KRX {k.get('name') or '지수 없음'} {krx}",
            f"   {m} {mine} | {krx}"]), width))
    return out


def _gap_chips(d: dict, markets: list[str]) -> list[str]:
    """금액 근사 오차가 문턱을 넘은 시장 — `금액 근사 오차 큼(코스닥 6.1%)`. 없으면 빈 목록.

    시장 합계는 종목 간 오차가 상쇄된다. 섹터 칸(``sector_max``)이 문턱을 넘으면 그것도 적는다 —
    `금액 근사 오차: 거래소 섹터 최대 금융 11.2%`.
    """
    T = d.get("today") or {}
    warn = T.get("approx_gap_warn", 5.0)
    out = []
    for m in markets:
        g = ((T.get("market") or {}).get(m) or {}).get("approx_gap")
        if not g or g.get("pct") is None:
            continue
        if g["pct"] > warn:
            out.append(f"금액 근사 오차 큼({m} {rhu(g['pct'], 1):.1f}%)")
        sm = g.get("sector_max")
        if sm and sm.get("pct") is not None and sm["pct"] > warn and m != "전체":
            out.append(f"금액 근사 오차: {m} 섹터 최대 {sm['sector']} {rhu(sm['pct'], 1):.1f}%")
    return out


def unclassified_count(d: dict, markets: list[str]) -> int:
    """그 시장(들)에서 섹터가 빈 종목 수(``(미분류)``) — 마스터 결함이 화면에서 섹터처럼 읽히지 않게."""
    nb = d.get("n_by_sector") or {}
    return sum(int((nb.get(m) or {}).get("(미분류)", 0) or 0) for m in markets)


def _today_rank(d: dict, market: str, actor: str) -> list[tuple[str, float, "float | None"]]:
    """오늘 (섹터, 순매수, 1년%ile) — 금액 큰 순. 값이 없는 섹터는 뺀다."""
    secs = (((d.get("today") or {}).get("sectors") or {}).get(market)) or {}
    out = [(s, (v.get(actor) or {}).get("v"), (v.get(actor) or {}).get("pct"))
           for s, v in secs.items()]
    out = [t for t in out if t[1] is not None]
    out.sort(key=lambda t: (-t[1], t[0]))
    return out


def today_extremes(d: dict, market: str, actor: str) -> tuple[list[str], list[str]]:
    """오늘 순매수의 1년 백분위가 ≥95 / ≤5 인 섹터 — (위, 아래), 각각 백분위가 극단인 순.

    "오늘" 하루 값의 백분위다(페이로드 `today`, 오늘 포함 1년 일별 분포). 직전 리포트와 견줄 필요가
    없다 — 어제도 극단이었는지가 아니라 **오늘 값이** 1년 중 어디인지를 말한다.
    """
    hi, lo = [], []
    for s, _v, p in _today_rank(d, market, actor):
        if p is None:
            continue
        if p >= PCT_HI:
            hi.append((s, p))
        elif p <= PCT_LO:
            lo.append((s, p))
    hi.sort(key=lambda t: (-t[1], t[0]))
    lo.sort(key=lambda t: (t[1], t[0]))
    return [f"{s} {pct_int(p)}" for s, p in hi], [f"{s} {pct_int(p)}" for s, p in lo]


def change_items(d: dict, market: str, actor: str) -> "list[str] | None":
    """직전 리포트 대비 20일 순매수 상위 N 의 들고 남 — `들어옴 X`·`나감 Y`. 비교를 못 했으면 None."""
    C = d.get("change")
    if not C:
        return None
    t = ((C.get("top") or {}).get(market) or {}).get(actor) or {}
    if t.get("entered") is None or t.get("exited") is None:
        return None
    return [f"들어옴 {s}" for s in t["entered"]] + [f"나감 {s}" for s in t["exited"]]


def final_diff_line(d: dict) -> str | None:
    """잠정 → 확정 변경 요약 한 줄 — 확정 리포트에서만. 목록은 도움말 끝(:func:`final_diff_help`)."""
    if not d.get("final"):
        return None
    fd = d.get("final_diff")
    if fd is None:
        why = d.get("final_diff_reason") or "이유 없음"
        return f" 잠정→확정 비교 없음: {why}"
    return (f" 잠정→확정 변경: {fd.get('n_changed', 0)}칸 / {fd.get('n_cells', 0)}칸"
            f" (±{fd.get('tol', 0)}억 초과, 1·5일 순매수) — 목록은 ? 도움말 끝")


def final_diff_help(d: dict) -> list[tuple[str, str]]:
    """도움말 끝에 붙는 **이 리포트**의 잠정 → 확정 변경 목록(페이로드 `final_diff`)."""
    if not d.get("final"):
        return []
    fd = d.get("final_diff")
    out = [("", ""), ("", "── 이 리포트: 잠정 → 확정 변경 ──")]
    if fd is None:
        return out + [("", f"비교 없음: {d.get('final_diff_reason') or '이유 없음'}")]
    ko = dict(TODAY_KO)

    def f(v):
        return "—" if v is None else f"{v:+,.2f}"
    base = "확정" if fd.get("base_final") else "잠정"
    out.append(("", f"바꿔 끼운 {base} 리포트({fd.get('base_built', '?')} KST) 대비"))
    out.append(("", f"순매수[억] 1·5일 · 반올림 ±{fd.get('tol')}억 초과만"))
    moved = [t for t in fd.get("market_totals") or []
             if t.get("diff") is None or abs(t["diff"]) > (fd.get("tol") or 0)]
    out.append(("", f"시장 합계 {len(fd.get('market_totals') or [])}칸 중 {len(moved)}칸 바뀜"))
    for t in moved:
        out.append(("", f"  {t['market']} {ko.get(t['actor'], t['actor'])} {t['win']}일 "
                        f"{f(t.get('old'))} → {f(t.get('new'))} ({f(t.get('diff'))})"))
    out.append(("", f"섹터 칸 {fd.get('n_cells', 0)}칸 중 {fd.get('n_changed', 0)}칸 바뀜 — 큰 순"))
    for r in fd.get("changed") or []:
        out.append(("", f"  {r['market']}/{r['sector']} {ko.get(r['actor'], r['actor'])} {r['win']}일 "
                        f"{f(r.get('old'))} → {f(r.get('new'))} ({f(r.get('diff'))})"))
    return out


#: 공식 대조 절의 주체 순서·이름 — 페이로드 ``today.krx_truth.markets.*`` 의 키.
KRX_TRUTH_KO = (("inst", "기관"), ("forgn", "외국인"), ("indiv", "개인"), ("etc", "기타법인"))


def krx_truth_help(d: dict) -> list[tuple[str, str]]:
    """도움말 끝 — KRX 공식 거래대금 대비 근사 오차(페이로드 ``today.krx_truth``, 선택 실행).

    % 가 없는 칸(``—``)은 분모 게이트다 — |공식 순매수| 가 그 시장 거래대금의 문턱 몫 미만이면
    상대 오차가 잡음이라 내지 않는다(절대차는 페이로드에 있다).
    """
    kt = (d.get("today") or {}).get("krx_truth")
    if not kt:
        return []                                # 기능을 안 켠 리포트 — 절도 없다
    out = [("", ""), ("", "── 이 리포트: KRX 공식 거래대금 대조(내부 검증용) ──")]
    if kt.get("status") != "ok":
        return out + [("", f"건너뜀: {kt.get('reason') or '이유 없음'}")]

    def g(row) -> str:
        v = (row or {}).get("gap_pct")
        return "—" if v is None else f"{rhu(v, 1):.1f}%"
    for m, acts in (kt.get("markets") or {}).items():
        gaps = " · ".join(f"{ko} {g(acts.get(a))}" for a, ko in KRX_TRUTH_KO)
        out.append(("", f"{m} — 공식 거래대금 대비 근사 오차: {gaps} (KRX 공표치, 내부 검증용)"))
    return out


def help_entries(st: "State") -> list[tuple[str, str]]:
    """도움말 = 고정 :data:`HELP` + 이 리포트에만 있는 것(잠정 → 확정 변경 목록·KRX 공식 대조)."""
    return HELP + final_diff_help(st.d) + krx_truth_help(st.d)


#: 그날 시장 순매수 |금액| 이 가장 큰 주체를 찾는 후보 — 4주체(합 ≈ 0).
DOMINANT_FROM = ACTORS
#: 배너가 섹터 옆에 다는 종목 수(그 주체의 그날 상위). 폭이 모자라면 1 → 0 으로 줄인다.
BANNER_STOCKS = 2


def banner_actors(d: dict, market: str) -> list[tuple[str, str]]:
    """첫 화면이 상위·하위·극단을 적는 주체 — 기관·투신+연기금, 그리고 **그날의 최대 순매매 주체**.

    최대 순매매 주체 = 그 시장 오늘 순매수 |금액| 이 가장 큰 4주체. 기관·투신+연기금에 없으면 덧붙인다 —
    11-06 외국인 −28,336억(17%ile)이 첫 화면에 없었다(2026-11-08 리뷰).
    """
    out = list(BANNER_ACTORS)
    net = ((((d.get("today") or {}).get("market") or {}).get(market) or {}).get("net")) or {}
    live = [(abs((net.get(k) or {}).get("v") or 0.0), k, ko) for k, ko in DOMINANT_FROM
            if (net.get(k) or {}).get("v") is not None]
    if live:
        _v, k, ko = max(live)
        if k not in {a for a, _ in out}:
            out.append((k, f"{ko}(최대 순매매 주체)"))
    return out


def day_stocks(d: dict, markets, actor: str) -> dict:
    """섹터 → [(종목명, 그날 순매수[억])] 금액 큰 순 — ``names[code].win["1"]``. 투신+연기금은 둘의 합."""
    mk = set(markets)
    out: dict = {}
    for nm in (d.get("names") or {}).values():
        if nm.get("market") not in mk:
            continue
        w = (nm.get("win") or {}).get("1")
        if not w:
            continue
        if actor == "invpen":
            vals = [w.get("invtrt"), w.get("penfnd_etc")]
            if all(v is None for v in vals):
                continue
            v = sum(x for x in vals if x is not None)
        else:
            v = w.get(actor)
            if v is None:
                continue
        out.setdefault(nm.get("sector"), []).append((nm.get("name", "—"), v))
    for arr in out.values():
        arr.sort(key=lambda t: (-t[1], t[0]))
    return out


def _stock_tag(stocks: list, side: str, k: int) -> str:
    """`[기업은행 +300·KB금융 +200]` — 사는 줄은 순매수 상위, 파는 줄은 순매도 상위(부호가 맞는 것만)."""
    if k <= 0 or not stocks:
        return ""
    pick = [t for t in stocks if t[1] > 0][:k] if side == "buy" else \
        [t for t in stocks[::-1] if t[1] < 0][:k]
    return "[" + "·".join(f"{clip(n, 10)} {fmt_amt(v)}" for n, v in pick) + "]" if pick else ""


def sector_day_ret(d: dict, market: str) -> dict:
    """섹터 → 그날 수익률[%] (페이로드 ``today.sectors[시장][섹터].ret`` — 1일 창 수익률 열과 같은 값)."""
    secs = (((d.get("today") or {}).get("sectors") or {}).get(market)) or {}
    return {s: v.get("ret") for s, v in secs.items()}


def _ranked_line(prefix: str, ranked: list, stocks: dict, side: str, width: int, rets: "dict | None" = None) -> str:
    """섹터 3개(금액·%ile·그날 수익률) + 그 주체의 종목 — 폭이 모자라면 종목 수를 2 → 1 → 0 으로 줄인다.

    수익률은 섹터 이름 옆 금액이 "얼마나 움직인 날"의 것인지 같이 읽으라고 단다(리뷰 3차 D3-m1) —
    웹 '오늘' 패널과 같은 반올림(:func:`fmt_pct`, 소수 2자리, 0.5 는 0 에서 먼 쪽)이다.
    """
    rets = rets or {}
    for k in range(BANNER_STOCKS, -1, -1):
        items = [f"{s} {fmt_amt(v)}{_pct_tag(p)} {_fmt_ret(rets.get(s))}{_stock_tag(stocks.get(s) or [], side, k)}"
                 for s, v, p in ranked]
        line = prefix + " · ".join(items)
        if cell_len(line) <= width or k == 0:
            return _items_line(prefix, items, width)
    return pad(prefix, width)


def revision_tiers(d: dict) -> "list[str] | None":
    """잠정 리포트에서 — 최근 N개 확정일의 잠정→확정 수정 폭(페이로드 ``revision_stats``). 확정이면 None.

    긴 것부터 짧은 것 순의 후보 — 좁은 화면에서 칸 수부터 줄이고, 시드 출처(`revision_stats.seed`)는 끝까지 남긴다.
    마감 값(확정, 또는 창 밖 재구성 ``finalize_unverified``)이면 None — 웹 ``CLOSED`` 와 같은 기준이다.
    """
    if d.get("finalize_unverified") or d.get("final"):
        return None
    R = d.get("revision_stats")
    if not isinstance(R, dict):
        return None
    if not R.get("n_dates") or R.get("rel_median_pct") is None:
        return [" 잠정→확정 수정 폭: 아직 확정 기록이 없다 — 확정 리포트가 쌓이면 여기 적는다"]
    keep = R.get("top3_keep_pct")
    head = (f" 최근 {R['n_dates']}일 잠정→확정: 섹터 칸 중앙 {rhu(R['rel_median_pct']):.0f}%,"
            f" 부호 뒤집힘 {R.get('flips', 0)}회" + (f", 상위3 유지 {rhu(keep):.0f}%" if keep is not None else ""))
    # 종목(주도주) 층 — 기록이 그 요약을 남긴 날이 있을 때만. 좁은 화면에서는 섹터 줄보다 먼저 줄인다.
    S = R.get("stocks") or {}
    stk = ""
    if S.get("n_dates") and S.get("rel_median_pct") is not None:
        stk = f", 주도주 중앙 {rhu(S['rel_median_pct']):.0f}%"
        if S.get("top3_keep_pct") is not None:
            stk += f"·종목 상위3 유지 {rhu(S['top3_keep_pct']):.0f}%"
    cells = f" (1일 칸 {R.get('n_cells', 0)}개)"
    seed = R.get("seed") or {}
    if not seed.get("dates"):
        return [head + stk + cells, head + cells, head]
    k = len(seed["dates"])
    return [f"{head}{stk}{cells} · {seed.get('label')} {k}일", f"{head}{cells} · {seed.get('label')} {k}일",
            f"{head} · {seed.get('label')} {k}일", f"{head} · 시드 {k}일"]


def revision_line(d: dict) -> "str | None":
    """:func:`revision_tiers` 의 가장 긴 꼴 — 웹 '오늘' 패널이 같은 글자를 적는다."""
    t = revision_tiers(d)
    return t[0] if t else None


#: 연속 순매매 줄의 주체 이름(페이로드 ``today.streaks.actors`` 가 없을 때).
STREAK_KO = {"inst": "기관", "invpen": "투신+연기금", "forgn": "외국인", "indiv": "개인", "etc": "기타법인"}


def fmt_big(v) -> str:
    """누적 금액 — |값| 이 1조(10,000억) 이상이면 `-4.1조`, 아래는 `-3,210억`. 웹 `BIG` 와 같은 반올림."""
    if v is None:
        return "—"
    if abs(rhu(v)) >= 10000:
        return ("+" if v >= 0 else "-") + f"{abs(rhu(v / 10000, 1)):.1f}조"
    return fmt_amt(v) + "억"


def _streak_text(f: "dict | None") -> str:
    if not f or not f.get("days"):
        return "연속 없음(오늘 ≈0)"
    side = "순매수" if f.get("sign", 0) > 0 else "순매도"
    return f"{f['days']}일 연속 {side} 누적 {fmt_big(f.get('sum'))}{_pct_tag(f.get('pct'))}"


def streak_items(d: dict, market: str) -> "list[str] | None":
    """첫 화면 연속 순매매 줄의 항목 — 시장 합계 4주체, 그다음 기관·투신+연기금·그날 최대 순매매 주체의 최장 섹터.

    페이로드 ``today.streaks`` 를 옮길 뿐이다(정의 ``sector_flow.streak_block``). 없으면(옛 리포트) None.
    웹 '오늘' 패널이 같은 항목을 같은 순서로 적는다(검증기 W 층이 대조한다).
    """
    S = (d.get("today") or {}).get("streaks")
    if not isinstance(S, dict):
        return None
    ko = {**STREAK_KO, **(S.get("actors") or {})}
    M = (S.get("market") or {}).get(market) or {}
    items = [f"{ko.get(a, a)} {_streak_text(M.get(a))}" for a in S.get("market_actors") or ()]
    rows = (S.get("sectors") or {}).get(market) or {}
    for a, _label in banner_actors(d, market):
        sec = ((S.get("longest") or {}).get(market) or {}).get(a)
        items.append(f"{ko.get(a, a)} 최장 " + (f"{sec} {_streak_text((rows.get(sec) or {}).get(a))}" if sec else "없음"))
    return items


def streak_prefix(market: str) -> str:
    return f" 연속 순매매 {market}[일·누적(연속 길이 1년%ile)]: "


def change_title(C: dict) -> list[str]:
    """직전 리포트 대비 제목 후보 — 직전이 잠정·검증 실패였으면 적는다(리뷰 8), 20일 누적 기준(리뷰 9)."""
    st_ = "확정" if C.get("prev_final") else "잠정"
    if C.get("prev_verify_failed"):
        st_ += "·검증 실패"
    w, n = C.get("window", 20), C.get("top_n", 5)
    pd_ = str(C.get("prev_date", "?"))
    return [f" 직전 리포트({pd_}·{st_}) 대비 — {w}일 누적 기준 순매수 상위{n} 섹터의 들고 남",
            f" {pd_[5:]}({st_}) 대비 {w}일 누적 상위{n} 들고 남",
            f" {pd_[5:]} 대비 {w}일 상위{n} 들고 남"]


def final_top_changes(d: dict, n: int = 3) -> list[str]:
    """확정 리포트 — 잠정→확정에서 가장 크게 바뀐 1일 섹터 칸 ``n`` 개(``final_diff.changed`` 는 |차| 큰 순)."""
    fd = d.get("final_diff") if d.get("final") else None
    if not fd:
        return []
    ko = dict(TODAY_KO)
    out = []
    for r in fd.get("changed") or []:
        if r.get("win") != 1 or r.get("diff") is None:
            continue
        out.append(f"{r['market']}/{r['sector']} {ko.get(r['actor'], r['actor'])} "
                   f"{fmt_amt(r.get('old'))}→{fmt_amt(r.get('new'))}")
        if len(out) >= n:
            break
    return out


def final_extreme_changes(d: dict, market: str, actors) -> list[str]:
    """확정 리포트 — 잠정→확정에서 오늘 %ile 극단(≥95·≤5) 목록에 들고 난 섹터(``final_diff.extremes_change``)."""
    fd = d.get("final_diff") if d.get("final") else None
    if not fd:
        return []
    ch = (fd.get("extremes_change") or {}).get(market) or {}
    out = []
    for a, ko in actors:
        c = ch.get(a) or {}
        for key, what in (("hi_in", f"≥{PCT_HI:.0f} 들어옴"), ("hi_out", f"≥{PCT_HI:.0f} 나감"),
                          ("lo_in", f"≤{PCT_LO:.0f} 들어옴"), ("lo_out", f"≤{PCT_LO:.0f} 나감")):
            out += [f"{ko.split('(')[0]} {what} {s}" for s in c.get(key) or []]
    return out


def _banner_today(st: State, width: int) -> list[str]:
    d, mk = st.d, st.market
    mkts = [mk] if mk != "전체" else st.markets[1:]
    prov = not d.get("final")
    lines = _ctx_lines(d, mkts, width)
    M = ((d.get("today") or {}).get("market") or {}).get(mk) or {}
    net = M.get("net") or {}
    parts = [f"{ko} {fmt_amt((net.get(k) or {}).get('v'))}{_pct_tag((net.get(k) or {}).get('pct'))}"
             for k, ko in ACTORS]
    head = f" 오늘 {mk} 순매수[억(1년%ile)]: "
    if cell_len(head + " · ".join(parts)) <= width:
        lines.append(pad(head + " · ".join(parts), width))
    elif cell_len(head + " · ".join(parts[:2])) <= width:  # 4주체는 합 ≈ 0 이라 하나라도 잘리면 그림이 틀린다 — 두 줄로
        lines.append(_items_line(head, parts[:2], width))
        lines.append(_items_line("   ", parts[2:], width))
    else:                                   # 더 좁으면 머리말을 따로 한 줄에 둔다
        lines.append(pad(head.rstrip(), width))
        lines.append(_items_line("   ", parts[:2], width))
        lines.append(_items_line("   ", parts[2:], width))
    chips = _gap_chips(d, mkts if mk != "전체" else ["전체", *mkts])
    if chips:
        lines.append(_items_line(" ⚠ ", chips + ["수량×종가 근사 — ? 도움말"], width))
    unc = unclassified_count(d, mkts)
    if unc:
        lines.append(pad(_first_fitting(width, [
            f" ※ (미분류) {unc}종목 — 벤더 마스터에 섹터가 빈 종목이다(섹터가 아니다). ? 도움말",
            f" ※ (미분류) {unc}종목 — 섹터가 아니다"]), width))
    rl = revision_line(d)
    if rl:
        lines.append(pad(_first_fitting(width, revision_tiers(d)), width))
    sti = streak_items(d, mk)
    if sti is not None:
        lines.append(_items_line(streak_prefix(mk), sti, width))
    lines.append(pad("", width))
    actors = banner_actors(d, mk)
    ranked_lines = []
    rets = sector_day_ret(d, mk)
    for a, ko in actors:
        ranked = _today_rank(d, mk, a)
        stocks = day_stocks(d, mkts, a)
        ranked_lines.append(_ranked_line(f"   {ko} 상위: ", ranked[:3], stocks, "buy", width, rets))
        ranked_lines.append(_ranked_line(f"   {ko} 하위: ", ranked[::-1][:3], stocks, "sell", width, rets))
    # 종목 꼬리표가 하나도 안 들어간 폭이면 제목에서도 `[ ]` 설명을 뺀다 — 없는 것을 설명하지 않는다.
    tags = any("[" in ln for ln in ranked_lines)
    lines.append(pad(_first_fitting(width, ([
        f" 오늘 섹터 순매수[억(1년%ile)] 1일 수익률[%] — {mk} · 금액 순 · [ ] = 그 주체의 그날 종목(1 키로 1일 창)",
        f" 오늘 섹터 순매수[억(1년%ile)] 수익률[%] — {mk} · 금액 순 · [ ]=종목"] if tags else [
        f" 오늘 섹터 순매수[억(1년%ile)] 1일 수익률[%] — {mk} · 금액 순 · 1 키로 1일 창(그날 종목)"]) + [
        f" 오늘 섹터 순매수[억(1년%ile)] 수익률[%] — {mk}"]), width))
    lines += ranked_lines
    tag = "(잠정) " if prov else ""
    for a, ko in actors:
        hi, lo = today_extremes(d, mk, a)
        lines.append(_items_line(f"   {tag}{ko} %ile≥{PCT_HI:.0f}: ", hi, width))
        lines.append(_items_line(f"   {tag}{ko} %ile≤{PCT_LO:.0f}: ", lo, width))
    fch = final_extreme_changes(d, mk, actors)
    if d.get("final") and d.get("final_diff"):
        lines.append(_items_line("   잠정→확정 극단 목록 변화: ", fch, width, empty="없음"))
    lines.append(pad("", width))
    C = d.get("change")
    if C:
        lines.append(pad(_first_fitting(width, change_title(C)), width))
        for a, ko in BANNER_ACTORS:
            items = change_items(d, mk, a)
            if items is None:
                lines.append(pad(f"   {ko}: 비교 없음 — 직전 리포트에 이 시장이 없다", width))
            else:
                lines.append(_items_line(f"   {ko}: ", items, width, empty="변동 없음"))
    else:
        why = d.get("change_reason") or "비교 못 함"
        # 이유를 지키고 머리말을 줄인다 — 120칸에서 이유가 통째로 잘려 "비교 없음" 만 남았다(3차 리뷰).
        lines.append(pad(_first_fitting(width, [f" 직전 리포트 대비: 비교 없음 — {why}",
                                                f" 직전 대비 없음: {why}",
                                                f" 직전 대비 없음: {why.split(' — ')[0]}",
                                                " 직전 리포트 대비: 비교 없음"]), width))
    return lines


def _banner_20d(st: State, width: int) -> list[str]:
    """옛 리포트(페이로드에 `today` 가 없다) — 20일 블록에서 상위·하위만 적는다."""
    d, mk, w = st.d, st.market, BANNER_WIN
    mkts = [mk] if mk != "전체" else st.markets[1:]
    lines = [pad(_first_fitting(width, [
        " 이 리포트에는 '오늘' 블록이 없다(옛 형식) — 20일 기준으로 적는다. 다시 만들려면 KR_QUANT_FORCE=1",
        " 옛 리포트 — '오늘' 블록 없음, 20일 기준", " 옛 리포트 — 20일 기준"]), width)]
    rows = _block_rows(d, w, mk)
    inst = sorted((r for r in rows if r.get("inst") is not None), key=lambda r: -r["inst"])
    trust = _sector_trusted(d, mkts, w)
    tr = sorted(((r, trust[r["sector"]]) for r in rows if r.get("sector") in trust),
                key=lambda t: -t[1])
    for label, ranked in (("기관", [(r, r["inst"]) for r in inst]), ("투신+연기금", tr)):
        top = [f"{_sec_label(r)} {fmt_amt(v)}" for r, v in ranked[:3]]
        bot = [f"{_sec_label(r)} {fmt_amt(v)}" for r, v in ranked[::-1][:3]]
        lines.append(_items_line(f" {label} {w}일 순매수 상위[억]: ", top, width))
        lines.append(_items_line(f" {label} {w}일 순매수 하위[억]: ", bot, width))
    return lines


#: 부호 반전 목록 아래 줄 — 웹 수치표 '오늘' 패널의 주석과 같은 말(긴 것부터).
FLIP_NOTE = ("   눌림인지 전환인지는 판정하지 않는다 — 리포트에는 구간 수익률뿐이다.",
             "   눌림·전환은 판정하지 않는다.")


#: 첫 화면의 마지막 줄 — **어느 높이에서도** 남는다(리뷰 M5: 80x24 에서 잘려 사라졌다).
BANNER_DISCLAIMER = (
    " 사실 요약이다 — 순위·추천이 아니다. 섹터 수급의 앞날 관계는 검정함 — 증거 없음(docs/validation/).",
    " 사실 요약 — 추천 아님. 섹터 수급의 앞날 관계는 검정함 — 증거 없음.",
    " 추천 아님. 섹터 수급 앞날 관계: 검정함 — 증거 없음.",
    " 사실 요약 — 추천 아님.")


def _banner_title(d: dict, width: int, mark: str = "") -> str:
    chip = status_chip(d)
    more = " · ↓↑ 더 보기" if mark else ""
    title = f" 오늘의 수급 · {d.get('asof', '')} {chip} — 아무 키나 눌러 표로{more}"
    return pad(_first_fitting(width, [title + " · o:다시 열기 · q:종료" + mark, title + mark,
                                      f" 오늘의 수급 · {d.get('asof', '')} {chip}{more}{mark}",
                                      f" 오늘의 수급 {chip}{mark}"]), width)


def basis_line(width: int) -> str:
    """첫 화면 둘째 줄 — 자료 범위(KRX 단독·NXT 제외). 도움말 끝 절까지 안 내려가도 보이게(리뷰 3차 D1-m5)."""
    return pad(_first_fitting(width, [f" {DATA_BASIS_SHORT} — 근거는 ? 도움말 끝", f" {DATA_BASIS_SHORT}"]), width)


def banner_lines(st: State, width: int) -> list[str]:
    """시작 배너 — 표를 그리기 전에 뜨는 **사실 요약**("오늘의 수급"). `o` 로 다시 연다.

    점수·순위·추천을 싣지 않는다. 예전 배너는 G 통과 섹터와 곱순위 상위 종목을 먼저
    보였는데, 검정 결과 섹터 지표는 앞날 증거가 없고 G 는 판정 불가이며, 곱의 종목 축은
    투신+연기금을 "많이 살수록 좋다" 로 세는데 그 순위 정보는 부호가 **음**이었다
    (docs/validation/). 그래서 여기에는 누가 어디서 사고팔았나만 적는다 — 모든 값은
    페이로드 `today`·`change`·`final_diff`·`revision_stats` 에 이미 있는 것을 옮길 뿐이고, 줄 순서는 금액
    크기(측정값)다. 웹 수치표 맨 위 "오늘" 패널이 같은 사실을 같은 순서로 적는다.
    마지막 줄은 언제나 :data:`BANNER_DISCLAIMER` 다(:func:`banner_view` 가 높이와 무관하게 남긴다).
    """
    d = st.d
    lines = [_banner_title(d, width), basis_line(width)]
    lines += _banner_today(st, width) if st.has_today else _banner_20d(st, width)
    lines.append(pad("", width))
    ko = ACTORS[st.ai][1]
    flips = [f"{s} {fmt_amt(p)}{TREND_ARROW}{fmt_amt(f)}" for s, p, f in reversal_flags(st)]
    lines.append(pad(_first_fitting(width, [
        f" {ko} 최근 5일 부호가 직전 15일과 반대[억, 직전15일{TREND_ARROW}최근5일, 5일 |금액| ≥ 목록의 "
        f"{FLIP_MIN_SHARE * 100:.0f}%]:",
        f" {ko} 최근 5일 부호가 직전 15일과 반대[억, 직전15일{TREND_ARROW}최근5일]:"]), width))
    lines.append(_items_line("   ", flips, width))
    # 웹 '오늘' 패널과 같은 말 — 목록만 두면 눌림·전환 판정으로 읽힌다(`reversal_flags` 는 판정 불가 라벨이다).
    lines.append(pad(_first_fitting(width, list(FLIP_NOTE)), width))
    fdl = final_diff_line(d)
    if fdl:
        lines.append(pad(_first_fitting(width, [fdl, fdl.split(" — ")[0]]), width))
        top = final_top_changes(d)
        if top:
            lines.append(_items_line("   큰 변경(1일): ", top, width))
    lines.append(pad(_first_fitting(width, list(BANNER_DISCLAIMER)), width))
    return lines


def banner_view(st: State, width: int, height: int) -> list[str]:
    """첫 화면을 ``height`` 줄에 — 제목과 마지막 줄(면책)은 **늘** 남기고 가운데를 ``st.brow`` 부터 보인다.

    예전엔 앱이 ``lines[:h]`` 로 잘라 80x24 에서 면책 줄이 사라졌다(리뷰 M5). 넘치면 제목에 위치를 적는다.
    """
    lines = banner_lines(st, width)
    if len(lines) <= height:
        st.brow = 0
        return lines
    if height < 2:
        return lines[-1:][:max(height, 0)]
    body, last = lines[1:-1], lines[-1]
    room = max(height - 2, 0)
    st.brow = max(0, min(st.brow, len(body) - room))
    mark = f" {st.brow + 1}-{st.brow + room}/{len(body)}" if room else ""
    return [_banner_title(st.d, width, mark)] + body[st.brow:st.brow + room] + [last]


_REPORT_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def status_chip(d: dict) -> str:
    """제목 옆 상태 칩 — 값의 지위와 리포트 경고.

    ``finalized`` 는 **시계만 본다**(수집 창이 지났나). 값이 확정됐다는 뜻이 아니다 — 당일 수급은
    잠정이고 확정치는 다음날 새벽 ``daily_finalize`` 가 DB 에 덮어쓰며, 08:20 확정 모드가 그 기준일을 다시 만든다.
    한때 "확정" 이라고 적었다. 페이로드 키 이름은 옛 리포트와 맞추려고 그대로 둔다.
    """
    # ``final`` 은 다음날 아침 daily_finalize 확인 뒤 확정치로 다시 만든 리포트에만 있다(daily_report.sh 확정 모드).
    base = ("마감·확정" if d.get("final") else "마감·확정기록없음" if d.get("finalize_unverified")
            else "마감·잠정" if d.get("finalized") else "장중·미확정")
    # ``rebuilt`` 은 지난 기준일을 지금 코드·확정 데이터로 다시 만든 리포트(KR_QUANT_ASOF)에만 있다 — 그날 게시본이 아니다.
    if d.get("rebuilt"):
        base += "·재구성"
    return " ".join([base, *(f"⚠ {w}" for w in d.get("report_warnings") or ())])


def report_warnings(report_dir: str, asof: str) -> list[str]:
    """열린 리포트에 대해 화면이 먼저 말해야 할 것 — 폴더만 읽는다.

    * 그 폴더에 ``VERIFY_FAILED.txt`` — 검사에 걸린 리포트다. 파일로만 남기면 화면만 보는 사람은 모른다.
    * 더 **뒤** 기준일 폴더에 ``INCOMPLETE.txt`` — 오늘 수집이 안 끝나 ``latest`` 가 전날에 머문다.
      제목의 날짜만으로는 "어제 것을 보고 있다" 는 걸 놓친다.
    """
    real = os.path.realpath(os.path.expanduser(report_dir))
    out = []
    if os.path.exists(os.path.join(real, "VERIFY_FAILED.txt")):
        out.append("검증 실패(VERIFY_FAILED.txt)")
    root = os.path.dirname(real)
    try:
        days = sorted(n for n in os.listdir(root) if _REPORT_DAY.fullmatch(n) and n > str(asof))
    except OSError:
        days = []
    out.extend(f"{n[5:]} 수집 미완" for n in days
               if os.path.exists(os.path.join(root, n, "INCOMPLETE.txt")))
    return out


#: 기준일이 이만큼(달력일) 지나면 화면이 말한다. 금요일 리포트를 월요일 오전에 보는 3일은
#: 정상이므로 그보다 위에서 끊는다.
STALE_DAYS = 4


def staleness_warning(asof: str, today: date | None = None) -> list[str]:
    """리포트가 며칠 묵었나 — 받은 폴더나 멈춘 배치를 오늘 것으로 읽지 않게."""
    try:
        age = ((today or date.today()) - date.fromisoformat(str(asof))).days
    except ValueError:
        return []
    return [f"{age}일 지난 리포트"] if age >= STALE_DAYS else []


def missing_report_hint(report_dir: str) -> str:
    """리포트가 없는 폴더를 열었을 때 — 수집 미완 폴더면 그 사유를 말한다."""
    path = os.path.join(os.path.expanduser(report_dir), "INCOMPLETE.txt")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return "\n  수집 미완이라 만들지 않은 폴더다:\n  " + f.read().strip().replace("\n", "\n  ")


def header_lines(st: State, width: int) -> list[str]:
    d = st.d
    chip = status_chip(d)
    l1 = f" 섹터 자금 흐름 · {d['asof']} {chip}"
    if st.explore:
        # 탐색 표지는 **제목 줄 앞**에 붙인다 — 폭이 모자라면 날짜·칩보다 먼저 줄지 않게
        # 짧은 문구로 내려가고, 어느 폭에서도 `[탐색` 은 남는다.
        l1 = _first_fitting(width, [f" [{t}]{l1}" for t in EXPLORE_LABEL_TIERS]
                            + [f" [{t}]" for t in EXPLORE_LABEL_TIERS])
    label, note = SORTS[st.si][1], ""
    # 종합 화면은 **정렬 자체가 없다**(페이로드 순서). 예전엔 방향 화살표만 빼고
    # 열 이름은 남겼는데, `s` 를 누르면 그 이름이 바뀌어서 정렬이 된 것처럼
    # 보였다 — 화살표를 지운 판단을 열 이름까지 밀고 간다. 이 화면이 무엇으로
    # 줄세워졌는지는 첫 열(G) 이 말한다.
    if st.allv and not st.drill:
        # 전 종목 화면은 곱 내림차순 **고정**이다. 섹터 표의 정렬 칩을 그대로 그리면
        # `정렬[가속▼]` 이 곱 순 목록 위에 뜬다 — 화면이 자기 순서를 틀리게 말한다.
        sort_chip = " 정렬없음[곱 순]"
    elif st.sortable:
        key, fell = st.effective_sort(st.rows())
        if fell:
            note = f"  ※ 이 구간에 {label} 값이 없다"
            label = dict(SORTS).get(key, key)
        ev = sort_evidence(st, key)
        sort_chip = f" 정렬[{label}{'▲' if st.rev else '▼'}]{B_SHORT[ev[0]] if ev else ''}"
    else:
        sort_chip = " 정렬없음[G 순]"
    l2 = (f" 구간[{st.window}] 시장[{st.market}] 주체[{ACTORS[st.ai][1]}]"
          f"{sort_chip}{note}")
    # 폭이 모자라면 **덩어리째** 뺀다. 그냥 자르면 `k=10.179` 가 `k=1` 로 남아 다른 숫자가 되고,
    # `…풀림·G 는` 에서 끊기면 "기관 기준" 이 사라진다.
    if st.actor != "inst" and st.window != "종합" and st.explore:
        notes = ("  ※ 미실현·풀림가속·dW/dt·G 는 기관 기준",
                 "  ※ 미실현 등은 기관 기준", "  ※ 기관 기준")
    else:
        notes = ("", "", "")
    meta = st.block_meta()
    metas = (f"  {meta}" if meta else "", f"  {meta.split(' · ')[0]}" if meta else "", "")
    order = ((0, 0), (0, 1), (1, 1), (0, 2), (1, 2), (2, 2))
    # 마지막 단계는 꼬리 없이 — 탐색 모드 좁은 폭에서 `※ 기관 기준` 이 `※ 기관 기` 로 잘려 남았다.
    l2 = _first_fitting(width, [l2 + notes[n] + metas[m] for n, m in order] + [l2])
    return [pad(l1, width), pad(l2, width)]


#: 표의 한 열. **헤더·폭·정렬·값 추출을 한 자리에** 둔다.
#:
#: 예전엔 `table_cols()`(헤더) 와 `table_lines()`(셀) 가 같은 사실을 두 번 적었다 —
#: 폭 상수(11·8·7·1·12·9·10…)가 양쪽에 각각 있었고, 폭 임계값(>=100·>=132·>=150)도
#: 양쪽에 있었고, 심지어 분기 모양이 서로 달랐다(한쪽은 if/elif, 다른 쪽은 if 두 개).
#: 결과가 같았던 것은 우연이고, 어긋나기 직전이었다. 이제 렌더는 이 정의를
#: **순회만** 하므로 "헤더와 셀이 어긋나는" 버그가 표현 불가능하다 —
#: swing-it docs/GUARDRAILS.md §0 의 "위험한 기능을 코드에서 제거" 와 같은 처방이다.
Col = namedtuple("Col", "header width right fn")      # fn(r, st) -> str


#: 스파크라인·발산 막대에 쓰는 글자는 **브라유**(U+28xx) 다.
#: 누적 경로의 높이 4단계. 브라유는 4행 × 2열이라 **아래에서부터 채우면**
#: 진짜 막대가 된다 — 세로 위치로 부호를 흉내내던 예전 글자표(⠛⠒⠀⠤⣤)보다
#: 읽기 쉽다. 그쪽은 0 이 빈칸이라 "값이 0" 과 "조각이 없다" 가 구분되지 않았고,
#: 기준선이 없어 위/아래를 잡을 데가 없었으며, 크기 단계도 둘뿐이었다.
#:
#: East Asian Width 가 'N'(Narrow) 이라 어떤 로케일에서도 1칸이다. 블록 문자
#: ``▁▂▃▄▅▆▇█`` 는 'A'(Ambiguous) 라 한글 로케일 터미널이 2칸으로 그릴 수 있고,
#: 그러면 그 행만 통째로 밀린다. 이 저장소는 폭 계산 함정을 이미 세 번 밟았다.
SPARK = ("⣀", "⣤", "⣶", "⣿")
SPARK_EMPTY = "⠀"      # 조각이 없는 자리(짧은 구간의 왼쪽). 값 0 과 구분된다.


def spark(vals, cells: int = 8) -> str:
    """구간 동안 **순매수가 누적된 경로**. 오른쪽 끝이 구간 끝이다.

    예전에는 조각별 순매수를 부호 있는 글자로 그렸다. 그건 "이 조각에 얼마가
    들어왔나" 라 조각마다 오르내려서, 정작 묻고 싶은 **"지금 들어오는 중인가,
    이미 끝났나"** 가 모양으로 안 드러났다. 누적으로 바꾸면 그게 기울기가 된다 —
    오른쪽으로 **올라가면 계속 들어오는 중**, 내려가면 빠져나가는 중, 평평하면 멈췄다.

    높이는 **그 행 안에서** 경로의 최저~최고를 4단계에 편다(0 도 범위에 넣는다).
    행끼리 높이는 비교되지 않는다 — 크기는 임펄스 열이 말한다.
    """
    vals = [v for v in (vals or []) if v is not None]
    if not vals:
        return pad("—", cells, right=True)
    cum, t = [], 0.0
    for v in vals:
        t += v
        cum.append(t)
    lo, hi = min(0.0, *cum), max(0.0, *cum)
    span = hi - lo
    out = ""
    for c in cum:
        # span 이 0 이면(전 구간 0) 전부 최저단계 — 평평한 바닥이 곧 "아무 일 없음".
        lv = 0 if not span else min(3, int((c - lo) / span * 3 + 0.5))
        out += SPARK[lv]
    # 조각이 cells 보다 적으면(짧은 구간) **왼쪽**을 비운다 — 오른쪽 끝이
    # 구간 끝이라는 약속을 깨지 않기 위해서다.
    return SPARK_EMPTY * max(0, cells - len(out)) + out


def _num(key, nd=2):
    return lambda r, st: fmt_pct(r.get(key), nd)


#: 정렬 키 → 그 열의 헤더 이름. 하이라이트할 열을 찾는 데 쓴다.
SORT_COL = {"G": "G", "flow": "임펄스[억]", "accel": "가속[%p]",
            "ret": "수익률[%]", "pct": "1년[%ile]",
            "trust5": "투신+연기금5일[억]", "trustw": "투신+연기금[억]",
            "x": "미실현[%p]", "P": "dW/dt[%p/일]",
            "xddot": "풀림가속[%p/일²]", "n_all": "종목[수]"}   # 거래대금·시총은 표에 열이 없어 하이라이트 대상이 아니다
NAME_SORT_COL = {"flow": "순매수[억]", "pick": "선정",
                 "part": "참여율[%]", "a": "시총대비[%p]",
                 "invtrt": COL_INVTRT, "penfnd": COL_PENFND,
                 "conc": "최근집중[%]", "rrel": "상대수익[%p]",
                 "cap": "시총[억]", "tv": "거래대금[억]", "name": "종목"}


def _lead(side: str):
    """이름 13칸 + 금액 9칸(우측정렬). 이름 길이가 제각각이라 그냥 이어붙이면
    금액이 줄마다 다른 칸에 떨어진다."""
    def fn(r, st):
        arr = (r.get("top") or {}).get(side) or []
        t = arr[0] if arr else None
        if not t:
            return pad("—", 13) + " " + pad("", 9, right=True)
        return pad(clip(t["name"], 13), 13) + " " + pad(fmt_amt(t["flow"]) + "억", 9, right=True)
    return fn


#: 섹터 표의 열 — **왼쪽부터 중요한 순서**이고, 폭이 되는 데까지 `_fit` 이 자른다.
#:
#: 순서는 물리 서사를 따른다: 힘(가속·임펄스) → 그 힘이 1년 안에서 어느 정도인가
#: (1년%·추이) → 운동(수익률) → 차이(미실현) → 해소(풀림) → 요약(G) → 누가(주도주).
#: 예전엔 결론인 G 가 자기 입력(가속·미실현·풀림)보다 **왼쪽**에 있었고, 판단
#: 변수가 아니라 데이터 품질 주석인 종목수가 2번 자리를 차지했다.
#: 종목수는 오른쪽 끝으로 보냈다 — 얇은 섹터 경고는 이미 `~` 마커가 한다.
_TABLE_COLS = (
    Col("섹터", 13, False, lambda r, st: r.get("sector", "—")),
    # 마커는 **별도 1칸 열**이다. 값에 붙이면 ● 가 2칸이라 열이 밀린다.
    # 얇은 섹터(~)는 글자로도 표시한다 — 색에만 실으면 무색 터미널·색맹에서
    # 경고가 통째로 사라진다.
    # `*`(G 관문 통과)는 **탐색 모드에서만** 찍는다 — G 열과 같은 지위다.
    Col("", 1, False,
        lambda r, st: "~" if r.get("thin") else
        ("*" if st.explore and r.get("G_pass") else "")),
    Col("가속[%p]", 9, True, _num("accel")),
    Col("임펄스[억]", 12, True, lambda r, st: fmt_amt(r.get("flow"))),
    Col("1년[%ile]", 9, True,
        lambda r, st: "—" if r.get("pct") is None else str(pct_int(r["pct"]))),
    Col("추이[8]", 8, False, lambda r, st: spark(r.get("spark"))),
    Col("수익률[%]", 10, True, _num("ret")),
    # 투신+연기금 — 기관 안에서 방향성이 있는 둘(CLAUDE.md 수급 신뢰 순서). 5일(지금도 들어오나)과 이 창.
    Col("투신+연기금5일[억]", 18, True, lambda r, st: fmt_amt(r.get("trust5"))),
    Col("투신+연기금[억]", 15, True, lambda r, st: fmt_amt(r.get("trustw"))),
    Col("종목[수]", 8, True, lambda r, st: str(r.get("n_all", "—"))),
    # ── 탐색 모드(e)에서만: x 계열과 G. x 는 섹터 20일 반전과 ρ 0.82~0.93 이고 앞날 증거가 없다.
    Col("미실현[%p]", 10, True, _num("x", 1)),
    Col("풀림가속[%p/일²]", 16, True, _num("xddot", 3)),
    Col("dW/dt[%p/일]", 12, True, _num("P", 3)),
    Col("G", 6, True,
        lambda r, st: f"{rhu(r['G'], 2):.2f}" if r.get("G") is not None else "—"),
    # 이름만 잘라 넣으면 한글 길이가 제각각이라 줄마다 다르게 잘려 보인다.
    # **이름 + 금액**을 한 덩어리로 넣고 칸을 고정하면 모양이 일정하다.
    Col("순매수상위[억]", 23, False, _lead("buy")),
    Col("순매도상위[억]", 23, False, _lead("sell")),
)


def _per_win(w):
    def fn(r, st):
        per = r.get("per", {})
        v = per.get(str(w), per.get(w))
        return f"{rhu(v, 2):.2f}" if v is not None else "—"
    return fn


def table_cols(st: State, width: int) -> list[Col]:
    """섹터 표의 열 정의 — 렌더·하이라이트·검사가 **모두 이걸 본다**.

    폭에 따라 여기서 열을 빼지 않는다. 순서가 곧 우선순위이고, 자르는 일은
    `_fit` 하나가 한다 — 폭 임계값이 두 군데에 있으면 언젠가 어긋난다.
    """
    if st.window != "종합":
        # G 는 탐색 모드에서만 — 검정 결과 판정 불가(검정력 없음)라 기본 화면에 두지 않는다.
        return [c for c in _TABLE_COLS if st.explore or c.header not in EXPLORE_COLS]
    cols = [_TABLE_COLS[0], _TABLE_COLS[1],
            Col("G", 6, True,
                lambda r, st_: f"{rhu(r['G'], 2):.2f}" if r.get("G") is not None else "—")]
    wins = (st.d.get("combined", {}).get(st.market) or {}).get("windows", [])
    for w in wins:
        cols.append(Col(f"{w}일[G]", 8, True, _per_win(w)))
    # 분모는 섞은 창 수다. `seen` 은 "G 가 나온 창" 이라 언제나 pass_n 과 같아 1/1·2/2 만 나온다.
    cols.append(Col("통과[구간]", 10, True,
                    lambda r, st_: f"{r.get('pass_n', 0)}/{len(wins)}"))
    cols.append(next(c for c in _TABLE_COLS if c.header == "종목[수]"))
    return cols


def view_width(term_width: int) -> int:
    """터미널 폭에서 **뷰가 실제로 쓸 수 있는 폭**.

    curses 는 오른쪽 아래 칸에 글자를 쓰면 스크롤을 유발해 예외를 낸다. 그래서
    앱은 늘 마지막 칸을 비워 두고 그린다 — "80칸 터미널" 은 뷰에게 79칸이다.

    그 −1 이 앱에만 있었다. 그래서 "폭 80 에서 이 열이 보이는가" 를 묻는 검사는
    80 으로 물었고, 열 합계가 정확히 80 인 배치를 통과시켰다. 실제 80칸 터미널에서
    그 열은 안 보인다 — 검사와 화면이 한 칸 어긋나 있었고, 실제로 `상대수익[%p]`
    이 그렇게 잘렸다. 규칙을 한 곳에 둔다.
    """
    return max(term_width - 1, 1)


def fit_widths(widths: list[int], total: int) -> int:
    """``total`` 칸에 **온전히** 들어가는 열 **개수**. 열 사이 공백 1칸을 센다.

    첫 열은 잘려도 남긴다 — 섹터 이름은 잘려도 뜻이 남지만, 숫자는 잘리면 다른
    값이 된다(-1,360 이 -1 로 보인다).

    열 표현이 아니라 **폭 목록**만 받는다. 흐름 화면의 열은 ``Col`` 이고 원장
    화면의 열은 3-튜플이라 같은 규칙이 두 벌 살고 있었다 — 둘을 잇는 검사가
    하나도 없어서, 한쪽을 고치면 다른 쪽이 조용히 옛 규칙을 유지하고 같은 앱의
    두 화면이 열을 다르게 자른다. 폭만 받으면 양쪽이 같이 쓸 수 있다.
    """
    n, used = 0, 0
    for w in widths:
        need = w + (1 if n else 0)
        if n and used + need > total:
            break
        n += 1
        used += need
    return n


def span_at(widths: list[int], i: int) -> tuple[int, int]:
    """``i`` 번째 열의 (시작 표시칸, 폭). 열 사이 공백 1칸을 더해가며 센다."""
    return sum(w + 1 for w in widths[:i]), widths[i]


def _fit(cols: list[Col], width: int) -> list[Col]:
    """폭에 **온전히** 들어가는 열까지만 남긴다."""
    return cols[:fit_widths([c.width for c in cols], width)]


def col_span(cols: list[Col], header: str) -> tuple[int, int] | None:
    """열 헤더의 (시작 표시칸, 폭). 같은 헤더가 둘이면 **앞의 것**."""
    widths = [c.width for c in cols]
    for i, c in enumerate(cols):
        if c.header == header:
            return span_at(widths, i)
    return None


def _render(cols: list[Col], r: dict, st: State, width: int) -> str:
    return pad(" ".join(pad(c.fn(r, st), c.width, c.right) for c in cols), width)


def table_lines(st: State, width: int, height: int) -> tuple[list[str], list[bool], int]:
    """(행 문자열, 얇은섹터 여부, 헤더 줄 수). 폭에 따라 열을 줄인다.

    폭이 모자라면 **열 경계에서** 떨어뜨린다. 줄을 통째로 잘라내면 숫자가
    자릿수 중간에서 끊겨 -1,360 이 -1 로 보인다 — 안 보이는 것보다 나쁘다.
    """
    cols = _fit(table_cols(st, width), width)
    head = pad(" ".join(pad(c.header, c.width, c.right) for c in cols), width)
    out, thin = [head], [False]
    for r in st.rows():
        out.append(_render(cols, r, st, width))
        thin.append(bool(r.get("thin")))
    return out, thin, 1


def sort_span(st: State, width: int) -> tuple[int, int] | None:
    """지금 정렬 중인 열의 (시작 표시칸, 폭). 종합 화면·이름정렬은 None."""
    if st.window == "종합":
        return None
    key, _fell = st.effective_sort(st.rows())
    header = SORT_COL.get(key)
    return col_span(_fit(table_cols(st, width), width), header) if header else None


#: 종목 목록의 열. 누적 기여율이 순매수 바로 옆에 붙고, 80% 를 처음 넘긴 행에
#: 가로 마커가 선다 — 그 위가 "이 섹터를 움직인 종목", 아래는 스크롤할 0 이다.
_NAME_COLS = (
    Col("종목", 14, False, lambda t, st: clip(t.get("name", "—"), 14)),
    # 코드는 6자리고 헤더 `코드` 는 4칸이라 6이면 충분하다. 7이던 시절 그 한 칸이
    # 폭 79(=80칸 터미널)에서 `상대수익[%p]` 을 통째로 밀어냈다.
    Col("코드", 6, False, lambda t, st: t.get("code", "")),
    # **결론을 맨 앞에 둔다.** 섹터 표가 `*`·`~` 마커를 섹터 이름 바로 옆에 두는
    # 것과 같은 자리다 — 이 화면은 "고르는" 화면이라, 눈이 먼저 후보를 좁히고
    # 그 다음 오른쪽에서 근거를 확인한다. 오른쪽 끝에 두면 폭 123 부터 보여서
    # 정작 대부분의 터미널에서 없는 열이 된다(실측).
    Col("선정", 4, True,
        lambda t, st: "—" if t.get("pick") is None else f"{t['pick']:.2f}"),
    Col("순매수[억]", 14, True, lambda t, st: fmt_amt(t.get("flow"))),
    Col("누적[%]", 8, True,
        lambda t, st: "—" if t.get("cum") is None else f"{t['cum']:.0f}"),
    Col("", 1, False, lambda t, st: "-" if t.get("cut") else ""),
    Col(COL_INVTRT, 12, True, lambda t, st: fmt_amt(t.get("invtrt"))),
    Col(COL_PENFND, 14, True, lambda t, st: fmt_amt(t.get("penfnd"))),
    Col("상대수익[%p]", 12, True, lambda t, st: fmt_pct(t.get("rrel"), 1)),
    Col("최근집중[%]", 11, True, lambda t, st: fmt_conc(t.get("conc"))),
    # 참여율과 거래대금은 **붙어 있어야 한다.** 참여율은 비율이라 혼자서는
    # "살 수 있는가" 를 못 답한다 — 실측: 참여율 11~14% 구간에 DB금융스팩12호
    # (20일 거래대금 3억) 와 삼성SDI(43,543억) 가 같이 있다. 유동성이 14,000배
    # 다른데 같은 칸에 같은 숫자가 뜬다. 참여율만 보고 "기관이 장악했다" 로 읽으면
    # 살 수 없는 종목이 상단에 섞인다.
    #
    # 그래서 예전 순서(참여율 → 시총대비 → 시총 → 거래대금)에서 거래대금을
    # 앞으로 당겼다. 시총대비는 섹터를 이미 고른 뒤에는 대체로 **작은 종목을 위로
    # 올리는 재정렬**이라(상위 10 에 GS건설 옆으로 모비릭스 시총 329억이 온다)
    # 뒤로 밀어도 잃는 것이 적다. 시총은 시총대비의 분모라 그 옆에 둔다.
    Col("참여율[%]", 9, True, lambda t, st: fmt_pct(t.get("part"), 1)),
    Col("거래대금[억]", 13, True,
        lambda t, st: fmt_amt(t.get("tv")).replace("+", "")),
    Col("시총대비[%p]", 16, True,
        lambda t, st: fmt_pct(t["a"]) + _cap_mark(t) if t.get("a") is not None else "—"),
    Col("시총[억]", 13, True,
        lambda t, st: fmt_amt(t["cap"]).replace("+", "") + _cap_mark(t) if t.get("cap") else "—"),
)


def names_cols(explore: bool = False) -> list[Col]:
    """종목 목록의 열. 선정점수는 **탐색 모드에서만** 싣는다(:data:`EXPLORE_COLS`)."""
    return [c for c in _NAME_COLS if explore or c.header not in EXPLORE_COLS]


#: 혼자 남으면 **오해를 만드는** 열 → 그 열이 뜻을 가지려면 같이 있어야 하는 열.
#: 폭이 모자라 짝이 떨어지면 앞의 것도 같이 뺀다.
#:
#: `참여율[%]` 은 순매수 ÷ 거래대금이라 비율이다. 분모를 못 보면 "기관이 이 종목
#: 거래의 13% 를 먹었다" 가 살 수 있다는 뜻인지 알 수 없다 — 실측(2026-08-28,
#: 20일): 참여율 11~14% 구간에 DB금융스팩12호(거래대금 3억)와 삼성SDI(43,543억)가
#: 같이 있다. 유동성이 14,000배 다른데 화면에는 같은 숫자만 남는다.
#:
#: "덜 보여준다" 가 아니라 **틀리게 읽힐 칸을 안 만든다** 는 규칙이다. 이 저장소가
#: 얇은 섹터·정렬 없는 화면·뜻을 잃은 비율에서 이미 같은 선택을 했다.
COL_PAIRS = (("참여율[%]", "거래대금[억]"),)


def fit_names(width: int, explore: bool = False) -> list[Col]:
    """폭에 들어가는 종목 열 — :data:`COL_PAIRS` 의 짝은 통째로 남거나 통째로 빠진다.

    ``width`` 는 **뷰 폭**이다(:func:`view_width` 를 이미 통과한 값). 앱이 한 번
    빼고 여기서 또 빼면 한 칸씩 좁아진다 — 그 −1 이 두 곳에 있어서 난 버그를
    방금 고쳤으므로 같은 실수를 반대 방향으로 만들지 않는다.
    """
    cols = _fit(names_cols(explore), width)
    heads = [c.header for c in cols]
    drop = {a for a, b in COL_PAIRS if a in heads and b not in heads}
    return [c for c in cols if c.header not in drop]


def name_sort_span(st: State) -> tuple[int, int] | None:
    """종목 목록에서 지금 정렬 중인 열의 (시작 표시칸, 폭)."""
    header = NAME_SORT_COL.get(st.name_sort)
    return col_span(names_cols(st.explore), header) if header else None


#: 상세 패널 첫 줄의 들여쓰기. :func:`detail_lines` 와 :func:`detail_title_span`
#: 이 **같은 상수**를 봐야 색이 글자와 어긋나지 않는다.
DETAIL_INDENT = " "


def detail_title_span(st: State, width: int) -> tuple[int, int] | None:
    """상세 패널 첫 줄에서 **섹터 이름**의 (시작 표시칸, 폭). 없으면 None.

    이 줄에서 "지금 무엇을 보고 있는가" 를 말하는 건 섹터 이름 하나뿐인데,
    줄 전체가 한 색이라 부속 정보(`종목 56개`·`Enter 로 전체`)에 묻혀 있었다.

    좌표를 **뷰가 낸다.** 앱이 문자열을 다시 뜯어 이름 길이를 추측하면 문구를
    고칠 때 색이 조용히 어긋난다 — 이 저장소는 표 헤더와 셀, 히트맵과 색에서
    이미 두 번 밟았다(`col_span`·`name_sort_span`·`is_section` 이 같은 관용구다).
    한글이 두 칸이라 문자 인덱스가 아니라 **표시 칸**을 낸다.
    """
    rows = st.rows()
    if not rows:
        return None
    if st.drill:
        names = st.names()
        name = names[min(st.drow, len(names) - 1)].get("name") if names else None
    else:
        name = rows[min(st.row, len(rows) - 1)].get("sector")
    name = name or "—"
    start = cell_len(DETAIL_INDENT)
    w = min(cell_len(name), max(width - start, 0))
    return (start, w) if w > 0 else None


#: 구간 열 폭. 라벨(`120-60`)과 금액(`-1,662`) 중 긴 쪽이 기준이다.
TREND_COL_W = 9


def _stock_detail_lines(st: State, width: int) -> list[str]:
    """종목 목록에서 고른 **그 종목**의 자금 성격 추이 — 겹치지 않는 구간별.

    드릴다운에는 예전엔 패널이 아예 없었다(`layout` 이 `detail=0` 으로 죽였다).
    그런데 종목을 고르는 자리에서 정작 묻는 것이 "이 돈이 언제 들어왔나" 다 —
    표는 **현재 창 하나**만 보여주므로 창을 네 번 바꿔야 답이 나왔다.

    표에 안 싣고 패널에 두는 이유: 구간 4개 × 주체 2 = 8칸이라 열로 만들면
    종목 표가 통째로 밀린다. 패널은 **고른 한 종목**만 그리므로 그 값이 든다.
    """
    names = st.names()
    if not names:
        return [pad(f"{DETAIL_INDENT}(종목 없음)", width)]
    t = names[min(st.drow, len(names) - 1)]
    win = ((st.d.get("names") or {}).get(t.get("code")) or {}).get("win") or {}

    label_w = max(cell_len(lab) for _, lab in TREND_ACTORS) + 2
    def _row(label: str, cells: list[str]) -> str:
        out = DETAIL_INDENT + pad(label, label_w)
        for c in cells:
            out += pad(c, TREND_COL_W, right=True)
        return out

    head = _row("", [lab for lab, _, _ in TREND_SEGS])
    body = [_row(lab, [fmt_amt(v) for _, v in segment_flows(win, key)])
            for key, lab in TREND_ACTORS]
    # 제목 줄은 **중첩을 뺀 값**임을 적는다. 안 적으면 `120-60` 이 "120일 값"
    # 으로 읽히고, 그러면 최근 5일치가 네 칸에 네 번 세어진 것처럼 보인다.
    title = (f"{DETAIL_INDENT}{t.get('name', '—')}"
             f" · 구간별 순매수[억] — 겹치지 않게 차분(오래된→최근)")
    cp = t.get("cap_pending")
    if cp:
        title = f"{DETAIL_INDENT}{t.get('name', '—')} · 구간별 순매수[억] · {CAP_PENDING_MARK}권리락 {cp['date'][5:]} {cp['rt']:+.1f}% 신주 미반영"
    return [pad(title, width), pad(head, width)] + [pad(b, width) for b in body]


def detail_lines(st: State, width: int) -> list[str]:
    rows = st.rows()
    if not rows:
        return [pad(f" ({st.empty_reason()})", width)]
    if st.drill:
        return _stock_detail_lines(st, width)
    r = rows[min(st.row, len(rows) - 1)]
    top = r.get("top") or {}
    def side(key, label):
        arr = top.get(key) or []
        if not arr:
            return f" {label}: —"
        # 정렬이 **금액** 순이므로 금액을 보여준다. 시총대비(%p)를 보이면
        # 표시값과 순서가 어긋나 보인다(현대 +1.34%p 가 대우 +1.40%p 위에 오는 식).
        parts = [f"{t['name']} {fmt_amt(t['flow'])}억" for t in arr[:3]]
        return f" {label}: " + " · ".join(parts)
    n = (r.get("top") or {}).get("n", 0)
    # 미실현·포텐셜은 여기 안 적는다. 미실현은 **표에 이미 숫자로 있고**
    # (`Col("미실현[%p]", …, _num("x", 1))`), 이 줄은 그것이 발산 막대였던
    # 시절 "도형은 순서를, 숫자는 크기를" 이라며 붙인 것이다 — 막대를 숫자로
    # 되돌리면서 이쪽을 안 지워 같은 값이 두 자리에 남았다.
    #
    # 포텐셜(U = ½k·x²)은 `x` 가 제곱돼 **부호가 죽는다** — 미실현 +2.9 인
    # 섹터와 −2.9 인 섹터가 같은 U 를 받는다. 도움말이 말하는 Spearman 1.0000
    # 도 `x` 가 아니라 `|x|` 와의 순위상관이고 k>0 인 한에서만 성립한다.
    # 그런데 이 화면의 논지는 방향이 핵심이라(`G_pass` 가 x>0 을 요구한다),
    # 방향을 지운 값이 무슨 질문에 답하는지가 불분명하다. 정렬 열로는 남는다.
    # 종합은 창을 섞은 축이라 4주체가 **대표 창(20일)** 값이다 — 그 사실을 줄에
    # 적는다. 안 적으면 위 표(종합)와 이 줄(20일)이 다른 것을 말하는데 화면은
    # 같은 것처럼 보인다.
    note = " · 20일 기준" if st.window == "종합" else ""
    out = [pad(f"{DETAIL_INDENT}{r.get('sector','—')}"
                f" · 종목 {n}개 · Enter 로 전체", width),
           pad(_actors_line(r, width, note, ACTORS[st.ai][1]), width),
           pad(side("buy", "순매수 상위"), width),
           pad(side("sell", "순매도 상위"), width)]
    pending = cap_pending_note(st.d, [st.market] if st.market != "전체" else st.markets[1:], r.get("sector"))
    if pending and cell_len(out[0].rstrip() + pending) + 1 <= width:
        out[0] = pad(out[0].rstrip() + pending, width)
    return out


#: 상세 패널의 4주체 줄 — 넓은 것부터. 폭이 모자라면 뒤에서 잘라 낸다.
_ACTORS_TIERS = (
    ("개인", "외국인", "기관", "기타법인"),
    ("개인", "외국인", "기관"),
    ("개인", "기관"),
)
_ACTOR_KEY = dict((ko, k) for k, ko in ACTORS)


def _actors_line(r: dict, width: int, note: str = "", actor: str | None = None) -> str:
    """선택 섹터의 **4주체 순매수 분해**를 한 줄에 — 표에 없는 나머지 셋까지.

    화면이 한 번에 한 주체만 보여주므로 "기관이 팔았다" 까지는 알아도
    **누가 받았는지**는 앱을 바꿔야 알 수 있었다(`sw-ledger` 의 원장 화면).
    주식은 누가 사면 누가 판 것이고 4주체 합은 0 에 닫히므로, 그 답은
    같은 페이로드 안에 이미 있다 — 화면을 바꿀 이유가 없다.

    라벨은 안 붙인다. ``개인 -69 · 외국인 -1 · 기관 +14 · 기타법인 +56 [억]``
    은 무엇인지 딱 봐도 읽히고, 붙어 있던 ``반대편:`` 은 **틀린 이름이었다** —
    이 줄은 선택한 주체까지 포함해 넷을 다 적는데(위 예의 `기관 +14` 는 바로
    위 표에서 보던 그 값이다), "반대편" 은 선택 주체를 뺀 나머지를 뜻한다.
    라벨을 떼면 폭도 6칸 벌어 좁은 화면에서 `기타법인` 이 늦게 잘린다.

    잔여(= −Σ4주체)는 여기 안 적는다. 원장에는 그 열이 있지만, 다섯째 주체까지
    말하려면 설명이 붙어야 하고 그건 원장이 할 일이다.
    """
    for names in _ACTORS_TIERS:
        # 고른 주체가 떨어지면 지금 보는 값이 이 줄에 없게 된다 — 맨 뒤 자리를 내준다.
        if actor and actor not in names:
            names = (*names[:-1], actor)
        parts = []
        for ko in names:
            v = r.get(_ACTOR_KEY[ko])
            parts.append(f"{ko} {fmt_amt(v)}" if v is not None else f"{ko} —")
        # 앞의 공백 한 칸은 다른 패널 줄과 들여쓰기를 맞추는 것이다.
        line = " " + " · ".join(parts) + " [억]" + note
        if cell_len(line) + 1 <= width:
            return line
    # 꼬리표는 값이 하나라도 보이는 동안 안 뗀다 — 그건 장식이 아니라 **어느 창의
    # 값인가** 다. 반대로 값이 하나도 안 보이면 꼬리표도 뜻이 없다.
    return " —"


#: 전 종목 화면(`t`)의 열. 종목 목록과 달리 **섹터**를 붙이고, 두 층의 점수와
#: 그 곱을 함께 보인다 — 곱만 보이면 "섹터가 좋아서인가 종목이 좋아서인가" 를
#: 못 묻는다. 나머지는 종목 목록과 같은 열을 같은 순서로 쓴다.
_ALL_COLS = (
    Col("종목", 14, False, lambda t, st: clip(t.get("name", "—"), 14)),
    Col("섹터", 13, False, lambda t, st: t.get("sector", "—")),
    Col("곱", 5, True, lambda t, st: f"{t['both']:.2f}"),
    Col("섹터G", 9, True, lambda t, st: f"{t['gsec']:.2f}"),
    Col("종목선정", 9, True, lambda t, st: f"{t['pick']:.2f}"),
    Col("순매수[억]", 13, True, lambda t, st: fmt_amt(t.get("flow"))),
    Col(COL_INVTRT, 8, True, lambda t, st: fmt_amt(t.get("invtrt"))),
    Col(COL_PENFND, 10, True, lambda t, st: fmt_amt(t.get("penfnd"))),
    Col("상대수익[%p]", 12, True, lambda t, st: fmt_pct(t.get("rrel"), 1)),
    Col("최근집중[%]", 11, True, lambda t, st: fmt_conc(t.get("conc"))),
    Col("참여율[%]", 9, True, lambda t, st: fmt_pct(t.get("part"), 1)),
    Col("거래대금[억]", 13, True,
        lambda t, st: fmt_amt(t.get("tv")).replace("+", "")),
)


def all_cols() -> list[Col]:
    return list(_ALL_COLS)


def all_lines(st: State, width: int) -> tuple[list[str], int]:
    """전 종목 화면 — (행, 헤더 줄 수).

    섹터도 높고 종목도 높은 것을 국내 전체에서 한 번에 본다. 정렬은 곱 내림차순
    **고정**이다 — 이 화면의 존재 이유가 그 순서이고, 다른 축으로 줄세우고 싶으면
    섹터 표나 드릴다운으로 가면 된다. 정렬이 없다는 사실은 헤더가 적는다.
    """
    rows = st.all_picks()
    cols = _fit(all_cols(), width)
    # ⚠️ 종합에서는 **두 층이 다른 구간을 본다.** 섹터 점수는 섞은 창(20·60·120) 가운데
    # **관문을 통과한 창만**의 평균인데, 종목 점수는 그 섹터의 20일 목록에서 나온다(`State.names`).
    # 곱이 뜻을 가지려면 무엇과 무엇을 곱했는지가 화면에 있어야 한다 — 예전엔
    # `20일 기준` 이라고만 적어서, 섹터 쪽도 20일인 것처럼 읽혔다.
    if st.window == "종합":
        wins = "·".join(st.combined_windows())
        bases = [f"섹터 종합({wins}일) × 종목 20일 기준",
                 "섹터 종합 × 종목 20일",
                 "종합×20일"]
    else:
        bases = [f"{st.window}일 기준", f"{st.window}일", f"{st.window}일"]
    # 폭에 맞춰 **단계적으로** 줄인다. 한 줄 고정이면 좁은 화면에서 잘리는데,
    # 잘린 설명은 설명이 아니다 — "섹터 종합(20·60·120일) × 종목 20일" 이
    # "…섹터 종합(20·60" 으로 끝나면 곱한 대상을 오히려 잘못 읽힌다.
    tiers = tuple(f" 전 종목 · {len(rows)}개 · {tail}"
                  for tail in (f"양쪽 관문 통과 · {bases[0]} · 곱 내림차순 고정",
                               f"관문 통과 · {bases[1]} · 곱순",
                               f"{bases[2]} · 곱순",
                               bases[2]))
    title = tier_for(tiers, width)
    head = pad(" ".join(pad(c.header, c.width, c.right) for c in cols), width)
    out = [pad(title, width), head]
    for t in rows:
        out.append(_render(cols, t, st, width))
    if not rows:
        out.append(pad("  (양쪽 관문을 다 통과한 종목이 없다)", width))
    return out, 2


def names_lines(st: State, width: int) -> tuple[list[str], int]:
    """종목 목록 화면 — (행, 헤더 줄 수).

    ⚠️ 본문 행은 `st.names()` 와 **1:1** 이어야 한다. 80% 가로줄을 별도 행으로
    끼워 넣으면 화면 선택(`st.drow`)이 그 아래 전 종목에서 한 칸씩 어긋난다 —
    그래서 줄은 행 안의 1칸 마커 열로 긋는다.
    """
    r = st.selected()
    if not r:
        return [pad(" (섹터를 고르라)", width)], 1
    names = st.names()
    # 종합에서 Enter 를 누르면 나오는 목록은 **20일** 이다(`State.names`). 예전엔
    # 제목이 `종합일 기준` 이라고 적어서, 화면이 스스로 없는 구간을 말했다.
    win_note = ("20일 기준(종합)" if st.window == "종합" else f"{st.window}일 기준")
    title = (f" {r.get('sector','—')} · 종목 {len(names)}개 · {win_note}"
             f" · 정렬[{NAME_SORTS[st.nsi][1]}]")
    # 새 열 넷이 통째로 빌 때는 **왜** 비는지 화면이 말한다. 안 적으면 고장으로
    # 읽힌다. 긴 설명(다시 만드는 명령)은 `?` 에 있다 — 이 줄은 폭 80 에서
    # 안 잘려야 하므로 짧게.
    if st.legacy_names:
        note = " · 옛 리포트(새 열 없음)"
        if cell_len(title + note) + 1 <= width:
            title += note
    note = cap_pending_note(st.d, [st.market] if st.market != "전체" else st.markets[1:], r.get("sector"))
    if note and cell_len(title + note) + 1 <= width:
        title += note
    cols = fit_names(width, st.explore)
    head = pad(" ".join(pad(name_head(c.header, st), c.width, c.right) for c in cols), width)
    out = [pad(title, width), head]
    for t in names:
        out.append(_render(cols, t, st, width))
    if not names:
        out.append(pad("  (이 시장·섹터에 종목이 없다)", width))
    return out, 2


#: 색을 입힐 구간을 찾는 정규식. 렌더는 문자열만 만들고, 어디를 무슨 색으로
#: 칠할지는 이 함수가 (시작칸, 길이, 역할) 로 낸다 — curses 를 여기 들이지 않는다.
#:
#: 앞의 ``(?<![\d\w-])`` 는 **날짜를 음수로 읽지 않기 위한 것**이다. 헤더의
#: ``2026-07-31 ~ 2026-08-28`` 에서 `-07-31` `-08-28` 이 음수로 잡혀 하락색으로
#: 칠해졌다. 표의 숫자는 앞이 공백이거나 줄 처음이라 이 제한에 안 걸린다.
#: (배색을 검은 바탕으로 옮기면서 그 청록이 더 눈에 띄었다 — 값이 아닌 것이
#: 값처럼 보이는 건 배색 취향 문제가 아니다.)
_NUM = __import__("re").compile(r"(?<![\d\w-])[+-][\d,]+(?:\.\d+)?%?p?")


def color_spans(line: str) -> list[tuple[int, int, str]]:
    """한 줄에서 색칠할 구간 — [(시작 표시칸, 표시폭, 역할)].

    역할: ``up``(양수) · ``down``(음수) · ``mark``(통과 표시).
    문자 인덱스가 아니라 **표시 칸**을 낸다 — 한글이 2칸이라 curses 의 addstr
    좌표와 문자 인덱스가 다르다(이 저장소가 이미 밟은 함정).
    """
    spans = []
    # 문자 인덱스 → 표시 칸 매핑
    cell_at, cell = [], 0
    for ch in line:
        cell_at.append(cell)
        cell += cell_width(ch)
    cell_at.append(cell)
    for m in _NUM.finditer(line):
        role = "up" if line[m.start()] == "+" else "down"
        a, b = cell_at[m.start()], cell_at[m.end()]
        spans.append((a, b - a, role))
    for i, ch in enumerate(line):
        if ch == "*":
            spans.append((cell_at[i], 1, "mark"))
    return spans


#: 증거 수준 표지 — 열마다 "이 값의 앞날 관계를 검정했나, 결과가 무엇인가" 를 붙인다.
#: 근거: docs/validation/2026-11-08-results.md(Q3d 재검)·2026-11-08-usefulness-results.md.
B_MEAS = "[측정]"
B_NONE = "[앞날 증거 없음]"
B_G = "[판정 불가: G]"
B_X = "[≈섹터 20일 반전: x]"
B_NEG = "[음의 순위 정보·쓸 수 없음]"
BADGES = (B_MEAS, B_NONE, B_G, B_X, B_NEG)
#: 증거 표지가 말하는 **검정 범위** — (주체, 값의 종류)별로 검정한 창 길이(거래일). `share` = 순매수 ÷ 섹터 시총(가속),
#: `amt` = 순매수 금액(임펄스). 검정은 전부 **전체 시장**·10종목 미만 섹터 제외로 했다(docs/validation/):
#: 기관 가속 20일·기관 임펄스 20일 = Q3d 재검(results.md 주 판정), 외국인 20일·기관 60일 = ÷ 섹터 시총만(usefulness S2·S3).
#: **웹 뷰어(sector_flow.html `EV_TESTED`)·수치표와 같은 표**다 — 검사가 셋을 대조한다.
EV_TESTED = {"inst": {"share": (20, 60), "amt": (20,)}, "forgn": {"share": (20,)}}
#: x·G 는 기관 회귀(k·b)에서 나온 값 — 20일만 검정했다(Q3d).
EV_TESTED_SCORE = {"x": (20,), "G": (20,)}
#: 검정한 시장.
EV_MARKET = "전체"
#: 범위 밖 표지에 붙는 말 — 웹 두 장(수치표·뷰어)과 **같은 글자**다.
MARKET_NOTE = "전체 시장만 검정"
#: 섹터 투신+연기금 — 검정한 것은 ÷ 섹터 시총 20일(S1)이고 이 열은 금액이다.
EV_TRUST_NOTE = "÷시총 기준으로만 검정(S1)"


def scope_note(days) -> str:
    """창이 검정 범위 밖일 때의 말 — `앞날 검정은 20·60일만`(뷰어 `scopeNote` 와 같은 글자)."""
    return f"앞날 검정은 {'·'.join(str(d) for d in days)}일만"

#: 페이로드 키 → 증거 수준. **웹 두 장이 같은 표를 싣는다**(`const EV`) — 화면과 웹이 같은
#: 값에 다른 표지를 달면 둘 중 하나가 거짓말이다. 검사가 대조한다.
#:
#: 검정한 것: 섹터 가속·기관 임펄스·x·G (20일, 5·20일 지평), 섹터 투신+연기금·외국인 20일,
#: 기관 60일 — 전부 증거 없음(G 는 판정 불가). 개인·기타법인·파생값은 검정하지 않았다.
EVIDENCE = {
    "accel": B_NONE, "inst": B_NONE, "forgn": B_NONE, "trust": B_MEAS,
    "indiv": B_MEAS, "etc": B_MEAS,
    "x": B_X, "G": B_G,
    "exp": B_MEAS, "U": B_MEAS, "P": B_MEAS, "xdot": B_MEAS, "xddot": B_MEAS,
    "ret": B_MEAS, "pct": B_MEAS, "spark": B_MEAS, "tv": B_MEAS, "cap": B_MEAS,
    "n_all": B_MEAS,
    # 오늘(첫 화면·웹 '오늘' 패널) — 금액 근사 오차와 KRX 지수(정규장) 수익률은 잰 값이다.
    "gap": B_MEAS, "krx": B_MEAS,
    # 연속 순매매(1일 순매수 부호가 같은 날 수) — 센 값이다. 앞날 관계는 검정하지 않았다.
    "streak": B_MEAS,
}

#: 도움말 열 이름 → (증거 표지, 한 줄 근거). 섹터 표 절.
_EV_SECTOR = {
    "투신+연기금5일[억]": (B_MEAS, EV_TRUST_NOTE),
    "투신+연기금[억]": (B_MEAS, EV_TRUST_NOTE),
    "종목[수]": (B_MEAS, ""),
    "G": (B_G, "전체·20일만 검정(관문 통과 2~7개), 밖은 [측정]"),
    "임펄스[억]": (B_NONE, "기관 20일·전체 시장만 검정(Q3d), 밖은 [측정]"),
    "가속[%p]": (B_NONE, "전체·기관 20·60·외국인 20일만, 밖은 [측정]"),
    "1년[%ile]": (B_MEAS, ""),
    "추이[8]": (B_MEAS, ""),
    "수익률[%]": (B_MEAS, ""),
    "예상Δv": (B_MEAS, ""),
    "미실현[%p]": (B_X, "전체·20일만 검정 — 증거 없음, 밖은 [측정]"),
    "dW/dt[%p/일]": (B_MEAS, ""),
    "풀림가속[%p/일²]": (B_MEAS, "x 에서 나온 값"),
    "순매수상위[억]": (B_MEAS, ""),
    "순매도상위[억]": (B_MEAS, ""),
    "N일[G]": (B_G, ""),
    "통과[구간]": (B_G, ""),
}
#: 종목 층의 **검정 범위** — ``"주체|종류"`` → [검정한 창(거래일), 그 창의 표지 글자]. 창 목록이 비면 어느 창이든 그 글자다.
#: 근거 docs/validation/2026-11-08-usefulness-results.md: 종목 검정은 셋 다 **20일 순매수 ÷ 종목 시총**의 섹터 안 순위 IC 다 —
#: K1 투신+연기금(음으로 확인 → 쓸 수 없음), K2 기관(발견 통과, 확인 구간 미확인), K3 외국인(발견 탈락). 금액은 검정하지 않았고,
#: 투신·연기금 **개별** 값도 검정하지 않았다(K1 은 둘의 합). 종류: ``share`` = ÷ 종목 시총(시총대비), ``amt`` = 금액.
#: **뷰어(sector_flow.html `NAME_EV`)가 같은 표를 싣는다** — 검사가 글자째 대조하고(test_viewer_page), W 층이 창마다 그려 본다.
NAME_EV = {
    "invpen|share": [[20], B_NEG],
    "inst|share": [[20], B_NONE + "(K2 미확인)"],
    "forgn|share": [[20], B_NONE],
    "inst|amt": [[], B_MEAS + " · ÷시총 기준으로만 검정(K1/K2/K3)"],
    "forgn|amt": [[], B_MEAS + " · ÷시총 기준으로만 검정(K1/K2/K3)"],
    "invtrt|amt": [[], B_MEAS + " · 투신+연기금 합 ÷시총만 검정(K1)"],
    "penfnd|amt": [[], B_MEAS + " · 투신+연기금 합 ÷시총만 검정(K1)"],
}


def name_evidence(actor: str, kind: str, window) -> str:
    """종목 층 표지 글자 — :data:`NAME_EV` 하나로 TUI 드릴다운 헤더·도움말·뷰어(`evName`)가 같은 말을 한다.

    표에 없는 (주체, 종류)는 검정하지 않았으므로 ``[측정]``. 검정한 창 밖(1·5·60·120일)은 ``[측정] · 앞날 검정은 20일만``.
    시장은 보지 않는다 — K1~K3 은 섹터 **안** 순위라 거래소·코스닥 부분집합에도 같은 검정이다(K1 은 둘 다 음).
    """
    e = NAME_EV.get(f"{actor}|{kind}")
    if not e:
        return B_MEAS
    days, text = e
    if not days or (str(window).isdigit() and int(window) in days):
        return text
    return f"{B_MEAS} · {scope_note(days)}"


def name_badge(text: str) -> str:
    """표지 글자의 앞 표지(``[앞날 증거 없음](K2 미확인)`` → ``[앞날 증거 없음]``)."""
    return text[:text.index("]") + 1]


#: 종목 목록 열 → (주체, 종류). 주체 ``None`` 은 고른 주체(`State.actor`)다. 드릴다운 헤더가 이 열들에 짧은 표지를 단다.
NAME_EV_COLS = {"순매수[억]": (None, "amt"), COL_INVTRT: ("invtrt", "amt"), COL_PENFND: ("penfnd", "amt"),
                "시총대비[%p]": (None, "share")}


def names_window(st: "State") -> str:
    """종목 목록이 보는 창 — 종합에서는 20일(:meth:`State.names`)."""
    return "20" if st.window == "종합" else st.window


def name_head(header: str, st: "State") -> str:
    """드릴다운 헤더 글자 — :data:`NAME_EV_COLS` 의 열에는 **지금 창·주체의** 짧은 표지를 붙인다(``시총대비[%p][무]``).

    열 폭은 헤더 + 짧은 표지(4칸)가 들어가게 잡았다(순매수 14 · 투신 12 · 연기금 14 · 시총대비 16) — 모자라면 `pad` 가 표지를 자른다.
    """
    spec = NAME_EV_COLS.get(header)
    if not spec:
        return header
    actor, kind = spec
    return header + B_SHORT[name_badge(name_evidence(actor or st.actor, kind, names_window(st)))]


#: 종목 목록·전 종목 절 — 종목 층의 열은 섹터 표와 다른 표지를 단다. 범위가 창·주체로 갈리는 열은 :data:`NAME_EV` 의 말을 옮긴다.
_EV_NAMES = {
    "순매수[억]": (B_MEAS, "÷시총 기준으로만 검정(K1/K2/K3)"),
    "누적[%]": (B_MEAS, ""),
    "선정": (B_NEG, "투신+연기금 축이 검정 부호와 반대"),
    COL_INVTRT: (B_MEAS, "투신+연기금 합 ÷시총만 검정(K1)"),
    COL_PENFND: (B_MEAS, "투신+연기금 합 ÷시총만 검정(K1)"),
    "상대수익[%p]": (B_MEAS, ""),
    "최근집중[%]": (B_MEAS, ""),
    "참여율[%]": (B_MEAS, ""),
    "시총대비[%p]": (B_NONE, "기관(K2 미확인)·외국인 20일만, 밖은 [측정]"),
    "시총[억]": (B_MEAS, ""),
    "거래대금[억]": (B_MEAS, ""),
    "곱": (B_G, "섹터 G × 종목 선정점수"),
    "섹터G": (B_G, ""),
    "종목선정": (B_NEG, "투신+연기금 축이 검정 부호와 반대"),
}


#: 1일 창에서 값이 전부 `—` 인 열 — 표지를 달지 않는다(달 값이 없다).
ONE_DAY_BLANK = ("accel", "x", "xddot", "xdot", "P", "G", "exp", "U")
#: 증거 표지의 짧은 꼴 — 정렬 칩·좁은 자리.
B_SHORT = {B_MEAS: "[측]", B_NONE: "[무]", B_G: "[불가]", B_X: "[반전]", B_NEG: "[음]"}
#: 섹터 주체 열 — 그 값의 (주체, 종류)로 범위를 본다.
_EV_ACTOR_KEYS = ("inst", "forgn", "indiv", "etc")


def evidence_at(key: str, window: str, actor: str = "inst",
                market: str = EV_MARKET) -> "tuple[str, str] | None":
    """(증거 표지, 범위 말) — **값의 종류·주체·창·시장까지** 본다(뷰어 `evFor` 와 같은 규칙).

    ``key`` 는 :data:`EVIDENCE` 의 키 — ``accel`` 은 ``actor`` 의 가속(÷시총), 주체 키는 그 주체의 금액.
    검정한 (주체, 종류, 창)이고 전체 시장이면 표지 그대로, 창이 밖이면 [측정] + :func:`scope_note`,
    시장이 밖이면 [측정] + :data:`MARKET_NOTE`, 검정한 적 없는 (주체, 종류)는 [측정]. 1일 창의 빈 열은 ``None``.
    """
    if str(window) == ONE_DAY and key in ONE_DAY_BLANK:
        return None
    base = EVIDENCE.get(key, B_MEAS)
    if key == "trust":
        return B_MEAS, EV_TRUST_NOTE
    if key in EV_TESTED_SCORE:
        tested = EV_TESTED_SCORE[key]
    elif key == "accel" or key in _EV_ACTOR_KEYS:
        if base == B_MEAS:
            return B_MEAS, ""
        kind, who = ("share", actor) if key == "accel" else ("amt", key)
        tested = (EV_TESTED.get(who) or {}).get(kind)
        if not tested:
            return B_MEAS, ""
    else:
        return base, ""
    if not str(window).isdigit() or int(window) not in tested:
        return B_MEAS, scope_note(tested)
    if market != EV_MARKET:
        return B_MEAS, MARKET_NOTE
    return base, ""


#: 정렬키 → (EVIDENCE 키, 창을 고정하면 그 창). 주체를 따르는 열은 키 자리에 None.
_SORT_EV = {"accel": ("accel", None), "flow": (None, None), "pct": ("pct", None), "ret": ("ret", None),
            "trust5": ("trust", "5"), "trustw": ("trust", None), "x": ("x", None), "xddot": ("xddot", None),
            "P": ("P", None), "G": ("G", None), "tv": ("tv", None), "cap_idx": ("cap", None),
            "n_all": ("n_all", None)}
#: 기관 회귀(k·b)에서 나온 값 — 주체를 바꿔도 기관 기준이다.
_EV_INST_ONLY = ("x", "xddot", "P", "G")


def sort_evidence(st: "State", key: str) -> "tuple[str, str] | None":
    """섹터 표 정렬키의 (증거 표지, 근거) — 지금 창·주체·시장으로. 1일 창의 빈 열이면 ``None``."""
    ek, win = _SORT_EV.get(key, (key, None))
    actor = "invpen" if ek == "trust" else "inst" if ek in _EV_INST_ONLY else st.actor
    return evidence_at(ek or st.actor, win or st.window, actor, st.market)


def evidence_for(header: str, section: str = "sector") -> tuple[str, str] | None:
    """열 헤더의 (증거 표지, 근거). 섹터 표면 ``section="sector"``, 종목 쪽이면 ``"names"``."""
    table = _EV_NAMES if section == "names" else _EV_SECTOR
    return table.get(header)


def _with_evidence(entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """도움말 열 항목마다 바로 아래에 **증거 줄**을 끼운다(설명이 몇 줄이든 그 항목 끝에).

    표지를 설명 첫 줄 앞에 붙이면 폭 80 에서 줄이 넘친다 — 한 줄을 따로 쓴다.
    절(`── 종목 목록 ──`·`── 전 종목 ──`)에 따라 표를 바꾼다: `선정` 이 두 절에서 다른 열이다.
    """
    out: list[tuple[str, str]] = []
    section, pending = "sector", None

    def flush():
        if pending:
            badge, why = pending
            out.append(("", f"        증거 {badge}" + (f" — {why}" if why else "")))
    for name, desc in entries:
        if not name and desc.strip().startswith("──"):
            flush()
            pending = None
            if "종목 목록" in desc or "전 종목" in desc:
                section = "names"
            elif "섹터 표" in desc:
                section = "sector"
            else:
                section = "other"
            out.append((name, desc))
            continue
        if name:
            flush()
            pending = evidence_for(name, section) if section != "other" else None
        out.append((name, desc))
    flush()
    return out


#: 한영(두벌식) 상태의 키 한계 — **흐름·원장 도움말과 README 가 같은 글자**로 적는다(tests 가 이어 붙여 대조한다).
#: 근거 `flow_app.JAMO_TO_ASCII`: Shift 로 다른 자모가 나오는 자리는 q·w·e·r·t(ㅂㅈㄷㄱㅅ) 뿐이고, 그중 앱이 쓰는 대문자는 `W`(ㅉ) 하나다.
#: 세 곳이 따로 적던 시절 셋이 다 달랐고(`w·r 만`·`W 만`·`W(구간)뿐`) 흐름 쪽은 틀렸다 — `R` 은 듣는 키가 아니다.
IME_NOTE = ("한영 상태에서도 두벌식 같은 자리로 듣는다. Shift 로 다른 글자가 나오는 자리가 q·w·e·r·t 뿐이라 "
            "한글에서 대문자 역방향은 W(구간)뿐이다 — 나머지 대문자(M·A·S, 원장의 V)는 소문자 동작으로 떨어진다.")


#: 도움말 — 열의 뜻과 계산식. 화면에서 읽는 사람이 "이 숫자가 뭐냐" 를 물을 자리에
#: 답을 둔다. 한계도 같이 적는다(추정치·상대지표·유동성 함정).
HELP = [
    ("", "── 키 ──"),
    ("↑↓ j k", "한 줄 이동. g·Home 처음 · G·End 끝 · PgUp/PgDn 한 화면씩."),
    ("Enter l →", "그 섹터의 전 종목 보기(드릴다운)."),
    ("h ← Esc", "드릴다운에서 나가기. h 는 vim 의 '왼쪽' 이라 l 의 반대다."),
    ("w W", "구간 1·5·20·60·120(탐색 모드는 종합까지). 대문자는 역방향."),
    ("", "        1일은 하루의 사실만 — 가속·추이(탐색 열 포함)는 `—` 다."),
    ("", "        드릴다운에서도 듣는다."),
    ("1", "1일 창으로 바로(드릴다운이면 그날의 종목). 옛 리포트에는 없다."),
    ("o", "첫 화면(오늘의 수급)을 다시 연다. 길면 ↓↑ j k PgUp/PgDn 으로 훑고,"),
    ("", "        그 밖의 아무 키나 닫는다(q 는 종료, ? 는 도움말)."),
    ("m M", "시장 전체·거래소·코스닥. 대문자는 역방향."),
    ("a A", "주체 기관·외국인·개인·기타법인. 대문자는 역방향."),
    ("s S", "정렬 열 바꾸기. 대문자는 역방향. 드릴다운에서는 종목 정렬."),
    ("r", "정렬 역순 토글. ▼ 내림차순(큰 것 먼저) · ▲ 오름차순 —"),
    ("", "        **순매도 상위**(가장 많이 판 쪽)는 이걸 켜야 위로 온다."),
    ("", "        드릴다운 순매수는 기본이 절댓값 큰 순, 역순이 가장 많이 판 순이다."),
    ("e", "탐색 모드 켜기/끄기 — G·미실현·풀림가속·dW/dt·종목 선정·종합·"),
    ("", "        전 종목(곱). 기본 꺼짐. 검정 결과 앞날 증거가 없는(G 는 판정 불가)"),
    ("", "        값이라 기본 화면에서 뺐다. 켜 있는 동안 제목 줄 맨 앞에 [탐색 — …]"),
    ("", "        표지가 붙는다. 종목 선정의 투신+연기금 축은 검정 부호와 **반대**다."),
    ("t", "탐색 모드에서만 — 전 종목 화면(전 섹터를 곱 순으로 한 화면에)."),
    ("", "        섹터 표·드릴다운에서 열리고, t·h·←·Esc 로 섹터 표로 돌아온다."),
    ("? F1", "이 도움말. 안에서 ↑↓·PgUp/PgDn·Space·g·G·Home·End 로 훑고,"),
    ("", "        q·Esc·?·Enter 로 닫는다."),
    ("q", "종료. 단 도움말 안에서는 **닫기만** 한다(한 번 더 눌러야 종료)."),
    # IME_NOTE 를 그대로 나눠 적는다(검사가 이어 붙여 대조한다 — 원장 도움말·README 와 같은 글자).
    ("", "한영 상태에서도 두벌식 같은 자리로 듣는다. Shift 로 다른 글자가 나오는"),
    ("", "        자리가 q·w·e·r·t 뿐이라 한글에서 대문자 역방향은 W(구간)뿐이다 —"),
    ("", "        나머지 대문자(M·A·S, 원장의 V)는 소문자 동작으로 떨어진다."),
    ("", ""),
    ("", "── 증거 수준 — 열마다 '증거' 줄이 붙는다 ──"),
    ("", "앞날 수익률(섹터·종목)과의 관계를 사전등록해 검정한 결과다(docs/validation/)."),
    ("", "정렬 칩·힌트바의 짧은 꼴: [측]=측정 [무]=앞날 증거 없음 [반전]=x"),
    ("", "        [불가]=G 판정 불가 [음]=음의 순위 정보."),
    ("", "검정한 것: 가속(순매수÷섹터 시총) 기관 20·60일·외국인 20일, 기관 임펄스 20일,"),
    ("", "        x·G 20일 — 전부 전체 시장(10종목 미만 제외). 그 밖의 창은 같은 열"),
    ("", "        이라도 [측정] · 앞날 검정은 N일만, 거래소/코스닥은"),
    ("", "        [측정] · 전체 시장만 검정."),
    ("", "        투신+연기금은 금액이라 [측정] · ÷시총 기준으로만 검정(S1)."),
    ("", "        1일 창에서 값이 없는 열(가속 등)에는 표지를 달지 않는다."),
    ("", "종목 층(드릴다운 헤더의 짧은 꼴, 창·주체마다 다시 단다): 검정한 것은"),
    ("", "        20일 순매수 ÷ 종목 시총의 섹터 안 순위뿐 — 투신+연기금 합(K1)"),
    ("", "        [음], 기관(K2 미확인)·외국인(K3 탈락) [무]. 그 밖의 창의 시총대비는"),
    ("", "        [측정] · 앞날 검정은 20일만, 금액은 [측정] · ÷시총 기준으로만"),
    ("", "        검정, 투신·연기금 개별 열은 [측정] · 투신+연기금 합 ÷시총만 검정."),
    (B_MEAS, "지금·지난 구간을 잰 값. 앞날을 주장하지 않는다(앞날 검정 안 함)."),
    (B_NONE, "검정함 — 증거 없음. 2018~2026, 섹터 앞날 5·20일 순위 IC."),
    (B_G, "검정함 — 판정 불가. 관문 통과 섹터가 날마다 2~7개라 검정력이 없다."),
    (B_X, "x 는 사실상 섹터 20일 수익률 반전이다(ρ 0.82~0.93)."),
    ("", "        앞날 관계도 검정함 — 증거 없음(20일 지평은 경계)."),
    (B_NEG, "종목 투신+연기금 합 20일÷시총의 섹터 안 순위 IC 가 음으로 확인됐다."),
    ("", "        그러나 매수 상위 회피는 확인 구간에서 역효과였다 — 쓸 수 없다."),
    ("", "        드릴다운에 그 합의 열은 없다 — 탐색 모드 선정점수의 한 축이다."),
    ("", "        화면의 값은 앞날 예측이 아니라 누가 무엇을 사고파나의 관측이다."),
    ("", ""),
    ("", "── 오늘 — 첫 화면(오늘의 수급)과 1일 창 ──"),
    ("", "첫 화면은 기준일 **하루**의 사실이다. 웹 수치표 맨 위 '오늘' 패널과 같은 말이다."),
    ("상태 칩", "마감·잠정 = 17시 수집 창 뒤 그날 **잠정값**(수집 시각 스냅숏)."),
    ("", "        다음날 아침 확정치(daily_finalize)를 확인해 다시 만들면 마감·확정."),
    ("", "        장중·미확정 = 17시 전이라 부분 데이터일 수 있다."),
    ("수익률", "확정 리포트의 수익률은 **전일 20:00 → 당일 20:00** 이다(애프터마켓 포함"),
    ("", "        통합 종가, 2026-09-14 부터). 잠정 리포트의 마지막 날은 **전일 20:00 →"),
    ("", "        당일 수집 시각**(애프터마켓 진행 중)의 스냅숏이다 — 확정 때 바뀐다."),
    ("", "        KRX 지수는 정규장(15:30) 값이라 같은 날 크게 갈릴 수 있다."),
    ("", "        첫 화면은 둘을 나란히 적는다: 자체 바구니 | KRX 지수(정규장)."),
    ("오늘%ile", "그날 순매수가 오늘 포함 최근 260거래일 **일별** 값 중 몇 등 [0~100]."),
    ("", "        동률은 중간순위. 0(또는 미보고)인 날이 75% 를 넘는 줄은 `—` 다."),
    ("", "        첫 화면은 기관·투신+연기금의 상위·하위 3(금액 순)과 ≥95·≤5 섹터."),
    ("", "        표시는 반올림(0.5 는 위로) — 웹 수치표와 같은 규칙이다."),
    ("최대순매매", "그날 시장 순매수 |금액| 이 가장 큰 주체(최대 순매매 주체)."),
    ("", "        기관·투신+연기금이 아니면"),
    ("", "        그 주체(외국인·개인·기타법인)의 상위·하위 3 과 극단을 덧붙인다."),
    ("종목", "섹터 값 옆 [ ] 는 그 주체의 그날 순매수 상위(하위 줄은 순매도 상위)"),
    ("", "        종목 1~2개다. 1 로 1일 창, Enter 로 그 섹터의 그날 전 종목."),
    ("잠정 수정폭", "잠정 리포트 첫 화면에 최근 N개 확정일의 잠정→확정 수정 폭을 적는다:"),
    ("", "        1일 섹터 칸 |확정−잠정|÷|확정| 의 중앙값과 부호 뒤집힘 수. 칸은"),
    ("", "        (시장, 주체) 안에서 Σ|확정값| 의 2% 이상인 것만(0 근처 칸 제외)."),
    ("", "        상위3 유지 = (시장, 주체)마다 잠정 상위 3 중 확정 상위 3 에도"),
    ("", "        든 몫. 확정 기록이 없는 날은 시드(아카이브 18:10 게시본 대"),
    ("", "        확정 재구성)로 세고 그렇게 적는다."),
    ("", "        주도주 중앙 = 종목 층(주도주 = 섹터 1일 |기관| 누적 80%)의 1일"),
    ("", "        |확정−잠정|÷|확정| 일별 중앙값들의 중앙값, 종목 상위3 유지 ="),
    ("", "        (시장, 섹터, 주체)마다 잠정 상위 3 종목이 확정에도 든 몫."),
    ("", "        잠정 리포트의 %ile 극단 목록에는 (잠정) 표지가 붙는다."),
    ("연속순매매", "1일 순매수 부호가 오늘까지 같은 날 수와 그 누적, 그 길이의 1년%ile"),
    ("", "        (최근 260일 날마다의 연속 길이 분포 안 순위, 부호 무관). 그날 |값| 이"),
    ("", "        (시장, 주체) 섹터 목록 Σ|값| 의 1% 미만이면 ≈0 날 — 연속을 끊는다."),
    ("", "        시장 합계 4주체, 그리고 기관·투신+연기금·최대 순매매 주체의 최장 섹터"),
    ("", "        (10종목 미만 섹터·(미분류) 제외). 센 값이다 — 앞날을 말하지 않는다."),
    ("직전 대비", "직전 거래일 리포트가 **게시한 그대로의** 20일 누적 순매수 순위와 견준"),
    ("", "        상위 5 섹터의 들고 남(기관·투신+연기금). 20일 이동 창이라 오늘"),
    ("", "        하루가 아니라 들어온 날·빠진 날이 같이 움직인다. 직전이 잠정이었거나"),
    ("", "        검증에 걸렸으면 제목에 적는다. 없으면 이유를 적는다."),
    ("부호 반전", "최근 5일 순매수 부호가 직전 15일과 반대인 섹터 — 최근 5일 |금액| 이"),
    ("", "        목록 Σ|5일| 의 2% 미만인 섹터는 뺀다(작은 칸의 부호는 잡음이다)."),
    ("금액 오차", "순매수 금액은 수량 × 종가 근사다 — 네이버·KRX 가 주는 주체별 순매수"),
    ("", "        금액(체결가 합)과 이 화면 값이 다른 이유가 이것이다. 시장마다 그날"),
    ("", "        Σ|4주체 수량|×종가와 Σ|수량|×VWAP(거래대금÷거래량)의 차이가 5% 를"),
    ("", "        넘으면 '금액 근사 오차 큼' 칩이 뜬다. 시장 합계는 종목 간 오차가"),
    ("", "        상쇄되지만 섹터 칸은 덜 상쇄된다 — 칩 옆에 섹터 최대 오차를 적는다."),
    ("공식 대조", "(선택) KR_QUANT_KRX_TRUTH=1 로 만든 리포트는 KRX 정보데이터시스템이"),
    ("", "        공표한 투자자별 **거래대금**(순매수)에 시장 1일 금액을 대조해 근사"),
    ("", "        오차를 잰다 — 측정값은 이 도움말 맨 끝 절. 내부 검증용으로만 받고"),
    ("", "        기준일당 한 번 재운다(krx_truth.json) — 재배포하지 않는다."),
    ("(미분류)", "벤더 마스터에 섹터가 빈 종목. 은행 넷(기업은행·카카오뱅크·케이뱅크·"),
    ("", "        제주은행)은 KRX 업종(금융)으로 덮었다(sectorflow/sector_override.py)."),
    ("", "        남은 종목 수는 첫 화면이 적는다."),
    ("잠정→확정", "확정 리포트에서 바꿔 끼운 잠정 리포트 대비 1·5일 순매수가 반올림"),
    ("", "        (±0.05억)을 넘게 바뀐 칸 수 — 첫 화면에 큰 칸 셋과 %ile 극단 목록의"),
    ("", "        들고 남, 전체 목록은 이 도움말 맨 끝."),
    ("", ""),
    ("", "── 섹터 표 ──"),
    ("섹터", "벤더 분류(stocks.sector) 27개 — KRX 업종 분류와 다르다."),
    ("", "        마스터가 섹터를 비운 은행 넷만 KRX 업종(금융)으로 보정한다"),
    ("", "        (sectorflow/sector_override.py)."),
    ("투신+연기금5일[억]", "그 섹터의 **최근 5일** 투신+연기금 순매수 합. 표의 `임펄스` 는"),
    ("", "        현재 구간 누적이라 \"아직도 들어오는가\" 를 말하지 않는다 — 이 열이"),
    ("", "        그 답이다. 5일은 최단창이라 다른 구간과 겹치지 않는다."),
    ("", "        ⚠️ 최근 유출이 눌림목인지 추세전환인지는 **여기서 안 갈린다**"),
    ("", "        (그건 가격 구조를 봐야 하는데 이 리포트에는 구간 수익률뿐이다)."),
    ("투신+연기금[억]", "그 섹터의 **이 창** 투신+연기금 순매수 합(종목 값의 합)."),
    ("", "        5일 열과 나란히 — 창 전체로는 사는데 최근엔 파는지가 보인다."),
    ("종목[수]", "그 (시장,섹터)에서 **이 구간에 거래된** 종목 수 — Enter 로 여는 목록의"),
    ("", "        길이와 정확히 같다. '상장 종목 수' 가 아니다: 벤더 마스터에는"),
    ("", "        수급 보고가 두 달 전에 끊긴 이름이 남아 있어서, 그걸 세면 표가"),
    ("", "        387 이라 하고 목록이 386 개인 일이 생긴다(2026-08-28 리포트 실측)."),
    ("", "        10개 미만은 **~ 마커**와 회색 — 벤더 분류가 좁아 사실상 단일종목인"),
    ("", "        라벨이 있다(부동산 3, 출판/매체복제 2). 얇은 섹터는 G 순위에서 빼고"),
    ("", "        정렬과 무관하게 표 맨 아래로 보낸다 — 분모가 작아 가속이 쉽게 튄다."),
    ("", "        색이 없는 터미널에서도 남도록 마커를 글자로 둔다(섹터 이름 옆 칸)."),
    ("G", "탐색 모드(e)에서만. **관문을 통과한 것들 사이**의 순위 평균 0~1."),
    ("", "        섹터 표는 27개 섹터 사이, 종목 목록은 그 섹터 안 종목 사이다."),
    ("", "        통과 섹터가 하나뿐이면 G = 0.5 다(종목 선정은 그때 점수가 없다)."),
    ("", "        섹터 표의 관문: 얇지 않고(`~` 아님) **가속>0 · 미실현>0 · 풀림가속>0**."),
    ("", "        하나라도 어기면 점수가 없다(`—`). `*` 는 점수가 있는 줄과 정확히"),
    ("", "        같은 통과 표시다(탐색 모드에서만 찍힌다)."),
    ("", "        점수 = rank(가속)·rank(미실현)·rank(풀림가속) 평균. 동률은 중간순위."),
    ("", "        순위를 쓰는 이유: 가속은 %p(0~5), 미실현은 %p(±50), 풀림가속은 1e-2"),
    ("", "        스케일이라 그대로 더하면 미실현이 전부를 지배한다."),
    ("", "        * 는 세 조건을 다 만족(a>0 · x>0 · ẍ>0). 앞날 검정 — 판정 불가."),
    ("임펄스[억]", "구간 누적 순매수 [억원] = Σ(순매매 수량 × 그날 종가)."),
    ("", "        DB 는 수량만 주므로 금액은 종가 환산 근사다(참값은 VWAP 가중)."),
    ("가속[%p]", "임펄스 ÷ 구간말 섹터 시총 × 100 [%p]. 물리로 a = F/m."),
    ("", "        금액만 보면 대형 섹터가 늘 이기므로 쏠림은 이쪽이 정직하다."),
    ("1년[%ile]", "그 주체의 롤링 N일 순매수 합, **최근 1년(260거래일) 분포에서 몇 등**인가"),
    ("", "        (1일 창은 그날 값의 일별 분포 — 첫 화면의 오늘%ile 과 같다)."),
    ("", "        [0~100]. 100=1년 최대 매수 · 0=최대 매도 · 50=평범. 동률은 중간순위."),
    ("", "        z 를 안 쓴다 — 구간이 겹쳐 자기상관이 크고 260일에 비겹침 20일 구간은"),
    ("", "        13개뿐이라 z 는 정밀도를 과장한다. 이 표에서 **유일한 시계열 맥락**이다"),
    ("", "        (나머지 열은 전부 27개 섹터 사이의 상대순위다)."),
    ("추이[8]", "구간 동안 순매수가 **누적된 경로**. 왼쪽이 구간 시작, 오른쪽이 끝이다."),
    ("", "        오른쪽으로 **올라가면 계속 들어오는 중** · 내려가면 빠져나가는 중 ·"),
    ("", "        평평하면 멈췄다. 임펄스가 같아도 앞에서 다 들어온 것과 지금"),
    ("", "        들어오는 것은 다른 이야기인데, 숫자 하나로는 그게 안 보인다."),
    ("", "        높이는 **그 행 안에서** 경로의 최저~최고를 4단계(⣀⣤⣶⣿)에 편 것이라"),
    ("", "        행끼리 비교되지 않는다 — 크기는 임펄스 열이 말한다."),
    ("", "        짧은 구간(5일)은 조각이 모자라 왼쪽이 빈다(⠀)."),
    ("수익률[%]", "그 섹터 **자체 바구니**의 구간 수익률 [%], 전일 시총 가중."),
    ("", "        종가는 애프터마켓 포함 통합값 — 하루는 전일 20:00→당일 20:00 다."),
    ("", "        KRX 업종지수가 아니다 — 구성종목이 달라 분자·분모가 어긋난다."),
    ("예상Δv", "k × 가속 + b. k·b 는 10종목 이상 섹터의 횡단면 회귀(절편 포함 OLS)."),
    ("미실현[%p]", "탐색 모드(e)에서만. 예상Δv − 실제 수익률 [%p]. + 는 적합선 대비"),
    ("", "        덜 오른 폭이다"),
    ("", "        (섹터 20일 반전과 ρ 0.82~0.93), − 는 더 오른 폭."),
    ("", "        OLS 잔차의 부호 반전이라 적합한 섹터의 합이 0 이다 — **상대** 지표."),
    ("dW/dt[%p/일]", "탐색 모드(e)에서만. 구간을 반으로 갈라 본 W=가속×수익률 의 전·후반"),
    ("", "        차 ÷ 거래일. 열은 폭이 모자라면 **경계에서** 통째로 빠진다."),
    ("풀림가속[%p/일²]", "탐색 모드(e)에서만. 미실현 x 가 해소되는 **가속**(ẍ). 구간을 셋으로"),
    ("", "        갈라 중앙차분한다. 2차 차분이라 짧은 구간(조각 6~7일)에서 흔들린다."),
    ("", "        포텐셜(½kx²)은 |미실현| 의 단조 변환이라 열을 지웠다."),
    ("순매수상위[억]", "그 섹터에서 **선택 주체**가 가장 많이 산 종목과 금액[억원]."),
    ("순매도상위[억]", "**가장 많이 판** 종목. 둘은 같은 목록(금액순)의 위/아래 끝이다."),
    ("", "        표는 1개씩, 하단 패널은 3개씩 — 같은 목록이고 시총과 무관하다."),
    ("", "        순매도상위는 화면이 더 넓어야 보인다. Enter 로 전 종목."),
    ("N일[G]", "종합 화면에서 그 구간의 G. 20일G·60일G·120일G 로 뜬다."),
    ("통과[구간]", "세 조건(a>0·x>0·ẍ>0)을 만족한 구간 수 / 전체 구간 수. 예 2/3."),
    ("", "        종합 G 는 **통과한 구간만의 평균**이라 1/3 이 2/3 보다 위에 올 수 있다."),
    ("", ""),
    ("", "── 종목 목록 (Enter) ──"),
    ("순매수[억]", "그 구간 **선택된 주체**의 순매수 [억원]. 기본 정렬은 이것의 **절댓값**"),
    ("", "        순 — 많이 산 종목과 많이 판 종목이 같이 위로 온다."),
    ("누적[%]", "|순매수| 큰 순으로 훑을 때의 **누적 기여율**. '-' 마커가 80% 지점이다 —"),
    ("", "        그 위가 이 섹터를 움직인 종목이고 아래는 사실상 0 이다."),
    ("", "        순매수 정렬에서만 채운다 — 시총·이름 순의 누적은 뜻이 없다."),
    ("선정", "탐색 모드에서만. **이 섹터 안 종목들** 사이의 순위 평균 0~1."),
    ("", "        섹터 표의 G 와 다른 층이다"),
    ("", "        (G 는 27개 섹터 사이, 이것은 그 섹터 안 종목 사이)."),
    ("", "        먼저 **관문** — 하나라도 어기면 점수가 없다(`—`):"),
    ("", "          · 누적 80% 안 · 순매수 > 0"),
    ("", "          · 최근집중·상대수익·참여율이 다 있어야 한다 — 그래서 5일 창"),
    ("", "            (최근집중이 늘 비는 창)에는 선정이 하나도 없다."),
    ("", "          · 후보가 둘 이상이어야 한다 — 하나뿐이면 점수가 없다(G 와 다르다)."),
    ("", "          · 투신 ≥ 0 **그리고** 연기금 ≥ 0 — 기관은 6개 세부의 합이라"),
    ("", "            금투(헤지·차익)가 만든 숫자일 수 있다. 예(2026-08): 대한항공 20일"),
    ("", "            기관 +692억인데 투신 −144 다."),
    ("", "          · 거래대금 ≥ 100억 — 참여율은 비율이라 혼자서는 체결 가능성을"),
    ("", "            못 답한다 — 참여율 11~14% 대에 거래대금 3억과"),
    ("", "            43,543억이 같이 있다."),
    ("", "        점수 = 네 순위의 평균, 각 순위는 pos/(N−1):"),
    ("", "          rank(투신+연기금) · rank(최근집중) · rank(−상대수익) · rank(참여율)"),
    ("", "        **상대수익만 부호를 뒤집는다** — 섹터보다 덜 오른 종목이 위로 간다."),
    ("", "        순위를 쓰는 이유는 G 와 같다: 억원·%·%p 를 그대로 더하면 단위가 큰"),
    ("", "        축이 전부를 지배한다."),
    ("", "        ⚠️ **탐색 점수다** — 탐색 모드(e)에서만 보인다. 점수 자체는 앞날"),
    ("", "        검정을 안 했고, rank(투신+연기금) 축은 검정 결과와 **부호가 반대**다"),
    ("", "        (섹터 안 순위 IC 음 — 많이 산 쪽을 위로 올리는 축이다)."),
    (COL_INVTRT, "그 구간 **투신**(자산운용) 순매수 [억원]."),
    (COL_PENFND, "그 구간 **연기금**(국민연금 등) 순매수 [억원]."),
    ("", "        왜 따로 보나: 기관 총액이 같아도 이 둘이 채운 것과 금투(증권사"),
    ("", "        자기매매 — 헤지·차익이 섞여 방향성이 약하다)가 채운 것은 다른"),
    ("", "        이야기인데, `기관` 한 덩어리에는 그 구분이 없다. 예(2026-08, 20일):"),
    ("", "        SK하이닉스 기관 −13,157억인데 투신 +1,549 · 연기금 +4,614 ·"),
    ("", "        금투 −22,611 이다 — 총액만 보면 '판다' 지만 이 둘은 사고 있었다."),
    ("", "        **선택 주체와 무관하게** 늘 기관 세부다(헤더가 제 이름을 단다)."),
    ("상대수익[%p]", "종목 구간수익률 − **섹터** 구간수익률 [%p]. 섹터는 갔는데 이건"),
    ("", "        안 갔나. 기준선은 표의 `수익률[%]` 열과 **같은 값**이다."),
    ("", "        섹터의 미실현(x)을 종목에 내리지 않는다 — 그 k·b 는 27개 섹터"),
    ("", "        횡단면에서 적합된 것이라 2,645종목 위에서는 적합된 적이 없다."),
    ("", "        뺄셈은 회귀 없이 같은 질문에 답한다. 더 정직하고 더 간단하다."),
    ("최근집중[%]", "5일 순매수 ÷ **이 창**의 순매수 × 100 [%]. 지속성이다."),
    ("", "        20일 화면에서 ≈25 면 고르게 분산 · ≈100 이면 20일치가"),
    ("", "        최근 5일에 몰림 · >100 이면 앞에서는"),
    ("", "        팔다 최근에 방향을 튼 것 · **음수면** 최근 5일이 구간 전체와"),
    ("", "        반대 방향이다. 분모가 1억 미만이면 비율이 폭발해 비운다."),
    ("", "        가장 짧은 창(5일)에서는 분자=분모라 늘 100 이므로 역시 비운다."),
    ("", "        그 종목 |순매수| 가 목록 Σ|순매수| 의 0.5% 미만이어도 `—` 다 —"),
    ("", "        섹터를 움직이지 않은 작은 줄의 큰 비율(+3억에 −287%)을 지운다."),
    ("", "        섹터 표의 `추이[8]` 가 하는 일을 숫자 하나로 대신한다."),
    ("참여율[%]", "순매수 ÷ 그 종목 거래대금 × 100 [%]. '얼마나 붐볐나' 가 아니라"),
    ("", "        '그 거래의 몇 %가 한 방향이었나' 다. 시총대비와 달리 분모가"),
    ("", "        유동성이라 **살 수 있는 종목인가** 를 같이 말해준다."),
    ("시총대비[%p]", "순매수 ÷ 그 종목 시총 × 100 [%p]. 시총 작은 스팩이 위로 올라온다."),
    ("시총[억]", "그 종목의 구간말 시가총액 [억원]. 시총대비의 분모다."),
    ("", "        칸의 ! = 권리락이 지났는데 신주가 아직 상장 전. 가격만 내려가 시총이"),
    ("", "        작게, 시총대비가 크게(최대 약 2배) 나온다. 신주가 들어오면 사라진다."),
    ("거래대금[억]", "그 구간 거래대금 [억원]."),
    ("", ""),
    ("", "── 전 종목 화면 (t, 탐색 모드에서만) ──"),
    ("곱", "`섹터G × 종목선정`. **양쪽 관문을 다 통과한 종목**만 뜬다 —"),
    ("", "        어느 한쪽이라도 점수가 없으면 곱도 없다."),
    ("", "        **왜 곱인가**: 각 점수 **안에서는** 축 하나가 낮아도 나머지가"),
    ("", "        메우는 것이 맞다(그래서 평균이다). 층 **사이에서는** 아니다 —"),
    ("", "        평균이면 아무도 안 사는 섹터의 1등이 모두가 사는 섹터의 3등을"),
    ("", "        이긴다. 곱은 둘 다 높을 때만 남는다(0.2×0.9=0.18 < 0.6×0.7=0.42)."),
    ("섹터G", "그 종목이 속한 섹터의 G. 27개 섹터 사이의 순위다."),
    ("종목선정", "그 섹터 **안에서** 그 종목의 선정점수."),
    ("", "        둘을 같이 보이는 이유: 곱만 보이면 '섹터가 좋아서인가 종목이"),
    ("", "        좋아서인가' 를 못 묻는다."),
    ("", "        이 화면은 정렬이 없다 — 곱 내림차순 고정이다."),
    ("", ""),
    ("", "── 알아둘 것 ──"),
    ("", "· k 는 추정치다. 구간마다·날마다 다르다 — 지금 블록의 값은 탐색 모드(e)"),
    ("", "  헤더 줄의 k=…(t=…) 다. 도움말에 숫자로 적어 두면 다음 리포트에서 틀린"),
    ("", "  말이 된다. 동시기 적합이라 [측정] 이고 |t|<2 인 날이 42~70% 다(Q3c)."),
    ("", "· 위 열들은 **동시기**(같은 구간 안)의 값이다. 앞날 수익률과의 관계는"),
    ("", "  검정함 — 섹터 지표는 증거 없음, G 는 판정 불가(docs/validation/)."),
    ("", "· 값 0 은 '관망' 이 아니라 '0 또는 미보고' 다 — 수집기가 파싱 실패를"),
    ("", "  0 으로 준다."),
    ("", "· 코스닥 단독은 관계가 약하다(R² 0.00~0.25). 거래소가 전체를 끌고 간다."),
    ("", "· **투신·연기금·개인은 키움 수집분에만 있다.** 그래서 리포트는 키움"),
    ("", "  수집분만 쓰고, 상장폐지된 종목이 표본에서 빠진 채(생존편향) 만든다."),
    ("", "  관측용 화면이라 심사 기준을 적용할 자리는 아니지만, 여기 적힌 숫자로"),
    ("", "  백테스트를 대신하면 안 된다(검정 문서도 같은 한계를 적는다 —"),
    ("", "  docs/validation/)."),
    ("", "· 종목 목록 제목에 **옛 리포트(새 열 없음)** 가 뜨면 투신·연기금·상대수익이"),
    ("", "  통째로 `—` 다. 화면 고장이 아니라 그 리포트가 이 열들이 생기기 전에"),
    ("", "  만들어진 것이다. 일일 배치는 같은 날짜 폴더가 있으면 건너뛰므로 데이터가"),
    ("", "  안 바뀌면 저절로 채워지지 않는다. 다시 만들려면:"),
    ("", "     KR_QUANT_FORCE=1 scripts/daily_report.sh      (약 6분)"),
    ("", "  옆에 지어 마지막에 바꿔 끼우므로, 실패하면 옛 리포트가 그대로 남는다."),
]
HELP = _with_evidence(HELP) + BASIS_HELP


#: 도움말 제목 — 넓은 것부터. 폭에 안 들어가면 다음 단계로 내려간다.
HELP_TITLE_TIERS = (
    "키와 열의 뜻 — ↑↓/PgDn 스크롤 · q·Esc·?·Enter 로 닫기 (종료는 닫은 뒤 q 를 한 번 더)",
    "키와 열의 뜻 — ↑↓ 스크롤 · q 로 닫기(종료는 한 번 더)",
    "키와 열의 뜻 — q 로 닫기(종료는 한 번 더)",
    "q 로 닫기(종료는 한 번 더)",
    "q 닫기",
)


def help_desc(entries: list[tuple[str, str]], name: str) -> str:
    """도움말 목록에서 항목 하나의 설명. 없으면 빈 문자열.

    ``**강조**`` 는 소스 표기라 여기서 뗀다 — 한 줄 힌트로 쓰는 쪽은 별표를
    통과 마커(``*``)와 헷갈리기만 한다.
    """
    for n, desc in entries:
        if n == name:
            return desc.strip().replace("**", "")
    return ""


#: 힌트바 전용 **짧은 설명** — 열 이름 → 한 줄. 없으면 :data:`HELP` 로 떨어진다.
#:
#: 왜 두 벌인가: 같은 문장을 길이 제약이 **정반대**인 두 자리에 쓰고 있었다.
#: 도움말은 86줄 모달이라 비유·유래·주의사항이 다 들어가도 되지만, 힌트바는
#: 한 줄이라 그 뒤가 `…` 로 잘려나간다 — 그리고 잘린 설명은 설명이 아니다
#: (:func:`hint_line` 독스트링이 스스로 인정하는 문제였다).
#:
#: 실제로 가속 정렬에서 화면에 늘 떠 있던 줄이 이랬다:
#: ``정렬 가속[%p]▼ · 임펄스 ÷ 구간말 섹터 시총 × 100 [%p]. 물리로 a = F/m.``
#: 값을 읽는 데 필요한 것은 "임펄스 ÷ 시총" 까지고, 물리 비유는 한 줄짜리
#: 힌트바에서 아무 일도 안 한다. 게다가 정확하지도 않다 — 여기 `a` 는 자금
#: 축의 0차 비율(F/m)이고 `ẍ`(풀림)는 가격 갭의 2차 시간미분인데, 둘 다
#: "가속" 이라 불러서 읽는 사람을 헷갈리게 했다. 비유는 도움말에 남는다.
#:
#: 규칙: **그 숫자를 읽는 데 필요한 것만** — 분자·분모와 단위까지. 비유·유래·
#: 한계는 `?` 의 일이다. 길이는 폭 80 에서 `…` 로 잘리지 않는 선을 지킨다
#: (검사가 전 열 × 폭 80 을 돌며 확인한다).
HINT_DESC = {
    # 섹터 표
    "가속[%p]": "임펄스 ÷ 구간말 섹터 시총 × 100 [%p]",
    "임펄스[억]": "구간 누적 순매수 [억원], 수량×종가",
    "1년[%ile]": "이 순매수가 최근 1년 분포에서 몇 등 [0~100]",
    "추이[8]": "구간 동안 순매수가 누적된 경로. 왼쪽이 시작",
    "수익률[%]": "섹터 바구니 구간 수익률 [%], ~20시 통합종가",
    "미실현[%p]": "예상Δv − 실제 수익률 [%p]. + 면 덜 올랐다",
    "풀림가속[%p/일²]": "미실현이 해소되는 2차 변화 ẍ [%p/일²]",
    "G": "관문 통과 섹터끼리의 순위 평균 [0~1]",
    "선정": "관문 통과 종목끼리의 순위 평균 [0~1]",
    "dW/dt[%p/일]": "전·후반 W=가속×수익률 의 변화 [%p/일]",
    "투신+연기금5일[억]": "최근 5일 투신+연기금 순매수 합 [억원]",
    "투신+연기금[억]": "이 창의 투신+연기금 순매수 합 [억원]",
    "종목[수]": "이 구간에 거래된 종목 수. ~ 는 10개 미만",
    # 종목 목록(드릴다운)
    "종목": "종목명 가나다순",
    "순매수[억]": "선택 주체의 구간 순매수 [억원], 절댓값 순",
    "누적[%]": "|순매수| 큰 순으로 훑을 때의 누적 기여율. - 가 80%",
    COL_INVTRT: "투신(자산운용)의 구간 순매수 [억원]",
    COL_PENFND: "연기금(국민연금 등)의 구간 순매수 [억원]",
    "상대수익[%p]": "종목 구간수익률 − 섹터 구간수익률 [%p]",
    "최근집중[%]": "5일 순매수 ÷ 이 창의 순매수 × 100 [%]",
    "참여율[%]": "순매수 ÷ 그 종목 거래대금 × 100 [%]",
    "시총대비[%p]": "순매수 ÷ 그 종목 시총 × 100 [%p]",
    # 이 둘은 **두 화면이 같은 헤더를 쓴다** — 섹터 표에서는 섹터 합계고
    # 드릴다운에서는 종목 값이다. 어느 쪽에서도 참인 말로 적는다.
    "시총[억]": "구간말 시가총액 [억원] — 가속·시총대비의 분모",
    "거래대금[억]": "그 구간 거래대금 [억원]",
}


def hint_desc(header: str) -> str:
    """힌트바 한 줄 설명 — 짧은 것이 있으면 그것, 없으면 도움말의 긴 설명.

    폴백을 남기는 이유: 열이 하나 늘었는데 여기 안 적히면 힌트바가 **비어**
    버린다. 긴 설명은 잘리기라도 하지만 빈 줄은 아무 말도 안 한다.
    """
    return HINT_DESC.get(header) or help_desc(HELP, header)


def hint_line(head: str, desc: str, width: int) -> str:
    """항상 보이는 한 줄 힌트 — ``head · desc`` 를 폭에 **맞춰서** 낸다.

    설명이 자리를 못 채우면 열 이름만 남긴다. 한두 글자로 잘린 설명은 설명이
    아니다. ``sw-flow`` 와 ``sw-ledger`` 가 같은 자리에 같은 줄을 그리므로
    자르기 규칙도 한 곳에 둔다 — 예전에 flow 쪽만 폭을 안 봐서 폭 40 에서
    78칸짜리 줄이 나갔고, 그 실수를 원장에서 다시 하지 않으려면 함수가 하나여야 한다.

    ⚠️ 말줄임표 자리를 **한 칸으로 못박지 않는다.** ``…``(U+2026)는 East Asian
    Width 가 `A`(Ambiguous) 라 ``KQ_AMBIGUOUS_WIDE=1`` 이나 한글 로케일 터미널
    에서는 **두 칸**이다. 1 로 박아 두면 그 환경에서 이 줄만 정확히 한 칸을
    넘쳐서, 두 앱의 힌트바 끝 글자가 통째로 잘려 나갔다(실측: 폭 24~200 중
    54개 폭에서 발생). 폭 계산을 하는 함수가 자기가 쓸 글자의 폭을 다시 손으로
    세면 이렇게 어긋난다 — `cell_width` 에 묻는다.
    """
    ell = "…"
    ew = cell_width(ell)
    room = width - cell_len(head) - 4          # " · " 세 칸 + 여유 한 칸
    if room < ew + 1:
        return pad(head, width)
    if cell_len(desc) > room:
        cut, used = "", 0
        for ch in desc:
            w2 = cell_width(ch)
            if used + w2 > room - ew:
                break
            cut += ch
            used += w2
        desc = cut.rstrip() + ell
    return head + " · " + desc


def is_section(line: str) -> bool:
    """도움말에서 **구역 제목 줄**인가 — ``── 키 ──`` ``── 섹터 표 ──``.

    색을 입히는 쪽(`flow_app`)이 문자열을 다시 뜯어 맞히면 문구를 고칠 때
    조용히 어긋난다. 줄을 만드는 쪽이 판정을 진다 — 이 저장소가 헤더와 셀,
    히트맵과 색에서 이미 두 번 밟은 자리다.
    """
    return line.strip().startswith("──")


def help_lines(width: int, offset: int, height: int,
               entries: list[tuple[str, str]] | None = None,
               title_tiers: tuple[str, ...] = HELP_TITLE_TIERS,
               label_w: int = 10) -> tuple[list[str], int]:
    """도움말 화면 — (행, 전체 줄 수). offset 부터 height 줄을 낸다.

    ``entries`` 와 ``title_tiers`` 를 받는 이유: **원장도 같은 도움말이 필요하다.**
    ``sw-flow`` 와 ``sw-ledger`` 는 같은 제품이고, 도움말이 두 벌이면 렌더 규칙
    (라벨 폭 · 이어쓰기 들여쓰기 · ``**`` 떼기 · 스크롤 하한)이 반드시 갈라진다.
    이 저장소는 오늘 폭 단계 고르기가 네 벌로 갈라져 있던 걸 :func:`tier_for`
    하나로 합쳤다 — 같은 실수를 도움말에서 반복하지 않는다. **내용만** 다르다.
    """
    # 제목도 푸터처럼 **폭에 맞춰 단계별로** 줄인다. 한 줄 고정이면 85칸짜리
    # 문장이 80칸(SSH 기본)에서 단어 중간에 잘린다 — 푸터에 단계를 만든 바로
    # 그 이유가 이 줄에도 그대로 적용된다.
    title = tier_for(title_tiers, width - 1)      # 앞의 공백 한 칸
    out = [pad(" " + title, width)]
    body = []
    for name, desc in (HELP if entries is None else entries):
        # ⚠️ f"{name:>9}" 는 **문자 폭**이라 한글 라벨(임펄스=6칸)과 ASCII(G=1칸)가
        # 어긋난다. pad 는 표시 칸으로 맞춘다 — 이 저장소가 세 번 밟은 함정이다.
        #
        # **강조** 는 소스에서 눈에 띄라고 쓴 표기지 화면에 나갈 글자가 아니다.
        # 힌트바는 떼는데 여기는 안 떼서 `**닫기만**` 이 그대로 보였다.
        desc = desc.replace("**", "")
        if not name:
            body.append(pad("   " + desc, width))
        elif cell_len(name) > label_w:
            # 라벨이 열보다 길면 **자기 줄에 온전히** 둔다. `pad` 로 자르면
            # `순매수상위[억]` 이 `순매수상위` 가 되어 표 헤더와 이름이 안 맞고,
            # 그러면 도움말이 어느 열 설명인지 알려주지 못한다. 열을 넓히면
            # 설명이 오른쪽으로 밀려 폭 80(SSH 기본)을 넘는다 — 줄을 하나 쓰는
            # 편이 싸다. (flow 에서 7개가 이렇게 잘려 있었다.)
            body.append(pad(name, width))
            body.append(pad(" " * (label_w + 2) + desc, width))
        else:
            body.append(pad(pad(name, label_w, right=True) + "  " + desc, width))
    view = body[offset:offset + max(height - 1, 1)]
    return out + view, len(body)


#: 푸터는 폭에 맞춰 **단계별로** 줄인다. 예전엔 한 줄 고정이라 좁은 터미널에서
#: 잘렸고(무엇이 잘렸는지도 몰랐고), 넓은 터미널에서는 절반이 비어 있는데도
#: 대문자 역방향·g/G·PgUp/PgDn·r 이 화면 어디에도 안 적혀 있었다.
#:
#: 그 뒤 반대로 기울었다 — ``w/W m/M a/A s/S`` 처럼 **한 키의 두 방향을 다 적으니**
#: 푸터가 길어져서 정작 무슨 키가 있는지가 안 읽혔다. 이제 푸터는 역방향 대문자를
#: 안 적는다. 다만 ``G``(끝)는 ``g``(처음)의 **역방향이 아니라 별개 동작**이라
#: 남긴다 — 안 적으면 목록 끝으로 가는 길이 화면에서 사라진다.
#: 대문자 역방향·``l``·``←``·``Esc`` 는 **여전히 듣고**,
#: :data:`HELP` 의 키 절에 그대로 적혀 있다 — 화면 어디에도 안 적혀 있으면
#: 없는 기능이라는 원칙은 그대로다. 바뀐 것은 **어느 화면에 적히느냐** 뿐이다.
FOOTER_TIERS = (
    " w:구간 1:1일 m:시장 a:주체 s:정렬 r:역순 ↑↓:섹터 g/G:처음/끝"
    " PgUp/PgDn:쪽 Enter:종목 e:탐색 o:오늘 ?:도움말 q:종료",
    " w:구간 1:1일 m:시장 a:주체 s:정렬 r:역순 ↑↓:섹터 Enter:종목 e:탐색 o:오늘 ?:도움말 q:종료",
    " w m a s:바꾸기 r:역순 Enter:종목 e:탐색 o:오늘 ?:전체 키 q:종료",
    " w m a s r:바꾸기 Enter:종목 e ?:키 q:종료",
    " ?:키 q:종료",
)
FOOTER_DRILL_TIERS = (
    " ↑↓:종목 s:정렬 r:역순 g/G:처음/끝 PgUp/PgDn:쪽 w:구간 1:1일 m:시장 a:주체"
    " e:탐색 o:오늘 h:돌아가기 ?:도움말 q:종료",
    " ↑↓:종목 s:정렬 r:역순 w:구간 m:시장 a:주체 e:탐색 h:돌아가기 ?:도움말 q:종료",
    " s:정렬 r:역순 w m a:바꾸기 h:돌아가기 ?:전체 키 q:종료",
    " s r w m a:바꾸기 h:뒤로 ?:키 q:종료",
    " ?:키 h:뒤로 q:종료",
)
#: 탐색 모드(`e`)의 푸터 — 탐색 전용 키 `t`(전 종목·곱)가 여기서만 적힌다. 기본 모드에서
#: `t` 는 안 듣는다(곱은 탐색 점수다). 적혔는데 안 듣는 키가 안 적힌 키보다 나쁘다.
FOOTER_EXPLORE_TIERS = (
    " w:구간 1:1일 m:시장 a:주체 s:정렬 r:역순 ↑↓:섹터 g/G:처음/끝"
    " PgUp/PgDn:쪽 Enter:종목 t:전종목 e:탐색끔 o:오늘 ?:도움말 q:종료",
    " w:구간 m:시장 a:주체 s:정렬 r:역순 ↑↓:섹터 Enter:종목 t:전종목 e:탐색끔 o:오늘 ?:도움말 q:종료",
    " w m a s:바꾸기 r:역순 Enter:종목 t:전종목 e:탐색끔 ?:전체 키 q:종료",
    " w m a s r:바꾸기 Enter:종목 t e ?:키 q:종료",
    " ?:키 q:종료",
)
FOOTER_DRILL_EXPLORE_TIERS = (
    " ↑↓:종목 s:정렬 r:역순 g/G:처음/끝 PgUp/PgDn:쪽 w:구간 m:시장 a:주체"
    " t:전종목 e:탐색끔 h:돌아가기 ?:도움말 q:종료",
    " ↑↓:종목 s:정렬 r:역순 w:구간 m:시장 a:주체 t:전종목 e:탐색끔 h:돌아가기 ?:도움말 q:종료",
    " s:정렬 r:역순 w m a:바꾸기 t e h:돌아가기 ?:전체 키 q:종료",
    " s r w m a:바꾸기 h:뒤로 ?:키 q:종료",
    " ?:키 h:뒤로 q:종료",
)
#: 전 종목 화면(``t``)의 푸터. **이 화면은 자기 푸터를 가져야 한다** — 예전엔
#: 섹터 표의 푸터를 그대로 그렸고, 그 줄은 여기서 **안 듣는** ``s``(정렬)·``r``
#: (역순)·``Enter``(종목)를 광고하면서 정작 **돌아가는 키**(``h``·``←``·``Esc``)는
#: 한 글자도 안 적었다. 적혔는데 안 듣는 키가 안 적힌 키보다 나쁘다 — 없는 기능을
#: 찾아 누르게 만들기 때문이다. 게다가 ``t:전종목`` 이라 적혀 있었는데 이 화면에서
#: ``t`` 는 **나가기**였다(같은 글자가 같은 줄에서 반대 뜻).
#:
#: 정렬이 없는 것은 설계다(곱 내림차순 고정) — 힌트바가 그 사실을 적는다
#: (:data:`flow_app.HINT_ALL_TIERS`), 종합 화면이 정렬 없음을 적는 것과 같은 규칙이다.
#: 이 화면은 탐색 모드에서만 열린다.
FOOTER_ALL_TIERS = (
    " ↑↓:종목 g/G:처음/끝 PgUp/PgDn:쪽 w:구간 m:시장 a:주체"
    " t·h:섹터 표로 e:탐색끔 ?:도움말 q:종료",
    " ↑↓:종목 w:구간 m:시장 a:주체 t·h:섹터 표로 e:탐색끔 ?:도움말 q:종료",
    " w m a:바꾸기 t·h:돌아가기 e:탐색끔 ?:전체 키 q:종료",
    " w m a:바꾸기 h:뒤로 ?:키 q:종료",
    " ?:키 h:뒤로 q:종료",
)
#: 종합 화면의 푸터. 이 화면은 `s`·`r` 이 안 듣는다(:attr:`State.sortable`) — 적혔는데 안 듣는
#: 키가 안 적힌 키보다 나쁘다. 종합은 G 를 섞은 축이라 탐색 모드에서만 닿는다.
FOOTER_COMBINED_TIERS = (
    " w:구간 m:시장 a:주체 ↑↓:섹터 g/G:처음/끝 PgUp/PgDn:쪽 Enter:종목 t:전종목 e:탐색끔"
    " ?:도움말 q:종료",
    " w:구간 m:시장 a:주체 ↑↓:섹터 Enter:종목 t:전종목 e:탐색끔 ?:도움말 q:종료",
    " w m a:바꾸기 Enter:종목 t:전종목 e:탐색끔 ?:전체 키 q:종료",
    " w m a:바꾸기 Enter t e ?:키 q:종료",
    " ?:키 q:종료",
)
#: 도움말 모달의 마지막 줄. 예전엔 :mod:`flow_app` 안에 **한 줄 고정 문자열**로
#: 박혀 있었다 — 폭 50 에서 위치 표시 ``1-20 / 200`` 이 ``1-20 / 20`` 으로 잘렸다.
#: 잘린 숫자는 다른 숫자다(전체 200줄이 20줄로 읽힌다). 원장은 이미 단계로
#: 갖고 있었으므로(``ledger_view.HELP_FOOT_TIERS``) 같은 자리에 같은 모양으로 둔다.
HELP_FOOT_TIERS = (
    " ↑↓/PgUp/PgDn/Space:스크롤  g/G·Home/End:처음·끝  q·Esc·?·Enter:닫기",
    " ↑↓/PgDn/Space:스크롤  g/G:처음·끝  q·Esc·?:닫기",
    " ↑↓/PgDn:스크롤  g/G:처음·끝  q:닫기",
    " ↑↓:스크롤  q:닫기",
    " q:닫기",
)
#: 예전 이름 — 중간 단계가 기본이다.
FOOTER = FOOTER_TIERS[1]
FOOTER_DRILL = FOOTER_DRILL_TIERS[1]


def footer_tiers(drill: bool = False, allv: bool = False,
                 combined: bool = False, explore: bool = False) -> tuple[str, ...]:
    """이 화면이 쓰는 푸터 단계 — **어느 화면이 어느 푸터를 쓰는지의 유일한 판정**.

    앱이 `if` 로 고르면 화면이 하나 늘 때 그 `if` 를 빠뜨리고, 실제로 빠뜨렸다
    (전 종목 화면이 섹터 표 푸터를 그렸다). 검사도 여기 묻는다 — 검사와 화면이
    다른 표를 보면 검사가 통과하면서 아무것도 확인 못 한다.
    """
    if allv and not drill:
        return FOOTER_ALL_TIERS
    if drill:
        return FOOTER_DRILL_EXPLORE_TIERS if explore else FOOTER_DRILL_TIERS
    if combined:
        return FOOTER_COMBINED_TIERS
    return FOOTER_EXPLORE_TIERS if explore else FOOTER_TIERS


def scroll_mark(first: int, shown: int, total: int) -> str:
    """목록이 화면보다 길 때 **어디를 보고 있나** — `1-14/27`. 다 보이면 빈 문자열.

    이 표시가 없으면 80x24 에서 27개 중 14개만 보여도 그게 전부처럼 읽힌다.
    """
    return f" {first + 1}-{first + shown}/{total} " if 0 < shown < total else ""


def footer_line(width: int, drill: bool = False, allv: bool = False,
                combined: bool = False, explore: bool = False) -> str:
    """폭에 **온전히** 들어가는 가장 자세한 푸터.

    어느 단계에서도 ``?`` 는 남긴다 — 줄어든 푸터가 "여기가 전부" 로 읽히면
    안 되기 때문이다. 나머지 키는 ``?`` 뒤에 전부 적혀 있다.
    """
    return tier_for(footer_tiers(drill, allv, combined, explore), width)
