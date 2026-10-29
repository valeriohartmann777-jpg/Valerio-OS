# APEX — product specification (as built)

APEX is JARVIS's research and **paper-only** trading workstation for **XAUUSD** (spot gold),
**NQ** and **MNQ** (CME Nasdaq-100 futures). It shows, for a moment in time, the strongest
supports and resistances, the buy and sell zone, possible paths, three rule-based setups and —
only if every condition is proven — a qualified paper signal. It never places an order
(`user_executable` is always false) and never invents data, order flow or probabilities.

## Where it lives

JARVIS → **QuantLab** → first level **APEX / Backtest Lab / Strategy Studio / Data Vault /
Research & Validation** (`apps/desktop/src/pages/QuantLab.tsx`; every former section is kept on
the second level, deep links `#/quantlab/<section>` still work).

## The workstation (APEX)

| Area | What it shows | Code |
|---|---|---|
| Toolbar | Market (XAUUSD / NQ / MNQ), timeframe 1·2·3·5·10·15 min, 1 h, 4 h, D1 (New York trading days), Start analysis, data chip | `Apex.tsx` |
| Checkpoints | Eight independent chips: App, Data, Zones, Features, Strategy, Probability, Forward, Signal — never merged into one "online" | `ApexDesk.tsx · CheckpointStrip`, `apexView.ts · checkpoints` |
| Mode line | LIVE / DELAYED / HISTORICAL / REPLAY / SYNTHETIC, as-of, last-bar age, source and schema, zone build time, `ASK REQUIRED FOR REALISTIC EXECUTION` on bid-only data | `ModeLine` |
| Chart | Candles, ranked zones S1–S4 / R1–R4 as bands (buy / sell zone outlined), path-scenario barriers as dashed hypotheses, swings and BOS/CHoCH, the selected plan's entry / stop / targets; zone labels open **why** the zone exists | `ApexChart.tsx` |
| Decision panel | LONG / SHORT only for a gate-qualified plan, else WAIT or NO TRADE with the failed checks; bias (not a signal), buy / sell zone, path scenario (counted, not validated), the three setups with their stage, event risk and auction status | `DecisionPanel` |
| Analyst panel | Per strategy: data, structure, setup, trigger, plan, R:R after costs, evidence, why (not) | `ApexAnalyst.tsx` |
| Bottom tabs | Signal Journal · Data Fitness · Validation (+ track record) · Trade Replay · ULTRON Research (probability heads, promotion, SENTINEL re-check) · Market Context (macro calendar import) | `Apex.tsx`, `ApexDesk.tsx` |

## States a person reads (never mixed)

- **Zone quality score 0–100** = evidence strength, never a probability.
- **Bias** (D1 / H4 / H1 structure) = context, never a signal.
- **Path shares** (upper first / lower first / neither / ambiguous within 120 min) = counted
  frequencies, `NOT_VALIDATED`.
- **Setup state**: WATCH · TRIGGER_PENDING · RESEARCH_ONLY (a plan exists, not qualified) ·
  ENTRY_ARMED (qualified paper signal) · NO_TRADE · BLOCKED.
- **Trade-success probability** P(TP before SL after the fill) — shown only from a validated,
  calibrated **and owner-promoted** head; otherwise `PROBABILITY NOT VALIDATED`.

## Owner goals → implementation

| Goal | Implementation | Status |
|---|---|---|
| Top 4 support / resistance, fewer when evidence is thin | `zonemap.py` (`APEX_ZONE_SPEC.md`) | TESTED_SYNTHETIC |
| Buy zone / sell zone | `zonemap.rank → buy_zone / sell_zone` | TESTED_SYNTHETIC |
| Paths incl. rotation, breakout, fakeout, sweep, neutral | `context.py` (path partition, zone interactions, path cards) | TESTED_SYNTHETIC |
| Statistically checked entry / SL / TP | `setups.py`, `eligibility.py`, `probability.py` | TESTED_SYNTHETIC; no real validation possible yet |
| Breakouts detected | `setups.breakout_retest`, zone lifecycle BROKEN / FLIPPED / fakeout | TESTED_SYNTHETIC |
| ≥ 70 % only when validated (80 % preferred class) | gate check PROBABILITY + UNCERTAINTY_SAMPLE, conviction classes | TESTED_SYNTHETIC |
| ≤ 4 qualified alerts / market / day, 0 is fine | `alerts.py`, `apex_alerts` | TESTED_SYNTHETIC |
| Real order flow only with proper data | `capability.py`, `orderflow.py` | TESTED_SYNTHETIC (fixtures) |
| JARVIS + ULTRON research, SENTINEL veto | validation trials, probability heads, `governance.py` | TESTED_SYNTHETIC |
| Simple UI, 5 global tabs, 5 QuantLab areas | `QuantLab.tsx`, `Apex.tsx` | E2E (synthetic); Mac runtime BLOCKED |

What is confirmed on real market data: **nothing yet** — see `APEX_RELEASE_STATUS.md`.
