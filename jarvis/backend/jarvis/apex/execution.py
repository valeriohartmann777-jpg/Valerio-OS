"""How an APEX order plan would have been filled on the data APEX has.

Plan prices are broker order prices: a buy (long entry, short exit) executes on the **ask**,
a sell (short entry, long exit) on the **bid**.

- Dukascopy candles are **bid** prices. The ask is modelled as bid + ``spread``: the session
  median measured from Dukascopy's ask candles of the recent days, otherwise the market's
  configured estimate (``spread_points``). The rest of ``cost_points`` (slippage, commission)
  is deducted from every filled trade. Results carry ``execution_assumption_limited``: a
  modelled spread is not a broker's executable bid/ask feed.
- Data Vault futures bars are traded prices; ``cost_points`` (spread, slippage, commission)
  is deducted flat per round trip.
- A CFD proxy for a futures contract (Dukascopy USA Tech 100 for NQ/MNQ) is no tradable data
  for that contract: the analysis shows structure and zones, but there is no order plan and no
  backtest (``DATA_UNAVAILABLE``).
- Generated test data uses flat costs and is labelled SYNTHETIC everywhere.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.apex.data import Series
    from jarvis.settings import ApexMarketSettings

# Sources whose prices are not the instrument APEX would plan a trade in.
NOT_TRADABLE = {
    "dukascopy:NQ": "Dukascopy's USA Tech 100 CFD is not NQ/MNQ futures data (no contract, no "
    "roll, a basis to the futures). Import NQ/MNQ minute bars into QuantLab → Data Vault.",
}


@dataclass(frozen=True)
class Execution:
    assumption: str  # execution_assumption_limited | futures_trade_prices | synthetic_flat_costs
    quotes: str  # bid | trades | synthetic
    spread: float  # modelled bid/ask spread in price points (0 = inside the flat costs)
    spread_source: str  # measured | estimate | inside_costs
    costs: float  # round-trip costs deducted from R (beyond the modelled spread)
    tradable: bool = True
    reason: str = ""

    @property
    def total_cost(self) -> float:
        return round(self.spread + self.costs, 10)

    def stressed(self, factor: float) -> Execution:
        """Spread and costs multiplied by ``factor`` (cost stress test)."""
        return replace(
            self, spread=round(self.spread * factor, 10), costs=round(self.costs * factor, 10)
        )

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "total_cost": self.total_cost}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Execution:
        fields = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw}
        return cls(**fields)

    def describe(self) -> str:
        if not self.tradable:
            return "No order plan: " + self.reason
        if self.quotes == "bid":
            how = (
                "measured from the ask candles"
                if self.spread_source == "measured"
                else "an estimate (ask candles not measured)"
            )
            return (
                f"Bid prices only — buys modelled at bid + {self.spread:g} ({how}), "
                f"plus {self.costs:g} slippage/commission per round trip."
            )
        if self.quotes == "trades":
            return f"Traded futures prices; {self.costs:g} points per round trip deducted."
        return f"Generated test data; {self.costs:g} points per round trip deducted."


def execution_for(series: Series, spec: ApexMarketSettings) -> Execution:
    source_id = str(series.provenance.get("source_id") or "")
    if series.synthetic:
        return Execution("synthetic_flat_costs", "synthetic", 0.0, "inside_costs", spec.cost_points)
    if source_id in NOT_TRADABLE:
        return Execution(
            "data_unavailable", "bid", 0.0, "inside_costs", spec.cost_points,
            tradable=False, reason=NOT_TRADABLE[source_id],
        )  # fmt: skip
    if series.source == "dukascopy":
        measured = series.provenance.get("spread") or {}
        if measured.get("measured") and float(measured.get("session_median") or 0) > 0:
            spread, origin = float(measured["session_median"]), "measured"
        else:
            spread, origin = spec.spread_points, "estimate"
        return Execution(
            "execution_assumption_limited",
            "bid",
            round(spread, 10),
            origin,
            round(max(spec.cost_points - spread, 0.0), 10),
        )
    return Execution("futures_trade_prices", "trades", 0.0, "inside_costs", spec.cost_points)


def fill_model(candidate: dict[str, Any], default_costs: float) -> tuple[float, float]:
    """(costs, spread) a journaled candidate is judged with — its own, recorded at creation.

    Candidates from before the execution model (engine < 1.2) used flat costs only."""
    recorded = candidate.get("execution")
    if not recorded:
        return default_costs, 0.0
    return float(recorded["costs"]), float(recorded["spread"])
