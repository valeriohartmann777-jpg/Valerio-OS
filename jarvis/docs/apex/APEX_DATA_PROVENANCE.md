# APEX — data provenance and capability

Every analysis carries a **source capability record** (`backend/jarvis/apex/capability.py`) and
a **factor matrix**: for each analytic factor one of `CONFIRMED · CONTRADICTED · UNAVAILABLE ·
PROXY · STALE · NOT_APPLICABLE`. The absence of a capability is never a passing factor.

## Sources and what they can measure

| Source | Market type | Bid | Ask | Traded volume | Aggressor | Depth | Live entitlement |
|---|---|---|---|---|---|---|---|
| `dukascopy:XAUUSD` (free history) | spot quote (OTC) | yes | only if ask candles were measured | no (tick counts) | no | no | no |
| `dukascopy:NQ` (USA Tech 100 CFD) | spot quote — **not** CME NQ/MNQ | yes | as above | no | no | no | no |
| `vault:NQ`, `vault:MNQ` (Databento / owner imports) | CME futures OHLCV | — | — | yes (per bar) | no | no | no |
| `fixture` (generated) | generated | — | — | — | — | — | — (always labelled SYNTHETIC) |

Consequences (enforced in code and tests):

- **Bid-only gold:** buy-side prices carry the modelled spread; the eligibility gate refuses
  `SOURCE_CAPABILITY` unless the ask was measured: *ASK REQUIRED FOR REALISTIC EXECUTION*.
- **CFD proxy for NQ/MNQ:** `DATA_UNAVAILABLE` — structure and zones only, no order plan, no
  backtest (`execution.NOT_TRADABLE`).
- **Volume profile / POC** is always `PROXY` (bar or quote volume, not trades) and is shown,
  never scored.
- **VWAP:** `CONFIRMED` (bar-level approximation of traded volume) on venue futures bars, reset
  17:00 America/Chicago; `PROXY` (quote-activity weighted) on spot quotes; `UNAVAILABLE` without
  volume (`orderflow.session_vwap`).
- **Delta / CVD** only from trade prints with an aggressor flag or trades + quotes (TBBO), per
  trade method and confidence (`orderflow.classify`). No source in APEX has trades today →
  `DELTA UNAVAILABLE`. Bars never give delta.
- **Absorption:** always `ABSORPTION NOT MEASURABLE` (needs MBP-10 / MBO depth with trades).
- **Options, macro, cross-market:** `UNAVAILABLE` unless a licensed source is connected; a macro
  calendar can be imported by the owner (CSV, schedule vintage, DST-correct, values visible only
  after their release time — `macro.py`).
- **Mode:** never `LIVE` (no source has a live entitlement); a current download is at best
  `DELAYED` (≤ 30 min behind), a past moment is `REPLAY`, generated data `SYNTHETIC`.

## Local first, no surprise costs

Market data comes from the local caches and the QuantLab Data Vault. Paid acquisitions
(Databento) need a free quote and the owner's explicit approval each time; a re-run of a stored
history never calls a provider (`test_quantlab_vault.py::test_17`, `test_01`). The default
spending limit is zero.

## Provenance on every record

Analyses store the source id, content SHA-256, quote side, execution assumption and
capability; signals carry `market_data_source = provider|quotes|sha256`; zone snapshots, alerts,
probability heads and promotion events are immutable rows (database triggers refuse UPDATE and
DELETE).

## In this session

The cloud container could not reach any market-data host (Dukascopy, Databento, CME, Yahoo,
Stooq all refused) and no data had been imported, so every run used the generated `fixture`
source — labelled SYNTHETIC everywhere.
