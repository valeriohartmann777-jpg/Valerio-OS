# research/ — Strategy lab: Auction Market Theory, ICT/SMC, Price Action

A skeptical research lab that tries to find out whether three trading philosophies
contain repeatable, cost-surviving market behaviour on CME index futures. It is
built to falsify ideas, not to produce attractive backtests.

**Current status (2026-10-09): BLOCKED on data.** The framework, its tests and the
Databento data pipeline (below) are in place. No real market data has been downloaded:
the environment cannot reach Databento and no API key is set (see
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
* `data/` — vendor loader (explicit timezone and open/close label), manifest, quality report, roll calendar;
  Databento client, instrument definitions and active contract, trade validation, bar and profile build
* `features/` — volatility, candles, VWAP, volume/TPO profile (POC, value area, HVN/LVN,
  shapes), exact volume at price from trades, swings with confirmation delay, structure
  (BOS/CHoCH), FVG, session levels, opening range, equal highs/lows, S/R zones and
  touches, regimes
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

## Databento: ES and NQ from trades

Real CME data comes from Databento (`GLBX.MDP3`, schema `trades`, parent symbols
`ES.FUT` and `NQ.FUT`); see [docs/DATA_INTEGRATION_PLAN.md](docs/DATA_INTEGRATION_PLAN.md)
and [docs/FUTURES_ROLL_METHOD.md](docs/FUTURES_ROLL_METHOD.md). Nothing in this path
changes a study, a parameter or the frozen roll rule.

The API key is read from the environment variable `DATABENTO_API_KEY` and nowhere else;
it is never written to a file, log or report. The network must allow `hist.databento.com`.

```bash
export DATABENTO_API_KEY="..."        # macOS / Linux
$env:DATABENTO_API_KEY="..."          # Windows PowerShell
```

1. `python scripts/databento_cost.py --start 2024-01-01 --end 2024-04-01 --symbols NQ.FUT ES.FUT`
   estimates cost and size (free, downloads nothing).
2. `python scripts/databento_download.py --pilot` downloads trades and instrument
   definitions per calendar month to `data/raw/databento/<ES|NQ>/`, with a metadata JSON
   per file; a file that exists is never bought or overwritten again. Above the safety
   limit (`configs/databento.yaml`, $20.00) it stops unless `--confirm-cost <amount>` is
   given; `--dry-run` shows the plan and the cost only.
3. `python scripts/validate_market_data.py --pilot` checks every trade and writes
   `reports/TRADE_DATA_QUALITY_REPORT.md`. Nothing is dropped silently: every excluded
   record is counted with its reason. A fatal finding stops the pipeline here.
4. `python scripts/build_market_data.py --pilot` builds 1-minute bars of the active
   contract (frozen calendar roll, unadjusted), exact volume at price, POC/VAH/VAL,
   developing levels and VWAP per minute, registers the datasets `ES_DB_PILOT` and
   `NQ_DB_PILOT` in `configs/datasets.yaml` and prepares them.
5. `python scripts/databento_debug_charts.py --id ES_DB_PILOT` draws sessions chosen by
   rule (roll, DST change, highest volume, seeded random, overnight).
6. After the `DATA_QUALITY <id>` journal note,
   `python scripts/pipeline_check.py --dataset ES_DB_PILOT` runs the 52 studies once over the pilot in a sandbox and reports structure only (no
   effect sizes, p-values or decisions). The research runner refuses pilot datasets, so
   the pilot can never freeze the split or count as the first run.
7. `python scripts/databento_cost.py --table` estimates 1, 3 and 5 years, since 2020 and
   the full history. The full download starts only after a period has been chosen.

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
