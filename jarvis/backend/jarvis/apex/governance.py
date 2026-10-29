"""Who may do what with APEX — decided in code, never by an agent's or a model's words.

**Actors.** The owner; JARVIS (orchestrates); the ULTRON agents CIPHER (methodology), FORGE
(implementation), ATLAS (source checks), ARCHIVE (records), PRISM (UX), VECTOR (reliability),
OPERATOR (local integration) and SENTINEL (independent audit, veto).

**Promotion of a probability head** (``apex/probability.py``) — the only way a P(TP before SL)
can ever qualify a plan:

1. *propose* — anyone (an agent or the owner) asks for a head to be promoted;
2. *SENTINEL review*, at once and automatically: the head is VALIDATED on the untouched test
   window, fitted on real data (not generated), under the current protocol version, its setup
   version passed the frozen holdout (``OOS_SUPPORTED``), and no SENTINEL re-check of the
   market's latest analysis failed. Any failing check is a **veto**;
3. *approve* — the owner only, with an explicit confirmation, and only an un-vetoed proposal.

Every step is an immutable event (``apex_promotions``); the head's state is its last event.
A later revoke by the owner is a new event, never an edit.

**Never, for anyone in this release:** live or broker orders, relaxing the 0.70 gate, reusing a
sealed holdout, erasing a registered trial or a rejected variant, changing the pre-registered
validation numbers. **Owner only:** approving a promotion, paid data (after a quote), reading a
secret. Agents can never self-authorize any of these.
"""

from __future__ import annotations

from typing import Any

OWNER = "owner"
AGENTS = ("jarvis", "cipher", "forge", "atlas", "archive", "prism", "vector", "operator",
          "sentinel")  # fmt: skip
ACTORS = (OWNER, *AGENTS)

ANYONE = ("propose_promotion", "run_validation", "run_analysis", "run_audit", "register_trial")
OWNER_ONLY = ("approve_promotion", "revoke_promotion", "approve_paid_data", "read_secret")
NEVER = ("live_order", "relax_probability_gate", "reuse_holdout", "erase_trial",
         "change_validation_protocol")  # fmt: skip


class GovernanceError(PermissionError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def authorize(actor: str, action: str) -> None:
    """Raise unless ``actor`` may do ``action``."""
    if actor not in ACTORS:
        raise GovernanceError("UNKNOWN_ACTOR", f"{actor!r} is not an APEX actor.")
    if action in NEVER:
        raise GovernanceError("LOCKED", f"{action} is locked for everyone in this release.")
    if action in OWNER_ONLY and actor != OWNER:
        raise GovernanceError(
            "OWNER_ONLY", f"{actor} can't {action.replace('_', ' ')} — only the owner can."
        )
    if action not in (*ANYONE, *OWNER_ONLY):
        raise GovernanceError("UNKNOWN_ACTION", f"{action!r} is not an APEX action.")


def sentinel_review(
    model: dict[str, Any],
    *,
    protocol_version: str,
    strategy_state: str | None,
    audit_failed: bool,
) -> dict[str, Any]:
    """SENTINEL's checks on a proposed head; any failure is a veto."""
    checks = [
        ("HEAD_VALIDATED", bool(model.get("validated")),
         f"head verdict {((model.get('verdict') or {}).get('code'))}"),
        ("REAL_DATA", not model.get("synthetic", True),
         "generated test data never qualifies" if model.get("synthetic", True)
         else f"source {model.get('source_id')}"),
        ("PROTOCOL", model.get("protocol_version") == protocol_version,
         f"fitted under {model.get('protocol_version')}, current {protocol_version}"),
        ("STRATEGY_HOLDOUT", strategy_state in ("OOS_SUPPORTED", "FORWARD_SUPPORTED"),
         f"setup version {model.get('version')}: {strategy_state or 'not validated'}"),
        ("NO_OPEN_FAILURE", not audit_failed,
         "a SENTINEL re-check of this market failed" if audit_failed else "no failed re-check"),
    ]  # fmt: skip
    rows = [{"check": c, "passed": bool(ok), "reason": why} for c, ok, why in checks]
    return {"vetoed": not all(r["passed"] for r in rows), "checks": rows}


def state_of(events: list[dict[str, Any]]) -> str:
    """The promotion state of a head from its events (oldest first): NONE when never proposed."""
    return str(events[-1]["state"]) if events else "NONE"
