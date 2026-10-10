"""The owner's AI choices and the hard caps on paid API use.

Paid API fallback is OFF until the owner turns it on with a monthly budget and a
per-mission cap (``confirm: true``, recorded in an append-only approval log). Changing an
amount needs a new confirmation; no agent can raise a limit, and there is no auto top-up.
Once the monthly budget is reached, no new paid call starts — with "stop at budget" the
paid fallback switches itself off until the owner approves it again.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from jarvis.settings import ModelPrice

Strategy = Literal["subscription_first", "plan_only", "api_only"]
Profile = Literal["economy", "balanced", "deep"]


class PaidFallback(BaseModel):
    enabled: bool = False
    monthly_budget_usd: float = Field(0.0, ge=0, le=5000)
    per_mission_cap_usd: float = Field(0.0, ge=0, le=5000)
    warn_at: list[int] = Field(default_factory=lambda: [50, 80, 100])
    max_concurrent: int = Field(1, ge=1, le=8)
    stop_at_budget: bool = True
    approved_at: str | None = None

    @field_validator("warn_at")
    @classmethod
    def _thresholds(cls, value: list[int]) -> list[int]:
        return sorted({int(v) for v in value if 1 <= int(v) <= 100}) or [100]

    @model_validator(mode="after")
    def _caps(self) -> PaidFallback:
        if self.enabled and (self.monthly_budget_usd <= 0 or self.per_mission_cap_usd <= 0):
            raise ValueError("Paid fallback needs a monthly budget and a per-mission cap above 0.")
        if self.per_mission_cap_usd > self.monthly_budget_usd > 0:
            raise ValueError("The per-mission cap can't exceed the monthly budget.")
        return self


class LocalConfig(BaseModel):
    enabled: bool = False
    base_url: str = "http://127.0.0.1:11434"
    model: str = ""


class AiConfig(BaseModel):
    strategy: Strategy = "subscription_first"
    profile: Profile = "balanced"
    # The Claude plan route runs Claude Code under the owner's own sign-in; the owner turns
    # it on once and confirms that JARVIS is used only by them (Anthropic: plans are for
    # the subscriber; no third-party traffic on subscription limits).
    plan_enabled: bool = False
    plan_confirmed_at: str | None = None
    paid: PaidFallback = Field(default_factory=PaidFallback)
    local: LocalConfig = Field(default_factory=LocalConfig)
    display_currency: Literal["USD", "CHF"] = "USD"
    usd_to_chf: float | None = Field(None, gt=0, lt=10)  # the owner's own rate assumption


def estimate(price: ModelPrice, system: str, messages: list[Any], max_tokens: int) -> float:
    """Worst case of one call: all input uncached-and-written, the full output allowance."""
    chars = len(system) + len(json.dumps(messages, default=str)) + 4000  # + tool schemas
    return (chars / 3 * max(price.input, price.cache_write) + max_tokens * price.output) / 1e6


def actual(price: ModelPrice, usage: Any) -> float:
    return (
        float(
            usage.input_tokens * price.input
            + usage.output_tokens * price.output
            + usage.cache_read_tokens * price.cache_read
            + usage.cache_write_tokens * price.cache_write
        )
        / 1e6
    )
