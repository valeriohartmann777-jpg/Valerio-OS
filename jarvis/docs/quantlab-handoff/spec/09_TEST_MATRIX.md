# 09 — Testmatrix: Quantitative Correctness First

## Hard tests — must pass before claim of R1
| ID | Test | Expected behavior |
|---|---|---|
| Q-001 | Synthetic fixture: BUY signal from bar t, next-bar open fill t+1 | No same-bar fill; `fill bar index > signal bar index`; fill event not before bar close; exact price |
| Q-002 | SELL next open | Close at correct later open |
| Q-003 | Fixed per-order fee 0.50 USD each side; 1-share 103 ->104 | Gross +1.00, total fees 1.00, net 0, cash 10,000.00 |
| Q-004 | Signal on last bar | No nonexistent next-bar execution |
| Q-005 | No cash for buy | No negative cash/no implicit leverage |
| Q-006 | Moving-average crossover computed on closed bars | No future prices in signal |
| Q-007 | Shift last future prices dramatically | Preceding decisions unchanged |
| Q-008 | Duplicate timestamp | Data reject with explicit reason |
| Q-009 | OHLC invalid low>high or prices <=0 | Data reject |
| Q-010 | Missing times and unknown timezone | Signal user limitation / require proper mapping. Bar-start vs bar-end semantics explicit; model fill is not earlier than signal availability |
| Q-011 | Identical spec/data/engine config | Same numeric trade ledger and equity |
| Q-012 | Changing any execution-critical input | Different experiment manifest hash |
| Q-013 | Immutable snapshot & version | Old run remains tied to original hashes |
| Q-014 | Chronological OOS split | train max time < OOS min time; no random shuffle |
| Q-015 | Optimization attempts on final holdout | Forbidden by orchestration contract |
| Q-016 | Synthetic fixture displays in UI | `SYNTHETIC / TEST ONLY`, never certified market result |
| Q-017 | NQ futures spec under R1 cash-equity engine | `UNSUPPORTED_INSTRUMENT`, not false USD-per-share accounting |
| Q-018 | Stop+target both inside one OHLC bar (future feature) | `AMBIGUOUS_FILL` or feature explicitly unsupported; no positive fill assumption |
| Q-019 | Strategies with invalid/missing fields | 4xx validation and blocked run |
| Q-020 | UI receives errors/status events | Coherent visible state and result reconciliation |
| Q-021 | Repo integration smoke test | Existing JARVIS command center not broken |
| Q-022 | No live trading or model-provided Python execution | No broker/execution endpoint present |

## Numerically independent fixture
`fixtures/golden_execution_case.csv` and `fixtures/golden_signals.csv`, plus `fixtures/golden_expected.json`. This is **an order accounting fixture**: signals are explicit and separate from the MA-strategy test.

## MA-strategy fixture
`fixtures/ma_crossover_case.csv` (separate from execution signals). Test must derive signal locations algorithmically and independently calculate expected outcomes (not using the same engine under test). A reference implementation can assert known signal/position transitions based on closed-bar SMAs.

## Additional tests
- Zero trades -> metrics `not available` where denominators zero, not NaN/Infinity or arbitrary zero Sharpe.
- One trade -> significance/ratio warnings.
- Zero slippage and nonzero slippage directional sign is right for buys and sells.
- Open position at sample end -> distinguish realized and mark-to-market, no invented exit.
- Failed import never produces a completed experiment.
- File path traversal attempts blocked.
- UI never displays stale results from prior strategy after a new run fails.
- Review timestamp parsing over DST and holiday sessions before intraday futures release.

## Test automation
Use backend pytest and frontend's existing runner. Tests should be runnable by README commands that Claude verifies in the environment. No external paid API needed for MVP automated tests.
