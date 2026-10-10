"""The mission plan JARVIS writes, and the rules it must satisfy before anything runs.

The model proposes; this module decides. A plan is refused (with reasons the
model can fix) unless the task graph is acyclic, every owner is an active
worker, scopes and checks pass the policy, and FORGE tasks carry runnable
checks. Work nobody reviews is not allowed: if no SENTINEL task covers a FORGE
task, a review task is added deterministically.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from jarvis.ultron.agents import WORKERS
from jarvis.ultron.policy import PolicyError, check_command, valid_glob

MAX_TASKS = 12


class PlanError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class PlanTask(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    title: str = Field(min_length=3, max_length=120)
    owner: Literal["axiom", "forge", "sentinel"]
    depends_on: list[str] = Field(default_factory=list, max_length=MAX_TASKS)
    objective: str = Field(min_length=5, max_length=4000)
    deliverables: list[str] = Field(default_factory=list, max_length=12)
    done_when: list[str] = Field(min_length=1, max_length=12)
    allowed_paths: list[str] = Field(default_factory=list, max_length=20)
    checks: list[list[str]] = Field(default_factory=list, max_length=8)

    @field_validator("owner", mode="before")
    @classmethod
    def _lower(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value


class Criterion(BaseModel):
    criterion: str = Field(min_length=3, max_length=500)
    check: list[str] | None = None


class Plan(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    objective: str = Field(min_length=5, max_length=4000)
    in_scope: list[str] = Field(default_factory=list, max_length=20)
    out_of_scope: list[str] = Field(default_factory=list, max_length=20)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    blocking_unknowns: list[str] = Field(default_factory=list, max_length=10)
    success_criteria: list[Criterion] = Field(min_length=1, max_length=12)
    tasks: list[PlanTask] = Field(min_length=1, max_length=MAX_TASKS)


def topological(tasks: list[PlanTask]) -> list[str]:
    """Keys in dependency order; raises PlanError on a cycle."""
    deps = {t.key: set(t.depends_on) for t in tasks}
    order: list[str] = []
    ready = sorted(k for k, d in deps.items() if not d)
    remaining = {k: set(d) for k, d in deps.items() if d}
    while ready:
        key = ready.pop(0)
        order.append(key)
        for other, d in list(remaining.items()):
            d.discard(key)
            if not d:
                del remaining[other]
                ready.append(other)
                ready.sort()
    if remaining:
        raise PlanError([f"dependency cycle among: {', '.join(sorted(remaining))}"])
    return order


def ancestors(tasks: dict[str, PlanTask], key: str) -> set[str]:
    seen: set[str] = set()
    stack = list(tasks[key].depends_on)
    while stack:
        k = stack.pop()
        if k not in seen:
            seen.add(k)
            stack.extend(tasks[k].depends_on)
    return seen


def depth(tasks: dict[str, PlanTask], key: str) -> int:
    deps = tasks[key].depends_on
    return 0 if not deps else 1 + max(depth(tasks, d) for d in deps)


def validate(raw: Any) -> tuple[Plan, list[str]]:
    """The checked plan and notes about what the runtime added or changed."""
    try:
        plan = Plan.model_validate(raw)
    except ValidationError as exc:
        raise PlanError(
            [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:12]]
        ) from exc
    problems: list[str] = []
    notes: list[str] = []
    keys = [t.key for t in plan.tasks]
    if len(set(keys)) != len(keys):
        problems.append("task keys must be unique")
    known = set(keys)
    for t in plan.tasks:
        if t.owner not in WORKERS:
            problems.append(f"{t.key}: {t.owner} isn't an active worker")
        for d in t.depends_on:
            if d == t.key or d not in known:
                problems.append(f"{t.key}: unknown dependency '{d}'")
        if t.owner == "sentinel":
            if t.allowed_paths:
                notes.append(f"{t.key}: SENTINEL never writes; its allowed paths were removed")
                t.allowed_paths = []
        elif not t.allowed_paths:
            problems.append(f"{t.key}: {t.owner.upper()} needs allowed_paths (globs) to write")
        for g in t.allowed_paths:
            if not valid_glob(g):
                problems.append(f"{t.key}: allowed path '{g}' isn't a glob inside the workspace")
        if t.owner == "forge" and not t.checks:
            problems.append(
                f'{t.key}: a FORGE task needs at least one check (e.g. ["python", "-m", '
                '"pytest", "-q"]) — its result must be proven by running it'
            )
        for argv in t.checks:
            try:
                check_command(argv)
            except PolicyError as exc:
                problems.append(f"{t.key}: check {argv} refused — {exc.message}")
    for c in plan.success_criteria:
        if c.check is not None:
            try:
                check_command(c.check)
            except PolicyError as exc:
                problems.append(f"success criterion check {c.check} refused — {exc.message}")
    if problems:
        raise PlanError(problems)
    topological(plan.tasks)

    by_key = {t.key: t for t in plan.tasks}
    reviewed: set[str] = set()
    for t in plan.tasks:
        if t.owner == "sentinel":
            covered = {k for k in ancestors(by_key, t.key) if by_key[k].owner != "sentinel"}
            if not covered:
                raise PlanError([f"{t.key}: a SENTINEL task must depend on the work it reviews"])
            reviewed |= covered
    unreviewed = [t.key for t in plan.tasks if t.owner == "forge" and t.key not in reviewed]
    if unreviewed:
        if len(plan.tasks) >= MAX_TASKS:
            raise PlanError([f"FORGE work without a SENTINEL review: {', '.join(unreviewed)}"])
        key = "verify" if "verify" not in by_key else "verify-final"
        plan.tasks.append(
            PlanTask(
                key=key,
                title="Independent verification",
                owner="sentinel",
                depends_on=unreviewed,
                objective="Independently verify the delivered work against the mission's "
                "success criteria and the reviewed tasks' done_when.",
                deliverables=["acceptance report with evidence"],
                done_when=[c.criterion for c in plan.success_criteria][:12],
            )
        )
        notes.append(f"added '{key}': SENTINEL must review {', '.join(unreviewed)}")
    return plan, notes
