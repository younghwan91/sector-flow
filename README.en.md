<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo-dark.svg">
  <img alt="sector-flow" src="docs/brand/logo.svg" height="64">
</picture>

# sector-flow

**Who sold and who bought which sector today — net buying by investor type, as one map.**

KOSPI and KOSDAQ sector flows in a terminal and a browser. Same-period observations, not forecasts.

[![Release](https://img.shields.io/github/v/release/younghwan91/sector-flow)](https://github.com/younghwan91/sector-flow/releases)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[Live demo](https://younghwan91.github.io/sector-flow/) · [Guide (Korean)](docs/guide.md) · [Bring your own key](docs/byo/quickstart.md) · [한국어](README.md) · [Sponsor](https://github.com/sponsors/younghwan91)

</div>

![Flow map time-lapse (synthetic demo) — investors that net sold on the left, sectors in the middle, investors that net bought on the right. Band width is the amount; the map is redrawn day by day](docs/images/map-demo.gif)

<p align="center"><sub>Flow map time-lapse, <b>synthetic demo</b> — every number and name in these pictures is random.<br>
Each band is one (investor type, sector) cell; its width is the amount. Sectors are never linked to sectors, nor investors to investors.</sub></p>

## Quick start

```bash
uv sync
uv run sw-flow --demo      # terminal — no database, no data needed
uv run sector-flow-demo --out ~/sf-demo && uv run python scripts/serve_reports.py --root ~/sf-demo   # flow map and tables in a browser — http://127.0.0.1:8765/
```

You need no key and no data. The first run takes a few dozen seconds to build the synthetic demo; after that it opens at once.
Sector names such as `가상전자` ("virtual electronics") are made up on purpose, and every demo screen carries a `[합성 데모]` label.

## What you see

- **Flow map** `map.html` — investor types that sold → sectors → investor types that bought. Click a type to see only its flows; the "time" view stacks them by date.
- **Terminal** `sw-flow` — today's flows on the first screen, then 27 sectors × 5 windows × 3 markets × 4 investor types; `Enter` drills into stocks.
- **Ledger** `sw-ledger` — who handed over how much, and what stayed unclassified, in won.
- **Web tables** `numbers.html` and `viewer.html`.

The screens and documents are in Korean. The full guide — every column, the daily batch, how each report checks itself — is
[docs/guide.md](docs/guide.md).

## Why sector-flow?

- **Observations only.** It does not score, rank or pick. It shows who net bought how much of which sector, and stops there.
- **How unusual is today?** Each day's net buying is placed in that sector's one-year (260 trading days) distribution, with streak lengths.
- **From sector to stock.** The first screen lists each investor type's top stocks; `Enter` opens the rest.
- **One basket.** Net buying, market cap and returns come from the same set of stocks, so numerators and denominators line up.
- **It checks itself.** Thirteen verification layers recount every report, and mutation tests check that the checks still bite.

## Data policy

- This repository ships **code only**. Every number and picture in the README, docs and any public page is a synthetic demo.
- Real data comes from your own account and key (the bring-your-own collector `sector-flow-collect`, guide at
  [docs/byo/quickstart.md](docs/byo/quickstart.md)) and stays on your own machine.
- Market data from KRX and brokers generally may not be redistributed. Do not share report folders, real per-stock flows, or
  screenshots of real data — not in issues, pull requests, or anywhere else. Use the demo to show a problem.
- Not investment advice. Sector indicators were tested as predictors of future returns and showed no evidence (or were
  inconclusive); they are shown as observations only. Rights holders can reach the maintainer through GitHub issues, and
  requests are acted on promptly — see [DISCLAIMER.md](DISCLAIMER.md) and [SECURITY.md](SECURITY.md) (Korean).

## Contributing · Sponsoring

- **Issues** — bugs, questions and ideas in [Issues](https://github.com/younghwan91/sector-flow/issues); reproduce with `--demo`, not real data.
- **Contributing** — [CONTRIBUTING.md](CONTRIBUTING.md) (Korean) and the [code of conduct](CODE_OF_CONDUCT.md).
- **Sponsoring** — if the code is useful to you, [GitHub Sponsors](https://github.com/sponsors/younghwan91). It buys no perks — sponsors see no extra data, screens or numbers — and no data is ever sold.

## License

MIT
