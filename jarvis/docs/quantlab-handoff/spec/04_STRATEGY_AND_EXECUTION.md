# 04 — StrategySpec und Execution Semantics

## Primary contract
`contracts/strategy_spec.schema.json` definiert unterstützte Felder. JSON-Objekt wird normalisiert, validiert, versioniert und mit SHA-256 gehasht. Keine LLM-Ausgabe direkt ausführen; sie ist zunächst `DRAFT`, erst eine bestätigte formal gültige StrategySpec darf in die Engine.

## MVP instrument scope: Cash Equity, long-only
R1 Referenz-Engine handelt genau **ein** Instrument, long-only, keine Hebelwirkung, keine Shorts, keine Fremdwährungsumrechnung, keine Corporate-Action-Behandlung *sofern keine adjusted Historie mit expliziter Dokumentation*. Symbol/Market/Tick Size/Währung müssen korrekt gesetzt sein. Futures, Optionen, Tick-Kontraktwerte und Margin werden später separat entworfen. Ein NQ-Futures-StrategySpec muss in R1 **UNSUPPORTED_INSTRUMENT** ausgeben, statt Aktien-Accounting fälschlich anzuwenden.

## MVP Signal: MA-Crossover
- Fast MA and Slow MA: simple moving average of **closed bar** prices.
- Entry: if `fast_{t-1} <= slow_{t-1}` and `fast_t > slow_t`, and flat, generate `BUY` signal at bar t close; execute at bar t+1 *open* if tradable.
- Exit: if `fast_{t-1} >= slow_{t-1}` and `fast_t < slow_t`, and long, generate `SELL` at bar t close; execute at bar t+1 open.
- Neither signals nor fills exist until minimum required lookback bars are available. Use strict chronological bars, never reindex future data into past.
- Supported timestamps for R1: UTC bar-start stamps, explicitly known interval. For each bar, `available_at_utc = bar_start_utc + interval` (or actual venue calendar boundary where appropriate). Bar close-derived signals cannot execute before that moment. The idealized next-bar-open assumption is zero-latency; user-facing reports must disclose it.
- Contract initially one buy/sell quantity `size_units` fixed in shares; require enough cash for price*size+fees; no partial fills in R1.
- Same-bar reversal not supported; no overlapping multiple open positions. Missing future bar means end-of-data cannot fill next-bar signal. Clear terminal valuation vs realized P&L.

## Executions
- Fill price: future bar's `open`, plus slippage per direction; BUY increases by slippage; SELL decreases. V1 slippage is deterministic basis points (or fixed value if implemented with explicit unit) not historical measured spreads. Label as modeled assumption.
- Fees: fixed per order (quote currency) + variable per unit or notional, explicitly declared. Fixture: 0.50 USD per side, zero slippage.
- Cash initial: numeric > 0; BUY: `cash -= qty*fill_price + fee`; SELL: `cash += qty*fill_price - fee`.
- Equity per bar: cash + mark_to_market_shares*valid_price. At last bar with open position, clearly report unrealized PnL; do not forcibly sell without specified closeout policy.
- Ledger: 1 record per executed order with signal_time, fill_time, side, qty, fill_price, fee, cash_after, position_after; 1 trade record per round trip with gross/net pnl and fees. UTC timestamp internal; local session conversion display only.
- Enforce `low <= min(open, close) <= max(open, close) <= high`, prices > 0, finite and strictly ordered time.

## Strong invariants
1. Fill bar index > signal bar index. The timestamps encode bar **start**; signal information is available only at end-of-bar (`bar_start + duration`). Next-bar open may equal the previous bar close at the boundary; this assumes zero latency and is an optimistic research approximation. Test `fill_event_time >= signal_available_at`, never same-bar fills. Stress realistic latency later.
2. Cash-flow audit: cash_after = cash_before -/+ notional - fee; total equity = cash + quantity*mark.
3. Orders cannot execute if market data missing/invalid/asset market closed.
4. Open and close may differ from signal bar; no accidental same-bar fill defaults.
5. MA windows never consume future rows. Any shift leak must fail a golden test.
6. No extrapolated or forward-filled future OHLCV observations.
7. Every completed trade has matched entry/exit order references.

## Important beyond R1
- **OHLC ambiguity:** if later add stops/targets and both touched in the same bar, do NOT assume profit. Return `AMBIGUOUS_FILL`; optionally conservative worst-case scenario with transparent label; recommend quote/trade data. No stop execution system is promised in R1.
- **Intraday sessions:** venue calendars, DST, holidays, early closes, timezone conversion and trading halts, not naive `datetime.hour == 9`.
- **Futures:** separate engine accounting for multiplier/tick value, expiry, rolling, settlement, fees, gaps, margin; distinct QA acceptance tests.
- **Corporate actions:** adjusted-price series need matching volume/price and cash handling; survivor universe and dividends not ignored for multi-equity tests.

## Demonstration reference case
Fixtures in `fixtures/golden_execution_case.csv` use an explicitly given entry/exit signal series (as an isolated **execution engine** test independent of MA). Initial cash 10,000 USD, quantity 1, BUY signal from the bar starting 09:33 UTC (available only at its 09:34 close), BUY at the next bar open 103 at 09:34; SELL signal from the bar starting 09:36 (available at 09:37), SELL next open 104 at 09:37; 0.50 USD fee per order; zero slippage.
Expected: gross +1.00 USD, total fees 1.00 USD, net 0.00 USD, final cash/equity 10,000.00 USD; BUY cash 9,896.50; SELL cash 10,000.00. The MA test should use its own generated signals and independent expected results, NOT retrofit this execution fixture as a MA proof.
