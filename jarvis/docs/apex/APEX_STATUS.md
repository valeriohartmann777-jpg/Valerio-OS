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
| Verifiable after the fact | SENTINEL recomputes a stored analysis with later data available; a manipulated stored analysis is caught field by field | `test_sentinel_recomputes_a_stored_analysis_with_later_data`; E2E (3/3 checks passed) |

## Last verified run

`./scripts/check.sh` green — pytest **655** (625 before APEX + 30 in `test_apex.py`), vitest 47,
ruff, mypy ×3, typecheck. E2E **35/35** incl. the APEX step (XAUUSD + MNQ analysed, candidates
journaled, SENTINEL re-check 3/3, SENTINEL in the HQ, track record verdict, no order route).
Screenshots: `docs/screenshots/apex-*.png` (all on SYNTHETIC test data).

## Mocked / not verified here

- All market data in tests and E2E is generated (SYNTHETIC). The container's network policy
  blocks Dukascopy and Databento, so **no APEX result on real market data exists yet**.
- Real runs happen on the owner's Mac with its Dukascopy cache and Data Vault.
- Not verified: macOS, real Dukascopy refresh, real Databento bars through APEX.
