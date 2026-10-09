"""Transaction-cost arithmetic (all results in price points per contract)."""

from __future__ import annotations

from typing import Iterable

from ..config import CostModel, InstrumentSpec, load_cost_model

ORDER_TYPES = ("market", "stop", "limit")


def slippage_points(cost: CostModel, tick_size: float, order: str, is_rth: bool) -> float:
    """Adverse slippage for one side of a trade, in points."""
    if order == "market":
        ticks = cost.slippage_ticks_market
    elif order == "stop":
        ticks = cost.slippage_ticks_stop
    elif order == "limit":
        ticks = cost.slippage_ticks_limit
    else:
        raise ValueError(order)
    mult = cost.slippage_mult * (1.0 if is_rth else cost.eth_slippage_mult)
    return ticks * tick_size * mult


def fee_points(cost: CostModel, inst: InstrumentSpec, entry_price: float, exit_price: float, entry_order: str, exit_order: str) -> float:
    """Commission + exchange fees for a round turn, in points.

    Fixed per-contract fees are divided by the point value; percentage fees (crypto)
    use maker rates for limit orders and taker rates otherwise.
    """
    fixed = cost.commission_rt / inst.point_value
    bps_in = cost.fee_bps_maker if entry_order == "limit" else cost.fee_bps_taker
    bps_out = cost.fee_bps_maker if exit_order == "limit" else cost.fee_bps_taker
    return fixed + (bps_in * entry_price + bps_out * exit_price) / 1e4


def scenario_models(symbol: str, scenarios: Iterable[str] = ("LOW", "BASE", "STRESS")) -> list[CostModel]:
    """LOW / BASE / STRESS cost models for an instrument."""
    return [load_cost_model(symbol, s) for s in scenarios]


def slippage_stress_models(symbol: str, scenario: str = "BASE", mults: Iterable[float] = (0.5, 1.0, 1.5, 2.0)) -> list[CostModel]:
    """One cost model per global slippage multiplier."""
    base = load_cost_model(symbol, scenario)
    return [base.with_slippage_mult(m) for m in mults]
