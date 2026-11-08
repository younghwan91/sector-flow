# (haiku-verifier 지시문 — Q3d 재검 앞날 수익률 재계산) Recompute one forward sector return f_h(A, S) from the raw DB (read-only)

DB: run from the repo root:
  set -a; . ~/git/quant-airflow/.env; set +a
  uv run --extra pg python your_script.py
Connect with psycopg2.connect(os.environ["KR_QUANT_DB"], options="-c statement_timeout=120000 -c default_transaction_read_only=on").
NEVER print environment variables or the connection string.

Definitions (all dates are trading dates = DISTINCT supply_demand.date):
- SOURCES = ('kiwoom','kiwoom_backfill_2026-10-11','kiwoom_backfill_2026-10-12').
- Sector membership: stocks.sector (NULL or '' means '(미분류)'), stocks.market IN ('거래소','코스닥'). Current labels, both markets together.
- Let D1..Dh be the h trading dates strictly after A.
- For each day d in D1..Dh, universe U(d) = codes of sector S with a supply_demand row on d with source IN SOURCES.
- Stock return r_i(d) in % = 100 * (adj(d) / adj(prev) - 1), where adj = daily_bars_adjusted.close (rows with close>0),
  prev = the code's latest daily_bars_adjusted row strictly before d. Missing adj(d) -> drop the stock that day.
- Weight w_i(d) = market cap of the code on p = the code's previous supply_demand row date (source IN SOURCES) strictly before d.
  cap(p) = price(p) * shares(p)
    price(p) = abs(supply_demand.close) on p (source IN SOURCES); if missing or <=0 use daily_bars_raw.close on p.
    shares(p) = shares_outstanding_history.shares_outstanding of the latest row with date <= p (call its date s),
      multiplied by PRODUCT over corporate_actions rows of that code with s < date <= p and (upd_stkpc_tp::int & 88) != 0
      and upd_rt not null, upd_rt != 0, upd_rt > -100, of 1/(1+upd_rt/100).
      shares <= 0 -> missing.
  Missing cap -> drop the stock that day.
- Sector day return R(d) = sum(w*r)/sum(w) over stocks with both w and r. (No stocks -> 0.)
- f_h = 100 * (PRODUCT over d of (1 + R(d)/100) - 1).

Tolerance: |recomputed - claimed| <= 0.01 (percentage points).
Return JSON: {"asof":..., "sector":..., "h":..., "claimed":..., "recomputed":..., "match": true/false, "n_stocks_per_day":[...], "notes":"..."}
