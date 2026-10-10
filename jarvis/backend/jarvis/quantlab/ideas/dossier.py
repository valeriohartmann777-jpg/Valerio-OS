"""The strategy dossier: source claims, formal hypothesis and empirical evidence — kept apart.

Built only from stored records (mission, source, blueprint, run summary, audits, trial
ledger). The three fact planes never mix: what the source *claimed* (including any
win-rate boast) is listed as claims, never as results; results come only from the
deterministic engine on a named dataset, with SENTINEL's audit beside them.
"""

from __future__ import annotations

from typing import Any

from jarvis.quantlab.ideas.claims import clock

DISCLAIMER = (
    "Research, not investment advice. No verdict here means a strategy will make money; "
    "past simulated results don't predict live results. JARVIS places no orders."
)
FIDELITY = (
    "BAR_ONLY_CONSERVATIVE — 1-minute OHLCV bars: the order of high and low inside a minute "
    "is unknown, ambiguous minutes are resolved against the strategy, the spread is inside "
    "the slippage assumption."
)


def _money(value: Any) -> str:
    if value is None:
        return "n/a"
    sign = "−" if float(value) < 0 else ""
    return f"{sign}${abs(float(value)):,.2f}"


def build(
    mission: dict[str, Any],
    verdict: dict[str, Any],
    source: dict[str, Any] | None,
    blueprint: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    trials: list[dict[str, Any]],
    *,
    engine_version: str,
) -> tuple[str, dict[str, Any]]:
    refs = mission["refs"]
    lines = [f"# Strategy dossier — {mission['title']}", ""]
    label = verdict["label"].replace("_", " ")
    lines += [f"**Research verdict: {label}**", ""]
    for reason in verdict["reasons"]:
        lines.append(f"- {reason}")
    if verdict.get("note") and verdict["note"].get("text"):
        checked = "checked by SENTINEL" if verdict["note"].get("checked") else "plain summary"
        lines += ["", f"> {verdict['note']['text']}", f"> — JARVIS ({checked})"]
    if summary and summary.get("fixture"):
        lines += [
            "",
            "⚠ **SYNTHETIC FIXTURE DATA** — an engineering check of the pipeline, not market evidence.",
        ]
    lines += ["", f"_{DISCLAIMER}_", ""]

    # 1. Source claims
    lines += ["## 1 · What the source claims (not tested)", ""]
    if source:
        lines.append(
            f"Source: {source['kind']} “{source['title']}” (`{source['id']}`), status {source['status']}."
        )
        coverage = (source.get("meta") or {}).get("coverage") or {}
        if coverage.get("speech"):
            sp = coverage["speech"]
            lines.append(
                f"Speech: {sp.get('provider')} ({sp.get('transcribed_seconds')} s transcribed of "
                f"{sp.get('voiced_seconds')} s with speech energy; timestamps {sp.get('timestamps')})."
            )
        if coverage.get("onscreen"):
            on = coverage["onscreen"]
            lines.append(
                f"On-screen text: {on.get('provider')} on {on.get('sampled')} sampled frames, "
                f"{on.get('keyframes')} keyframes kept."
            )
        lines.append("")
        by_id = {s["id"]: s for s in source.get("segments", [])}
        for claim in source.get("claims", []):
            where = []
            for sid in claim["segment_ids"]:
                seg = by_id.get(sid)
                if seg and seg.get("start_ms") is not None:
                    where.append(
                        f"{seg['modality']} {clock(seg['start_ms'])}–{clock(seg['end_ms'])}"
                    )
                elif seg:
                    where.append(seg["modality"])
            quote = f" — “{claim['quote']}”" if claim.get("quote") else ""
            lines.append(
                f"- **{claim['kind']}** ({claim['claim_status']}, {claim['extraction_confidence']}): "
                f"{claim['content']}{quote} [{'; '.join(where)}]"
            )
    else:
        lines.append("No source (the mission started from a frozen strategy).")
    lines.append("")

    # 2. Formal hypothesis
    lines += ["## 2 · Formal hypothesis (what was tested)", ""]
    if blueprint and blueprint.get("spec"):
        lines.append(
            f"Spec SHA-256 `{refs.get('spec_sha256') or blueprint.get('spec_sha256')}`; "
            f"version `{refs.get('version_id', '—')}`."
        )
        lines.append("")
        for path, entry in sorted(blueprint.get("provenance", {}).items()):
            if path.startswith(
                ("rule.", "exits.", "session.start", "instrument.product", "filters.")
            ):
                lines.append(
                    f"- `{path}` — {entry['class']}"
                    + (f" ({entry['note']})" if entry.get("note") else "")
                )
        if blueprint.get("unsupported"):
            lines += ["", "Not testable with the current rule language:"]
            lines += [f"- {u['feature']}: {u['reason']}" for u in blueprint["unsupported"]]
    else:
        lines.append("No formal rule was produced.")
    if refs.get("protocol"):
        p = refs["protocol"]
        lines += [
            "",
            f"Pre-registered protocol (`{p['sha256'][:12]}…`, frozen before data): "
            f"{p['months']} months, ≥ {p['min_trades_oos']} out-of-sample trades.",
        ]
        lines += [f"- accept: {a}" for a in p.get("acceptance", [])]
        lines += [f"- reject: {r}" for r in p.get("rejection", [])]
    lines.append("")

    # 3. Empirical evidence
    lines += ["## 3 · Empirical evidence", ""]
    if summary:
        ds = summary.get("dataset") or {}
        seg = summary["segments"]
        lines += [
            f"Dataset `{refs.get('dataset_id')}` ({ds.get('symbol', '')} {ds.get('start', '')} → "
            f"{ds.get('end', '')}), engine {engine_version}, run `{refs.get('run_id')}`.",
            f"Execution fidelity: {FIDELITY}",
            "",
            "| Segment | Trades | Net | Win rate | Profit factor |",
            "|---|---|---|---|---|",
        ]
        for name in ("insample", "oos"):
            s = seg.get(name) or {}
            lines.append(
                f"| {name.upper()} | {s.get('trades')} | {_money(s.get('net_pnl'))} | "
                f"{s.get('win_rate')} | {s.get('profit_factor')} |"
            )
        lines += ["", "Tests:"]
        for t in summary.get("tests") or []:
            lines.append(f"- {t['id']}: {t['status']}")
        lines.append(
            f"\nHoldout: {'sealed (not looked at)' if not refs.get('holdout') else 'evaluated once'}."
        )
    else:
        lines.append("No simulation was run — see the verdict's reasons.")
    audit = refs.get("audit")
    if audit:
        lines += [
            "",
            f"SENTINEL audit (`{audit['sha256'][:12]}…`): "
            + ("passed" if audit["passed"] else "**failed**"),
        ]
        lines += [f"- {c['result']} · {c['title']} — {c['detail']}" for c in audit["checks"]]
        lines.append(f"- Limitation: {audit['limitation']}")
    if trials:
        lines += [
            "",
            "## 4 · Trial ledger (append-only)",
            "",
            "| # | Variant | Changes | OOS trades | OOS net | Audit | Post-holdout |",
            "|---|---|---|---|---|---|---|",
        ]
        for n, trial in enumerate(trials, start=1):
            metrics = trial.get("metrics") or {}
            changes = ", ".join(f"{c['path']}={c['value']}" for c in trial["changes"]) or "baseline"
            audit_ok = (trial.get("audit") or {}).get("passed")
            lines.append(
                f"| {n} | {trial['title']} | {changes} | {metrics.get('oos_trades')} | "
                f"{_money(metrics.get('oos_net'))} | {'pass' if audit_ok else 'fail' if audit_ok is False else '—'} | "
                f"{'yes' if trial['post_holdout'] else 'no'} |"
            )
    lines += ["", f"_{DISCLAIMER}_"]
    data = {
        "mission": {k: mission[k] for k in ("id", "kind", "title", "state", "created_at")},
        "verdict": verdict,
        "source": {k: source[k] for k in ("id", "kind", "title", "status")} if source else None,
        "claims": source.get("claims", []) if source else [],
        "blueprint": blueprint,
        "protocol": refs.get("protocol"),
        "evidence": {
            "dataset_id": refs.get("dataset_id"),
            "run_id": refs.get("run_id"),
            "fixture": bool(summary and summary.get("fixture")),
            "segments": summary["segments"] if summary else None,
            "tests": summary.get("tests") if summary else None,
            "execution_fidelity": "BAR_ONLY_CONSERVATIVE",
        },
        "audit": audit,
        "trials": trials,
        "disclaimer": DISCLAIMER,
    }
    return "\n".join(lines) + "\n", data
