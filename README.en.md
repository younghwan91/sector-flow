<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo-dark.svg">
  <img alt="sector-flow" src="docs/brand/logo.svg" height="52">
</picture>

[한국어](README.md) · **English**

**sector-flow · 수급 지도 (flow map)** — who sold and who bought which sector today: net buying by investor type
(individuals, foreigners, institutions, other corporations) across KOSPI and KOSDAQ sectors, in a terminal and a browser.
The numbers are same-period observations, not forecasts.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

## Quick start

```bash
uv sync
uv run sw-flow --demo      # sector flow screen — no database, no data needed
uv run sw-ledger --demo    # flow ledger — same demo
uv run sector-flow-demo --out ~/sf-demo && uv run python scripts/serve_reports.py --root ~/sf-demo   # same demo in a browser — http://127.0.0.1:8765/
```

**Everything in `--demo` is random.** Sector names such as `가상전자` ("virtual electronics") are made up on purpose, and
every demo screen carries a `[합성 데모]` ("synthetic demo") label. The first run takes a few dozen seconds to build
`~/.cache/sector-flow/demo`; later runs open at once.

The screens and documents are in Korean. The full guide — what each column means, how the daily batch runs, how the report
checks itself — is the [Korean README](README.md).

## Data policy

- This repository ships **code only**. Every number and picture in the README, docs and any public page is a synthetic demo.
- Real data comes from your own account and key (the bring-your-own collector `sector-flow-collect` uses your own Kiwoom
  REST API key and writes a local SQLite file) and stays on your own machine.
- Market data from KRX and brokers generally may not be redistributed. Do not share report folders, real per-stock flows, or
  screenshots of real data — not in issues, pull requests, or anywhere else. Use the demo to show a problem.
- Not investment advice. Sector indicators were tested as predictors of future returns and showed no evidence (or were
  inconclusive); they are shown as observations only. Rights holders can reach the maintainer through GitHub issues, and
  requests are acted on promptly — see [DISCLAIMER.md](DISCLAIMER.md) and [SECURITY.md](SECURITY.md) (Korean).

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) (Korean) and the [code of conduct](CODE_OF_CONDUCT.md).

## Sponsoring

sector-flow is MIT-licensed open source. If the code is useful to you, you can support its development and maintenance
through [GitHub Sponsors](https://github.com/sponsors/younghwan91). Sponsorship buys no perks — sponsors see no extra data,
screens or numbers — and no data is ever sold.

## License

MIT
