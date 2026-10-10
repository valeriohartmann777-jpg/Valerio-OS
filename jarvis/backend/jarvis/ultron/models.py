"""Mission and task states, and the transitions the runtime allows between them."""

from __future__ import annotations

from enum import StrEnum


class MissionState(StrEnum):
    PLANNING = "PLANNING"  # JARVIS is writing the charter and task graph
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"  # only an approval-gated step is left
    BLOCKED = "BLOCKED"  # needs the user: a question, a budget, retries exhausted
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskState(StrEnum):
    DRAFT = "DRAFT"  # waiting for its dependencies
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"  # the runtime runs the task's checks
    RETRYING = "RETRYING"  # failed checks or review; another attempt follows
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


MISSION_DONE = {MissionState.COMPLETE, MissionState.FAILED, MissionState.CANCELLED}
TASK_DONE = {TaskState.COMPLETE, TaskState.FAILED, TaskState.CANCELLED}

_TASK_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.DRAFT: {TaskState.READY, TaskState.CANCELLED, TaskState.BLOCKED},
    TaskState.READY: {TaskState.RUNNING, TaskState.CANCELLED, TaskState.DRAFT},
    TaskState.RUNNING: {
        TaskState.VERIFYING,
        TaskState.RETRYING,
        TaskState.COMPLETE,
        TaskState.BLOCKED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.READY,  # interrupted (pause, restart): runs again from a clean tree
        TaskState.WAITING_APPROVAL,
    },
    TaskState.VERIFYING: {
        TaskState.COMPLETE,
        TaskState.RETRYING,
        TaskState.BLOCKED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.READY,
    },
    TaskState.RETRYING: {
        TaskState.RUNNING,
        TaskState.READY,
        TaskState.DRAFT,
        TaskState.CANCELLED,
        TaskState.BLOCKED,
    },
    TaskState.WAITING_APPROVAL: {
        TaskState.COMPLETE,
        TaskState.CANCELLED,
        TaskState.FAILED,
        TaskState.RUNNING,
    },
    # SENTINEL rejecting reviewed work reopens a finished FORGE task.
    TaskState.COMPLETE: {TaskState.RETRYING, TaskState.DRAFT},
    TaskState.BLOCKED: {TaskState.READY, TaskState.CANCELLED, TaskState.DRAFT},
    TaskState.FAILED: {TaskState.READY},
    TaskState.CANCELLED: set(),
}


class TransitionError(RuntimeError):
    pass


def check_task_transition(current: TaskState, target: TaskState) -> None:
    if target != current and target not in _TASK_TRANSITIONS[current]:
        raise TransitionError(f"task can't go from {current} to {target}")


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"
