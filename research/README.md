# research/ — Strategy lab: Auction Market Theory, ICT/SMC, Price Action

A skeptical research lab that tries to find out whether three trading philosophies
contain repeatable, cost-surviving market behaviour on CME index futures. It is
built to falsify ideas, not to produce attractive backtests.

**Current status (2026-10-07): BLOCKED on data.** The framework and its tests are in
place; no real market data could be obtained from inside the environment (see
[reports/DATA_ACQUISITION_LOG.md](reports/DATA_ACQUISITION_LOG.md)). No performance
result exists anywhere in this directory, and none will be produced from synthetic data.

All 52 event studies (A001–A015, B001–B018, C001–C018, C009vC001) are implemented in
`edgelab/studies/`, their parameters are frozen in `configs/event_studies.yaml`, and each
is pre-registered with its expected outcome in
[journal/RESEARCH_JOURNAL.md](journal/RESEARCH_JOURNAL.md), all before any data. The first
real run is one command (below) and cannot be tuned to what it shows.

## Research chain

theory → mechanism → hypothesis → event study → minimum viable strategy → trade
distribution → development (V0 + at most 3 refinements) → ablation → validation →
rule freeze → one final out-of-sample test → robustness → practical system.

The order is enforced by the code where it can be: the journal refuses results for
experiments without a pre-registered expectation, the split ledger freezes the
DEV/VAL/TEST dates the first time data is registered, and the test-set guard lets a
frozen candidate see the final test period exactly once.

## Layout

| path | content |
|---|---|
| `configs/` | instruments (tick size/value), costs (LOW/BASE/STRESS), sessions, protocol, dataset manifest |
| `edgelab/` | the library (below) |
| `scripts/` | command-line entry points |
| `tests/` | unit, property and lookahead tests (synthetic data, software checks only) |
| `theory/` | THEORY_AUCTION.md, THEORY_ICT.md, THEORY_PRICE_ACTION.md |
| `hypotheses/` | ranked, pre-registered hypotheses per family |
| `journal/` | RESEARCH_JOURNAL.md: expectation before every experiment, result after |
| `event_studies/`, `candidate_tests/`, `validation/`, `robustness/` | outputs per research phase |
| `reports/` | data quality, acquisition log, final reports |
| `results/` | `experiments.parquet` registry and one directory per experiment (`auction/A001/`, `ict/B001/`, `price_action/C001/`) |
| `data/raw`, `data/processed` | market data (never committed) |

`edgelab` modules:

* `config`, `sessions`, `timing`, `resample` — YAML specs, CME trading dates, DST-safe
  session labels, scheduled availability times, the single as-of join, causal resampling
* `data/` — vendor loader (explicit timezone and open/close label), manifest, quality report, roll calendar
* `features/` — volatility, candles, VWAP, volume/TPO profile (POC, value area, HVN/LVN,
  shapes), swings with confirmation delay, structure (BOS/CHoCH), FVG, session levels,
  opening range, equal highs/lows, S/R zones and touches, regimes
* `events/` — event studies against time-matched controls, first-passage analysis
* `studies/` — the pre-registered event studies: causal feature context, detectors
  (sweeps, zone entries, breakouts, retests, runs, band fades), controls (time-matched,
  momentum / strength twins, shifted reference, displaced pivot-zone set), date-cluster
  bootstrap tests, one function per study ID, and the runner that applies gates G1–G6
  with Benjamini–Hochberg over the whole test registry
* `execution/` — bar-based execution engine (market/limit/stop, gaps, stop-first
  ambiguity, flat time, session end), cost model, position sizing
* `metrics`, `stats/`, `validation/` — full metric set, bootstrap, Monte Carlo, risk of
  ruin, BH-FDR test registry, randomisation tests, splits, walk-forward, perturbations
* `experiments`, `journal`, `plotting` — registry, pre-registration, trade-audit charts

## Conventions that prevent lookahead

* Bars are indexed by their open time in America/New_York; a feature on a row is known
  at that bar's close; the earliest fill is the next bar's open.
* Session summaries (prior-day high, value area, overnight range) carry an
  `available_at` equal to the SCHEDULED end of the session, never the last bar present.
* Higher-timeframe values reach a lower timeframe only through `timing.attach_asof`,
  keyed on the decision time.
* Swing pivots are known `right` bars after the pivot bar; FVGs after the third candle.
* `tests/test_lookahead.py` checks every feature, level, detector and the engine for
  truncation and future-perturbation invariance, and checks that the harness catches
  five deliberately leaky features.

## Setup and tests

```bash
cd research
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m pytest            # about a minute
```

## Adding data

1. Put the file in `data/raw/` (CSV, txt, parquet; any vendor).
2. `python scripts/prepare_data.py --inspect data/raw/<file>` and decide from the output
   which timezone the timestamps are in and whether they mark bar open or close.
3. Register it in `configs/datasets.yaml`.
4. `python scripts/prepare_data.py` then `python scripts/data_quality.py`, and read
   `reports/DATA_QUALITY_REPORT.md` before any research step.
5. Record how its findings were handled:
   `python scripts/journal_note.py --title "DATA_QUALITY <id>" --text-file <notes>`.
6. `python scripts/run_event_studies.py --dataset <id>` runs every pre-registered event
   study on the DEV period only (the split is frozen on this first run) and writes
   `event_studies/<id>/SUMMARY.md`, the test registry, journal results and rejections.

Preferred: ES and NQ 1-minute OHLCV, 2015 or later, with a contract column or a
documented roll method. BTCUSDT perpetual data is accepted as PROXY evidence only and
is labelled as such in every output.
