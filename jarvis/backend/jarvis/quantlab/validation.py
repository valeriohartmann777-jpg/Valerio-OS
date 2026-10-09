"""Evidence gates, the verdict, and JARVIS's critical read of a run.

R1 implements Gate A (data, causality, accounting), Gate B (baseline) and the
simple chronological out-of-sample part of Gate C. Everything else is listed
as NOT_RUN. The verdict policy is fixed before any run and written into every
manifest:

- INVALID if any Gate A check fails;
- FAILED if the out-of-sample segment holds enough closed trades and its net
  P&L after costs is ≤ 0;
- INCONCLUSIVE otherwise. R1 never returns PROMISING_RESEARCH_CANDIDATE and
  never says "validated".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from jarvis.quantlab.engine import Check

METHOD_VERSION = "r1.0"
MIN_OOS_CLOSED_TRADES = 30
MIN_CLOSED_TRADES = 30

Verdict = Literal["INVALID", "FAILED", "INCONCLUSIVE", "PROMISING_RESEARCH_CANDIDATE"]
Outcome = Literal["PASS", "WARN", "FAIL", "NOT_RUN", "N_A"]

VERDICT_POLICY: dict[str, Any] = {
    "version": METHOD_VERSION,
    "invalid_if": "any Gate A check fails (data quality, causality, accounting)",
    "failed_if": (
        f"the OOS segment has >= {MIN_OOS_CLOSED_TRADES} closed trades and OOS net P&L "
        "after costs <= 0"
    ),
    "otherwise": "INCONCLUSIVE; R1 never returns PROMISING_RESEARCH_CANDIDATE",
    "min_oos_closed_trades": MIN_OOS_CLOSED_TRADES,
}

MEANINGS: dict[str, str] = {
    "INVALID": "Data, causality or accounting failed — the numbers can't be used at all.",
    "FAILED": "A pre-registered criterion failed: the hypothesis didn't survive its own test.",
    "INCONCLUSIVE": "The run is valid, but the evidence is too thin or too incomplete to "
    "support any conclusion about an edge.",
    "PROMISING_RESEARCH_CANDIDATE": "Positive, reproducible, reasonably general evidence — "
    "worth forward testing, never live capital.",
}


@dataclass
class Report:
    verdict: Verdict
    reason: str
    checks: list[dict[str, Any]]
    missing: list[str]
    assessment: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "meaning": MEANINGS[self.verdict],
            "reason": self.reason,
            "policy": VERDICT_POLICY,
            "checks": self.checks,
            "missing_gates": self.missing,
            "assessment": self.assessment,
        }


def _check(
    id_: str,
    gate: str,
    title: str,
    result: Outcome,
    observations: str,
    *,
    metric: dict[str, Any] | None = None,
    assumptions: list[str] | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id_,
        "gate": gate,
        "title": title,
        "method_version": METHOD_VERSION,
        "result": result,
        "assumptions": assumptions or [],
        "metric": metric or {},
        "observations": observations,
        "evidence_artifact": evidence,
    }


def _money(value: float | None, currency: str) -> str:
    if value is None:
        return "n/a"
    return f"{value:+,.2f} {currency}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2%}"


def evaluate(
    *,
    passport: dict[str, Any],
    audit: list[Check],
    full: dict[str, Any],
    train: dict[str, Any],
    oos: dict[str, Any],
    split: dict[str, Any],
    spec: dict[str, Any],
    spec_sha256: str,
    variants_on_dataset: int,
) -> Report:
    currency = spec["instrument"]["currency"]
    synthetic = bool(passport.get("synthetic"))
    checks: list[dict[str, Any]] = []

    # Gate A --------------------------------------------------------------------------
    status = passport.get("quality_status")
    warnings = [f["code"] for f in passport.get("quality_findings", []) if f["severity"] == "warn"]
    checks.append(
        _check(
            "A1.data_quality",
            "A",
            "Data quality",
            "PASS" if status == "ACCEPTED" else "WARN" if status == "WARNING" else "FAIL",
            f"Data Passport status {status}"
            + (f"; warnings: {', '.join(warnings)}" if warnings else "; no warnings."),
            metric={"quality_status": status, "rows": passport.get("row_count")},
            evidence="data_qa.json",
        )
    )
    if synthetic:
        provenance: Outcome = "WARN"
        note = "SYNTHETIC / TEST ONLY fixture: engineering data, no market evidence possible."
    elif str(passport.get("license", "")).startswith("unverified"):
        provenance, note = "WARN", "User-supplied data, licence and provider not verified."
    else:
        provenance, note = "PASS", f"Provider {passport.get('provider')}, licence recorded."
    checks.append(
        _check(
            "A2.provenance",
            "A",
            "Provenance & licence",
            provenance,
            note,
            metric={"provider": passport.get("provider"), "license": passport.get("license")},
            evidence="data_qa.json",
        )
    )
    checks.append(
        _check(
            "A3.spec_confirmed",
            "A",
            "Rules unambiguous",
            "PASS",
            "StrategySpec v0.1 validated against the schema and frozen by its hash.",
            metric={"spec_sha256": spec_sha256},
            evidence="manifest.json",
        )
    )
    by_id = {c.id: c for c in audit}
    causal = [by_id[k] for k in by_id if k.startswith(("causality.", "execution."))]
    books = [by_id[k] for k in by_id if k.startswith(("accounting.", "ledger."))]
    checks.append(
        _check(
            "A4.causality",
            "A",
            "Time causality",
            "PASS" if all(c.passed for c in causal) else "FAIL",
            " ".join(c.detail for c in causal),
            metric={c.id: c.passed for c in causal},
            assumptions=[
                "Signals use closed bars only; a bar's close is known at bar_start + interval.",
                "Fills at the next bar's open with zero latency (optimistic).",
            ],
            evidence="audit.jsonl",
        )
    )
    checks.append(
        _check(
            "A5.accounting",
            "A",
            "Cost & accounting reconciliation",
            "PASS" if all(c.passed for c in books) else "FAIL",
            " ".join(c.detail for c in books),
            metric={c.id: c.passed for c in books},
            assumptions=[
                f"Fees {spec['costs']['fee_fixed_per_order']:g} {currency}/order + "
                f"{spec['costs']['fee_variable_bps']:g} bps; slippage "
                f"{spec['execution']['slippage_bps']:g} bps (modelled, not measured)."
            ],
            evidence="audit.jsonl",
        )
    )
    checks.append(
        _check(
            "A6.intrabar_ambiguity",
            "A",
            "Stop/target ambiguity",
            "N_A",
            "No stops or targets in R1 market-order specs, so no same-bar stop/target "
            "ambiguity can arise. Specs with stops are refused, not guessed (AMBIGUOUS_FILL).",
        )
    )

    # Gate B --------------------------------------------------------------------------
    closed = int(full["trades_closed"])
    checks.append(
        _check(
            "B1.ledger",
            "B",
            "Complete ledger",
            "PASS",
            f"{full['orders_filled']} fills, {full['orders_rejected']} rejected, "
            f"{full['orders_not_executable']} not executable; {closed} closed and "
            f"{full['trades_open']} open trade(s).",
            metric={
                "orders_filled": full["orders_filled"],
                "trades_closed": closed,
                "final_equity": full["final_equity"],
            },
            evidence="trades.parquet",
        )
    )
    checks.append(
        _check(
            "B2.benchmark",
            "B",
            "Benchmark",
            "PASS",
            f"Strategy {_pct(full['return_pct'])} vs buy & hold price return "
            f"{_pct(full['benchmark_price_return'])} on the same bars (no costs, not "
            "exposure-matched).",
            metric={
                "strategy_return": full["return_pct"],
                "benchmark_price_return": full["benchmark_price_return"],
                "exposure": full["exposure"],
            },
            evidence="metrics.json",
        )
    )
    checks.append(
        _check(
            "B3.sample_size",
            "B",
            "Number of trades",
            "PASS" if closed >= MIN_CLOSED_TRADES else "WARN",
            f"{closed} closed trade(s)"
            + (
                "."
                if closed >= MIN_CLOSED_TRADES
                else f" — below {MIN_CLOSED_TRADES}; ratios and win rates are noise at this size."
            ),
            metric={"closed_trades": closed, "threshold": MIN_CLOSED_TRADES},
        )
    )
    checks.append(
        _check(
            "B4.risk_adjusted",
            "B",
            "Sharpe / risk-adjusted return",
            "NOT_RUN",
            full["sharpe"]["reason"],
        )
    )

    # Gate C --------------------------------------------------------------------------
    ordered = split["train_end_utc"] < split["oos_start_utc"]
    checks.append(
        _check(
            "C1.chronological_split",
            "C",
            "Chronological split",
            "PASS" if ordered else "FAIL",
            f"Train {split['train_start_utc']} → {split['train_end_utc']} "
            f"({split['train_bars']} bars), OOS {split['oos_start_utc']} → "
            f"{split['oos_end_utc']} ({split['oos_bars']} bars). Cutoff fixed by the spec "
            "before the run; no shuffling; no parameter search on OOS.",
            metric=split,
            evidence="manifest.json",
        )
    )
    oos_closed = int(oos["trades_closed"])
    enough = oos_closed >= MIN_OOS_CLOSED_TRADES
    checks.append(
        _check(
            "C2.oos_sample",
            "C",
            "OOS sample size",
            "PASS" if enough else "WARN",
            f"{oos_closed} closed trade(s) entered in the OOS segment"
            + ("." if enough else f" — OOS not meaningful (needs {MIN_OOS_CLOSED_TRADES})."),
            metric={"oos_closed_trades": oos_closed, "threshold": MIN_OOS_CLOSED_TRADES},
        )
    )
    oos_net = oos["net_pnl"]
    checks.append(
        _check(
            "C3.oos_net_after_costs",
            "C",
            "OOS net after costs",
            ("PASS" if oos_net > 0 else "FAIL") if enough else "NOT_RUN",
            f"OOS net {_money(oos_net, currency)} ({_pct(oos['return_pct'])} of OOS start "
            "equity)" + ("." if enough else "; not judged — too few OOS trades."),
            metric={"oos_net_pnl": oos_net, "oos_return": oos["return_pct"]},
            evidence="metrics.json",
        )
    )
    checks.append(
        _check(
            "C4.variants_on_dataset",
            "C",
            "Variants tried on this data",
            "N_A" if variants_on_dataset <= 1 else "WARN",
            "First strategy version run on this dataset."
            if variants_on_dataset <= 1
            else f"{variants_on_dataset} strategy versions have been run on this dataset. "
            "Every extra variant spends statistical independence, and once OOS results were "
            "seen, this OOS segment is no longer untouched for later variants.",
            metric={"variants": variants_on_dataset},
        )
    )
    for id_, title in (
        ("C5.walk_forward", "Walk-forward"),
        ("C6.parameter_stability", "Parameter stability"),
    ):
        checks.append(_check(id_, "C", title, "NOT_RUN", "Planned for R2."))

    # Gates D, E ------------------------------------------------------------------------
    for id_, gate, title in (
        ("D1.cost_stress", "D", "Cost & slippage stress"),
        ("D2.regimes", "D", "Market regimes"),
        ("D3.dependence_bootstrap", "D", "Dependence-preserving bootstrap"),
        ("D4.multiple_testing", "D", "Multiple-testing adjustment"),
        ("E1.forward_paper", "E", "Forward paper test"),
    ):
        checks.append(_check(id_, gate, title, "NOT_RUN", "Not part of R1."))

    # Verdict ---------------------------------------------------------------------------
    gate_a_failed = [c["id"] for c in checks if c["gate"] == "A" and c["result"] == "FAIL"]
    missing = [c["title"] for c in checks if c["result"] == "NOT_RUN"]
    if not enough:
        missing.insert(0, "Meaningful OOS sample")
    verdict: Verdict
    if gate_a_failed:
        verdict, reason = "INVALID", f"Gate A failed: {', '.join(gate_a_failed)}."
    elif enough and oos_net <= 0:
        verdict = "FAILED"
        reason = (
            f"Pre-registered criterion: OOS net after costs must be > 0 with ≥ "
            f"{MIN_OOS_CLOSED_TRADES} trades; it was {_money(oos_net, currency)}."
        )
    else:
        verdict = "INCONCLUSIVE"
        reason = "Valid run, baseline only. Missing: " + ", ".join(missing[:4]) + "."
    report = Report(verdict, reason, checks, missing)
    report.assessment = assess(report, passport, full, train, oos, spec, variants_on_dataset)
    return report


def assess(
    report: Report,
    passport: dict[str, Any],
    full: dict[str, Any],
    train: dict[str, Any],
    oos: dict[str, Any],
    spec: dict[str, Any],
    variants: int,
) -> dict[str, Any]:
    """JARVIS's critical read: observed, limitations, what can't be concluded, next test."""
    cur = spec["instrument"]["currency"]
    synthetic = bool(passport.get("synthetic"))
    observed = [
        f"Full period: net {_money(full['net_pnl'], cur)} ({_pct(full['return_pct'])} of initial "
        f"cash) after {full['fees']:,.2f} {cur} fees; before fees "
        f"{_money(full['gross_pnl'], cur)}. Max drawdown {full['max_drawdown_pct']:.2%} "
        f"({full['max_drawdown_abs']:,.2f} {cur}).",
        f"Train ({train['bars']} bars): net {_money(train['net_pnl'], cur)}, "
        f"{train['trades_closed']} closed trade(s). OOS ({oos['bars']} bars): net "
        f"{_money(oos['net_pnl'], cur)}, {oos['trades_closed']} closed trade(s)"
        + (
            f", {oos['trades_carried_in']} position carried in."
            if oos["trades_carried_in"]
            else "."
        ),
        f"Buy & hold price return on the same bars: {_pct(full['benchmark_price_return'])} "
        f"(no costs, not exposure-matched); the strategy was invested "
        f"{(full['exposure'] or 0):.0%} of the time.",
    ]
    if full["trades_open"]:
        observed.append(
            f"A position is still open at the end: {_money(full['unrealized_gross_pnl'], cur)} "
            f"unrealised at the last close {full['mark_price']:,.4g}. It is marked, not sold."
        )
    if full["orders_rejected"]:
        observed.append(f"{full['orders_rejected']} buy order(s) rejected for insufficient cash.")

    limitations: list[str] = []
    if synthetic:
        limitations.append(
            "SYNTHETIC / TEST ONLY data: the numbers prove the arithmetic, not a market."
        )
    limitations.append(
        "Fills at the next open with zero latency and "
        f"{spec['execution']['slippage_bps']:g} bps slippage — real fills are usually worse."
    )
    warnings = [f for f in passport.get("quality_findings", []) if f["severity"] == "warn"]
    for finding in warnings[:3]:
        limitations.append(f"Data: {finding['message']}")
    limitations.append("No exchange calendar, dividends or borrow costs are modelled.")

    cannot = []
    if oos["trades_closed"] < MIN_OOS_CLOSED_TRADES:
        cannot.append(
            f"Whether there is an edge: {oos['trades_closed']} OOS trade(s) is far below the "
            f"{MIN_OOS_CLOSED_TRADES} needed for even a first read."
        )
    else:
        cannot.append(
            "Robustness: one OOS segment is a single draw — no walk-forward, cost stress or "
            "regime split has been run."
        )
    if variants > 1:
        cannot.append(
            f"Independence: {variants} versions were tried on this dataset, so its OOS segment "
            "has been looked at before."
        )
    cannot.append("Anything about trading real money. This is research only.")

    if synthetic:
        nxt = (
            "Import real historical bars for a liquid cash equity (CSV/Parquet with provider "
            "and licence) and run this same frozen spec on them."
        )
    elif report.verdict == "INVALID":
        nxt = "Fix the failed Gate A check first; no result is usable until it passes."
    elif report.verdict == "FAILED":
        nxt = (
            "Record the failure. A new idea needs a new spec version judged on fresh, untouched "
            "data — not a parameter tweak tested on this OOS segment."
        )
    elif oos["trades_closed"] < MIN_OOS_CLOSED_TRADES:
        nxt = (
            "Decide now, before looking further: extend the history (same frozen spec) until "
            f"the OOS segment can hold ≥ {MIN_OOS_CLOSED_TRADES} trades, then run once."
        )
    else:
        nxt = (
            "Pre-register a cost stress (slippage ×2 and ×3) and a walk-forward with the "
            "parameter range fixed in advance (R2) before any further variant."
        )
    headline = {
        "INVALID": "Not usable: a validity check failed.",
        "FAILED": "Falsified by its own pre-registered OOS criterion.",
        "INCONCLUSIVE": "Valid run, but it doesn't show an edge — and doesn't rule one out.",
        "PROMISING_RESEARCH_CANDIDATE": "Worth forward testing — not live capital.",
    }[report.verdict]
    if synthetic:
        headline = "Engineering check only (synthetic data). " + headline
    return {
        "headline": headline,
        "observed": observed,
        "limitations": limitations,
        "cannot_conclude": cannot,
        "next_test": nxt,
    }
