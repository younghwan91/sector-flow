#!/usr/bin/env python3
"""검증·품질 페이지 — 리포트 폴더의 기록에서 ``trust.html`` 을 만든다. 표준 라이브러리와 금칙어 표만, DB 를 안 읽는다.

    uv run python scripts/trust_page.py --root reports --build reports/2026-11-06.building.123 --asof 2026-11-06
    uv run python scripts/trust_page.py --root reports --out /tmp/x/trust.html       # 시험·미리 보기

싣는 것(네 절):

1. **일일 파이프라인 품질** — 기준일 폴더마다 잠정/확정/재구성(``REBUILT.txt`` 의 ``finalize:`` 포함), **첫 게시의**
   자기 검증 결과(원장, 없으면 보관한 원본)와 **지금 판의** ``VERIFY.txt`` 요약(통과/전체·문서 주장 어긋남), 완전성 관문 수치
   (``INCOMPLETE.txt`` 또는 ``cron.log`` 의 관문 줄), 잠정→확정 변경 칸 수(``FINAL.txt``). 전 기준일을 띠와 표로.
   SLO 준수(최근 30일)와 정정 목록은 운영 사건 원장(``events.jsonl``, ``scripts/ops_ledger.py``)으로 센다 —
   원장에 게시 사건이 아직 없으면(도입 전) 정정은 폴더 표시에서 세고 그렇게 적는다. 폴더 표시는 바꿔 끼우기가
   지우므로 원장이 정본이다(``docs/slo.md``). 정정마다 시장 합계의 전후 값과 원인을 싣는다 — ``.archive/`` 의 원본과
   지금 payload 에서 계산한다(:func:`errata_size`·:func:`errata_cause`).
2. **방법** — 관문·자기 검증 층(최신 ``VERIFY.txt`` 에서 읽는다)·확정 모드·사전등록, 문서 사본 링크.
3. **검증 결과** — :data:`VALIDATION`·:data:`CHECKS`. 불리한 결과(증거 없음·판정 불가·쓸 수 없음·불합격)를 뺀 것이 없다.
   사전등록 커밋이 결과 커밋보다 앞선다는 것은 ``tests/test_trust_page.py`` 가 git 으로 확인한다. 페이지는 그 순서와
   간격만 말한다 — 커밋 시각은 '분석 전에 고정' 의 증명이 아니다.
4. **알려진 한계** — 템플릿에 고정.

수익률·성과 숫자는 싣지 않는다. 금칙어(``sectorflow.wording``)가 페이지에 들어가면(예: 검사 이름에 섞여 들어오면) 쓰지 않고 2 로 끝난다.

**어디에 쓰나.** 웹(``serve_reports.py``)은 기준일 폴더 안의 허용 목록 파일만 내보낸다. 배치(``daily_report.sh``)가
**짓는 폴더 안에서**(``--build``) 만들어 리포트와 함께 바꿔 낀다 — 게시된 폴더의 파일 하나를 고쳐 쓰는 길은 없다
(``--out`` 이 ``<root>/<기준일>/`` 안이면 2 로 끝난다). 저장소가 비공개라 GitHub 링크는 익명에게 404 다 — 그래서 문서
사본을 ``validation/`` 에 같이 두고(:func:`export_docs`) 거기로 잇는다. 지난 기준일을 재구성하면 그 폴더의 페이지만 바뀌고
``/latest/trust.html`` 은 다음 배치 때 따라온다.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from sectorflow.wording import page_hits

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO, "scripts", "templates", "trust.html")


def _load_ops_ledger():
    # 같은 폴더의 ops_ledger.py — 원장 읽기·SLO 통계·정정 목록의 정본을 그대로 쓴다(수식 복제 금지).
    import importlib.util
    spec = importlib.util.spec_from_file_location("_trust_ops_ledger",
                                                  os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                               "ops_ledger.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ops = _load_ops_ledger()
KST = timezone(timedelta(hours=9))
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: 문서 사본을 두는 하위 폴더 — serve_reports.DOCS_DIR 와 같아야 한다(tests/test_trust_page.py 가 HTTP 로 연다).
DOCS_DIR = "validation"

SUMMARY_RE = re.compile(r"^검사 (\d+)건 · 실패 (\d+)건(?: · 문서 주장 어긋남 (\d+)건)?\s*$", re.M)
FAIL_LIST_RE = re.compile(r"^실패 목록: (.+)$", re.M)
MARK_RE = re.compile(r"^\s*\[(OK  |FAIL|OK   |CLAIM)\] ", re.M)
LAYER_RE = re.compile(r"^([A-Z][0-9]?)\. (.+)$", re.M)
GATE_HEAD_RE = re.compile(r"^완전성 관문 — 기준일 (\d{4}-\d{2}-\d{2})")
GATE_LINE_RE = re.compile(r"^\s+\[(PASS|FAIL)\] ([^:]+): (.*)$")
#: 시장 목록이 통째로 빈 날 — ``evaluate`` 가 시장 이름 없이 ``[FAIL] <기준일> 시장 목록이 비었다`` 를 낸다.
GATE_EMPTY_RE = re.compile(r"^\s+\[FAIL\] (\d{4}-\d{2}-\d{2}) (.+)$")
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
        e = GATE_EMPTY_RE.match(line) if cur is not None else None
        if e:
            cur.append({"market": "전체", "ok": False, "text": e.group(2).strip(), "min": None})
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


def rebuilt_fields(path: str) -> dict:
    """``REBUILT.txt`` 의 ``key: 값`` 줄들(``commit:``·``finalize:`` — daily_report.sh 재구성이 쓴다)."""
    out: dict[str, str] = {}
    for line in (_read(path) or "").splitlines():
        k, sep, v = line.partition(":")
        if sep and k.strip() in ("commit", "finalize"):
            out[k.strip()] = v.strip()
    return out


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


def archive_dirs(root: str) -> dict[str, list[str]]:
    """``.archive/`` 의 옛 게시본 폴더 — 기준일 → 경로들(오래된 판 먼저, payload 의 mtime 순).

    꼴은 셋이다: ``<기준일>-provisional`` · ``<기준일>.<시각>``(daily_report.sh 재구성) · ``<묶음>/<기준일>``(일괄 재구성 전 보관).
    로그 묶음(``rebuild-…-logs``)처럼 리포트가 없는 폴더는 세지 않는다.
    """
    base = os.path.join(root, ".archive")
    found: dict[str, list[str]] = {}

    def add(asof: str, path: str) -> None:
        if os.path.exists(os.path.join(path, "payload.json")) or os.path.exists(os.path.join(path, "numbers.html")):
            found.setdefault(asof, []).append(path)

    try:
        names = sorted(os.listdir(base))
    except OSError:
        return {}
    for n in names:
        p = os.path.join(base, n)
        if not os.path.isdir(p):
            continue
        if DATE_RE.match(n[:10]):
            add(n[:10], p)
            continue
        try:
            for q in sorted(os.listdir(p)):
                if DATE_RE.match(q) and os.path.isdir(os.path.join(p, q)):
                    add(q, os.path.join(p, q))
        except OSError:
            continue

    def born(path: str) -> float:
        for f in ("payload.json", "numbers.html"):
            try:
                return os.path.getmtime(os.path.join(path, f))
            except OSError:
                pass
        return float("inf")
    return {d: sorted(ps, key=born) for d, ps in found.items()}


def scan_day(root: str, d: str, gates: dict, path: str | None = None) -> dict:
    p = path or os.path.join(root, d)
    has = {f: os.path.exists(os.path.join(p, f)) for f in (
        "numbers.html", "payload.json", "VERIFY.txt", "VERIFY_FAILED.txt", "CLAIMS.txt",
        "FINAL.txt", "REBUILT.txt", "INCOMPLETE.txt")}
    day = {"date": d, "verify": None, "verify_status": "unknown", "gate": None, "gate_src": None,
           "final": None, "built": None, "rebuilt": None}
    if has["REBUILT.txt"]:
        day["rebuilt"] = rebuilt_fields(os.path.join(p, "REBUILT.txt"))
    if not has["numbers.html"]:
        day["state"] = "수집 미완" if has["INCOMPLETE.txt"] else "빈 폴더"
    elif has["FINAL.txt"]:
        # 재구성의 확정은 ``finalize: verified`` 가 있어야 확인된 것이다. 그 줄이 없는 옛 재구성(3617962, 09-02·09-28)은
        # FINAL.txt 가 있어도 그날을 덮은 확정 실행을 확인한 기록이 없다(serve_reports 와 같은 말).
        fin = None if day["rebuilt"] is None else day["rebuilt"].get("finalize")
        day["state"] = "확정" if day["rebuilt"] is None or fin == "verified" else "확정 미확인"
    elif day["rebuilt"] is not None and day["rebuilt"].get("finalize") == "unverified":
        # daily_finalize 창 밖의 재구성 — 마감 값이지만 확정 기록이 없다(serve_reports 와 같은 말).
        day["state"] = "확정 기록 없음"
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


def scan(root: str, build: tuple[str, str] | None = None) -> list[dict]:
    """기준일 오름차순. ``build=(기준일, 짓는 폴더)`` 면 그 날은 짓는 폴더를 읽는다 — 곧 바꿔 낄 판이 그날의 지금 판이다."""
    gates = parse_gate(_read(os.path.join(root, "cron.log")) or "")
    arc = archive_dirs(root)
    names = report_dirs(root)
    if build and build[0] not in names:
        names = sorted([*names, build[0]])
    days = []
    for d in names:
        day = scan_day(root, d, gates, build[1] if build and build[0] == d else None)
        day["archived"] = len(arc.get(d, ()))
        days.append(day)
    return days


# ─────────────────────────────────────────────── 첫 게시 — 바꿔 끼우기가 지운 결과를 되찾는다

def first_publications(events: list[dict]) -> dict[str, dict]:
    """기준일 → 재구성 아닌 가장 이른 게시 사건(원장). 재구성은 '첫 게시' 가 아니다."""
    out: dict[str, tuple] = {}
    for e in events:
        asof = e.get("asof")
        if not (isinstance(asof, str) and DATE_RE.match(asof)):
            continue
        if not ops.is_publication(e) or e.get("mode") == "rebuild":
            continue
        t = ops.parse_ts(e.get("ts"))
        if t is not None and (asof not in out or t < out[asof][0]):
            out[asof] = (t, e)
    return {d: v[1] for d, v in out.items()}


def _archived_verify(path: str) -> dict | None:
    failed = _read(os.path.join(path, "VERIFY_FAILED.txt"))
    if failed is not None:
        v = parse_verify(failed)
        return v | {"status": "fail"}
    txt = _read(os.path.join(path, "VERIFY.txt"))
    if txt is None:
        return None
    v = parse_verify(txt)
    return v | {"status": "fail" if v["fails"] or not v["complete"] else "pass"}


def attach_first(days: list[dict], events: list[dict], arc: dict[str, list[str]]) -> None:
    """``day["first"]`` — 그날 첫 게시의 자기 검증 결과와 출처. 원장이 정본이고, 없으면 보관한 원본, 재구성이 아니면 지금 판.

    재구성한 날의 ``VERIFY.txt`` 는 나중 검사기가 나중 데이터로 돈 결과다 — 그것만 보면 첫 게시 때 걸린 날이 없어 보인다.
    """
    pubs = first_publications(events)
    for d in days:
        origin = arc.get(d["date"], [])
        orig = _archived_verify(origin[0]) if origin else None
        e = pubs.get(d["date"])
        if e is not None and e.get("verify") in ("pass", "fail"):
            names = orig["fail_names"] if orig and orig["status"] == "fail" else []
            complete = orig["complete"] if orig and orig["status"] == "fail" else True
            d["first"] = {"status": e["verify"], "src": "원장", "fail_names": names, "complete": complete,
                          "fails": e.get("verify_fails") if e.get("verify_fails") is not None else
                          (orig["fails"] if orig and orig["status"] == "fail" else None),
                          "backfill": e.get("source") == "backfill"}
        elif orig is not None:
            d["first"] = {"status": orig["status"], "src": "보관 원본", "fail_names": orig["fail_names"],
                          "complete": orig["complete"], "fails": orig["fails"], "backfill": False}
        elif d["rebuilt"] is None and d["verify"] is not None:
            st = "fail" if d["verify_status"] == "fail" else "pass"
            d["first"] = {"status": st, "src": "지금 판", "fail_names": d["verify"]["fail_names"],
                          "complete": d["verify"]["complete"], "fails": d["verify"]["fails"], "backfill": False}
        elif d["state"] == "수집 미완":
            d["first"] = {"status": "incomplete", "src": "폴더", "fail_names": [], "complete": True, "fails": None,
                          "backfill": False}
        else:
            d["first"] = {"status": "unknown", "src": None, "fail_names": [], "complete": True, "fails": None,
                          "backfill": False}


# ─────────────────────────────────────────────── 정정의 크기 — 원본 payload 와 지금 payload 의 시장 합계

#: 시장 합계를 비교하는 주체(합 ≈ 0 이라 넷 다) — 거래대금은 따로 적는다.
NET_ACTORS = (("inst", "기관"), ("forgn", "외국인"), ("indiv", "개인"), ("etc", "기타법인"))
#: 정정 칸에 부호가 뒤집힌 칸을 더 적을 때의 문턱[억] — 0 근처의 뒤집힘은 뉴스가 아니다.
FLIP_MIN_EOK = 100.0
#: 정정 칸에 적는 순매수 줄 수 상한.
SIZE_ROWS = 3
#: "다음날 게시본에 이미 같은 값" 의 허용 차 — 그 시장 거래대금의 몫. 문턱 바닥은 :data:`SAME_FLOOR_EOK`.
SAME_TV_SHARE = 0.001
SAME_FLOOR_EOK = 20.0
#: 시장 합계 변화(칸마다 |후−전| 의 합)의 이 몫 이상을 다음 판이 이미 설명하면 그 원인으로 본다.
EXPLAINED_SHARE = 0.9
#: 그날 거래대금이 나중 값의 이 몫 미만이면 수집 미완 중 게시로 본다. 나중 값이 :data:`INCOMPLETE_MIN_EOK` 미만이면
#: 비율을 내지 않는다(분모 게이트 — 작은 시장의 흔들림을 수집 미완이라 부르지 않게).
INCOMPLETE_SHARE = 0.5
INCOMPLETE_MIN_EOK = 1000.0


def market_totals(payload: dict, asof: str) -> dict | None:
    """payload ``flows`` 의 그날 섹터 값을 시장별로 더한다 — {시장: {주체|"tv": 억}}. 옛 형식에도 ``flows`` 는 있다."""
    dates = payload.get("dates") or []
    if asof not in dates:
        return None
    i = dates.index(asof)
    out: dict[str, dict[str, float]] = {}
    for mkt, secs in (payload.get("flows") or {}).items():
        tot: dict[str, float] = {}
        for row in secs.values():
            for k in (*(a for a, _ in NET_ACTORS), "tv"):
                vals = row.get(k)
                if vals and i < len(vals) and vals[i] is not None:
                    tot[k] = tot.get(k, 0.0) + vals[i]
        out[mkt] = tot
    return out


def _load_payload(folder: str) -> dict | None:
    try:
        with open(os.path.join(folder, "payload.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def errata_cause(before: dict, after: dict, next_view: dict | None) -> tuple[str, str]:
    """(원인 코드, 원인 글) — 기록으로 가를 수 있는 만큼만 말한다.

    ``next_view`` = 다음 거래일 **원본** 게시본이 본 같은 기준일 값. 코드가 거의 같은 하루 뒤의 판이다. 그것이 지금 값과 같으면
    바뀐 것은 코드가 아니라 그 사이 DB 값(잠정치→확정치)이다.
    """
    short = []
    for m, a in after.items():
        tv, was = a.get("tv"), before.get(m, {}).get("tv")
        if tv is not None and tv >= INCOMPLETE_MIN_EOK and (was or 0) < INCOMPLETE_SHARE * tv:
            short.append(f"{m} 거래대금이 나중 값의 {(was or 0) / tv * 100:.1f}%")
    if short:
        return "incomplete", "수집이 덜 끝난 채 게시 — " + " · ".join(short)

    def gap(x: dict, y: dict) -> float:
        return sum(abs(v - y.get(m, {}).get(k, 0.0)) for m, a in x.items() for k, v in a.items())

    def same(x: dict, y: dict) -> bool:
        return all(abs(v - y.get(m, {}).get(k, 0.0)) <= max(SAME_FLOOR_EOK, SAME_TV_SHARE * abs(a.get("tv") or 0))
                   for m, a in x.items() for k, v in a.items())
    if same(after, before):
        return "same", "시장 합계는 그대로 — 재구성에서 바뀐 것은 합계 아래(섹터·정의·표시)다"
    if next_view is None:
        return "unknown", "못 가름 — 다음 원본 게시본이 없다"
    total = gap(after, before)                         # same 이 아니면 0 보다 크다
    if gap(after, next_view) <= (1 - EXPLAINED_SHARE) * total:
        return "provisional", "잠정치 → 확정치"
    if gap(next_view, before) <= (1 - EXPLAINED_SHARE) * total:
        return "later", "나중 코드·정의 또는 뒤늦은 정정"
    return "mixed", "잠정치 → 확정치 + 나중 코드·정정"


def _signed(v: float) -> str:
    return f"{v:+,.0f}".replace("-", "−")


def errata_size(before: dict, after: dict) -> dict:
    """전후 값 — 순매수는 가장 크게 바뀐 칸과 부호가 뒤집힌 칸, 거래대금은 시장마다."""
    cells = []
    for m, a in after.items():
        for k, ko in NET_ACTORS:
            if k not in a:
                continue
            o, n = before.get(m, {}).get(k, 0.0), a[k]
            flip = o * n < 0 and max(abs(o), abs(n)) >= FLIP_MIN_EOK
            cells.append({"market": m, "actor": ko, "before": o, "after": n, "delta": abs(n - o), "flip": flip})
    cells.sort(key=lambda c: -c["delta"])
    keep = cells[:1] + [c for c in cells[1:] if c["flip"]]
    tv = [{"market": m, "before": before.get(m, {}).get("tv", 0.0), "after": a["tv"]}
          for m, a in after.items() if "tv" in a]
    return {"net": keep[:SIZE_ROWS], "tv": tv}


def errata_sizes(days: list[dict], arc: dict[str, list[str]], root: str, build: tuple[str, str] | None) -> dict[str, dict]:
    """기준일 → {"origin": 원본 폴더 이름, "size": ..., "cause": (코드, 글)} — 원본과 지금 판이 둘 다 있는 날만."""
    dated = sorted(arc)
    views: dict[str, dict] = {}                        # 원본 payload 는 비싸다 — 다음날 칸에서 다시 쓴다

    def origin_payload(d: str) -> dict | None:
        if d not in views:
            for path in arc.get(d, []):
                if (P := _load_payload(path)) is not None:
                    # 전체를 들고 있으면 48일 × 3MB 다 — 다음날 칸에 필요한 끝 며칠만 남긴다.
                    keep = (P.get("dates") or [])[-10:]
                    views[d] = {"path": path, "totals": {x: market_totals(P, x) for x in keep}}
                    break
            else:
                views[d] = None
        return views[d]

    out = {}
    for day in days:
        d = day["date"]
        o = origin_payload(d) if d in arc else None
        before = o["totals"].get(d) if o else None
        if not before:
            continue
        now_dir = build[1] if build and build[0] == d else os.path.join(root, d)
        cur = _load_payload(now_dir)
        after = market_totals(cur, d) if cur else None
        if not after:
            continue
        nxt = next((x for x in dated if x > d), None)
        nview = origin_payload(nxt) if nxt else None
        next_view = nview["totals"].get(d) if nview else None
        if next_view is None:
            # 마지막 날엔 다음날 원본이 없다 — 같은 기준일을 뒤에 다시 만든 판(잠정 뒤의 확정본)이 있으면 그것과 견준다.
            for later in arc[d][1:]:
                if (P := _load_payload(later)) is not None:
                    next_view = market_totals(P, d)
                    break
        out[d] = {"origin": os.path.relpath(o["path"], os.path.join(root, ".archive")),
                  "size": errata_size(before, after), "cause": errata_cause(before, after, next_view)}
    return out


def folder_errata(days: list[dict]) -> list[dict]:
    """원장에 게시 사건이 없을 때의 대안 — 폴더 표시에서 '첫 게시 뒤 숫자가 바뀐 날' 을 센다.

    재구성(``REBUILT.txt``)과 섹터 칸이 바뀐 확정(``FINAL.txt`` 의 diff>0)만 보이고, 바꿔 끼우며 지워진
    흔적은 못 본다 — 그래서 페이지가 출처를 같이 적는다.
    """
    rows = []
    for d in days:
        if d["rebuilt"] is not None:
            commit = (d["rebuilt"].get("commit") or "?")[:12]
            fin = d["rebuilt"].get("finalize")
            rows.append({"asof": d["date"], "kind": "재구성",
                         "detail": f"commit {commit}" + (f" · finalize {fin}" if fin else ""), "backfill": False})
        f = d["final"]
        if f and f.get("kind") == "diff" and f["sector"][0] > 0 and d["rebuilt"] is None:
            rows.append({"asof": d["date"], "kind": "확정 정정", "detail": f"섹터 칸 {f['sector'][0]}개 변경",
                         "backfill": False})
    return rows


def ledger_report_events(events: list[dict]) -> list[dict]:
    """원장에서 게시 이력을 말하는 사건 — report·archive. 프로브·잡만 있으면 이력으로는 빈 원장이다."""
    return [e for e in events if e.get("event") in ("report", "archive")]


def verify_layers(folder: str | None) -> list[tuple[str, str]]:
    """최신 ``VERIFY.txt`` 의 층 머리줄 — 방법 절이 지금 검사기를 말하게."""
    t = _read(os.path.join(folder, "VERIFY.txt")) if folder else None
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

#: 행 하나 = 질문 하나. ``id`` 는 페이지의 ``data-qid`` 이고 테스트가 행마다 이 값으로 찾는다(행을 지우면 빨개진다).
#: ``links`` = [(연구 이름, (사전등록 파일, 커밋) 또는 None, (결과 파일, 커밋 또는 None))] — None 커밋은 지금 HEAD 의 문서.
#: 등급: none = 증거 없음 · bad = 불합격·쓸 수 없음 · mixed = 판정 불가·조건부 · ok = 통과
Q3D = ("Q3d 재검", (PREREG_Q3D, "6ca326a"), (RESULTS, "8385ae1"))
Q123 = ("지표 검증 Q1~Q3", (PREREG, "bd27017"), (RESULTS, "dae572f"))
USE = ("수급 쓸모", (PREREG_USE, "b702dc4"), (RESULTS_USE, "cd9c4a2"))
FORMULAS = ("근거 수준 표", None, ("docs/formulas.md", None))

VALIDATION = [
    {"id": "Q3d-sector", "q": "섹터 임펄스·가속·x(20일)의 앞날 관계 — h5·h20, 6칸", "verdict": "증거 없음", "cls": "none",
     "why": "6칸 모두 사전등록 기준(널 p&lt;0.05 + 전·후반 같은 부호)에 못 미쳤다. h5 는 |IC| 약 0.035 이상을 대략 배제하지만 "
            "h20 은 0.05~0.08 아래를 배제하지 못한다 — 있어도 이 표본으로는 못 본다. 결함을 고친 정의로 다시 쟀고 첫 실행도 같은 결론이었다.",
     "links": [Q3D]},
    {"id": "Q3d-G", "q": "선정 점수 G의 앞날 관계 — h5·h20", "verdict": "판정 불가", "cls": "mixed",
     "why": "관문 통과 섹터가 5개 이상인 날이 h5 82일·h20 19일뿐이라 검정력이 없다. 순위를 매기는 모집단이 날짜당 2~7개(중앙 2)라 "
            "2개인 날은 순위가 0 아니면 1 이다. 잠정치에도 흔들린다(아래 Q3a 불합격). 화면은 G 를 탐색 모드(<code>e</code>)에만 두고 [판정 불가: G] 표지를 단다.",
     "links": [Q3D, Q123]},
    {"id": "x", "q": "미실현 x 는 무엇을 재나", "verdict": "사실상 섹터 20일 반전", "cls": "mixed",
     "why": "x 와 −Δv(같은 구간 섹터 수익률의 반대)의 순위상관이 0.82~0.93 이다(Q3c). 앞날 관계에서 h20 의 약한 관계도 수급이 아니라 "
            "과거 20일 수익률만으로 같게 나오고, 8칸 다중검정 기준(t≈2.7)에 못 미친다(Q3d 재검).",
     "links": [Q123, Q3D]},
    {"id": "S1-S3", "q": "섹터 수급(S1 투신+연기금·S2 외국인 20일, S3 기관 60일)의 앞날 관계 — 6칸", "verdict": "증거 없음",
     "cls": "none",
     "why": "발견 구간(2018~2022)에서 여섯 칸 모두 탈락해 확인 단계로 가지 못했다. 화면의 주 단위인 섹터 수급은 앞날 수익률에 대한 증거가 없다.",
     "links": [USE]},
    {"id": "K1", "q": "종목 투신+연기금 20일 순매수 ÷ 시총 — 섹터 안 순위(K1)",
     "verdict": "음(−)의 순위 정보 확인 · 쓸 수 있는 형태는 못 보임", "cls": "bad",
     "why": "섹터 안 순위 IC 의 부호가 음으로 확인됐다(확인 구간 t≈−6, 9개 연도 같은 부호). 그러나 쓸 수 있는 형태는 하나도 보이지 않았다: "
            "순매수 상위를 피하는 쓰임은 확인 구간에서 역효과였다. 순매수(K1&gt;0) 종목만 보면 확인 구간 IC 는 증거 없음이다. "
            "순매도 상위만 사는 쓰임은 왕복 비용을 빼면 발견 구간에서 진다. 5분위 차는 롱숏 숫자라 공매도 없는 계좌에서 실현할 수 없다. "
            "정보가 순매도 쪽에 몰리는 모양이 발견 구간과 달랐고, 절반가량은 단기 반전과 겹치며, 생존편향이 결론 쪽으로 부풀렸을 수 있다. "
            "화면의 종목 선정 축(큰 순매수를 좋게 본다)은 이 부호와 반대다.",
     "links": [USE]},
    {"id": "K2-K3", "q": "종목 기관 합계(K2)·외국인(K3) 20일", "verdict": "미확인 · 탈락", "cls": "none",
     "why": "K2 는 발견에서 통과했으나 확인 구간에서 유의수준(0.0125)을 넘지 못했다. K3 는 발견에서 탈락했다.",
     "links": [USE]},
    {"id": "untested", "q": "1년%ile · 추이 · 풀림 ẍ · dW/dt, 종목 선정 · 곱", "verdict": "검정된 적 없음", "cls": "none",
     "why": "앞날 수익률에 대해 따로 검정한 적이 없다. 1년%ile 은 그날 값이 지난 1년 분포의 어디인지 재는 측정값이지 앞날 지표가 아니다 — "
            "앞날 검정을 하지 않았다. ẍ 는 G 의 입력이다. 종목 선정의 투신+연기금 축은 K1 의 부호와 반대 방향으로 점수를 준다.",
     "links": [FORMULAS]},
]

CHECKS = [
    {"id": "Q1", "q": "Q1 데이터 — 게시본 값 = DB 재계산", "verdict": "불합격(원인 설명됨)", "cls": "bad",
     "why": "11-06 셀 99.735% 일치(합격선 99.9%). 넘는 셀은 전부 기준일 잠정치였다. 과거 게시본 셋은 그 뒤 주식수 정정으로 불합격, "
            "미해명 16셀(최대 0.026%p). 표본 15건 손검산은 15/15 일치.",
     "links": [Q123]},
    {"id": "Q2", "q": "Q2 계산 — 문서 정의만으로 다시 계산", "verdict": "불합격 → 결함 셋은 같은 날 고침 · 둘은 남김", "cls": "bad",
     "why": "문서가 코드와 달랐고 결함이 나왔다. 같은 날 고친 것: 통과 섹터가 하나면 G 가 사라짐, 섹터 수익률을 이어 쓸 수 없는 등락률로 만듦, "
            "시총을 수정주가 × 늦은 주식수로 만듦(검사·변이를 붙였다). 그때 남긴 것: PDF 문서의 G 식이 관문을 적지 않음(지금 정본은 "
            "<code>docs/formulas.md</code>), 시장에 없는 섹터의 가속이 결측이 아니라 0.0(화면은 이미 걸러 영향 없음).",
     "links": [Q123]},
    {"id": "Q3a", "q": "Q3a 잠정치 민감도 — 게시본 vs 확정치", "verdict": "가속 통과 · G 불합격", "cls": "bad",
     "why": "가속 순위 ρ 중앙 0.999. G 는 세 시장 통과 62.5%(합격선 90%). 그래서 다음날 08:20 확정 모드를 만들었다.",
     "links": [Q123]},
    {"id": "Q3b", "q": "Q3b 금액 근사(수량 × 종가)", "verdict": "거래소 통과 · 코스닥 불안정", "cls": "mixed",
     "why": "48일 중 상대차 중앙 5% 이내: 거래소 100%, 전체 98%, 코스닥 71%.",
     "links": [Q123]},
    {"id": "Q3c", "q": "Q3c 강성 k 추정 안정성", "verdict": "약함(기술 통계)", "cls": "mixed",
     "why": "k 의 |t|&lt;2 비율이 20일·전체에서 42%(겹침)·47%(비겹침), 코스닥은 60~70% 다. t 는 등분산 표준오차, 표본 22~27섹터. "
            "k 가 얇은 섹터에 크게 흔들리던 것은 같은 날 적합 표본에서 10종목 미만 섹터를 빼 고쳤다.",
     "links": [Q123]},
]


def git_info(commit: str, repo: str = REPO) -> tuple[str, int] | None:
    """(커밋 시각 KST 분까지, epoch). git 이 없거나 해시가 없으면 None — 페이지는 해시만 싣는다."""
    try:
        out = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%ct", commit],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    ts = int(out.stdout.strip())
    return datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d %H:%M"), ts


def prereg_gaps(info: dict) -> list[int]:
    """사전등록 커밋 → 결과 커밋 간격[분], 연구마다."""
    gaps = []
    for _label, pre, res in {lk[0]: lk for r in VALIDATION + CHECKS for lk in r["links"]}.values():
        if pre and res[1] and info.get(pre[1]) and info.get(res[1]):
            gaps.append(round((info[res[1]][1] - info[pre[1]][1]) / 60))
    return gaps


# ─────────────────────────────────────────────── 그리기

STATE_TAG = {"확정": "ok", "확정 미확인": "prov", "잠정": "prov", "확정 기록 없음": "prov", "수집 미완": "warn",
             "빈 폴더": "warn"}
GLYPH = {"pass": "✓", "claim": "!", "fail": "✕", "incomplete": "…", "unknown": "?"}
STATUS_KO = {"pass": "검증 통과", "claim": "검증 통과 · 문서 주장 어긋남", "fail": "검증 실패",
             "incomplete": "수집 미완", "unknown": "검증 기록 없음"}
FIRST_KO = {"pass": "검증 통과", "fail": "검증 실패", "incomplete": "수집 미완", "unknown": "기록 없음"}
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
    if day["state"] not in ("확정", "확정 미확인") or f is None:
        return '<span class="dim">—</span>'
    if f["kind"] == "diff":
        s = f"섹터 {f['sector'][0]:,}/{f['sector'][1]:,}칸"
        if f["market"]:
            s = f"시장 {f['market'][0]}/{f['market'][1]} · " + s
        return f'<a href="/{day["date"]}/FINAL.txt">{s}</a>'
    if f["kind"] == "none":
        return f'<span class="dim" title="{E(f["reason"])}">비교 없음</span>'
    return '<span class="dim">기록 없음(비교 기능 이전)</span>'


def first_cell(day: dict) -> str:
    """첫 게시의 자기 검증 — 지금 판과 따로. 재구성한 날이면 이 칸만이 그때 걸렸는지를 말한다."""
    f = day.get("first") or {"status": "unknown", "src": None}
    src = f.get("src")
    tip = f"출처: {src}" + (" · 백필 복원" if f.get("backfill") else "") if src else ""
    if f["status"] == "fail":
        names = ", ".join(f.get("fail_names") or [])
        if not f.get("complete", True):
            label = "실패 · 검사기 중단"
        elif f.get("fails"):
            label = f'실패 {f["fails"]}'
        else:
            label = "실패"
        return f'<span class="tag bad" title="{E(tip + (" · " + names if names else ""))}">{label}</span>'
    if f["status"] == "pass":
        return f'<span class="tag ok" title="{E(tip)}">통과</span>'
    if f["status"] == "incomplete":
        return '<span class="dim">안 만듦</span>'
    return '<span class="dim" title="원장·보관 원본 어디에도 없다">기록 없음</span>'


def state_cell(day: dict) -> str:
    st = day["state"]
    href = {"확정": "FINAL.txt", "확정 미확인": "REBUILT.txt", "확정 기록 없음": "REBUILT.txt",
            "수집 미완": "INCOMPLETE.txt"}.get(st)
    tag = f'<span class="tag {STATE_TAG[st]}">{st}</span>'
    if href:
        tag = f'<a class="tag {STATE_TAG[st]}" href="/{day["date"]}/{href}">{st}</a>'
    if day["rebuilt"] is not None:
        commit = (day["rebuilt"].get("commit") or "?")[:12]
        tag += (f' <a class="tag rebuilt" href="/{day["date"]}/REBUILT.txt" '
                f'title="나중 코드({commit})·확정 데이터로 다시 만든 날 — 그날 저녁 게시본이 아니다">재구성</a>')
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


def first_strip_html(days: list[dict]) -> str:
    cells = []
    for day in days:
        st = (day.get("first") or {}).get("status", "unknown")
        tip = f"{day['date']} · 첫 게시 · {FIRST_KO[st]}"
        cells.append(f'<span class="cell {st}" title="{E(tip)}" aria-label="{E(tip)}">{GLYPH[st]}</span>')
    return f'<div class="strip first" role="list">{"".join(cells)}</div>'


def tiles_html(days: list[dict]) -> str:
    c = Counter(d["verify_status"] for d in days)
    s = Counter(d["state"] for d in days)
    first = Counter((d.get("first") or {}).get("status", "unknown") for d in days)
    items = [
        (len(days), "기준일 폴더"),
        (first["fail"], "첫 게시 검증 실패"),
        (first["unknown"], "첫 게시 기록 없음"),
        (c["pass"] + c["claim"], "지금 판 검증 통과"),
        (c["fail"], "지금 판 검증 실패"),
        (c["claim"], "통과 · 문서 주장 어긋남"),
        (c["incomplete"], "수집 미완(안 만듦)"),
        (s["확정"], "확정"), (s["확정 미확인"], "확정 미확인"), (s["잠정"], "잠정"), (s["확정 기록 없음"], "확정 기록 없음"),
        (sum(1 for d in days if d["rebuilt"] is not None), "재구성"),
    ]
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><b>{n}</b><span>{E(k)}</span></div>' for n, k in items) + "</div>"


def fail_reasons_html(days: list[dict]) -> str:
    """첫 게시와 지금 판에서 걸린 사유 — 하루에 같은 이름은 한 번. 지금 판만 세면 재구성이 그때의 실패를 지운다."""
    cnt: Counter = Counter()
    last: dict[str, str] = {}
    n_first = 0
    for d in days:
        names: set[str] = set()
        f = d.get("first") or {}
        if f.get("status") == "fail":
            n_first += 1
            names |= set(f.get("fail_names") or (
                ["검사기가 도중에 멈췄다(요약 줄 없음)"] if not f.get("complete", True) else ["(사유 기록 없음 — 원장에 실패만 남았다)"]))
        if d["verify_status"] == "fail" and d["verify"]:
            names |= set(d["verify"]["fail_names"] or (["검사기가 도중에 멈췄다(요약 줄 없음)"]
                                                        if not d["verify"]["complete"] else ["(실패 목록 없음)"]))
        for n in names:
            cnt[n] += 1
            last[n] = d["date"]
    if not cnt:
        return '<p class="dim">이 구간의 첫 게시·지금 판 어디에도 자기 검증 실패 기록이 없다.</p>'
    rows = "".join(f"<li><b>{n}회</b> · {E(k)} <span class=\"dim\">(마지막 {last[k]})</span></li>"
                   for k, n in cnt.most_common())
    return (f'<h3>검증 실패 사유 — 첫 게시 {n_first}일 포함, 많은 순</h3><div class="card"><ul class="tight reasons">{rows}</ul>'
            '<p class="dim">이름은 그날 검사기가 쓴 그대로이고, 첫 게시 사유는 보관한 원본의 <code>VERIFY_FAILED.txt</code> 에서 읽는다. '
            '같은 사유가 이어진 것은 그 기간에 그 결함이 고쳐지지 않았다는 뜻이다.</p></div>')


def days_table_html(days: list[dict]) -> str:
    rows = []
    for d in reversed(days):
        v = d["verify"]
        claims = '<span class="dim">—</span>' if not v else (
            f'<span class="tag warn">{v["claims"]}건</span>' if v["claims"] else "0")
        gwrap = ' class="wrap"' if d["gate"] and not all(r["ok"] for r in d["gate"]) else ""  # 막힌 사유는 길다
        rows.append(f"<tr><td>{d['date']}</td><td>{state_cell(d)}</td><td>{first_cell(d)}</td><td>{verify_cell(d)}</td>"
                    f"<td class=\"num\">{claims}</td><td{gwrap}>{gate_cell(d)}</td><td>{final_cell(d)}</td>"
                    f"<td class=\"num\">{d['built'] or '—'}</td></tr>")
    return "".join(rows)


def export_docs(dest: str, repo: str = REPO) -> dict[tuple[str, str | None], str]:
    """문서 사본을 ``<dest>/validation/`` 에 쓴다 → {(문서 경로, 커밋|None): 링크}. 저장소가 비공개라 GitHub 링크는 익명에게 404 다.

    ``None`` 은 지금 판이고, 커밋이 붙은 칸은 그 커밋 시점의 문서다(``git show``). git 이 없으면 지금 판으로 잇는다 — 페이지가
    "그 커밋 시점" 이라 적으므로 그때는 그 칸의 링크가 어떤 판인지 파일 이름으로 드러난다(커밋이 안 붙는다).
    """
    out = os.path.join(dest, DOCS_DIR)
    os.makedirs(out, exist_ok=True)
    links: dict[tuple[str, str | None], str] = {}
    vdir = os.path.join(repo, "docs", "validation")
    try:
        current = [f"docs/validation/{n}" for n in sorted(os.listdir(vdir)) if n.endswith(".md")]
    except OSError:
        current = []
    for path in ["docs/formulas.md", "docs/slo.md", *current]:
        body = _read(os.path.join(repo, path))
        if body is None:
            continue
        name = os.path.basename(path)
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            f.write(body)
        links[(path, None)] = f"{DOCS_DIR}/{name}"
    for path, commit in sorted({(p, h) for r in VALIDATION + CHECKS for _l, pre, res in r["links"]
                                for p, h in (pre or (None, None), res) if p and h}):
        try:
            got = subprocess.run(["git", "-C", repo, "show", f"{commit}:{path}"], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if got.returncode != 0:
            continue
        name = f"{commit}-{os.path.basename(path)}"
        with open(os.path.join(out, name), "wb") as f:
            f.write(got.stdout)
        links[(path, commit)] = f"{DOCS_DIR}/{name}"
    return links


def _doc_link(links: dict, path: str, commit: str | None, label: str) -> str:
    href = links.get((path, commit)) or links.get((path, None))
    code = f"<code>{E(label)}</code>"
    return f'<a href="{E(href)}">{code}</a>' if href else code


def _links_cell(rows, docs: dict, info: dict) -> str:
    """연구마다 사전등록 → 결과 커밋과 그 간격. 링크는 그 커밋 시점 문서의 사본이다."""
    parts = []
    for label, pre, res in rows:
        rpath, rh = res
        if pre is None:
            parts.append(f'{E(label)} · 사전등록 없음 — 검정한 적 없다 · 출처 {_doc_link(docs, rpath, rh, rpath)}')
            continue
        ppath, ph = pre
        pi, ri = info.get(ph), info.get(rh)
        gap = f' <span class="gap">간격 {round((ri[1] - pi[1]) / 60)}분</span>' if pi and ri else ""
        parts.append(f'{E(label)} · 사전등록 {_doc_link(docs, ppath, ph, ph)}'
                     + (f' <span class="dim">{pi[0]}</span>' if pi else "")
                     + f' → 결과 {_doc_link(docs, rpath, rh, rh)}'
                     + (f' <span class="dim">{ri[0]}</span>' if ri else "") + gap)
    return "<br>".join(parts)


def validation_rows(rows, docs: dict, info: dict, why_label: str = "무엇을 봤나 · 왜") -> str:
    out = []
    for r in rows:
        out.append(f"<tr data-qid=\"{E(r['id'])}\"><td><b>{E(r['q'])}</b> <span class=\"qid\">{E(r['id'])}</span></td>"
                   f"<td class=\"verdict {VERDICT_CLS[r['cls']]}\" data-label=\"판정\">{E(r['verdict'])}</td>"
                   f"<td data-label=\"{why_label}\">{r['why']}</td>"
                   f"<td data-label=\"사전등록 → 결과\">{_links_cell(r['links'], docs, info)}</td></tr>")
    return "".join(out)


def docs_html(docs: dict, repo: str = REPO) -> str:
    items = [("docs/formulas.md", "화면 값의 수식 정의 — 정본, 지표마다 근거 수준"),
             ("docs/slo.md", "운영 SLO 정의·측정법")]
    for p, h in sorted(docs, key=str):
        if h is None and p.startswith("docs/validation/"):
            first = (_read(os.path.join(repo, p)) or "").lstrip().splitlines()[:1]
            items.append((p, first[0].lstrip("# ").strip() if first else p))
    return '<ul class="tight">' + "".join(
        f'<li>{_doc_link(docs, p, None, p)} — {E(t)}</li>' for p, t in items if (p, None) in docs) + "</ul>"


def layers_html(layers: list[tuple[str, str]]) -> str:
    if not layers:
        return ' <span class="dim">(최신 VERIFY.txt 가 없어 층 목록을 못 읽었다)</span>'
    return '<ul class="tight">' + "".join(f"<li><b>{E(k)}</b> {E(t)}</li>" for k, t in layers) + "</ul>"


def _days_list(label: str, ds: list) -> str:
    names = [d if isinstance(d, str) else f"{d['asof']}({d['at']})" for d in ds]
    return f"{label} {len(ds)}일" + (" — " + ", ".join(names) if names else "")


def slo_html(st: dict | None, has_reports: bool, first_fail_days: int = 0, n_days: int = 0) -> str:
    """원장 30일 통계 → SLO 카드. 원장이 없으면 없다고 말한다 — 폴더로 흉내 내지 않는다(흔적이 지워진다).

    프로브(웹 응답)만 쌓인 원장이면 그 줄만 싣고, 게시·확정 준시성은 게시 사건이 쌓인 뒤부터 잰다 —
    빈 집계를 그리면 '늦은 날 0일' 이 '늦은 날 없음' 으로 읽힌다.
    """
    if st is None:
        return ('<div class="card"><p class="dim">운영 사건 원장(<code>events.jsonl</code>)이 아직 없다 — '
                'SLO 준수는 원장이 쌓인 뒤부터 잰다(<code>docs/slo.md</code>). 폴더 표시로는 재지 않는다: '
                '바꿔 끼우기가 흔적을 지워 준시성을 과장하게 된다.</p></div>')
    p, f, pr = st["prov"], st["final"], st["probe"]
    if not has_reports:
        probe = (f"웹 프로브(5분 /healthz): {pr['n']}회 중 200 OK {pr['ok']}회"
                 + (f" ({pr['share']:.1%})" if pr["share"] is not None else "")) if pr["n"] else ""
        return ('<div class="card"><p class="dim">원장에 아직 게시 사건이 없다 — 게시·확정 준시성은 게시 사건이 '
                '쌓인 뒤부터 잰다(<code>docs/slo.md</code>). 폴더 표시로는 재지 않는다: 바꿔 끼우기가 흔적을 '
                '지워 준시성을 과장하게 된다.</p>'
                + (f'<p>{probe} — 서버가 내려가 있던 주기는 줄 자체가 없어 가용률이 아니라 응답률이다.</p>' if probe else "")
                + '</div>')
    lines = [
        f"<li><b>잠정 게시(당일 {ops.PROV_SLO_KST:%H:%M} KST 기한)</b> — 제때 {p['on_time']}일 · "
        + _days_list("늦음", p["late"])
        + (f" · 기록 없음 {len(p['no_record'])}일" if p["no_record"] else "") + "</li>",
        f"<li><b>확정(다음날 {ops.FINAL_TARGET_KST:%H:%M} 목표 · {ops.FINAL_SLO_KST:%H:%M} SLO)</b> — "
        f"목표 내 {f['by_target']}일 · 재시도로 SLO 내 {len(f['by_slo'])}일 · "
        + _days_list("SLO 초과", f["late"]) + " · " + _days_list("기한 지나 미확정", f["missing"])
        + (f" · 재구성으로만 확정 {len(f['rebuilt_only'])}일(처음 확정 시각 기록 없음)" if f["rebuilt_only"] else "")
        + "</li>",
        "<li><b>관문 차단</b> — " + (", ".join(st["gate_blocked_days"]) or "없음")
        + " · <b>검증 실패(이 30일)</b> — " + (", ".join(st["verify_failed_days"]) or "없음")
        + f" · <b>전 기간 첫 게시 실패</b> — {n_days}일 중 {first_fail_days}일(아래 표의 첫 게시 칸)</li>",
        f"<li><b>웹 프로브(5분 /healthz)</b> — {pr['n']}회 중 200 OK {pr['ok']}회"
        + (f" ({pr['share']:.1%})" if pr["share"] is not None else "")
        + " — 서버가 내려가 있던 주기는 줄 자체가 없어 가용률이 아니라 응답률이다</li>",
    ]
    if st["backfill_events"]:
        lines.append(f"<li class=\"dim\">이 창의 사건 중 {st['backfill_events']}건은 백필 복원 — 시각·필드가 성기다</li>")
    return (f'<div class="card"><ul class="tight">{"".join(lines)}</ul>'
            f'<p class="dim">기준일 {st["asof_days"]}일({st["from"]} ~ {st["to"]}) · 출처 원장 '
            f'<code>events.jsonl</code> · 기준·측정법은 <code>docs/slo.md</code>. 원장은 웹에 안 보인다.</p></div>')


def _size_cells(sz: dict | None) -> tuple[str, str, str]:
    """(순매수 칸, 거래대금 칸, 원인 칸) — 시장 합계의 전 → 후[억]."""
    if not sz:
        return ('<span class="dim">—</span>', '<span class="dim">—</span>',
                '<span class="dim">보관한 원본 payload 가 없어 크기를 모른다</span>')
    net = "<br>".join(f"{E(c['market'])} {E(c['actor'])} {_signed(c['before'])} → {_signed(c['after'])}"
                      + (' <span class="tag warn">부호 반대</span>' if c["flip"] else "") for c in sz["size"]["net"])
    tv = "<br>".join(f"{E(t['market'])} {t['before']:,.0f} → {t['after']:,.0f}" for t in sz["size"]["tv"])
    return net or "—", tv or "—", E(sz["cause"][1])


FINAL_DIFF_LINE = re.compile(r"^\s+(\S+) (\S+) 1일\s+(\S+) → (\S+) \(\S+\)$", re.M)


def final_market_moves(path: str) -> str:
    """확정 정정의 시장 합계 — ``FINAL.txt`` 가 잠정→확정 1일 칸을 그대로 적어 둔다(바꿔 낀 잠정본은 보관하지 않는다)."""
    body = _read(path) or ""
    head, sep, rest = body.partition("시장 합계:")
    if not sep:
        return ""
    block = rest.split("섹터 칸:", 1)[0]
    return "<br>".join(f"{E(m)} {E(a)} {E(o)} → {E(n)}" for m, a, o, n in FINAL_DIFF_LINE.findall(block)[:SIZE_ROWS])


def errata_html(rows: list[dict], source: str, sizes: dict | None = None, root: str | None = None) -> str:
    """정정 목록 — 첫 게시 뒤 숫자가 바뀐 날과 **얼마나** 바뀌었나. 숨기지 않는 것이 이 페이지의 존재 이유다."""
    sizes = sizes or {}
    src = ('출처: 운영 사건 원장(<code>ops_ledger.py --errata</code>)' if source == "ledger" else
           '출처: 폴더 표시(<code>REBUILT.txt</code>·<code>FINAL.txt</code>) — 원장 도입 전이라 바꿔 끼우며 '
           '지워진 정정은 못 센 하한이다')
    if not rows:
        return f'<div class="card"><p>정정 없음 — 첫 게시 뒤 숫자가 바뀐 날이 기록에 없다.</p><p class="dim">{src}.</p></div>'
    body = []
    for r in rows:
        sized = r["kind"] != "확정 정정" and r["asof"] in sizes
        if r["kind"] == "확정 정정":
            moves = final_market_moves(os.path.join(root, r["asof"], "FINAL.txt")) if root else ""
            net, tv, cause = moves or '<span class="dim">시장 합계 변화 없음</span>', '<span class="dim">—</span>', "잠정치 → 확정치"
        else:
            net, tv, cause = _size_cells(sizes.get(r["asof"]))
        note = ("" if sized else E(r["detail"])) + (" [백필 복원]" if r.get("backfill") else "")
        body.append(f"<tr><td>{E(r['asof'])}</td><td>{E(r['kind'])}</td><td class=\"move\">{net}</td><td class=\"move\">{tv}</td>"
                    f"<td>{cause} <span class=\"dim\">{note}</span></td></tr>")
    return (f'<div class="scroll"><table class="errata"><thead><tr><th>기준일</th><th>종류</th><th>시장 순매수 전 → 후[억]</th>'
            f'<th>시장 거래대금 전 → 후[억]</th><th>원인</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div><p class="dim">{src}. 전 = 보관한 그날 원본 게시본, 후 = 지금 판의 시장 합계. '
            f'순매수 칸은 가장 크게 바뀐 칸과 부호가 반대로 바뀐 칸(±{FLIP_MIN_EOK:.0f}억 이상)이다. '
            f'원인은 바로 다음 원본 게시본이 본 같은 날 값과 견줘 가른다: <b>잠정치 → 확정치</b> = 변화의 {EXPLAINED_SHARE:.0%} 이상이 그 판에 이미 있다(코드가 아니라 그 사이 DB 값이 바뀌었다), '
            f'<b>나중 코드·정의 또는 뒤늦은 정정</b> = 그 판은 아직 옛 값이다, 둘 다 아니면 겹친 것이다. '
            f'재구성은 지난 기준일을 나중 코드·확정 데이터로 다시 만든 것이고, 확정 정정은 잠정→확정에서 머리 숫자가 반올림을 넘게 바뀐 날이다.</p>')


def render(root: str, now: datetime | None = None, repo: str = REPO, window: int | None = None,
           build: tuple[str, str] | None = None, docs: dict | None = None) -> str:
    """페이지 문자열. ``build=(기준일, 짓는 폴더)`` 면 그날은 곧 바꿔 낄 그 폴더를 읽는다. ``window`` 는 시험용 — 기본은 전 기준일."""
    now = now or datetime.now(KST)
    all_days = scan(root, build)
    arc = archive_dirs(root)
    events = list(ops.iter_events(root))
    attach_first(all_days, events, arc)
    days = all_days[-window:] if window else all_days
    latest = build[0] if build else latest_dir(root)
    history = ledger_report_events(events)
    slo = ops.stats(events, 30, now=now.astimezone(timezone.utc)) if events else None
    err_rows, err_src = (ops.errata_rows(events), "ledger") if history else (folder_errata(all_days), "folders")
    commits = {h for r in VALIDATION + CHECKS for _l, pre, res in r["links"] for h in ((pre or (0, None))[1], res[1]) if h}
    info = {h: git_info(h, repo) for h in commits}
    gaps = prereg_gaps(info)
    docs = docs or {}
    layers_dir = build[1] if build else (os.path.join(root, latest) if latest else None)
    first_fail = sum(1 for d in all_days if d["first"]["status"] == "fail")
    slots = {
        "asof": E(latest or "없음"),
        "generated": now.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
        "n_days": str(len(all_days)),
        "window": f"최근 {len(days)}개 기준일" if window else f"전 기준일 {len(days)}개",
        "tiles": tiles_html(days),
        "strip": strip_html(days),
        "first_strip": first_strip_html(days),
        "fail_reasons": fail_reasons_html(days),
        "days_table": days_table_html(days) or '<tr><td colspan="8">리포트 폴더가 없다</td></tr>',
        "slo": slo_html(slo, bool(history), first_fail, len(all_days)),
        "errata": errata_html(err_rows, err_src, errata_sizes(all_days, arc, root, build), root),
        "layers": layers_html(verify_layers(layers_dir)),
        "docs": docs_html(docs, repo),
        "gaps": (f"{min(gaps)}~{max(gaps)}분" if len(set(gaps)) > 1 else f"{gaps[0]}분") if gaps else "알 수 없음(git 없음)",
        "validation": validation_rows(VALIDATION, docs, info),
        "checks": validation_rows(CHECKS, docs, info, "내용 · 조치"),
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
    return page_hits(page)


def in_published_folder(root: str, out: str) -> bool:
    """``out`` 이 게시된 기준일 폴더 안인가 — 그 폴더는 배치가 통째로 바꿔 끼우는 단위다(symlink ``latest`` 도 푼다)."""
    r, o = os.path.realpath(root), os.path.dirname(os.path.realpath(out))
    while o.startswith(r + os.sep):
        if DATE_RE.match(os.path.basename(o)) and os.path.dirname(o) == r:
            return True
        o = os.path.dirname(o)
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.path.join(REPO, "reports"), help="리포트 폴더(읽기만 한다)")
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--build", help="배치가 짓는 폴더 — 그 안에 trust.html·validation/ 을 쓴다(--asof 필요)")
    where.add_argument("--out", help="밖에 쓸 파일(시험·미리 보기). 옆에 validation/ 도 쓴다. 게시된 폴더 안은 거절한다")
    ap.add_argument("--asof", help="--build 폴더의 기준일")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    build = None
    if a.build:
        if not (a.asof and DATE_RE.match(a.asof)):
            print("--build 에는 --asof YYYY-MM-DD 가 필요하다.", file=sys.stderr)
            return 2
        build = (a.asof, os.path.abspath(a.build))
        out = os.path.join(build[1], "trust.html")
    else:
        out = os.path.abspath(a.out)
    if in_published_folder(root, out):
        print(f"{out} 는 게시된 폴더 안이다 — 고쳐 쓰지 않는다. 그 기준일을 다시 만들면 배치가 함께 짓는다"
              "(KR_QUANT_FORCE=1 scripts/daily_report.sh).", file=sys.stderr)
        return 2
    if not build and not report_dirs(root):
        print(f"{root} 에 리포트 폴더가 없다 — 실을 것이 없다.", file=sys.stderr)
        return 1
    dest = os.path.dirname(out)
    os.makedirs(dest, exist_ok=True)
    page = render(root, build=build, docs=export_docs(dest))
    hits = banned_hits(page)
    if hits:
        print(f"페이지에 쓰지 않을 말이 들어갔다 {hits} — 쓰지 않는다. 출처(검사 이름 등)를 고쳐라.", file=sys.stderr)
        return 2
    tmp = f"{out}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
