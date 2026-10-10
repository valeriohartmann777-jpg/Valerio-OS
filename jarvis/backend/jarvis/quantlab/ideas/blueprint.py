"""Strategy blueprints: a FuturesSpec plus where every rule-defining value came from.

Provenance classes per field:

* ``EXPLICIT_SOURCE`` — stated in the source (cites claims, hence segments).
* ``USER_SPECIFIED`` — the owner said so (a note or an answer).
* ``INFERRED_NONCRITICAL`` — inferred, and it doesn't change the hypothesis.
* ``DEFAULT_RESEARCH_ASSUMPTION`` — a labelled research default (costs, slippage,
  sizing, validation, or a catalog definition the owner accepted as default).
* ``MISSING_BLOCKING`` — not defined; blocks the test until resolved.

Rule-defining (critical) fields can only be source-explicit, user-specified or a
catalog default the owner accepted; anything else is ``MISSING_BLOCKING``. Status
is computed, never declared: ``DRAFT`` (no testable rule), ``INVALID``,
``NEEDS_DEFINITION``, ``READY_FOR_DATA``, ``READY_TO_TEST`` (data in the library).
"""

from __future__ import annotations

import copy
from typing import Any

from jarvis.llm.base import ToolDefinition
from jarvis.quantlab.futures.spec import (
    FuturesSpec,
    FuturesSpecError,
    describe,
    parse,
    range_minutes_of,
)
from jarvis.quantlab.ideas import catalog
from jarvis.quantlab.ideas.catalog import BY_ID, apply_patch, get_path

CLASSES = (
    "EXPLICIT_SOURCE",
    "USER_SPECIFIED",
    "INFERRED_NONCRITICAL",
    "MISSING_BLOCKING",
    "DEFAULT_RESEARCH_ASSUMPTION",
)
STATUSES = ("DRAFT", "NEEDS_DEFINITION", "READY_FOR_DATA", "READY_TO_TEST", "INVALID")
ENUM_OPTIONS = {
    "rule.direction": ["long", "short", "both"],
    "rule.sides": ["fade_highs", "fade_lows", "both"],
    "rule.entry": ["stop_through_range", "close_beyond_range"],
    "rule.reclaim": ["close_back_inside", "stop_back_through"],
    "rule.level": ["opening_range", "prior_session"],
    "instrument.product": ["NQ", "MNQ", "ES", "MES"],
    "session.start": ["09:30", "08:30"],
    "exits.stop.type": ["range_opposite", "ticks", "setup_extreme", "none"],
    "exits.target.type": ["r_multiple", "ticks", "none"],
}
STANDARD_DEFAULTS = {
    "costs.commission_per_contract_side": "Typical retail rate, not your broker's quote.",
    "costs.exchange_fees_per_contract_side": "Approximate CME + NFA fees.",
    "execution.slippage_ticks": "One tick per market/stop fill; stress tests multiply it.",
    "sizing.account_capital": "Only the denominator for returns; no margin model.",
    "sizing.contracts": "One contract: results scale linearly, risk does not.",
}

SUBMIT_BLUEPRINT = ToolDefinition(
    name="submit_blueprint",
    description=(
        "Submit the formal rule (a FuturesSpec), the provenance of each rule-defining field, "
        "the definition chosen for each ambiguous term, open questions and unsupported parts. "
        "Set no_rule when the source contains no testable trading rule."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "no_rule": {"type": "boolean"},
            "summary": {"type": "string"},
            "spec": {"type": "object"},
            "provenance": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "class": {"type": "string", "enum": list(CLASSES)},
                        "claim_ids": {"type": "array", "items": {"type": "string"}},
                        "note_ids": {"type": "array", "items": {"type": "string"}},
                        "note": {"type": "string"},
                    },
                    "required": ["field", "class"],
                },
            },
            "ambiguities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "term": {"type": "string"},
                        "chosen": {"type": ["string", "null"]},
                        "basis": {"type": "string", "enum": ["source", "user", "default", "open"]},
                    },
                    "required": ["term", "basis"],
                },
            },
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"field": {"type": "string"}, "question": {"type": "string"}},
                },
            },
            "unsupported": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"feature": {"type": "string"}, "reason": {"type": "string"}},
                },
            },
        },
        "required": ["no_rule", "summary"],
    },
)


class BlueprintError(ValueError):
    pass


def critical_fields(spec: FuturesSpec) -> list[str]:
    rule = spec.rule
    fields = [
        "instrument.product",
        "rule.type",
        "session.start",
        "exits.stop.type",
        "exits.target.type",
    ]
    per_rule = {
        "opening_range_breakout": ["rule.range_minutes", "rule.entry", "rule.direction"],
        "ma_crossover": ["rule.fast", "rule.slow", "rule.direction"],
        "level_sweep_reclaim": [
            "rule.level",
            "rule.sides",
            "rule.sweep_min_ticks",
            "rule.reclaim",
            "rule.reclaim_within_bars",
        ],
        "opening_range_retest": [
            "rule.range_minutes",
            "rule.direction",
            "rule.retest_within_bars",
            "rule.retest_tolerance_ticks",
        ],
    }
    fields += per_rule[rule.type]
    if rule.type == "level_sweep_reclaim" and range_minutes_of(rule) is not None:
        fields.append("rule.range_minutes")
    if spec.exits.stop.type in ("ticks", "setup_extreme"):
        fields.append("exits.stop.ticks")
    if spec.exits.target.type != "none":
        fields.append("exits.target.value")
    return fields


def _term_fields(term: catalog.Term) -> set[str]:
    return {path for alt in term.alternatives for path in alt.patch}


def compile_blueprint(
    raw: dict[str, Any],
    *,
    claims: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    detected: dict[str, Any],
) -> dict[str, Any]:
    """Check a submit_blueprint payload and derive provenance, assumptions and status."""
    claim_ids = {f"c{n}": c for n, c in enumerate(claims, start=1)}
    note_ids = {f"n{n}" for n in range(1, len(notes) + 1)}
    summary = str(raw.get("summary") or "").strip()[:1500]
    unsupported = _unsupported(raw, detected)
    if raw.get("no_rule"):
        return {
            "status": "DRAFT",
            "spec": {},
            "spec_sha256": None,
            "summary": summary,
            "provenance": {},
            "ambiguities": [],
            "questions": [],
            "unsupported": unsupported,
            "what_it_does": [],
            "illustration": [],
            "reason": "No testable trading rule in the source (RULES_UNCLEAR).",
        }
    spec_raw = raw.get("spec")
    if not isinstance(spec_raw, dict):
        raise BlueprintError("spec must be a FuturesSpec object (or set no_rule)")
    spec_raw = copy.deepcopy(spec_raw)
    spec_raw.pop("assumptions", None)  # rebuilt from provenance below
    problems: list[str] = []

    # Ambiguous terms: chosen definitions must exist, be supported and match the spec.
    choices: dict[str, dict[str, Any]] = {}
    for item in raw.get("ambiguities") or []:
        term = BY_ID.get(str(item.get("term")))
        if term is None:
            problems.append(f"ambiguity '{item.get('term')}' isn't a catalog term")
            continue
        basis = item.get("basis")
        chosen = item.get("chosen")
        if basis != "open":
            alt = term.alternative(str(chosen)) if chosen else None
            if alt is None:
                problems.append(
                    f"{term.id}: chosen must be one of {[a.id for a in term.alternatives]}"
                )
                continue
            if not alt.supported:
                problems.append(f"{term.id}: '{alt.id}' isn't supported by the rule language")
                continue
            for path, value in alt.patch.items():
                if get_path(spec_raw, path) != value:
                    problems.append(
                        f"{term.id}: '{alt.id}' means {path} = {value!r}, but the spec has "
                        f"{get_path(spec_raw, path)!r}"
                    )
        choices[term.id] = {"chosen": chosen if basis != "open" else None, "basis": basis}
    try:
        spec = parse({**spec_raw, "assumptions": []})
    except FuturesSpecError as exc:
        problems.append(exc.message)
        raise BlueprintError("; ".join(problems[:20])) from exc
    rule_type = spec.rule.type
    seen = detected.get("terms", {})
    named = [*seen, *(t for t in choices if t not in seen)]  # detected, plus any the model chose
    relevant = [BY_ID[t] for t in named if catalog.relevant(BY_ID[t], rule_type)]
    for term in relevant:
        if term.id not in choices:
            problems.append(
                f"the source uses {term.name}: add it to ambiguities (chosen definition and "
                "basis source/user/default/open)"
            )

    # Provenance entries.
    provenance: dict[str, dict[str, Any]] = {}
    for item in raw.get("provenance") or []:
        path = str(item.get("field") or "")
        cls = item.get("class")
        if get_path(spec.model_dump(mode="json"), path) is None:
            problems.append(f"provenance field '{path}' isn't in the spec")
            continue
        if cls not in CLASSES:
            problems.append(f"{path}: class must be one of {', '.join(CLASSES)}")
            continue
        cites = [c for c in (item.get("claim_ids") or []) if isinstance(c, str)]
        bad = [c for c in cites if c not in claim_ids]
        if bad:
            problems.append(f"{path}: unknown claim id(s) {bad}")
        if cls == "EXPLICIT_SOURCE":
            if not cites:
                problems.append(f"{path}: EXPLICIT_SOURCE needs the claim ids that state it")
            elif any(
                claim_ids[c]["kind"] in ("PERFORMANCE_CLAIM", "INSTRUCTION_TO_AI", "MARKETING")
                for c in cites
                if c in claim_ids
            ):
                problems.append(f"{path}: performance, marketing or injected text defines no rule")
        notes_cited = [n for n in (item.get("note_ids") or []) if isinstance(n, str)]
        if cls == "USER_SPECIFIED" and not (set(notes_cited) & note_ids):
            problems.append(f"{path}: USER_SPECIFIED needs the owner note id(s) (n1, …)")
        provenance[path] = {
            "class": cls,
            "claim_ids": [claim_ids[c]["id"] for c in cites if c in claim_ids],
            "segment_ids": sorted(
                {s for c in cites if c in claim_ids for s in claim_ids[c]["segment_ids"]}
            ),
            "note_ids": notes_cited,
            "note": str(item.get("note") or "")[:300],
        }
    if problems:
        raise BlueprintError("; ".join(problems[:20]))

    ambiguities = []
    for term in relevant:
        choice = choices[term.id]
        ambiguities.append(
            {
                **term.as_dict(),
                "segment_ids": seen.get(term.id, []),
                "chosen": choice["chosen"],
                "basis": choice["basis"],
            }
        )
        for path in _term_fields(term):
            current = provenance.get(path)
            if current and current["class"] in ("EXPLICIT_SOURCE", "USER_SPECIFIED"):
                continue  # the source or the owner already defined it
            if choice["basis"] == "open":
                provenance[path] = _missing(f"{term.name}: definition not chosen", term.id)
            elif choice["basis"] == "default":
                provenance[path] = {
                    "class": "DEFAULT_RESEARCH_ASSUMPTION",
                    "claim_ids": [],
                    "segment_ids": [],
                    "note_ids": [],
                    "term": term.id,
                    "note": f"{term.name}: research default '{choice['chosen']}', labelled — "
                    "not what the source said",
                }
    return finalize(
        spec.model_dump(mode="json"),
        provenance,
        ambiguities,
        unsupported,
        summary,
        extra_questions=raw.get("questions") or [],
    )


def _missing(note: str, term: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "class": "MISSING_BLOCKING",
        "claim_ids": [],
        "segment_ids": [],
        "note_ids": [],
        "note": note,
    }
    if term:
        entry["term"] = term
    return entry


def _unsupported(raw: dict[str, Any], detected: dict[str, Any]) -> list[dict[str, Any]]:
    out = [
        {
            "feature": catalog.UNSUPPORTED_NAMES[key],
            "reason": catalog.REASONS[key],
            "segment_ids": sids,
            "origin": "catalog",
        }
        for key, sids in detected.get("unsupported", {}).items()
    ]
    for item in raw.get("unsupported") or []:
        if isinstance(item, dict) and item.get("feature"):
            out.append(
                {
                    "feature": str(item["feature"])[:120],
                    "reason": str(item.get("reason") or "")[:300],
                    "segment_ids": [],
                    "origin": "model",
                }
            )
    return out


def finalize(
    spec_raw: dict[str, Any],
    provenance: dict[str, dict[str, Any]],
    ambiguities: list[dict[str, Any]],
    unsupported: list[dict[str, Any]],
    summary: str,
    extra_questions: list[Any] | None = None,
) -> dict[str, Any]:
    """Critical-field gate, assumptions from provenance, status, questions, plain language."""
    spec = parse({**spec_raw, "assumptions": []})
    provenance = dict(provenance)
    for path in critical_fields(spec):
        entry = provenance.get(path)
        if entry is None:
            provenance[path] = _missing("not stated in the source or by you")
        elif entry["class"] in ("INFERRED_NONCRITICAL",) or (
            entry["class"] == "DEFAULT_RESEARCH_ASSUMPTION" and not entry.get("term")
        ):
            provenance[path] = _missing(
                f"defines the hypothesis — a guess isn't enough ({entry['note'] or entry['class']})"
            )
    for path, note in STANDARD_DEFAULTS.items():
        provenance.setdefault(
            path,
            {
                "class": "DEFAULT_RESEARCH_ASSUMPTION",
                "claim_ids": [],
                "segment_ids": [],
                "note_ids": [],
                "note": note,
            },
        )
    assumptions = []
    for path, entry in sorted(provenance.items()):
        cls = entry["class"]
        if cls == "MISSING_BLOCKING":
            assumptions.append(
                {"field": path[:80], "state": "unknown", "note": entry["note"][:400]}
            )
        elif cls in ("DEFAULT_RESEARCH_ASSUMPTION", "INFERRED_NONCRITICAL"):
            assumptions.append(
                {"field": path[:80], "state": "assumed", "note": entry["note"][:400]}
            )
        elif cls == "USER_SPECIFIED":
            assumptions.append({"field": path[:80], "state": "confirmed", "note": "you said so"})
    final = parse({**spec_raw, "assumptions": assumptions[:60]})
    blocking = sorted(p for p, e in provenance.items() if e["class"] == "MISSING_BLOCKING")
    questions = _questions(final, provenance, ambiguities, extra_questions or [])
    status = "NEEDS_DEFINITION" if blocking else "READY_FOR_DATA"
    return {
        "status": status,
        "spec": final.model_dump(mode="json"),
        "spec_sha256": final.sha256(),
        "summary": summary,
        "provenance": provenance,
        "ambiguities": ambiguities,
        "questions": questions,
        "unsupported": unsupported,
        "blocking": blocking,
        "what_it_does": describe(final),
        "illustration": illustrate(final),
    }


def _questions(
    spec: FuturesSpec,
    provenance: dict[str, dict[str, Any]],
    ambiguities: list[dict[str, Any]],
    extra: list[Any],
) -> list[dict[str, Any]]:
    """One grouped set: open catalog terms first, then other blocking fields."""
    out: list[dict[str, Any]] = []
    covered: set[str] = set()
    for amb in ambiguities:
        if amb["basis"] != "open":
            continue
        term = BY_ID[amb["id"]]
        covered |= _term_fields(term)
        out.append(
            {
                "kind": "term",
                "term": term.id,
                "question": f"{term.name}: {term.why}",
                "default": term.default,
                "material": term.material,
                "options": [
                    {"id": a.id, "label": a.label, "definition": a.definition}
                    for a in term.alternatives
                    if a.supported
                ],
            }
        )
    dumped = spec.model_dump(mode="json")
    for path, entry in sorted(provenance.items()):
        if entry["class"] != "MISSING_BLOCKING" or path in covered:
            continue
        question: dict[str, Any] = {
            "kind": "field",
            "field": path,
            "question": f"{path}: {entry['note']}",
            "current": get_path(dumped, path),
        }
        if path in ENUM_OPTIONS:
            question["options"] = [{"id": v, "label": v} for v in ENUM_OPTIONS[path]]
        else:
            question["input"] = "number"
        out.append(question)
    for item in extra[:8]:
        if isinstance(item, dict) and item.get("question"):
            out.append(
                {
                    "kind": "note",
                    "field": str(item.get("field") or ""),
                    "question": str(item["question"])[:300],
                }
            )
    return out


def resolve(blueprint: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    """Apply the owner's grouped answer deterministically (no model): a new blueprint."""
    spec_raw = copy.deepcopy(blueprint["spec"])
    spec_raw.pop("assumptions", None)
    provenance = copy.deepcopy(blueprint["provenance"])
    ambiguities = copy.deepcopy(blueprint["ambiguities"])
    accept = bool(answers.get("accept_defaults"))
    choices = answers.get("choices") or {}
    values = answers.get("values") or {}
    for amb in ambiguities:
        if amb["basis"] != "open":
            continue
        term = BY_ID[amb["id"]]
        picked = choices.get(term.id) or (term.default if accept else None)
        if picked is None:
            continue
        alt = term.alternative(str(picked))
        if alt is None or not alt.supported:
            raise BlueprintError(f"{term.id}: '{picked}' isn't an available definition")
        spec_raw = apply_patch(spec_raw, alt.patch)
        by_user = term.id in choices
        amb.update(chosen=alt.id, basis="user" if by_user else "default")
        for path in _term_fields(term):
            if path in alt.patch or provenance.get(path, {}).get("term") == term.id:
                provenance[path] = {
                    "class": "USER_SPECIFIED" if by_user else "DEFAULT_RESEARCH_ASSUMPTION",
                    "claim_ids": [],
                    "segment_ids": [],
                    "note_ids": [],
                    "term": term.id,
                    "note": (
                        f"you chose '{alt.label}'"
                        if by_user
                        else f"research default '{alt.label}', accepted by you"
                    ),
                }
    for path, value in values.items():
        if path not in provenance or provenance[path]["class"] != "MISSING_BLOCKING":
            raise BlueprintError(f"{path} isn't an open question")
        if path in ENUM_OPTIONS and value not in ENUM_OPTIONS[path]:
            raise BlueprintError(f"{path}: choose one of {ENUM_OPTIONS[path]}")
        spec_raw = apply_patch(spec_raw, {path: value})
        provenance[path] = {
            "class": "USER_SPECIFIED",
            "claim_ids": [],
            "segment_ids": [],
            "note_ids": [],
            "note": f"you set {value!r}",
        }
    try:
        return finalize(
            spec_raw,
            provenance,
            ambiguities,
            blueprint["unsupported"],
            blueprint.get("summary") or "",
        )
    except FuturesSpecError as exc:
        raise BlueprintError(exc.message) from exc


def illustrate(spec: FuturesSpec) -> list[str]:
    """A worked example with made-up prices — shows the mechanics, proves nothing."""
    rule = spec.rule
    tick = 0.25 if spec.instrument.product in ("NQ", "MNQ", "ES", "MES") else 0.01
    head = "Illustration with made-up prices — not market data:"
    if rule.type == "level_sweep_reclaim":
        k = rule.sweep_min_ticks * tick
        return [
            head,
            "Level high 20,000.00. A bar trades up to "
            f"{20000 + k + tick:,.2f} (≥ {k:g} points beyond) — the sweep.",
            "A later bar closes at 19,998.00, back below 20,000.00 → short at the next bar's open."
            if rule.reclaim == "close_back_inside"
            else "Price then trades back down through the level → the stop order sells there.",
            "The stop sits beyond the sweep's highest price; the target is set in multiples of "
            "that risk.",
        ]
    if rule.type in ("opening_range_breakout", "opening_range_retest"):
        return [
            head,
            "Opening range 19,980.00–20,000.00 (known when its last bar closes).",
            "A bar closes at 20,004.00 → breakout above."
            if rule.type == "opening_range_retest"
            else "Price trades to 20,000.25 → the buy stop fills (plus slippage).",
            "A later bar dips to 20,000.50 and closes at 20,006.00 → retest holds, buy next open."
            if rule.type == "opening_range_retest"
            else "Stop and target follow the exits; flat by the session's flatten time.",
        ]
    return [
        head,
        "Fast average 20,001.00 crosses above slow average 20,000.50 when a bar completes → "
        "buy at the next bar's open; the opposite cross reverses.",
    ]
