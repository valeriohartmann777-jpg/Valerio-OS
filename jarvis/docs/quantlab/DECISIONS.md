# QuantLab decisions (Institutional Edition)

Repo-wide decision: D-028 in `/DECISIONS.md`. Details here.

## QD-1 — Databento key only in the OS keystore, verified by metadata

- **Decision:**
  - The key is stored with `keyring` (macOS Keychain on the user's Mac).
  - Insecure backends (`fail`, `null`, `keyrings.alt`) are refused. There is no plaintext
    fallback.
  - The key is checked first with `metadata.list_datasets()` and stored only if that
    succeeds.
  - It is read per provider call and never cached in memory. Only a 4-character hint is
    stored.
  - A logging filter redacts `db-…` and `sk-ant-…` patterns everywhere.
  - Validation errors on the connect route never echo the key (`SecretStr`).
- **Consequences:**
  - On macOS the first access may show a Keychain prompt ("Always allow").
  - In this Linux container no keystore exists, so the UI says connecting is disabled.
    Tests use a labelled memory keystore.

## QD-2 — Cost-first acquisition with bound, single-use approvals

- **Decision:**
  - Every download comes from a quote:
    - cached days subtracted;
    - priced by `get_cost`, with size and record count;
    - stored with a signature, valid 15 minutes.
  - The approval names a maximum budget and must fit the per-request cap ($25 default) and
    the monthly cap ($100 default). Caps change only by explicit user action (audited).
  - The approval is re-priced against Databento right before use:
    - a change over 1 % voids it (SUPERSEDED);
    - a price above the approved maximum is refused.
  - It is claimed by compare-and-set (single use).
  - No tool of JARVIS or ULTRON can reach the approval route; the route requires
    `confirm: true` and the Origin guard.
- **Alternative:** Databento batch jobs. They make sense for large requests later; streaming
  per ≤31-day chunk gives progress and cancellation now.

## QD-3 — Raw + canonical cache by UTC day

- **Decision:**
  - Raw DBN is kept as delivered.
  - Canonical data is per-day Parquet with integer fixed-precision prices.
  - A day is never bought twice.
  - A dataset is an immutable snapshot plus manifest built only from cached days.
- **Reason:** Exact tick arithmetic, auditable provenance and incremental purchases.

## QD-4 — Futures engine on integer ticks, flat each session

- **Decision:**
  - A new engine with prices in ticks of the traded contract and money in `Decimal`.
  - Explicit orders and fills, each fill with `known_at`.
  - Positions are flat at every session end; sessions spanning two contracts are skipped.
- **Reason:** Correct multipliers (NQ vs. MNQ). It also rules out fake P&L from
  continuous-contract jumps without needing a roll-trade model, which comes later
  (overnight strategies).
- **Consequence:** Overnight and multi-day strategies are not supported yet.

## QD-5 — Contract specs from definition records

- **Decision:**
  - Tick size, tick value and multiplier come from Databento `definition` records for the
    exact instrument ids, point-in-time at the contract's first bar, and are cross-checked.
  - A small CME reference table is used only when no definition exists, labelled ASSUMED.
  - Mismatches with the strategy's product, or internally inconsistent definitions, are
    INVALID.

## QD-6 — Ambiguous 1-minute bars: conservative result plus an optimistic bound

- **Decision:**
  - Stop and target in the same bar: the stop is assumed, and the optimistic run is reported.
  - Both breakouts in one bar: the day is excluded and counted.
  - The AMBIGUITY test warns when the sign depends on it.
- **Reason:** OHLC bars don't contain intrabar order. Inventing it would inflate results.

## QD-7 — Verdicts by a fixed function; synthetic data capped

- **Decision:** See VALIDATION_PROTOCOL.md.
  - ROBUST_UNDER_TESTED_ASSUMPTIONS is unreachable until forward monitoring exists.
  - Fixture data never earns more than INSUFFICIENT_EVIDENCE, with `would_be` for
    transparency.

## QD-8 — JARVIS drafts, code decides

- **Decision:**
  - The Strategy Architect proposes a spec through one tool (`submit_strategy_spec`).
  - The strict parser validates it, and errors go back to the model (≤ 3 rounds).
  - Drafts are never saved or run automatically. Unknowns block runs.
  - AI revisions become new versions (origin `ai`) and so count as variants in the trial
    registry.
  - The model never computes results.

## QD-9 — Selection bias from the trial registry

- **Decision:**
  - Every evaluated variant (version × parameter set, in-sample and walk-forward training)
    is stored in `qr_trials`.
  - The deflated Sharpe uses all distinct variants of the strategy family.
  - Holdout looks are stored per family and date range; repeated looks are flagged.

## QD-10 — Parquet + pyarrow now, DuckDB later

- **Decision:** Per-dataset work reads Parquet with pyarrow and numpy.
- **Reason:** It is enough for one strategy on one dataset and adds no new runtime.
- **Trigger for DuckDB:** cross-dataset queries (portfolio, cross-instrument), or datasets
  larger than memory.

## QD-11 — An offline fixture provider for development and CI

- **Decision:**
  - `jarvis/quantlab/hub/fixture.py` mimics the SDK call surface, raises the real SDK
    exception types and writes real DBN files with synthetic prices.
  - It is enabled only by `JARVIS_QUANTLAB_PROVIDER=fixture` and is labelled in every UI
    surface, dataset manifest, report and verdict.
- **Reason:** The whole path (adapter → DBN decode → cache → engine → validation → UI) is
  exercised without a key or spending money, and without pretending to be Databento.
