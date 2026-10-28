"""The APEX signal contract refuses every inconsistent or overstated record."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from jarvis.apex.signal import (
    DataQuality,
    Direction,
    EntryType,
    Signal,
    SignalStatus,
    Structure,
    ValidationState,
)

NOW = datetime(2026, 10, 28, 14, 0, tzinfo=UTC)


def make(**changes: Any) -> Signal:
    base: dict[str, Any] = {
        "signal_id": "sig-test",
        "symbol": "XAUUSD",
        "market_data_source": "dukascopy:XAUUSD|bid|sha256:abc",
        "as_of_utc": NOW,
        "last_closed_bar_utc": NOW,
        "timeframe": "5m",
        "strategy_id": "zone_rejection_5m",
        "strategy_version": "1.0.0",
        "status": SignalStatus.CANDIDATE,
        "direction": Direction.LONG,
        "market_structure": Structure.BULLISH,
        "entry_type": EntryType.STOP,
        "entry": 2275.5,
        "stop_loss": 2270.0,
        "take_profit_1": 2285.0,
        "take_profit_2": 2290.0,
        "risk_reward_after_costs": 1.65,
        "data_quality": DataQuality.PASS,
        "execution_assumption": "bid_only_spread_estimated",
        "trigger": {"rule": "bullish 5-minute rejection of S1"},
        "created_at_utc": NOW,
    }
    return Signal(**{**base, **changes})


def no_plan(**changes: Any) -> dict[str, Any]:
    return {
        "entry_type": EntryType.NONE,
        "entry": None,
        "stop_loss": None,
        "take_profit_1": None,
        "take_profit_2": None,
        "risk_reward_after_costs": None,
        **changes,
    }


def test_a_complete_long_plan_is_accepted_and_serialises() -> None:
    record = make().as_record()
    assert record["status"] == "CANDIDATE" and record["entry_type"] == "STOP"
    assert record["validation_state"] == "UNVALIDATED"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"stop_loss": 2276.0}, "geometry"),  # stop above a long entry
        ({"take_profit_1": 2274.0}, "geometry"),  # target below a long entry
        ({"take_profit_2": 2280.0}, "geometry"),  # target 2 before target 1
        ({"direction": Direction.SHORT}, "geometry"),  # long prices, short label
        ({"risk_reward_after_costs": 0.0}, "reward/risk"),
        ({"data_quality": DataQuality.FAIL}, "quality gate"),
        ({"trigger": None}, "trigger"),  # a stop entry without its confirmed trigger
        ({"take_profit_1": None}, "entry, stop loss and take profit"),
        ({"direction": Direction.NONE}, "direction"),
    ],
)
def test_broken_order_plans_are_refused(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        make(**changes)


def test_a_short_plan_mirrors_the_geometry() -> None:
    short = make(direction=Direction.SHORT, market_structure=Structure.BEARISH, entry=2270.0,
                 stop_loss=2275.0, take_profit_1=2262.0, take_profit_2=2258.0)  # fmt: skip
    assert short.direction == Direction.SHORT


@pytest.mark.parametrize(
    "status", [SignalStatus.NO_TRADE, SignalStatus.WAITING_FOR_TRIGGER, SignalStatus.BLOCKED]
)
def test_no_trade_waiting_and_blocked_carry_no_order(status: SignalStatus) -> None:
    with pytest.raises(ValidationError, match="executable-looking"):
        make(status=status)  # still has entry/stop/targets
    ok = make(status=status, direction=Direction.NONE, trigger=None, reasons=["why"], **no_plan())
    assert ok.entry is None and ok.entry_type == EntryType.NONE


def test_blocked_needs_reasons_and_paper_validated_needs_evidence() -> None:
    with pytest.raises(ValidationError, match="reasons"):
        make(status=SignalStatus.BLOCKED, direction=Direction.NONE, trigger=None, **no_plan())
    with pytest.raises(ValidationError, match="out-of-sample or forward"):
        make(status=SignalStatus.PAPER_VALIDATED)
    assert make(status=SignalStatus.PAPER_VALIDATED, validation_state=ValidationState.OOS_SUPPORTED)


def test_no_probability_without_interval_and_calibration_sample() -> None:
    with pytest.raises(ValidationError, match="interval"):
        make(probability_estimate=0.62)
    with pytest.raises(ValidationError, match="at least 30"):
        make(probability_estimate=0.62, probability_interval=(0.5, 0.7), calibration_sample_size=12)
    with pytest.raises(ValidationError, match="outside its interval"):
        make(probability_estimate=0.8, probability_interval=(0.5, 0.7), calibration_sample_size=200)
    assert make(
        probability_estimate=0.62, probability_interval=(0.55, 0.69), calibration_sample_size=212
    )


def test_signals_are_immutable() -> None:
    signal = make()
    with pytest.raises(ValidationError):
        signal.entry = 1.0  # type: ignore[misc]
