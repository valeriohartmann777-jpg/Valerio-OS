"""The APEX signal contract: one typed, immutable decision record per strategy and analysis.

A signal says what a versioned rule concluded at ``as_of`` from the bars closed by then — and
nothing it can't back up:

- ``NO_TRADE`` / ``BLOCKED`` / ``WAITING_FOR_TRIGGER`` carry **no** entry, stop or target and
  ``entry_type = NONE`` (no executable-looking instruction without a confirmed trigger);
- ``CANDIDATE`` / ``PAPER_VALIDATED`` carry a complete order plan whose geometry is valid for
  its direction (long: stop < entry < target 1 < target 2; short mirrored) on the tick grid;
- a ``probability_estimate`` exists only with its interval and a calibration sample of at
  least ``MIN_CALIBRATION`` decided cases — never a bare number;
- ``validation_state`` comes from the validation protocol (``docs/APEX_VALIDATION_PROTOCOL.md``),
  never from the strategy itself;
- the SignalPlan fields (``setup_state``, ``stage``, ``eligibility``, ``trade_success_probability``,
  ``net_expected_r``, ``mode``, ``zone_ids``) come from the zone setups and the eligibility gate:
  ``eligible`` is true only with every gate check passed, a validated and calibrated
  P(TP before SL) of at least 0.70, a positive stressed expected R and a current (not replay,
  not synthetic) mode; ``user_executable`` is always false (paper only).

Signals are written once (``apex_signals``, no UPDATE/DELETE); what happened afterwards is a
separate outcome record.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MIN_CALIBRATION = 30


class SignalStatus(StrEnum):
    NO_TRADE = "NO_TRADE"
    WAITING_FOR_TRIGGER = "WAITING_FOR_TRIGGER"
    CANDIDATE = "CANDIDATE"
    PAPER_VALIDATED = "PAPER_VALIDATED"
    BLOCKED = "BLOCKED"


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


class Structure(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class EntryType(StrEnum):
    LIMIT = "LIMIT"
    STOP = "STOP"
    MARKET = "MARKET"
    NONE = "NONE"


class ValidationState(StrEnum):
    UNVALIDATED = "UNVALIDATED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    OOS_SUPPORTED = "OOS_SUPPORTED"
    FORWARD_SUPPORTED = "FORWARD_SUPPORTED"
    REJECTED = "REJECTED"


class DataQuality(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class SetupState(StrEnum):
    WATCH = "WATCH"
    TRIGGER_PENDING = "TRIGGER_PENDING"
    TRIGGER_CONFIRMED = "TRIGGER_CONFIRMED"
    ENTRY_ARMED = "ENTRY_ARMED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    NO_TRADE = "NO_TRADE"
    BLOCKED = "BLOCKED"


ORDER_STATES = (SignalStatus.CANDIDATE, SignalStatus.PAPER_VALIDATED)


class SignalZone(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    side: Literal["support", "resistance"]
    low: float
    high: float
    confirmed_at_utc: datetime | None = None
    source: str  # the rule that made it (key levels → zones)


class Signal(BaseModel):
    model_config = ConfigDict(frozen=True, use_enum_values=False)

    signal_id: str
    symbol: Literal["XAUUSD", "NQ", "MNQ"]
    market_data_source: str  # provider + dataset + content hash
    as_of_utc: datetime
    last_closed_bar_utc: datetime | None
    timeframe: str
    strategy_id: str
    strategy_version: str
    status: SignalStatus
    direction: Direction
    market_structure: Structure
    zones: list[SignalZone] = Field(default_factory=list)
    entry_type: EntryType = EntryType.NONE
    entry: float | None = None
    stop_loss: float | None = None
    take_profit_1: float | None = None
    take_profit_2: float | None = None
    risk_reward_after_costs: float | None = None
    probability_estimate: float | None = None
    probability_interval: tuple[float, float] | None = None
    calibration_sample_size: int | None = None
    validation_state: ValidationState = ValidationState.UNVALIDATED
    invalidation_rule: str = ""
    reasons: list[str] = Field(default_factory=list)
    data_quality: DataQuality
    execution_assumption: str  # e.g. "bid_only_spread_estimated" — how fills were modelled
    setup: dict[str, Any] | None = None  # what was observed
    trigger: dict[str, Any] | None = None  # the confirmed event (None = not triggered)
    evidence_ids: list[str] = Field(default_factory=list)
    candidate_id: str | None = None  # the journaled order plan, when there is one
    created_at_utc: datetime
    # SignalPlan (APEX Ultimate): stage and gate of the zone setups.
    setup_state: SetupState | None = None
    stage: str | None = None
    eligible: bool = False
    eligibility: list[dict[str, Any]] = Field(default_factory=list)
    net_expected_r: float | None = None
    trade_success_probability: dict[str, Any] | None = None
    conviction: str | None = None
    mode: str | None = None
    zone_ids: list[str] = Field(default_factory=list)
    instrument_id: str | None = None
    risk_usd: float | None = None
    expiry_utc: datetime | None = None
    user_executable: Literal[False] = False

    @model_validator(mode="after")
    def _invariants(self) -> Signal:
        if self.eligible or self.setup_state == SetupState.ENTRY_ARMED:
            self._qualified()
        plan = (self.entry, self.stop_loss, self.take_profit_1)
        if self.status in ORDER_STATES:
            if self.direction == Direction.NONE or self.entry_type == EntryType.NONE:
                raise ValueError(f"{self.status} needs a direction and an entry type")
            if any(p is None for p in plan):
                raise ValueError(f"{self.status} needs entry, stop loss and take profit 1")
            entry, stop, tp1 = (float(p) for p in plan)  # type: ignore[arg-type]
            tp2 = self.take_profit_2
            if self.direction == Direction.LONG:
                ok = stop < entry < tp1 and (tp2 is None or tp2 > tp1)
            else:
                ok = stop > entry > tp1 and (tp2 is None or tp2 < tp1)
            if not ok:
                raise ValueError(
                    f"{self.direction} geometry broken: stop {stop}, entry {entry}, tp {tp1}/{tp2}"
                )
            if self.risk_reward_after_costs is None or self.risk_reward_after_costs <= 0:
                raise ValueError("an order plan needs a positive reward/risk after costs")
            if self.data_quality == DataQuality.FAIL:
                raise ValueError("no order plan on data that failed the quality gate")
            if self.trigger is None and self.entry_type != EntryType.LIMIT:
                raise ValueError("a stop or market entry needs its confirmed trigger")
        else:
            if self.entry_type != EntryType.NONE or any(
                p is not None for p in (*plan, self.take_profit_2, self.risk_reward_after_costs)
            ):
                raise ValueError(f"{self.status} must not carry an executable-looking order plan")
        if self.status == SignalStatus.BLOCKED and not self.reasons:
            raise ValueError("BLOCKED needs its reasons")
        if self.status == SignalStatus.PAPER_VALIDATED and self.validation_state not in (
            ValidationState.OOS_SUPPORTED,
            ValidationState.FORWARD_SUPPORTED,
        ):
            raise ValueError("PAPER_VALIDATED needs out-of-sample or forward support")
        if self.probability_estimate is not None:
            if self.probability_interval is None:
                raise ValueError("a probability needs its interval")
            if (self.calibration_sample_size or 0) < MIN_CALIBRATION:
                raise ValueError(
                    f"a probability needs at least {MIN_CALIBRATION} calibration cases"
                )
            lo, hi = self.probability_interval
            if not 0 <= lo <= self.probability_estimate <= hi <= 1:
                raise ValueError("probability outside its interval")
        return self

    def _qualified(self) -> None:
        """A qualified paper signal can't be faked: every check, a validated probability."""
        if not (self.eligible and self.setup_state == SetupState.ENTRY_ARMED):
            raise ValueError("ENTRY_ARMED and eligible go together")
        if self.status not in ORDER_STATES:
            raise ValueError("a qualified signal needs an order plan")
        tsp = self.trade_success_probability or {}
        value = tsp.get("value")
        if not (tsp.get("validated") and tsp.get("calibrated") and value is not None):
            raise ValueError("PROBABILITY NOT VALIDATED — a signal can't qualify without it")
        if tsp.get("event_definition") != "TP_before_SL_after_fill_within_horizon":
            raise ValueError("only P(TP before SL) qualifies a trade")
        if float(value) < 0.70:
            raise ValueError("P(TP before SL) below 0.70 never qualifies")
        if self.net_expected_r is None or self.net_expected_r <= 0:
            raise ValueError("a qualified signal needs a positive stressed expected R")
        if self.mode not in ("LIVE", "DELAYED"):
            raise ValueError(f"mode {self.mode} never qualifies (replay, synthetic, historical)")
        if not self.eligibility or not all(c.get("passed") for c in self.eligibility):
            raise ValueError("every eligibility check must pass")

    def as_record(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def utc(epoch: int | None) -> datetime | None:
    return None if epoch is None else datetime.fromtimestamp(int(epoch), UTC)


def signal_id(fields: dict[str, Any]) -> str:
    """Content id: the same strategy, moment, data and decision always get the same id."""
    raw = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return "sig-" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def structure_of(trend: str | None) -> Structure:
    return {"up": Structure.BULLISH, "down": Structure.BEARISH, "range": Structure.RANGE}.get(
        trend or "", Structure.UNKNOWN
    )


def quality_of(status: str) -> DataQuality:
    return {"OK": DataQuality.PASS, "WARN": DataQuality.WARN}.get(status, DataQuality.FAIL)


TIMEFRAMES = {
    "zone_rejection_5m": "5m",
    "zone_reaction": "15m",
    "sweep_reclaim": "5m",
    "rejection_rotation": "5m",
    "breakout_retest": "5m",
}
SUPPORTED = (ValidationState.OOS_SUPPORTED, ValidationState.FORWARD_SUPPORTED)


def from_analysis(
    analysis: dict[str, Any],
    validation: dict[str, ValidationState],
    *,
    created_at: datetime,
) -> list[Signal]:
    """One signal per strategy decision of a stored analysis — one per order plan when the
    strategy made several (a long at S1 and a short at R1)."""
    execution = analysis.get("execution") or {}
    data = analysis["data"]
    source = f"{data.get('source_id') or data['source']}|{execution.get('quotes', '?')}"
    common: dict[str, Any] = {
        "symbol": analysis["market"],
        "market_data_source": f"{source}|sha256:{str(data['sha256'])[:16]}",
        "as_of_utc": utc(analysis["as_of"]),
        "last_closed_bar_utc": utc(data.get("last_bar")),
        "market_structure": structure_of(
            ((analysis.get("structure") or {}).get("60") or {}).get("trend")
        ),
        "data_quality": quality_of(str((analysis.get("quality") or {}).get("status"))),
        "execution_assumption": str(execution.get("assumption") or "unknown"),
        "created_at_utc": created_at,
    }
    zones = {z["id"]: z for z in analysis.get("zones", [])}
    mapped = analysis.get("zone_map") or {}
    for z in (*mapped.get("support", []), *mapped.get("resistance", []),
              *mapped.get("others", [])):  # fmt: skip
        zones.setdefault(z["zone_id"], _mapped(z))
    common["mode"] = analysis.get("mode")
    common["instrument_id"] = data.get("source_id") or data["source"]
    out: list[Signal] = []
    for name, decision in (analysis.get("strategies") or {}).items():
        state = validation.get(name, ValidationState.UNVALIDATED)
        evidence = [str(analysis.get("id") or "")] + (
            [str(analysis["validation_ids"][name])] if name in analysis.get("validation_ids", {})
            else []
        )  # fmt: skip
        base = {
            **common,
            "timeframe": TIMEFRAMES.get(name, "1m"),
            "strategy_id": name,
            "strategy_version": str(decision.get("version") or ""),
            "validation_state": state,
            "setup": {"setups": decision.get("setups") or []},
            "trigger": decision.get("trigger"),
            "evidence_ids": [e for e in evidence if e],
        }
        plans = decision.get("candidates") or []
        if not plans:
            status = SignalStatus(decision["status"])
            near = [z for z in zones.values() if z.get("side") in ("support", "resistance")]
            looked = [s["zone"] for s in decision.get("setups") or [] if s.get("zone")]
            shown = decision.get("setup_state")
            out.append(
                _make(
                    base,
                    status=status,
                    direction=Direction.NONE,
                    zones=[_zone(zones[z]) for z in looked if z in zones]
                    or [_zone(z) for z in near[:2]],
                    reasons=list(decision.get("reasons") or [])
                    or ["No zone in reach — nothing to do."],
                    trigger=None,  # no confirmed event (or none that made a valid plan)
                    setup_state=SetupState(shown) if shown and shown != "ENTRY_ARMED" else None,
                    stage=decision.get("stage"),
                    zone_ids=looked,
                )
            )
            continue
        for plan in plans:
            targets = plan["targets"]
            involved = [plan["zone"], *(t.get("zone") for t in targets)]
            gate = plan.get("eligibility") or {}
            shown = gate.get("setup_state") or (
                "RESEARCH_ONLY" if name in TIMEFRAMES and plan.get("setup") else None
            )
            out.append(
                _make(
                    base,
                    status=SignalStatus.PAPER_VALIDATED if state in SUPPORTED
                    else SignalStatus.CANDIDATE,
                    direction=Direction.LONG if plan["direction"] == "long" else Direction.SHORT,
                    zones=[_zone(zones[z]) for z in involved if z in zones],
                    entry_type=EntryType(str(plan["entry"]["type"]).upper()),
                    entry=float(plan["entry"]["price"]),
                    stop_loss=float(plan["stop"]),
                    take_profit_1=float(targets[0]["price"]),
                    take_profit_2=float(targets[1]["price"]) if len(targets) > 1 else None,
                    risk_reward_after_costs=float(targets[0]["rr"]),
                    invalidation_rule=" ".join(plan.get("rules") or []),
                    reasons=[_why(plan, decision)],
                    candidate_id=str(plan["id"]),
                    setup_state=SetupState(shown) if shown else None,
                    stage="ENTRY_ARMED" if plan.get("setup") else None,
                    eligible=bool(gate.get("eligible")),
                    eligibility=list(gate.get("checks") or []),
                    net_expected_r=gate.get("net_expected_r"),
                    trade_success_probability=plan.get("trade_success_probability"),
                    conviction=gate.get("conviction"),
                    zone_ids=list(plan.get("zone_ids") or involved),
                    risk_usd=plan.get("risk_usd"),
                    expiry_utc=utc(plan.get("valid_until")),
                )
            )  # fmt: skip
    return out


def _make(base: dict[str, Any], **fields: Any) -> Signal:
    raw = {**base, **fields}
    identity = {
        k: raw.get(k)
        for k in ("symbol", "market_data_source", "as_of_utc", "strategy_id",
                  "strategy_version", "status", "direction", "candidate_id")
    }  # fmt: skip
    # A different probability, gate verdict or zone set is a different signal (a new record,
    # never an edit of the old one); absent for the older strategies, so their ids stay.
    for k in ("trade_success_probability", "eligible", "setup_state", "zone_ids"):
        if raw.get(k):
            identity[k] = raw[k]
    return Signal(signal_id=signal_id(identity), **raw)


def _mapped(z: dict[str, Any]) -> dict[str, Any]:
    """A zone-map zone in the shape ``_zone`` reads."""
    return {"id": z["zone_id"], "side": str(z["side"]).lower(), "low": z["bounds"]["low"],
            "high": z["bounds"]["high"], "kinds": ["zone_map", *z.get("kinds", [])],
            "confirmed_at": z.get("confirmed_at")}  # fmt: skip


def _zone(z: dict[str, Any]) -> SignalZone:
    kinds = list(z.get("groups") or z.get("kinds") or [])
    mapped = kinds[:1] == ["zone_map"]
    return SignalZone(
        id=str(z["id"]),
        side=z["side"],
        low=float(z["low"]),
        high=float(z["high"]),
        confirmed_at_utc=utc(z.get("confirmed_at")),
        source=("zone_map:" + ",".join(kinds[1:])) if mapped else "key_levels:" + ",".join(kinds),
    )


def _why(plan: dict[str, Any], decision: dict[str, Any]) -> str:
    trend = plan.get("trend_60")
    flag = plan.get("with_trend")
    with_trend = (
        "no clear 60-minute trend"
        if flag is None
        else ("with" if flag else "against") + " the 60-minute trend"
    )
    if decision.get("trigger"):
        return f"{decision['trigger']['rule']}; {with_trend} ({trend})."
    side = "support" if plan["direction"] == "long" else "resistance"
    return f"Limit at {plan['zone']} ({side}); {with_trend} ({trend})."
