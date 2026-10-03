from __future__ import annotations

import asyncio

import pytest

from jarvis.core.trace import TraceContext
from jarvis.events.bus import EventBus
from jarvis.events.types import Event, EventType
from jarvis.permissions.models import ActionDescriptor, ApprovalPolicy, PermissionLevel
from jarvis.permissions.service import (
    ApprovalError,
    PermissionService,
    StrongConfirmationRequired,
)
from jarvis.settings import PermissionSettings


def action(level: PermissionLevel, category: str | None = None) -> ActionDescriptor:
    return ActionDescriptor(
        title="Do thing", target="Target", summary="Does the thing.", level=level, category=category
    )


def service(**overrides: object) -> tuple[PermissionService, EventBus, list[Event]]:
    bus = EventBus()
    events: list[Event] = []

    async def record(event: Event) -> None:
        events.append(event)

    bus.subscribe("permission.*", record)
    settings = PermissionSettings(approval_timeout_seconds=1.0).model_copy(update=overrides)
    return PermissionService(settings, bus), bus, events


@pytest.mark.parametrize(
    ("level", "policy"),
    [
        (PermissionLevel.READ, ApprovalPolicy.AUTO),
        (PermissionLevel.SAFE_ACTION, ApprovalPolicy.AUTO),
        (PermissionLevel.MODIFICATION, ApprovalPolicy.CONFIRM),
        (PermissionLevel.EXTERNAL_EFFECT, ApprovalPolicy.CONFIRM),
        (PermissionLevel.HIGH_RISK, ApprovalPolicy.STRONG_CONFIRM),
    ],
)
def test_default_policy_per_level(level: PermissionLevel, policy: ApprovalPolicy) -> None:
    svc, _, _ = service()
    assert svc.policy_for("any_tool", action(level)) is policy


def test_financial_category_is_disabled_and_overrides_apply() -> None:
    svc, _, _ = service(tool_overrides={"open_application": ApprovalPolicy.CONFIRM})
    assert svc.policy_for("x", action(PermissionLevel.READ, "financial")) is ApprovalPolicy.DENY
    assert (
        svc.policy_for("open_application", action(PermissionLevel.READ)) is ApprovalPolicy.CONFIRM
    )


async def test_auto_levels_do_not_ask() -> None:
    svc, _, events = service()
    decision = await svc.authorize(
        "t", action(PermissionLevel.SAFE_ACTION), reason="r", ctx=TraceContext.new()
    )
    assert decision.approved and decision.approval_label == "auto"
    assert events == []


async def test_denied_category_is_refused_without_asking() -> None:
    svc, _, events = service()
    decision = await svc.authorize(
        "t", action(PermissionLevel.READ, "financial"), reason="r", ctx=TraceContext.new()
    )
    assert not decision.approved
    assert "Financial" in decision.note
    assert events == []


async def test_confirm_flow_approve() -> None:
    svc, _, events = service()
    task = asyncio.create_task(
        svc.authorize(
            "t", action(PermissionLevel.MODIFICATION), reason="user asked", ctx=TraceContext.new()
        )
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    [request] = svc.pending()
    assert request.reason == "user asked"
    assert request.action.target == "Target"
    await svc.approve(request.id)
    decision = await task
    assert decision.approved and decision.approval_label == "approved"
    assert [e.type for e in events] == [
        EventType.PERMISSION_REQUESTED,
        EventType.PERMISSION_APPROVED,
    ]
    assert svc.pending() == []


async def test_confirm_flow_reject() -> None:
    svc, _, events = service()
    task = asyncio.create_task(
        svc.authorize(
            "t", action(PermissionLevel.EXTERNAL_EFFECT), reason="r", ctx=TraceContext.new()
        )
    )
    await asyncio.sleep(0.01)
    await svc.reject(svc.pending()[0].id)
    decision = await task
    assert not decision.approved and decision.approval_label == "rejected"
    assert events[-1].type is EventType.PERMISSION_REJECTED


async def test_high_risk_requires_strong_confirmation() -> None:
    svc, _, _ = service()
    task = asyncio.create_task(
        svc.authorize("t", action(PermissionLevel.HIGH_RISK), reason="r", ctx=TraceContext.new())
    )
    await asyncio.sleep(0.01)
    request_id = svc.pending()[0].id
    with pytest.raises(StrongConfirmationRequired):
        await svc.approve(request_id)
    await svc.approve(request_id, strong_confirmation=True)
    assert (await task).approved


async def test_unanswered_request_expires() -> None:
    svc, _, events = service(approval_timeout_seconds=0.05)
    decision = await svc.authorize(
        "t", action(PermissionLevel.MODIFICATION), reason="r", ctx=TraceContext.new()
    )
    assert not decision.approved
    assert events[-1].type is EventType.PERMISSION_EXPIRED
    with pytest.raises(ApprovalError):
        await svc.approve(decision.request_id or "")
