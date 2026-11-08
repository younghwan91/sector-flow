#!/usr/bin/env python3
"""검증·품질 페이지 — 리포트 폴더의 기록에서 ``trust.html`` 을 만든다. 표준 라이브러리만, DB 를 안 읽는다.

    python3 scripts/trust_page.py --root reports                 # → reports/latest/trust.html
    python3 scripts/trust_page.py --root reports --out /tmp/x.html

싣는 것(네 절):

1. **일일 파이프라인 품질** — 기준일 폴더마다 잠정/확정/재구성, ``VERIFY.txt`` 요약(통과/전체·문서 주장 어긋남),
   완전성 관문 수치(``INCOMPLETE.txt`` 또는 ``cron.log`` 의 관문 줄), 잠정→확정 변경 칸 수(``FINAL.txt``).
   최근 :data:`WINDOW` 개 기준일을 한 줄 띠와 표로.
2. **방법** — 관문·자기 검증 층(최신 ``VERIFY.txt`` 에서 읽는다)·확정 모드·사전등록, ``docs/`` 링크.
3. **검증 결과** — :data:`VALIDATION`·:data:`CHECKS`. 불리한 결과(증거 없음·판정 불가·쓸 수 없음·불합격)를 뺀 것이 없다.
   사전등록 커밋이 결과 커밋보다 앞선다는 것은 ``tests/test_trust_page.py`` 가 git 으로 확인한다.
4. **알려진 한계** — 템플릿에 고정.

수익률·성과 숫자는 싣지 않는다. :data:`BANNED` 의 말이 페이지에 들어가면(예: 검사 이름에 섞여 들어오면) 쓰지 않고 2 로 끝난다.

**어디에 쓰나.** 웹(``serve_reports.py``)은 기준일 폴더 안의 허용 목록 파일만 내보낸다. 그래서 기본 출력은
``<root>/latest/trust.html`` 이고 ``/latest/trust.html`` 로 열린다(그날 폴더의 ``/<기준일>/trust.html`` 로 넘긴다).
폴더를 바꿔 끼우면 사라지므로 리포트를 만든 **뒤에** 다시 돌린다.

**배치에 붙이는 법(운영 담당이 ``daily_report.sh`` 끝, ``exit "$VERIFY_RC"`` 바로 앞에 넣는다)** — 실패해도
리포트 결과(종료코드)를 바꾸지 않는다::

    if [ -n "${KR_QUANT_TRUST_PAGE:-}" ]; then
      "${UVR[@]}" python scripts/trust_page.py --root "$OUT_ROOT" \\
        || echo "[$(date '+%F %T')] ⚠️ 검증·품질 페이지를 못 만들었다(리포트와 무관)" >&2
    fi

크론에서 켜려면 ``deploy/crontab.simnode`` 의 리포트 줄에 ``KR_QUANT_TRUST_PAGE=1`` 을 붙인다.
"""
from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO, "scripts", "templates", "trust.html")
KST = timezone(timedelta(hours=9))
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: 띠·표에 싣는 최근 기준일 수.
WINDOW = 60
#: 이 말이 페이지에 있으면 쓰지 않는다 — 관측 페이지가 매매 권유처럼 읽히면 안 된다.
BANNED = ("추천", "매수 신호", "급등", "수익 보장", "적중")
DOCS_BASE = "https://github.com/younghwan91/sector-flow/blob/main/"

SUMMARY_RE = re.compile(r"^검사 (\d+)건 · 실패 (\d+)건(?: · 문서 주장 어긋남 (\d+)건)?\s*$", re.M)
FAIL_LIST_RE = re.compile(r"^실패 목록: (.+)$", re.M)
MARK_RE = re.compile(r"^\s*\[(OK  |FAIL|OK   |CLAIM)\] ", re.M)
LAYER_RE = re.compile(r"^([A-Z][0-9]?)\. (.+)$", re.M)
GATE_HEAD_RE = re.compile(r"^완전성 관문 — 기준일 (\d{4}-\d{2}-\d{2})")
GATE_LINE_RE = re.compile(r"^\s+\[(PASS|FAIL)\] ([^:]+): (.*)$")
GATE_PASS_RE = re.compile(r"수급 (\d+)/(\d+) · 시총 (\d+)/(\d+)")
FINAL_MKT_RE = re.compile(r"^시장 합계: (\d+)칸 중 (\d+)칸 바뀜", re.M)
FINAL_SEC_RE = re.compile(r"^섹터 칸: (\d+)칸 중 (\d+)칸 바뀜", re.M)
FINAL_NONE_RE = re.compile(r"비교 없음: (.+)$", re.M)


# ─────────────────────────────────────────────── 읽기 — 파일 하나씩, 순수 함수

def parse_verify(text: str) -> dict:
    """``VERIFY.txt`` → 검사 수·실패 수·문서 주장 수·실패 이름. 요약 줄이 없으면(검사기가 도중에 멈춤) 표시를 센다."""
    m = None
    for m in SUMMARY_RE.finditer(text):
        pass
    fails = []
    fl = FAIL_LIST_RE.search(text)
    if fl:
        fails = [s.strip() for s in fl.group(1).split(", ") if s.strip()]
    marks = Counter(x.strip() for x in MARK_RE.findall(text))
    if m:
        checks, n_fail = int(m.group(1)), int(m.group(2))
        claims = int(m.group(3)) if m.group(3) is not None else marks.get("CLAIM", 0)
        return {"checks": checks, "fails": n_fail, "claims": claims, "complete": True, "fail_names": fails}
    return {"checks": sum(marks.values()), "fails": marks.get("FAIL", 0), "claims": marks.get("CLAIM", 0),
            "complete": False, "fail_names": fails}


def parse_gate(text: str) -> dict[str, list[dict]]:
    """관문 출력(크론 로그·``INCOMPLETE.txt``) → 기준일 → 시장별 판정. 같은 기준일이 여러 번이면 마지막 것."""
    out: dict[str, list[dict]] = {}
    cur = None
    for line in text.splitlines():
        h = GATE_HEAD_RE.match(line)
        if h:
            cur = out[h.group(1)] = []
            continue
        g = GATE_LINE_RE.match(line) if cur is not None else None
        if not g:
            cur = None  # 관문 블록은 머리줄 바로 아래 판정 줄들뿐이다
            continue
        row = {"market": g.group(2).strip(), "ok": g.group(1) == "PASS", "text": g.group(3).strip(), "min": None}
        p = GATE_PASS_RE.search(row["text"])
        if p:
            n, base, ncap, bcap = (int(v) for v in p.groups())
            ratios = [a / b for a, b in ((n, base), (ncap, bcap)) if b]
            row["min"] = min(ratios) if ratios else None
        cur.append(row)
    return {d: rows for d, rows in out.items() if rows}


def parse_final(text: str) -> dict:
    """``FINAL.txt`` → 잠정→확정 변경 칸 수. 비교가 생기기 전 확정본은 ``kind="unknown"``."""
    mk, sc, no = FINAL_MKT_RE.search(text), FINAL_SEC_RE.search(text), FINAL_NONE_RE.search(text)
    if sc:
        return {"kind": "diff", "sector": (int(sc.group(2)), int(sc.group(1))),
                "market": (int(mk.group(2)), int(mk.group(1))) if mk else None}
    if no:
        return {"kind": "none", "reason": no.group(1).strip()}
    return {"kind": "unknown"}


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def report_dirs(root: str) -> list[str]:
    try:
        names = os.listdir(root)
    except FileNotFoundError:
        return []
    return sorted(n for n in names if DATE_RE.match(n) and os.path.isdir(os.path.join(root, n)))


def latest_dir(root: str) -> str | None:
    """``latest`` 링크의 기준일. 없거나 깨졌으면 리포트가 있는 가장 늦은 폴더."""
    try:
        t = os.path.basename(os.path.normpath(os.readlink(os.path.join(root, "latest"))))
        if DATE_RE.match(t) and os.path.isdir(os.path.join(root, t)):
            return t
    except OSError:
        pass
    ds = [d for d in report_dirs(root) if os.path.exists(os.path.join(root, d, "numbers.html"))]
    return ds[-1] if ds else None


def archived(root: str) -> Counter:
    """``.archive/`` 에 보관된 옛 게시본 수 — 기준일별(``<기준일>-provisional``·``<기준일>.<시각>``)."""
    try:
        names = os.listdir(os.path.join(root, ".archive"))
    except OSError:
        return Counter()
    return Counter(n[:10] for n in names if DATE_RE.match(n[:10]))


def scan_day(root: str, d: str, gates: dict) -> dict:
    p = os.path.join(root, d)
    has = {f: os.path.exists(os.path.join(p, f)) for f in (
        "numbers.html", "payload.json", "VERIFY.txt", "VERIFY_FAILED.txt", "CLAIMS.txt",
        "FINAL.txt", "REBUILT.txt", "INCOMPLETE.txt")}
    day = {"date": d, "verify": None, "verify_status": "unknown", "gate": None, "gate_src": None,
           "final": None, "built": None}
    if not has["numbers.html"]:
        day["state"] = "수집 미완" if has["INCOMPLETE.txt"] else "빈 폴더"
    elif has["REBUILT.txt"]:
        day["state"] = "재구성"
    elif has["FINAL.txt"]:
        day["state"] = "확정"
    else:
        day["state"] = "잠정"
    vtxt = _read(os.path.join(p, "VERIFY.txt")) if has["VERIFY.txt"] else None
    if vtxt is None and has["VERIFY_FAILED.txt"]:
        vtxt = _read(os.path.join(p, "VERIFY_FAILED.txt"))
    if vtxt is not None:
        v = day["verify"] = parse_verify(vtxt)
        if has["CLAIMS.txt"] and not v["claims"]:
            v["claims"] = sum(1 for ln in (_read(os.path.join(p, "CLAIMS.txt")) or "").splitlines()
                              if ln.startswith("- "))
        if has["VERIFY_FAILED.txt"] or v["fails"] or not v["complete"]:
            day["verify_status"] = "fail"
        else:
            day["verify_status"] = "claim" if v["claims"] else "pass"
    if day["state"] == "수집 미완":
        day["verify_status"] = "incomplete"
    inc = _read(os.path.join(p, "INCOMPLETE.txt")) if has["INCOMPLETE.txt"] else None
    own = parse_gate(inc).get(d) if inc else None
    if own:
        day["gate"], day["gate_src"] = own, "INCOMPLETE.txt"
    elif d in gates:
        day["gate"], day["gate_src"] = gates[d], "cron.log"
    if has["FINAL.txt"]:
        day["final"] = parse_final(_read(os.path.join(p, "FINAL.txt")) or "")
    built = os.path.join(p, "payload.json" if has["payload.json"] else "numbers.html")
    if os.path.exists(built):
        day["built"] = datetime.fromtimestamp(os.path.getmtime(built), KST).strftime("%Y-%m-%d %H:%M")
    return day


def scan(root: str) -> list[dict]:
    """기준일 오름차순."""
    gates = parse_gate(_read(os.path.join(root, "cron.log")) or "")
    arc = archived(root)
    days = []
    for d in report_dirs(root):
        day = scan_day(root, d, gates)
        day["archived"] = arc.get(d, 0)
        days.append(day)
    return days


def verify_layers(root: str, d: str | None) -> list[tuple[str, str]]:
    """최신 ``VERIFY.txt`` 의 층 머리줄 — 방법 절이 지금 검사기를 말하게."""
    t = _read(os.path.join(root, d, "VERIFY.txt")) if d else None
    return LAYER_RE.findall(t or "")


# ─────────────────────────────────────────────── 검증 결과 — 연구 문서에서 옮긴 것(2026-11-08)
#
# 숫자를 바꾸면 docs/validation/ 의 문서도 같이 봐야 한다. 커밋 해시는 테스트가 git 으로 대조한다:
# 사전등록 해시 = 그 파일을 처음 넣은 커밋, 결과 해시 = 그 결과 파일을 건드린 커밋, 사전등록이 결과보다 먼저.

PREREG = "docs/validation/2026-11-08-prereg.md"
PREREG_Q3D = "docs/validation/2026-11-08-prereg-q3d-rerun.md"
PREREG_USE = "docs/validation/2026-11-08-prereg-usefulness.md"
RESULTS = "docs/validation/2026-11-08-results.md"
RESULTS_USE = "docs/validation/2026-11-08-usefulness-results.md"

#: (질문, 판정, 판정 등급, 설명, (사전등록 파일, 커밋), (결과 파일, 커밋))
#: 등급: none = 증거 없음 · bad = 불합격·쓸 수 없음 · mixed = 판정 불가·조건부 · ok = 통과
VALIDATION = [
    ("섹터 임펄스·가속·x(20일)의 앞날 관계 — h5·h20, 6칸", "증거 없음", "none",
     "6칸 모두 사전등록 기준(널 p&lt;0.05 + 전·후반 같은 부호)에 못 미쳤다. h5 는 |IC| 약 0.035 이상을 대략 배제하지만 "
     "h20 은 0.05~0.08 아래를 배제하지 못한다 — 있어도 이 표본으로는 못 본다. 결함을 고친 정의로 다시 쟀고 첫 실행도 같은 결론이었다.",
     (PREREG_Q3D, "6ca326a"), (RESULTS, "8385ae1")),
    ("선정 점수 G의 앞날 관계 — h5·h20", "판정 불가", "mixed",
     "관문 통과 섹터가 5개 이상인 날이 h5 82일·h20 19일뿐이라 검정력이 없다. 잠정치에도 흔들린다(아래 Q3a 불합격). "
     "화면은 G 를 “검증 안 된 탐색 점수” 로 적는다.",
     (PREREG_Q3D, "6ca326a"), (RESULTS, "8385ae1")),
    ("미실현 x 는 무엇을 재나", "사실상 섹터 20일 반전", "mixed",
     "x 와 −Δv(같은 구간 섹터 수익률의 반대)의 순위상관이 0.82~0.93 이다. h20 의 약한 신호도 수급이 아니라 과거 20일 수익률만으로 "
     "같게 나오고, 8칸 다중검정 기준(t≈2.7)에 못 미친다.",
     (PREREG_Q3D, "6ca326a"), (RESULTS, "8385ae1")),
    ("섹터 수급(투신+연기금·외국인 20일, 기관 60일)의 앞날 관계 — 6칸", "증거 없음", "none",
     "발견 구간(2018~2022)에서 여섯 칸 모두 탈락해 확인 단계로 가지 못했다. 화면의 주 단위인 섹터 수급은 앞날 수익률에 대한 증거가 없다.",
     (PREREG_USE, "b702dc4"), (RESULTS_USE, "cd9c4a2")),
    ("종목 투신+연기금 20일 순매수 ÷ 시총 — 섹터 안 순위(K1)", "음(−)의 순위 정보 확인 · 쓸 수 있는 형태는 못 보임", "bad",
     "섹터 안 순위 IC 의 부호가 음으로 확인됐다(확인 구간 t≈−6, 9개 연도 같은 부호). 그러나 순매수 상위를 피하는 쓰임은 확인 구간에서 "
     "역효과였고, 정보가 순매도 쪽에 몰리는 모양이 발견 구간과 달랐으며, 절반가량은 단기 반전과 겹치고, 생존편향이 결론 쪽으로 부풀렸을 수 있다. "
     "화면의 종목 선정 축(큰 순매수를 좋게 본다)은 이 부호와 반대다.",
     (PREREG_USE, "b702dc4"), (RESULTS_USE, "cd9c4a2")),
    ("종목 기관 합계(K2)·외국인(K3) 20일", "미확인 · 탈락", "none",
     "K2 는 발견에서 통과했으나 확인 구간에서 유의수준(0.0125)을 넘지 못했다. K3 는 발견에서 탈락했다.",
     (PREREG_USE, "b702dc4"), (RESULTS_USE, "cd9c4a2")),
]

CHECKS = [
    ("Q1 데이터 — 게시본 값 = DB 재계산", "불합격(원인 설명됨)", "bad",
     "11-06 셀 99.735% 일치(합격선 99.9%). 넘는 셀은 전부 기준일 잠정치였다. 과거 게시본 셋은 그 뒤 주식수 정정으로 불합격, "
     "미해명 16셀(최대 0.026%p). 표본 15건 손검산은 15/15 일치.",
     (PREREG, "bd27017"), (RESULTS, "dae572f")),
    ("Q2 계산 — 문서 정의만으로 다시 계산", "불합격 → 같은 날 고침", "bad",
     "문서가 코드와 달랐고 버그가 나왔다: 통과 섹터가 하나면 G 가 사라짐, 섹터 수익률을 이어 쓸 수 없는 등락률로 만듦, "
     "시총을 수정주가 × 늦은 주식수로 만듦. 세 결함을 고치고 검사·변이를 붙였다.",
     (PREREG, "bd27017"), (RESULTS, "dae572f")),
    ("Q3a 잠정치 민감도 — 게시본 vs 확정치", "가속 통과 · G 불합격", "bad",
     "가속 순위 ρ 중앙 0.999. G 는 세 시장 통과 62.5%(합격선 90%). 그래서 다음날 08:20 확정 모드를 만들었다.",
     (PREREG, "bd27017"), (RESULTS, "dae572f")),
    ("Q3b 금액 근사(수량 × 종가)", "거래소 통과 · 코스닥 불안정", "mixed",
     "48일 중 상대차 중앙 5% 이내: 거래소 100%, 전체 98%, 코스닥 71%.",
     (PREREG, "bd27017"), (RESULTS, "dae572f")),
]


def git_time(commit: str, repo: str = REPO) -> str | None:
    """커밋 시각(KST, 분까지). git 이 없거나 해시가 없으면 None — 페이지는 해시만 싣는다."""
    try:
        out = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%ad", "--date=format-local:%Y-%m-%d %H:%M",
                              commit], capture_output=True, text=True, timeout=10, env={**os.environ, "TZ": "Asia/Seoul"})
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout.strip() or None) if out.returncode == 0 else None


# ─────────────────────────────────────────────── 그리기

STATE_TAG = {"확정": "ok", "잠정": "prov", "재구성": "rebuilt", "수집 미완": "warn", "빈 폴더": "warn"}
GLYPH = {"pass": "✓", "claim": "!", "fail": "✕", "incomplete": "…", "unknown": "?"}
STATUS_KO = {"pass": "검증 통과", "claim": "검증 통과 · 문서 주장 어긋남", "fail": "검증 실패",
             "incomplete": "수집 미완", "unknown": "검증 기록 없음"}
VERDICT_CLS = {"none": "v-none", "bad": "v-bad", "mixed": "v-mixed", "ok": "v-ok"}
E = html.escape


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def gate_cell(day: dict) -> str:
    g = day["gate"]
    if not g:
        return '<span class="dim">기록 없음</span>'
    title = E(" / ".join(f"{r['market']}: {r['text']}" for r in g) + f" ({day['gate_src']})")
    if all(r["ok"] for r in g):
        mins = [r["min"] for r in g if r["min"] is not None]
        return f'<span class="tag ok" title="{title}">통과' + (f" · 최저 {_pct(min(mins))}" if mins else "") + "</span>"
    bad = [r for r in g if not r["ok"]]
    return (f'<span class="tag warn" title="{title}">막힘</span> '
            + E(" · ".join(f"{r['market']}: {r['text']}" for r in bad)))


def verify_cell(day: dict) -> str:
    v = day["verify"]
    if v is None:
        return '<span class="dim">기록 없음</span>'
    d = day["date"]
    href = f"/{d}/VERIFY_FAILED.txt" if day["verify_status"] == "fail" else f"/{d}/VERIFY.txt"
    ok = v["checks"] - v["fails"] - v["claims"]
    txt = f"{ok}/{v['checks']}"
    if not v["complete"]:
        return (f'<a class="tag bad" href="{href}" title="검사기가 도중에 멈췄다">중단</a> '
                f'요약 줄 없음 · 멈추기 전 {v["checks"]}건 실패 {v["fails"]}')
    if day["verify_status"] == "fail":
        names = E(", ".join(v["fail_names"]))
        return f'<a class="tag bad" href="{href}" title="{names}">실패 {v["fails"]}</a> <span class="num">{txt}</span>'
    return f'<a class="tag ok" href="{href}">통과</a> <span class="num">{txt}</span>'


def final_cell(day: dict) -> str:
    f = day["final"]
    if day["state"] not in ("확정", "재구성") or f is None:
        return '<span class="dim">—</span>'
    if f["kind"] == "diff":
        s = f"섹터 {f['sector'][0]:,}/{f['sector'][1]:,}칸"
        if f["market"]:
            s = f"시장 {f['market'][0]}/{f['market'][1]} · " + s
        return f'<a href="/{day["date"]}/FINAL.txt">{s}</a>'
    if f["kind"] == "none":
        return f'<span class="dim" title="{E(f["reason"])}">비교 없음</span>'
    return '<span class="dim">기록 없음(비교 기능 이전)</span>'


def state_cell(day: dict) -> str:
    st = day["state"]
    href = {"확정": "FINAL.txt", "재구성": "REBUILT.txt", "수집 미완": "INCOMPLETE.txt"}.get(st)
    tag = f'<span class="tag {STATE_TAG[st]}">{st}</span>'
    if href:
        tag = f'<a class="tag {STATE_TAG[st]}" href="/{day["date"]}/{href}">{st}</a>'
    if day.get("archived"):
        tag += f' <span class="dim" title=".archive/ 에 옛 게시본 보관">원본 보관 {day["archived"]}</span>'
    return tag


def strip_html(days: list[dict]) -> str:
    cells = []
    for day in days:
        st = day["verify_status"]
        v = day["verify"]
        tip = f"{day['date']} · {day['state']} · {STATUS_KO[st]}"
        if v:
            tip += f" · 검사 {v['checks']}건 실패 {v['fails']}건 문서 주장 {v['claims']}건"
        cells.append(f'<span class="cell {st}" title="{E(tip)}" aria-label="{E(tip)}">{GLYPH[st]}</span>')
    return f'<div class="strip" role="list">{"".join(cells)}</div>'


def tiles_html(days: list[dict]) -> str:
    c = Counter(d["verify_status"] for d in days)
    s = Counter(d["state"] for d in days)
    items = [
        (len(days), "기준일 폴더"),
        (c["pass"] + c["claim"], "자기 검증 통과"),
        (c["fail"], "자기 검증 실패"),
        (c["claim"], "통과 · 문서 주장 어긋남"),
        (c["incomplete"], "수집 미완(안 만듦)"),
        (s["확정"], "확정"), (s["잠정"], "잠정"), (s["재구성"], "재구성"),
    ]
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><b>{n}</b><span>{E(k)}</span></div>' for n, k in items) + "</div>"


def fail_reasons_html(days: list[dict]) -> str:
    cnt: Counter = Counter()
    last: dict[str, str] = {}
    for d in days:
        if d["verify_status"] != "fail" or not d["verify"]:
            continue
        names = d["verify"]["fail_names"] or (["검사기가 도중에 멈췄다(요약 줄 없음)"] if not d["verify"]["complete"]
                                             else ["(실패 목록 없음)"])
        for n in names:
            cnt[n] += 1
            last[n] = d["date"]
    if not cnt:
        return '<p class="dim">이 구간에 자기 검증 실패가 없다.</p>'
    rows = "".join(f"<li><b>{n}회</b> · {E(k)} <span class=\"dim\">(마지막 {last[k]})</span></li>"
                   for k, n in cnt.most_common())
    return (f'<h3>이 구간의 검증 실패 사유 — 많은 순</h3><div class="card"><ul class="tight reasons">{rows}</ul>'
            '<p class="dim">이름은 그날 검사기가 쓴 그대로다. 같은 사유가 이어진 것은 그 기간에 그 결함이 고쳐지지 않았다는 뜻이다.</p></div>')


def days_table_html(days: list[dict]) -> str:
    rows = []
    for d in reversed(days):
        v = d["verify"]
        claims = '<span class="dim">—</span>' if not v else (
            f'<span class="tag warn">{v["claims"]}건</span>' if v["claims"] else "0")
        gwrap = ' class="wrap"' if d["gate"] and not all(r["ok"] for r in d["gate"]) else ""  # 막힌 사유는 길다
        rows.append(f"<tr><td>{d['date']}</td><td>{state_cell(d)}</td><td>{verify_cell(d)}</td>"
                    f"<td class=\"num\">{claims}</td><td{gwrap}>{gate_cell(d)}</td><td>{final_cell(d)}</td>"
                    f"<td class=\"num\">{d['built'] or '—'}</td></tr>")
    return "".join(rows)


def _commit_cell(prereg: tuple[str, str], result: tuple[str, str], docs_base: str, times: dict) -> str:
    def one(label, path, h):
        t = times.get(h)
        return (f'{label} <a href="{E(docs_base + path)}"><code>{h}</code></a>'
                + (f' <span class="dim">{t}</span>' if t else ""))
    return one("사전등록", *prereg) + "<br>→ " + one("결과", *result)


def validation_rows(rows, docs_base: str, times: dict, why_label: str = "무엇을 봤나 · 왜") -> str:
    out = []
    for q, verdict, cls, why, prereg, result in rows:
        out.append(f"<tr><td><b>{E(q)}</b></td>"
                   f"<td class=\"verdict {VERDICT_CLS[cls]}\" data-label=\"판정\">{E(verdict)}</td>"
                   f"<td data-label=\"{why_label}\">{why}</td>"
                   f"<td data-label=\"사전등록 → 결과\">{_commit_cell(prereg, result, docs_base, times)}</td></tr>")
    return "".join(out)


def docs_html(docs_base: str, repo: str = REPO) -> str:
    items = [("docs/formulas.md", "화면 값의 수식 정의 — 정본, 지표마다 근거 수준")]
    vdir = os.path.join(repo, "docs", "validation")
    try:
        names = sorted(n for n in os.listdir(vdir) if n.endswith(".md"))
    except OSError:
        names = []
    for n in names:
        first = (_read(os.path.join(vdir, n)) or "").lstrip().splitlines()[:1]
        title = first[0].lstrip("# ").strip() if first else n
        items.append((f"docs/validation/{n}", title))
    return '<ul class="tight">' + "".join(
        f'<li><a href="{E(docs_base + p)}"><code>{E(p)}</code></a> — {E(t)}</li>' for p, t in items) + "</ul>"


def layers_html(layers: list[tuple[str, str]]) -> str:
    if not layers:
        return ' <span class="dim">(최신 VERIFY.txt 가 없어 층 목록을 못 읽었다)</span>'
    return '<ul class="tight">' + "".join(f"<li><b>{E(k)}</b> {E(t)}</li>" for k, t in layers) + "</ul>"


def render(root: str, now: datetime | None = None, docs_base: str = DOCS_BASE, repo: str = REPO,
           window: int = WINDOW) -> str:
    now = now or datetime.now(KST)
    all_days = scan(root)
    days = all_days[-window:]
    latest = latest_dir(root)
    commits = {h for *_, p, r in VALIDATION + CHECKS for h in (p[1], r[1])}
    times = {h: git_time(h, repo) for h in commits}
    slots = {
        "asof": E(latest or "없음"),
        "generated": now.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
        "n_days": str(len(all_days)),
        "window": str(len(days)),
        "tiles": tiles_html(days),
        "strip": strip_html(days),
        "fail_reasons": fail_reasons_html(days),
        "days_table": days_table_html(days) or '<tr><td colspan="7">리포트 폴더가 없다</td></tr>',
        "layers": layers_html(verify_layers(root, latest)),
        "docs": docs_html(docs_base, repo),
        "validation": validation_rows(VALIDATION, docs_base, times),
        "checks": validation_rows(CHECKS, docs_base, times, "내용 · 조치"),
    }
    with open(TEMPLATE, encoding="utf-8") as f:
        page = f.read()
    for k, v in slots.items():
        page = page.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{\w+\}\}", page)
    if left:
        raise ValueError(f"템플릿 자리표시자가 남았다: {left}")
    return page


def banned_hits(page: str) -> list[str]:
    return [w for w in BANNED if w in page]


def default_out(root: str) -> str:
    d = latest_dir(root)
    return os.path.join(root, d, "trust.html") if d else os.path.join(root, "trust.html")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.path.join(REPO, "reports"), help="리포트 폴더(읽기만 한다)")
    ap.add_argument("--out", help="쓸 파일(기본: <root>/latest/trust.html)")
    ap.add_argument("--docs-base", default=DOCS_BASE, help="docs/ 링크 앞부분")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    page = render(root, docs_base=a.docs_base)
    hits = banned_hits(page)
    if hits:
        print(f"페이지에 쓰지 않을 말이 들어갔다 {hits} — 쓰지 않는다. 출처(검사 이름 등)를 고쳐라.", file=sys.stderr)
        return 2
    out = a.out or default_out(root)
    tmp = f"{out}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
