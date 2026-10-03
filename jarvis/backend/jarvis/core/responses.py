"""Composes what JARVIS says, using the configured personality.

Phase 1 replies are templated (no model). Error *reasons* stay structured so
the UI can show "Reason" / "Suggested action" separately from the reply.
"""

from __future__ import annotations

from pydantic import BaseModel

from jarvis.core.router import Intent, IntentKind
from jarvis.missions.models import Mission, MissionStatus, MissionStep, StepKind, StepStatus
from jarvis.settings import PersonalitySettings
from jarvis.tools.base import ToolError, ToolResult
from jarvis.tools.system.metrics import format_duration
from jarvis.util import join_names

_DEFAULTS = {
    "greeting": "Online. Everything is nominal.",
    "capabilities": "I can open applications, report the active window, list running "
    "applications and report system status.",
    "unsupported": "That needs reasoning I don't have yet.",
    "app_opened": "{app} is open.",
    "apps_opened": "{apps} are open.",
    "app_already_open": "{app} was already open.",
    "app_open_unverified": "I started {app}, but I couldn't confirm a window.",
    "action_failed": "I couldn't open {app}.",
    "action_rejected": "Understood. I won't open {app}.",
    "mission_stopped": "Stopped. {done} of {total} steps were completed.",
    "query_failed": "I couldn't do that.",
    "active_window": "You're in {app} — “{title}”.",
    "no_active_window": "I can't see an active window right now.",
    "running_apps": "{count} applications with windows are running: {apps}.",
    "system_status": "CPU at {cpu}%, memory at {memory}% of {memory_total} GB. Up for {uptime}.",
}


class Reply(BaseModel):
    text: str
    success: bool
    error: ToolError | None = None


class ResponseComposer:
    def __init__(self, personality: PersonalitySettings) -> None:
        self._templates = {**_DEFAULTS, **personality.responses}

    def say(self, key: str, **values: object) -> str:
        return " ".join(self._templates[key].format(**values).split())

    def conversation(self, intent: Intent) -> Reply:
        if intent.kind is IntentKind.UNSUPPORTED:
            return Reply(text=self.say("unsupported"), success=True)
        return Reply(text=self.say(intent.topic or "greeting"), success=True)

    def query(self, intent: Intent, result: ToolResult) -> Reply:
        if not result.success:
            return Reply(text=self.say("query_failed"), success=False, error=result.error)
        data = result.data
        match intent.tool:
            case "get_active_window":
                window = data.get("window")
                if not window:
                    return Reply(text=self.say("no_active_window"), success=True)
                return Reply(
                    text=self.say("active_window", app=data.get("app"), title=window["title"]),
                    success=True,
                )
            case "list_running_apps":
                names = [str(a["app"]) for a in data.get("apps", [])]
                shown = names[:6] + ([f"{len(names) - 6} more"] if len(names) > 6 else [])
                return Reply(
                    text=self.say("running_apps", count=len(names), apps=join_names(shown)),
                    success=True,
                )
            case "get_system_info":
                return Reply(
                    text=self.say(
                        "system_status",
                        cpu=f"{data['cpu_percent']:.0f}",
                        memory=f"{data['memory_percent']:.0f}",
                        memory_total=f"{data['memory_total_gb']:.0f}",
                        uptime=format_duration(int(data["uptime_seconds"])),
                    ),
                    success=True,
                )
        return Reply(text=result.summary, success=True)

    def mission(self, mission: Mission, *, stopped: bool) -> Reply:
        actions = [s for s in mission.steps if s.kind is StepKind.ACTION]
        verifications = {
            s.depends_on: s.verification for s in mission.steps if s.kind is StepKind.VERIFY
        }
        succeeded = [s for s in actions if s.status is StepStatus.COMPLETE]
        rejected = [s for s in actions if s.status is StepStatus.REJECTED]
        failed = [s for s in actions if s.status is StepStatus.FAILED]

        if stopped:  # an explicit stop wins, even if it rejected a pending approval
            text = self.say("mission_stopped", done=len(succeeded), total=len(actions))
            return Reply(text=text, success=True)

        parts: list[str] = []
        error: ToolError | None = mission.errors[0] if mission.errors else None
        show_error = bool(failed) or mission.status is MissionStatus.FAILED
        if len(succeeded) == 1:
            step = succeeded[0]
            verification = verifications.get(step.index)
            outcome = step.result.data.get("outcome") if step.result else None
            if verification is not None and not verification.verified:
                parts.append(self.say("app_open_unverified", app=_app_name(step)))
                error = error or ToolError(code="unverified", message=verification.summary)
                show_error = True
            elif outcome == "already_running":
                parts.append(self.say("app_already_open", app=_app_name(step)))
            else:
                parts.append(self.say("app_opened", app=_app_name(step)))
        elif succeeded:
            parts.append(
                self.say("apps_opened", apps=join_names([_app_name(s) for s in succeeded]))
            )
        if rejected:
            parts.append(
                self.say("action_rejected", app=join_names([_app_name(s) for s in rejected]))
            )
        if failed:
            parts.append(self.say("action_failed", app=join_names([_app_name(s) for s in failed])))
        return Reply(
            text=" ".join(parts) or mission.title,
            success=mission.status is MissionStatus.COMPLETE,
            error=error if show_error else None,
        )


def _app_name(step: MissionStep) -> str:
    if step.result is not None and step.result.target:
        return step.result.target
    return step.title.removeprefix("Launch ")
