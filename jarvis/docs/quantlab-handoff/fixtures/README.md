# Synthetic engineering fixtures (not real market data)

**All fixtures are fabricated solely to test numerical correctness.** They must never be shown as historical or evaluated for a real trading edge.

- `golden_execution_case.csv`: 8 OHLCV minute bars.
- `golden_signals.csv`: explicit BUY and SELL signal timestamps, independent of MA logic.
- `golden_expected.json`: known exact fills and cash account results with $0.50 order fees and one share.
- `ma_crossover_case.csv`: separate 12-bar SMA(2)/SMA(3) fixture with two BUY signals, two SELL signals but last SELL cannot fill; expected final equity $92.50 on initial $100 with fee $0.50 per filled order.
- `invalid_ohlc_duplicate.csv`: intentionally invalid high and duplicate timestamps; importer must reject.
- `verify_fixture_math.py`: standalone stdlib assertion script that proves reference arithmetic of static golden numbers; **not** a full backtest validation.

## Signal timing
All input timestamps are UTC **bar-start** timestamps, NOT signal availability. For a 1-minute bar starting 09:33, OHLC becomes known at 09:34; a next-bar-open fill may be stamped 09:34. This assumes zero latency and can be optimistic: mark as a model assumption and stress separately. Fills must use bar index t+1 or later, never t. The toy bars do not encode a real exchange calendar.

## How to use
1. Unit-test execution engine against explicit signal fixture and expected ledger.
2. Unit-test MA signal generator against `ma_crossover_case.csv` independently; check entire sequence.
3. Unit-test importer rejects invalid fixture.
4. UI must show a conspicuous SYNTHETIC / TEST ONLY label.
