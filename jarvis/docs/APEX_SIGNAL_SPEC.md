# JARVIS APEX — signal specification

Engine `apex-1.2.0`. Code: `backend/jarvis/apex/signal.py` (contract), `trigger.py`
(`zone_rejection_5m`), `candidates.py` (`zone_reaction`), `execution.py`, `resolve.py`,
`engine.py`. TypeScript mirror: `packages/protocol/src/apex.ts` (`ApexSignal`).

Research and paper only. No code path places, sends or simulates a broker order.

## 1. One decision → one immutable record

Every analysis (`POST /apex/analyze`, or a forward-log run) runs **both strategies on the same
point-in-time view** (bars closed by `as_of`, `Series.until`). Each strategy's decision becomes
one `Signal`, or one per order plan when a strategy made two (a long at S1 and a short at R1).
Signals are stored in `apex_signals` (migration 21). Database triggers refuse every `UPDATE` and
`DELETE`. What happened later is a separate outcome record (`apex_outcomes`), keyed by the
plan's `candidate_id`.

`signal_id` = `sig-` + SHA-256 of (symbol, data source + content hash, `as_of`, strategy,
version, status, direction, candidate id). The same moment on the same data gives the same id,
so it is recorded once.

## 2. Fields and null semantics

| Field | Type | Meaning / null |
|---|---|---|
| `signal_id` | str | content id (above) |
| `symbol` | `XAUUSD` \| `NQ` \| `MNQ` | |
| `market_data_source` | str | `source_id\|quote side\|sha256:<16>` — provider, dataset and the hash of exactly the bars used |
| `as_of_utc` | ISO-8601 | the decision moment |
| `last_closed_bar_utc` | ISO-8601 \| null | open time of the newest closed one-minute bar; null = no bars |
| `timeframe` | `5m` (zone_rejection_5m), `15m` (zone_reaction) | the decision timeframe; fills are judged on 1-minute bars |
| `strategy_id`, `strategy_version` | str | `zone_rejection_5m 1.0.0`, `zone_reaction 1.1.0` |
| `status` | `NO_TRADE` \| `WAITING_FOR_TRIGGER` \| `CANDIDATE` \| `PAPER_VALIDATED` \| `BLOCKED` | §3 |
| `direction` | `LONG` \| `SHORT` \| `NONE` | `NONE` whenever there is no plan |
| `market_structure` | `BULLISH` \| `BEARISH` \| `RANGE` \| `UNKNOWN` | the 60-minute trend from confirmed swings |
| `zones` | list | `{id, side, low, high, confirmed_at_utc, source}`; `source` = `key_levels:<kinds>`; `confirmed_at_utc` is null (zones are recomputed from the bars before `as_of`; their level sources are fixed then) |
| `entry_type` | `LIMIT` \| `STOP` \| `MARKET` \| `NONE` | `NONE` without a plan |
| `entry`, `stop_loss`, `take_profit_1` | float \| null | null without a plan; on the market's tick grid |
| `take_profit_2` | float \| null | null when only one opposing zone exists |
| `risk_reward_after_costs` | float \| null | (distance to TP1 − costs deducted) / distance to SL; null without a plan |
| `probability_estimate`, `probability_interval`, `calibration_sample_size` | null | **always null today.** APEX has no calibrated per-signal model. The contract refuses a probability without its interval and ≥ 30 calibration cases |
| `validation_state` | `UNVALIDATED` \| `RESEARCH_ONLY` \| `OOS_SUPPORTED` \| `FORWARD_SUPPORTED` \| `REJECTED` | from the validation protocol for **this strategy version** on this kind of data; never from the strategy itself |
| `invalidation_rule` | str | the plan's rules (fill, missed, same-bar, max hold, cancel) |
| `reasons` | list[str] | why this plan, or why no plan |
| `data_quality` | `PASS` \| `WARN` \| `FAIL` | quality gate OK / WARN / BLOCK |
| `execution_assumption` | str | `execution_assumption_limited` (bid-only gold), `futures_trade_prices`, `synthetic_flat_costs`, `data_unavailable` |
| `setup` | object \| null | `{setups: [{zone, direction, distance_atr}]}` — what was observed |
| `trigger` | object \| null | the confirmed event (bar, OHLC, rule); null = not triggered |
| `evidence_ids` | list[str] | the analysis id, and the validation run id when there is one |
| `candidate_id` | str \| null | the journaled order plan |
| `created_at_utc` | ISO-8601 | when it was recorded |

## 3. Invariants (refused at construction — `Signal._invariants`)

- `CANDIDATE` / `PAPER_VALIDATED`: direction and entry type set, entry, SL and TP1 present,
  geometry valid (long: SL < entry < TP1 < TP2; short mirrored), reward/risk after costs > 0,
  data quality not `FAIL`, and a `STOP`/`MARKET` entry has its confirmed `trigger`.
- `NO_TRADE` / `WAITING_FOR_TRIGGER` / `BLOCKED`: entry type `NONE` and no entry, SL, TP or R:R
  — **no executable-looking instruction without a plan**.
- `BLOCKED` needs its reasons.
- `PAPER_VALIDATED` needs `OOS_SUPPORTED` or `FORWARD_SUPPORTED`.
- A probability needs its interval, ≥ 30 calibration cases, and must lie inside the interval.
- Records are frozen (pydantic `frozen=True`) and immutable in the database.

### Status rules

| Status | When |
|---|---|
| `BLOCKED` | the quality gate blocked; or the data is stale; or the source is not tradable data for the symbol (CFD proxy for NQ/MNQ → `DATA_UNAVAILABLE: …`) |
| `NO_TRADE` | no zone in reach, or (zone_reaction) every plan was rejected with its reason |
| `WAITING_FOR_TRIGGER` | (zone_rejection_5m) a setup exists but the last closed 5-minute bar is no valid rejection, or its plan failed a rule |
| `CANDIDATE` | a plan passed every rule; the rule is not (yet) supported by validation |
| `PAPER_VALIDATED` | a plan passed and its strategy version is `OOS_SUPPORTED` / `FORWARD_SUPPORTED` on real data — still paper only |

## 4. Strategies

Parameters: `config/apex.yaml` (registered values below). Changing a rule or a number is a
new version and a new trial.

### `zone_rejection_5m` 1.0.0 — SETUP → TRIGGER → ORDER_PLAN

- **SETUP (long):** the nearest support zone S1 is within `reach_atr` (3.0) × ATR(14, 15 min)
  below the price, or the price is inside it. Short mirrors at R1.
- **TRIGGER (long):** the last 5-minute bar that had closed by `as_of`, closed at most one bar
  ago, traded into S1 (low ≤ S1 top), closed back above it (close > S1 top) with a bullish body
  (close > open), and held (low ≥ S1 bottom − `stop_buffer_atr` × ATR). No such bar →
  `WAITING_FOR_TRIGGER`.
- **ORDER_PLAN (long):** buy **stop** one tick above the trigger bar's high; SL below
  min(trigger low, S1 bottom) by `stop_buffer_atr` (0.15) × ATR; TP1/TP2 one tick before the
  next two resistance zones; valid `trigger_valid_minutes` (15); held at most
  `max_hold_minutes` (480). Rejected when the stop is below `min_stop_points` or wider than
  `max_stop_atr` (2.5) × ATR, no target zone exists, or R:R to TP1 after costs < `min_rr` (1.5).

### `zone_reaction` 1.1.0 — limit at the zone

- Long at support: price above S1 within `reach_atr` ATR → buy **limit** at the zone's top;
  SL below the zone's bottom by `stop_buffer_atr` ATR; targets as above; valid
  `valid_minutes` (240). Price inside the zone → no entry. Short mirrors.
- 1.1.0 (vs 1.0.0): costs come from the execution model (§5); everything else unchanged.

## 5. Execution model (`execution.py`)

Plan prices are broker order prices: **buys execute on the ask, sells on the bid.**

| Data | Model | Label |
|---|---|---|
| Dukascopy gold (bid candles) | ask = bid + spread; spread = session median measured from Dukascopy ask candles of the last 10 days, else the configured **estimate** `spread_points` (0.30); remaining costs = `cost_points` − spread (≥ 0) | `execution_assumption_limited` |
| Data Vault NQ/MNQ (traded futures prices) | `cost_points` (1.0) deducted flat per round trip | `futures_trade_prices` |
| Dukascopy USA Tech 100 for NQ/MNQ | **no plan, no backtest** | `data_unavailable` |
| Generated test data | `cost_points` flat | `synthetic_flat_costs` |

A journaled plan keeps the execution model it was made with; its outcome is judged with it.

## 6. Resolution (`resolve.py`) — conservative

- Only bars that opened after the decision; nothing in the deciding bar.
- Limit: fills at its price, never better. Stop entry: at its price, or at the bar's open
  when the bar opened beyond it (gap). Market (baseline only): the next bar's open.
- Before the fill: TP1 traded first → `missed`; a stop entry whose price closed beyond the SL
  first → `expired` (cancelled); validity over → `expired`.
- In the fill bar only the SL can be hit. After it, a bar touching SL and TP → **loss**. A bar
  opening beyond the SL exits at that open.
- Short SL/TP trigger on the ask (bid + spread) on bid data — the side bid-only data hides.
- R = (exit − fill) / planned risk − costs / risk; `timeout` closes at the close after the
  maximum hold.

## 7. Where it shows

APEX → Chart & analysis → **Analyst** (per strategy: data ready/blocked, structure, setup,
trigger, entry/SL/TP, R:R after costs, evidence, why this setup, why not trade); APEX →
**Signal journal**; `GET /apex/signals`.
