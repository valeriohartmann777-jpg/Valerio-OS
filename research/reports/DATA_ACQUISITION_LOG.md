# Data acquisition log

Append-only record of where market data was searched for and what happened.
DATA_QUALITY_REPORT.md is regenerated from the data; this log is not.

## 2026-10-07 — environment search and download attempts

**Local search.** The repository (a Next.js app) and the container filesystem were
searched for `.parquet .csv .feather .h5 .db .sqlite .arrow` files and for the names
ES, NQ, MES, MNQ, CME, futures, OHLCV, trades, ticks, bid, ask. No market data found.
The connected Google Drive had no market data either.

**Network.** Outbound HTTPS goes through the environment proxy. Every market-data host
was refused at the proxy (`CONNECT tunnel failed, response 403`), re-checked at
13:38 UTC:

| host | purpose | result |
|---|---|---|
| data.binance.vision | Binance public kline archives (BTCUSDT proxy) | 403 at proxy |
| api.binance.com, fapi.binance.com | Binance REST | 403 at proxy |
| api.bybit.com | Bybit REST | 403 at proxy |
| api.kraken.com, api.exchange.coinbase.com | crypto REST | 403 at proxy |
| query1.finance.yahoo.com | Yahoo chart API (ES=F, daily/limited intraday) | 403 at proxy |
| stooq.com | daily futures history | 403 at proxy |
| datafeed.dukascopy.com | CFD index ticks | 403 at proxy |
| hist.databento.com, api.databento.com | CME GLBX.MDP3 (paid, API key) | 403 at proxy |
| firstratedata.com | vendor bars (paid) | 403 at proxy |
| www.cmegroup.com | contract specs | 403 at proxy |
| zenodo.org | research datasets | 403 at proxy |
| pypi.org | Python packages | 200 (allowed) |

GitHub is reachable, but this session may only read the project's own repository, so
third-party data repositories were not used. The LuxAlgo connector only offers derived
statistics for a few crypto symbols, not raw bars; it is not a data source for this study.

**Conclusion.** No real data can be acquired from inside the environment. Per the
mission rules, no synthetic data was generated for research, and no results exist.

**Ways to unblock (in order of preference):**
1. Upload ES and NQ 1-minute OHLCV (CSV, txt or parquet; ideally 2015 or later, at least
   3 years) in the project thread. Any vendor works; state the timezone of the timestamps
   and whether a timestamp marks the bar open or close (see `configs/datasets.yaml`).
2. Commit a data file to a branch of the project repository.
3. Allow a data host in the environment's network settings. `data.binance.vision`
   would give BTCUSDT perpetual klines, which are PROXY evidence only and never stand
   in for CME results.

## 2026-10-09 — Databento (GLBX.MDP3 trades, ES.FUT and NQ.FUT)

The Databento pipeline is implemented (README, section "Databento"). Before the pilot
cost estimate (2024-01-01 to 2024-04-01), checked at 10:35 UTC:

| check | result |
|---|---|
| environment variable `DATABENTO_API_KEY` | not set |
| hist.databento.com (historical API: metadata, cost, downloads) | 403 at proxy (`CONNECT tunnel failed`) |
| api.databento.com | 403 at proxy |

Nothing was requested from Databento and nothing was downloaded; $0.00 spent. No cost
estimate exists yet, because the cost endpoints are on the same blocked host.

**To unblock:** allow `hist.databento.com` in the cloud environment's network access and
add `DATABENTO_API_KEY` as an environment variable there (never in a file of this
repository); a new session picks both up. Then run step 1 of the README Databento section.
