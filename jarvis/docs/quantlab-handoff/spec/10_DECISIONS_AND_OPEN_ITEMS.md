# 10 — Decisions, Constraints, Open Inputs

## Fixed product/engineering decisions
- QuantLab lives *within* JARVIS; not a separate independent product by default.
- UI appearance: clean, premium graphite/black, restrained teal accent, minimal visual noise.
- Structured StrategySpec is canonical; LLM never directly determines accounting outcomes.
- Local-first; CSV/Parquet import first; no automatic paid market-data integration R1.
- Small deterministic reference engine **before** vectorized sweep adapters. This narrows the conceptual slides without contradicting the core product vision.
- First execution semantics: next-bar open, fixed shares, cash-equity, long only; explicit fee/slippage model.
- Audit every experiment, including failures and optimized variants.
- Chronological splits, protect holdout, never label a promising train equity curve as a proven edge.
- No brokerage execution, no uncontrolled codegen, no undisclosed provider data uploads.

## Open real-world inputs (not blockers for creating MVP)
- Real existing JARVIS repo path, framework and current code state — Claude must inspect.
- First actual market/instrument and granularity — select later with user after baseline is technically verified. Prior illustrations use NQ intraday but this is *not* support commitment.
- Real historical market-data dataset, its provenance and license — required before evaluating a genuine trading hypothesis.
- Whether/which paid provider is licensed (e.g. Databento) — not automatically install/use.
- Agent provider API credentials — optional; keep out of baseline dependencies.
- Product-scale goals, private-vs-cloud data requirements and eventual multi-user access — later design.

## Decisions Claude may make autonomously
- Reuse existing JARVIS routing/component library; naming and folders in repo.
- Choice of existing DB migration, testing, frontend chart library.
- Conservative numerical tolerance documented and asserted.
- Import implementation that preserves provenance and block-on-errors behavior.

## Decisions Claude must NOT silently make
- Invent a trading timeframe, stop, target, instrument, tick value or market-data quality.
- Treat NQ futures as ordinary equity accounting.
- Claim the provided fixtures are real market data.
- Run arbitrary AI-generated code on the host.
- Connect a brokerage or send real orders.
- Overwrite an existing CLAUDE.md, credentials, unrelated code, or source data.

## Reference and methodology links
- Claude project memory: https://code.claude.com/docs/en/memory
- VectorBT `Portfolio.from_signals` docs: https://vectorbt.dev/api/portfolio/base/
- DuckDB Parquet: https://duckdb.org/docs/current/guides/file_formats/query_parquet
- Backtest overfitting: https://escholarship.org/uc/item/4w1110bb
- Historical data schema (future candidate): https://databento.com/docs/schemas-and-data-formats/whats-a-schema
