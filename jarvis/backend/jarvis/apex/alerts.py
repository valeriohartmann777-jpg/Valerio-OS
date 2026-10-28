"""The alert budget: at most ``cap`` distinct qualified paper alerts per market and trading day.

- A trading day runs from 18:00 to 18:00 New York (the futures day; gold uses the same clock).
- An alert on the same zone and direction as one issued within ``cooldown_minutes`` — by any
  setup — is a duplicate (oscillation around one zone is one idea, not several).
- The same plan is never alerted twice.
- When several plans qualify at one moment, the higher stressed net expected R goes first.
- Zero alerts is a correct outcome: nothing is forced to fill a quota. Rejected and watch
  plans are still journaled for research, without using a slot.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def trading_day(t: int) -> str:
    """The New York futures trading day of ``t`` (a day starts at 18:00 the evening before)."""
    local = datetime.fromtimestamp(t, NY) + timedelta(hours=6)
    return local.date().isoformat()


def admit(
    plan: dict[str, Any],
    issued: list[dict[str, Any]],
    *,
    as_of: int,
    cap: int,
    cooldown_minutes: int,
) -> dict[str, Any]:
    """Whether ``plan`` may become an alert, given the market's ``issued`` alerts."""
    day = trading_day(as_of)
    today = [a for a in issued if a["trading_day"] == day]
    zone = str(plan["zone"])
    for a in issued:
        if a["candidate_id"] == plan["id"]:
            return {"admitted": False, "reason": f"already alerted as {a['id']}", "day": day,
                    "count": len(today)}  # fmt: skip
        if (
            a["zone_id"] == zone
            and a["direction"] == plan["direction"]
            and as_of - int(a["as_of"]) < cooldown_minutes * 60
        ):
            return {
                "admitted": False,
                "reason": f"duplicate: {a['id']} on the same zone and direction "
                f"{(as_of - int(a['as_of'])) // 60} min ago (cooldown {cooldown_minutes} min)",
                "day": day,
                "count": len(today),
            }
    if len(today) >= cap:
        return {"admitted": False, "reason": f"daily maximum of {cap} alerts reached for {day}",
                "day": day, "count": len(today)}  # fmt: skip
    return {"admitted": True, "reason": f"alert {len(today) + 1} of at most {cap} on {day}",
            "day": day, "count": len(today) + 1}  # fmt: skip


def alert_record(
    plan: dict[str, Any], signal_id: str, market: str, as_of: int, eligibility: dict[str, Any]
) -> dict[str, Any]:
    raw = f"{market}|{plan['id']}|{signal_id}"
    return {
        "id": "alr-" + hashlib.sha256(raw.encode()).hexdigest()[:16],
        "market": market,
        "trading_day": trading_day(as_of),
        "signal_id": signal_id,
        "candidate_id": str(plan["id"]),
        "setup": str(plan.get("setup") or plan.get("template")),
        "direction": str(plan["direction"]),
        "zone_id": str(plan["zone"]),
        "as_of": as_of,
        "entry": plan["entry"],
        "stop": plan["stop"],
        "targets": plan["targets"],
        "trade_success_probability": plan.get("trade_success_probability"),
        "net_expected_r": eligibility.get("net_expected_r"),
        "conviction": eligibility.get("conviction"),
        "user_executable": False,
    }
