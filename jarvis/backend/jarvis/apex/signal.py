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
  never from the strategy itself.

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

    @model_validator(mode="after")
    def _invariants(self) -> Signal:
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
