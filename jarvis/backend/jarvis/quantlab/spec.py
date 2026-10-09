"""StrategySpec v0.1: the only thing the engine will run.

A spec is plain JSON, validated against the contract in
``docs/quantlab-handoff/contracts/strategy_spec.schema.json`` (mirrored here as
Pydantic models, unknown fields forbidden), normalised to canonical JSON and
identified by its SHA-256. Instruments the R1 engine can't account for
correctly — futures, FX and metal pairs, anything that isn't a cash equity —
are refused as ``UNSUPPORTED_INSTRUMENT`` instead of being forced through
share-based accounting.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

SCHEMA_VERSION = "0.1"

BarInterval = Literal["1d", "1h", "5m", "1m"]
INTERVALS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}

# Venues and contract codes that mean "futures", whatever asset class was typed in.
_FUTURES_VENUES = {
    "CME", "CBOT", "NYMEX", "COMEX", "GLOBEX", "CME GLOBEX", "EUREX", "ICE", "ICE US",
    "ICE EU", "CFE", "SGX", "HKFE", "OSE",
}  # fmt: skip
_FUTURES_ROOTS = {"NQ", "MNQ", "MES", "MYM", "M2K", "MGC", "MCL", "FDAX", "FESX", "FGBL"}
_CONTRACT_CODE = re.compile(r"^[A-Z0-9]{1,4}[FGHJKMNQUVXZ]\d{1,2}$|^[A-Z0-9]{1,4}(\d!|=F)$")
_FX_CODES = {
    "XAU", "XAG", "XPT", "XPD", "EUR", "USD", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD",
    "SEK", "NOK", "DKK", "SGD", "HKD", "CNH", "MXN", "ZAR", "TRY", "PLN", "BTC", "ETH",
}  # fmt: skip


class SpecError(ValueError):
    """A spec the engine must not run. ``code`` is SPEC_INVALID or UNSUPPORTED_INSTRUMENT."""

    def __init__(
        self, code: str, message: str, details: list[dict[str, str]] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


def unsupported_reason(asset_class: str, symbol: str, exchange: str) -> str | None:
    """Why the R1 cash-equity engine can't account for this instrument, if it can't."""
    asset = asset_class.strip().lower()
    sym = symbol.strip().upper().replace("/", "")
    venue = exchange.strip().upper()
    if asset and asset != "cash_equity":
        return (
            f"Asset class '{asset_class}' isn't supported: the R1 engine only accounts for "
            "cash equities (shares × price, no multiplier, margin, roll or expiry)."
        )
    if venue in _FUTURES_VENUES or sym in _FUTURES_ROOTS or _CONTRACT_CODE.match(sym):
        return (
            f"{symbol} on {exchange} looks like a futures contract. Futures need multiplier, "
            "tick value, margin and roll accounting, which R1 doesn't have — share-based "
            "P&L would be wrong."
        )
    if len(sym) == 6 and sym[:3] in _FX_CODES and sym[3:] in _FX_CODES:
        return (
            f"{symbol} is a currency/metal pair (spot, CFD or FX). Contract size, leverage and "
            "swap financing aren't modelled in R1 — share-based P&L would be wrong."
        )
    return None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Instrument(_Strict):
    asset_class: Literal["cash_equity"]
    symbol: str = Field(min_length=1, max_length=40)
    exchange: str = Field(min_length=1, max_length=40)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    timezone: str = Field(min_length=3, max_length=64)
    corporate_action_policy: Literal["data_adjusted", "not_applicable_test_fixture"] | None = None

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone '{value}'") from exc
        return value


class Timeframe(_Strict):
    bar_interval: BarInterval
    timestamps_are_bar: Literal["open"]
    signal_timestamp: Literal["bar_close"]
    session_calendar: str | None = Field(default=None, min_length=1)


class Signal(_Strict):
    type: Literal["sma_crossover"]
    fast_window: int = Field(ge=1, le=10_000)
    slow_window: int = Field(ge=2, le=10_000)
    price_column: Literal["close"]
    cross: Literal["strict_cross"]


class Execution(_Strict):
    entry_order: Literal["market"]
    exit_order: Literal["market"]
    fill_timing: Literal["next_bar_open"]
    slippage_bps: float = Field(ge=0, le=500, allow_inf_nan=False)
    end_of_data: Literal["mark_open_position_no_forced_sale"]


class Position(_Strict):
    direction: Literal["long_only"]
    size_units: int = Field(ge=1, le=10_000_000)
    initial_cash: float = Field(gt=0, le=1e12, allow_inf_nan=False)
    leverage: Literal[1]


class Costs(_Strict):
    fee_fixed_per_order: float = Field(ge=0, le=1e6, allow_inf_nan=False)
    fee_variable_bps: float = Field(ge=0, le=1000, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class Analysis(_Strict):
    chronological_oos_fraction: float = Field(gt=0, lt=0.5, allow_inf_nan=False)
    allow_parameter_search_on_oos: Literal[False]


class StrategySpec(_Strict):
    schema_version: Literal["0.1"]
    name: str = Field(min_length=3, max_length=120)
    hypothesis: str = Field(min_length=5, max_length=1000)
    instrument: Instrument
    timeframe: Timeframe
    signal: Signal
    execution: Execution
    position: Position
    costs: Costs
    analysis: Analysis

    @property
    def interval(self) -> timedelta:
        return INTERVALS[self.timeframe.bar_interval]

    def canonical(self) -> str:
        return canonical_json(self.model_dump(mode="json", exclude_none=True))

    def sha256(self) -> str:
        return sha256_text(self.canonical())


def canonical_json(value: Any) -> str:
    """Sorted keys, no whitespace, UTF-8: the same document always hashes the same."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_spec(raw: Any) -> StrategySpec:
    """Validate a spec, refusing unsupported instruments before anything else."""
    if not isinstance(raw, dict):
        raise SpecError("SPEC_INVALID", "A StrategySpec must be a JSON object.")
    instrument = raw.get("instrument")
    if isinstance(instrument, dict):
        reason = unsupported_reason(
            str(instrument.get("asset_class", "")),
            str(instrument.get("symbol", "")),
            str(instrument.get("exchange", "")),
        )
        if reason:
            raise SpecError("UNSUPPORTED_INSTRUMENT", reason)
    # Accept whole-number floats for integer fields (JSON has one number type).
    try:
        return StrategySpec.model_validate(_integral(raw))
    except ValidationError as exc:
        details = [
            {"field": ".".join(str(p) for p in err["loc"]) or "(spec)", "problem": err["msg"]}
            for err in exc.errors()
        ]
        summary = "; ".join(f"{d['field']}: {d['problem']}" for d in details[:4])
        raise SpecError("SPEC_INVALID", f"The spec isn't valid — {summary}", details) from exc


_INTEGER_FIELDS = {
    ("signal", "fast_window"),
    ("signal", "slow_window"),
    ("position", "size_units"),
    ("position", "leverage"),
}


def _integral(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    for section, key in _INTEGER_FIELDS:
        block = out.get(section)
        if isinstance(block, dict):
            value = block.get(key)
            if isinstance(value, float) and value.is_integer():
                out[section] = {**block, key: int(value)}
    return out


def spec_warnings(spec: StrategySpec) -> list[str]:
    """Valid but worth a second look before the user confirms the spec."""
    notes: list[str] = []
    if spec.signal.fast_window >= spec.signal.slow_window:
        notes.append(
            f"fast_window ({spec.signal.fast_window}) isn't shorter than slow_window "
            f"({spec.signal.slow_window}): the 'fast' average reacts slower. Intended?"
        )
    if spec.execution.slippage_bps == 0:
        notes.append(
            "Slippage is 0 bps: fills at exactly the next open are optimistic. "
            "Real spreads and latency cost more."
        )
    if spec.costs.fee_fixed_per_order == 0 and spec.costs.fee_variable_bps == 0:
        notes.append("No fees: real brokers charge commissions or spreads.")
    if spec.costs.currency != spec.instrument.currency:
        notes.append(
            f"Fees are in {spec.costs.currency} but the instrument trades in "
            f"{spec.instrument.currency}; R1 has no currency conversion, so runs are blocked."
        )
    return notes


def describe(spec: StrategySpec) -> str:
    """The plain-language summary shown before a run."""
    pos, costs, ex = spec.position, spec.costs, spec.execution
    fee = f"{costs.fee_fixed_per_order:g} {costs.currency} per order"
    if costs.fee_variable_bps:
        fee += f" + {costs.fee_variable_bps:g} bps of notional"
    return (
        f"Long {pos.size_units} × {spec.instrument.symbol} when SMA({spec.signal.fast_window}) "
        f"crosses above SMA({spec.signal.slow_window}) at a {spec.timeframe.bar_interval} bar's "
        f"close; exit when it crosses back below. Orders fill at the next bar's open "
        f"(zero-latency assumption) with {ex.slippage_bps:g} bps slippage; fees {fee}. "
        f"Starting cash {pos.initial_cash:,.2f} {spec.instrument.currency}, no leverage. "
        f"The last {spec.analysis.chronological_oos_fraction:.0%} of bars are a chronological "
        "out-of-sample segment; no parameters are chosen on it. An open position at the end "
        "is marked to market, never sold by assumption."
    )


def example_spec() -> dict[str, Any]:
    """The handoff's demo spec (synthetic fixture instrument)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "name": "Simple SMA Cross Demo",
        "hypothesis": "Synthetic engineering example only; not an investment hypothesis.",
        "instrument": {
            "asset_class": "cash_equity",
            "symbol": "TEST_SYNTHETIC",
            "exchange": "TEST_ONLY",
            "currency": "USD",
            "timezone": "Etc/UTC",
            "corporate_action_policy": "not_applicable_test_fixture",
        },
        "timeframe": {
            "bar_interval": "1m",
            "timestamps_are_bar": "open",
            "signal_timestamp": "bar_close",
        },
        "signal": {
            "type": "sma_crossover",
            "fast_window": 2,
            "slow_window": 3,
            "price_column": "close",
            "cross": "strict_cross",
        },
        "execution": {
            "entry_order": "market",
            "exit_order": "market",
            "fill_timing": "next_bar_open",
            "slippage_bps": 0,
            "end_of_data": "mark_open_position_no_forced_sale",
        },
        "position": {"direction": "long_only", "size_units": 1, "initial_cash": 100, "leverage": 1},
        "costs": {"fee_fixed_per_order": 0.5, "fee_variable_bps": 0, "currency": "USD"},
        "analysis": {"chronological_oos_fraction": 0.25, "allow_parameter_search_on_oos": False},
    }
