"""결함 3(시총 = 수정주가 × 주식수) 크기 — 최근 1년 daily_bars 행 중 원주가(daily_bars_raw)와 다른 몫(문서 7.8%),
코스닥 종이/목재 종목별 비율(078130 병합)."""

import pandas as pd

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import connect_ro  # noqa: E402

c = connect_ro()


def q(s, p=None):
    return pd.read_sql(s, c, params=p)


print(
    q("""select count(*) bars, count(r.close) with_raw, sum((r.close is not null and r.close<>b.close)::int) differ,
   count(distinct b.code) filter (where r.close is not null and r.close<>b.close) codes_differ
   from daily_bars b join stocks s using(code) left join daily_bars_raw r using(code,date) where b.date between '2025-10-15' and '2026-11-05'""")
)
print(
    q("""select b.code,s.name,s.market,s.sector,count(*) n,round(avg(b.close::numeric/r.close),3) ratio from daily_bars b join stocks s using(code) join daily_bars_raw r using(code,date)
   where b.date between '2025-10-15' and '2026-11-05' and r.close<>b.close and s.market='코스닥' and s.sector='종이/목재' group by 1,2,3,4""")
)
