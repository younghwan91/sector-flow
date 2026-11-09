#!/usr/bin/env python
"""잠정→확정 수정 폭(``revision_stats``)의 **시드** — 아카이브 18:10 게시본 대 확정 재구성 리포트.

확정 모드(08:20, ``FINAL.txt`` + ``final_diff.json``)는 2026-11-08 에 생겼다. 그 전 기준일의 리포트 폴더는
11-08 에 확정 데이터로 다시 만든 것(``REBUILT.txt``)이라 "그날 18:10 게시본이 얼마나 바뀌었나" 를 말하지
않는다(바꿔 끼운 리포트가 기준일 당일 것이 아니다). 그날 저녁 게시본은 ``reports/.archive/pre-rebuild-2026-11-08``
에 남아 있다. 이 스크립트는 둘을 견줘 기준일마다 ``final_diff.json`` 과 **같은 정의**의 1일 칸(``cells_1d`` —
``sector_flow.final_diff_compute``)을 만들어 ``<출력>/<기준일>.json`` 으로 쓴다. ``revision_stats`` 는 그 기준일
폴더가 쓸 만한 잠정 대비를 주지 못할 때만 ``<리포트 루트>/.revision_seed/`` 의 이 파일을 읽고, 화면은
``시드: 아카이브 18:10 게시본 대 확정 재구성`` 이라고 출처를 적는다.

DB 를 읽지 않는다 — 두 폴더의 ``payload.json`` 만 본다. 몇 번을 돌려도 같은 파일이 나온다(``seed.made`` 빼고).

쓰는 기준일(전부 만족해야 한다 — 빠진 날은 이유와 함께 찍는다):

1. 아카이브 ``payload.json`` 의 마지막 날 = 폴더 기준일, 파일 수정 시각(KST)이 **그 기준일 17시 이후** —
   그날 저녁 게시본이다(다음날 손으로 다시 만든 것은 뺀다. ``_usable_base`` 와 같은 규칙).
2. 수집이 끝난 게시본 — 시장마다 마지막 날 수익률 가중치 합(``retw``: 그날 조정 종가가 있는 종목의 전일 시총)이
   확정 쪽의 :data:`MIN_COVERAGE`(97%, ``completeness_gate.py`` 와 같은 값) 이상. 10-27(코스닥 0%)처럼 지금이면
   완전성 관문이 막았을 게시본을 뺀다. ``VERIFY_FAILED.txt`` 로 거르지 않는다 — 그 시기 실패는 대부분 문서 주장
   (G 층)·화면 이동(H 층) 검사였고 수치가 아니었다.
3. 기준일 ≥ ``--since``(기본 2026-09-14) — 그날부터 일별 값이 애프터마켓(~20:00) 포함 통합값이라 18:10
   스냅숏이 확정과 갈린다. 그 전에는 둘이 거의 같아 다른 체제다.
4. 확정 쪽 ``<리포트 루트>/<기준일>`` 에 ``FINAL.txt`` 가 있고 ``VERIFY_FAILED.txt`` 가 없으며 페이로드의
   마지막 날 = 기준일.
칸 규칙: (시장, 섹터) 의 종목 집합(``names`` 의 시장·섹터)이 두 페이로드에서 다르면 그 칸은 버린다 — 재구성은
지금의 종목 마스터와 섹터 덮어쓰기(``sector_override``)로 만들어 그 사이 편입·분류 변경이 섞인다. 수정 폭이
아니다. 버린 칸 수는 파일에 적는다(덮어쓰기 지문이 달라도 날을 통째로 버리지 않고 바뀐 칸만 버린다).

Run:
  uv run python scripts/seed_revisions.py \\
      --archive reports/.archive/pre-rebuild-2026-11-08 --archive reports/.archive/2026-11-06-provisional \\
      --final-root reports --out <스크래치>/.revision_seed
  적용: mkdir -p reports/.revision_seed && cp <스크래치>/.revision_seed/*.json reports/.revision_seed/
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))


def _sf():
    spec = importlib.util.spec_from_file_location("sector_flow", os.path.join(HERE, "sector_flow.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sf = _sf()

#: 애프터마켓 포함 통합값이 시작된 날(quant-airflow CLAUDE.md §3) — 그 전 18:10 게시본은 확정과 거의 같다.
SINCE = "2026-09-14"
#: 수집 완료 문턱 — ``completeness_gate.MIN_COVERAGE`` 와 같은 값.
MIN_COVERAGE = 0.97


def _load(path: str) -> "dict | None":
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def candidates(archives: list[str]) -> list[tuple[str, str]]:
    """(기준일 폴더 이름 또는 None, 아카이브 폴더) — 폴더 바로 아래 ``payload.json`` 이면 그 하나, 아니면 하위 폴더."""
    out = []
    for a in archives:
        if os.path.isfile(os.path.join(a, "payload.json")):
            out.append((None, a))
            continue
        if not os.path.isdir(a):
            # 디스크 소실 뒤에는 아카이브가 없다 — docs/runbook.md. 시드 없이도 화면은 돈다(수정 폭 칸이 비어 있을 뿐).
            print(f"  건너뜀 {a}: 아카이브 폴더가 없다 — 그날 저녁 게시본은 다시 만들 수 없다(docs/runbook.md)")
            continue
        for name in sorted(os.listdir(a)):
            d = os.path.join(a, name)
            if os.path.isfile(os.path.join(d, "payload.json")):
                out.append((name, d))
    return out


def membership(P: dict) -> dict:
    """(시장, 섹터) → 종목 코드 집합(``names``)."""
    out: dict = {}
    for code, r in (P.get("names") or {}).items():
        out.setdefault((r.get("market"), r.get("sector")), set()).add(code)
    return out


def seed_one(folder: "str | None", arch: str, final_root: str, since: str) -> tuple["dict | None", str]:
    """한 기준일의 시드 문서와 한 줄 요약 — 못 쓰면 (None, 이유)."""
    path = os.path.join(arch, "payload.json")
    prov = _load(path)
    if prov is None:
        return None, "아카이브 payload.json 을 못 읽었다"
    date = (prov.get("dates") or [None])[-1]
    if folder is not None and date != folder:
        return None, f"아카이브 페이로드의 기준일이 {date} 다"
    built = datetime.fromtimestamp(os.path.getmtime(path), sf.KST)
    if built.strftime("%Y-%m-%d") != date or built.hour < sf.FINALIZED_KST_HOUR:
        return None, f"그날 저녁 게시본이 아니다({built:%Y-%m-%d %H:%M} 생성)"
    if date < since:
        return None, f"{since} 전 — 애프터마켓 통합값 이전 체제"
    fdir = os.path.join(final_root, date)
    if not os.path.isfile(os.path.join(fdir, "FINAL.txt")):
        return None, "확정 리포트가 없다(FINAL.txt)"
    if os.path.isfile(os.path.join(fdir, "VERIFY_FAILED.txt")):
        return None, "확정 리포트가 자기 검증에 걸렸다"
    fin = _load(os.path.join(fdir, "payload.json"))
    if fin is None or (fin.get("dates") or [None])[-1] != date:
        return None, "확정 payload.json 이 없거나 기준일이 다르다"
    for m in fin.get("markets") or []:
        wf = sum((v or [0])[-1] or 0.0 for v in (fin.get("retw") or {}).get(m, {}).values())
        wp = sum((v or [0])[-1] or 0.0 for v in (prov.get("retw") or {}).get(m, {}).values())
        if wf and wp < MIN_COVERAGE * wf:
            return None, f"수집 미완 게시본 — {m} 수익률 가중치 {wp / wf:.0%} < {MIN_COVERAGE:.0%}"
    fd = sf.final_diff_compute(fin, prov)
    mp, mf = membership(prov), membership(fin)
    cells = [c for c in fd["cells_1d"] if mp.get((c[0], c[1])) == mf.get((c[0], c[1]))]
    dropped = len(fd["cells_1d"]) - len(cells)
    rel, flips = sf.revision_rows(cells)
    kept, tot = sf.revision_top3(cells)
    doc = {
        "base_final": False,
        "base_built": built.strftime("%Y-%m-%d %H:%M"),
        "cells_1d": cells,
        # 종목(기관상위) 층 — final_diff_compute 안의 stock_diff 가 같은 membership 규칙으로
        # 구성이 갈린 (시장, 섹터)를 이미 뺐다(dropped_sectors). 그대로 싣는다.
        "stocks": fd["stocks"],
        "seed": {"label": sf.REV_SEED_LABEL,
                 "provisional": os.path.abspath(path),
                 "final": os.path.abspath(os.path.join(fdir, "payload.json")),
                 "dropped_membership": dropped,
                 "made": datetime.now(sf.KST).strftime("%Y-%m-%d %H:%M")},
    }
    med = sorted(rel)[len(rel) // 2] if rel else None
    S = fd["stocks"]
    smed = None if S is None else S["rel_median_pct"]
    stk = " · 종목 층 없음(게시본에 1일 종목 칸이 없다 — 옛 형식)" if S is None else (
        f" · 기관상위 {S['n_stocks']}종목 행 {S['n_rows']} 중앙 "
        + ("—" if smed is None else f"{smed:.1f}%"))
    line = (f"칸 {len(cells)}(버림 {dropped}) · 게이트 통과 {len(rel)} · 중앙 "
            f"{'—' if med is None else f'{med:.1f}%'} · 뒤집힘 {flips} · 상위3 유지 "
            f"{'—' if not tot else f'{100 * kept / tot:.0f}%'}{stk}")
    return doc, line


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", action="append", required=True,
                    help="18:10 게시본 아카이브 — <기준일>/payload.json 폴더들의 루트, 또는 payload.json 이 든 폴더(여러 번)")
    ap.add_argument("--final-root", required=True, help="확정(재구성) 리포트 루트 — <기준일>/FINAL.txt·payload.json")
    ap.add_argument("--out", required=True, help="시드를 쓸 폴더(적용하려면 <리포트 루트>/.revision_seed)")
    ap.add_argument("--since", default=SINCE)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    n = 0
    for folder, arch in candidates(a.archive):
        doc, line = seed_one(folder, arch, a.final_root, a.since)
        tag = folder or os.path.basename(os.path.normpath(arch))
        if doc is None:
            print(f"  건너뜀 {tag}: {line}")
            continue
        date = doc["base_built"][:10]
        with open(os.path.join(a.out, f"{date}.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        print(f"  시드 {date}: {line}")
        n += 1
    print(f"시드 {n}일 → {a.out}  ({sf.REV_SEED_LABEL})")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
