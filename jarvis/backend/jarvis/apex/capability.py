"""What a data source can and cannot prove — the evidence-capability matrix (spec E4).

Every analytic factor APEX shows carries one evidence status:

- ``CONFIRMED`` — computed on adequate sourced data before the decision time;
- ``CONTRADICTED`` — measured, and it opposes the thesis;
- ``UNAVAILABLE`` — the source lacks the capability; never counted as favourable;
- ``PROXY`` — an indirect, lower-fidelity measure, labelled as such;
- ``STALE`` — the source exists but is not current enough for this use;
- ``NOT_APPLICABLE`` — intentionally excluded by the version's registered rules.

A price-bar feed cannot verify aggressor delta, depth, absorption, a traded-volume POC or options
positioning. Absence of a capability is ``UNAVAILABLE`` — not a pass and not a failure of the
price action itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.apex.data import Series
    from jarvis.settings import ApexMarketSettings


class Evidence(StrEnum):
    CONFIRMED = "CONFIRMED"
    CONTRADICTED = "CONTRADICTED"
    UNAVAILABLE = "UNAVAILABLE"
    PROXY = "PROXY"
    STALE = "STALE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Mode(StrEnum):
    """How current the data behind an analysis is (spec C: live vs historical)."""

    LIVE = "LIVE"  # a legitimately current feed with known entitlement and latency
    DELAYED = "DELAYED"  # current, but older than the live limit
    HISTORICAL = "HISTORICAL"  # stored bars; the newest is not recent enough for live use
    REPLAY = "REPLAY"  # an analysis as of a past moment
    SYNTHETIC = "SYNTHETIC"  # generated test data


@dataclass(frozen=True)
class SourceCapability:
    """One source as APEX may use it (contracts: "Source capability record")."""

    source_id: str
    provider: str
    dataset: str
    market_type: str  # spot_quote | futures | generated
    schema: str  # ohlc | ohlcv | trades | tbbo | mbp_10 | mbo | bid_ask_quotes | macro
    venue: str
    has_bid: bool
    has_ask: bool
    has_actual_traded_volume: bool
    has_aggressor_side: bool
    has_depth: bool
    has_options_oi: bool
    live_entitlement: bool
    tick_size: float
    point_value: float
    licence_state: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["reasons"] = list(self.reasons)
        return out


# Factor → what it needs from the source. Order = display order in the evidence matrix.
FACTORS: dict[str, dict[str, Any]] = {
    "price_structure": {"label": "Swings, structure, BOS/CHoCH", "family": "structure"},
    "price_rejection": {"label": "Closed-bar price rejection", "family": "price_action"},
    "liquidity_sweep": {"label": "Sweep and reclaim of a known level", "family": "price_action"},
    "displacement": {
        "label": "Displacement (range vs ATR, close location)",
        "family": "price_action",
    },
    "session_levels": {"label": "Session / day / week levels", "family": "structure"},
    "volume_profile": {
        "label": "Traded-volume profile (POC, VAH, VAL, HVN, LVN)",
        "family": "auction",
    },
    "vwap": {"label": "Session VWAP on traded volume", "family": "auction"},
    "delta": {"label": "Aggressor delta / CVD", "family": "orderflow"},
    "absorption": {"label": "Absorption (depth + trades)", "family": "orderflow"},
    "spread": {"label": "Observed bid/ask spread", "family": "execution"},
    "options": {"label": "Options positioning (OI, IV, gamma)", "family": "options"},
    "macro_calendar": {"label": "Scheduled macro events", "family": "macro"},
    "cross_market": {
        "label": "Related markets (DXY, yields, ES, VIX, GC)",
        "family": "cross_market",
    },
}


def capability_for(series: Series, spec: ApexMarketSettings) -> SourceCapability:
    """The capability record of the series an analysis uses — from its provenance, never
    from what would be convenient."""
    source_id = str(series.provenance.get("source_id") or series.source)
    spread = series.provenance.get("spread") or {}
    if series.synthetic:
        return SourceCapability(
            source_id, "generated", "apex.data.synthetic_bars", "generated", "ohlc", "none",
            False, False, False, False, False, False, False, spec.tick, spec.point_value,
            "test data",
            ("Generated test data: mechanics only, never a market statement.",),
        )  # fmt: skip
    if series.source == "dukascopy":
        proxy = source_id == "dukascopy:NQ"
        ask = bool(spread.get("measured"))
        return SourceCapability(
            source_id, "dukascopy", "historical candles", "spot_quote", "bid_ask_quotes",
            "OTC (Dukascopy)",
            True, ask, False, False, False, False, False, spec.tick, spec.point_value,
            "free historical data",
            (
                "Broker spot quotes: no centralised exchange trades, no traded volume, no depth.",
                "Ask candles measured for the spread." if ask
                else "Bid only: ASK REQUIRED FOR REALISTIC EXECUTION (spread is an estimate).",
                *(("A CFD on the index — not CME NQ/MNQ futures.",) if proxy else ()),
                "No live entitlement: stored history, refreshed by download.",
            ),
        )  # fmt: skip
    # Data Vault futures bars (Databento / imports): venue OHLCV of real contracts.
    schema = str(series.provenance.get("schema") or "ohlcv-1m")
    return SourceCapability(
        source_id, series.source, str(series.provenance.get("dataset_id") or "vault"), "futures",
        "ohlcv", "CME Globex", False, False, True, False, False, False, False,
        spec.tick, spec.point_value, "owner-imported or approved purchase",
        (
            f"Venue OHLCV bars ({schema}) of actual contracts: traded volume per bar, no trades, "
            "no quotes, no depth.",
            "No live entitlement: stored history.",
        ),
    )  # fmt: skip


def factor_matrix(cap: SourceCapability, *, stale: bool) -> list[dict[str, Any]]:
    """Each factor's status for this source (before any setup-specific measurement)."""
    price = Evidence.STALE if stale else Evidence.CONFIRMED
    rows: dict[str, tuple[Evidence, str]] = {
        "price_structure": (price, "closed bars only"),
        "price_rejection": (price, "closed bars only"),
        "liquidity_sweep": (price, "a known level breached and reclaimed on closed bars"),
        "displacement": (price, "range vs ATR on closed bars"),
        "session_levels": (price, "New York sessions, futures trading day 18:00-17:00"),
        "volume_profile": (
            (Evidence.PROXY, "from bar volume, not from trades — a proxy, never a precise POC")
            if cap.has_actual_traded_volume
            else (Evidence.PROXY, "quote activity (tick counts), not traded volume")
            if cap.market_type == "spot_quote"
            else (Evidence.UNAVAILABLE, "no volume")
        ),
        "vwap": (
            (
                Evidence.CONFIRMED if not stale else Evidence.STALE,
                "session VWAP from venue bar volume (typical price x volume)",
            )
            if cap.has_actual_traded_volume
            else (Evidence.PROXY, "quote-activity weighted — not a traded VWAP")
            if cap.market_type == "spot_quote"
            else (Evidence.UNAVAILABLE, "no volume")
        ),
        "delta": (Evidence.UNAVAILABLE, "needs trades with aggressor side or trades + quotes"),
        "absorption": (
            Evidence.UNAVAILABLE,
            "ABSORPTION NOT MEASURABLE: needs depth (MBP-10/MBO) and trades",
        ),
        "spread": (
            (Evidence.CONFIRMED, "measured from ask candles")
            if cap.has_bid and cap.has_ask
            else (Evidence.PROXY, "estimated — ASK REQUIRED FOR REALISTIC EXECUTION")
            if cap.has_bid
            else (Evidence.NOT_APPLICABLE, "traded prices: spread is inside the cost model")
        ),
        "options": (Evidence.UNAVAILABLE, "no licensed options source connected"),
        "macro_calendar": (Evidence.UNAVAILABLE, "no macro calendar imported"),
        "cross_market": (Evidence.UNAVAILABLE, "no related-market feed connected"),
    }
    if cap.market_type == "generated":
        rows["spread"] = (Evidence.NOT_APPLICABLE, "generated data: flat costs")
    return [
        {"factor": f, **FACTORS[f], "status": rows[f][0].value, "detail": rows[f][1],
         "source_id": cap.source_id}
        for f in FACTORS
    ]  # fmt: skip


def mode_of(series: Series, as_of: int, now: int, *, replay: bool, behind_minutes: int) -> Mode:
    """No source APEX has today carries a live entitlement, so nothing is ever LIVE here: the
    best a current download can be is DELAYED."""
    if series.synthetic:
        return Mode.SYNTHETIC
    if replay or as_of < now - 60:
        return Mode.REPLAY
    return Mode.DELAYED if behind_minutes <= 30 else Mode.HISTORICAL
