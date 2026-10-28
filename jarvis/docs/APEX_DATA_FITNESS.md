# JARVIS APEX — data fitness

Is a series fit for an analysis, and if not, exactly why. Code: `backend/jarvis/apex/data.py`
(quality gate `assess`, calendar, spikes), `fitness.py` (the view), `execution.py` (what the
quote side means for fills), `learning/market.py` (Dukascopy cache, healing, update reports).
API: `GET /apex/fitness/{market}`, `GET /apex/data/{market}`, `POST /apex/data/{market}/repair`.
UI: APEX → Data fitness. Root-cause history of the owner's blocked screen:
`docs/apex/APEX_DATA_DIAGNOSIS.md`.

**Nothing here repairs a bar or loosens a check.** The view explains the gate's verdict with
counts and examples; a bad basis stays blocked until the data itself is fixed (download,
import).

## 1. What the view shows

| Field | Definition |
|---|---|
| Source | label and `source_id` (`dukascopy:XAUUSD`, `vault:NQ`, `fixture`) |
| Symbol · contract | spot (no expiry) for gold; for Data Vault futures the front contract's instrument ids and the rolls marked; "none — a CFD proxy" for Dukascopy USA Tech 100 |
| Quote side | `bid` (Dukascopy), `trades` (futures), `generated` (test data) |
| Granularity | 1-minute bars; 5 / 15 / 60 / 240 minutes are built from them, closed bars only |
| Last closed bar | close time of the newest one-minute bar before now |
| Expected bars | trading minutes between the first bar and the last closed bar (calendar below) — present / expected = coverage |
| Gaps by reason | **day without bars** (a trading day with not one bar: holiday, or not in the cache), **long gap** (≥ 30 missing minutes in a row inside a day with bars), **short gap** (fewer: quiet minutes without trades); minutes per reason add up to all missing minutes; examples with dates |
| Outside calendar | bars at minutes that are not trading minutes |
| Integrity | duplicates / out-of-order timestamps; timestamps not on the minute grid |
| Spikes | one-minute moves above 15 robust σ on **consecutive** minutes that the next minute takes back (≥ 70 %), or wicks that far beyond their own body and both neighbours; with minute and σ. Large moves that held (news) are listed apart and are not errors |
| Execution | how fills are modelled on this data (`APEX_SIGNAL_SPEC.md` §5) |
| Status | the quality gate's `OK` / `WARN` / `BLOCK`, or `DATA_UNAVAILABLE` when the source is not tradable data for the symbol |
| Hints | concrete next steps (update, repair, import futures, inspect spikes) |
| Cache days | (Dukascopy) the cache's own reason per day: fetched, empty, 404, not yet published, failed |

**Calendar.** Weekend (Friday 17:00 – Sunday 18:00 New York) and the daily break 17:00-18:00
New York are not trading minutes and never count as missing (`data.trading`).

## 2. The quality gate (unchanged rules, D-034)

| Code | Level | Rule |
|---|---|---|
| `EMPTY` | BLOCK | no bars before the moment |
| `ORDER` | BLOCK | timestamps out of order or duplicated |
| `PRICE` | BLOCK | prices missing, ≤ 0, or outside the instrument's range |
| `OHLC` | BLOCK | high/low don't contain open/close |
| `NO_RECENT_DATA` | BLOCK | no bar in the last `level_days` (21) |
| `GAPS` | BLOCK > 50 %, WARN > 2 % | missing trading minutes between the first and the newest bar of the window (holidays included) |
| `STALE` | WARN, **no candidates** | newest bar more than `stale_minutes` (30) trading minutes old |
| `SPIKES` | WARN | §1 |
| `PROXY` | WARN | the series stands in for another instrument |
| `SHORT_HISTORY` | WARN | fewer than 60 % of `history_days` |
| `LARGE_MOVES`, `ROLL`, `SYNTHETIC` | INFO | |

## 3. XAUUSD — bid-only data

Dukascopy gold candles are **bid** prices. A bid-only series is no executable bid/ask feed:
every APEX result on it carries `execution_assumption_limited`. Buys are modelled at
bid + spread — the session median measured from Dukascopy's **ask** candles of the last 10 days
when they are cached, otherwise the configured estimate (`spread_points` 0.30, marked
"estimate"). If the measured spread exceeds the configured round-trip costs, the analysis says
so and the costs used are never below the measured spread.

## 4. NQ / MNQ — futures only

- Tradable plans and backtests need **futures** bars: QuantLab → Data Vault (CSV/Parquet import,
  or a one-time Databento purchase with an explicit quote and approval — never automatic).
  NQ 20 USD/point, MNQ 2 USD/point, tick 0.25; front contract by daily volume; levels never mix
  two contracts; rolls are marked.
- Without futures bars APEX falls back to the Dukascopy USA Tech 100 **CFD** only to show
  structure and zones, labelled as a proxy. Order plans, backtests and validations on it are
  refused with `DATA_UNAVAILABLE` — never a silent proxy.
- MNQ may use NQ bars and the reverse (the same index, the same prices); the point value of the
  chosen market applies, and the substitution is labelled.

## 5. Repair

`Data → Repair` re-checks every trading day without candles in the window and downloads what is
missing (free Dukascopy data only), bid and ask. Each update leaves a report (days asked, with
data, empty, 404, failed, HTTP answers, error). The block disappears only when the data is
actually there.
