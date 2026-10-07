# HYPOTHESES_PRICE_ACTION — Family C (Price action / support & resistance)

Pre-registered 2026-10-07, **before any market data was seen**. Event definitions,
parameters, primary tests and expectations are frozen; later changes get new IDs and a
journal entry. Theory: `theory/THEORY_PRICE_ACTION.md`. Common definitions are those of
`HYPOTHESES_AUCTION.md` and `RESEARCH_PROTOCOL.md`. Machine-readable mirror:
`configs/event_studies.yaml`.

## Family-specific definitions

* **Level sources.** PD = completed prior RTH high/low; PW = completed prior-week
  high/low; PZ = intraday pivot zones from confirmed pivots (left = right = 6 on 5m),
  merged within 0.25·ATR (`pivot_zones`), usable once confirmed; RN = round numbers
  (ES multiples of 25 points, NQ multiples of 100).
* **Zone entry (touch).** A level is armed once a close sits on its correct side by more
  than 0.10·ATR. A touch = the first bar after arming with low ≤ L + 0.10·ATR (support;
  resistance mirrors) whose close is not beyond L − 0.25·ATR. A new touch of the same
  level needs a move away of ≥ 0.50·ATR first. Decision at the touch bar's close.
  (This is the *entry* into the zone, not `level_touches`' confirmed reaction, so the
  event does not condition on the bounce it is supposed to predict.)
* **Breakout variants.** wick: high > L + 1 tick; close: close > L + 0.10·ATR;
  ATR: close > L + 0.25·ATR; volume: close breakout with bar volume ≥ 80th percentile of
  the same minute over the prior 20 sessions; strong body: close breakout with body ≥ 1.5 ×
  median body(100) and close location ≥ 0.75.
* **Session window.** RTH decisions 09:45–15:00 unless stated.
* **Shifted-reference twin** and **time-matched controls** as in family A.

## Ranking (made before data)

Scores 1–5 (M mechanism, T testability, F frequency, D distinctness, C cost survival);
ties by C, M, D, F, ID.

| rank | ID | hypothesis | M | T | F | D | C | total |
|---|---|---|---|---|---|---|---|---|
| 1 | HC01 | First touch of prior-day extremes → reaction | 4 | 5 | 4 | 3 | 3 | 19 |
| 2 | HC05 | Failed breakout → reversal, stronger than a touch | 4 | 5 | 4 | 2 | 3 | 18 |
| 3 | HC12 | 30-minute opening-range breakout → continuation | 3 | 5 | 5 | 2 | 3 | 18 |
| 4 | HC04 | Strong close breakout → continuation | 3 | 5 | 4 | 2 | 3 | 17 |
| 5 | HC02 | Touch number: first touch best, later touches weaker | 3 | 5 | 4 | 3 | 2 | 17 |
| 6 | HC09 | Round numbers: bounce at, acceleration through | 3 | 5 | 4 | 3 | 2 | 17 |
| 7 | HC11 | Level source: PD vs PW vs pivot zones vs round numbers | 3 | 5 | 4 | 3 | 2 | 17 |
| 8 | HC03 | Recent levels beat old levels | 2 | 5 | 4 | 3 | 2 | 16 |
| 9 | HC07 | Compression → larger move (magnitude, not direction) | 4 | 5 | 3 | 3 | 1 | 16 |
| 10 | HC06 | Polarity: first retest of a broken level holds | 3 | 4 | 3 | 2 | 3 | 15 |
| 11 | HC10 | Rejection candle at support adds information | 2 | 5 | 3 | 2 | 2 | 14 |
| 12 | HC08 | Higher-low confirmation in an uptrend → continuation | 2 | 4 | 4 | 1 | 2 | 13 |

---

## HC01 — First touch of prior-day extremes → reaction
* **Theory.** Support/resistance from order clustering and anchoring (THEORY_PA 1, 2.1).
* **Market mechanism.** Resting limit orders and anchored traders at yesterday's
  extremes absorb the first approach.
* **Event (long at PDL; short mirrors at PDH).** First zone entry of PDL in the session.
* **Expected direction.** Bounce away from the level.
* **Expected timeframe.** 15–60 minutes.
* **Expected session.** RTH.
* **Expected regime.** Balance; low/mid volatility.
* **Potential entry.** Next-bar market; or limit at L.
* **Potential invalidation.** L − 0.25·ATR (the break threshold).
* **Potential target.** Session VWAP; 1R / 2R; the session mid.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Price oscillates around any line; generic intraday
  reversion.
* **Controls.** Shifted-reference (primary), time-matched.
* **Primary test (C001).** 12-bar signed return in ATR: event minus shifted-reference.
* **Conditions.** Trend alignment (daily trend tercile, sign of the 20-bar return);
  daily volatility tercile; distance of the RTH open from the level.
* **Prior.** +0.02 to +0.08 ATR; P ≈ 0.25.

## HC02 — Touch number: first touch strongest
* **Theory.** Level strength vs weakening (THEORY_PA 2.10).
* **Market mechanism.** Each test consumes resting orders.
* **Event.** All zone entries of PD and PZ levels, numbered within the session (1, 2, 3+).
* **Expected direction / timeframe / session / regime.** Bounce; 15–60 minutes; RTH; any.
* **Potential entry / invalidation / target.** As HC01.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Later touches happen later in the day (different
  volatility); time-matched controls per touch absorb it.
* **Controls.** Time-matched per touch number.
* **Primary test (C002).** (event − control) for touch 1 minus (event − control) for
  touches 3+, 12-bar ATR return.
* **Prior.** Positive difference (+0.02 to +0.06); P ≈ 0.20.

## HC03 — Recent levels beat old levels
* **Theory.** Recency (THEORY_PA 2.10).
* **Event.** First zone entry of PZ levels, split by age: formed in the current session
  vs 1 session old vs 2–5 sessions old.
* **Expected direction / timeframe / session / regime.** Bounce; 15–60 minutes; RTH; any.
* **Potential entry / invalidation / target.** As HC01.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Old levels that survive are a selected sample.
* **Controls.** Time-matched per age group.
* **Primary test (C003).** (event − control): current-session minus 2–5-session-old
  levels, 12-bar ATR return.
* **Prior.** Small positive; P ≈ 0.15.

## HC04 — Strong close breakout → continuation
* **Theory.** Breakouts through levels with stop clusters accelerate (THEORY_PA 2.4).
* **Event (long through resistance; short mirrors).** ATR breakout of PD or PZ levels
  (close > L + 0.25·ATR), first breakout of that level in the session.
* **Pre-registered variants** (separate tests): wick **C005**, close **C006**, volume
  **C007**, strong body **C008**.
* **Expected direction / timeframe / session / regime.** Continuation; 30–120 minutes;
  RTH; trend, expanding volatility.
* **Potential entry.** Next-bar market or stop entry one tick above the breakout bar.
* **Potential invalidation.** Back below L − 0.25·ATR.
* **Potential target.** Next level; 2R; session close.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Large up bars continue slightly anywhere (or
  reverse); the level may add nothing.
* **Controls.** Time-matched (primary); the same bar-strength filter without a level
  (strength twin).
* **Primary test (C004).** 12-bar signed return in ATR: event minus time-matched.
* **Descriptive.** False-break rate (close back inside within 3 bars) per variant.
* **Prior.** +0.00 to +0.05 ATR; false-break rate 0.35–0.55; P ≈ 0.20.

## HC05 — Failed breakout → reversal, stronger than an ordinary touch
* **Theory.** Trapped breakout traders (THEORY_PA 2.5); the mission's explicit question.
* **Event (short after a failed upside break; long mirrors).** Close breakout of PD or PZ
  (close > L + 0.10·ATR), then a close < L within 3 bars; event at that close.
* **Expected direction / timeframe / session / regime.** Reversal; 30–120 minutes; RTH;
  balance.
* **Potential entry / invalidation / target.** Next-bar market; failed-break extreme +
  0.10·ATR; the opposite side of the range / session VWAP.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Same as HB01 (and HA05) with different levels;
  generic mean reversion.
* **Controls.** Shifted-reference (primary), time-matched.
* **Primary test (C009).** 12-bar signed return in ATR: event minus shifted-reference.
* **Registered comparison.** (C009 effect) minus (C001 effect) on PD levels, same
  horizon — does a failed breakout beat an ordinary touch?
* **Prior.** +0.03 to +0.10 ATR; larger than the C001 effect; P ≈ 0.25.

## HC06 — Polarity: first retest of a broken level holds
* **Theory.** Polarity (THEORY_PA 1, 2.6).
* **Event (long after an upside break; short mirrors).** After a C004 breakout, the
  first later bar within 24 bars with low ≤ L + 0.10·ATR and close ≥ L − 0.25·ATR;
  event at that close. Also recorded: share of breakouts with no retest (missed
  trades), second retest outcomes.
* **Expected direction / timeframe / session / regime.** With the original breakout;
  30–120 minutes; RTH; trend.
* **Potential entry.** Limit at L + 0.10·ATR (the retest); market at the close.
* **Potential invalidation.** L − 0.25·ATR.
* **Potential target.** The breakout leg high; 2R.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Only breakouts that come back are traded; the
  retest entry hides the missed winners.
* **Controls.** Shifted-reference (primary), time-matched.
* **Primary test (C010).** 12-bar signed return in ATR: event minus shifted-reference.
* **Prior.** +0.00 to +0.05 ATR; P ≈ 0.15.

## HC07 — Compression → larger move (magnitude, not direction)
* **Theory.** Volatility contraction precedes expansion (THEORY_PA 1, 2.7).
* **Event (both directions).** Compression at t−1: ATR percentile within the trailing
  500 bars ≤ 0.20 and the 12-bar high−low range ≤ 2.0·ATR. Event = first close outside
  the prior 12-bar range; direction with the close. Twin: the same range break without
  compression.
* **Expected direction.** Magnitude larger; direction no better than the twin.
* **Expected timeframe / session / regime.** 30–120 minutes; RTH; low volatility.
* **Potential entry / invalidation / target.** Stop entry beyond the range; opposite
  side of the range; 1× range projection.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Magnitude is real but useless without direction;
  directional "success" may be drift.
* **Controls.** Non-compressed range breaks (primary).
* **Primary tests.** C011: mean |12-bar return| in ATR units of the 5m bars at the event
  — note that ATR is low in compression, so magnitude is ALSO reported in ATR_d units —
  compression minus twin. C012: signed 12-bar return, compression minus twin.
* **Prior.** C011 positive in ATR units, ≈ 0 in ATR_d units; C012 ≈ 0. P(C012) ≈ 0.10.

## HC08 — Higher-low confirmation in an uptrend → continuation
* **Theory.** Trend structure HH/HL (THEORY_PA 2.2).
* **Event (long; short mirrors).** `structure_trend` = +1 (last structure break bullish,
  pivots 3/3) and a newly confirmed pivot low above the previous pivot low; event at the
  confirmation bar (pivot + 3 bars).
* **Expected direction / timeframe / session / regime.** With the trend; 30–90 minutes;
  RTH; trend.
* **Potential entry / invalidation / target.** Next-bar market; below the new higher
  low; the last high, then 2R.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Equivalent to short-term momentum.
* **Controls.** Momentum twin (primary): time-matched bars with direction = sign of the
  20-bar return.
* **Primary test (C013).** 12-bar signed return in ATR: event minus momentum twin.
* **Prior.** ≈ 0; P ≈ 0.10.

## HC09 — Round numbers: bounce at, acceleration through
* **Theory.** Osler (2003): take-profits at round numbers, stops just beyond.
* **Event a (bounce).** Zone entry at the nearest round number (RN) below (support) /
  above (resistance), RTH.
* **Event b (cascade).** A close through an RN by ≥ 0.25·ATR; direction with the cross.
* **Expected direction.** a: bounce; b: continuation.
* **Expected timeframe / session / regime.** 15–60 minutes; RTH; any.
* **Potential entry / invalidation / target.** a: as HC01; b: as HC04.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Round numbers are dense; many weak events.
* **Controls.** Shifted-RN twin: the same grid offset by half a step (primary).
* **Primary tests.** C014 (bounce) and C015 (cascade): 12-bar signed return in ATR,
  event minus half-step twin.
* **Prior.** a: +0.00 to +0.03; b: +0.00 to +0.04; P ≈ 0.10 each.

## HC10 — Rejection candle at support adds information
* **Theory.** Candle confirmation (THEORY_PA 2.9).
* **Event.** C001 events where the touch bar has lower wick ≥ 0.50 × range and close
  location ≥ 0.65 (mirror for resistance), vs C001 events without.
* **Expected direction / timeframe / session / regime.** As HC01.
* **Potential entry / invalidation / target.** As HC01.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Small subsamples; post-hoc pattern hunting.
* **Controls.** Within-event comparison.
* **Primary test (C016).** 12-bar signed return in ATR: rejection-bar minus other C001
  events.
* **Prior.** ≈ 0; P ≈ 0.10.

## HC11 — Which level source matters?
* **Theory.** Do prior-day levels beat local swing levels (the mission's question)?
* **Event.** First zone entry of PD, PW, PZ and RN levels (HC01 definition), each
  against its own shifted-reference twin.
* **Expected direction / timeframe / session / regime.** Bounce; 15–60 minutes; RTH; any.
* **Potential entry / invalidation / target.** As HC01.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Multiple comparisons across sources.
* **Controls.** Shifted-reference per source.
* **Primary test (C017).** (event − twin) for PD minus (event − twin) for PZ, 12-bar ATR
  return. PW and RN are descriptive.
* **Prior.** PD > PZ by +0.01 to +0.05; P ≈ 0.15.

## HC12 — 30-minute opening-range breakout → continuation
* **Theory.** Classical opening-range breakout; benchmark-class rule.
* **Event (both sides).** OR = 09:30–10:00 high/low (known at 10:00). First 5m close
  above the OR high or below the OR low, decisions 10:00–15:00; direction with the close.
* **Expected direction / timeframe / session / regime.** Continuation; to the session
  close; RTH; trend days.
* **Potential entry.** Next-bar market or stop one tick beyond the OR.
* **Potential invalidation.** OR mid or the opposite OR side.
* **Potential target.** Session close; 1× / 2× OR width.
* **Required data.** 1m OHLCV.
* **Main risk of false discovery.** Bull-market drift on long breakouts; intraday
  momentum already documented.
* **Controls.** Time-matched (primary).
* **Primary test (C018).** Signed return from the next bar to 15:55, in ATR_d, minus
  time-matched controls.
* **Conditions.** OR width tercile; daily trend tercile; gap direction.
* **Prior.** +0.00 to +0.05 ATR_d; P ≈ 0.20.

---

## Coverage of the mission's S/R questions

| question | where |
|---|---|
| Does S/R change the future return distribution? | HC01, HC11 |
| Number of touches? Repeated testing weakens / strengthens? First retest special? | HC02, HC06 |
| Level age / recency? | HC03 |
| Breakout candle strength, close beyond, distance beyond, volume expansion? | HC04 variants |
| Compression before breakout? | HC07 |
| Volatility regime, trend alignment? | conditions in HC01, HC04, HC12 |
| Previous-day levels vs local swing levels? | HC11 |
| Failed breakout vs ordinary support touch? | HC05 registered comparison |
| Does polarity hold? | HC06 |

## Simple baselines (computed for every candidate of every family)

Intraday buy-and-hold; random entry and direction with the candidate's exits;
previous-day breakout; VWAP 2-SD mean reversion; 5m EMA crossover, flat by 15:55
(THEORY_PRICE_ACTION section 4). These are benchmarks, not hypotheses, and are not
counted as tests.
