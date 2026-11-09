"""합성 데모 리포트 — DB·받은 폴더 없이 화면을 처음 띄워 본다(``sw-flow --demo``, ``sector-flow-demo``).

숫자와 이름은 **전부 난수에서 나온다.** 실데이터·실종목명을 레포에 넣지 않으려는 것이다(KRX·키움 이용 범위 질의 회신 전,
``docs/strategy/inquiries/``). 그래서 파일을 싣지 않고 씨앗 고정 sqlite 를 그 자리에서 만들어 **배치와 같은 생성기**
(``sector_flow.py`` → ``sector_numbers.py``)에 태운다 — 손으로 쓴 사본은 리포트 형식이 바뀌면 조용히 낡는다.

표지는 데이터 안에 있다(``numbers.html``·``payload.json`` 의 ``"demo": true``, 웹 두 장 맨 위 띠). 폴더 이름을 바꾸거나
통째로 넘겨도 화면이 ``[합성 데모]`` 를 뗄 수 없다.
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
BANNER = ('<div data-demo-banner style="position:sticky;top:0;z-index:9999;background:#b45309;color:#fff;'
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
    days = _weekdays(ASOF, DB_DAYS)
    sd, bars, raw, adj, shares, stocks = [], [], [], [], [], []
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
                shares.append((code, days[0], n_shares))
                size = rnd.uniform(0.3, 1.6)
                for d in days:
                    px = max(200.0, px * (1 + market_shock[d] + beta + sector_shock[d] + rnd.gauss(0, 0.014)))
                    close = int(px)
                    qty = int(n_shares * rnd.uniform(0.002, 0.02))
                    inst = int(qty * (rnd.gauss(0, 0.09) + flows[d]) * size)
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


def _stamp(out: Path) -> None:
    """생성물에 표지를 박는다 — 검증기·화면이 읽는 데이터 안에 ``demo``, 웹 두 장 맨 위에 띠."""
    payload = json.loads((out / "payload.json").read_text("utf-8"))
    payload["demo"] = True
    (out / "payload.json").write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    for name in ("numbers.html", "viewer.html"):
        html = (out / name).read_text("utf-8")
        html, n_d = re.subn(r"^const D = \{", 'const D = {"demo": true, ', html, count=1, flags=re.M)
        # 두 템플릿에는 <body> 태그가 없다 — 첫 </style> 뒤가 본문의 시작이다.
        html, n_b = re.subn(r"</style>", lambda m: m.group(0) + BANNER, html, count=1)
        html, n_t = re.subn(r"<title>", "<title>[합성 데모] ", html, count=1)
        if not (n_d and n_b and n_t):
            raise SystemExit(f"{name} 에 합성 데모 표지를 못 박았다 — 템플릿 형식이 바뀌었나?")
        (out / name).write_text(html, "utf-8")
    (out / MARKER).write_text(f"{DEMO_NOTICE}\nfingerprint: {_fingerprint()}\n", "utf-8")


def write_report(db: str | Path, out: Path) -> None:
    """``db`` 로 ``out`` 에 리포트를 짓는다 — 배치와 같은 명령. 직전 리포트 없이(빈 루트) 혼자 선다."""
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as root:
        _run("sector_flow.py", "--db", str(db), "--days", str(REPORT_DAYS), "--reports-root", root,
             "--json", str(out / "payload.json"))
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
    print(ensure(a.out, force=a.force))
