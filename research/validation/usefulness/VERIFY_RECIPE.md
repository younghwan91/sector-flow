# (haiku-verifier 지시문 — 수급 쓸모 표본 재계산) Recompute sample rows from the raw DB (read-only). Do not import repo code.

DB: from the repo root run
  set -a; . ~/git/quant-airflow/.env; set +a
  uv run --extra pg python your_script.py
psycopg2.connect(os.environ["KR_QUANT_DB"], options="-c statement_timeout=120000 -c default_transaction_read_only=on").
NEVER print environment variables or the DSN.

Common:
- SOURCES = ('kiwoom','kiwoom_backfill_2026-10-11','kiwoom_backfill_2026-10-12'). Trading dates = DISTINCT supply_demand.date.
- Window W20(t) = the 20 trading dates ending at t inclusive; W60 = 60 trading dates ending at t inclusive.
- Net buy amount (억원) of a code on day d for column c = supply_demand.c * abs(supply_demand.close) / 1e8 (row with source IN SOURCES).
- Market cap (억원) of a code on day t = price * shares / 1e8:
    price = abs(supply_demand.close) on t (else daily_bars_raw.close on t);
    shares = latest shares_outstanding_history.shares_outstanding with date <= t (date s), times PRODUCT of 1/(1+upd_rt/100)
      over corporate_actions of that code with s < date <= t, (upd_stkpc_tp::int & 88) != 0, upd_rt not null, != 0, > -100.
- Sector = stocks.sector (NULL/'' -> '(미분류)'), stocks.market IN ('거래소','코스닥'), both markets together.

Stock row (K): 
- K1 numerator = sum over d in W20(t) of net buy of (invtrt + penfnd_etc); K2 = institution; K3 = foreign_.
- cap = market cap of the code on t.
- r_h = 100*(adj(t+h)/adj(t) - 1), adj = daily_bars_adjusted.close, t+h = the h-th trading date after t.

Sector row (S):
- sector cap = sum of market caps on t of sector codes that have a supply_demand row (SOURCES) on t.
- S1 numerator = sum over codes of the sector and d in W20(t) of net buy (invtrt + penfnd_etc); S2 = foreign_ over W20; S3 = institution over W60.
- f5/f20: follow research/validation/q3cd/VERIFY_RECIPE.md

Tolerance: relative 0.1% or absolute 0.01 (whichever larger) for amounts/caps; 0.01 %p for returns.
Return JSON per row: {"row":..., "field": {"claimed":..,"recomputed":..,"match":..}, ..., "notes":".."}
