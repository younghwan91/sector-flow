"""공개 표면(README 그림·브랜드·랜딩)에는 **합성 데모**만 나간다 — 설계 ``2026-11-21-flow-map-brand-design.md`` §6.1.

실데이터(리포트 폴더·종목별 수급·섹터 합계 그림)는 KRX·키움 이용 범위상 공개하지 않는다. 공개 경로에 실데이터가 나갈 수 있는
길을 사람이 매번 기억하는 대신 이 검사가 막는다.

* HTML: 데모 표지(:data:`sectorflow.basis.DEMO_NOTICE`)가 있고, 벤더 업종명이 **따옴표 안의 값**(심은 데이터)으로 나오지
  않는다. 실 리포트의 데이터 줄은 섹터 이름을 ``"전기/전자"`` 처럼 따옴표로 수천 번 적는다. 템플릿의 주석·도움말은 업종명을
  설명에 쓰므로(``부동산 3종목`` 등) 산문은 보지 않는다 — 데모 뷰어도 그렇다.
* PNG: ``docs/SCREENSHOTS.md`` 원장에 그 그림의 줄이 있다(원장은 그림이 전부 합성 데모라고 적고 찍는 법을 남긴다).
* 추적 없음: 분석 스크립트·쿠키를 넣지 않는다.

README 산문은 대상이 아니다 — 업종명을 설명에 쓸 수 있다. 이미지·HTML 만 본다.
"""
from __future__ import annotations

import re
from pathlib import Path

from sectorflow.basis import DEMO_NOTICE
from sectorflow.demo import BANNER

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "docs" / "SCREENSHOTS.md"

#: 공개되는 경로. README 는 ``docs/images/`` 의 그림을 싣는다.
PUBLIC_GLOBS = ("README.md", "README.en.md", "docs/images/**", "docs/brand/**", "site/**")

#: 벤더(키움) 업종 분류의 섹터 이름 — 실데이터 페이로드의 ``sectors``(``(미분류)`` 는 결측의 이름이라 뺀다).
#: 데모 이름은 전부 ``가상`` 으로 시작한다(``sectorflow.demo.SECTORS``) — 실제처럼 보이는 가짜 이름은 실데이터로 오인된다.
VENDOR_SECTORS = ("IT 서비스", "건설", "금속", "금융", "기계/장비", "기타제조", "보험", "부동산", "비금속", "섬유/의류",
                  "오락/문화", "운송/창고", "운송장비/부품", "유통", "음식료/담배", "의료/정밀기기", "일반서비스", "전기/가스",
                  "전기/전자", "제약", "제조", "종이/목재", "증권", "출판/매체복제", "통신", "화학")

#: 따옴표 안의 값이 통째로 벤더 업종명인 곳. 기관 세부 라벨 ``"insrnc": "보험"`` 은 섹터가 아니라 주체 이름이라 뺀다.
_VENDOR_VALUE = re.compile(r"""(?<!"insrnc":)(?<!"insrnc": )(["'])(""" + "|".join(map(re.escape, VENDOR_SECTORS))
                           + r""")\1""")
_TRACKING = re.compile(r"gtag|analytics|plausible|document\.cookie", re.I)
#: 원장의 표 줄 — 첫 칸이 그림 경로다(``| docs/images/x.png | 감시 경로 | sha |``).
_LEDGER_ROW = re.compile(r"^\|\s*(\S+?\.png)\s*\|", re.M)


def public_files(suffix: str) -> list[Path]:
    seen: set[Path] = set()
    for pat in PUBLIC_GLOBS:
        seen.update(p for p in REPO.glob(pat) if p.is_file() and p.suffix == suffix)
    return sorted(seen)


def html_problems(text: str) -> list[str]:
    """공개 HTML 이 데모가 아닌 까닭들 — 비면 통과."""
    out = [] if DEMO_NOTICE in text else ["데모 표지(DEMO_NOTICE)가 없다"]
    out += [f"벤더 업종명 {n!r} 이 데이터로 들었다" for n in sorted({m.group(2) for m in _VENDOR_VALUE.finditer(text)})]
    return out


def tracking_hits(text: str) -> list[str]:
    return sorted(set(_TRACKING.findall(text)))


def ledger_pngs() -> set[str]:
    return set(_LEDGER_ROW.findall(LEDGER.read_text("utf-8")))


# mutation-exempt: 지금 공개 HTML 이 없어(site/·docs/brand/ 가 생기기 전) 빈 목록을 돈다 — 판정은 test_html_judge_* 가 변이로 증명한다
def test_public_html_is_demo_only():
    bad = {str(p.relative_to(REPO)): probs for p in public_files(".html") if (probs := html_problems(p.read_text("utf-8")))}
    assert not bad, f"공개 HTML 은 합성 데모만 — {bad}"


def test_public_png_has_demo_sidecar():
    listed = ledger_pngs()
    pngs = [str(p.relative_to(REPO)) for p in public_files(".png")]
    assert pngs, "공개 그림을 하나도 못 찾았다 — PUBLIC_GLOBS 가 헛돈다"
    missing = [p for p in pngs if p not in listed]
    assert not missing, f"docs/SCREENSHOTS.md 원장에 없는 공개 그림(데모로 찍은 기록이 없다): {missing}"


# mutation-exempt: 지금 공개 HTML 이 없어 빈 목록을 돈다 — 판정은 test_tracking_judge_catches_scripts 가 변이로 증명한다
def test_no_tracking():
    bad = {str(p.relative_to(REPO)): hits for p in public_files(".html") if (hits := tracking_hits(p.read_text("utf-8")))}
    assert not bad, f"공개 HTML 에 분석·쿠키: {bad}"


def test_html_judge_passes_demo_and_fails_real():
    demo_page = (f"<body>{BANNER}<script>const D = {{\"sectors\": [\"가상전자\", \"가상금융\"], "
                 f"\"detail_labels\": {{\"insrnc\": \"보험\"}}}};</script>"
                 "<!-- 부동산 3종목이 2등이던 옛 정렬 --></body>")
    assert html_problems(demo_page) == [], "데모 뷰어처럼 주석·도움말이 업종명을 산문으로 쓰는 것은 막지 않는다"
    real_page = '<script>const D = {"sectors": ["전기/전자", "금융"], "flows": {"거래소": {"운송/창고": {}}}};</script>'
    assert html_problems(real_page) == ["데모 표지(DEMO_NOTICE)가 없다", "벤더 업종명 '금융' 이 데이터로 들었다",
                                        "벤더 업종명 '운송/창고' 이 데이터로 들었다",
                                        "벤더 업종명 '전기/전자' 이 데이터로 들었다"]
    mixed = f"{BANNER}<script type=\"application/json\">{{\"name\": 'IT 서비스'}}</script>"
    assert html_problems(mixed) == ["벤더 업종명 'IT 서비스' 이 데이터로 들었다"], "표지가 있어도 실데이터가 섞이면 실패"


def test_tracking_judge_catches_scripts():
    assert tracking_hits("<script src='https://www.googletagmanager.com/gtag/js'></script>") == ["gtag"]
    assert tracking_hits("document.cookie = 'x=1'; plausible('pageview')") == ["document.cookie", "plausible"]
    assert tracking_hits(f"<body>{BANNER}</body>") == []


def test_ledger_rows_are_read():
    """원장 형식이 바뀌어 줄을 하나도 못 읽으면 모든 그림이 '원장에 없다' 가 된다 — 반대로 헛돌지 않는지도 본다."""
    rows = ledger_pngs()
    assert "docs/images/web-today.png" in rows
    assert all(r.endswith(".png") and "|" not in r for r in rows)
