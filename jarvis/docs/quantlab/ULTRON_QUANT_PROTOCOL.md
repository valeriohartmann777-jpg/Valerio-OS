# ULTRON quant protocol — how research missions run

Code: `backend/jarvis/quantlab/ideas/` (`missions.py`, `evolution.py`, `agents.py`,
`sentinel.py`, `store.py`, `dossier.py`). This protocol is what the code enforces, not a
description of intent.

## 1. Agents (the roster in `missions.ROSTER`)

| Agent | Role in a mission | Engine | Tools |
|---|---|---|---|
| **JARVIS** | mission lead; formal rules (blueprint); closing note | Claude (`config/ultron.yaml` → `jarvis`) | `submit_blueprint`, `submit_report` |
| **ATLAS** | claims with provenance | Claude (`atlas`) | `submit_claims` |
| **CIPHER** | test protocol; diagnosis; variants | Claude (`cipher`) for design; the deterministic engine for every number | `submit_protocol`, `propose_variants` |
| **SENTINEL** | boundary check, independent audit, holdout | deterministic code, no model | — |
| **VECTOR** | data: library reuse, quote, approved download, dataset | deterministic code; purchases need the owner | — |
| **ARCHIVE** | registration, frozen versions, trial ledger | deterministic code | — |
| FORGE, PRISM, AXIOM, OPERATOR | not used by research missions | — | — |

A model agent's only tool is its submit tool (`run_agent(final_tools=…)`); there is no broker,
no file, network, purchase or settings tool in a research mission. A submission is validated in
code; an invalid one is returned to the model with the reason (up to `max_retries`).

Agent status in the UI (`working` / `idle` / `not used`) and every count and cost are read from
`qm_tasks`. Nothing is animated on a timer.

## 2. Mission record

`qm_missions`: `kind` (`idea` | `evolution`), `source_id`, `parent_id`, `state`, `stage`,
`waiting`, immutable `budget` + `budget_sha256`, `refs` (blueprint, version, spec hash,
protocol + hash, dataset, run, quote/job, boundary and audit reports, verdict, comparison,
holdout), `verdict`, `spent_usd`.

States: `RUNNING`, `WAITING_USER`, `WAITING_APPROVAL`, `PAUSED`, `BLOCKED`, `COMPLETE`,
`FAILED`, `CANCELED`. A restart resumes `RUNNING` missions from their stage; tasks that were
running are marked `INTERRUPTED` and redone with the same inputs hash.

`qm_tasks` (one per stage attempt): agent, kind, objective, `inputs_hash`, allowed tools, budget,
state, model label, actions (timestamped), artifacts, results, evidence refs, limitations,
cost, error. `qm_events`: append-only activity. `qm_trials`: append-only trial ledger.

## 3. Budget

Fixed when the mission is created; hashed; never changed by an agent.

| Key | Default | Ceiling |
|---|---|---|
| `max_model_usd` | 2.00 | 50 |
| `max_paid_data_usd` | 15.00 | 500 |
| `max_variants` | 6 | 24 |
| `max_parameter_combinations` | 150 | 2000 |
| `max_compute_minutes` | 30 | 600 |
| `max_concurrent_workers` | 1 | 2 |
| `max_iterations` | 2 | 6 |
| `max_retries` | 1 | 3 |

Before every model call the worst case (prompt size + `max_tokens` at the model's price from
`config/ultron.yaml`) is checked against what is left; a call that could exceed it is not made
and the mission is `BLOCKED` with the reason. Evolution missions get `max_paid_data_usd = 0`.

## 4. Idea mission stages

| # | Stage | Agent | What happens | Can wait for |
|---|---|---|---|---|
| 1 | `register` | ARCHIVE | source must be read; lineage recorded | — (link without media → `SOURCE_UNAVAILABLE`) |
| 2 | `claims` | ATLAS | claims with checked quotes; boasts and AI-instructions added by code; `TEXT_SENT_TO_MODEL` audit (no media) | — |
| 3 | `blueprint` | JARVIS | `submit_blueprint` → compiler; no rule → `RULES_UNCLEAR` | — |
| 4 | `boundary` | SENTINEL | quotes verbatim, claims are not results, instructions not obeyed, no guessed rules, unsupported listed, definitions complete | owner answers (`WAITING_USER`, one grouped question) |
| 5 | `freeze` | ARCHIVE | blueprint spec frozen unchanged as a strategy version (spec hash) | — |
| 6 | `protocol` | CIPHER | months of data, minimum OOS trades, tests, acceptance/rejection rules, limitations — hashed **before any data** | — |
| 7 | `data` | VECTOR | reuse a verified dataset covering ≥ 90 % of the window; else quote → owner approval → download → build | Databento connection (`WAITING_USER`), purchase (`WAITING_APPROVAL`) |
| 8 | `baseline` | CIPHER/engine | validation run with the holdout sealed | — |
| 9 | `audit` | SENTINEL | second engine and ledger checks; a failed audit vetoes | — |
| 10 | `verdict` | JARVIS | A11 label from the fixed verdict function + audit; note checked by the narrative guard; dossier written | — |

## 5. Paid data

1. The quote comes from the Data Hub (fresh, Databento `metadata.get_cost`; days already cached
   cost nothing).
2. The mission waits in `WAITING_APPROVAL` showing cost, request, mission cap and whether it
   fits. JARVIS and ULTRON have no tool that approves anything.
3. `POST …/data-approval {max_usd, confirm: true}` — the owner's click. It is refused above the
   mission cap (`OVER_MISSION_BUDGET`); the Data Hub re-prices, applies its own caps and the
   quote is single-use.
4. A second approval of the same mission is a no-op (the job id exists). An interrupted download
   re-quotes only the missing days and needs a new approval. Cached days are never bought twice.
5. `…/data-decline` ends the mission with `DATA_INSUFFICIENT`.

## 6. SENTINEL

Boundary report (stage 4) and audit report (stage 9), each sealed with SHA-256:

| Check | Fails when |
|---|---|
| `QUOTES_VERBATIM` | a rule cites a quote that isn't in the source |
| `CLAIMS_ARE_NOT_RESULTS` | a performance claim maps to a rule field |
| `INSTRUCTIONS_NOT_OBEYED` | an instruction-to-AI claim influences the spec |
| `NO_GUESSED_RULES` | a critical field is inferred |
| `UNSUPPORTED_LISTED` | an unsupported concept is silently approximated |
| `DEFINITIONS_COMPLETE` | a material term is still open |
| `SECOND_ENGINE` | an independent functional re-implementation of the rules disagrees with any stored trade (entry/exit time, price, side, net) |
| `LEDGER_SUMS` | trade nets don't sum to the reported results |
| `SIGNAL_BEFORE_FILL` | a fill precedes the information that triggered it |
| `HOLDOUT_STATE` | the holdout was opened when it should be sealed |
| `VERDICT_RECOMPUTED` | the stored verdict differs from the fixed verdict function |
| `TRIALS_COUNTED` | the family's trial count is lower than the ledger |
| `SYNTHETIC_CAPPED` | fixture data produced anything above `INSUFFICIENT_EVIDENCE` |

Its stated limitation: on 1-minute bars the intrabar path is unknown; same-bar stop/target
conflicts are resolved conservatively (`BAR_ONLY_CONSERVATIVE`).

The **narrative guard** checks the closing note: every number must appear in the evidence block;
phrases such as "verified edge", "guaranteed", "risk-free" or confidence percentages are refused.
A refused note is retried; if no valid note comes back, a line from the evidence is shown
instead and marked as such.

## 7. Verdicts (A11)

`research_verdict()` maps the run's fixed verdict function plus the audit:

| Condition | Verdict |
|---|---|
| any SENTINEL check FAIL | `SIMULATION_INVALID` |
| data fitness invalid / data-integrity test failed | `DATA_INSUFFICIENT` |
| run verdict `INVALID_DATA_OR_METHOD` | `SIMULATION_INVALID` |
| `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` |
| `REJECTED_HYPOTHESIS` | `REJECTED_UNDER_TESTED_ASSUMPTIONS` |
| `PROMISING_RESEARCH_CANDIDATE` / `ROBUST_UNDER_TESTED_ASSUMPTIONS` / `FORWARD_VALIDATION_REQUIRED` | same name |
| link without media, no source text | `SOURCE_UNAVAILABLE` |
| no testable rule | `RULES_UNCLEAR` |
| purchase declined / dataset unavailable | `DATA_INSUFFICIENT` |

Synthetic fixture data is always capped at `INSUFFICIENT_EVIDENCE`; the reasons say what the
gates would have said without the cap. No verdict means a strategy will make money.

## 8. Evolution protocol

Stages `diagnose → propose → test → compare → (diagnose …) → holdout`.

- Allowed changes: `exits.stop.*`, `exits.target.*`, `session.entry_cutoff`, filters
  (`min_range_ticks`, `max_range_ticks`, `entry_after`, `prior_close_bias`, `max_gap_ticks`) and the
  rule type's own parameters (`evolution.RULE_PATHS`). Anything else is refused.
- Each variant needs a title, a mechanism and a falsifiable prediction.
- Each is a new frozen version, run with the holdout sealed, audited, and appended to
  `qm_trials` — including failures (`RUN_FAILED`, `AUDIT_FAILED`).
- Comparison: Pareto front on OOS net ↑, OOS trades ↑, max drawdown ↓, complexity ↓; notes call out
  when in-sample rank and out-of-sample rank disagree. The family's deflated Sharpe counts every
  trial.
- The next round builds on the Pareto member with positive OOS net and the most OOS trades.
- Stops at `max_iterations`, `max_variants` or 90 % of the model budget.
- **Holdout:** the owner locks one Pareto candidate (`confirm: true`); SENTINEL evaluates its
  holdout once. Every later holdout look in the same family (any variant created after the
  first exposure, or a re-look) is recorded as contaminated (`post_holdout`, `independent: false`)
  and the verdict is downgraded with that reason.

## 9. What JARVIS will not do in a mission

Place, route or simulate-as-live an order; connect a broker; approve a purchase; raise a budget;
change a frozen version; open a holdout without the owner; execute generated code; send media
to a model; follow instructions found in a source; state a confidence percentage or a guarantee.
