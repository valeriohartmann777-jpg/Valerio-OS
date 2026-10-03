"""Phase 1 planner: deterministic act → verify plans.

Every side-effecting action is paired with an independent verification step.
Model-driven planning replaces this in Phase 4 behind the same interface.
"""

from __future__ import annotations

from jarvis.core.router import Intent
from jarvis.missions.models import Mission, MissionStep, StepKind
from jarvis.tools.system.apps import AppCatalog
from jarvis.util import join_names


class DeterministicPlanner:
    def __init__(self, catalog: AppCatalog) -> None:
        self._catalog = catalog

    def plan(self, intent: Intent) -> Mission:
        if intent.tool != "open_application" or not intent.targets:
            raise ValueError(f"No plan available for intent {intent.summary!r}")
        names: list[str] = []
        steps: list[MissionStep] = []
        for target in intent.targets:
            app = self._catalog.resolve(target)
            name = app.name if app else target
            names.append(name)
            act = MissionStep(
                index=len(steps),
                title=f"Launch {name}",
                kind=StepKind.ACTION,
                agent="operator",
                tool="open_application",
                args={"name": target},
            )
            steps.append(act)
            steps.append(
                MissionStep(
                    index=len(steps),
                    title=f"Verify {name} is running",
                    kind=StepKind.VERIFY,
                    agent="sentinel",
                    tool="open_application",
                    args={"name": target},
                    depends_on=act.index,
                )
            )
        return Mission(
            title=f"Open {join_names(names)}",
            goal=intent.text,
            steps=steps,
            agents=sorted({step.agent for step in steps}),
        )
