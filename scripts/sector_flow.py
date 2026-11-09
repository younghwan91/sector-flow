#!/usr/bin/env python
"""섹터 자금흐름 — 어느 시점부터 어느 장에서 어느 섹터로 돈이 흘러갔나.

DB 읽기 전용. 수급 정문(:func:`sectorflow.storage.read_supply_demand`)으로 순매매
**수량**을 읽어 그날 종가를 곱해 금액으로 환산하고, `stocks` 의 섹터·시장으로 묶어
**일별 시계열**을 낸다. 하루치만 보면 블록딜 한 건이 섹터를 통째로 흔들어 보이므로,
임의 구간 누적이 기본 단위다.

세 가지 관점을 같이 낸다:

* **금액 누적** — 구간 동안 그 섹터로 순유입된 금액(억원).
* **비중 변화** — 누적 순매수금액 ÷ **기간말 섹터 시가총액**. 기관 지분율이 몇 %p
  올랐나에 대한 근사다. 금액만 보면 대형 섹터가 늘 이기므로 이쪽이 "어디로 쏠렸나"를
  더 정직하게 준다.
* **시장 분리** — 거래소/코스닥은 주체 구성이 달라 섞으면 신호가 상쇄된다.

⚠️ **순매매대금은 종가 환산 근사다.** DB 는 수량만 주므로 금액은 종가를 곱해 복원한다.
참값은 VWAP 가중이다 — 방향과 상대 크기를 보는 용도이며 원 단위 정확도를 주장하지 않는다.

Run:  python scripts/sector_flow.py --html viewer.html
      python scripts/sector_flow.py --days 120 --json out.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import warnings
from datetime import datetime, timedelta, timezone

import pandas as pd

from sectorflow.basis import BASIS_SLOT, DATA_BASIS
from sectorflow.sector_override import SECTOR_OVERRIDE, SOURCE as OVERRIDE_SOURCE, UNCLASSIFIED, fingerprint
from sectorflow.storage import (
    _is_pg,
    connect, db_default, market_cap_asof_bulk, read_supply_demand, recent_dates, rights_pending)

# pandas 는 psycopg2 연결을 "검증 안 된 DBAPI" 라며 read_sql 마다 UserWarning 을 낸다. 동작엔 문제가 없는데
# 크론 로그를 덮어 진짜 경고를 묻는다(2026-11 cron.log 에 1,491줄). 이 메시지만 끈다 — 크론의 전역 -W 는 다른 경고까지 끈다.
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy", category=UserWarning)

EOK = 1e8                 # 원 → 억원
INDEX_RET: dict = {}
#: 이번 실행에서 실제로 덮은 (코드 → 섹터) — ``load`` 가 채우고 ``main`` 이 페이로드 ``sector_override`` 에 싣는다.
OVERRIDE_APPLIED: dict = {}
DEFAULT_DAYS = 260        # 거래일 ≈ 1년
#: 종목 집계를 낼 구간(거래일). 수치표·TUI 의 프리셋과 같아야 한다. 1 은 "오늘" — 뷰어의 1일 프리셋이
#: 종목을 비워 내던 칸이다. 1일 창에는 사실(순매수·거래대금·수익률)만 있다 — x·G·가속 같은 파생값은
#: 원래 종목 집계에 없고, 섹터 쪽 1일(``today``)에도 싣지 않는다.
NAME_WINDOWS = (1, 5, 20, 60, 120)
#: 이 시각 전의 "오늘" 은 장중으로 친다. 수집이 끝났다는 보장이 **아니다**(끝나는 시각은 18:47 까지
#: 갔다, 2026-10-27) — 그 판정은 ``completeness_gate.py`` 몫이다.
FINALIZED_KST_HOUR = 17

ACTORS = (("individual", "indiv"), ("foreign_", "forgn"),
          ("institution", "inst"), ("etc_corp", "etc"))
#: 기관 세부. ``natn``(국가)은 90일 내내 전부 0 이라 뺀다(quant-airflow 실측).
INST_DETAIL = (("fnnc_invt", "금투"), ("insrnc", "보험"), ("invtrt", "투신"),
               ("bank", "은행"), ("penfnd_etc", "연기금"), ("samo_fund", "사모"))


def _finalized(date: str) -> bool:
    """수집 창이 지난 날짜인가 — 오늘이면 17시(KST) 이후. **시계만 본다.**

    값이 확정됐다는 뜻이 아니다. 당일 수급은 잠정이고 확정치는 다음날 새벽 ``daily_finalize`` 가
    DB 에 덮어쓴다. 키 이름은 리포트 형식(payload.json 바이트 동일)을 지키려고 그대로 둔다.
    """
    now = datetime.now(timezone(timedelta(hours=9)))
    return date != now.strftime("%Y-%m-%d") or now.hour >= FINALIZED_KST_HOUR


def load(con, days: int, asof: "str | None" = None) -> tuple[pd.DataFrame, list[str]]:
    """``asof`` 를 주면 그날까지의 거래일만 본다 — 지난 기준일을 지금 코드로 다시 만드는 재구성.

    아래 읽기는 전부 이 ``dates`` 의 끝(=기준일) 안에 묶인다: 수급·조정 종가는 날짜 구간, 시총은
    기준일 이하 주식수·이벤트(``market_cap_asof_bulk``), 업종지수는 ``index_returns`` 의 구간. 묶이지 않는 것은
    ``stocks`` 뿐이다 — 이력이 없는 지금의 마스터라 섹터·시장 분류와 유니버스는 지금 기준이다.
    """
    dates = recent_dates(con, days, asof=asof)
    if not dates:
        raise SystemExit("supply_demand 에 데이터가 없다 — 접속정보를 불러왔는지 확인하라.\n"
                         "  set -a; . ~/git/quant-airflow/.env; set +a  (또는 --db)")
    if asof is not None and dates[-1] != asof:
        raise SystemExit(f"{asof} 는 거래일이 아니다(그 이하 마지막 수급일 {dates[-1]}) — 휴장일이면 건너뛴다.")

    sd = read_supply_demand(
        con,
        cols=("code", "date", "close", "flu_rt", "acc_trde_qty",
              "individual", "foreign_", "institution", "etc_corp")
             + tuple(c for c, _ in INST_DETAIL),
        start=dates[0], end=dates[-1],
        # 개인·기관세부는 키움에만 있다. 소스를 안 좁히면 지표마다 유니버스가 달라진다.
        sources=("kiwoom",), allow_individual_survivorship=True, require_delisted=False,
    )
    sd["date"] = sd["date"].astype(str)
    meta = pd.read_sql_query("SELECT code, name, sector, market FROM stocks", con)
    df = sd.merge(meta, on="code", how="inner")
    df["close"] = df["close"].abs()
    raw = df["sector"].fillna("")
    df["sector"] = apply_sector_override(df["code"], df["sector"])
    OVERRIDE_APPLIED.clear()
    OVERRIDE_APPLIED.update({c: SECTOR_OVERRIDE[c] for c in
                             set(df.loc[raw.eq("") & df["code"].isin(list(SECTOR_OVERRIDE)), "code"])})

    for raw, short in ACTORS:
        df[short] = df[raw].astype(float) * df["close"] / EOK
    for raw, _ in INST_DETAIL:
        df[raw] = df[raw].astype(float) * df["close"] / EOK
    df["tv"] = df["acc_trde_qty"].astype(float) * df["close"] / EOK
    df["ret"] = adjusted_returns(con, df[["code", "date"]], dates)
    return df, dates


def apply_sector_override(codes: pd.Series, sectors: pd.Series) -> pd.Series:
    """마스터가 비워 둔 섹터를 :data:`SECTOR_OVERRIDE` 로 덮고, 남은 빈 값은 ``(미분류)``.

    덮는 것은 표에 적힌 종목뿐이다(근거와 함께 ``sectorflow/sector_override.py``). 검증기 F 층이
    같은 표로 SQL 을 짠다.
    """
    empty = sectors.fillna("").eq("")
    out = codes.map(SECTOR_OVERRIDE).where(empty)          # 마스터가 비었을 때만 — 채워지면 마스터를 따른다
    return out.where(out.notna(), sectors).fillna("").replace("", UNCLASSIFIED)


def adjusted_returns(con, keys: pd.DataFrame, dates: list[str]) -> pd.Series:
    """종목 일별 수익률[%] = ``daily_bars_adjusted`` 종가 ÷ 그 종목의 직전 거래일 종가 − 1.

    예전엔 ``supply_demand.flu_rt`` 를 썼다. 2026-09-14 부터 그 값의 분자는 애프터마켓까지 포함한
    통합 종가인데 기준가는 전일 **정규장** 종가라, 날마다 이어 곱하면 실제 종가 비와 어긋난다
    (20일 섹터 수익률 중앙 1.9%p·최대 8.9%p, 2026-11-08 지표 검증). quant-airflow 스키마 문서도
    "flu_rt 를 수익률로 쓰지 않는다 — 수익률은 daily_bars_adjusted 에서" 라고 적는다. 같은 표 안의
    종가 비라 세션 정의가 양쪽에서 같고, 분할·병합은 백조정돼 있다.

    그날 조정 종가가 없으면(조정 표가 아직 안 돈 저녁) ``NaN`` 이다 — 0 으로 채우지 않는다. 완전성
    관문이 기준일의 조정 종가 커버리지를 본다.
    """
    codes = keys["code"].unique().tolist()
    if not codes:
        return pd.Series([], dtype=float, index=keys.index)
    start = recent_dates(con, 2, asof=dates[0])[0]   # 첫날의 전일 종가가 있어야 첫날 수익률이 나온다
    ph = "%s" if _is_pg(con) else "?"
    adj = pd.read_sql_query(
        f"SELECT code, date, close FROM daily_bars_adjusted WHERE date >= {ph} AND date <= {ph}",
        con, params=[start, dates[-1]])
    adj["date"] = adj["date"].astype(str)
    adj = adj[adj["code"].isin(codes) & adj["close"].notna() & (adj["close"] > 0)]
    adj = adj.sort_values(["code", "date"])
    adj["ret"] = adj.groupby("code")["close"].pct_change() * 100.0
    got = adj.set_index(["code", "date"])["ret"]
    out = got.reindex(pd.MultiIndex.from_arrays([keys["code"], keys["date"]])).to_numpy()
    return pd.Series(out, index=keys.index, dtype=float)


def attach_daily_cap(con, df: pd.DataFrame) -> pd.DataFrame:
    """종목별 일별 시총과 **전일** 시총을 붙인다 — 섹터 수익률의 가중치.

    거래대금 가중은 못 쓴다. 그날 급등한 종목이 거래대금도 크므로 가중치와 수익률이
    양의 상관을 갖고, 섹터 수익률이 체계적으로 부풀려진다(실측: 3종목짜리 부동산이
    20거래일 +102%). 표준은 **전일** 시총 가중이다 — 가중치가 수익률보다 먼저
    정해져야 편향이 안 생긴다.
    """
    key = df[["code", "date"]].drop_duplicates()
    key["cap"] = market_cap_asof_bulk(con, key).to_numpy() / EOK
    out = df.merge(key, on=["code", "date"], how="left").sort_values(["code", "date"])
    out["cap_lag"] = out.groupby("code")["cap"].shift(1)
    return out


def index_returns(con, dates: list[str], sectors, markets,
                  members: dict | None = None) -> dict:
    """KRX 업종지수의 일별 수익률 — 섹터 수익률의 **실측 기준선**.

    처음엔 종목 등락률을 섹터로 집계해 썼는데, 그러면 가중치 선택이 그대로 오차가
    된다(같은 날 거래대금 가중은 급등주에 가중치를 몰아 20거래일 +102% 같은 값을
    만들었고, 전일 시총 가중으로 바꿔도 20일은 정확했지만 60일에서 KRX 대비 중앙값
    17%p 벌어졌다). 벤더 지수가 DB 에 있으므로 그걸 쓴다.

    이름이 같은 코드가 둘이면 작은 코드가 거래소, 큰 코드가 코스닥이다. 코드가
    하나뿐인 업종(보험·증권 등 코스피 전용)은 거래소에만 붙인다. 지수가 아예 없는
    섹터는 ``None`` 으로 남겨, 소비자가 **섞지 않고 제외**할 수 있게 한다.
    """
    # 구간 밖 행은 ``reindex(dates)`` 가 어차피 버린다 — 재구성에서 기준일 뒤 행을 읽지 않게 SQL 에서 묶는다.
    ph = "%s" if _is_pg(con) else "?"
    si = pd.read_sql_query(
        f"SELECT code, name, date, close FROM sector_index WHERE date >= {ph} AND date <= {ph}",
        con, params=[dates[0], dates[-1]])
    si["date"] = si["date"].astype(str)
    lookup: dict = {m: {} for m in markets}
    for name, g in si.groupby("name"):
        codes = sorted(g["code"].unique())
        mapping = ({"거래소": codes[0], "코스닥": codes[1]} if len(codes) == 2
                   else {"거래소": codes[0]})
        for m, c in mapping.items():
            if m not in lookup:
                continue
            ser = (g[g["code"] == c].set_index("date")["close"]
                   .reindex(dates).astype(float).ffill())
            r = ser.pct_change() * 100.0
            lookup[m][name] = [None if pd.isna(v) else round(float(v), 4) for v in r]
    # ⚠️ 그 시장에 **종목이 하나도 없는** (시장,섹터)에는 지수를 붙이지 않는다.
    # 이름만 맞으면 매핑되던 이전 판본에서는 거래소/기타제조·거래소/출판매체복제·
    # 코스닥/제조 처럼 구성종목이 0 인 칸에 지수가 달렸고, 자금흐름이 0 인 칸의
    # 수익률만 살아 있어 "다른 바구니를 맞대는" 부류가 만들어졌다.
    # 근원에서 끊는다 — 소비자마다 걸러내면 한 곳을 빠뜨린다.
    def _has(mk: str, sec: str) -> bool:
        return not members or bool(members.get(mk, {}).get(sec))

    return {m: {s: (lookup[m].get(s) if _has(m, s) else None) for s in sectors}
            for m in markets}


def sector_cap(con, df: pd.DataFrame, last_date: str) -> pd.DataFrame:
    """기간말 섹터 시가총액(억) — 비중 변화의 분모."""
    codes = df[["code", "sector", "market"]].drop_duplicates("code").copy()
    codes["date"] = last_date
    if "cap" in df.columns:
        known = (
            df.loc[df["date"] == last_date, ["code", "cap"]]
            .drop_duplicates("code")
            .set_index("code")["cap"]
        )
        codes["cap"] = codes["code"].map(known)
        miss = ~codes["code"].isin(known.index)
        if miss.any():
            codes.loc[miss, "cap"] = (
                market_cap_asof_bulk(con, codes.loc[miss, ["code", "date"]]).to_numpy() / EOK
            )
    else:
        codes["cap"] = market_cap_asof_bulk(con, codes[["code", "date"]]).to_numpy() / EOK
    return codes.dropna(subset=["cap"])


def sector_cap_monthly(con, df: pd.DataFrame, dates: list[str]) -> dict:
    """월말 시점별 섹터 시가총액 — 임의 구간의 분모.

    이전 판본은 페이로드 **마지막 날짜**의 시총 하나만 실었다. 뷰어는 시작·종료일을
    직접 고를 수 있으므로, 1년 전으로 끝나는 구간의 유량을 **오늘** 시총으로 나누게
    된다. 실측: 구간말이 2025-08 일 때 오늘/그때 시총 비가 중앙값 1.33배,
    27개 중 9개가 1.5배 이상 — 그만큼 가속도가 작게 나왔다.
    """
    months: dict[str, str] = {}
    for d in dates:
        months[d[:7]] = d                      # 각 월의 마지막 거래일
    if not months:
        return {}
    codes = df[["code", "sector", "market"]].drop_duplicates("code")
    ym_items = sorted(months.items())
    q_all = pd.concat(
        [codes.assign(ym=ym, date=last) for ym, last in ym_items],
        ignore_index=True,
    )
    if "cap" in df.columns:
        end_dates = {last for _, last in ym_items}
        known = (
            df.loc[df["date"].isin(end_dates), ["code", "date", "cap"]]
            .drop_duplicates(["code", "date"])
        )
        q_all = q_all.merge(known, on=["code", "date"], how="left")
        miss = ~q_all.set_index(["code", "date"]).index.isin(
            known.set_index(["code", "date"]).index
        )
        if miss.any():
            q_all.loc[miss, "cap"] = (
                market_cap_asof_bulk(con, q_all.loc[miss, ["code", "date"]]).to_numpy() / EOK
            )
    else:
        q_all["cap"] = market_cap_asof_bulk(con, q_all[["code", "date"]]).to_numpy() / EOK
    m = q_all.dropna(subset=["cap"])
    out: dict = {}
    for (ym, mk, sec), g in m.groupby(["ym", "market", "sector"]):
        out.setdefault(mk, {}).setdefault(sec, {})[ym] = round(float(g["cap"].sum()), 1)
    return out


def build_payload(df: pd.DataFrame, dates: list[str], caps: pd.DataFrame,
                  cap_monthly: dict | None = None) -> dict:
    sectors = sorted(df["sector"].unique())
    markets = sorted(df["market"].dropna().unique())
    di = {d: i for i, d in enumerate(dates)}
    n = len(dates)

    def zeros():
        return [0.0] * n

    flows: dict = {m: {s: {k: zeros() for k in ("indiv", "forgn", "inst", "etc", "tv")}
                       for s in sectors} for m in markets}
    g = df.groupby(["market", "sector", "date"], sort=False)[
        ["indiv", "forgn", "inst", "etc", "tv"]].sum()
    for (m, s, d), row in g.iterrows():
        if m not in flows:
            continue
        i = di[d]
        for k in ("indiv", "forgn", "inst", "etc", "tv"):
            flows[m][s][k][i] = round(float(row[k]), 2)

    detail: dict = {m: {s: {k: zeros() for k, _ in INST_DETAIL} for s in sectors}
                    for m in markets}
    gd = df.groupby(["market", "sector", "date"], sort=False)[
        [k for k, _ in INST_DETAIL]].sum()
    for (m, s, d), row in gd.iterrows():
        if m not in detail:
            continue
        i = di[d]
        for k, _ in INST_DETAIL:
            detail[m][s][k][i] = round(float(row[k]), 2)

    cap = {m: {s: round(float(v), 1) for s, v in
               caps[caps["market"] == m].groupby("sector")["cap"].sum().items()}
           for m in markets}

    # 종목 드릴다운 — **전 종목**의 구간별 집계.
    #
    # ⚠️ 이전 판본은 (시장,섹터)당 거래대금 상위 8 ∪ 기관 순매수 상위 4 를 **1년
    # 전체 기준**으로 미리 뽑아 389종목만 실었다. 그래서 화면의 순위가 "그 섹터
    # 전체의 순매수 순위"가 아니라 "미리 뽑아둔 12개 안에서의 순위"였다. 밖에 더 큰
    # 종목이 있어도 안 보였다 — 실측: 20일 기준 각 섹터 순매수 상위 3 중 **32% 누락**,
    # 금융에서 GS(+1,854억)가 화면에 있는 하림지주(+247억)보다 7배 큰데 빠졌다.
    # 누락되는 쪽이 하필 **최근에 방향이 바뀐** 종목이라(1년 누적이 0 근처) 성격이 나쁘다.
    #
    # 일별 배열로 전 종목을 실으면 ~4MB 가 붙는다. **프리셋 구간의 합계만** 실으면
    # 2,645종목 × 4구간 × 5값 ≈ 200KB 다. 임의 구간 드릴다운(뷰어의 날짜 직접 지정)을
    # 포기하고 프리셋(5·20·60·120일)에서 전 종목을 정확히 보는 쪽을 택한다.
    name_cap = (caps.set_index("code")["cap"].to_dict()
                if "cap" in getattr(caps, "columns", []) else {})
    meta = df.drop_duplicates("code").set_index("code")[["name", "sector", "market"]]
    names: dict = {}
    for code in meta.index:
        row = meta.loc[code]
        # ⚠️ `cap` 은 위에서 만든 **섹터 시총 dict** 다. 여기서 같은 이름을 쓰면
        # 조용히 덮어써서 페이로드의 cap 이 float 하나가 된다(실제로 밟았다).
        ncap = name_cap.get(code)
        names[code] = {
            "name": row["name"], "sector": row["sector"], "market": row["market"],
            "cap": (round(float(ncap), 1) if ncap is not None and ncap == ncap else None),
            "win": {},
        }
    # 창별 종목 집계에 실리는 값. 4주체·거래대금에 더해:
    #
    # * ``invtrt``·``penfnd_etc`` — 기관 세부 중 **투신·연기금**. 기관 총액이 같아도
    #   이 둘이 채운 것과 금투(증권사 자기매매 — 헤지·차익이 섞여 방향성이 약하다)가
    #   채운 것은 다른 이야기인데, `inst` 한 덩어리에는 그 구분이 없다. 실측(20거래일):
    #   SK하이닉스 기관 −13,157억인데 투신 +1,549 · 연기금 +4,614 · 금투 −22,611 이다.
    #   원본 `df` 에 이미 종목·일별로 실려 있고 `load()` 에서 종가 환산까지 끝났으므로
    #   집계 대상에 이름만 더하면 된다. 2,645종목 × 4창 × 2값이라 크기는 미미하다.
    # * ``ret`` — 그 구간의 종목 수익률(%). 화면의 `상대수익` 이 이것에서 섹터
    #   수익률을 빼서 "섹터는 갔는데 이건 안 갔다" 를 직접 답한다.
    _WIN_KEYS = ("inst", "forgn", "indiv", "etc", "tv", "invtrt", "penfnd_etc")
    for win in NAME_WINDOWS:
        if win > n:
            continue
        sub = df[df["date"] >= dates[n - win]]
        agg = sub.groupby("code")[list(_WIN_KEYS)].sum()
        # **기하** 누적이다. 일별 등락률을 그냥 더하면 120일 구간에서 실제와 크게
        # 벌어진다(+10% 와 −10% 를 더하면 0 이지만 실제는 −1%). 결측일은 0 으로
        # 본다 — 그날 거래가 없었다는 뜻이고, 빼면 구간 길이가 종목마다 달라진다.
        #
        # ⚠️ `df["ret"]` 은 **퍼센트**다(조정 종가 비 × 100). 분수로
        # 착각해 1+ret 를 곱하면 하루 +2.5% 가 +250% 가 되어 20일 누적이 1e14 로
        # 튄다 — 실제로 한 번 그렇게 냈고 verify_report 의 −100% 하한이 잡았다.
        rr = (sub.assign(_r=1.0 + sub["ret"].fillna(0.0) / 100.0)
                 .groupby("code")["_r"].prod() - 1.0) * 100.0
        for code, r in agg.iterrows():
            w = {k: round(float(r[k]), 1) for k in _WIN_KEYS}
            v = rr.get(code)
            w["ret"] = round(float(v), 2) if v is not None and v == v else None
            names[code]["win"][str(win)] = w

    # 참고용 자체 계산(전일 시총 가중). 화면의 수익률은 KRX 업종지수를 쓴다 —
    # 아래 값은 지수가 없는 섹터의 폴백이자 대조용이다.
    ret: dict = {m: {s: zeros() for s in sectors} for m in markets}
    rw: dict = {m: {s: zeros() for s in sectors} for m in markets}   # 그날 유효 가중합
    for (m, s, d), grp in df.groupby(["market", "sector", "date"], sort=False):
        if m not in ret:
            continue
        # 수익률이 없는 종목(그날 조정 종가 없음)은 가중치에서도 뺀다 — 남기면 평균이 0 쪽으로 끌린다.
        g = grp.dropna(subset=["cap_lag", "ret"])
        w = float(g["cap_lag"].sum())
        ret[m][s][di[d]] = round(float((g["ret"] * g["cap_lag"]).sum() / w) if w else 0.0, 3)
        rw[m][s][di[d]] = round(w, 1)

    # (시장, 섹터)별 실제 종목 수 — 표에서 "섹터로 읽을 만한가" 를 가리는 기준.
    counts = {m: {} for m in markets}
    for (m, sec), g in df.groupby(["market", "sector"], sort=False):
        if m in counts:
            counts[m][sec] = int(g["code"].nunique())

    out = {
        "dates": dates,
        "sectors": sectors,
        "n_by_sector": counts,
        "markets": markets,
        "flows": flows,
        "detail": detail,
        "ret": ret,
        "retw": rw,
        "iret": INDEX_RET,
        "cap": cap,
        "cap_by_month": cap_monthly or {},
        "names": names,
        "n_names": int(df["code"].nunique()),
        "finalized": _finalized(dates[-1]),
        "detail_labels": {k: ko for k, ko in INST_DETAIL},
    }
    out["today"] = today_block(out, _market_daily(df, dates, markets))
    out["today"]["streaks"] = streak_block(out)
    out["today"]["day_change"] = day_change_block(out)
    out["today"]["concentration"] = concentration_block(out)
    return out


# ─────────────────────────────────────────────── 오늘 — 무엇이 바뀌었나 (사실만, 점수 없음)
#
# 화면이 답할 질문: "오늘 누가 어디를 샀고 팔았나, 그게 얼마나 드문가, 어제 리포트와 무엇이 달라졌나".
# 아래는 전부 페이로드에 이미 있는 일별 배열의 **마지막 날**과 그 1년 분포, 그리고 직전 리포트와의 대조다.
# 판정·점수는 없다(CLAUDE.md 화면·리포트 규칙).

#: 오늘 이상도를 재는 되돌아보기(거래일, 오늘 포함). 수치표의 `1년[%ile]`(``sector_numbers.PCT_LOOKBACK``)과 같다.
TODAY_LOOKBACK = 260
#: 값이 있는(0 이 아닌) 날이 되돌아보기의 이 몫 미만이면 백분위를 안 낸다(분모 게이트). 0 은 "0 또는
#: 미보고"라, 거의 다 0 인 줄의 midrank 백분위는 "1년 중 몇 등" 이 아니라 결측의 모양을 말한다.
TODAY_MIN_LIVE = 0.25
#: 오늘 표의 주체. ``invpen`` = 투신+연기금 — 기관 안에서 방향성이 있는 둘(CLAUDE.md 수급 신뢰 순서).
TODAY_ACTORS = (("inst", "기관"), ("invtrt", "투신"), ("penfnd_etc", "연기금"),
                ("invpen", "투신+연기금"), ("forgn", "외국인"), ("indiv", "개인"),
                ("etc", "기타법인"))
#: 시장 합계에 싣는 주체 — 4주체(합 ≈ 0)와 기관 세부 금투·투신·연기금. 금투가 기관 총액을 채우는 날을 화면이 적는다
#: (2026-11-06 기관 +19,714억 중 금투 +20,181억·투신 −3,186억).
MARKET_ACTORS = ("inst", "forgn", "indiv", "etc", "fnnc_invt", "invtrt", "penfnd_etc", "invpen")
#: 시장 맥락 수익률의 구간(거래일).
MARKET_RET_WINDOWS = (1, 20)
#: 직전 리포트 대비 — 이 구간 순매수 순위와 상위 몇 섹터의 들고 남.
CHANGE_WINDOW = 20
CHANGE_ACTORS = ("inst", "invpen")
CHANGE_TOP = 5
#: 잠정 → 확정 비교 칸. 일별 값이 소수 2자리라 5일 합의 반올림 차는 양쪽 합쳐 최대 0.05억이다.
#: 차이도 소수 2자리로 **반올림해서** 이 문턱과 잰다 — 7.86 − 7.81 = 0.05000000000000071 을 그대로 재면 0.05 칸이
#: '바뀜' 으로 실리고, 반올림한 ``diff`` 를 보는 검증기가 그 칸을 거부한다(2026-11-09 리허설, 시드 22일 중 20일).
FINAL_DIFF_WINDOWS = (1, 5)
FINAL_DIFF_ACTORS = ("inst", "invtrt", "penfnd_etc", "forgn", "indiv", "etc")
FINAL_DIFF_TOL = 0.05
_DETAIL_KEYS = {k for k, _ in INST_DETAIL}
_DATE_DIR = re.compile(r"\d{4}-\d{2}-\d{2}(\.building\.\d+)?")


def _mkeys(P: dict) -> list[tuple[str, list[str]]]:
    """수치표와 같은 시장 키 — ``전체`` 는 두 시장의 합."""
    return [("전체", list(P["markets"]))] + [(m, [m]) for m in P["markets"]]


def _present(P: dict, markets: list[str], sec: str) -> bool:
    """그 시장(들)에 종목이 하나라도 있는 섹터인가 — 없는 칸은 0 이 아니라 해당 없음이다."""
    return sum(int((P.get("n_by_sector") or {}).get(m, {}).get(sec, 0) or 0) for m in markets) > 0


def _series(P: dict, markets: list[str], sec: str, key: str) -> "list[float] | None":
    """시장을 합친 (섹터, 주체) 일별 순매수[억]. 투신·연기금은 ``detail``, ``invpen`` 은 그 합.

    값은 소수 2자리(페이로드 자릿수)로 맞춘다 — 백분위의 동률 판정이 합하는 순서의 부동소수
    잔차에 흔들리지 않게(검증기는 다른 순서로 더한다).
    """
    if key == "invpen":
        a, b = (_series(P, markets, sec, k) for k in ("invtrt", "penfnd_etc"))
        return None if a is None or b is None else [round(x + y, 2) for x, y in zip(a, b)]
    src = P.get("detail" if key in _DETAIL_KEYS else "flows") or {}
    n = len(P["dates"])
    out, seen = [0.0] * n, False
    for m in markets:
        arr = src.get(m, {}).get(sec, {}).get(key)
        if arr is None:
            continue
        seen = True
        for i, v in enumerate(arr[:n]):
            out[i] += v or 0.0
    return [round(v, 2) for v in out] if seen else None


def day_pct(ser: list[float]) -> "float | None":
    """마지막 날 값의 백분위(0~100) — 오늘 포함 최근 ``TODAY_LOOKBACK`` 일 일별 값 안에서.

    ``sector_numbers.rolling_pct`` 의 1일 창과 같은 정의다: 동률은 중간순위(midrank), 자기 자신을
    분포에 넣는다. 페이로드에는 기준일 이하의 날만 있으므로 미래를 볼 수 없다.
    """
    n = min(len(ser), TODAY_LOOKBACK)
    if n < 2:
        return None
    s = ser[-n:]
    if sum(1 for v in s if v) < TODAY_MIN_LIVE * n:
        return None
    cur = s[-1]
    below = sum(1 for v in s if v < cur)
    ties = sum(1 for v in s if v == cur)           # 자기 자신 포함
    return 100.0 * (below + (ties - 1) / 2.0) / (n - 1)


def _fact(ser: list[float]) -> dict:
    p = day_pct(ser)
    return {"v": round(ser[-1], 2), "pct": None if p is None else round(p, 1)}


def _market_daily(df: pd.DataFrame, dates: list[str], markets: list[str]) -> dict:
    """시장 단위 일별 순매수와 자체 바구니 수익률 — **종목에서 바로** 모은다(섹터 배열을 거치지 않는다).

    수익률 정의는 섹터 수익률과 같다: 그날 조정 종가 비 수익률의 **전일 시총** 가중 평균, 수익률이나
    전일 시총이 없는 종목은 가중치에서도 뺀다. 가중치가 0 인 날은 ``None``.
    """
    di = {d: i for i, d in enumerate(dates)}
    n = len(dates)
    keys = [k for k in MARKET_ACTORS if k != "invpen"] + ["tv"]
    out: dict = {}
    for mkey, mks in [("전체", markets)] + [(m, [m]) for m in markets]:
        sub = df[df["market"].isin(mks)]
        flows = {k: [0.0] * n for k in keys}
        for d, row in sub.groupby("date")[keys].sum().iterrows():
            if d in di:
                for k in keys:
                    flows[k][di[d]] = float(row[k])
        flows["invpen"] = [a + b for a, b in zip(flows["invtrt"], flows["penfnd_etc"])]
        ret: list = [None] * n
        g = sub.dropna(subset=["cap_lag", "ret"])
        num = (g["ret"] * g["cap_lag"]).groupby(g["date"]).sum()
        den = g.groupby("date")["cap_lag"].sum()
        for d, w in den.items():
            if d in di and w:
                ret[di[d]] = float(num[d] / w)
        out[mkey] = {"flows": flows, "ret": ret}
    return out


def today_block(P: dict, mkt: dict) -> dict:
    """``payload["today"]`` — 기준일 하루의 섹터×주체 순매수와 그 1년 백분위, 시장 합계·시장 수익률.

    섹터 값은 페이로드의 일별 배열(``flows``·``detail``) 마지막 칸 그대로다. 1일 창에는 x·G·가속을
    싣지 않는다 — 회귀·2차 차분이 하루치에서 정의되지 않는다.
    """
    dates = P["dates"]
    secs: dict = {}
    for mkey, mks in _mkeys(P):
        rows = {}
        for s in P["sectors"]:
            if not _present(P, mks, s):
                continue
            row = {}
            for a, _ in TODAY_ACTORS:
                ser = _series(P, mks, s, a)
                if ser is not None:
                    row[a] = _fact(ser)
            row["tv"] = round(_series(P, mks, s, "tv")[-1], 2)
            num = den = 0.0
            for m in mks:
                r = P["ret"].get(m, {}).get(s)
                w = P["retw"].get(m, {}).get(s)
                if r and w and r[-1] is not None and w[-1]:
                    num += r[-1] * w[-1]
                    den += w[-1]
            row["ret"] = round(num / den, 3) if den else None
            rows[s] = row
        secs[mkey] = rows

    market: dict = {}
    for mkey, d in mkt.items():
        net = {a: _fact([round(v, 2) for v in d["flows"][a]]) for a in MARKET_ACTORS}
        ret = {}
        for w in MARKET_RET_WINDOWS:
            span = d["ret"][-w:] if w <= len(dates) else []
            if not span or span[-1] is None:
                ret[str(w)] = None
                continue
            acc = 1.0
            for r in span:                       # 가중치가 0 인 날은 0% — sector_numbers.agg 와 같다
                acc *= 1.0 + (r or 0.0) / 100.0
            ret[str(w)] = round((acc - 1.0) * 100.0, 3)
        market[mkey] = {"net": net, "tv": round(d["flows"]["tv"][-1], 2), "ret": ret}

    return {
        "date": dates[-1],
        "lookback": min(len(dates), TODAY_LOOKBACK),
        "min_live": TODAY_MIN_LIVE,
        "actors": {a: ko for a, ko in TODAY_ACTORS},
        "sectors": secs,
        "market": market,
    }


# ─────────────────────────────────────────────── 연속 순매매 — 1일 순매수 부호가 같은 날이 며칠째인가
#
# "외국인이 거래소에서 며칠째 팔고 있나" 를 사실로 적는다. 부호만 센다 — 판정·점수가 아니다.

#: 연속을 재는 주체. 시장 줄은 :data:`STREAK_MARKET_ACTORS`, 섹터 최장은 기관·투신+연기금·그날 최대 순매매 주체
#: (기타법인일 수 있어 넣는다).
STREAK_ACTORS = (("inst", "기관"), ("invpen", "투신+연기금"), ("forgn", "외국인"), ("indiv", "개인"),
                 ("etc", "기타법인"))
STREAK_MARKET_ACTORS = ("inst", "invpen", "forgn", "indiv")
#: 분모 게이트 — 그날 |값| 이 같은 (시장, 주체) 섹터 목록의 Σ|값| 의 이 몫 미만이면 **≈0 날**이다. ≈0 날은
#: 사는 날도 파는 날도 아니어서 연속을 끊는다(오늘이 ≈0 이면 연속이 없다). 시장 합계도 같은 목록(그 시장의
#: 섹터)에 견준다. 문턱은 목록 안 몫이다(CLAUDE.md 화면·리포트 규칙).
STREAK_EPS_SHARE = 0.01
#: 섹터 연속에서 빼는 얇은 섹터 — 종목 수가 이보다 적으면 한두 종목이 부호를 정한다(수치표 ``MIN_NAMES`` 와
#: 같은 값). ``(미분류)`` 는 섹터가 아니라 뺀다.
STREAK_MIN_MEMBERS = 10


def _members(P: dict, mks: list[str], sec: str) -> int:
    return sum(int((P.get("n_by_sector") or {}).get(m, {}).get(sec, 0) or 0) for m in mks)


def streak_fact(ser: list[float], gross: list[float], lookback: int = TODAY_LOOKBACK) -> dict:
    """마지막 날까지 1일 순매수 부호가 같은 날 수와 그 누적, 그 길이의 1년 백분위.

    ``sign`` 은 +1(순매수)·−1(순매도)·0(≈0 날 — 연속 없음). 백분위는 되돌아보기 안의 **날마다의 연속 길이**
    (부호 무관, ≈0 날은 0) 분포에서 오늘 길이의 midrank(:func:`day_pct` 와 같은 정의)다. 페이로드 첫날보다
    앞은 모르므로 첫 며칠의 길이는 짧게 잡힌다.
    """
    sig = [0 if not g or abs(v) < STREAK_EPS_SHARE * g else (1 if v > 0 else -1) for v, g in zip(ser, gross)]
    runs: list[int] = []
    for i, s in enumerate(sig):
        runs.append(0 if s == 0 else runs[-1] + 1 if i and sig[i - 1] == s else 1)
    k = runs[-1] if runs else 0
    n = min(len(runs), lookback)
    pct = None
    if k and n >= 2:
        win = runs[-n:]
        below = sum(1 for r in win if r < k)
        ties = sum(1 for r in win if r == k)
        pct = round(100.0 * (below + (ties - 1) / 2.0) / (n - 1), 1)
    return {"sign": sig[-1] if sig else 0, "days": k,
            "sum": round(sum(ser[len(ser) - k:]), 2) if k else 0.0, "pct": pct}


def streak_block(P: dict) -> dict:
    """``payload["today"]["streaks"]`` — 시장 합계와 섹터(얇은 섹터·미분류 제외)의 연속 순매매.

    시장 합계는 그 시장(들)의 섹터 일별 배열의 합이다(검증기가 같은 배열에서 다시 센다). ``longest`` 는
    (시장, 주체)마다 연속이 가장 긴 섹터 — 동률은 누적 |금액| 큰 쪽, 그다음 이름순.
    """
    n = len(P["dates"])
    out: dict = {"eps_share": STREAK_EPS_SHARE, "min_members": STREAK_MIN_MEMBERS,
                 "lookback": min(n, TODAY_LOOKBACK), "actors": {a: ko for a, ko in STREAK_ACTORS},
                 "market_actors": list(STREAK_MARKET_ACTORS), "market": {}, "sectors": {}, "longest": {}}
    for mkey, mks in _mkeys(P):
        present = [s for s in P["sectors"] if _present(P, mks, s)]
        for a, _ko in STREAK_ACTORS:
            sers = {s: ser for s in present if (ser := _series(P, mks, s, a)) is not None}
            if not sers:
                continue
            gross = [sum(abs(x[i]) for x in sers.values()) for i in range(n)]
            total = [round(sum(x[i] for x in sers.values()), 2) for i in range(n)]
            out["market"].setdefault(mkey, {})[a] = streak_fact(total, gross)
            best = None
            for s, ser in sers.items():
                if s == UNCLASSIFIED or _members(P, mks, s) < STREAK_MIN_MEMBERS:
                    continue
                f = streak_fact(ser, gross)
                out["sectors"].setdefault(mkey, {}).setdefault(s, {})[a] = f
                key = (-f["days"], -abs(f["sum"]), s)
                if f["days"] and (best is None or key < best):
                    best = key
            out["longest"].setdefault(mkey, {})[a] = best[2] if best else None
    return out


# ─────────────────────────────────────────────── 하루 단위 변화 — 어제와 무엇이 다른가
#
# 극단 목록은 오늘 값만 말하고, 부호 반전 줄은 5일 대 15일을 말한다. 어제 하루와의 차이는 어디에도 없었다
# (11-05→11-06 기관 전기/전자 −1,625→+7,057). 세기만 한다 — 앞날을 말하지 않는다(CLAUDE.md 화면·리포트 규칙).

#: 극단(1년 %ile)의 **보이는 정수** 문턱 — 화면에 95 로 적힌 날은 95 로 센다(``digest.pct_int`` 와 같은 반올림).
DAY_PCT_HI, DAY_PCT_LO = 95, 5
#: 하루 단위 변화를 싣는 주체 — 첫 화면이 적을 수 있는 주체(기관·투신+연기금·그날 최대 순매매 주체).
DAY_ACTORS = ("inst", "invpen", "forgn", "indiv", "etc")
#: 부호가 바뀐 섹터의 크기 문턱 — |어제| 와 |오늘| 이 각각 그날 (시장, 주체) 섹터 목록 Σ|값| 의 이 몫 이상.
#: 작은 칸의 +2억 → −3억 은 부호가 잡음이다. 문턱은 목록 안 몫으로 잡는다(CLAUDE.md 화면·리포트 규칙).
DAY_FLIP_MIN_SHARE = 0.02


def _visible_pct(p: float) -> int:
    """화면이 적는 백분위 정수 — 소수 1자리로 실은 값을 0.5 올림(``flow_view.pct_int`` 와 같은 값)."""
    return int(math.floor(round(p, 1) + 0.5 + 1e-9))


def extreme_run(ser: list[float], hi: bool) -> int:
    """마지막 날부터 거슬러 **그날 값의 1년 %ile** 이 같은 쪽 극단인 날 수. 오늘이 극단이 아니면 0.

    날마다 그날까지의 일별 값으로 다시 잰다(:func:`day_pct`) — 오늘 분포로 어제를 재면 미래를 본다.
    페이로드 첫날보다 앞은 모르므로 그 앞에서 끊긴다.
    """
    k = 0
    for end in range(len(ser), 1, -1):
        p = day_pct(ser[:end])
        if p is None:
            break
        q = _visible_pct(p)
        if not (q >= DAY_PCT_HI if hi else q <= DAY_PCT_LO):
            break
        k += 1
    return k


def day_change_block(P: dict) -> dict:
    """``payload["today"]["day_change"]`` — 극단 목록의 지속 일수와 어제와 부호가 바뀐 섹터.

    ``extreme[시장][주체]["hi"|"lo"][섹터]`` = 그 극단에 며칠째인가(1 = 오늘 처음). ``flips[시장][주체]`` = 어제와
    부호가 반대이고 양일 다 크기 문턱을 넘은 섹터(|오늘| 큰 순). 얇은 섹터·``(미분류)`` 는 부호 줄에서 뺀다.
    """
    dates = P["dates"]
    out: dict = {"prev_date": dates[-2] if len(dates) > 1 else None, "pct_hi": DAY_PCT_HI, "pct_lo": DAY_PCT_LO,
                 "flip_min_share": DAY_FLIP_MIN_SHARE, "min_members": STREAK_MIN_MEMBERS, "extreme": {}, "flips": {}}
    for mkey, mks in _mkeys(P):
        present = [s for s in P["sectors"] if _present(P, mks, s)]
        for a in DAY_ACTORS:
            sers = {s: x for s in present if (x := _series(P, mks, s, a)) is not None}
            ext: dict = {"hi": {}, "lo": {}}
            for s, x in sers.items():
                for side, hi in (("hi", True), ("lo", False)):
                    k = extreme_run(x, hi)
                    if k:
                        ext[side][s] = k
            out["extreme"].setdefault(mkey, {})[a] = ext
            flips = []
            if len(dates) > 1:
                g_now = sum(abs(x[-1]) for x in sers.values())
                g_prev = sum(abs(x[-2]) for x in sers.values())
                for s, x in sers.items():
                    if s == UNCLASSIFIED or _members(P, mks, s) < STREAK_MIN_MEMBERS:
                        continue
                    if (x[-1] * x[-2] < 0 and abs(x[-1]) >= DAY_FLIP_MIN_SHARE * g_now
                            and abs(x[-2]) >= DAY_FLIP_MIN_SHARE * g_prev):
                        flips.append({"sector": s, "prev": x[-2], "now": x[-1]})
                flips.sort(key=lambda f: (-abs(f["now"]), f["sector"]))
            out["flips"].setdefault(mkey, {})[a] = flips
    return out


# ─────────────────────────────────────────────── 거래대금 집중 — 섹터 하나가 사실상 두 종목인 날
#
# 전기/전자는 2026-11-06 전체 시장 거래대금의 63% 이고 그 76% 가 두 종목이다(외국인 |순매수| 1위 48일 중 44일).
# 섹터 합계만 적으면 그 칸이 두 종목의 이야기라는 걸 모른다. 분류는 그대로 두고 두 종목을 뺀 값을 나란히 적는다.

#: 집중 줄에 싣는 섹터 — 그날 그 시장 거래대금의 이 몫 이상. 문턱은 목록 안 몫이다.
CONCENTRATION_MIN_SHARE = 0.20
#: 거래대금 상위 몇 종목을 보나.
CONCENTRATION_TOP = 2
#: 두 종목을 뺀 값을 적는 주체.
CONCENTRATION_ACTORS = ("inst", "invpen", "forgn", "indiv", "etc")


def concentration_block(P: dict) -> dict:
    """``payload["today"]["concentration"]`` — 시장별로 거래대금 몫이 큰 섹터의 상위 종목 몫과 그 종목을 뺀 순매수.

    종목 값은 ``names[code].win["1"]``(소수 1자리), 섹터 값은 ``today.sectors`` 다. 얇은 섹터·``(미분류)`` 는 뺀다.
    """
    T = P["today"]
    out: dict = {"min_share": CONCENTRATION_MIN_SHARE, "top_n": CONCENTRATION_TOP, "markets": {}}
    for mkey, mks in _mkeys(P):
        total = ((T["market"].get(mkey) or {}).get("tv")) or 0.0
        rows = T["sectors"].get(mkey) or {}
        picked = []
        for s, row in rows.items():
            tv = row.get("tv") or 0.0
            if (s == UNCLASSIFIED or _members(P, mks, s) < STREAK_MIN_MEMBERS or not total
                    or tv < CONCENTRATION_MIN_SHARE * total):
                continue
            stocks = []
            for code, nm in (P.get("names") or {}).items():
                w = (nm.get("win") or {}).get("1")
                if w and nm.get("sector") == s and nm.get("market") in mks and w.get("tv"):
                    stocks.append((-w["tv"], str(code), nm.get("name", "—"), w))
            stocks.sort(key=lambda t: t[:2])
            top = stocks[:CONCENTRATION_TOP]
            if not top:
                continue

            def stock_value(w: dict, a: str) -> float:
                return ((w.get("invtrt") or 0.0) + (w.get("penfnd_etc") or 0.0)) if a == "invpen" else (w.get(a) or 0.0)
            rest = {a: round(((row.get(a) or {}).get("v") or 0.0) - sum(stock_value(t[3], a) for t in top), 1)
                    for a in CONCENTRATION_ACTORS}
            top_tv = sum(-t[0] for t in top)
            picked.append({"sector": s, "tv": tv, "market_share_pct": round(tv / total * 100.0, 1),
                           "top": [{"code": t[1], "name": t[2], "tv": -t[0]} for t in top],
                           "top_share_pct": round(top_tv / tv * 100.0, 1), "rest": rest})
        picked.sort(key=lambda r: (-r["tv"], r["sector"]))
        out["markets"][mkey] = picked
    return out


def _ranks(P: dict, mks: list[str], actor: str, win: int):
    """섹터의 ``win`` 일 순매수 순위(1 = 가장 많이 샀다). 동률은 섹터 이름순. 못 세면 ``None``."""
    if len(P.get("dates") or []) < win:
        return None
    vals = {}
    for s in P["sectors"]:
        if not _present(P, mks, s):
            continue
        ser = _series(P, mks, s, actor)
        if ser is None:
            return None
        vals[s] = round(sum(ser[-win:]), 2)
    order = sorted(vals, key=lambda s: (-vals[s], s))
    return {s: i + 1 for i, s in enumerate(order)}, order


def reports_root(path: "str | None") -> "str | None":
    """``<root>/<기준일>[.building.<pid>]/payload.json`` 꼴이면 ``<root>``. 아니면 ``None``.

    리포트 폴더 밖(임시 파일 등)에서 만든 페이로드가 엉뚱한 옆 폴더를 "직전 리포트" 로 읽지 않게
    폴더 이름이 날짜 꼴일 때만 인정한다.
    """
    if not path:
        return None
    d = os.path.dirname(os.path.abspath(path))
    return os.path.dirname(d) if _DATE_DIR.fullmatch(os.path.basename(d)) else None


def _read_report(root: "str | None", date: str) -> "tuple[dict | None, str | None]":
    if not root:
        return None, "리포트 폴더 밖에서 만들었다(--reports-root 없음)"
    folder = os.path.join(root, date)
    if not os.path.isdir(folder):
        return None, f"{date} 리포트 폴더가 없다"
    path = os.path.join(folder, "payload.json")
    if not os.path.isfile(path):
        why = "수집 미완(INCOMPLETE)" if os.path.isfile(os.path.join(folder, "INCOMPLETE.txt")) else "없음"
        return None, f"{date} 폴더에 payload.json 이 없다 — {why}"
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError) as e:
        return None, f"{date}/payload.json 을 못 읽었다: {type(e).__name__}"
    if (old.get("dates") or [None])[-1] != date:
        return None, f"{date}/payload.json 의 기준일이 {(old.get('dates') or [None])[-1]} 다"
    return old, None


def override_block(applied: dict) -> dict:
    """``payload["sector_override"]`` — 실제로 덮은 섹터와 그 지문·근거."""
    return {"applied": dict(sorted(applied.items())), "fp": fingerprint(applied), "source": OVERRIDE_SOURCE}


def override_fp(P: dict) -> str:
    """페이로드의 덮어쓰기 지문 — 그 키가 없는 옛 리포트는 덮어쓰기 전이므로 ``none``."""
    return (P.get("sector_override") or {}).get("fp") or "none"


def _override_mismatch(P: dict, old: dict) -> "str | None":
    """두 리포트의 섹터 라벨이 다르면 이유 — 같은 섹터 이름이 다른 종목 집합이라 견주면 섞인다(첫날 오염)."""
    a, b = override_fp(old), override_fp(P)
    return None if a == b else f"섹터 덮어쓰기가 달라졌다(지문 {a}→{b}) — 같은 섹터가 다른 종목 집합이라 견주지 않는다"


def change_block(P: dict, root: "str | None") -> "tuple[dict | None, str | None]":
    """``payload["change"]`` — **직전 거래일 리포트**(``<root>/<dates[-2]>/payload.json``) 대비.

    섹터의 ``CHANGE_WINDOW`` 일 순매수 순위(기관·투신+연기금)의 이동과 상위 ``CHANGE_TOP`` 의 들고 남.
    직전 순위는 그 리포트가 **게시한 그대로의** 페이로드로 센다 — 그 뒤 확정치로 바뀐 것까지 섞여
    있을 수 있어 ``prev_final`` 을 같이 싣는다. 직전 거래일은 이 페이로드의 바로 앞 날짜라 기준일보다
    뒤의 것은 읽을 수 없다. 못 읽으면 ``(None, 이유)``.
    """
    if len(P["dates"]) < 2:
        return None, "거래일이 하루뿐이다"
    prev_date = P["dates"][-2]
    old, why = _read_report(root, prev_date)
    if old is None:
        return None, why
    if (why := _override_mismatch(P, old)):
        return None, why
    out = {
        "prev_date": prev_date,
        "prev_final": bool(old.get("final")),
        "prev_verify_failed": os.path.isfile(os.path.join(root, prev_date, "VERIFY_FAILED.txt")),
        "window": CHANGE_WINDOW, "top_n": CHANGE_TOP,
        "rank": {}, "top": {},
    }
    for mkey, mks in _mkeys(P):
        for a in CHANGE_ACTORS:
            cur = _ranks(P, mks, a, CHANGE_WINDOW)
            if cur is None:
                continue
            prev = _ranks(old, mks, a, CHANGE_WINDOW) if set(mks) <= set(old.get("markets") or []) else None
            out["rank"].setdefault(mkey, {})[a] = {
                s: [prev[0].get(s) if prev else None, cur[0][s]] for s in cur[1]}
            top_c = cur[1][:CHANGE_TOP]
            top_p = prev[1][:CHANGE_TOP] if prev else None
            out["top"].setdefault(mkey, {})[a] = {
                "cur": top_c, "prev": top_p,
                "entered": None if top_p is None else [s for s in top_c if s not in top_p],
                "exited": None if top_p is None else [s for s in top_p if s not in top_c],
            }
    return out, None


def final_diff_block(P: dict, root: "str | None") -> "tuple[dict | None, str | None]":
    """``payload["final_diff"]`` — 같은 기준일의 **바꿔 끼워질** 리포트(보통 잠정) 대비 확정치의 차이.

    머리 숫자만 본다: (시장, 섹터, 주체) 의 1일·5일 순매수와 시장 합계. 반올림(``FINAL_DIFF_TOL``)을
    넘는 칸만 ``changed`` 에 싣는다. 시장 합계는 다 싣는다(몇 줄 안 된다).
    바꿔 끼울 리포트가 이미 확정이면(검증 실패 재시도·강제 재생성) 잠정은 없다 — :func:`_inherit_final_diff`.
    """
    asof = P["dates"][-1]
    old, why = _read_report(root, asof)
    if old is None:
        return None, why.replace(f"{asof} 리포트 폴더가 없다", "같은 기준일의 잠정 리포트가 없었다")
    if (why := _override_mismatch(P, old)):
        return None, why
    built = datetime.fromtimestamp(os.path.getmtime(os.path.join(root, asof, "payload.json")), KST).strftime("%Y-%m-%d %H:%M")
    if old.get("final"):
        return _inherit_final_diff(P, old, built, os.path.join(root, asof))
    fd = final_diff_compute(P, old)
    return {"base_final": fd.pop("base_final"), "base_built": built, **fd}, None


KST = timezone(timedelta(hours=9))
#: 확정 위에 다시 만들어 잠정 대비를 못 낼 때의 이유 머리말 — 재시도가 거듭돼도 이유가 겹겹이 붙지 않게 본다.
REFINAL_WHY = "확정 위에 다시 만들었다 — "


def _head_cells(Q: dict) -> dict:
    """(시장, 섹터, 주체, 창) → 순매수 — 잠정 대비가 보는 칸 전부(소수 2자리)."""
    out = {}
    for m in Q.get("markets") or []:
        for a in FINAL_DIFF_ACTORS:
            for s in Q["sectors"]:
                ser = _series(Q, [m], s, a) if _present(Q, [m], s) else None
                if ser is not None:
                    out.update({(m, s, a, w): round(sum(ser[-w:]), 2) for w in FINAL_DIFF_WINDOWS})
    return out


#: 첫 확정의 ``VERIFY_FAILED.txt`` 에서 찾는 검사 이름 — ``verify_report.py`` 의 ``chk`` 이름 그대로(테스트가 대조한다).
#: 경계 칸만 걸린 옛 코드의 실패에서 이 칸이 거부된다(문턱과 같은 칸이 ``changed`` 에 실렸다).
FD_CHECK_VALUES = "잠정 → 확정 — 확정값·차이가 페이로드에서 다시 나오는가"
#: 진짜 잠정 리포트와 다시 세어 통과한 증거 — 첫 확정에서 나왔거나, 그 기록을 이은 재시도에서 나왔거나.
FD_CHECKS_RECOUNT = ("잠정 → 확정 — 잠정값·차이·칸 수를 옆 잠정 리포트에서 다시 센 것과 같은가",
                     "잠정 → 확정 — %ile 극단 목록의 들고 남 재계산",
                     "잠정 → 확정 — 견준 리포트의 지위·생성 시각 = 옆 폴더의 것")
FD_CHECKS_RETRY = ("확정 재시도 — 잠정 대비가 첫 확정의 기록에서 나왔는가(확정끼리 견주지 않았는가)",
                   "확정 재시도 — 첫 확정 시각이 옆 폴더의 것인가")
_FD_FAMILY = ("잠정 → 확정", "확정 재시도", "final_diff.json", "확정 리포트에 잠정 대비")
_VERIFY_LINE = re.compile(r"^\s*\[(OK  |FAIL)\] T · (.+?)(?:   (.*))?$")
#: 옛 규칙(날값 %ile)의 들고 남을 지금 규칙(반올림 %ile)에 견줄 때 봐주는 폭 — 반올림 자릿수 한 칸.
_EXT_SLACK = 0.1


def _failed_record_why(folder: str, fd: dict) -> "str | None":
    """첫 확정 폴더의 ``VERIFY_FAILED.txt`` 가 그 잠정 대비 기록을 믿지 말라는가 — 이유, 믿어도 되면 ``None``.

    잠정 리포트는 첫 확정 때 바뀌어 없으니 그 기록을 진짜 잠정과 다시 셀 수 있었던 것은 그때의 검증뿐이다. 그래서 그
    파일이 증거다. 다음이 **모두** 참일 때만 믿는다(아니면 비교 없음 — 틀린 값이 이어지는 것이 하루 비는 것보다 나쁘다):

    * 잠정 재계산 검사들이 ``OK`` 로 적혀 있다 — 빈 파일·검증이 도중에 죽은 파일은 증거가 없다.
    * 잠정 대비 쪽 ``FAIL`` 이 없다. 단 하나의 예외: ``FD_CHECK_VALUES`` 가 적은 건수가 기록의 경계 칸(|차| ≤ 문턱)
      수와 정확히 같으면 그 실패는 2026-11-09 전 코드의 경계 칸뿐이다 — 값은 잠정 재계산을 통과했고, 잇는 쪽이 그 칸을 거른다.
      다른 칸이 하나라도 함께 걸렸으면 건수가 늘어 예외가 아니다.
    """
    path = os.path.join(folder, "VERIFY_FAILED.txt")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        marks = [m.groups() for m in map(_VERIFY_LINE.match, f) if m]
    marks = [(st.strip(), name, detail or "") for st, name, detail in marks if name.startswith(_FD_FAMILY)]
    oks = {name for st, name, _ in marks if st == "OK"}
    if not (set(FD_CHECKS_RECOUNT) <= oks or set(FD_CHECKS_RETRY) <= oks):
        return "잠정 재계산을 통과한 흔적이 없다"
    for _st, name, detail in (x for x in marks if x[0] == "FAIL"):
        n = re.match(r"(\d+)건", detail)
        edge = sum(1 for r in fd.get("changed") or [] if r.get("diff") is not None
                   and abs(r["diff"]) <= fd.get("tol", FINAL_DIFF_TOL))
        if name != FD_CHECK_VALUES or not n or not edge or int(n.group(1)) != edge:
            return name
    return None


def _near(a: "float | None", b: "float | None", room: float) -> bool:
    return (a is None) == (b is None) and (a is None or abs(a - b) <= room)


def _record_mismatch(Q: dict, fd: dict) -> "str | None":
    """잠정 대비 기록이 자기 안에서·그 기록을 낸 페이로드 ``Q`` 와 어긋나면 그 칸 이름 — 잠정 없이도 보이는 것만 본다.

    ``cells_1d`` 의 잠정 열이 1일 ``changed``·시장 합계와 맞는가, 확정 열이 ``Q`` 와 맞는가, 극단 들고 남이 ``Q`` 의 목록과
    모순되지 않는가, 종목 층의 행이 ``Q`` 와 맞는가. 5일 잠정 합은 바뀐 칸에만 남아 합계를 문턱 폭 안에서만 잴 수 있다.
    """
    cells, rows = fd.get("cells_1d"), fd.get("changed") or []
    if not isinstance(cells, list):
        return "칸별 기록 없음"
    tol, wins = fd.get("tol", FINAL_DIFF_TOL), fd.get("windows") or list(FINAL_DIFF_WINDOWS)
    if fd.get("n_changed") != len(rows) or fd.get("n_cells") != len(cells) * len(wins):
        return "칸 수"
    got = {(r["market"], r["sector"], r["actor"], r["win"]): r for r in rows}
    if len(got) != len(rows) or any(k[3] not in wins for k in got):
        return "changed 칸 집합"
    want = {(m, s, a): _series(Q, [m], s, a) for m in Q.get("markets") or [] for a in FINAL_DIFF_ACTORS
            for s in Q["sectors"] if _present(Q, [m], s)}
    want = {k: v for k, v in want.items() if v is not None}
    if {tuple(c[:3]) for c in cells} != set(want) or len(cells) != len(want):
        return "cells_1d 칸 집합"
    prov = {}
    for m, s, a, vo, vn in cells:
        prov[(m, s, a)] = vo
        if abs(want[(m, s, a)][-1] - vn) > 0.006:
            return f"cells_1d 확정값 {m}/{s}/{a}"
        r = got.get((m, s, a, 1))
        if (vo is None or abs(round(vn - vo, 2)) > tol) != (r is not None):
            return f"1일 바뀐 칸 {m}/{s}/{a}"
        if r is not None and not _near(r["old"], vo, 0.006):
            return f"cells_1d 잠정값 {m}/{s}/{a}"
    for (m, s, a, w), r in got.items():
        ser = want.get((m, s, a))
        if ser is None or abs(round(sum(ser[-w:]), 2) - r["new"]) > 0.011:
            return f"changed 확정값 {m}/{s}/{a}/{w}"
        if r["old"] is None:
            if r["diff"] is not None:
                return f"changed 차이 {m}/{s}/{a}/{w}"
        elif r["diff"] is None or abs(r["new"] - r["old"] - r["diff"]) > 0.011 or abs(r["diff"]) <= tol:
            return f"changed 차이 {m}/{s}/{a}/{w}"
    totals = {(t["market"], t["actor"], t["win"]): t for t in fd.get("market_totals") or []}
    if set(totals) != {(m, a, w) for m in Q.get("markets") or [] for a in FINAL_DIFF_ACTORS for w in wins}:
        return "market_totals 칸 집합"
    for (m, a, w), t in totals.items():
        keys = [k for k in want if k[0] == m and k[2] == a]
        new, old = 0.0, 0.0
        for k in keys:
            vn = round(sum(want[k][-w:]), 2)
            new += vn
            r = got.get((*k, w))
            vo = prov[k] if w == 1 else (r["old"] if r is not None else vn)
            old = None if old is None or vo is None else old + vo
        room = len(keys) * (0.011 if w == 1 else tol + 0.011) + 0.011
        if not _near(t["new"], new, room) or not _near(t["old"], old, room):
            return f"market_totals {m}/{a}/{w}"
        if t["old"] is not None and abs(t["new"] - t["old"] - t["diff"]) > 0.011:
            return f"market_totals 차이 {m}/{a}/{w}"
    for mkey, mks in _mkeys(Q):
        for a, ch in ((fd.get("extremes_change") or {}).get(mkey) or {}).items():
            sers = {s: _series(Q, mks, s, a) for s in Q["sectors"] if _present(Q, mks, s)}
            pct = {s: None if ser is None else _fact(ser)["pct"] for s, ser in sers.items()}
            # 들어온 섹터는 지금 그 목록에 있어야 하고 나간 섹터는 없어야 한다. 값이 없으면(None) 나간 쪽만 말이 된다.
            if (any(pct.get(s) is None or pct[s] < PCT_HI - _EXT_SLACK for s in ch.get("hi_in") or [])
                    or any(pct.get(s) is None or pct[s] > PCT_LO + _EXT_SLACK for s in ch.get("lo_in") or [])
                    or any(pct.get(s) is not None and pct[s] >= PCT_HI + _EXT_SLACK for s in ch.get("hi_out") or [])
                    or any(pct.get(s) is not None and pct[s] <= PCT_LO - _EXT_SLACK for s in ch.get("lo_out") or [])):
                return f"extremes_change {mkey}/{a}"
    return _stock_record_mismatch(Q, fd["stocks"]) if isinstance(fd.get("stocks"), dict) else None


def _inherit_final_diff(P: dict, old: dict, built: str, folder: str) -> "tuple[dict | None, str | None]":
    """확정(``old``, 폴더 ``folder``) 위에 다시 만드는 확정 ``P`` 의 잠정 대비 — 첫 확정의 기록에서 잇는다.

    잠정 리포트는 첫 확정 때 바꿔 끼워져 없다. 확정끼리 견주면 "0칸 변경" 이 되고 실제로 바뀐 칸의 기록과
    수정 폭(``revision_stats``)의 그날 칸이 사라진다. 그래서:

    * 그 기록이 검증에 걸렸거나(:func:`_failed_record_why`) 자기 안에서 어긋나면(:func:`_record_mismatch`) 잇지 않고 비교 없음.
    * 값이 첫 확정과 같으면 그대로 잇는다(``base_built`` 는 그날 저녁 잠정의 시각, ``inherited_from`` 은 첫 확정의 시각).
    * 값이 또 바뀌었으면 잠정 열만 이어 1일 칸을 다시 센다(:func:`_rebase_final_diff`). 기록에 종목 층이 있는데 종목 칸만
      움직인 경우도 그렇다(:func:`_stock_layer_moved`) — 섹터 칸이 같아도 그 층의 확정 열이 낡았다.

    0칸이라고 쓰지 않는다. ``built`` 는 바꿔 끼울 리포트의 생성 시각.
    """
    fd, first_why = old.get("final_diff"), old.get("final_diff_reason")
    if not isinstance(fd, dict) or fd.get("base_final"):
        if first_why and first_why.startswith(REFINAL_WHY):
            return None, first_why
        return None, (REFINAL_WHY + "잠정 리포트는 첫 확정 때 바뀌어 없고 이어받을 잠정 대비 기록도 없다"
                      + (f"(첫 확정: {first_why})" if first_why else ""))
    if (why := _failed_record_why(folder, fd)):
        return None, f"{REFINAL_WHY}첫 확정의 잠정 대비가 검증에 걸려 잇지 않는다({why})"
    # 고치기 전 코드가 남긴 기록의 경계 칸(|차| = 문턱)은 지금 규칙으로 걸러 잇는다 — 안 그러면 재시도가 영영 검증에 걸린다.
    changed = [r for r in fd.get("changed") or [] if r["diff"] is None or abs(r["diff"]) > FINAL_DIFF_TOL]
    fd = {**fd, "changed": changed, "n_changed": len(changed)}
    if (why := _record_mismatch(old, fd)):
        return None, f"{REFINAL_WHY}첫 확정의 잠정 대비 기록이 어긋나 잇지 않는다({why})"
    first = fd.get("inherited_from") or built
    if _head_cells(P) == _head_cells(old) and not extremes_change(P, old):
        if not _stock_layer_moved(P, old, fd):
            return {**fd, "inherited_from": first}, None
    return _rebase_final_diff(P, old, fd, first), None


def _stock_cells(Q: dict) -> tuple:
    """:func:`stock_diff` 가 페이로드에서 읽는 것 — 종목별 (시장, 섹터, 네 주체 1일 칸)과 (시장, 섹터) 구성."""
    return ({c: (m, s, [w.get(a) for a in STOCK_DIFF_ACTORS]) for c, (m, s, _n, w) in _win1(Q).items()},
            _stock_membership(Q))


def _stock_layer_moved(P: dict, old: dict, fd: dict) -> bool:
    """이어받을 기록의 종목 층이 낡았는가 — 기록에 그 층이 없으면 낡을 것도 없다(옛 코드의 첫 확정)."""
    return isinstance(fd.get("stocks"), dict) and _stock_cells(P) != _stock_cells(old)


def _stock_record_mismatch(Q: dict, S: dict) -> "str | None":
    """종목 층이 그 기록을 낸 페이로드 ``Q`` 와 어긋나면 그 칸 이름 — 행 수와 확정 열만 볼 수 있다(잠정 칸은 ``Q`` 에 없다)."""
    rows, now = S.get("rows") or [], _win1(Q)
    if len(rows) != min(S.get("n_rows") or 0, S.get("cap", STOCK_DIFF_CAP)):
        return "stocks 행 수"
    for m, s, c, _name, vals in rows:
        r = now.get(c)
        w = r[3] if r is not None and r[:2] == (m, s) else {}
        for a, (_vo, vn) in vals.items():
            if not _near(vn, w.get(a), 0.051):
                return f"stocks 확정값 {m}/{s}/{c}/{a}"
    return None


def _rebase_final_diff(P: dict, old: dict, fd: dict, first: str) -> dict:
    """첫 확정 뒤 값이 또 바뀐 재시도 — 기록의 잠정 열을 이어 1일 칸을 새 값과 다시 센다(``windows`` = [1], ``rebased``).

    폴더를 바꿔 끼우면 잠정 열은 이 기록에만 남는다. 버리면 그날이 수정 폭에서 빠진다. 5일 칸은 낼 수 없다 — 잠정
    5일 합은 바뀐 칸에만 남았다. 극단 들고 남은 ``old`` 의 목록을 그 기록의 들고 남으로 되돌려 잠정 목록을 얻는다.
    종목 층(``stocks``)은 ``None`` — 종목의 잠정 칸은 문턱·상한을 넘은 행에만 남아 다시 셀 수 없고, 첫 확정의 것을 들고
    가면 확정 열이 이 리포트와 어긋난다. 그날은 수정 폭의 종목 층에서 빠지고 섹터 층에는 남는다.
    """
    prov = {(m, s, a): vo for m, s, a, vo, _ in fd["cells_1d"]}
    wins = [1]

    def prov_of(m, s, a):
        vo = prov.get((m, s, a))
        return None if vo is None else [vo]

    changed, totals, n_cells, cells_1d = _diff_cells(P, prov_of, wins)
    ext: dict = {}
    for mkey, mks in _mkeys(P):
        for a in EXTREME_ACTORS:
            new, was = _extremes(P, mks, a), _extremes(old, mks, a)
            if new is None or was is None:
                continue
            ch0 = ((fd.get("extremes_change") or {}).get(mkey) or {}).get(a) or {}
            hi = (was[0] - set(ch0.get("hi_in") or [])) | set(ch0.get("hi_out") or [])
            lo = (was[1] - set(ch0.get("lo_in") or [])) | set(ch0.get("lo_out") or [])
            ch = {"hi_in": sorted(new[0] - hi), "hi_out": sorted(hi - new[0]),
                  "lo_in": sorted(new[1] - lo), "lo_out": sorted(lo - new[1])}
            if any(ch.values()):
                ext.setdefault(mkey, {})[a] = ch
    return {"base_final": False, "base_built": fd.get("base_built"),
            "windows": wins, "tol": FINAL_DIFF_TOL,
            "n_cells": n_cells, "n_changed": len(changed),
            "changed": changed, "market_totals": totals,
            "cells_1d": cells_1d,
            "stocks": None,
            "pct_hi": PCT_HI, "pct_lo": PCT_LO, "extremes_change": ext,
            "inherited_from": first, "rebased": True}


def _diff_cells(P: dict, prov_of, windows: list[int]) -> tuple[list, list, int, list]:
    """(바뀐 칸, 시장 합계, 칸 수, 1일 칸) — ``prov_of(시장, 섹터, 주체)`` 가 잠정 일별 값(없으면 ``None``)을 준다."""
    changed, totals, n_cells, cells_1d = [], [], 0, []

    def win_sum(ser, w):
        return None if ser is None or len(ser) < w else round(sum(ser[-w:]), 2)

    for m in P["markets"]:
        for a in FINAL_DIFF_ACTORS:
            tn = [0.0] * len(windows)
            to: list = [0.0] * len(windows)
            for s in P["sectors"]:
                if not _present(P, [m], s):
                    continue
                sn = _series(P, [m], s, a)
                so = prov_of(m, s, a)
                if sn is None:
                    continue
                # 1일 칸은 (잠정, 확정) 그대로 남긴다 — 다음 리포트들이 잠정→확정 수정 폭을 모은다(revision_stats).
                cells_1d.append([m, s, a, None if so is None else so[-1], sn[-1]])
                for j, w in enumerate(windows):
                    vn, vo = win_sum(sn, w), win_sum(so, w)
                    diff = None if vo is None else round(vn - vo, 2)
                    n_cells += 1
                    tn[j] += vn
                    to[j] = None if to[j] is None or vo is None else to[j] + vo
                    if diff is None or abs(diff) > FINAL_DIFF_TOL:
                        changed.append({"market": m, "sector": s, "actor": a, "win": w, "old": vo,
                                        "new": vn, "diff": diff})
            for j, w in enumerate(windows):
                vo = None if to[j] is None else round(to[j], 2)
                totals.append({"market": m, "actor": a, "win": w, "old": vo, "new": round(tn[j], 2),
                               "diff": None if vo is None else round(tn[j] - vo, 2)})
    changed.sort(key=lambda r: -(abs(r["diff"]) if r["diff"] is not None else float("inf")))
    return changed, totals, n_cells, cells_1d


def final_diff_compute(P: dict, old: dict) -> dict:
    """두 페이로드(확정 ``P``, 잠정 ``old``)의 차이 — :func:`final_diff_block` 의 몸통(``base_built`` 만 뺀 것).

    아카이브 시드(``scripts/seed_revisions.py``)가 같은 정의로 1일 칸을 만들려고 따로 부른다.
    """
    def prov_of(m, s, a):
        return _series(old, [m], s, a) if m in (old.get("markets") or []) else None

    changed, totals, n_cells, cells_1d = _diff_cells(P, prov_of, list(FINAL_DIFF_WINDOWS))
    ext_change = extremes_change(P, old)
    return {
        "base_final": bool(old.get("final")),
        "windows": list(FINAL_DIFF_WINDOWS), "tol": FINAL_DIFF_TOL,
        "n_cells": n_cells, "n_changed": len(changed),
        "changed": changed, "market_totals": totals,
        "cells_1d": cells_1d,
        "stocks": stock_diff(P, old),
        "pct_hi": PCT_HI, "pct_lo": PCT_LO, "extremes_change": ext_change,
    }


#: 오늘 1년%ile 의 극단 문턱 — 화면(``flow_view.PCT_HI/LO``)과 웹(``PCT_HI/LO``)이 같은 값을 쓴다.
PCT_HI, PCT_LO = 95.0, 5.0
#: 극단 목록의 들고 남을 적는 주체 — 첫 화면이 극단을 적는 주체(기관·투신+연기금)와 그날 최대 순매매 주체이 될 수 있는 셋.
EXTREME_ACTORS = ("inst", "invpen", "forgn", "indiv", "etc")


def _extremes(P: dict, mks: list[str], actor: str) -> "tuple[set, set] | None":
    """그 페이로드에서 (≥95 섹터, ≤5 섹터) — 화면과 같은 반올림 ``pct``(:func:`_fact`)로 센다. 그 시장이 없으면 ``None``.

    날값으로 재면 94.98 이 화면의 ≥95 목록에는 있는데(95.0) 들고 남에서는 빠진다.
    """
    if not set(mks) <= set(P.get("markets") or []):
        return None
    hi, lo = set(), set()
    for s in P["sectors"]:
        if not _present(P, mks, s):
            continue
        ser = _series(P, mks, s, actor)
        p = None if ser is None else _fact(ser)["pct"]
        if p is None:
            continue
        if p >= PCT_HI:
            hi.add(s)
        elif p <= PCT_LO:
            lo.add(s)
    return hi, lo


def extremes_change(P: dict, old: dict) -> dict:
    """잠정 → 확정에서 오늘 1년%ile 극단(≥95·≤5) 목록에 들고 난 섹터 — 바뀐 (시장, 주체)만 싣는다."""
    out: dict = {}
    for mkey, mks in _mkeys(P):
        for a in EXTREME_ACTORS:
            new, prev = _extremes(P, mks, a), _extremes(old, mks, a)
            if new is None or prev is None:
                continue
            ch = {"hi_in": sorted(new[0] - prev[0]), "hi_out": sorted(prev[0] - new[0]),
                  "lo_in": sorted(new[1] - prev[1]), "lo_out": sorted(prev[1] - new[1])}
            if any(ch.values()):
                out.setdefault(mkey, {})[a] = ch
    return out


# ─────────────────────────────────────────────── 잠정 → 확정 — 종목(기관상위) 층
#
# 섹터 칸만으로는 "무엇을 샀나" 의 수정이 안 보인다 — 드릴다운의 행동 단위는 종목이다. 전 종목
# (~2,600)을 다 실으면 하루 수백 KB 가 붙고, 화면 규칙상 "그 섹터를 움직였다" 고 부르지 않는 행
# (|순매수| ≈ 0, 실측 51%)의 수정 폭은 볼 이유가 없다. 그래서 **기관상위**(아래 STOCK_DIFF_CUT)만 본다.

#: 종목 수정 기록의 주체 — 화면 신뢰 순서(기관 안에서 투신·연기금 우선)와 외국인. 종목 1일 칸(``names[].win["1"]``)에
#: 있는 값 그대로다.
STOCK_DIFF_ACTORS = ("inst", "invtrt", "penfnd_etc", "forgn")
#: 기관상위 경계 — (시장, 섹터)에서 1일 |기관 순매수| 큰 순으로 훑은 누적 기여율이 이 %를 **처음 넘긴 행까지**.
#: 화면 종목 목록의 누적[%] 가로줄(``flow_view.CUM_CUT``)과 같은 정의·같은 값이다(실측 평균 6.2종목이 80% 를 설명).
STOCK_DIFF_CUT = 80.0
#: 종목 행을 ``rows`` 에 싣는 문턱(억) — 네 주체 중 하나라도 |확정 − 잠정| 이 이걸 넘는 행만. 종목 1일 집계는
#: 0.1억 단위 반올림이라 0.5억 아래의 차이는 반올림 잡음과 구분되지 않는다. 요약 통계는 문턱과 무관하게 센다.
STOCK_DIFF_TOL = 0.5
#: ``rows`` 상한 — max|Δ| 큰 순(한쪽 값이 없는 행이 맨 앞 — '값이 없는 것'은 작은 차이가 아니다). 크기를 묶는다.
STOCK_DIFF_CAP = 200
#: 상위 3 유지율의 최소 목록 크기 — (시장, 섹터, 주체)에 1일 칸이 이보다 적으면 유지율이 늘 100% 에 붙는다
#: (섹터 쪽 ``REV_TOP_MIN_SECTORS`` 와 같은 이유).
STOCK_TOP_MIN = 6


def _win1(P: dict) -> dict:
    """code → (시장, 섹터, 이름, 1일 칸) — ``names`` 에서 1일 칸이 있는 종목만(그날 거래가 없으면 칸이 없다)."""
    out: dict = {}
    for code, r in (P.get("names") or {}).items():
        w = (r.get("win") or {}).get("1")
        if w and r.get("market") and r.get("sector"):
            out[code] = (r["market"], r["sector"], r.get("name"), w)
    return out


def _stock_drivers(w1: dict) -> set:
    """기관상위 코드 집합 — (시장, 섹터)마다 1일 |기관| 큰 순 누적 기여율이 :data:`STOCK_DIFF_CUT` 를 처음 넘긴 행까지."""
    groups: dict = {}
    for code, (m, s, _n, w) in w1.items():
        groups.setdefault((m, s), []).append((abs(w.get("inst") or 0.0), code))
    out: set = set()
    for rows in groups.values():
        tot = sum(v for v, _c in rows)
        if not tot:
            continue
        rows.sort(key=lambda t: (-t[0], t[1]))
        cum = 0.0
        for v, code in rows:
            out.add(code)
            cum += v
            if cum >= STOCK_DIFF_CUT / 100.0 * tot:
                break
    return out


def _stock_top3(new1: dict, old1: dict, dropped: set, n: "int | None" = None) -> tuple[int, int]:
    """(유지된 칸, 목록 수) — (시장, 섹터, 주체)마다 잠정 상위 ``n``(기본 :data:`REV_TOP_N`) 종목 중
    확정 상위 ``n`` 에도 있는 수.

    순위는 1일 순매수 큰 순, 동률은 코드순. 양쪽에 1일 칸이 :data:`STOCK_TOP_MIN` 종목 미만인 목록과
    종목 구성이 갈린(``dropped``) (시장, 섹터)는 세지 않는다.
    """
    n = REV_TOP_N if n is None else n
    by_new: dict = {}
    by_old: dict = {}
    for d, by in ((new1, by_new), (old1, by_old)):
        for code, (m, s, _n, w) in d.items():
            by.setdefault((m, s), []).append((code, w))
    kept = lists = 0
    for ms in sorted(by_new):
        rows_o = by_old.get(ms)
        if ms in dropped or rows_o is None or len(by_new[ms]) < STOCK_TOP_MIN or len(rows_o) < STOCK_TOP_MIN:
            continue
        for a in STOCK_DIFF_ACTORS:
            top_n = {c for c, _w in sorted(by_new[ms], key=lambda t: (-(t[1].get(a) or 0.0), t[0]))[:n]}
            top_o = {c for c, _w in sorted(rows_o, key=lambda t: (-(t[1].get(a) or 0.0), t[0]))[:n]}
            kept += len(top_n & top_o)
            lists += 1
    return kept, lists


def _stock_membership(P: dict) -> dict:
    """(시장, 섹터) → ``names`` 의 종목 코드 집합 — 1일 칸 유무와 무관하게 전부."""
    out: dict = {}
    for code, r in (P.get("names") or {}).items():
        out.setdefault((r.get("market"), r.get("sector")), set()).add(code)
    return out


def stock_diff(P: dict, old: dict) -> "dict | None":
    """``final_diff["stocks"]`` — 잠정(``old``) → 확정(``P``)에서 **기관상위 종목**의 1일 수정.

    * 어느 쪽이든 1일 종목 칸이 하나도 없으면 ``None`` — 옛 형식(1일 창이 생기기 전) 게시본과의 비교는
      종목 층을 말할 수 없다(모든 기관상위를 "수정"이라 적는 것은 거짓이다). 시드가 이 경우를 실제로 밟았다.
    * 모집단: 잠정·확정 각각의 기관상위(:func:`_stock_drivers`) 합집합. 한쪽에만 1일 칸이 있으면 없는 쪽은
      ``None`` — 수집 시각 뒤에 처음 잡힌(또는 사라진) 종목도 수정 사건이다.
    * 종목 구성이 갈린 (시장, 섹터)(``names`` 의 코드 집합이 다르다 — 재구성 시드처럼 그 사이 편입·분류
      변경이 섞인 경우)는 통째로 뺀다. 수정 폭이 아니다. 뺀 수는 ``dropped_sectors``.
    * ``rows``: 네 주체 중 max|Δ| 가 :data:`STOCK_DIFF_TOL` 을 넘는 행만, max|Δ| 큰 순(한쪽 없음이 맨 앞)
      :data:`STOCK_DIFF_CAP` 개까지. 꼴은 ``[시장, 섹터, 코드, 이름, {주체: [잠정, 확정]}]``.
    * 요약(상한·문턱과 무관): 분모 게이트(|확정| ≥ :data:`REV_GATE_SHARE` × 그 (시장, 섹터, 주체) 기관상위
      Σ|확정|)를 넘는 칸의 |확정 − 잠정| ÷ |확정| 중앙값(``rel_median_pct``)·부호 뒤집힘(``flips``), 그리고
      (시장, 섹터, 주체) 상위 3 종목 유지(:func:`_stock_top3`).
    """
    new1, old1 = _win1(P), _win1(old)
    if not new1 or not old1:
        return None
    mn, mo = _stock_membership(P), _stock_membership(old)
    dropped = {ms for ms in set(mn) | set(mo) if mn.get(ms) != mo.get(ms)}
    drivers = {c for c in _stock_drivers(new1) | _stock_drivers(old1)
               if (new1.get(c) or old1[c])[:2] not in dropped}
    tot: dict = {}
    for c in drivers:
        if c not in new1:
            continue
        m, s, _n, w = new1[c]
        for a in STOCK_DIFF_ACTORS:
            tot[(m, s, a)] = tot.get((m, s, a), 0.0) + abs(w.get(a) or 0.0)
    rel: list = []
    flips = 0
    rows: list = []
    for c in sorted(drivers):
        m, s, name = (new1.get(c) or old1[c])[:3]
        wn = new1[c][3] if c in new1 else None
        wo = old1[c][3] if c in old1 else None
        vals, dmax = {}, None
        for a in STOCK_DIFF_ACTORS:
            vn = None if wn is None else wn.get(a)
            vo = None if wo is None else wo.get(a)
            vals[a] = [vo, vn]
            if vo is None or vn is None:
                dmax = float("inf")
                continue
            dmax = max(dmax or 0.0, abs(vn - vo))
            if vn and abs(vn) >= REV_GATE_SHARE * tot[(m, s, a)]:
                rel.append(abs(vn - vo) / abs(vn) * 100.0)
                flips += vo * vn < 0
        if dmax is not None and dmax > STOCK_DIFF_TOL:
            rows.append((dmax, [m, s, c, name, vals]))
    rows.sort(key=lambda t: (-t[0], t[1][2]))
    kept, lists = _stock_top3(new1, old1, dropped)
    return {
        "cut": STOCK_DIFF_CUT, "tol": STOCK_DIFF_TOL, "cap": STOCK_DIFF_CAP,
        "actors": list(STOCK_DIFF_ACTORS), "gate_share": REV_GATE_SHARE,
        "top_n": REV_TOP_N, "top_min": STOCK_TOP_MIN,
        "n_stocks": len(drivers), "n_cells": len(rel),
        "rel_median_pct": round(float(pd.Series(rel).median()), 1) if rel else None,
        "flips": flips, "top3_kept": kept, "top3_lists": lists,
        "n_rows": len(rows), "dropped_sectors": len(dropped),
        "rows": [r for _d, r in rows[:STOCK_DIFF_CAP]],
    }


# ─────────────────────────────────────────────── 잠정 → 확정 수정 폭 — 지난 확정 리포트에서 모은다
#
# 18:10 리포트는 수집 시각(~16:00~18:10)의 스냅숏이다. 애프터마켓은 20:00 까지 돌고 확정치는 다음날 새벽
# daily_finalize 가 덮는다(quant-airflow CLAUDE.md §3). 실측: 11-05 거래소 기관 +8,855 → +9,658억, 금속 기관
# 327 → 677, 10-29 금융 기관 1,786 → 5,477. 잠정 리포트를 보는 사람에게 "이 숫자가 보통 얼마나 바뀌나" 를
# 지난 확정 리포트들의 ``final_diff.cells_1d``(잠정, 확정) 에서 센다 — 기준일 이하의 날만 읽는다.

#: 모을 확정 기준일 수(최근 것부터).
REV_DAYS = 20
#: 분모 게이트 — (시장, 주체) 안에서 |확정값| 이 Σ|확정값| 의 이 몫 이상인 칸만 상대 변화를 잰다.
#: 0 근처 칸의 상대 변화는 무한대로 튄다. 문턱은 목록 안 몫으로 잡는다(CLAUDE.md 화면·리포트 규칙).
REV_GATE_SHARE = 0.02


def _final_cells(root: str, date: str) -> "tuple[dict | None, str | None]":
    """그 기준일 확정 리포트의 잠정 대비(``final_diff`` 꼴) — ``final_diff.json``(작다) 먼저, 없으면 ``payload.json``."""
    folder = os.path.join(root, date)
    if not os.path.isfile(os.path.join(folder, "FINAL.txt")):
        return None, "확정 아님"
    if os.path.isfile(os.path.join(folder, "VERIFY_FAILED.txt")):
        return None, "검증 실패 리포트"
    fd = None
    for name in ("final_diff.json", "payload.json"):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        fd = doc if name == "final_diff.json" else doc.get("final_diff")
        break
    if not isinstance(fd, dict):
        return None, "잠정 대비 기록 없음"
    return _usable_base(fd, date)


def _usable_base(fd: dict, date: str) -> "tuple[dict | None, str | None]":
    """그 확정 리포트의 잠정 대비가 **그날 저녁의 잠정 리포트** 대비인가 — 맞으면 (그 기록, None), 아니면 (None, 이유).

    바꿔 끼운 리포트가 기준일 당일에 만든 것이어야 한다(``base_built`` 의 날짜 = 기준일). 다음날 확정 전에
    손으로 다시 만든 것이나 확정 위에 다시 만든 것은 "18:10 잠정이 얼마나 바뀌나" 를 말하지 않는다.
    첫 확정의 기록에서 이은 재시도(:func:`_inherit_final_diff`)는 그날 저녁 잠정 대비 그대로라 쓸 수 있다.
    ``base_final`` 참은 2026-11-09 전 코드가 확정끼리 견준 기록뿐이다.
    """
    if fd.get("base_final"):
        return None, "확정 위에 다시 만든 날(잠정 대비 아님)"
    if fd.get("cells_1d") is None:
        return None, "칸별 기록 없음(옛 형식)"
    if str(fd.get("base_built") or "")[:10] != date:
        return None, f"바꿔 끼운 리포트가 기준일 당일 것이 아니다({fd.get('base_built')})"
    return fd, None


def revision_rows(cells: list, gate_share: float = REV_GATE_SHARE) -> tuple[list[float], int]:
    """(상대 변화 목록[%], 부호 뒤집힘 수) — 분모 게이트를 넘는 1일 칸만."""
    tot: dict = {}
    for m, _s, a, _old, new in cells:
        tot[(m, a)] = tot.get((m, a), 0.0) + abs(new or 0.0)
    rel, flips = [], 0
    for m, _s, a, old, new in cells:
        if old is None or not new or abs(new) < gate_share * tot[(m, a)]:
            continue
        rel.append(abs(new - old) / abs(new) * 100.0)
        flips += old * new < 0
    return rel, flips


#: 상위 3 유지율을 재는 주체 — 첫 화면이 상위 3 을 적는 주체(기관·투신+연기금·그날 최대 순매매 주체 후보).
REV_TOP_ACTORS = ("inst", "invpen", "forgn", "indiv", "etc")
REV_TOP_N = 3
#: (시장, 주체) 목록에 잠정·확정 값이 다 있는 섹터가 이보다 적으면 그 목록은 상위 3 유지율에 넣지 않는다 —
#: 섹터가 셋뿐이면 유지율이 늘 100% 다.
REV_TOP_MIN_SECTORS = 6
#: 아카이브 시드 — 확정 모드가 생기기 전의 18:10 게시본을 확정 재구성 리포트에 견준 1일 칸(``scripts/seed_revisions.py``).
#: ``<root>/.revision_seed/<기준일>.json`` 에 ``final_diff.json`` 과 같은 꼴로 둔다. 그 기준일 폴더가 쓸 만한
#: 잠정 대비를 주지 못할 때만 읽는다(진짜 확정 기록이 늘 먼저다). 화면이 출처를 적는다.
REV_SEED_DIR = ".revision_seed"
REV_SEED_LABEL = "시드: 아카이브 18:10 게시본 대 확정 재구성"


def _seed_cells(root: str, date: str) -> "tuple[dict | None, str | None]":
    path = os.path.join(root, REV_SEED_DIR, f"{date}.json")
    if not os.path.isfile(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as f:
            fd = json.load(f)
    except (OSError, ValueError):
        return None, "시드 파일을 못 읽었다"
    if not isinstance(fd, dict) or (fd.get("seed") or {}).get("label") != REV_SEED_LABEL:
        return None, "시드 표지가 없다"
    return _usable_base(fd, date)


def _top_cell(d: "dict | None", parts: tuple) -> "tuple[float, float] | bool | None":
    """한 (시장, 섹터)의 (잠정, 확정) — 칸이 없으면 None, 있는데 값이 빠졌으면 False."""
    if d is None:
        return None
    got = [d.get(p) for p in parts]
    if any(g is None or g[0] is None or g[1] is None for g in got):
        return False
    return sum(g[0] for g in got), sum(g[1] for g in got)


def revision_top3(cells: list, n: int = REV_TOP_N) -> tuple[int, int]:
    """(유지된 칸, 전체 칸) — (시장, 주체)마다 잠정 상위 ``n`` 섹터 중 확정 상위 ``n`` 에도 있는 수.

    순위는 첫 화면과 같다: 1일 순매수 큰 순, 동률은 이름순. 시장은 개별 시장과 ``전체``(섹터별 시장 합 — 모든
    시장 칸에 잠정값이 있는 섹터만), 주체는 :data:`REV_TOP_ACTORS`(``invpen`` = 투신 + 연기금 칸의 합).
    """
    by: dict = {}
    for m, s, a, old, new in cells:
        by.setdefault((m, s), {})[a] = (old, new)
    mkts = sorted({m for m, _s in by})
    secs = sorted({s for _m, s in by})
    kept = total = 0
    for a in REV_TOP_ACTORS:
        parts = ("invtrt", "penfnd_etc") if a == "invpen" else (a,)
        for mk in mkts + ["전체"]:
            vals = []
            for s in secs:
                cs = [c for c in (_top_cell(by.get((m, s)), parts) for m in (mkts if mk == "전체" else [mk]))
                      if c is not None]
                if not cs or any(c is False for c in cs):
                    continue
                vals.append((s, round(sum(c[0] for c in cs), 2), round(sum(c[1] for c in cs), 2)))
            if len(vals) < REV_TOP_MIN_SECTORS:
                continue
            top_o = {t[0] for t in sorted(vals, key=lambda t: (-t[1], t[0]))[:n]}
            top_n = {t[0] for t in sorted(vals, key=lambda t: (-t[2], t[0]))[:n]}
            kept += len(top_o & top_n)
            total += n
    return kept, total


def revision_stats(P: dict, root: "str | None") -> dict:
    """``payload["revision_stats"]`` — 최근 :data:`REV_DAYS` 개 확정 기준일의 잠정→확정 수정 폭.

    섹터 1일 순매수 칸(시장 × 섹터 × 기관·투신·연기금·외국인·개인·기타법인)의 |확정 − 잠정| ÷ |확정| 중앙값과
    부호 뒤집힘 수, 그리고 (시장, 주체)별 상위 3 섹터가 잠정→확정에서 그대로인 몫(``top3_keep_pct``).
    이 리포트가 확정이면 자기 ``final_diff`` 도 넣는다. 기준일보다 뒤의 폴더는 읽지 않는다. 확정 기록이 없는
    기준일은 아카이브 시드(:data:`REV_SEED_DIR`)를 읽고 ``seed`` 에 그 날짜와 출처를 적는다.

    종목(기관상위) 층(``stocks``): 그 기록이 ``final_diff["stocks"]`` 요약을 남긴 날들에서 모은다 — 칸 수·부호
    뒤집힘·상위 3 유지는 날들의 합이고, ``rel_median_pct`` 는 **일별 중앙값의 중앙값**이다(종목 칸 원자료는
    날마다 요약으로만 남는다 — ``rows`` 는 문턱·상한이 걸린 발췌라 모으면 치우친다). 요약이 없는 옛 기록의
    날은 ``n_dates`` 에 안 센다. 기록이 하나도 없으면 ``None``.
    """
    asof = P["dates"][-1]
    out = {"days": REV_DAYS, "gate_share": REV_GATE_SHARE, "win": 1, "n_dates": 0, "dates": [],
           "n_cells": 0, "rel_median_pct": None, "flips": 0, "top_n": REV_TOP_N,
           "top3_keep_pct": None, "top3_lists": 0, "seed": None, "skipped": {}, "stocks": None}
    found: list = []
    seeded: list = []
    own = P.get("final_diff") if P.get("final") else None
    if isinstance(own, dict):
        fd, why = _usable_base(own, asof)
        if fd is not None:
            found.append((asof, fd))
        else:
            out["skipped"][asof] = why
    if root and os.path.isdir(root):
        names = set(os.listdir(root))
        seed_dir = os.path.join(root, REV_SEED_DIR)
        if os.path.isdir(seed_dir):
            names |= {f[:-5] for f in os.listdir(seed_dir) if f.endswith(".json")}
        # 최근 것부터 훑고 REV_DAYS 개를 모으면 멈춘다 — 1년치 폴더를 다 열지 않고, 건너뛴 이유도 그 안의 것만 적는다.
        for name in sorted(names, reverse=True):
            if len(found) >= REV_DAYS:
                break
            if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", name) and name < asof):
                continue
            fd, why = _final_cells(root, name) if os.path.isdir(os.path.join(root, name)) else (None, "확정 아님")
            if fd is None:
                sfd, swhy = _seed_cells(root, name)
                if sfd is not None:
                    found.append((name, sfd))
                    seeded.append(name)
                    continue
                why = swhy or why
                if why != "확정 아님":
                    out["skipped"][name] = why
                continue
            found.append((name, fd))
    found.sort()
    rel, flips, kept, lists = [], 0, 0, 0
    sdays, smeds, scells, sflips, skept, slists = 0, [], 0, 0, 0, 0
    for _d, fd in found:
        cells = fd["cells_1d"]
        r, f = revision_rows(cells)
        rel += r
        flips += f
        k, t = revision_top3(cells)
        kept += k
        lists += t // REV_TOP_N
        S = fd.get("stocks")
        if isinstance(S, dict):
            sdays += 1
            scells += S.get("n_cells") or 0
            sflips += S.get("flips") or 0
            if S.get("rel_median_pct") is not None:
                smeds.append(S["rel_median_pct"])
            skept += S.get("top3_kept") or 0
            slists += S.get("top3_lists") or 0
    out.update(n_dates=len(found), dates=[d for d, _ in found], n_cells=len(rel), flips=flips,
               rel_median_pct=round(float(pd.Series(rel).median()), 1) if rel else None,
               top3_keep_pct=round(100.0 * kept / (lists * REV_TOP_N), 1) if lists else None,
               top3_lists=lists,
               seed={"label": REV_SEED_LABEL, "dates": sorted(seeded)} if seeded else None)
    if sdays:
        out["stocks"] = {
            "n_dates": sdays, "n_cells": scells, "flips": sflips,
            "rel_median_pct": round(float(pd.Series(smeds).median()), 1) if smeds else None,
            "top3_keep_pct": round(100.0 * skept / (slists * REV_TOP_N), 1) if slists else None,
            "top3_lists": slists,
        }
    return out


def final_diff_text(P: dict, top: int = 15) -> str:
    """``final_diff.txt`` — ``FINAL.txt`` 뒤에 붙일 사람이 읽는 요약."""
    fd, why = P.get("final_diff"), P.get("final_diff_reason")
    ko = {a: k for a, k in TODAY_ACTORS} | {"etc": "기타법인"}
    if fd is None:
        return f"잠정 → 확정 비교 없음: {why}\n"
    base = "확정" if fd["base_final"] else "잠정"
    lines = [f"잠정 → 확정 비교 — 바꿔 끼운 {base} 리포트({fd['base_built']} KST 생성) 대비, "
             f"순매수[억] {'·'.join(f'{w}일' for w in fd['windows'])}, 반올림 ±{fd['tol']}억 초과만"]
    if fd.get("rebased"):
        lines.append(f"첫 확정({fd['inherited_from']} KST) 뒤 값이 또 바뀌어 그 기록의 잠정 열로 1일 칸만 다시 셌다"
                     " — 5일 잠정 합은 남아 있지 않다")
    elif fd.get("inherited_from"):
        lines.append(f"첫 확정({fd['inherited_from']} KST)의 기록을 이어받았다")
    moved = [t for t in fd["market_totals"] if t["diff"] is None or abs(t["diff"]) > fd["tol"]]
    lines.append(f"시장 합계: {len(fd['market_totals'])}칸 중 {len(moved)}칸 바뀜")

    def fmt(v):
        return "—" if v is None else f"{v:+,.2f}"

    for t in moved:
        lines.append(f"  {t['market']} {ko.get(t['actor'], t['actor'])} {t['win']}일  "
                     f"{fmt(t['old'])} → {fmt(t['new'])} ({fmt(t['diff'])})")
    lines.append(f"섹터 칸: {fd['n_cells']}칸 중 {fd['n_changed']}칸 바뀜"
                 + (f" — 큰 순 {min(top, fd['n_changed'])}" if fd["n_changed"] else ""))
    for r in fd["changed"][:top]:
        lines.append(f"  {r['market']}/{r['sector']} {ko.get(r['actor'], r['actor'])} {r['win']}일  "
                     f"{fmt(r['old'])} → {fmt(r['new'])} ({fmt(r['diff'])})")
    if fd["n_changed"] > top:
        lines.append(f"  외 {fd['n_changed'] - top}칸 — payload.json 의 final_diff")
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────── 시장 맥락 — KRX 지수(정규장) · 금액 근사 오차
#
# 둘 다 DB 를 읽기 전용으로 한 번 더 본다(``main`` 이 연결을 닫기 전에 부른다). ``today`` 블록 안에 싣는다 —
# 화면(TUI)은 numbers.html 의 D 하나만 읽고 ``sector_numbers.build`` 가 ``today`` 를 통째로 건넨다.

#: KRX 종합지수 — ``sector_index`` 의 코드. 지수는 **정규장**(09:00~15:30) 값이다. 리포트의 수익률은
#: 2026-09-14 이후 애프터마켓(~20:00)까지 포함한 통합 종가라, 같은 날 둘이 갈린다(11-06: 거래소 자체 바구니
#: −4.37% vs KOSPI −6.02%. 정규장 종가로 다시 재면 0.01%p 안에서 맞는다).
KRX_INDEX = (("거래소", "001", "코스피"), ("코스닥", "101", "코스닥"))
#: 종가·VWAP 환산 차 칩을 띄우는 문턱(%).
APPROX_GAP_WARN = 5.0
_QTY_COLS = ("individual", "foreign_", "institution", "etc_corp")


def krx_index_block(con, dates: list[str], markets: list[str]) -> dict:
    """시장별 KRX 종합지수의 1일·20일 수익률[%] — 날짜는 **이 페이로드의 거래일**에 맞춘다.

    기준일이나 그 ``w`` 거래일 전 지수가 없으면 그 칸은 ``None``(앞 값으로 메우지 않는다 — 저녁 18:10
    배치에 그날 지수가 아직 없으면 "어제 지수" 를 오늘 것처럼 적게 된다).
    """
    need = [dates[-1 - w] for w in MARKET_RET_WINDOWS if w < len(dates)] + [dates[-1]]
    codes = [c for m, c, _ in KRX_INDEX if m in markets]
    if not codes:
        return {}
    ph = "%s" if _is_pg(con) else "?"
    q = (f"SELECT code, date, close FROM sector_index WHERE code IN ({','.join([ph] * len(codes))}) "
         f"AND date >= {ph} AND date <= {ph}")
    si = pd.read_sql_query(q, con, params=[*codes, min(need), dates[-1]])
    si["date"] = si["date"].astype(str)
    px = {(r.code, r.date): float(r.close) for r in si.itertuples() if r.close}
    out = {}
    for m, code, ko in KRX_INDEX:
        if m not in markets:
            continue
        cur = px.get((code, dates[-1]))
        ret = {}
        for w in MARKET_RET_WINDOWS:
            base = px.get((code, dates[-1 - w])) if w < len(dates) else None
            ret[str(w)] = round((cur / base - 1.0) * 100.0, 3) if cur and base else None
        out[m] = {"code": code, "name": ko, "ret": ret}
    return out


def approx_gap(con, df: pd.DataFrame, asof: str, markets: list[str]) -> dict:
    """시장별 **종가·VWAP 환산 차**[%] — 내부 자료끼리의 대조다(D1 검토 M4). 실제 오차가 아니다.

    네이버 시장값 기준 차이는 ``krx_truth`` 가 잰다 — 48일 실측에서 이 값은 최대 3.12% 인데 네이버 시장값 기준 거래소
    오차는 중앙 5.7% 였다(``docs/validation/2026-11-09-market-total-vs-published.md``). 순매수 금액은
    수량 × 그날 종가로 복원한다(``load``). 그날
    종목 i 의 4주체 |순매수 수량| 합을 :math:`n_i` 라 할 때

    ``gap = |Σ n_i·close_i − Σ n_i·vwap_i| / Σ n_i·vwap_i × 100``,  ``vwap_i = trade_value(백만원)·1e6 / volume``

    (``daily_bars``, 거래량·거래대금이 둘 다 양수인 종목만 — 양쪽 합에서 같은 종목 집합). 2026-09-14
    이후 ``daily_bars`` 의 거래량·거래대금과 ``supply_demand.close`` 는 모두 애프터마켓 포함 통합값이라
    VWAP 도 통합 세션의 것이고 셋의 세션 정의가 같다. 지표 검증 Q3b: 코스닥은 48일 중 71% 만 상대차 중앙
    5% 이내였다. ``cover`` = VWAP 가 있는 종목이 종가 환산 금액에서 차지하는 몫[%].
    """
    day = df[df["date"] == asof]
    if day.empty:
        return {}
    ph = "%s" if _is_pg(con) else "?"
    bars = pd.read_sql_query(
        f"SELECT code, volume, trade_value, source FROM daily_bars WHERE date = {ph}", con, params=[asof])
    # 같은 날 소스가 둘이면 키움을 쓴다(수급과 같은 소스).
    bars = (bars.assign(_k=(bars["source"] != "kiwoom").astype(int))
                .sort_values(["code", "_k"]).drop_duplicates("code"))
    bars = bars[(bars["volume"].astype(float) > 0) & (bars["trade_value"].astype(float) > 0)]
    vwap = (bars["trade_value"].astype(float) * 1e6 / bars["volume"].astype(float))
    vwap.index = bars["code"]
    n = sum(day[c].astype(float).abs().fillna(0.0) for c in _QTY_COLS)
    t = pd.DataFrame({"market": day["market"].to_numpy(),
                      "sector": (day["sector"] if "sector" in day else pd.Series(UNCLASSIFIED, index=day.index)
                                 ).to_numpy(),
                      "n": n.to_numpy(),
                      "close": day["close"].astype(float).to_numpy(),
                      "vwap": day["code"].map(vwap).to_numpy(dtype=float)})
    out = {}
    for mkey, mks in [("전체", markets)] + [(m, [m]) for m in markets]:
        sub = t[t["market"].isin(mks)]
        all_amt = float((sub["n"] * sub["close"]).sum())
        cov = sub.dropna(subset=["vwap"])
        c = float((cov["n"] * cov["close"]).sum())
        v = float((cov["n"] * cov["vwap"]).sum())
        if not v:
            out[mkey] = None
            continue
        out[mkey] = {"pct": round(abs(c - v) / v * 100.0, 2),
                     "close": round(c / EOK, 1), "vwap": round(v / EOK, 1),
                     "cover": round(c / all_amt * 100.0, 1) if all_amt else None,
                     "sector_max": _sector_gap_max(cov, v)}
    return out


#: 섹터별 근사 오차는 그 섹터의 VWAP 환산 금액이 시장의 이 몫 이상일 때만 잰다(분모 게이트).
SECTOR_GAP_MIN_SHARE = 0.02


def _sector_gap_max(cov: pd.DataFrame, market_vwap: float) -> "dict | None":
    """시장 안에서 근사 오차가 가장 큰 섹터 — 시장 합계에서는 종목 간 오차가 상쇄되지만 섹터 칸은 덜 상쇄된다."""
    best = None
    for sec, g in cov.groupby("sector"):
        v = float((g["n"] * g["vwap"]).sum())
        if v <= 0 or v < SECTOR_GAP_MIN_SHARE * market_vwap:
            continue
        pct = abs(float((g["n"] * g["close"]).sum()) - v) / v * 100.0
        if best is None or pct > best["pct"]:
            best = {"sector": sec, "pct": round(pct, 2)}
    return best


#: 분봉 밖 거래 큼 — 그날 거래량 중 분봉에 없는 몫의 문턱.
BLOCK_OFF_MIN = 0.5
#: 분모 게이트 — 그 종목 최대 주체 금액이 시장 Σ|개인·외국인·기관 순매수| 의 이 몫 이상일 때만 띄운다.
BLOCK_SHARE_MIN = 0.01
#: 분봉 수집 완료 게이트 — 분봉이 있는 종목의 (분봉 합 ÷ 거래량) 중앙이 이 미만이면 판정하지 않는다.
BLOCK_MIN_COVER = 0.95


def _minute_volume(con, asof: str) -> "dict[str, float] | None":
    """그날 종목별 분봉 거래량 합. ``minute_bars`` 표가 없는 DB(테스트 픽스처·BYO)는 ``None``."""
    pg = _is_pg(con)
    exists = pd.read_sql_query(
        "SELECT to_regclass('public.minute_bars') IS NOT NULL AS ok" if pg else
        "SELECT count(*) AS ok FROM sqlite_master WHERE type='table' AND name='minute_bars'", con)
    if not bool(exists["ok"].iloc[0]):
        return None
    ph = "%s" if pg else "?"
    nxt = (datetime.fromisoformat(asof) + timedelta(days=1)).date().isoformat()
    q = pd.read_sql_query(
        f"SELECT code, SUM(volume) AS vm FROM minute_bars WHERE ts >= {ph} AND ts < {ph} GROUP BY code",
        con, params=[asof, nxt])
    return {r.code: float(r.vm) for r in q.itertuples() if r.vm is not None}


def block_suspects(con, df: pd.DataFrame, asof: str, markets: list[str]) -> dict:
    """시장별 **분봉 밖 거래 큼** 종목 — 시장 합계가 네이버 시장값과 크게 갈린 날의 원인 후보(측정, 2026-11-09).

    ``off = max(0, volume − Σ분봉 volume) / volume`` 이 :data:`BLOCK_OFF_MIN` 이상이고 최대 주체 금액
    ``max_a |q_a|·P`` 가 시장 ``Σ_a |Σ q_a·P|`` 의 :data:`BLOCK_SHARE_MIN` 이상인 종목(a = 개인·외국인·기관).
    분봉 밖 거래량이 시간외 대량매매라는 것은 **추정**이다 — 체결 유형 자료가 없다. 분봉이 없는 종목은 판정하지
    않고(``unjudged`` = 금액 게이트는 넘었는데 분봉이 없어 판정 못 한 종목 수), 표가 없거나 수집이 덜 끝난 날은
    ``status: skip`` 으로 이유를 적는다(0 건과 모름을 가른다). 대조한 범위(10-12 까지)에서 수량은 네이버 종목 값과 같았다 —
    금액까지 맞다는 말은 아니고, 시장값과 정의 차이일 수 있다는 표지다(까닭 미확인).
    문서 ``docs/validation/2026-11-09-market-total-vs-published.md`` 의 정의와 같다.
    """
    minutes = _minute_volume(con, asof)
    if minutes is None:
        return {"status": "skip", "reason": "분봉 표(minute_bars)가 없다"}
    if not minutes:
        return {"status": "skip", "reason": "그날 분봉이 없다"}
    ph = "%s" if _is_pg(con) else "?"
    bars = pd.read_sql_query(f"SELECT code, volume, source FROM daily_bars WHERE date = {ph}", con, params=[asof])
    bars = (bars.assign(_k=(bars["source"] != "kiwoom").astype(int))
                .sort_values(["code", "_k"]).drop_duplicates("code"))
    volume = {r.code: float(r.volume) for r in bars.itertuples() if r.volume and float(r.volume) > 0}
    ratios = [minutes[c] / v for c, v in volume.items() if c in minutes]
    cover = float(pd.Series(ratios).median()) if ratios else 0.0
    if cover < BLOCK_MIN_COVER:
        return {"status": "skip", "reason": f"분봉 수집이 덜 끝났다(분봉 합÷거래량 중앙 {cover * 100:.0f}%)"}
    day = df[df["date"] == asof]
    out: dict = {m: [] for m in markets}
    unjudged = 0
    for m in markets:
        sub = day[day["market"] == m]
        amts = {a: sub[a].astype(float).fillna(0.0) for a in ("indiv", "forgn", "inst")}
        den = sum(abs(float(v.sum())) for v in amts.values())
        if not den:
            continue
        for i, r in enumerate(sub.itertuples()):
            actor, amt = max(((a, float(x.iloc[i])) for a, x in amts.items()), key=lambda kv: abs(kv[1]))
            if abs(amt) < BLOCK_SHARE_MIN * den:
                continue
            v, vm = volume.get(r.code), minutes.get(r.code)
            if v is None or vm is None:
                unjudged += 1
                continue
            off = max(0.0, v - vm) / v
            if off >= BLOCK_OFF_MIN:
                out[m].append({"code": r.code, "name": r.name, "off_pct": round(off * 100.0, 1),
                               "actor": actor, "amt": round(amt, 1)})
        out[m].sort(key=lambda x: (-abs(x["amt"]), x["code"]))
    return {"status": "ok", "off_min": BLOCK_OFF_MIN, "share_min": BLOCK_SHARE_MIN, "unjudged": unjudged, "markets": out}


def market_context(con, df: pd.DataFrame, P: dict) -> None:
    """``today`` 에 KRX 지수(정규장)·시장별 금액 근사 오차·분봉 밖 거래 큼을 붙인다 — 제자리 수정."""
    T = P["today"]
    T["krx_index"] = krx_index_block(con, P["dates"], P["markets"])
    T["approx_gap_warn"] = APPROX_GAP_WARN
    gaps = approx_gap(con, df, P["dates"][-1], P["markets"])
    for mkey, M in T["market"].items():
        M["approx_gap"] = gaps.get(mkey)
    T["block_suspects"] = block_suspects(con, df, P["dates"][-1], P["markets"])


#: 설치본(wheel)에는 일부러 없는 옆 스크립트 — 내부 검증 전용이고 목적지가 data.krx.co.kr 이라 BYO 설계 §4.2 허용 목록 밖이다.
KRX_TRUTH_SCRIPT = "krx_truth.py"


def _attach_krx_truth(payload: dict, build_dir: "str | None", root: "str | None",
                      path: "str | None" = None) -> None:
    """네이버 시장값 대조(선택 기능) — 어떤 실패도 리포트를 깨지 않는다.

    ``attach`` 안의 실패는 그쪽이 skip 블록으로 바꾼다. 여기는 그 앞, **모듈을 읽는 단계**의 보호다. ``scripts/`` 는
    패키지가 아니라 importlib 로 읽는다. 파일이 없으면(설치본) 꺼진 것과 같고, 못 읽으면 skip 블록을 남긴다.
    """
    p = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), KRX_TRUTH_SCRIPT)
    if not os.path.isfile(p):
        if os.environ.get("KR_QUANT_KRX_TRUTH") == "1":
            print(f"⚠️ [네이버 대조] {KRX_TRUTH_SCRIPT} 가 없는 설치본이라 건너뛴다", file=sys.stderr)
        return
    import importlib.util  # noqa: PLC0415
    try:
        spec = importlib.util.spec_from_file_location("krx_truth", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001 — 선택 기능의 버그가 본 리포트를 죽이면 안 된다
        if isinstance(payload.get("today"), dict):
            payload["today"]["krx_truth"] = {"status": "skip", "reason": f"모듈 로드 실패: {e!r}"}
        print(f"⚠️ [네이버 대조] 내부 오류 — 모듈 로드 실패 {e!r}. 리포트는 그대로 만든다", file=sys.stderr)
        return
    mod.attach(payload, build_dir, root)


def script_json(payload: dict) -> str:
    """``<script>`` 안에 넣을 JSON — 값에 ``</script>`` 가 있어도 태그가 안 닫힌다(``<\\/`` 는 같은 JSON)."""
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("KR_QUANT_DB") or db_default())
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--json")
    ap.add_argument("--html")
    ap.add_argument("--from-json", help="이미 만든 페이로드를 읽어 HTML 만 낸다")
    ap.add_argument("--final", action="store_true",
                    help="daily_finalize 확인 뒤 확정치로 만든다(daily_report.sh 확정 모드가 붙인다)")
    ap.add_argument("--asof", help="이 기준일(YYYY-MM-DD)까지의 데이터로 만든다 — 지난 기준일 재구성")
    ap.add_argument("--rebuilt", action="store_true",
                    help="재구성 리포트로 표시한다(daily_report.sh 의 KR_QUANT_ASOF 가 붙인다)")
    ap.add_argument("--finalize-unverified", action="store_true",
                    help="재구성인데 daily_finalize 창(달력 3일) 밖이라 확정 기록이 없다 — 값은 지금 DB 의 마감 값이다")
    ap.add_argument("--reports-root",
                    help="직전 리포트를 찾을 리포트 루트(기본: --json 이 <루트>/<기준일>[.building.N]/ 안이면 그 루트)")
    a = ap.parse_args()

    if a.from_json:
        with open(a.from_json, encoding="utf-8") as f:
            payload = json.load(f)
        tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "templates", "sector_flow.html")
        with open(tpl, encoding="utf-8") as f:
            html = f.read()
        with open(a.html, "w", encoding="utf-8") as f:
            f.write(html.replace("__DATA__", script_json(payload)).replace(BASIS_SLOT, DATA_BASIS))
        print(f"wrote {a.html}  (재사용: {a.from_json})")
        return

    con = connect(a.db)
    df, dates = load(con, a.days, asof=a.asof)
    df = attach_daily_cap(con, df)
    caps = sector_cap(con, df, dates[-1])
    global INDEX_RET
    members = {m: dict(g.groupby("sector")["code"].nunique())
               for m, g in df.groupby("market")}
    INDEX_RET = index_returns(con, dates, sorted(df["sector"].unique()),
                              sorted(df["market"].dropna().unique()), members)
    cap_monthly = sector_cap_monthly(con, df, dates)
    payload = build_payload(df, dates, caps, cap_monthly)
    # 권리락 뒤 신주가 아직 상장 전인 종목 — 값은 그대로 두고 표지만 싣는다(storage.SHARE_EVENTS 주석).
    for code, pending in rights_pending(con, sorted(payload["names"]), dates[-1]).items():
        payload["names"][code]["cap_pending"] = pending
    payload["sector_override"] = override_block(OVERRIDE_APPLIED)
    market_context(con, df, payload)
    con.close()
    # 직전 리포트·바꿔 끼울 리포트는 **쓰기 전에** 읽는다 — --json 이 그 폴더의 payload.json 일 수 있다.
    root = a.reports_root or reports_root(a.json or a.html)
    payload["change"], payload["change_reason"] = change_block(payload, root)
    if a.final:
        # 키를 확정일 때만 싣는다.
        payload["final"] = True
        if a.rebuilt:
            # 같은 자리의 옛 리포트는 옛 코드·정의로 만든 것이라 잠정→확정 비교가 아니다.
            payload["final_diff"] = None
            payload["final_diff_reason"] = "재구성 리포트다 — 같은 기준일의 옛 리포트는 옛 코드·정의로 만들어 비교하지 않는다"
        else:
            payload["final_diff"], payload["final_diff_reason"] = final_diff_block(payload, root)
        # FINAL.txt 는 daily_report.sh 가 쓴다 — 거기 덧붙일 글을 산출물 옆에 둔다.
        if a.json or a.html:
            here = os.path.dirname(os.path.abspath(a.json or a.html))
            with open(os.path.join(here, "final_diff.txt"), "w", encoding="utf-8") as f:
                f.write(final_diff_text(payload))
            # 다음 리포트들의 revision_stats 가 3MB 페이로드 대신 이것만 읽는다.
            if payload["final_diff"] is not None:
                with open(os.path.join(here, "final_diff.json"), "w", encoding="utf-8") as f:
                    json.dump(payload["final_diff"], f, ensure_ascii=False)
    payload["revision_stats"] = revision_stats(payload, root)
    # 네이버 시장값 대조(선택, D1-m2) — 같은 기준일 캐시(krx_truth.json)가 있거나
    # KR_QUANT_KRX_TRUTH=1 일 때만. 실패·버그는 skip 블록이 된다 — 리포트를 깨지 않는다.
    _attach_krx_truth(payload,
                      os.path.dirname(os.path.abspath(a.json or a.html)) if (a.json or a.html) else None,
                      root)
    if a.rebuilt:
        # 키를 재구성일 때만 싣는다 — 화면이 "재구성" 을 붙인다.
        payload["rebuilt"] = True
        if a.finalize_unverified:
            # 확정(final)이 아니다 — daily_finalize 가 그날을 덮었다는 기록이 없다. 다만 두 달 지난 값이라 마감 값이고
            # 수익률 라벨은 확정과 같다(~20:00). 화면은 이 키로 "확정 기록 없음" 을 단다.
            payload["finalize_unverified"] = True
    covered = sum(1 for m in payload["markets"] for s in payload["sectors"]
                  if payload["iret"][m].get(s))
    print(f"업종지수 커버리지: {covered} / "
          f"{len(payload['markets']) * len(payload['sectors'])} (시장×섹터)")

    if a.html:
        tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "templates", "sector_flow.html")
        with open(tpl, encoding="utf-8") as f:
            html = f.read()
        with open(a.html, "w", encoding="utf-8") as f:
            f.write(html.replace("__DATA__", script_json(payload)).replace(BASIS_SLOT, DATA_BASIS))
        print(f"wrote {a.html}  ({dates[0]} ~ {dates[-1]}, {len(dates)}거래일, "
              f"{payload['n_names']}종목, 마감후={payload['finalized']})")
        return

    out = a.json or "-"
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        print(f"wrote {out}  ({dates[0]} ~ {dates[-1]}, {len(dates)}거래일)")
        return

    print(f"# 섹터 자금흐름 — {dates[0]} ~ {dates[-1]} ({len(dates)}거래일)\n")
    print("| 섹터 | 시장 | 기관누적(억) | 비중변화(%p) | 거래대금(억) |")
    print("|---|---|---:|---:|---:|")
    rows = []
    for m in payload["markets"]:
        for s in payload["sectors"]:
            f = payload["flows"][m][s]
            tot = sum(f["inst"])
            cap = payload["cap"].get(m, {}).get(s, 0.0)
            rows.append((s, m, tot, (tot / cap * 100) if cap else 0.0, sum(f["tv"])))
    for s, m, tot, sh, tv in sorted(rows, key=lambda x: -x[3])[:15]:
        print(f"| {s} | {m} | {tot:+,.0f} | {sh:+.2f} | {tv:,.0f} |")


if __name__ == "__main__":
    main()
