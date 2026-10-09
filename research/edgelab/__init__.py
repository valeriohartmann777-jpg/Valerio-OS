"""edgelab: a skeptical research lab for intraday futures strategies.

Conventions used everywhere in this package
-------------------------------------------
* Bars are indexed by their OPEN time (tz-aware, America/New_York by default).
  A bar labelled ``ts`` covers ``[ts, ts + bar_length)``.
* Every feature value stored on a bar row is the value known at that bar's
  CLOSE (``ts + bar_length``). Nothing in a row may depend on later rows.
* A signal computed on a bar row can be executed at the earliest at the OPEN
  of the next bar.
* Prices are in instrument points; ``R`` is the trade result divided by the
  initial risk (distance from fill price to protective stop).

Synthetic data appears only inside ``tests/``. It is never research evidence.
"""

__version__ = "0.1.0"
