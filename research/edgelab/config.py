"""Typed access to the YAML configuration in ``research/configs``.

Nothing in the engine hardcodes contract or cost numbers: they all come from
these loaders, so every experiment states its assumptions explicitly.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

RESEARCH_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = RESEARCH_ROOT / "configs"
RESULTS_DIR = RESEARCH_ROOT / "results"


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Read a YAML file into a dict (empty dict for an empty file)."""
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def parse_hhmm(value: str) -> dt.time:
    """Parse ``"HH:MM"`` into a :class:`datetime.time`."""
    hh, mm = value.split(":")
    return dt.time(int(hh), int(mm))


def minutes_of(t: dt.time) -> int:
    """Minutes since midnight of a wall-clock time."""
    return t.hour * 60 + t.minute


@dataclass(frozen=True)
class InstrumentSpec:
    """Contract specification. ``tick_value`` must equal ``tick_size * point_value``."""

    symbol: str
    tick_size: float
    tick_value: float
    point_value: float
    currency: str
    session_template: str
    description: str = ""
    asset_class: str = ""
    exchange: str = ""
    roll: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.tick_size <= 0 or self.point_value <= 0:
            raise ValueError(f"{self.symbol}: tick_size and point_value must be positive")
        if not math.isclose(self.tick_size * self.point_value, self.tick_value, rel_tol=1e-9):
            raise ValueError(
                f"{self.symbol}: tick_value {self.tick_value} != tick_size*point_value "
                f"{self.tick_size * self.point_value}"
            )

    @property
    def is_proxy(self) -> bool:
        """True for markets that may only ever be reported as proxy evidence."""
        return self.asset_class.startswith("crypto")

    def round_price(self, price: float, mode: str = "nearest") -> float:
        """Round a price to the tick grid (``nearest``, ``up`` or ``down``)."""
        q = price / self.tick_size
        if mode == "nearest":
            n = round(q)
        elif mode == "up":
            n = math.ceil(q - 1e-9)
        elif mode == "down":
            n = math.floor(q + 1e-9)
        else:
            raise ValueError(mode)
        return n * self.tick_size


@dataclass(frozen=True)
class CostModel:
    """Per-contract execution friction for one scenario.

    Slippage is expressed in ticks per side and is always adverse. Limit orders get
    no slippage but must trade *through* the limit by ``limit_through_ticks``.
    """

    instrument: str
    scenario: str
    commission_rt: float
    fee_bps_taker: float
    fee_bps_maker: float
    slippage_ticks_market: float
    slippage_ticks_stop: float
    slippage_ticks_limit: float
    eth_slippage_mult: float
    limit_through_ticks: int
    slippage_mult: float = 1.0

    def with_slippage_mult(self, mult: float) -> "CostModel":
        """Copy with a global slippage multiplier (0.5x / 1x / 1.5x / 2x stress tests)."""
        return replace(self, slippage_mult=float(mult))

    @property
    def label(self) -> str:
        return f"{self.instrument}:{self.scenario}:slip{self.slippage_mult:g}x"


@dataclass(frozen=True)
class SessionTemplate:
    """Wall-clock session layout for one market type."""

    name: str
    timezone: str
    trading_day_start: dt.time
    rth_start: dt.time
    rth_end: dt.time
    buckets: tuple[tuple[str, dt.time, dt.time], ...]
    phases: tuple[tuple[str, dt.time, dt.time], ...]
    maintenance_break: tuple[dt.time, dt.time] | None = None
    initial_balance_minutes: int = 60
    bucket_order: tuple[str, ...] = field(default_factory=tuple)


def load_instrument(symbol: str, path: str | Path | None = None) -> InstrumentSpec:
    """Load one instrument from ``configs/instruments.yaml``."""
    cfg = load_yaml(path or CONFIG_DIR / "instruments.yaml")["instruments"]
    if symbol not in cfg:
        raise KeyError(f"instrument {symbol!r} not configured; known: {sorted(cfg)}")
    d = cfg[symbol]
    return InstrumentSpec(
        symbol=symbol,
        tick_size=float(d["tick_size"]),
        tick_value=float(d["tick_value"]),
        point_value=float(d["point_value"]),
        currency=str(d["currency"]),
        session_template=str(d["session_template"]),
        description=str(d.get("description", "")),
        asset_class=str(d.get("asset_class", "")),
        exchange=str(d.get("exchange", "")),
        roll=d.get("roll"),
    )


def load_cost_model(
    symbol: str,
    scenario: str = "BASE",
    slippage_mult: float = 1.0,
    path: str | Path | None = None,
) -> CostModel:
    """Load one cost scenario (LOW / BASE / STRESS) for an instrument."""
    cfg = load_yaml(path or CONFIG_DIR / "costs.yaml")["scenarios"]
    if symbol not in cfg:
        raise KeyError(f"no cost scenarios for {symbol!r}")
    if scenario not in cfg[symbol]:
        raise KeyError(f"no scenario {scenario!r} for {symbol!r}; known: {sorted(cfg[symbol])}")
    d = cfg[symbol][scenario]
    return CostModel(
        instrument=symbol,
        scenario=scenario,
        commission_rt=float(d["commission_rt"]),
        fee_bps_taker=float(d["fee_bps_taker"]),
        fee_bps_maker=float(d["fee_bps_maker"]),
        slippage_ticks_market=float(d["slippage_ticks_market"]),
        slippage_ticks_stop=float(d["slippage_ticks_stop"]),
        slippage_ticks_limit=float(d["slippage_ticks_limit"]),
        eth_slippage_mult=float(d["eth_slippage_mult"]),
        limit_through_ticks=int(d["limit_through_ticks"]),
        slippage_mult=float(slippage_mult),
    )


def _parse_ranges(d: dict[str, list[str]]) -> tuple[tuple[str, dt.time, dt.time], ...]:
    return tuple((name, parse_hhmm(a), parse_hhmm(b)) for name, (a, b) in d.items())


def load_session_template(name: str, path: str | Path | None = None) -> SessionTemplate:
    """Load a session template from ``configs/sessions.yaml``."""
    cfg = load_yaml(path or CONFIG_DIR / "sessions.yaml")["templates"]
    if name not in cfg:
        raise KeyError(f"session template {name!r} not configured; known: {sorted(cfg)}")
    d = cfg[name]
    brk = d.get("maintenance_break")
    buckets = _parse_ranges(d["buckets"])
    return SessionTemplate(
        name=name,
        timezone=str(d["timezone"]),
        trading_day_start=parse_hhmm(d["trading_day_start"]),
        rth_start=parse_hhmm(d["rth"][0]),
        rth_end=parse_hhmm(d["rth"][1]),
        buckets=buckets,
        phases=_parse_ranges(d["phases"]),
        maintenance_break=(parse_hhmm(brk[0]), parse_hhmm(brk[1])) if brk else None,
        initial_balance_minutes=int(d.get("initial_balance_minutes", 60)),
        bucket_order=tuple(b[0] for b in buckets),
    )


def load_research_config(path: str | Path | None = None) -> dict[str, Any]:
    """Global protocol settings (seeds, splits, horizons, statistics)."""
    return load_yaml(path or CONFIG_DIR / "research.yaml")
