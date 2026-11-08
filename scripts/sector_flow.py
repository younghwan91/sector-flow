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
import os
import re
import warnings
from datetime import datetime, timedelta, timezone

import pandas as pd

from sectorflow.basis import BASIS_SLOT, DATA_BASIS
from sectorflow.storage import (
    _is_pg,
    connect, db_default, market_cap_asof_bulk, read_supply_demand, recent_dates)

# pandas 는 psycopg2 연결을 "검증 안 된 DBAPI" 라며 read_sql 마다 UserWarning 을 낸다. 동작엔 문제가 없는데
# 크론 로그를 덮어 진짜 경고를 묻는다(2026-11 cron.log 에 1,491줄). 이 메시지만 끈다 — 크론의 전역 -W 는 다른 경고까지 끈다.
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy", category=UserWarning)

EOK = 1e8                 # 원 → 억원
INDEX_RET: dict = {}
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


def load(con, days: int) -> tuple[pd.DataFrame, list[str]]:
    dates = recent_dates(con, days)
    if not dates:
        raise SystemExit("supply_demand 에 데이터가 없다 — 접속정보를 불러왔는지 확인하라.\n"
                         "  set -a; . ~/git/quant-airflow/.env; set +a  (또는 --db)")

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
    df["sector"] = df["sector"].fillna("").replace("", "(미분류)")

    for raw, short in ACTORS:
        df[short] = df[raw].astype(float) * df["close"] / EOK
    for raw, _ in INST_DETAIL:
        df[raw] = df[raw].astype(float) * df["close"] / EOK
    df["tv"] = df["acc_trde_qty"].astype(float) * df["close"] / EOK
    df["ret"] = adjusted_returns(con, df[["code", "date"]], dates)
    return df, dates


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
    si = pd.read_sql_query("SELECT code, name, date, close FROM sector_index", con)
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
#: 시장 합계에 싣는 주체 — 4주체(합 ≈ 0)와 투신·연기금.
MARKET_ACTORS = ("inst", "forgn", "indiv", "etc", "invtrt", "penfnd_etc", "invpen")
#: 시장 맥락 수익률의 구간(거래일).
MARKET_RET_WINDOWS = (1, 20)
#: 직전 리포트 대비 — 이 구간 순매수 순위와 상위 몇 섹터의 들고 남.
CHANGE_WINDOW = 20
CHANGE_ACTORS = ("inst", "invpen")
CHANGE_TOP = 5
#: 잠정 → 확정 비교 칸. 일별 값이 소수 2자리라 5일 합의 반올림 차는 양쪽 합쳐 최대 0.05억이다.
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
    """
    asof = P["dates"][-1]
    old, why = _read_report(root, asof)
    if old is None:
        return None, why.replace(f"{asof} 리포트 폴더가 없다", "같은 기준일의 잠정 리포트가 없었다")
    built = os.path.getmtime(os.path.join(root, asof, "payload.json"))
    changed, totals, n_cells = [], [], 0

    def win_sum(ser, w):
        return None if ser is None or len(ser) < w else round(sum(ser[-w:]), 2)

    for m in P["markets"]:
        for a in FINAL_DIFF_ACTORS:
            tn = [0.0] * len(FINAL_DIFF_WINDOWS)
            to: list = [0.0] * len(FINAL_DIFF_WINDOWS)
            for s in P["sectors"]:
                if not _present(P, [m], s):
                    continue
                sn = _series(P, [m], s, a)
                so = _series(old, [m], s, a) if m in (old.get("markets") or []) else None
                if sn is None:
                    continue
                for j, w in enumerate(FINAL_DIFF_WINDOWS):
                    vn, vo = win_sum(sn, w), win_sum(so, w)
                    n_cells += 1
                    tn[j] += vn
                    to[j] = None if to[j] is None or vo is None else to[j] + vo
                    if vo is None or abs(vn - vo) > FINAL_DIFF_TOL:
                        changed.append({"market": m, "sector": s, "actor": a, "win": w, "old": vo,
                                        "new": vn, "diff": None if vo is None else round(vn - vo, 2)})
            for j, w in enumerate(FINAL_DIFF_WINDOWS):
                vo = None if to[j] is None else round(to[j], 2)
                totals.append({"market": m, "actor": a, "win": w, "old": vo, "new": round(tn[j], 2),
                               "diff": None if vo is None else round(tn[j] - vo, 2)})
    changed.sort(key=lambda r: -(abs(r["diff"]) if r["diff"] is not None else float("inf")))
    return {
        "base_final": bool(old.get("final")),
        "base_built": datetime.fromtimestamp(built, timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M"),
        "windows": list(FINAL_DIFF_WINDOWS), "tol": FINAL_DIFF_TOL,
        "n_cells": n_cells, "n_changed": len(changed),
        "changed": changed, "market_totals": totals,
    }, None


def final_diff_text(P: dict, top: int = 15) -> str:
    """``final_diff.txt`` — ``FINAL.txt`` 뒤에 붙일 사람이 읽는 요약."""
    fd, why = P.get("final_diff"), P.get("final_diff_reason")
    ko = {a: k for a, k in TODAY_ACTORS} | {"etc": "기타법인"}
    if fd is None:
        return f"잠정 → 확정 비교 없음: {why}\n"
    base = "확정" if fd["base_final"] else "잠정"
    lines = [f"잠정 → 확정 비교 — 바꿔 끼운 {base} 리포트({fd['base_built']} KST 생성) 대비, "
             f"순매수[억] 1일·5일, 반올림 ±{fd['tol']}억 초과만"]
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
#: 금액 근사 오차 칩을 띄우는 문턱(%).
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
    """시장별 **순매수 금액 근사 오차**[%] — 종가 환산과 VWAP 환산의 차이(D1 검토 M4).

    순매수 금액은 수량 × 그날 종가로 복원한다(``load``). 참값에 가까운 것은 그날 VWAP 환산이다. 그날
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
    t = pd.DataFrame({"market": day["market"].to_numpy(), "n": n.to_numpy(),
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
                     "cover": round(c / all_amt * 100.0, 1) if all_amt else None}
    return out


def market_context(con, df: pd.DataFrame, P: dict) -> None:
    """``today`` 에 KRX 지수(정규장)와 시장별 금액 근사 오차를 붙인다 — 제자리 수정."""
    T = P["today"]
    T["krx_index"] = krx_index_block(con, P["dates"], P["markets"])
    T["approx_gap_warn"] = APPROX_GAP_WARN
    gaps = approx_gap(con, df, P["dates"][-1], P["markets"])
    for mkey, M in T["market"].items():
        M["approx_gap"] = gaps.get(mkey)


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
    df, dates = load(con, a.days)
    df = attach_daily_cap(con, df)
    caps = sector_cap(con, df, dates[-1])
    global INDEX_RET
    members = {m: dict(g.groupby("sector")["code"].nunique())
               for m, g in df.groupby("market")}
    INDEX_RET = index_returns(con, dates, sorted(df["sector"].unique()),
                              sorted(df["market"].dropna().unique()), members)
    cap_monthly = sector_cap_monthly(con, df, dates)
    payload = build_payload(df, dates, caps, cap_monthly)
    market_context(con, df, payload)
    con.close()
    # 직전 리포트·바꿔 끼울 리포트는 **쓰기 전에** 읽는다 — --json 이 그 폴더의 payload.json 일 수 있다.
    root = a.reports_root or reports_root(a.json or a.html)
    payload["change"], payload["change_reason"] = change_block(payload, root)
    if a.final:
        # 키를 확정일 때만 싣는다.
        payload["final"] = True
        payload["final_diff"], payload["final_diff_reason"] = final_diff_block(payload, root)
        # FINAL.txt 는 daily_report.sh 가 쓴다 — 거기 덧붙일 글을 산출물 옆에 둔다.
        if a.json or a.html:
            with open(os.path.join(os.path.dirname(os.path.abspath(a.json or a.html)),
                                   "final_diff.txt"), "w", encoding="utf-8") as f:
                f.write(final_diff_text(payload))
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
