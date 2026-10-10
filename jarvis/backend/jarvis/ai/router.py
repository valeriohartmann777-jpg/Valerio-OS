"""SmartRouter: one place where every model call of JARVIS, ULTRON and QuantLab is routed.

Order (``subscription_first``): Claude plan → Claude API → local model → pause.

- **Claude plan** — only when the owner enabled it and Claude Code reports a plan sign-in.
- **Claude API** — only with a key AND the owner's one-time approval of paid fallback with
  a monthly budget and a per-mission cap. Every call is checked against both caps before it
  starts; usage is booked per call with provider, model, tokens and cost.
- **Local model** — only if set up, and only for plain conversation.
- Otherwise the call raises ``AIPaused``: ULTRON and QuantLab missions block with an
  ``ai_route`` reason and resume by themselves when a route is available again.

Temporary failures (overload, 429, network) are retried on the same route with backoff and
then rest the route for a cooldown — they never switch work to the paid API. A clear
provider reason (plan limit, sign-in, key, credit, policy) fails over to the next allowed
route. A run that moved to the API stays there until it ends; new runs start on the plan
again once it is available (switching only at a task boundary).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import sys
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from jarvis.ai import local as local_route
from jarvis.ai import messages as msg
from jarvis.ai.classify import classify
from jarvis.ai.keys import ApiKeyStore
from jarvis.ai.plan import AuthStatus, ClaudeCli, PlanChatModel
from jarvis.ai.policy import AiConfig, PaidFallback, actual, estimate
from jarvis.ai.store import AiStore, month_of
from jarvis.ai.types import (
    LABELS,
    LOCAL_CAPABILITIES,
    TEMPORARY,
    AIPaused,
    Capability,
    Failure,
    Provider,
    ProviderStatus,
    TaskScope,
    current_scope,
)
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.llm.base import ChatModel, ModelError, ModelReply, ToolDefinition, ToolOutcome
from jarvis.quantlab.hub.vault import VaultError
from jarvis.settings import AiSettings, ModelPrice
from jarvis.util import utcnow

log = logging.getLogger("jarvis.ai.router")

WEB_SEARCH_USD = 0.01  # per search (Claude API list price, $10 per 1,000)
_ORDERS: dict[str, list[Provider]] = {
    "subscription_first": ["plan", "api", "local"],
    "plan_only": ["plan", "local"],
    "api_only": ["api", "local"],
}
CREDITS_NOTE = (
    "If your Claude Max or Team plan is linked to a Claude Console organization, Anthropic "
    "spends its monthly API credits before purchased credits. JARVIS can't read your credit "
    "balance (no official interface), so it counts every API call against your budget at "
    "list price. Plan limits (Claude / Claude Code) are separate and aren't retrievable "
    "either: JARVIS only knows a limit when Anthropic reports it."
)


@dataclass(frozen=True)
class RoleSpec:
    """One model role: who uses it, what the task needs, the configured model."""

    consumer: str
    role: str
    capability: Capability
    model: str
    effort: str | None = None
    max_tokens: int = 16000
    timeout_seconds: float = 300.0


class _Blocked(Exception):
    pass


ApiFactory = Callable[[str, RoleSpec, str, bool], ChatModel]
Verifier = Callable[[str, list[str]], Awaitable[None]]
Listener = Callable[[], Awaitable[None]]


def _default_api(key: str, spec: RoleSpec, model: str, refusal_fallback: bool) -> ChatModel:
    import anthropic

    from jarvis.llm.anthropic_provider import AnthropicChatModel

    return AnthropicChatModel(
        client=anthropic.AsyncAnthropic(api_key=key),
        model=model,
        max_tokens=spec.max_tokens,
        effort=spec.effort,
        timeout_seconds=spec.timeout_seconds,
        max_retries=1,  # the router retries; SDK retries on top would multiply
        refusal_fallback=refusal_fallback,
    )


async def _default_verify(key: str, models: list[str]) -> None:
    from jarvis.llm.anthropic_provider import verify_key

    await verify_key(key, models)


class RoutedChatModel:
    """What JARVIS, ULTRON and QuantLab hold: a model role whose calls the router places."""

    def __init__(self, router: SmartRouter, spec: RoleSpec) -> None:
        self._router = router
        self.spec = spec

    @property
    def model_id(self) -> str:
        return self._router.resolve_model(self.spec)

    @property
    def label(self) -> str:
        return self._router.label_for(self.spec)

    def user_message(self, blocks: list[str]) -> Any:
        return msg.user_message(blocks)

    def assistant_text(self, text: str) -> Any:
        return msg.assistant_text(text)

    def tool_results(self, outcomes: list[ToolOutcome]) -> Any:
        return msg.tool_results(outcomes)

    async def complete(
        self,
        *,
        system: str,
        messages: list[Any],
        tools: list[ToolDefinition],
        server_tools: list[dict[str, Any]] | None = None,
    ) -> ModelReply:
        return await self._router.complete(self.spec, system, messages, tools, server_tools)

    def will_pay(self, messages: list[Any]) -> bool:
        """Would the next call of this run go to the paid API? (for spend pre-checks)"""
        return self._router.next_provider(self.spec, messages) == "api"


class SmartRouter:
    def __init__(
        self,
        *,
        store: AiStore,
        bus: EventBus,
        settings: AiSettings,
        keys: ApiKeyStore,
        cli: ClaudeCli,
        models_for_check: list[str],
        refusal_fallback: bool = True,
        api_factory: ApiFactory | None = None,
        api_verify: Verifier | None = None,
        local_transport: Any = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.store = store
        self._bus = bus
        self.settings = settings
        self.keys = keys
        self.cli = cli
        self._check_models = models_for_check
        self._refusal_fallback = refusal_fallback
        self._api_factory = api_factory or _default_api
        self._api_verify = api_verify or _default_verify
        self._local_transport = local_transport
        self._sleep = sleep
        self._clock = clock
        self.config = AiConfig()
        self._status: dict[Provider, ProviderStatus] = {
            "plan": ProviderStatus("plan", "UNKNOWN"),
            "api": ProviderStatus("api", "UNKNOWN"),
            "local": ProviderStatus("local", "DISABLED"),
        }
        self._auth: AuthStatus | None = None
        self._auth_at = 0.0
        self._sticky: OrderedDict[int, tuple[Any, Provider]] = OrderedDict()
        self._models: dict[tuple[Any, ...], ChatModel] = {}
        self._paid_slots = asyncio.Semaphore(1)
        self._paid_in_flight = 0
        self._listeners: list[Listener] = []
        self._month_spent = 0.0
        self._month = ""
        self._last_route: str | None = None
        self._was_available: bool | None = None
        self._last_snapshot: dict[Provider, tuple[str, str | None]] = {}
        self._probe: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # lifecycle ---------------------------------------------------------------------------------

    async def start(self, *, probe: bool = True) -> None:
        saved = await self.store.settings()
        if saved:
            try:
                self.config = AiConfig.model_validate(saved)
            except ValueError:
                log.warning("stored AI settings are invalid; using safe defaults (paid off)")
                self.config = AiConfig()
        self._paid_slots = asyncio.Semaphore(self.config.paid.max_concurrent)
        if self.keys.migrate():
            await self._event("key_moved", "API key moved from .env into the OS keystore", "api")
        await self._refresh_month()
        await self.refresh(verify_key=False)
        self._was_available = self.available("planning")
        if probe:
            self._probe = asyncio.create_task(self._probe_loop(), name="ai-probe")

    async def stop(self) -> None:
        if self._probe is not None:
            self._probe.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._probe

    def on_available(self, listener: Listener) -> None:
        """Called when a route becomes available again (paused work can resume)."""
        self._listeners.append(listener)

    # status ------------------------------------------------------------------------------------

    async def refresh(self, *, verify_key: bool = True) -> None:
        await self._refresh_plan(force=True)
        await self._refresh_api(verify=verify_key)
        await self._refresh_local()
        await self._changed()

    async def _refresh_plan(self, *, force: bool = False) -> AuthStatus:
        if not force and self._auth is not None and time.monotonic() - self._auth_at < 60:
            return self._auth
        auth = await self.cli.status()
        self._auth, self._auth_at = auth, time.monotonic()
        current = self._status["plan"]
        version = f"Claude Code {auth.version}" if auth.version else "Claude Code"
        if not auth.installed:
            state, detail = "NOT_INSTALLED", "Claude Code isn't installed on this computer."
        elif not auth.logged_in:
            state, detail = "NOT_LOGGED_IN", "Claude Code isn't signed in to a Claude plan."
        elif not auth.plan_login:
            state = "NOT_SUPPORTED"
            detail = (
                f"Claude Code is signed in with '{auth.method}', not a Claude plan login — "
                "using it would bill the API, so JARVIS doesn't. Sign in with "
                "`claude auth login` (Claude account) to use your plan."
            )
        elif current.state in ("LIMIT_REACHED", "UNAVAILABLE") and self._active(current):
            return auth  # a reported limit stands until its reset time
        elif current.state == "NOT_SUPPORTED" and current.extra.get("policy"):
            return auth  # an account-level refusal stands until the owner checks again
        else:
            state = "CONNECTED"
            detail = "Signed in with your Claude plan" + (
                "" if self.config.plan_enabled else " — not enabled for JARVIS yet"
            )
        self._set("plan", state, detail, label=f"Claude plan ({version})")
        return auth

    async def _refresh_api(self, *, verify: bool) -> None:
        key = self.keys.get()
        current = self._status["api"]
        if key is None:
            self._set("api", "NO_KEY", "No Claude API key saved.")
            return
        if (
            current.state in ("INVALID_KEY",)
            and current.extra.get("hint") == key[-4:]
            and not verify
        ):
            return
        if current.state in (
            "INSUFFICIENT_CREDIT",
            "UNAVAILABLE",
            "BUDGET_REACHED",
        ) and self._active(current):
            return
        if not verify:
            if current.state not in ("CONNECTED",):
                self._set("api", "UNKNOWN", "Key saved; not checked yet.", extra={"hint": key[-4:]})
            return
        try:
            await self._api_verify(key, self._check_models)
        except ModelError as exc:
            failure = classify(exc)
            if failure in (Failure.KEY_INVALID, Failure.POLICY, Failure.BAD_REQUEST):
                self._set("api", "INVALID_KEY", exc.message, extra={"hint": key[-4:]})
            else:
                self._set("api", "UNAVAILABLE", exc.message, until=self._in(60))
            return
        self._set("api", "CONNECTED", "Key verified (free model check).", extra={"hint": key[-4:]})

    async def _refresh_local(self) -> None:
        cfg = self.config.local
        if not cfg.enabled:
            self._set("local", "DISABLED", "No local model set up.")
            return
        ok, detail = await local_route.health(cfg.base_url, cfg.model, self._local_transport)
        self._set("local", "READY" if ok else "NOT_INSTALLED", detail)

    def _set(
        self,
        provider: Provider,
        state: str,
        detail: str,
        *,
        until: datetime | None = None,
        label: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        old = self._status[provider]
        self._status[provider] = ProviderStatus(
            provider,
            state,
            detail,
            checked_at=self._clock().isoformat(),
            until=until.isoformat() if until else None,
            label=label or old.label,
            extra=extra if extra is not None else {},
        )

    def _active(self, status: ProviderStatus) -> bool:
        return bool(status.until) and datetime.fromisoformat(str(status.until)) > self._clock()

    def _in(self, seconds: float) -> datetime:
        return self._clock() + timedelta(seconds=seconds)

    # routing -----------------------------------------------------------------------------------

    def snapshot_sync(self, provider: Provider) -> ProviderStatus:
        return self._status[provider]

    def resolve_model(self, spec: RoleSpec) -> str:
        swap = self.settings.profiles.get(self.config.profile, {})
        if self.config.profile == "deep" and spec.capability == "chat":
            return spec.model  # deep research doesn't slow down conversation
        return swap.get(spec.model, spec.model)

    def label_for(self, spec: RoleSpec) -> str:
        from jarvis.llm.anthropic_provider import _label

        route = self.next_provider(spec, [])
        if route is None:
            return f"{_label(self.resolve_model(spec))} · paused"
        if route == "local":
            return f"{self.config.local.model} · Local AI"
        return f"{_label(self.resolve_model(spec))} · {LABELS[route]}"

    def usable(self, provider: Provider, capability: Capability) -> tuple[bool, str]:
        st = self._status[provider]
        if provider == "plan":
            if not self.config.plan_enabled:
                return False, "not enabled"
            if st.state in ("NOT_INSTALLED", "NOT_LOGGED_IN", "NOT_SUPPORTED"):
                return False, st.detail
            if st.state in ("LIMIT_REACHED", "UNAVAILABLE") and self._active(st):
                return False, st.detail
            return True, ""
        if provider == "api":
            if self.keys.get() is None:
                return False, "no API key"
            if not self.config.paid.enabled:
                return False, "paid fallback not approved"
            if st.state == "INVALID_KEY":
                return False, st.detail
            if st.state in ("INSUFFICIENT_CREDIT", "UNAVAILABLE") and self._active(st):
                return False, st.detail
            if self._month_spent >= self.config.paid.monthly_budget_usd:
                return False, "monthly budget reached"
            return True, ""
        if not self.config.local.enabled or st.state != "READY":
            return False, "no local model ready"
        if capability not in LOCAL_CAPABILITIES:
            return False, "not suitable for this kind of task"
        return True, ""

    def order(self, spec: RoleSpec, messages: list[Any]) -> list[Provider]:
        order = list(_ORDERS[self.config.strategy])
        sticky = self._sticky_get(messages[0] if messages else None)
        if sticky in order:
            order.remove(sticky)
            order.insert(0, sticky)
        return order

    def next_provider(self, spec: RoleSpec, messages: list[Any]) -> Provider | None:
        for provider in self.order(spec, messages):
            if self.usable(provider, spec.capability)[0]:
                return provider
        return None

    def available(self, capability: Capability) -> bool:
        return any(self.usable(p, capability)[0] for p in _ORDERS[self.config.strategy])

    def configured(self) -> bool:
        """Is any route set up at all (for the brain's online/offline state)?"""
        plan = self.config.plan_enabled and self._status["plan"].state not in (
            "NOT_INSTALLED",
            "NOT_SUPPORTED",
        )
        api = (
            self.keys.get() is not None
            and self.config.paid.enabled
            and self._status["api"].state != "INVALID_KEY"
        )
        return plan or api or self.config.local.enabled

    async def complete(
        self,
        spec: RoleSpec,
        system: str,
        messages: list[Any],
        tools: list[ToolDefinition],
        server_tools: list[dict[str, Any]] | None = None,
    ) -> ModelReply:
        scope = current_scope() or TaskScope(spec.consumer, agent=spec.role)
        anchor = messages[0] if messages else None
        skipped: list[str] = []
        no_paid = False  # set after a temporary failure: never switch to paid because of it
        temporary = False
        for provider in self.order(spec, messages):
            ok, why = self.usable(provider, spec.capability)
            if provider == "plan" and ok:
                auth = await self._refresh_plan()
                if not auth.plan_login:  # e.g. an API key slipped into Claude Code's own config
                    await self._changed()
                    ok, why = False, self._status["plan"].detail
            if not ok:
                skipped.append(f"{LABELS[provider]}: {why}")
                continue
            if provider == "api":
                if no_paid:
                    skipped.append("Claude API: not used for a temporary problem elsewhere")
                    continue
                try:
                    await self._check_paid(spec, scope, system, messages)
                except _Blocked as blocked:
                    skipped.append(f"Claude API: {blocked}")
                    continue
            model = self._model(provider, spec)
            try:
                reply = await self._call(
                    provider, model, spec, scope, system, messages, tools, server_tools
                )
            except ModelError as exc:
                failure = classify(exc)
                if failure in (Failure.CAPABILITY, Failure.BAD_REQUEST, Failure.UNKNOWN):
                    raise  # not a routing problem: the caller decides
                if failure in TEMPORARY:
                    cooldown = max(self.settings.cooldown_seconds, exc.retry_after or 0)
                    self._set(provider, "UNAVAILABLE", exc.message, until=self._in(cooldown))
                    await self._event(
                        "temporary",
                        f"{LABELS[provider]} temporarily unavailable ({exc.message}) — resting "
                        f"{int(cooldown)} s",
                        provider,
                        Severity.WARNING,
                    )
                    skipped.append(f"{LABELS[provider]}: {exc.message}")
                    no_paid = temporary = True
                    continue
                await self._fail_over(provider, failure, exc)
                skipped.append(f"{LABELS[provider]}: {exc.message}")
                self._sticky_drop(anchor)
                continue
            self._sticky_set(anchor, provider)
            await self._note_route(provider)
            await self._changed()
            return reply
        await self._changed()
        message = "AI work is paused — " + (
            "; ".join(skipped) if skipped else "no AI route is set up."
        )
        await self._event("paused", message, None, Severity.WARNING, {"consumer": scope.consumer})
        raise AIPaused(message, reasons=skipped, temporary=temporary)

    async def _call(
        self,
        provider: Provider,
        model: ChatModel,
        spec: RoleSpec,
        scope: TaskScope,
        system: str,
        messages: list[Any],
        tools: list[ToolDefinition],
        server_tools: list[dict[str, Any]] | None,
    ) -> ModelReply:
        attempts = self.settings.max_attempts
        for attempt in range(attempts):
            started = time.monotonic()
            try:
                async with self._slot(provider):
                    reply = await model.complete(  # type: ignore[call-arg]
                        system=system, messages=messages, tools=tools, server_tools=server_tools
                    )
            except ModelError as exc:
                failure = classify(exc)
                await self._book_failure(provider, model, scope, failure, started)
                wait = self._backoff(attempt, exc.retry_after)
                if failure in TEMPORARY and attempt + 1 < attempts and wait is not None:
                    await self._sleep(wait)
                    continue
                raise
            await self._book(provider, model, scope, reply, started)
            if self._status[provider].state not in ("CONNECTED", "READY"):
                self._set(
                    provider,
                    "READY" if provider == "local" else "CONNECTED",
                    "Working.",
                    label=self._status[provider].label,
                    extra=self._status[provider].extra,
                )
            return replace(reply, route=provider)
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int, retry_after: float | None) -> float | None:
        if retry_after is not None:
            return float(retry_after) if retry_after <= self.settings.max_backoff_seconds else None
        delay = float(self.settings.backoff_seconds) * float(2.0**attempt)
        return min(delay, float(self.settings.max_backoff_seconds))

    @contextlib.asynccontextmanager
    async def _slot(self, provider: Provider) -> AsyncIterator[None]:
        if provider != "api":
            yield
            return
        async with self._paid_slots:
            self._paid_in_flight += 1
            try:
                yield
            finally:
                self._paid_in_flight -= 1

    async def _fail_over(self, provider: Provider, failure: Failure, exc: ModelError) -> None:
        if provider == "plan":
            if failure is Failure.PLAN_LIMIT:
                seconds = exc.retry_after or self.settings.plan_retry_minutes * 60
                self._set(
                    "plan",
                    "LIMIT_REACHED",
                    "Plan usage limit reached (reported by Anthropic).",
                    until=self._in(max(60.0, seconds)),
                )
            elif failure is Failure.AUTH:
                self._set("plan", "NOT_LOGGED_IN", exc.message)
                self._auth = None
            else:  # POLICY / CREDIT on the plan route
                self._set("plan", "NOT_SUPPORTED", exc.message, extra={"policy": True})
        elif provider == "api":
            key = self.keys.get() or ""
            if failure is Failure.KEY_INVALID or failure is Failure.POLICY:
                self._set("api", "INVALID_KEY", exc.message, extra={"hint": key[-4:]})
            elif failure is Failure.CREDIT:
                self._set("api", "INSUFFICIENT_CREDIT", exc.message, until=self._in(6 * 3600))
            else:
                self._set(
                    "api",
                    "UNAVAILABLE",
                    exc.message,
                    until=self._in(self.settings.cooldown_seconds),
                )
        else:
            self._set("local", "NOT_INSTALLED", exc.message)
        nxt = [p for p in _ORDERS[self.config.strategy] if p != provider]
        await self._event(
            "failover",
            f"{LABELS[provider]}: {exc.message} → trying "
            + (", ".join(LABELS[p] for p in nxt) or "nothing else"),
            provider,
            Severity.WARNING,
            {"failure": failure.value},
        )

    # money -------------------------------------------------------------------------------------

    def price(self, model: str) -> ModelPrice | None:
        return self.settings.prices.get(model)

    async def _refresh_month(self) -> None:
        self._month = month_of(self._clock().isoformat())
        self._month_spent = await self.store.billed(self._month)

    async def _check_paid(
        self, spec: RoleSpec, scope: TaskScope, system: str, messages: list[Any]
    ) -> None:
        paid = self.config.paid
        model = self.resolve_model(spec)
        price = self.price(model)
        if price is None:
            raise _Blocked(f"no price for {model} in config/ai.yaml — not called")
        if month_of(self._clock().isoformat()) != self._month:
            await self._refresh_month()
        worst = estimate(price, system, messages, spec.max_tokens)
        if self._month_spent >= paid.monthly_budget_usd:
            await self._budget_reached()
            raise _Blocked("monthly budget reached")
        if self._month_spent + worst > paid.monthly_budget_usd:
            raise _Blocked(
                f"the next call could exceed the monthly budget (${self._month_spent:.2f} of "
                f"${paid.monthly_budget_usd:.2f} used)"
            )
        if scope.mission_id:
            spent = await self.store.billed(self._month, scope.mission_id)
            if spent + worst > paid.per_mission_cap_usd:
                raise _Blocked(
                    f"per-mission cap: ${spent:.2f} of ${paid.per_mission_cap_usd:.2f} used"
                )

    async def _book(
        self,
        provider: Provider,
        model: ChatModel,
        scope: TaskScope,
        reply: ModelReply,
        started: float,
    ) -> None:
        price = self.price(reply.model) or self.price(model.model_id)
        usage = reply.usage
        searches = usage.web_searches * WEB_SEARCH_USD
        cost = (actual(price, usage) + searches) if provider == "api" and price else 0.0
        list_cost = reply.list_cost_usd
        if list_cost is None and price is not None:
            list_cost = actual(price, usage) + searches
        await self.store.add_usage(
            {
                "provider": provider,
                "model": reply.model or model.model_id,
                "consumer": scope.consumer or "jarvis",
                "agent": scope.agent,
                "mission_id": scope.mission_id,
                "task_id": scope.task_id,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
                "cost_usd": round(cost, 6),
                "billed": int(provider == "api"),
                "list_cost_usd": round(list_cost, 6) if list_cost is not None else None,
                "outcome": "ok",
                "failure": None,
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        )
        if provider == "api":
            await self._paid(cost)

    async def _book_failure(
        self,
        provider: Provider,
        model: ChatModel,
        scope: TaskScope,
        failure: Failure,
        started: float,
    ) -> None:
        await self.store.add_usage(
            {
                "provider": provider,
                "model": model.model_id,
                "consumer": scope.consumer or "jarvis",
                "agent": scope.agent,
                "mission_id": scope.mission_id,
                "task_id": scope.task_id,
                "cost_usd": 0.0,
                "billed": 0,
                "outcome": "error",
                "failure": failure.value,
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        )

    async def _paid(self, cost: float) -> None:
        if month_of(self._clock().isoformat()) != self._month:
            await self._refresh_month()
        else:
            self._month_spent += cost
        paid = self.config.paid
        if paid.monthly_budget_usd <= 0:
            return
        share = self._month_spent / paid.monthly_budget_usd * 100
        for threshold in paid.warn_at:
            marker = f"{self._month}:{threshold}"
            if share >= threshold and not await self.store.has_event("budget_warning", marker):
                await self._event(
                    "budget_warning",
                    f"Claude API: {threshold}% of this month's budget used "
                    f"(${self._month_spent:.2f} of ${paid.monthly_budget_usd:.2f}, estimated at "
                    "list prices)",
                    "api",
                    Severity.WARNING if threshold < 100 else Severity.IMPORTANT,
                    {"marker": marker},
                )
        if self._month_spent >= paid.monthly_budget_usd:
            await self._budget_reached()

    async def _budget_reached(self) -> None:
        paid = self.config.paid
        if self._status["api"].state == "BUDGET_REACHED" and not paid.enabled:
            return
        first = datetime.fromisoformat(self._month + "-01T00:00:00+00:00")
        next_month = (first + timedelta(days=32)).replace(day=1)
        self._set("api", "BUDGET_REACHED", "This month's paid budget is used up.", until=next_month)
        if paid.stop_at_budget and paid.enabled:
            self.config = self.config.model_copy(
                update={"paid": paid.model_copy(update={"enabled": False})}
            )
            await self.store.save_settings(self.config.model_dump())
            await self.store.approve("paid_disabled_at_budget", {"month": self._month})
            await self._event(
                "budget_reached",
                "Monthly budget reached — paid API fallback switched off until you approve it "
                "again. Calls already running finish and are booked.",
                "api",
                Severity.IMPORTANT,
            )
        await self._changed()

    # owner actions -----------------------------------------------------------------------------

    async def _save(self, kind: str, detail: dict[str, Any]) -> None:
        await self.store.save_settings(self.config.model_dump())
        await self.store.approve(kind, detail)
        await self._event("settings", f"AI settings: {kind.replace('_', ' ')}", None)
        await self._changed()

    async def set_strategy(self, strategy: str, profile: str | None = None) -> None:
        update: dict[str, Any] = {"strategy": strategy}
        if profile:
            update["profile"] = profile
        self.config = AiConfig.model_validate({**self.config.model_dump(), **update})
        await self._save("strategy", update)

    async def enable_plan(self, personal_use: bool) -> None:
        if not personal_use:
            raise ValueError(
                "Confirm that JARVIS is used only by you, the Claude plan's subscriber."
            )
        stamp = self._clock().isoformat()
        self.config = self.config.model_copy(
            update={"plan_enabled": True, "plan_confirmed_at": stamp}
        )
        await self._refresh_plan(force=True)
        await self._save("plan_enabled", {"personal_use": True})

    async def disable_plan(self) -> None:
        self.config = self.config.model_copy(update={"plan_enabled": False})
        await self._save("plan_disabled", {})

    async def approve_paid(self, terms: dict[str, Any], confirm: bool) -> None:
        if not confirm:
            raise ValueError("Paid fallback needs your explicit confirmation.")
        stamp = self._clock().isoformat()
        paid = PaidFallback.model_validate({**terms, "enabled": True, "approved_at": stamp})
        self.config = self.config.model_copy(update={"paid": paid})
        self._paid_slots = asyncio.Semaphore(paid.max_concurrent)
        if self._status["api"].state == "BUDGET_REACHED":
            self._set("api", "UNKNOWN", "Budget changed; checking on the next call.")
        await self._refresh_month()
        await self._save("paid_approved", paid.model_dump(exclude={"approved_at"}))

    async def disable_paid(self) -> None:
        paid = self.config.paid.model_copy(update={"enabled": False})
        self.config = self.config.model_copy(update={"paid": paid})
        await self._save("paid_disabled", {})

    async def set_local(self, enabled: bool, base_url: str, model: str) -> None:
        if enabled:
            local_route.local_url(base_url)  # loopback only; raises ValueError
        local = self.config.local.model_copy(
            update={"enabled": enabled, "base_url": base_url, "model": model}
        )
        self.config = self.config.model_copy(update={"local": local})
        await self._refresh_local()
        await self._save("local", {"enabled": enabled, "base_url": base_url, "model": model})

    async def set_currency(self, currency: str, usd_to_chf: float | None) -> None:
        self.config = AiConfig.model_validate(
            {**self.config.model_dump(), "display_currency": currency, "usd_to_chf": usd_to_chf}
        )
        await self._save("currency", {"currency": currency, "usd_to_chf": usd_to_chf})

    async def connect_key(self, key: str) -> None:
        """Verify (free model check), then store in the OS keystore. Raises ModelError."""
        await self._api_verify(key, self._check_models)
        try:
            self.keys.put(key)
        except VaultError as exc:
            raise ModelError("save_failed", exc.message, suggestion=exc.remedy) from exc
        self._models = {k: v for k, v in self._models.items() if k[0] != "api"}
        self._set("api", "CONNECTED", "Key verified (free model check).", extra={"hint": key[-4:]})
        await self.store.approve("api_key_saved", {"hint": key[-4:]})
        await self._event("settings", f"Claude API key …{key[-4:]} saved in the OS keystore", "api")
        await self._changed()

    async def remove_key(self) -> None:
        try:
            self.keys.delete()
        except VaultError as exc:
            raise ModelError("save_failed", exc.message, suggestion=exc.remedy) from exc
        self._models = {k: v for k, v in self._models.items() if k[0] != "api"}
        self._set("api", "NO_KEY", "No Claude API key saved.")
        await self.store.approve("api_key_removed", {})
        await self._changed()

    async def check(self, provider: Provider) -> None:
        if provider == "plan":
            self._set("plan", "UNKNOWN", "Checking…")
            await self._refresh_plan(force=True)
        elif provider == "api":
            self._set("api", "UNKNOWN", "Checking…")
            await self._refresh_api(verify=True)
        else:
            await self._refresh_local()
        await self._changed()

    async def test_plan(self) -> dict[str, Any]:
        """One tiny real call on the plan (the owner clicks "Test"; uses a little plan usage)."""
        spec = RoleSpec("settings", "test", "chat", "claude-haiku-5-5", None, 200, 120)
        model = PlanChatModel(self.cli, model=spec.model, timeout_seconds=120)
        started = time.monotonic()
        scope = TaskScope("settings", agent="test")
        try:
            reply = await model.complete(
                system="Reply with exactly: OK", messages=[msg.user_message(["Test"])], tools=[]
            )
        except ModelError as exc:
            failure = classify(exc)
            await self._book_failure("plan", model, scope, failure, started)
            if (
                failure not in (Failure.CAPABILITY, Failure.BAD_REQUEST, Failure.UNKNOWN)
                and failure not in TEMPORARY
            ):
                await self._fail_over("plan", failure, exc)
            await self._changed()
            return {"ok": False, "message": exc.message, "failure": failure.value}
        await self._book("plan", model, scope, reply, started)
        self._set(
            "plan",
            "CONNECTED",
            "Test call answered on your Claude plan.",
            label=self._status["plan"].label,
        )
        await self._changed()
        return {
            "ok": True,
            "message": reply.text[:80],
            "latency_ms": int((time.monotonic() - started) * 1000),
        }

    async def open_terminal(self, action: str) -> str:
        """macOS: open Terminal with the official command (the owner sees and runs it)."""
        commands = {
            "login": "claude auth login",
            "install": "curl -fsSL https://claude.ai/install.sh | bash",
        }
        command = commands[action]
        if sys.platform != "darwin":
            return command
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            f'tell application "Terminal" to do script "{command}"',
            "-e",
            'tell application "Terminal" to activate',
        )
        await proc.wait()
        return command

    # models ------------------------------------------------------------------------------------

    def _model(self, provider: Provider, spec: RoleSpec) -> ChatModel:
        model = self.resolve_model(spec)
        if provider == "plan":
            key: tuple[Any, ...] = ("plan", model, spec.effort, spec.timeout_seconds)
            if key not in self._models:
                self._models[key] = PlanChatModel(
                    self.cli,
                    model=model,
                    effort=spec.effort,
                    max_turns=self.settings.plan_max_turns,
                    timeout_seconds=max(spec.timeout_seconds, self.settings.plan_timeout_seconds),
                )
            return self._models[key]
        if provider == "api":
            secret = self.keys.get() or ""
            digest = hashlib.sha256(secret.encode()).hexdigest()[:16]
            key = ("api", digest, model, spec.effort, spec.max_tokens, spec.timeout_seconds)
            if key not in self._models:
                self._models = {
                    k: v for k, v in self._models.items() if k[0] != "api" or k[1] == digest
                }
                self._models[key] = self._api_factory(secret, spec, model, self._refusal_fallback)
            return self._models[key]
        cfg = self.config.local
        key = ("local", cfg.base_url, cfg.model)
        if key not in self._models:
            self._models[key] = local_route.LocalChatModel(
                base_url=cfg.base_url, model=cfg.model, transport=self._local_transport
            )
        return self._models[key]

    def _sticky_get(self, anchor: Any) -> Provider | None:
        if anchor is None:
            return None
        item = self._sticky.get(id(anchor))
        return item[1] if item is not None and item[0] is anchor else None

    def _sticky_set(self, anchor: Any, provider: Provider) -> None:
        if anchor is None:
            return
        self._sticky[id(anchor)] = (anchor, provider)
        self._sticky.move_to_end(id(anchor))
        while len(self._sticky) > 512:
            self._sticky.popitem(last=False)

    def _sticky_drop(self, anchor: Any) -> None:
        if anchor is not None:
            self._sticky.pop(id(anchor), None)

    # events ------------------------------------------------------------------------------------

    def current(self) -> dict[str, Any]:
        """What the dashboard shows: the route that would serve work right now."""
        for provider in _ORDERS[self.config.strategy]:
            if provider != "local" and self.usable(provider, "planning")[0]:
                return {
                    "route": provider,
                    "label": LABELS[provider],
                    "reason": self._reason(provider),
                }
        if self.usable("local", "chat")[0]:
            return {
                "route": "local",
                "label": "Local AI",
                "reason": "Claude plan and API unavailable — local model for conversation only; "
                "missions are paused.",
            }
        reasons = [
            f"{LABELS[p]}: {self.usable(p, 'planning')[1].rstrip('.')}"
            for p in _ORDERS[self.config.strategy]
        ]
        return {"route": "paused", "label": "Paused", "reason": " · ".join(reasons)}

    def _reason(self, provider: Provider) -> str:
        if provider == "plan":
            return "Your Claude plan is available."
        why = self.usable("plan", "planning")[1]
        if self.config.strategy == "api_only":
            return "Strategy: Claude API only."
        return f"Claude plan unavailable ({why}) — paid API fallback within your budget."

    async def _note_route(self, provider: Provider) -> None:
        if provider != self._last_route:
            previous, self._last_route = self._last_route, provider
            if previous is not None:
                await self._event(
                    "route",
                    f"AI route: {LABELS[previous]} → {LABELS[provider]}",
                    provider,
                    Severity.IMPORTANT if provider == "api" else Severity.INFO,
                )

    async def _event(
        self,
        kind: str,
        message: str,
        provider: str | None,
        severity: Severity = Severity.INFO,
        data: dict[str, Any] | None = None,
    ) -> None:
        await self.store.event(kind, message, provider, data)
        await self._bus.emit(
            EventType.AI_ROUTE,
            message=message,
            source="ai",
            severity=severity,
            payload={"kind": kind, "provider": provider, **self.current()},
        )

    async def _changed(self) -> None:
        available = self.available("planning")
        if self._was_available is not None and available != self._was_available:
            await self._event(
                "available" if available else "unavailable",
                "AI available again: " + self.current()["label"]
                if available
                else "AI paused: " + self.current()["reason"],
                None,
                Severity.IMPORTANT,
            )
        resumed = available and self._was_available is False
        self._was_available = available
        snapshot = {k: (v.state, v.until) for k, v in self._status.items()}
        if snapshot != self._last_snapshot:
            self._last_snapshot = snapshot
            await self._bus.emit(
                EventType.AI_ROUTE,
                message="AI routing status",
                source="ai",
                severity=Severity.DEBUG,
                payload={"kind": "status", **self.current()},
            )
        if resumed:
            for listener in list(self._listeners):
                try:
                    await listener()
                except Exception:  # one service must not stop the others resuming
                    log.exception("resume listener failed")

    async def _probe_loop(self) -> None:
        while True:
            wait = self.settings.probe_seconds
            for status in self._status.values():
                if status.until:
                    left = (datetime.fromisoformat(status.until) - self._clock()).total_seconds()
                    if left > 0:
                        wait = min(wait, left + 1)
            await asyncio.sleep(max(1.0, wait))
            try:
                if month_of(self._clock().isoformat()) != self._month:
                    await self._refresh_month()
                for provider, status in self._status.items():
                    if (
                        status.until
                        and not self._active(status)
                        and status.state
                        in ("LIMIT_REACHED", "UNAVAILABLE", "INSUFFICIENT_CREDIT", "BUDGET_REACHED")
                    ):
                        self._set(
                            provider,
                            "UNKNOWN",
                            "The earlier limit should have reset.",
                            label=status.label,
                            extra=status.extra,
                        )
                await self._refresh_plan(force=True)
                await self._refresh_local()
                await self._changed()
            except Exception:  # the probe must never die
                log.exception("AI status probe failed")

    # snapshot ----------------------------------------------------------------------------------

    async def snapshot(self) -> dict[str, Any]:
        month = month_of(self._clock().isoformat())
        if month != self._month:
            await self._refresh_month()
        paid = self.config.paid
        api = self._status["api"].as_dict()
        api.update(
            key_hint=self.keys.hint(),
            key_source=self.keys.source(),
            keystore=self.keys.keystore(),
            paid=paid.model_dump(),
            month=month,
            month_spent_usd=round(self._month_spent, 4),
            budget_left_usd=round(max(0.0, paid.monthly_budget_usd - self._month_spent), 4)
            if paid.enabled
            else None,
            in_flight=self._paid_in_flight,
        )
        plan = self._status["plan"].as_dict()
        plan.update(
            enabled=self.config.plan_enabled,
            confirmed_at=self.config.plan_confirmed_at,
            login_method=self._auth.method if self._auth else None,
            version=self._auth.version if self._auth else None,
            usage="Not retrievable — Anthropic reports a limit when it is reached.",
            # A binary set in config/ai.yaml or JARVIS_AI_CLI (the E2E uses a labelled stand-in).
            custom_binary=self.settings.cli_path or None,
        )
        local = self._status["local"].as_dict()
        local.update(self.config.local.model_dump())
        return {
            "strategy": self.config.strategy,
            "profile": self.config.profile,
            "order": [LABELS[p] for p in _ORDERS[self.config.strategy]],
            "current": self.current(),
            "providers": {"plan": plan, "api": api, "local": local},
            "currency": {
                "display": self.config.display_currency,
                "usd_to_chf": self.config.usd_to_chf,
            },
            "usage": await self.store.month_summary(month),
            "by_consumer": await self.store.by_consumer(month),
            "credits_note": CREDITS_NOTE,
            "keystore_test_only": self.settings.keystore == "memory",
        }
