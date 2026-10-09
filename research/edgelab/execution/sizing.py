"""Position sizing for the secondary account simulation.

Primary research is 1R-normalised: signal quality is judged independently of
leverage. The account simulation answers a different question (what would this
have done to an account at 0.25% / 0.50% risk per trade) and never feeds back
into strategy selection.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import InstrumentSpec


def fixed_fractional(
    trades: pd.DataFrame,
    risk_fraction: float,
    initial_equity: float = 100_000.0,
    *,
    compounding: bool = False,
    inst: InstrumentSpec | None = None,
    integer_contracts: bool = False,
) -> pd.DataFrame:
    """Equity path when each trade risks ``risk_fraction`` of equity.

    With ``integer_contracts`` (requires ``inst``) the contract count is floored, so a
    trade whose stop is too wide for the risk budget is skipped. That is how a real
    account behaves and it matters for E-minis on small accounts.
    Default is non-compounding: compounding flatters long backtests.
    """
    if integer_contracts and inst is None:
        raise ValueError("integer_contracts requires the instrument spec")
    eq = initial_equity
    rows = []
    for t in trades.itertuples(index=False):
        base = eq if compounding else initial_equity
        budget = risk_fraction * base
        if integer_contracts:
            per_contract = t.risk_pts * inst.point_value
            n = int(np.floor(budget / per_contract)) if per_contract > 0 else 0
            pnl = n * t.net_pts * inst.point_value
        else:
            n = np.nan
            pnl = t.R_net * budget
        eq += pnl
        rows.append({"exit_ts": t.exit_ts, "contracts": n, "pnl": pnl, "equity": eq, "skipped": integer_contracts and n == 0})
    return pd.DataFrame(rows)


def account_summary(path: pd.DataFrame, initial_equity: float, years: float) -> dict[str, float]:
    """CAGR, max drawdown % and skipped-trade count of an equity path."""
    if path.empty:
        return {"cagr": np.nan, "max_dd_pct": np.nan, "final_equity": initial_equity, "skipped": 0}
    eq = np.r_[initial_equity, path["equity"].to_numpy(float)]
    peak = np.maximum.accumulate(eq)
    dd = 1 - eq / peak
    final = eq[-1]
    cagr = (final / initial_equity) ** (1 / years) - 1 if years > 0 and final > 0 else np.nan
    return {"cagr": float(cagr), "max_dd_pct": float(dd.max() * 100), "final_equity": float(final), "skipped": int(path["skipped"].sum())}
