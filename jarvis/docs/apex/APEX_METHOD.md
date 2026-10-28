# JARVIS APEX — method

> Engine `apex-1.2.0` (2026-10-28) adds the execution model, the 5-minute trigger strategy, the
> signal contract, the validation protocol and the forward log — specified in
> `docs/APEX_SIGNAL_SPEC.md`, `docs/APEX_VALIDATION_PROTOCOL.md`, `docs/APEX_DATA_FITNESS.md` and
> `docs/APEX_ARCHITECTURE.md`. Where they differ from this file, they win.

Every number APEX shows is defined here. Engine version `apex-1.1.0` (quality gate repaired,
D-034; `backend/jarvis/apex/`). Settings: `config/apex.yaml`.

## 1. Data

| Market | Sources, in order | Notes |
|---|---|---|
| XAUUSD | `dukascopy:XAUUSD` | gold spot, **bid** prices, free; learning cache `data/market/XAUUSD` |
| NQ | `vault:NQ` → `vault:MNQ` → `dukascopy:NQ` | Data Vault futures bars (Databento / imports) first; the Dukascopy series is a **CFD on the index**, shown with a proxy warning |
| MNQ | `vault:MNQ` → `vault:NQ` → `dukascopy:NQ` | same index as NQ at 1/10 the size; NQ prices are labelled as such |

- Vault bars are read through **checksum-verified, content-addressed dataset snapshots**
  (`DataHubService.build_dataset` / `load`) — local only, no provider call, no fee.
- Several contracts on one trading day → the one with the most volume that day is kept
  (front month). Contract changes are recorded; **levels never mix two contracts**.
- `Update data first` fetches missing Dukascopy days (free, no key) and today's bars so far.
  A Databento purchase is never triggered by APEX; it stays the Data Vault's explicit-approval
  flow.
- **Cache healing (D-034).** A trading day (Sunday-Friday) that Dukascopy answers without
  candles, or with 404, is *not* stored as fetched: it is noted in `_checks.json` and asked
  again — every update while ≤ 5 days old, every 12 hours while ≤ 45 days old, always on a
  repair. Days that older versions stored as empty are re-checked the same way. Three or more
  trading days in a row without candles fail the update ("not a holiday: the API changed or is
  down") and nothing is marked.
- **Update reports.** Every update records days asked, with data, empty, 404, not yet
  published, failed, the HTTP answers and the error. The analysis carries the outcome of its
  own update (`data.update`); a failed one is shown, never swallowed.
- **Bid / ask.** Gold candles are **bid**. The ask candles of the last `spread_days` (10) are
  downloaded too: `data.spread` reports the measured ask − bid (median and 90 % overall and in
  the session window) against the configured round-trip costs. The resolver still works on bid
  prices with the configured costs (D-034 explains why).
- `provider: fixture` (tests, E2E only) replaces every source with generated bars labelled
  **SYNTHETIC TEST DATA** in the API, the UI, the journal and the track record. Test-data
  results are never mixed with real-data results.

## 2. Moment of analysis (no look-ahead)

An analysis at `as_of` first cuts the series to bars that had **closed** by then
(`Series.until`). Higher timeframes (15 min, 1 h) drop an unfinished last bar. Swings are
known only after their confirming bars closed. The same moment therefore gives the same result
whether or not later bars exist — `test_an_analysis_never_sees_bars_after_its_moment` checks
this by appending five days and by rewriting all later prices by +30 %: the analysis is
identical. **Replay** (`as_of` in the past) uses the same path.

## 3. Quality gate

| Code | Level | Rule |
|---|---|---|
| EMPTY / ORDER / OHLC / PRICE | BLOCK | no bars · timestamps not strictly increasing · high/low don't contain open/close · non-positive or outside the market's plausible range |
| NO_RECENT_DATA | BLOCK | no bar at all in the last `level_days` — zones and structure need the recent days (reported with the newest bar's time) |
| GAPS | WARN (BLOCK > 50 %) | share of missing trading minutes **between the first and the newest bar inside** the last `level_days` (Sun 18:00 → Fri 17:00 New York, daily break 17-18; holidays and minutes without trades count as gaps); the three largest gaps are listed |
| STALE | WARN | more than `stale_minutes` trading minutes between the newest bar and `as_of` → **no candidates** |
| SPIKES | WARN | on **consecutive minutes only**: a move > 15 robust standard deviations that the next minute takes back (≥ 70 %), or a high/low that far beyond its own body and both neighbours — listed with times |
| LARGE_MOVES | INFO | moves > 15 robust standard deviations that held (news) — listed, not data errors |
| SHORT_HISTORY | WARN | fewer than 60 % of `history_days` covered |
| PROXY / SYNTHETIC / ROLL | WARN / INFO | as in §1 |

## 4. Market structure (`structure.py`)

On 15-minute and 1-hour bars: a swing high is a bar whose high is above the 3 bars before it
and not below the 3 after it (lows mirrored), confirmed when those 3 bars have closed. Labels:
HH/LH (highs), HL/LL (lows) against the previous swing of the same kind. Trend: **up** = latest
high HH and latest low HL; **down** = LH and LL; else **range**. A close beyond the latest
unbroken confirmed swing is a **BOS** with the trend (or from a range) and a **CHoCH**
against it.

## 5. Zones (`zones.py`)

Levels from `briefing/levels.py` (previous day high/low/close and value area, overnight or
Asia range, previous week high/low, intact 15-minute swings, round numbers). Each level
becomes a zone of ±max(0.25 × ATR(14, 15 min), half the merge distance, 2 ticks); overlapping
zones merge and keep all reasons. Nearest zones get ids S1, S2 … below and R1, R2 … above the
price. `tests` = 15-minute bars entering the zone after a bar outside it (last `level_days`).
`score` only ranks zones; it is not a probability.

## 6. Scenarios and rates (`scenarios.py`, `stats.py`)

The rates come from the touch dataset in `training/dataset.py` (tested for leakage): every
touch of a key level by a 5-minute bar inside the market's session window (XAUUSD 03:00-13:30,
NQ/MNQ 09:30-16:00 New York), labelled **held** if price moved 1 ATR away on the level's side
before going 0.5 ATR through it within 60 minutes, **broken** otherwise. Touches broken inside
the touching bar and touches undecided at the horizon are excluded and counted.

- Only touches whose **horizon ended before `as_of`** count.
- A zone's rate uses touches of the same side (support / resistance) and of any of the zone's
  kinds of level.
- Reported as share, **95 % Wilson interval**, number of touches, number of trading days.
- Fewer than `min_samples` (30) decided touches → **"insufficient data"**, no number.
- Hold and break are complementary among decided touches.

## 7. Candidates (`candidates.py`) — template `zone_reaction`

| | Long at support S1 | Short at resistance R1 |
|---|---|---|
| Condition | price above S1, at most 3 ATR(15m) away; price not inside the zone | mirrored |
| Entry | buy limit at S1's top (tick-rounded) | sell limit at R1's bottom |
| Stop | S1's bottom − 0.15 ATR | R1's top + 0.15 ATR |
| Target 1 / 2 | one tick before the nearest / next resistance zone | mirrored |
| Rejected when | stop < market minimum (gold 0.8, NQ 3 points) · stop > 2.5 ATR · no target zone · reward/risk to target 1 **after costs** < 1.5 | |
| Valid | 240 minutes for the fill; held at most 480 minutes | |

Costs per round trip: gold 0.40, NQ/MNQ 1.0 point. Risk per unit: points × point value
(gold $100 per lot of 100 oz, NQ $20, MNQ $2). Each candidate gets a content id, the zone's
counted rate and the rule's latest track record — never a probability of its own.

## 8. Resolution (`resolve.py`)

On the one-minute bars after the candidate, from the same source:
limit fills at its price, never better · in the fill bar only the stop can be hit · after the
fill a bar touching stop and target counts as **stop** · target 1 traded before a fill →
**missed** · no fill within validity → **expired** · open after the maximum hold → **timeout**
at that bar's close · R multiples net of costs. On a driftless random walk the resolver
reproduces the theoretical 40 % for +3 before −2 (test).

## 9. Journal

Every candidate is written once to `apex_candidates`; database triggers refuse UPDATE and
DELETE. Outcomes move from pending/open to a final state, and a **final outcome can't be
changed** (trigger). Real-data and test-data statistics are kept apart.

## 10. Track record (`evaluate.py`)

Walk forward over the period: every `eval_step_minutes` (60) inside the session window, the
series is cut to what was known then and **the same function as the live analysis** makes
candidates (a repeat of the same direction and entry on the same day counts once). Each is
resolved as in §8 if its horizon lies within the data. Reported for all, in-sample and the last
30 % (out-of-sample): outcome counts, fill rate, win rate with 95 % Wilson interval, expectancy
in R with a **day-clustered** 95 % interval, profit factor, maximum drawdown in R.

Verdicts: `INSUFFICIENT` (< 30 filled trades in a period) · `NO_EDGE` (out-of-sample mean ≤ 0)
· `INCONCLUSIVE` (interval includes 0) · `POSITIVE_OOS` (in-sample mean > 0 and the
out-of-sample interval above 0 — "a measurement on this data, not a guarantee").

## 10b. Calibration of the stated rates (APEX-3)

Every track record also checks the scenario rates (`calibration.py`). For each decided touch in
the period, in time order, the rate APEX *would have stated* then (same side, sharing a kind of
level, only touches decided before it — the rule of §6) is compared with what happened:

- **Reliability table**: stated rates in bins (< 35 %, 35-45 %, 45-55 %, 55-65 %, > 65 %); per bin
  the mean stated rate, the observed held share and its 95 % Wilson interval. A bin fits when
  the stated rate lies inside the observed share's interval, with the level corrected for the
  number of well-filled bins (Bonferroni) so one bin out of several isn't flagged by chance.
- **Brier score** of the stated rates vs. a side-only reference; `skill` = 1 - ratio: above 0
  means the kinds of level carry information, around 0 means they don't.
- Verdict `CALIBRATED` / `MISCALIBRATED` / `INSUFFICIENT`. Touches with fewer than 30
  comparable earlier touches get no stated rate (as in the live analysis) and are counted.

On generated random-walk data the check reports skill ≈ 0 and, typically, `MISCALIBRATED` for
the higher bins (regression to the mean) — as it should; it shows nothing about real markets.

## 11. SENTINEL re-check and ULTRON (APEX-2)

**Ask SENTINEL to re-check** (`POST /apex/analyses/{id}/audit`) recomputes a stored analysis
from the data on disk now — on the same source, which by now may hold bars after the moment:

| Check | Passes when |
|---|---|
| `SAME_DATA` | the bars before the moment hash to the value recorded with the analysis |
| `NO_LOOKAHEAD` | zones, structure, quality, rates, rejections, chart and candidates are identical |
| `OUTCOMES` | every final journal outcome of the analysis is reproduced exactly on re-judging |

Every re-check is stored (`apex_audits`). The ULTRON HQ shows SENTINEL's re-checks and CIPHER's
track records as real work in the ULTRON QuantLab room (`team/hq.py`, `from_apex`). JARVIS can
answer from APEX in the chat (`apex_analysis` tool, read-only; it names the data source and
says when it is test data).

## 11b. Data status, repair, chart and JARVIS's reading (D-034)

- `GET /apex/data/{market}` — per source: cache folder, first / last day, minute bars, newest
  bar and how many trading minutes before today are missing, each day's state in the analysis
  span (data, weekend, empty, missing, pending, no_trading = asked twice and still empty, a
  holiday most likely, stored_empty, not_fetched), runs of days without candles, the last update reports, the measured spread. From these: **causes** with
  level and action — `UPDATE_FAILED` (with what to do for a refused / unreachable / changed
  API), `CACHE_BEHIND`, `MISSING_DAYS`, `SPREAD`, `SPREAD_UNMEASURED`, `PROXY`,
  `NO_LOCAL_DATA`, or `OK`.
- `POST /apex/data/{market}/repair` — in the background: re-checks every trading day without
  candles in the span (`recheck`), downloads what is missing (bid; ask for the spread) and
  today's bars; Data Vault sources are skipped (never bought from here). The result per step
  stays visible.
- `GET /apex/analyses/{id}/chart?minutes=1|5|15|60|240` — the analysis' own bars at that
  timeframe, cut at its moment; a last bar still forming then is marked; `same_data` says
  whether the cache changed since (e.g. healed days).
- `narrative` — JARVIS's reading of an analysis (headline, points, caveats), built only from
  its numbers; no model call. "Ask JARVIS about it" puts a question into the command bar; it is
  sent only when the owner presses Enter.
- Probabilities on screen: the counted rates are labelled **calibrated** only when the latest
  track record on the same kind of data found them `CALIBRATED`; otherwise "past share" — a
  frequency of earlier touches, not a probability.

## 12. What APEX does not do

No order, no broker connection, no messages to anyone. No probability that wasn't counted. No
claim of an edge. No real-money switch exists.
