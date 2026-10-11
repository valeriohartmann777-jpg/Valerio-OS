"""The market catalogue of the Backtest Lab: exact futures products, never look-alikes.

NQ and MNQ track the same index but differ tenfold in risk ($5.00 vs $0.50 per tick); ES
and MES likewise. The cash index (NDX, SPX), ETFs (QQQ, SPY) and CFDs are different
instruments and are never offered or substituted. Contract specs here are the reference
table (shown as such); the run uses the provider's definition records when the data has
them (``futures/contracts.py``).
"""

from __future__ import annotations

from typing import Any

from jarvis.quantlab.futures.contracts import REFERENCE

_DETAILS: dict[str, dict[str, Any]] = {
    "NQ": {
        "family": "Nasdaq-100",
        "pair": "MNQ",
        "not": "Not the Nasdaq-100 cash index (NDX), not QQQ, not a CFD.",
    },
    "MNQ": {
        "family": "Nasdaq-100",
        "pair": "NQ",
        "not": "One tenth of NQ per tick. Not the cash index, not QQQ, not a CFD.",
    },
    "ES": {
        "family": "S&P 500",
        "pair": "MES",
        "not": "Not the S&P 500 cash index (SPX), not SPY, not a CFD.",
    },
    "MES": {
        "family": "S&P 500",
        "pair": "ES",
        "not": "One tenth of ES per tick. Not the cash index, not SPY, not a CFD.",
    },
}


def markets() -> list[dict[str, Any]]:
    out = []
    for root in ("NQ", "MNQ", "ES", "MES"):
        p = REFERENCE[root]
        d = _DETAILS[root]
        out.append(
            {
                "symbol": root,
                "name": p.name,
                "asset_class": "Equity index future",
                "venue": "CME Globex",
                "exchange": "XCME",
                "dataset": "GLBX.MDP3",
                "family": d["family"],
                "pair": d["pair"],
                "tick_size": p.tick_size_fixed / 1e9,
                "tick_value": float(p.tick_value),
                "multiplier": float(p.multiplier),
                "point_value": float(p.multiplier),
                "currency": p.currency,
                "contract_months": "H M U Z (Mar, Jun, Sep, Dec)",
                "sessions": "Globex Sun–Fri 18:00–17:00 ET with a daily break; strategies use "
                "their own window (default RTH 09:30–16:00 ET).",
                "continuous": f"{root}.v.0 = Databento's volume-based front month — a selector "
                "of real contracts, prices NOT back-adjusted.",
                "not": d["not"],
                "spec_source": "CME reference table (definition records win when present)",
            }
        )
    return out


def is_market(symbol: str) -> bool:
    return symbol in REFERENCE
