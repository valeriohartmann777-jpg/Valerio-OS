# APEX — signal rules (engine apex-1.4.0)

Code: `setups.py` (the three setups, binding docstring), `eligibility.py` (the gate),
`alerts.py` (the budget), `signal.py` (the contract). Tests: `tests/test_apex_setups.py`.

## The three setups (version 1.0.0 each, long and short)

| Setup | Trigger (closed 5-minute bar) | Entry | Stop |
|---|---|---|---|
| `sweep_reclaim` | a ranked zone confirmed before the sweep is breached by ≥ 0.1 ATR (≤ 1.0 ATR, deeper = break), then the first close back inside within 3 bars, in the upper (lower) half of its bar | buy (sell) stop one tick beyond the trigger bar | beyond the sweep's extreme by 0.15 ATR + 1 tick |
| `rejection_rotation` | the nearest ranked support (resistance), not WEAKENING, confirmed before the touch: touched, closed back out with a body in the trade's direction, wick ≥ 35 % of the range, close in the outer 40 %, not deeper than the stop buffer | as above | beyond min(bar low, zone low) by 0.15 ATR + 1 tick |
| `breakout_retest` | a zone BROKEN (two 15-minute closes beyond) in the last 24 h, score ≥ 30, confirmed before the break; acceptance = ≥ 1 ATR beyond since; retest within 0.15 ATR of the broken edge, held with a body in the break's direction | as above | beyond the broken zone by 0.15 ATR + 1 tick |

Targets: one tick before the next one or two ranked opposing zones — never an invented price.
No target, stop below the market's minimum or wider than 2.5 ATR, target 1 closer than 1 ATR, or
reward/risk to target 1 after costs below 1.5 → no plan, with the reason. Buy-side prices on
bid-only data carry the modelled spread. Dollar risk = points × point value (NQ 20, MNQ 2 USD per
point and contract — a 10-point stop is 200 vs 20 USD before fees).

A resistance being pressed is *watching a breakout* — a confirmed break makes it a continuation
case, never an automatic short.

## Stages and states

Stage: `SCANNING → NEAR_ZONE → WATCHING_SETUP → TRIGGER_PENDING → TRIGGER_CONFIRMED →
ENTRY_ARMED`; the order lifecycle (filled, no fill, expired, invalidated, outcome) is recorded
separately (`resolve.py`, `apex_outcomes`) and never written back into the plan.
What a person sees: `WATCH` (a touch alone is never a trade), `TRIGGER_PENDING`,
`RESEARCH_ONLY` (a plan, not qualified), `ENTRY_ARMED` (qualified paper signal), `NO_TRADE`,
`BLOCKED`.

## Eligibility gate — eleven independent checks

`APP_READY · DATA_FIT · SOURCE_CAPABILITY · ZONE_CONFIRMED · TRIGGER_CONFIRMED ·
EXECUTION_FEASIBLE · PROBABILITY · UNCERTAINTY_SAMPLE · NET_EV_STRESSED · MACRO_LIQUIDITY ·
UNIQUE_BUDGET`. All must pass; each says why it failed.

- `DATA_FIT`: quality gate passed, not stale, last bar ≤ 10 min old, mode DELAYED or LIVE —
  never SYNTHETIC, never REPLAY.
- `PROBABILITY`: a validated **and calibrated** P(TP before SL after the fill) ≥ 0.70 for this
  setup, version, market and source, from a head SENTINEL reviewed and the owner promoted. A path
  share (P(upper first)), a zone score or an LLM's conviction never counts.
- `UNCERTAINTY_SAMPLE`: effective sample ≥ 30 and lower bound ≥ 0.60 in the head's reliability
  tenth the plan falls in.
- `NET_EV_STRESSED`: expected R with costs × 1.5 > 0 (break-even probability shown).
- `MACRO_LIQUIDITY`: a calendar is loaded and no high-impact US release is within 30 min before
  / 15 min after; inside the permitted session. No calendar = event risk unknown = fail.
- `UNIQUE_BUDGET`: ≤ 4 alerts per market and New York trading day (18:00–18:00); same zone and
  direction within 240 min = duplicate; ranked by stressed expected R. Zero is a correct day.

High-conviction class: P ≥ 0.80 with lower bound ≥ 0.70.

## Contract

`Signal` (Pydantic, mirrored in `packages/protocol/src/apex.ts`, sync test) refuses: an order
plan for WATCH / NO_TRADE / BLOCKED, `eligible` without every check passed, a probability that
is unvalidated, uncalibrated, not promoted, below 0.70 or not P(TP before SL), a non-positive
stressed expected R, a replay / synthetic / historical mode, and `user_executable = true`.
A changed probability, gate verdict or zone set is a new signal id. Signals and alerts are
immutable rows.
