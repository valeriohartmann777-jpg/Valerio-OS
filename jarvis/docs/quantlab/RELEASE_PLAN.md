# QuantLab release plan (Institutional Edition)

Status markers:
- **done** — implemented and tested here.
- **partial** — part is built, the rest is listed.
- **planned** — not built.

"Tested here" means automated tests plus the E2E with the offline fixture provider. Nothing
has run against the real Databento API yet, because no key exists in this environment.

| Release | Content | Status |
|---|---|---|
| R0 | Repo recon, contracts, ADRs, QuantLab shell with modes and nav, the R1 equity lab preserved | **done** |
| R1 | Secure Databento key UI → OS keystore → metadata-only verification → catalog (datasets, schemas, range, conditions), contract resolution, cost quotes, bound approvals, spend caps, audit | **done** (fixture-tested; real-key check pending) |
| R2 | Approved download → raw DBN + canonical Parquet cache by day → immutable dataset with manifest → quality report + capability matrix → preview chart; CSV/Parquet import (R1 path) | **done** (fixture-tested) |
| R3 | Futures engine (integer ticks, stop/limit/market, conservative/optimistic, flat per session, roll guard), contract master from definitions, ledger, audit, metrics, Backtest Lab, Trade Explorer | **done** |
| R4 | Strategy Studio: JARVIS drafts a FuturesSpec through one validated tool call; rule cards, assumptions, versions (user / AI), templates | **done** (scripted model in tests; real-model run pending) |
| R5 | Validation V1: split + embargo + sealed holdout, OOS, walk-forward, cost stress, parameter sensitivity, bootstrap, regimes, subperiods, tails, ambiguity, deflated Sharpe over the trial registry; verdict; Experiments; Reports; reproduce | **done** |
| R6 | Institutional robustness: independent second engine; cross-instrument runs (ES ↔ NQ); capacity/liquidity limits from volume; PBO (CSCV); margin model; roll trades for overnight strategies | **planned** |
| R7 | Microstructure: trades / MBP-1 ingestion, spread-aware fills, 1-second resolution for ambiguous minutes (re-buy only those days); MBO replay where licensed | **planned** |
| R8 | Forward paper monitoring (live vs. simulation drift), research journal intelligence, ULTRON workers (CIPHER, VECTOR) on QuantLab missions, DuckDB for cross-dataset queries | **planned** |

## Next three, in order

1. **Real-key check on the MacBook.**
   - Connect (Keychain), open the catalog, then quote one month of `NQ.v.0` `ohlcv-1m` with
     definitions. The estimate should be well under $1.
   - Approve, download, build the dataset, run the ORB template once.
   - Compare the fixture-derived assumptions with the real SDK responses: the
     `get_dataset_range` shape, condition values, resolve output and error cases.
2. **Resolve ambiguity with data, not assumptions.** For days with ambiguous minutes, quote
   and fetch `ohlcv-1s` for just those minutes and re-run them.
3. **Second engine** for the audit (R6). It recomputes fills from the bars with a minimal,
   independently written implementation, and the gate compares ledgers trade by trade.
