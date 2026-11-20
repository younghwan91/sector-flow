"""합성 데모 리포트 — DB·받은 폴더 없이 화면을 처음 띄워 본다(``sw-flow --demo``, ``sector-flow-demo``).

숫자와 이름은 **전부 난수에서 나온다.** 실데이터·실종목명을 레포에 넣지 않으려는 것이다(KRX·키움 이용 범위 질의 회신 전).
그래서 파일을 싣지 않고 씨앗 고정 sqlite 를 그 자리에서 만들어 **배치와 같은 생성기**
(``sector_flow.py`` → ``sector_numbers.py``)에 태운다 — 손으로 쓴 사본은 리포트 형식이 바뀌면 조용히 낡는다.

표지는 데이터 안에 있다(``numbers.html``·``payload.json`` 의 ``"demo": true``, 웹 맨 위 띠). 폴더 이름을 바꾸거나
통째로 넘겨도 화면이 ``[합성 데모]`` 를 뗄 수 없다.

**실 리포트의 표지를 다 보인다.** 분봉 밖 거래 큼(그중 판정 못 한 종목)·권리락·거래대금 집중·확정·잠정→확정 변경·직전 대비 들고 남·네이버 대조 칩.
이 값들도 전부 난수이고, 네이버 대조는 실제 네이버 값을 받지 않고 합성 값을 댄다(:func:`attach_synthetic_truth`).
검증·품질 링크는 데모 폴더 안의 안내 페이지(:data:`TRUST_PAGE`)로 간다 — 리포트 없이 보는 사람에겐 이 데모가 면책 문구의 근거로 가는 유일한 길이다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from sectorflow.basis import DEMO_NOTICE
from sectorflow.report_cli import script_path
from sectorflow.storage import connect, init_db

#: 실종목·실섹터와 헷갈리지 않는 이름 — 표의 폭이 실제 라벨과 비슷하도록 짧다.
SECTORS = tuple(f"가상{n}" for n in ("소재", "화학", "기계", "전자", "운송", "유통", "금융", "제약", "건설", "통신",
                                     "음식", "섬유"))
#: 10종목 미만이라 화면이 ``~`` 로 빼는 얇은 섹터 — 그 표시도 데모에서 보이게.
THIN_SECTOR = "가상소형"
N_PER_MARKET, N_THIN = 10, 3
#: 기준일과 거래일 수. 고정이라 언제 만들어도 같은 리포트다. 1년 %ile(260거래일) 분포가 차도록 조금 더 길다.
ASOF, DB_DAYS, REPORT_DAYS = date(2026, 10, 30), 275, 260
SEED = 20261030
#: 받는 쪽 안내용 표식 파일. 안에 만든 코드의 지문을 적어 생성기가 바뀌면 다시 만든다.
MARKER = "DEMO.txt"
#: 표지를 보이려고 일부러 만든 종목 — (섹터 번호, 시장 번호, 종목 번호).
BLOCK_STOCKS = ((1, 0, 0), (4, 0, 1), (6, 0, 2), (2, 1, 0))     # 기준일 거래량 대부분이 분봉 밖
UNJUDGED_STOCK = (5, 0, 3)                                      # 금액은 큰데 분봉이 없다 — 판정 못 함
RIGHTS_STOCKS = ((0, 0, 4), (8, 1, 4))                          # 권리락 뒤 신주가 아직 안 들어옴
CONCENTRATED_SECTOR = 3                                         # 거래대금이 시장의 20% 를 넘는 섹터(상위 2종목)
#: 합성 네이버 대조 — 시장별로 근사값에 얹는 차이(주체 순서: 개인·외국인·기관). 거래소는 칩이 뜨고 코스닥은 안 뜬다.
TRUTH_NOISE = {"거래소": (0.14, 0.09, 0.21), "코스닥": (0.01, 0.015, 0.02)}
TRUTH_LABEL = "합성 값 — 실제 네이버 값이 아니다"
#: 합성 네이버 행(원) — 실제 파일과 같은 모양이고 값만 난수다. 주체 → 행 이름.
TRUTH_ROWS = {"indiv": "개인", "forgn": "외국인", "inst": "기관합계"}
#: 띠가 sticky 라 페이지 안 이동의 제목을 가린다 — 390폭에서 띠가 두세 줄이고(~80px) 본문의 scroll-margin 은 44px 다.
BANNER = ('<style>[id]{scroll-margin-top:96px!important}</style>'
          '<div data-demo-banner style="position:sticky;top:0;z-index:9999;background:#b45309;color:#fff;'
          'padding:6px 12px;font-weight:700;text-align:center">' + DEMO_NOTICE + "</div>")


def _weekdays(end: date, n: int) -> list[str]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out[::-1]


def build_db(path: str | Path, seed: int = SEED) -> None:
    """씨앗 고정 합성 DB. 4주체 순매수 수량 합은 정확히 0, 기관 세부 합은 기관과 같다(실 DB 의 항등식 그대로)."""
    rnd = random.Random(seed)
    con = connect(str(path))
    init_db(con)
    # 실 스키마에는 있고 테스트용 SCHEMA 에는 없는 것 — 검증기가 읽는다(tests/test_pipeline_e2e.py 와 같다).
    con.execute("ALTER TABLE daily_bars_adjusted ADD COLUMN high REAL")
    con.execute("ALTER TABLE daily_bars_adjusted ADD COLUMN low REAL")
    con.execute("CREATE TABLE IF NOT EXISTS delisted_stocks (code TEXT PRIMARY KEY, name TEXT, market TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS minute_bars (code TEXT, ts TEXT, volume REAL)")
    # 빈 표 — 정규장 종가 대조는 합성 지수에 뜻이 없어 건너뛴다(SKIP 으로 적힌다).
    con.execute("CREATE TABLE IF NOT EXISTS regular_close (code TEXT, date TEXT, close REAL, src TEXT)")
    days = _weekdays(ASOF, DB_DAYS)
    sd, bars, raw, adj, shares, stocks = [], [], [], [], [], []
    minute, actions = [], []
    asof = days[-1]
    market_shock = {d: rnd.gauss(0.0002, 0.008) for d in days}
    roster = [(s, N_PER_MARKET) for s in SECTORS] + [(THIN_SECTOR, N_THIN)]
    for j, (sec, per_market) in enumerate(roster):
        beta, tilt, flow = rnd.gauss(0, 0.0015), rnd.gauss(0, 0.02), 0.0
        sector_shock = {d: rnd.gauss(0, 0.006) for d in days}
        flows = {}
        for d in days:                              # 섹터 기관 쏠림은 AR(1) — 며칠 이어지는 흐름이 생긴다
            flow = 0.93 * flow + rnd.gauss(0, 0.035)
            flows[d] = tilt + flow
        for mk_i, mk in enumerate(("거래소", "코스닥")):
            for i in range(per_market):
                # 'Z' 로 시작하는 코드는 KRX 에 없어 실종목과 겹치지 않는다.
                code, name = f"Z{j:02d}{mk_i}{i:02d}", f"{sec}{mk[0]}{i:02d}"
                stocks.append((code, name, mk, sec, "stock"))
                px = rnd.uniform(4_000, 90_000) * (1.5 if mk == "거래소" else 0.6)
                n_shares = rnd.randint(8, 240) * 1_000_000 // (1 if mk == "거래소" else 3)
                who = (j, mk_i, i)
                if j == CONCENTRATED_SECTOR and i < 2:      # 두 종목이 시장 거래대금을 끌어올린다
                    n_shares, px = n_shares * 12, px * 3
                shares.append((code, days[0], n_shares))
                size = rnd.uniform(0.3, 1.6)
                if who in RIGHTS_STOCKS:
                    actions.append((code, days[-30], "256", -22.0))
                for d in days:
                    px = max(200.0, px * (1 + market_shock[d] + beta + sector_shock[d] + rnd.gauss(0, 0.014)))
                    close = int(px)
                    qty = int(n_shares * rnd.uniform(0.002, 0.02))
                    inst = int(qty * (rnd.gauss(0, 0.09) + flows[d]) * size)
                    if d == asof and who in (*BLOCK_STOCKS, UNJUDGED_STOCK):
                        qty, inst = qty * 3, qty            # 시장 Σ|주체| 의 1% 를 넘는 큰 체결
                    forgn = int(qty * rnd.gauss(0, 0.10))
                    etc = int(qty * rnd.gauss(0, 0.015))
                    indiv = -(inst + forgn + etc)
                    parts = [rnd.random() for _ in range(6)]
                    det = [int(inst * p / sum(parts)) for p in parts]
                    det[0] += inst - sum(det)
                    sd.append((code, d, close, 0, qty, indiv, forgn, inst, *det, 0, etc, "kiwoom"))
                    tv = round(qty * close * (1 + rnd.gauss(0, 0.012)) / 1e6)    # 백만원
                    bars.append((code, d, close, close, close, close, qty, tv, "kiwoom"))
                    raw.append((code, d, close))
                    adj.append((code, d, float(close), close * 1.01, close * 0.99))
                    if d == asof and who != UNJUDGED_STOCK:
                        off = rnd.uniform(0.55, 0.95) if who in BLOCK_STOCKS else rnd.uniform(0.0, 0.01)
                        minute.append((code, f"{d} 10:00:00", int(qty * (1 - off))))
    # 종합지수 코드(001·101)와 섹터별 업종지수(이름 = 섹터, 코드 둘 = 거래소·코스닥) — 비교선도 난수다.
    index_rows = [("001", "종합(KOSPI)", 2600.0), ("101", "종합(KOSDAQ)", 800.0)]
    for j, (sec, _n) in enumerate(roster):
        index_rows += [(f"I{j:02d}1", sec, 1000.0), (f"I{j:02d}2", sec, 800.0)]
    index = []
    for code, name, lvl in index_rows:
        for d in days:
            lvl *= 1 + market_shock[d] + rnd.gauss(0, 0.004)
            index.append((code, name, d, round(lvl)))
    con.executemany("INSERT INTO stocks VALUES (?,?,?,?,?)", stocks)
    con.executemany(
        "INSERT INTO supply_demand(code,date,close,flu_rt,acc_trde_qty,individual,foreign_,"
        "institution,fnnc_invt,insrnc,invtrt,bank,penfnd_etc,samo_fund,natn,etc_corp,source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", sd)
    con.executemany("INSERT INTO daily_bars(code,date,open,high,low,close,volume,trade_value,source)"
                    " VALUES (?,?,?,?,?,?,?,?,?)", bars)
    con.executemany("INSERT INTO daily_bars_raw VALUES (?,?,?)", raw)
    con.executemany("INSERT INTO daily_bars_adjusted(code,date,close,high,low) VALUES (?,?,?,?,?)", adj)
    con.executemany("INSERT INTO shares_outstanding_history VALUES (?,?,?)", shares)
    con.executemany("INSERT INTO sector_index(code,name,date,close) VALUES (?,?,?,?)", index)
    con.executemany("INSERT INTO minute_bars(code,ts,volume) VALUES (?,?,?)", minute)
    con.executemany("INSERT INTO corporate_actions(code,date,upd_stkpc_tp,upd_rt) VALUES (?,?,?,?)", actions)
    con.commit()
    con.close()


def _fingerprint() -> str:
    """이 코드와 생성기·템플릿의 지문 — 바뀌면 만들어 둔 데모가 옛 형식이다."""
    h = hashlib.sha256(Path(__file__).read_bytes())
    for name in ("sector_flow.py", "sector_numbers.py"):
        p = script_path(name)
        h.update(p.read_bytes())
        for t in sorted((p.parent / "templates").glob("sector_*.html")):
            h.update(t.read_bytes())
    return h.hexdigest()[:16]


def _run(script: str, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if k != "KR_QUANT_DB"}     # 실 DB 를 절대 안 보게
    p = subprocess.run([sys.executable, str(script_path(script)), *args], env=env,
                       capture_output=True, text=True, timeout=600)
    if p.returncode:
        raise SystemExit(f"합성 데모를 만들지 못했다 — {script} 실패(rc={p.returncode})\n{p.stderr[-2000:]}")


#: 게시 루트의 기준일 목록으로 가는 링크 — 데모 폴더에는 그 위가 없다.
LIST_LINK = '<a href="../">기준일 목록 →</a>'
#: 데모의 검증·품질 안내 — 근거 줄. 판정 글자는 실 페이지(``scripts/trust_page.VALIDATION``)와 같다(``tests/test_demo.py``).
TRUST_RESULTS = (
    ("섹터 임펄스·가속·x(20일)의 앞날 관계", "증거 없음", "h5·h20 6칸 모두 사전등록 기준에 못 미쳤다. 없다는 뜻이 아니라 그 표본으로 못 봤다는 뜻이다."),
    ("선정 점수 G 의 앞날 관계", "판정 불가", "관문 통과 섹터가 날마다 2~7개라 검정력이 없다. 그래서 탐색 모드(e)에만 둔다."),
    ("미실현 x 는 무엇을 재나", "사실상 섹터 20일 반전", "x 와 같은 구간 섹터 수익률의 반대가 순위상관 0.82~0.93 이다."),
    ("섹터 수급(투신+연기금·외국인 20일, 기관 60일)의 앞날 관계", "증거 없음", "발견 구간(2018~2022)에서 여섯 칸 모두 탈락해 확인 단계로 가지 못했다."),
    ("종목 투신+연기금 20일 순매수 ÷ 시총의 섹터 안 순위", "음(−)의 순위 정보 확인 · 쓸 수 있는 형태는 못 보임", "그래서 종목 선정 점수는 탐색 모드(e)에만 두고 표지를 단다."),
)
TRUST_LIMITS = (
    "순매수 금액은 수량 × 종가 근사다. DB 는 주체별 수량만 준다.",
    "시장 합계는 시장 전체가 아니다. 우선주·리츠 등이 수급 자료에 없고, 네이버 시장값과 대조만 했다. KRX 원표와는 대조하지 못했다.",
    "시장 합계와 네이버 시장값이 크게 갈리는 날의 원인은 분봉 밖 거래로 읽힌다(2026-09-14 무렵부터, 그 경계는 사후다). 수량은 대조한 범위(10-12 까지)에서 네이버 종목값과 같았지만 금액은 따로 틀릴 수 있다. 값의 오류가 아니라 정의 차이일 수 있고 까닭은 미확인이다.",
    "1일 값·1년 %ile·연속 일수는 앞날을 검정한 적이 없다. 관측 숫자이고 추천이 아니다.",
)


def trust_page() -> str:
    """데모의 '검증·품질' — 운영 기록이 없으니 그 표는 없다고 적고, 면책 문구의 근거(검증 결과 요지·한계)를 싣는다."""
    rows = "".join(f"<tr><td>{q}</td><td><b>{v}</b></td><td>{why}</td></tr>" for q, v, why in TRUST_RESULTS)
    limits = "".join(f"<li>{x}</li>" for x in TRUST_LIMITS)
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>[합성 데모] 검증·품질 안내</title>
<style>body{{margin:0;font:15px/1.6 system-ui,sans-serif;color:#1d2433;background:#f7f8fa}}
main{{max-width:760px;margin:0 auto;padding:16px}} h1{{font-size:20px}} h2{{font-size:16px;margin-top:24px}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;background:#fff;font-size:14px}} td,th{{border:1px solid #e2e5ea;padding:6px 8px;text-align:left;vertical-align:top}}
a{{color:#2457c5}}</style></head><body>{BANNER}<main>
<h1>검증·품질 — 데모 안내</h1>
<p>이 폴더는 난수로 만든 합성 리포트다. 그래서 실 리포트의 <b>일일 파이프라인 품질</b>(기준일별 자기 검증·확정 여부·정정 목록)은 여기 없다. 아래는 화면 면책 문구의 근거인 검정 결과 요지다.</p>
<nav><a href="numbers.html">수치표</a> · <a href="viewer.html">흐름 뷰어</a></nav>
<h2 id="validation">검증 결과 — 지표가 앞날 수익률에 대해 무엇을 말하나</h2>
<p>2026-11-08 사전등록 검정(실데이터, 2018~2026). 기술 통계이고 매매 판단이 아니다.</p>
<div class="scroll"><table><thead><tr><th>질문</th><th>판정</th><th>왜</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2 id="limits">알려진 한계</h2>
<ul>{limits}</ul>
<p>전체 표와 사전등록 문서는 실 리포트의 검증·품질 페이지에 있다.</p>
</main></body></html>"""


def _stamp(out: Path) -> None:
    """생성물에 표지를 박는다 — 검증기·화면이 읽는 데이터 안에 ``demo``, 웹 맨 위에 띠, 데모 안의 안내 페이지."""
    payload = json.loads((out / "payload.json").read_text("utf-8"))
    payload["demo"] = True
    (out / "payload.json").write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    for name in ("numbers.html", "viewer.html"):
        html = (out / name).read_text("utf-8")
        html, n_d = re.subn(r"^const D = \{", 'const D = {"demo": true, ', html, count=1, flags=re.M)
        # 두 템플릿에는 <body> 태그가 없다 — 첫 </style> 뒤가 본문의 시작이다.
        html, n_b = re.subn(r"</style>", lambda m: m.group(0) + BANNER, html, count=1)
        html, n_t = re.subn(r"<title>", "<title>[합성 데모] ", html, count=1)
        # 실 리포트의 링크는 게시 루트를 전제한다 — 데모는 폴더 하나라 그 안에서 닫는다(면책 문구의 근거가 404 였다).
        html, n_l = re.subn(r'href="\.\./latest/trust\.html', 'href="trust.html', html)
        html = html.replace(LIST_LINK, "")
        if not (n_d and n_b and n_t and n_l):
            raise SystemExit(f"{name} 에 합성 데모 표지를 못 박았다 — 템플릿 형식이 바뀌었나?")
        (out / name).write_text(html, "utf-8")
    (out / "trust.html").write_text(trust_page(), "utf-8")
    (out / MARKER).write_text(f"{DEMO_NOTICE}\nfingerprint: {_fingerprint()}\n", "utf-8")


def provisional_db(db: str | Path, path: str | Path) -> None:
    """``db`` 의 사본 — 기준일 일부 종목의 기관 수량을 바꾼 **잠정** 값. 4주체 합·기관 세부 합 항등식은 그대로다."""
    shutil.copyfile(db, path)
    con = connect(str(path))
    asof = ASOF.isoformat()
    con.execute("UPDATE supply_demand SET institution = institution + acc_trde_qty / 25, fnnc_invt = fnnc_invt + acc_trde_qty / 25, "
                "individual = individual - acc_trde_qty / 25 WHERE date = ? AND substr(code, 6, 1) IN ('3', '7')", (asof,))
    con.commit()
    con.close()


def attach_synthetic_truth(payload: dict, out: Path) -> None:
    """네이버 대조 칩을 보이려 합성 값을 댄다 — 실제 네이버 값을 받지 않는다. 검증기가 ``krx_truth.json`` 으로 다시 센다.

    공식은 ``scripts/krx_truth.compare`` 와 같다. 그 스크립트는 설치본에 일부러 없어 부르지 못한다.
    """
    T = payload["today"]
    markets, rows, gate_share = {}, {}, 0.002
    for m, noise in TRUTH_NOISE.items():
        M = T["market"][m]
        gate = gate_share * M["tv"]
        row, won, err, den = {}, {}, 0.0, 0.0
        for (a, name), e in zip(TRUTH_ROWS.items(), noise):
            v = M["net"][a]["v"]
            off = round(v * (1 + e), 2)
            won[name] = int(round(off * 1e8))
            err, den = err + abs(v - off), den + abs(off)
            big = abs(off) >= gate and off != 0
            row[a] = {"gap_pct": round(abs(v - off) / abs(off) * 100.0, 2) if big else None,
                      "flip": bool(off * v < 0 and max(abs(off), abs(v)) >= gate)}
        row["etc"] = None
        row["total_pct"] = total = round(err / den * 100.0, 2) if den and den >= gate else None
        row["hot"] = bool(total is not None and total > 5.0)
        markets[m], rows[m] = row, won
    T["krx_truth"] = {"status": "ok", "source": "naver", "label": TRUTH_LABEL, "date": T["date"], "fetched_at": None,
                      "min_tv_share": gate_share, "warn": 5.0, "markets": markets}
    (out / "krx_truth.json").write_text(json.dumps(
        {"date": T["date"], "source": "naver", "note": TRUTH_LABEL, "rows": rows}, ensure_ascii=False, indent=1), "utf-8")


def write_report(db: str | Path, out: Path) -> None:
    """``db`` 로 ``out`` 에 **확정** 리포트를 짓는다 — 배치와 같은 명령을 순서대로: 직전 거래일 → 잠정 → 확정."""
    out.mkdir(parents=True, exist_ok=True)
    asof = ASOF.isoformat()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "reports"
        prev = _weekdays(ASOF, 2)[0]
        (root / prev).mkdir(parents=True)
        (root / asof).mkdir()
        _run("sector_flow.py", "--db", str(db), "--days", str(REPORT_DAYS), "--reports-root", str(root),
             "--asof", prev, "--final", "--json", str(root / prev / "payload.json"))
        provisional_db(db, Path(tmp) / "provisional.db")
        _run("sector_flow.py", "--db", str(Path(tmp) / "provisional.db"), "--days", str(REPORT_DAYS),
             "--reports-root", str(root), "--json", str(root / asof / "payload.json"))
        _run("sector_flow.py", "--db", str(db), "--days", str(REPORT_DAYS), "--reports-root", str(root), "--final",
             "--json", str(out / "payload.json"))
    payload = json.loads((out / "payload.json").read_text("utf-8"))
    attach_synthetic_truth(payload, out)
    (out / "payload.json").write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    final_note = (out / "final_diff.txt").read_text("utf-8") if (out / "final_diff.txt").exists() else ""
    (out / "FINAL.txt").write_text(f"{DEMO_NOTICE}\n{final_note}", "utf-8")
    _run("sector_flow.py", "--from-json", str(out / "payload.json"), "--html", str(out / "viewer.html"))
    _run("sector_numbers.py", "--payload", str(out / "payload.json"), "--html", str(out / "numbers.html"))
    _stamp(out)


def default_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "sector-flow" / "demo"


def _is_current(out: Path) -> bool:
    try:
        said = (out / MARKER).read_text("utf-8")
    except OSError:
        return False
    return f"fingerprint: {_fingerprint()}" in said and all((out / n).exists() for n in ("numbers.html", "payload.json"))


def ensure(out: str | Path | None = None, *, force: bool = False) -> str:
    """합성 데모 폴더 경로를 돌려준다. 없거나 옛 형식이면 옆에 지어 바꿔 끼운다.

    데모가 아닌 내용이 든 폴더는 건드리지 않는다 — ``--out`` 을 잘못 줘 실 리포트를 덮으면 안 된다.
    """
    target = Path(out).expanduser() if out else default_dir()
    if not force and _is_current(target):
        return str(target)
    if target.exists() and any(target.iterdir()) and not (target / MARKER).exists():
        raise SystemExit(f"{target} 는 비어 있지 않고 합성 데모 폴더도 아니다 — 덮어쓰지 않는다. 다른 --out 을 주어라.")
    target.parent.mkdir(parents=True, exist_ok=True)
    print("합성 데모 리포트를 만든다(실데이터 아님, 수십 초)…", file=sys.stderr)
    build = Path(tempfile.mkdtemp(prefix=target.name + ".building.", dir=target.parent))
    try:
        build_db(build / "demo.db")
        write_report(build / "demo.db", build)
        (build / "demo.db").unlink()
        build.chmod(0o755)
        if target.exists():
            shutil.rmtree(target)
        build.rename(target)
    finally:
        shutil.rmtree(build, ignore_errors=True)
    return str(target)


def main() -> None:
    ap = argparse.ArgumentParser(description="합성 데모 리포트 폴더를 만든다 — DB 불요, 숫자·이름 전부 난수(실데이터 아님)")
    ap.add_argument("--out", help=f"만들 폴더(기본: {default_dir()})")
    ap.add_argument("--force", action="store_true", help="이미 있어도 다시 만든다")
    a = ap.parse_args()
    path = ensure(a.out, force=a.force)
    print(path)                        # stdout 은 경로 한 줄 — 안내는 stderr 로
    print(f"화면: uv run sw-flow --dir {path}\n웹(저장소 체크아웃에서): uv run python scripts/serve_reports.py --root {path}"
          " (주소는 서버가 찍는다)", file=sys.stderr)
