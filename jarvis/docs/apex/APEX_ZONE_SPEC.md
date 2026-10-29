# APEX — zone specification (zonemap 1.1.0)

Code: `backend/jarvis/apex/zonemap.py` (the module docstring is the binding definition);
tests: `tests/test_apex_zonemap.py`, `tests/test_apex_setups.py::test_cached_zone_maps_*`.

## Causality

Zones, ATR and lifecycle are built at the **last 15-minute close before `as_of`**, from the 14
days before it; only the placement (side, distance, rank) uses the price at `as_of`. Nothing
after the moment is read: the same moment gives the same map whatever came later (test:
`test_the_zone_map_at_a_moment_ignores_every_later_bar`). The map of a 15-minute bar is cached
exactly, so a walk through history can evaluate every 5-minute close.

## Candidates (each with formation and confirmation time)

- confirmed 15-minute swings (3 bars each side) and 1-hour swings (2 bars) — a pivot exists only
  once its right-hand bars have closed; the zone is the pivot bar's wick, 0.1–0.5 ATR wide;
- previous trading day's high / low (known at the next day's 18:00 New York start), previous
  week, last completed Asia / London / New York session (known at the session's end);
- displacement origins (15-minute bar ≥ 2 ATR, close in its outer quarter);
- major round numbers near the price;
- previous day's bar-volume profile POC / value area — **proxy**, shown, never scored.

Candidates within 0.15 ATR merge (≤ 0.8 ATR wide); zones never overlap.

## Episodes and lifecycle

A touch starts an episode only after price had left by ≥ 0.25 ATR (one visit = one episode).
Outcome: `rejection` (≥ 1.5 ATR away within 8 bars), `sweep_reclaim` (through the far edge by
≥ 0.05 ATR first), `weak`, `pending`. Two consecutive 15-minute closes beyond the far edge by
0.1 ATR **break** the zone; it **flips** only after that acceptance and a rejected retest from
the other side; two closes back on the original side make it a **fakeout** (counter-evidence).

States: `CANDIDATE · CONFIRMED · ACTIVE · TESTED · WEAKENING · BROKEN · FLIPPED · EXPIRED`
(no touch for 10 days). Every transition is recorded with time and reason.

## Score and ranking

Quality score 0–100 from capped families (structure ≤ 35, price reaction ≤ 35, freshness ≤ 15,
width ≤ 10, minus counter-evidence); volume profile, delta and absorption contribute **zero**
points (proxy / unavailable). **The score is evidence strength, never a probability.**

Rank 1–4 per side among non-broken, non-expired zones with score ≥ 30, by score × relevance
(1 within 6 ATR, else 6 / distance, at least 0.3). Fewer than four qualify → fewer are shown
with a note ("no zone is invented to fill four"). Buy zone / sell zone = the strongest ranked
support / resistance within reach (`WATCH`), else the top one (`FAR`).

## Names

`S1–S4 / R1–R4` always mean the ranked zone map. The key levels of the older `zone_reaction`
and `zone_rejection_5m` rules are named `KS1… / KR1…` so one name never means two zones.

## Snapshots

Every analysis stores its zone map once (`apex_zone_snapshots`, SHA-256, immutable;
`GET /apex/zones?market=&snapshot=`).
