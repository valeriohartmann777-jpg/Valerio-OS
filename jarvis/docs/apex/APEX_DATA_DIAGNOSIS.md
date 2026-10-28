# JARVIS APEX — data pipeline diagnosis (STALE · GAPS 100 % · SPIKES)

Date: 2026-10-28 · branch `claude/jarvis-foundation-mxnsz4` · before the repair: `1c0e3ab`.

## 1. Symptom (owner's screenshot)

XAUUSD analysis **blocked**; findings `STALE` ("newest candle 26,228 trading minutes old"),
`GAPS` ("100 % of trading minutes missing"), `SPIKES`; source Dukascopy, bid prices; no trade
candidates.

26,228 trading minutes = 19 trading days (19 × 1,380) + 8 minutes: the newest cached gold bar
was about four calendar weeks old.

## 2. Method

- Read the whole path: `learning/market.py` (download, decode, cache), `apex/service.py`
  (source selection, refresh), `apex/data.py` (quality gate), `apex/engine.py`.
- Reproduced each finding with the **real modules** on hand-built bars and with a stand-in for
  Dukascopy's API (`httpx` mock transport, the same one the market tests use). Script:
  `backend/tests/test_apex_data_repair.py` keeps every reproduction as a regression test.
- **Not possible here:** looking at the owner's cache or calling Dukascopy. The container's
  network policy refuses `jetta.dukascopy.com` (proxy 403). Which of the silent paths in §3.3
  happened on the Mac is therefore *not* known from here — the repaired data status (§5)
  records it on the next update.

## 3. Causes

### 3.1 GAPS 100 % — a calculation error, not missing data (certain)

`assess()` counted expected trading minutes from `as_of − 21 days` to
`min(as_of, newest bar)`. When the newest bar is **older than the 21-day window**, that range is
empty and the share fell back to `1.0`:

```
share = missing / len(expected) if len(expected) else 1.0   # 0 expected → "100 % missing"
```

Reproduction (complete, clean history ending 27 days before the moment):
`BLOCK GAPS count=0 — "100% of trading minutes are missing"` next to `STALE 26220`.
**Zero** minutes were counted missing, yet the finding blocked the analysis. The real problem
was only the age of the data (STALE). The gap share must be measured over the span the data
covers inside the window, and "no bar at all in the window" needs its own finding.

### 3.2 SPIKES — returns across time gaps (certain)

The spike test took log returns between consecutive **bars**, not consecutive **minutes**. Every
weekend reopen (Fri 17:00 → Sun 18:00 New York), every daily break (17:00 → 18:00) and every
missing minute became a "one-minute move". Reproduction on gap-free generated data:
**65 of 65** flagged returns cross a time gap; a clean series is always flagged. The test also
could not tell a bad tick (a price that jumps and comes straight back) from a real news move
(which stays), and it did not say *when*.

### 3.3 STALE that does not heal — silent update paths (certain in code; which one hit the Mac: unknown)

"Update free data first" is on by default, so the analysis asked for an update. Three paths
leave the cache old **without any visible error**:

| # | Path | Effect | Reproduction |
|---|---|---|---|
| a | `ApexService._from_dukascopy` catches a failed update (`DataError`), loads the cache and writes the reason only to `provenance.note` | the UI never shows that the update failed | code path |
| b | `MarketData._sync` stores a day that came back **200 with no candles** (or only zero-volume minutes) as *fetched* — for good | after an outage or API change those days are never asked again; every later update "succeeds" with 0 new days | 27 empty days, API back → **0 days re-fetched**, cache still ends 2026-09-30 |
| c | a **404** for a day older than 5 days is stored as "no data that day" — for good | an outage leaves a permanent hole | 27-day 404 outage, API back → only 4 of 27 days re-asked |

The cache is shared: the morning briefing, the research loop and the training sync write it
through the same `_sync`, so any of them can have marked the days.

### 3.4 Trading calendar used for gaps (checked — correct, with two known limits)

Sun 18:00 → Fri 17:00 New York with the daily break 17:00-18:00, daylight-saving aware
(`local_clock`, tested). This matches Dukascopy XAU/USD spot and CME Globex.
Limits: exchange holidays are not modelled (they count as gaps), and the decoder keeps only
minutes with volume (Dukascopy's flat, volume-less minutes count as gaps). Both are small
(well below the 50 % block level) and are stated in the finding.

### 3.5 Bid only (checked)

All gold candles are **bid** prices. A buy limit really fills at the ask, a short is stopped at
the ask. The spread was never measured; costs assumed a flat 0.40 per round trip.

## 4. Repair (what changed)

| Cause | Repair | Quality checks |
|---|---|---|
| 3.1 | GAPS measured over the covered span inside the window (first bar … newest bar), with the largest gaps listed (from, to, trading minutes). No bar in the window → `NO_RECENT_DATA` (BLOCK, with the date of the newest bar and the repair action) | kept; stricter wording |
| 3.2 | spike test on **consecutive minutes only**; a bad tick = a move > 15 robust σ that reverses at once, or a wick far beyond both neighbours; listed with times. Large moves that stay → `LARGE_MOVES` (INFO, listed — not data errors) | kept; a planted bad tick is still caught (test) |
| 3.3a | a failed update is shown as `UPDATE_FAILED` with the exact reason and what to do | new |
| 3.3b/c | empty or 404 trading days (Sun-Fri) are **not** marked fetched any more; they are re-checked at most every 12 h while younger than 45 days. Existing caches heal: days already stored empty are re-checked. 3+ consecutive trading days without candles → the update fails loudly ("API change or outage"), nothing is marked. A single day asked twice and still empty is shown as `no_trading` (a holiday most likely), not as missing | new |
| all | every update writes a report (days asked, with data, empty, 404, failed, HTTP statuses, error) next to the cache; `GET /apex/data/{market}` shows coverage, newest bar, suspicious days, the last update and concrete causes with repair actions; `POST /apex/data/{market}/repair` re-checks every suspicious day and downloads what is missing | new |
| 3.5 | ask candles for the recent days are downloaded too; the measured spread (median / 90 % in the session) is shown, and `SPREAD` warns when it exceeds the configured costs | new |

No check was removed or loosened to make an analysis pass: every original code still fires on
the case it was meant for (tests in `test_apex_data_repair.py`).
