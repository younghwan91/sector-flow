"""Q1 데이터(T 잠정치) — usage: provisional_check.py <기준일>. Day-T check: payload names[code].cap (18:10 build) -> implied provisional close; compare with final close and 15:30 bar."""

import json

import pandas as pd

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import connect_ro, reports_dir  # noqa: E402

asof = sys.argv[1]
c = connect_ro()


def q(s, p=None):
    return pd.read_sql(s, c, params=p)


p = json.load(open(reports_dir() / asof / "payload.json"))
cap = pd.DataFrame([(k, v["cap"]) for k, v in p["names"].items() if v.get("cap")], columns=["code", "pcap"])
x = q(
    """select b.code,b.close,b.high,b.low,(select shares_outstanding from shares_outstanding_history h where h.code=b.code and h.date<=b.date order by date desc limit 1) sh,
   (select close from minute_bars m where m.code=b.code and m.ts between %s::date+time '15:30' and %s::date+time '15:32' order by ts desc limit 1) m1530
   from daily_bars b where b.date=%s""",
    (asof, asof, asof),
)
x = x.merge(cap, on="code")
x = x[x.pcap >= 5000]
x["impl"] = x.pcap * 1e8 / x.sh
x["tol"] = x.impl * 0.5e-4 + 0.05e8 / x.sh * 0 + 1
x["eq_final"] = (x.impl - x.close).abs() <= x.impl * 2e-5 + 1
x["in_range"] = (x.impl >= x.low * (1 - 2e-5)) & (x.impl <= x.high * (1 + 2e-5))
x["eq_1530"] = (x.impl - x.m1530).abs() <= x.impl * 2e-5 + 1
print(
    asof,
    "stocks cap>=5000억:",
    len(x),
    "implied==final close:",
    int(x.eq_final.sum()),
    "implied in [low,high]:",
    int(x.in_range.sum()),
    "have 15:30 bar:",
    int(x.m1530.notna().sum()),
    "implied==15:30 bar:",
    int(x.eq_1530.sum()),
)
