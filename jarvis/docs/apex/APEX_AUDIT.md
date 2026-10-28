# JARVIS APEX Trading AI — repository audit (APEX-0)

Date: 2026-10-28 · branch `claude/jarvis-foundation-mxnsz4` · **rollback point `f6419d0`**
(last commit before APEX; JARVIS 2.0 complete, `check.sh` green with pytest 625 / vitest 47,
E2E 34/34).

> **Specification:** the owner's request (2026-10-28) names an attached "JARVIS-APEX
> masterprompt / developer package" as the binding spec. **It was not received** — the
> session's uploads contain only earlier packages (JARVIS 2 buildkit, QuantLab, ULTRON, AI
> billing). APEX-0 and APEX-1 therefore follow the owner's written request and the repository's
> existing rules (D-028 … D-032). When the package arrives, `APEX_SPEC_TRACE.md` maps every
> requirement and the gaps are closed first.

## 1. Goal (from the owner's request)

A trading AI integrated into JARVIS, QuantLab and ULTRON for **XAUUSD** and **Nasdaq futures
(NQ / MNQ)** that

1. recognises objective **market structure** and **support / resistance zones**,
2. analyses **probabilistic scenarios**,
3. generates **verifiable entry, stop-loss and take-profit candidates**,

under data-quality, no-look-ahead, backtesting, statistics and safety rules; only real or
clearly labelled test data; no invented results or accuracies; **no real-money trading**.

## 2. What already exists (reused, not duplicated)

| Building block | Location | State | Use in APEX |
|---|---|---|---|
| Minute data XAUUSD (Dukascopy spot, bid) | `learning/market.py`, cache `data/market/XAUUSD/*.npz` | real, free, validated per day | primary XAUUSD source |
| Minute data "NQ" (Dukascopy USA Tech 100 **CFD**) | same, `data/market/USATECHIDXUSD/` | real, but a **CFD proxy**, not the futures contract | NQ fallback, labelled as proxy |
| NQ / MNQ futures 1-minute bars (Databento) | QuantLab Data Vault + Data Hub (`quantlab/vault`, `quantlab/hub`) | real when the owner bought data; checksum-verified, content-addressed snapshots; fixture provider = synthetic | primary NQ / MNQ source |
| Contract master NQ / MNQ (tick 0.25, $5 / $0.50 per tick) | `quantlab/futures/contracts.py` | tested | risk per contract |
| Key levels (previous day / week, overnight / Asia, volume profile, intact 15-min swings, round numbers, merged by confluence) | `briefing/levels.py` | point-in-time by construction (only bars passed in) | zone candidates |
| No-look-ahead indicators (ATR, EMA, confirmed pivots) | `learning/features.py` | tested | structure, zone widths |
| Touch dataset: every touch of a key level, held / broken label, features as known at the touch | `training/dataset.py` | tested; leakage rules documented | empirical scenario base rates |
| S/R model, walk-forward judged, baseline comparison | `training/model.py`, `training/live.py` | tested; number shown only when confirmed | later: model odds next to base rates |
| Level studies vs. random control | `learning/levels.py` | tested | method reference |
| Futures backtest engine, costs, conservative same-bar rule, validation, verdicts, SENTINEL second engine | `quantlab/futures/*`, `quantlab/ideas/sentinel.py` | tested | resolution rules, audit pattern |
| ULTRON agents (CIPHER quant, SENTINEL verification), HQ projection, team workspaces | `ultron/`, `team/` | tested | APEX agent tasks |
| Chat tools (e.g. `level_odds`), QuantLab UI sections, Training Hub | `tools/`, `apps/desktop` | tested | APEX tool, APEX section |

## 3. Gaps (what APEX must add)

| Gap | Why it matters |
|---|---|
| No **market-structure** model (swings labelled HH/HL/LH/LL, trend state, break of structure / change of character) | goal 1 |
| Levels are **lines**, not zones with width, confluence and test history | goal 1 |
| No **scenario** report combining zones with measured probabilities and uncertainty | goal 2 |
| No **entry / SL / TP candidate** generator with risk per contract and expiry | goal 3 |
| No **candidate journal** with immutable records and **forward verification** on later bars | "überprüfbar" |
| No **track record** of the candidate rules (walk-forward, point-in-time, costs, IS/OOS) | no invented accuracies |
| APEX not reachable from chat, QuantLab or ULTRON | integration |
| XAUUSD exists only as spot (Dukascopy); the Data Vault knows futures roots NQ/MNQ/ES/MES only | source labelling: spot gold ≠ GC futures |

## 4. Constraints found

- **Network in the development container**: `jetta.dukascopy.com` and Databento are blocked
  (proxy 403). Real-data runs happen on the owner's Mac (existing caches / vault). Here, tests
  and the E2E run use **generated test data, labelled "SYNTHETIC TEST DATA" everywhere**.
- Contract rolls: continuous NQ series change `instrument_id` at rolls; prices of different
  contracts must not be mixed in one level calculation.
- Dukascopy prices are **bid** prices; the NQ series there is a CFD on the index.

## 5. Data safety

- No existing table, cache or file is changed or deleted. APEX adds tables in a new migration
  (20); the database is copied (`VACUUM INTO`, newest 5 kept) before any migration, as since
  J2-1; rollback rehearsed in `test_database.py`.
- APEX reads Data Vault bars only through checksum-verified, content-addressed dataset
  snapshots and the Dukascopy cache read path; it never writes into either.
- Fetching Dukascopy days is free; a Databento purchase stays the existing explicit-approval
  flow and is never triggered by APEX.

## 6. Plan

| Release | Content | Evidence |
|---|---|---|
| **APEX-1** vertical prototype | data adapters + quality gate; structure; zones; scenarios with empirical base rates (Wilson CI, n, days); entry/SL/TP candidates; immutable journal + forward resolution; walk-forward track record; API; QuantLab section; tests incl. look-ahead invariance | pytest, E2E |
| APEX-2 integration | chat tool, ULTRON tasks (CIPHER evaluation, SENTINEL audit of a candidate set), HQ projection, Home tile | tests, E2E |
| APEX-3 calibration | reliability of stated probabilities against resolved outcomes, model odds next to base rates, per-session breakdowns | tests |
