# JARVIS APEX — status and evidence

Branch `claude/jarvis-foundation-mxnsz4`. Rollback point before APEX: `f6419d0`.

> The binding "APEX developer package" named in the owner's request was **not received**.
> Everything below traces to the owner's written request; when the package arrives, its
> requirements are mapped here and gaps are closed first.

## Releases

| Release | Content | State |
|---|---|---|
| APEX-0 | Repository audit, data inventory, constraints, plan (`APEX_AUDIT.md`) | done |
| APEX-1 | Vertical prototype: data + quality gate → structure → zones → counted scenarios → candidates → immutable journal → forward resolution → walk-forward track record; API; QuantLab → APEX; tests | done |
| APEX-2 | JARVIS chat tool `apex_analysis`; SENTINEL re-check of a stored analysis (same data, no look-ahead, outcomes); CIPHER / SENTINEL APEX work in the ULTRON HQ | done |
| APEX-3 | Calibration of the stated scenario rates against later touches (reliability table, Brier skill, Bonferroni-corrected verdict), shown with every track record | done |
| APEX-R | Data recovery & UX repair (D-034): diagnosis of the owner's blocked screen (`APEX_DATA_DIAGNOSIS.md`), cache healing, update reports, data status + repair, corrected gate, measured spread; one trading screen with interactive chart 1m-4h, long/short/no-trade, JARVIS reading, sub-areas | done |
| APEX-4 | The trained S/R model's odds next to counted rates (only where it was confirmed); session / weekday breakdowns; first owner run on real data | planned |

## Requirements → evidence

| Requirement (owner's request) | How | Evidence |
|---|---|---|
| Objective market structure | confirmed swings, HH/HL/LH/LL, trend, BOS / CHoCH (`structure.py`) | `test_a_swing_exists_only_after_its_confirming_bars_closed`, `test_structure_labels_trend_and_breaks` |
| Support / resistance zones | briefing key levels → zones with width, reasons, tests (`zones.py`) | `test_zones_have_width_reasons_and_nearest_first_ids` |
| Probabilistic scenarios | counted hold / break rates from decided touches before the moment; Wilson 95 %, n, days; "insufficient" below 30 | `test_rates_come_with_counts_and_say_insufficient_below_the_minimum` |
| Verifiable entry / SL / TP candidates | fixed rule `zone_reaction`; content ids; immutable journal; conservative resolution; walk-forward track record | `test_a_long_candidate_follows_its_rules_exactly`, `test_candidates_are_rejected_with_their_reason`, `test_resolution_rules_are_conservative`, `test_resolution_matches_the_random_walk_theory`, `test_analyses_are_stored_and_candidates_journaled_immutably`, `test_replayed_candidates_are_resolved_on_later_bars`, `test_the_track_record_uses_the_live_rules_and_is_reproducible` |
| No look-ahead | analysis cut at the moment; unfinished bars dropped; future rewritten → identical result | `test_an_analysis_never_sees_bars_after_its_moment` (XAUUSD, NQ), `test_an_unfinished_higher_timeframe_bar_is_not_used` |
| Data quality | gate with BLOCK / WARN codes; stale data → no candidates; front contract, no mixing across rolls | `test_the_quality_gate_blocks_bad_data_and_stale_data_gets_no_candidates`, `test_futures_bars_keep_the_front_contract_and_never_mix_two`, `test_trading_calendar_skips_weekend_and_daily_break` |
| Backtesting / statistics | same function live and in history; IS / OOS; day-clustered intervals; verdicts never overstate | `test_the_track_record_uses_the_live_rules_and_is_reproducible`, `test_summaries_and_verdicts_never_overstate` |
| Only real or labelled test data | SYNTHETIC label in API, UI, journal, track record; real and test statistics kept apart; proxies labelled | `test_analyses_are_stored…`, `test_replayed_candidates…` (`stats.real` stays empty), E2E APEX step |
| No invented results | every rate counted; no probability per candidate; no edge claim | method doc; tests above |
| No real-money trading | no order path; API has no order / broker / execute route | E2E APEX step (OpenAPI check) |
| Keep data and functions | additive migration 20 with backup first; existing suites unchanged | `test_database.py` incl. rollback rehearsal; full suite |
| Integration JARVIS / QuantLab / ULTRON | QuantLab → APEX; chat tool `apex_analysis`; SENTINEL re-check and CIPHER track record shown in the ULTRON HQ | E2E APEX step; `test_sentinel_recomputes_a_stored_analysis_with_later_data`, `test_cipher_runs_the_track_record_and_the_hq_shows_it`, `test_jarvis_answers_from_apex_and_names_the_data` |
| Probabilities that can be checked | stated rates vs. what later touches did, per bin with intervals; Brier skill vs. a side-only reference | `test_calibration_finds_well_stated_rates_and_their_information`, `test_calibration_uses_only_touches_decided_before_each_one`, `test_the_track_record_reports_the_calibration_of_its_period`; E2E |
| Data errors explained and repaired (APEX-R) | concrete causes with actions; one repair; failed updates visible; empty/404 days never stored as fetched; outages fail loudly | `test_an_outage_fails_loudly_and_the_cache_heals_when_the_api_is_back`, `test_a_cache_poisoned_before_the_fix_is_re_checked`, `test_the_owners_case_is_explained_and_repaired`, `test_every_update_leaves_a_report` |
| Gap and spike checks measure what they claim (APEX-R) | gaps over the covered span; `NO_RECENT_DATA`; spikes on consecutive minutes; a planted hole and bad tick still caught | `test_old_data_is_stale_and_not_reported_as_100_percent_gaps`, `test_gaps_are_measured_where_the_data_is_and_say_where_they_are`, `test_reopen_moves_are_no_spikes_but_a_bad_tick_is`, `test_a_wick_far_beyond_its_neighbours_is_a_spike` |
| Whole pipeline import → visualisation (APEX-R) | Dukascopy format → decoder → cache → gate → structure → zones → candidates → chart (5 timeframes) → data status; local history reused | `test_from_download_to_chart_on_the_real_code_path`, `test_data_status_repair_and_chart_over_http`, `chartView.test.ts`; E2E APEX step |
| Verifiable after the fact | SENTINEL recomputes a stored analysis with later data available; a manipulated stored analysis is caught field by field | `test_sentinel_recomputes_a_stored_analysis_with_later_data`; E2E (3/3 checks passed) |

## Last verified run

APEX-R (2026-10-28): `./scripts/check.sh` green — pytest **668** (incl. `test_apex.py` 30,
`test_apex_data_repair.py` 10, `test_apex_pipeline.py` 3), vitest **54** (incl.
`chartView.test.ts` 7), ruff, mypy ×3, typecheck. E2E **35/35** incl. the rewritten APEX step
(chart in five timeframes, zoom / reset / crosshair, zones, long/short/no-trade, JARVIS reading
and the command-bar draft, data status, SENTINEL 3/3, MNQ, journal, track record with
calibration, no order route). The owner's case was also rebuilt in the real app (local
provider, cache ending four weeks back, Dukascopy unreachable): `STALE` + `NO_RECENT_DATA`
instead of "GAPS 100 %", and `UPDATE_FAILED` / `CACHE_BEHIND` / `MISSING_DAYS` with one Repair
action. Screenshots: `docs/screenshots/apex-*.png` (all on SYNTHETIC test data).

Also fixed on the way: `TrainingService._refresh` set the new report and the run history in
two steps with an await between them, so a status read could see a report next to an empty
history (an intermittent `test_training` failure). Both are now read first and set together.

## Mocked / not verified here

- All market data in tests and E2E is generated (SYNTHETIC). The container's network policy
  blocks Dukascopy and Databento, so **no APEX result on real market data exists yet**.
- Real runs happen on the owner's Mac with its Dukascopy cache and Data Vault.
- Not verified: macOS, a real Dukascopy refresh or repair (the container's network policy
  refuses `jetta.dukascopy.com`), real Databento bars through APEX. Which silent path froze
  the owner's cache (empty answers, 404s or a failed update) is shown by APEX → Data on the
  Mac after the next update.
