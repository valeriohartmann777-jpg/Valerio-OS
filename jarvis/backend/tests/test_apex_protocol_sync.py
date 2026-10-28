"""The TypeScript protocol mirrors the backend's signal contract (acceptance test 32).

Every field of ``Signal`` (Pydantic) must be declared in ``ApexSignal`` (packages/protocol), every
gate check in ``ApexGateCheckName``, every setup state in ``ApexSetupState`` and every strategy
in ``ApexStrategyName`` — a renamed or added field fails here before it reaches the window.
"""

from __future__ import annotations

import re
from pathlib import Path

from jarvis.apex import eligibility, setups
from jarvis.apex.engine import STRATEGIES
from jarvis.apex.signal import SetupState, Signal

TS = Path(__file__).resolve().parents[2] / "packages" / "protocol" / "src" / "apex.ts"


def _block(source: str, head: str) -> str:
    start = source.index(head)
    return source[start : source.index("\n}\n", start)]


def _union(source: str, name: str) -> set[str]:
    start = source.index(f"export type {name} =")
    body = source[start : source.index(";", start)]
    return set(re.findall(r'"([A-Za-z0-9_]+)"', body))


def test_every_signal_field_is_declared_in_typescript() -> None:
    source = TS.read_text()
    body = _block(source, "export interface ApexSignal {")
    declared = set(re.findall(r"^\s+([a-z_0-9]+)\??:", body, flags=re.M))
    missing = set(Signal.model_fields) - declared
    assert not missing, f"ApexSignal lacks {sorted(missing)}"


def test_gate_checks_states_and_strategies_match() -> None:
    source = TS.read_text()
    assert _union(source, "ApexGateCheckName") == set(eligibility.CHECKS)
    assert _union(source, "ApexSetupState") == {s.value for s in SetupState}
    assert _union(source, "ApexSetupStage") == set(setups.STAGES)
    assert _union(source, "ApexZoneSetupName") == set(setups.SETUPS)
    names = _union(source, "ApexStrategyName") | _union(source, "ApexZoneSetupName")
    assert names == set(STRATEGIES)
