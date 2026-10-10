# QuantLab — product spec (Institutional Edition, as built)

Source directive: `INSTITUTIONAL_DIRECTIVE.md` (owner's brief, 2026-10-10). This file says
what QuantLab *is* today; `IMPLEMENTATION_STATUS.md` says what is verified.

## Purpose

An AI-native research terminal inside JARVIS for intraday futures hypotheses
(NQ, MNQ, ES, MES). It falsifies ideas on real, licensed market data and says honestly
what the evidence supports. It is not a backtest leaderboard and never trades.

Workflow: **idea → StrategySpec → data intelligence → simulation → validation →
evidence → report**.

## User and modes

The primary user is an independent trader-researcher who doesn't program.

- **Simple** (default): Overview, Strategy Studio, Data Hub, Backtest Lab, Reports.
- **Research**: adds Validation, Trade Explorer, Experiments and the R1 equity lab.
- **Institutional**: adds Risk & Execution, the audit trail, machine-readable specs and raw
  test metrics.

All three modes use the same engine and the same numbers.

## The ten areas

| Area | What the user does there |
|---|---|
| Overview | Sees the research state, provider status, cached data, strategies, recent runs and active jobs. Can ask JARVIS. |
| Strategy Studio | Describes an idea → JARVIS drafts rules (tool call, validated). Alternatively starts from a template. Edits the rule cards, sees every assumption (assumed / unknown / confirmed) and the plain-language rules, then saves a version and runs it. |
| Data Hub | Connects Databento (masked key, Connect & Verify, Test, Replace, Disconnect). Browses the catalog and contracts. Gets a cost estimate, approves it explicitly, follows the download, sees the cache, and builds verified datasets with a quality report and capability matrix. Also imports CSV/Parquet files for R1. |
| Backtest Lab | Session candles with entries and exits, equity and drawdown, key statistics, costs, segments and trades. |
| Validation | Verdict, evidence matrix (each test with status, assumptions, needs and reading), charts per test, the chronological split, and the sealed holdout (evaluated once, explicitly). |
| Trade Explorer | Every trade: 1-minute bars around it, range/stop/target lines, fills with "known at" times, costs, MAE/MFE, an ambiguity flag and a plain-language story. |
| Experiments | Strategy families, the version tree (you / JARVIS), runs, the trial registry, holdout looks, run comparison with rule differences, and a journal of notes and decisions. |
| Risk & Execution | Notional, leverage, margin check, execution assumptions, ambiguity diagnostics, session statuses, contract specs with provenance, and the ledger audit. |
| Reports | Markdown report per run (verdict, rules, data, results by segment, gates, assumptions, limitations, hashes). Download and reproduce. |
| Equity lab (R1) | The earlier cash-equity reference lab, unchanged. |

## Hard rules

- No broker connection and no order path. Only deterministic code computes results.
- No paid data without a quote and the user's explicit approval:
  - the approval is bound to the exact request, capped, single-use and expiring;
  - it is re-priced right before use.
- The Databento key lives only in the OS keystore (macOS Keychain). It never appears in
  files, SQLite, logs, events, API responses or prompts.
- Synthetic or fixture data is labelled everywhere and can never earn a verdict above
  INSUFFICIENT_EVIDENCE.
- Verdicts:
  - INVALID_DATA_OR_METHOD
  - INSUFFICIENT_EVIDENCE
  - REJECTED_HYPOTHESIS
  - PROMISING_RESEARCH_CANDIDATE
  - FORWARD_VALIDATION_REQUIRED
  - ROBUST_UNDER_TESTED_ASSUMPTIONS (reserved until forward monitoring exists)

  No "guaranteed", no aggregate green score.
