"""Contract master: tick size, tick value and multiplier per traded contract.

Primary source: the provider's ``definition`` records for the exact instrument
ids in the data (point-in-time: the definition in force at the contract's
first bar). Fallback: a small reference table of CME product specifications,
marked ASSUMED and shown as a data-fitness limitation. A definition that
contradicts itself (tick value ≠ tick size × multiplier) or names a different
product than the strategy (MNQ data for an NQ strategy) blocks the run — NQ
and MNQ differ tenfold in risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pyarrow as pa

NANO = Decimal(1_000_000_000)
UNDEF = 9_223_372_036_854_775_807  # DBN's undefined price / quantity


@dataclass(frozen=True)
class Product:
    root: str
    name: str
    tick_size_fixed: int  # 1e-9 units
    tick_value: Decimal
    multiplier: Decimal
    currency: str


# CME specifications, used only when no definition record is available (ASSUMED).
REFERENCE: dict[str, Product] = {
    "NQ": Product("NQ", "E-mini Nasdaq-100", 250_000_000, Decimal("5"), Decimal("20"), "USD"),
    "MNQ": Product(
        "MNQ", "Micro E-mini Nasdaq-100", 250_000_000, Decimal("0.5"), Decimal("2"), "USD"
    ),
    "ES": Product("ES", "E-mini S&P 500", 250_000_000, Decimal("12.5"), Decimal("50"), "USD"),
    "MES": Product(
        "MES", "Micro E-mini S&P 500", 250_000_000, Decimal("1.25"), Decimal("5"), "USD"
    ),
}


@dataclass(frozen=True)
class ContractSpec:
    instrument_id: int
    raw_symbol: str
    root: str
    tick_size_fixed: int
    tick_value: Decimal
    multiplier: Decimal
    currency: str
    expiration_ns: int | None
    provenance: str  # "definition" | "assumed"
    effective_ns: int | None  # ts_recv of the definition used

    @property
    def tick_size(self) -> Decimal:
        return Decimal(self.tick_size_fixed) / NANO

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "raw_symbol": self.raw_symbol,
            "root": self.root,
            "tick_size": str(self.tick_size),
            "tick_value": str(self.tick_value),
            "multiplier": str(self.multiplier),
            "currency": self.currency,
            "expiration_ns": self.expiration_ns,
            "provenance": self.provenance,
            "effective_ns": self.effective_ns,
        }


def _defined(value: Any) -> int | None:
    if value is None:
        return None
    number = int(value)
    return None if number in (UNDEF, -UNDEF - 1) or number <= 0 else number


def build(
    root: str,
    first_ts: dict[int, int],
    definitions: pa.Table | None,
) -> tuple[dict[int, ContractSpec], list[dict[str, Any]]]:
    """Specs for every instrument id in the bars (``first_ts``: id → first bar time)."""
    findings: list[dict[str, Any]] = []
    reference = REFERENCE.get(root)
    by_id: dict[int, list[dict[str, Any]]] = {}
    if definitions is not None:
        for row in definitions.to_pylist():
            by_id.setdefault(int(row["instrument_id"]), []).append(row)
    specs: dict[int, ContractSpec] = {}
    for instrument, first in sorted(first_ts.items()):
        rows = sorted(by_id.get(instrument, []), key=lambda r: int(r.get("ts_recv") or 0))
        before = [r for r in rows if int(r.get("ts_recv") or 0) <= first]
        row = before[-1] if before else (rows[0] if rows else None)
        if row is None:
            if reference is None:
                findings.append(
                    _f(
                        "BLOCK",
                        "NO_CONTRACT_SPEC",
                        f"No definition and no reference spec for {instrument}.",
                    )
                )
                continue
            specs[instrument] = ContractSpec(
                instrument, f"{root}?{instrument}", root, reference.tick_size_fixed,
                reference.tick_value, reference.multiplier, reference.currency, None,
                "assumed", None,
            )  # fmt: skip
            findings.append(
                _f(
                    "WARN", "SPEC_ASSUMED",
                    f"No provider definition for instrument {instrument}: {root} reference "
                    f"specification assumed (tick {reference.tick_size_fixed / 1e9:g}, "
                    f"${reference.tick_value} per tick, ×{reference.multiplier}).",
                )
            )  # fmt: skip
            continue
        if not before:
            findings.append(
                _f(
                    "INFO",
                    "DEFINITION_LATE",
                    f"Definition for {instrument} was published after its first bar.",
                )
            )
        asset = str(row.get("asset") or "").strip()
        raw = str(row.get("raw_symbol") or instrument)
        if asset and asset != root:
            findings.append(
                _f(
                    "BLOCK", "INSTRUMENT_MISMATCH",
                    f"The data's contract {raw} belongs to {asset}, but the strategy trades "
                    f"{root}. Multipliers differ — results would be wrong.",
                )
            )  # fmt: skip
            continue
        tick = _defined(row.get("min_price_increment"))
        qty = _defined(row.get("unit_of_measure_qty"))
        amount = _defined(row.get("min_price_increment_amount"))
        if tick is None:
            findings.append(_f("BLOCK", "NO_TICK_SIZE", f"{raw}: the definition has no tick size."))
            continue
        tick_dec = Decimal(tick) / NANO
        multiplier = Decimal(qty) / NANO if qty is not None else None
        tick_value = Decimal(amount) / NANO if amount is not None else None
        if multiplier is None and tick_value is not None:
            multiplier = tick_value / tick_dec
        if tick_value is None and multiplier is not None:
            tick_value = tick_dec * multiplier
        if multiplier is None or tick_value is None:
            if reference is None:
                findings.append(
                    _f("BLOCK", "NO_MULTIPLIER", f"{raw}: no multiplier in the definition.")
                )
                continue
            multiplier, tick_value = reference.multiplier, reference.tick_value
            findings.append(
                _f(
                    "WARN",
                    "MULTIPLIER_ASSUMED",
                    f"{raw}: multiplier taken from the {root} reference table.",
                )
            )
        elif tick_value != tick_dec * multiplier:
            findings.append(
                _f(
                    "BLOCK", "SPEC_INCONSISTENT",
                    f"{raw}: tick value {tick_value} ≠ tick size {tick_dec} × multiplier {multiplier}.",  # noqa: E501
                )
            )  # fmt: skip
            continue
        if reference is not None and (
            multiplier != reference.multiplier or tick != reference.tick_size_fixed
        ):
            findings.append(
                _f(
                    "WARN", "SPEC_DIFFERS",
                    f"{raw}: definition (tick {tick_dec}, ×{multiplier}) differs from the {root} "
                    "reference table — the definition is used.",
                )
            )  # fmt: skip
        specs[instrument] = ContractSpec(
            instrument_id=instrument,
            raw_symbol=raw,
            root=root,
            tick_size_fixed=tick,
            tick_value=tick_value,
            multiplier=multiplier,
            currency=str(row.get("currency") or "USD"),
            expiration_ns=_defined(row.get("expiration")),
            provenance="definition",
            effective_ns=int(row.get("ts_recv") or 0) or None,
        )
    return specs, findings


def _f(level: str, code: str, message: str) -> dict[str, Any]:
    return {"level": level, "code": code, "message": message, "count": 1, "examples": []}
