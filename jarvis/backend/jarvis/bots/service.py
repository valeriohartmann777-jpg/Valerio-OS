"""Bot Lab: backtest the user's MT5 EAs and improve them with Claude — honestly.

- **Import**: an EA from the user's MQL5 folder becomes version 0 (its source
  is stored; the user's file is never changed).
- **Backtest**: a version is compiled with the deal export in JARVIS's test
  terminal and run over the whole period once. Statistics are cut into
  in-sample, out-of-sample and holdout by closing time.
- **Improve**: in rounds, Claude reads the source and the in-sample results,
  writes new versions as exact edits, backtests and validates them. The
  out-of-sample bar rises with every validation (Bonferroni); the holdout is
  shown only to the user. Within ``daily_budget_usd``; stops itself after
  ``stall_limit`` rounds without a new validated version.
- **Install**: on the user's click, a version is copied into their MQL5
  folder (``Experts/JARVIS/<bot>/``) — never over the original, never onto a
  chart. Trading it, on demo first, is the user's decision.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from jarvis.bots.mql import (
    Edit,
    EditError,
    Source,
    apply_edits,
    diff_summary,
    instrument,
    parse_inputs,
    read_source,
    write_source,
)
from jarvis.bots.mt5 import (
    MODELS,
    PERIODS,
    CompileResult,
    MetaTrader,
    Mt5Error,
    Mt5Paths,
    RunResult,
    RunSpec,
    discover,
    list_experts,
)
from jarvis.bots.prompt import SYSTEM_PROMPT, TOOLS, briefing
from jarvis.bots.stats import (
    HOLDOUT_T,
    DealsError,
    PropRules,
    Stats,
    parse_deals,
    prop_check,
    scaling,
    summarize,
    t_required,
)
from jarvis.bots.store import BotStore
from jarvis.events.bus import EventBus
from jarvis.events.types import Event, EventType, Severity
from jarvis.learning.service import ResearchModel
from jarvis.llm.base import ModelError, ModelReply, ToolOutcome, Usage
from jarvis.settings import BotsSettings, ModelPrice
from jarvis.storage.preferences import Preferences
from jarvis.util import utcnow, wait_wall

log = logging.getLogger("jarvis.bots")

IMPROVING = "bots.improving"
# ai_paused: no AI route right now (jarvis.ai) — look again in 5 minutes.
_RETRYABLE = {
    "rate_limited",
    "overloaded",
    "timeout",
    "connection",
    "server_error",
    "ai_paused",
}
# Never in a version JARVIS writes (unless the user's original already has it).
_FORBIDDEN = ("#import", "WebRequest", "SocketCreate", "SocketConnect", "FileOpen",
              "FileWrite", "FileDelete", "ShellExecute", "SendMail", "SendFTP")  # fmt: skip
_INCLUDE = re.compile(r'^\s*#include\s+"([^"]+)"', re.M)


class BotLabError(ValueError):
    pass


class Tester(Protocol):
    """What the lab needs from MetaTrader (a fake in tests)."""

    paths: Mt5Paths

    @property
    def ready(self) -> bool: ...

    def experts_dir(self) -> Path: ...

    def set_up(self) -> None: ...

    def sync(self) -> None: ...

    def user_terminal_running(self) -> bool: ...

    async def open_test_terminal(self) -> None: ...

    async def compile(self, source: Path) -> CompileResult: ...

    async def backtest(self, spec: RunSpec) -> RunResult: ...


class LabState(StrEnum):
    IDLE = "idle"
    WORKING = "working"  # setting up, compiling or backtesting on request
    IMPROVING = "improving"  # a research round is running
    WAITING = "waiting"  # between rounds
    BUDGET = "budget"
    STALLED = "stalled"
    NEEDS_BRAIN = "needs_brain"
    ERROR = "error"


@dataclass(frozen=True)
class BotLabStatus:
    state: LabState
    detail: str | None
    ready: bool
    user_terminal_running: bool
    checks: list[dict[str, Any]]
    experts: list[dict[str, Any]]
    improving: str | None
    model: str
    budget_usd: float
    spent_today_usd: float
    next_round_at: str | None
    running: list[dict[str, Any]] = field(default_factory=list)


def bot_name(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", path.stem).strip("_") or "EA"


def forbidden_additions(before: str, after: str) -> list[str]:
    return [word for word in _FORBIDDEN if after.count(word) > before.count(word)]


def local_includes(path: Path, root: Path) -> list[Path]:
    """Files an EA includes with quotes (relative to itself), recursively."""
    found: list[Path] = []
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            text = read_source(current).text
        except OSError:
            continue
        for name in _INCLUDE.findall(text):
            target = (current.parent / name.replace("\\", "/")).resolve()
            if target.exists() and target not in found and root in target.parents:
                found.append(target)
                pending.append(target)
    return found


class BotLabService:
    def __init__(
        self,
        *,
        settings: BotsSettings,
        prices: dict[str, ModelPrice],
        store: BotStore,
        bus: EventBus,
        preferences: Preferences,
        model_factory: Callable[[], ResearchModel | None],
        tester_factory: Callable[[Mt5Paths], Tester] | None = None,
        discover_paths: Callable[[], Mt5Paths] | None = None,
        today: Callable[[], date] = lambda: datetime.now().astimezone().date(),
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self._settings = settings
        self._prices = prices
        self._store = store
        self._bus = bus
        self._prefs = preferences
        self._model_factory = model_factory
        self._tester_factory = tester_factory or (lambda p: MetaTrader(p, settings))
        self._discover = discover_paths or (lambda: discover(settings))
        self._today = today
        self._now = now
        self._paths = Mt5Paths()
        self._tester: Tester = self._tester_factory(self._paths)
        self._state = LabState.IDLE
        self._detail: str | None = None
        self._next_round_at: str | None = None
        self._spent_today = 0.0
        self._running: dict[str, dict[str, Any]] = {}
        self._jobs: set[asyncio.Task[Any]] = set()
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._unsubscribe = bus.subscribe(EventType.BRAIN_CHANGED, self._on_brain)

    # Status ------------------------------------------------------------------------------

    @property
    def improving(self) -> str | None:
        value = self._prefs.get(IMPROVING)
        return value if isinstance(value, str) else None

    def refresh_discovery(self) -> None:
        self._paths = self._discover()
        self._tester = self._tester_factory(self._paths)

    def _experts(self) -> list[Path]:
        data = self._paths.data_dir
        return list_experts(data) if data else []

    def checks(self) -> list[dict[str, Any]]:
        p = self._paths
        experts = self._experts()
        test = p.test_dir
        items = [
            ("app", p.app is not None or p.terminal_dir is not None, "MetaTrader 5",
             str(p.app or p.terminal_dir or "not found — install MetaTrader 5 for Mac")),
            ("wine", p.wine is not None or (p.prefix is None and p.terminal_dir is not None),
             "Wine (inside the app)", str(p.wine or "not found")),
            ("data", p.data_dir is not None, "Your MQL5 folder",
             f"{p.data_dir} · {len(experts)} EA(s) with source" if p.data_dir else "not found"),
            ("test", self._tester.ready, "JARVIS test terminal",
             str(test) if self._tester.ready else "not set up yet"),
        ]  # fmt: skip
        return [{"key": k, "ok": ok, "label": label, "detail": d} for k, ok, label, d in items]

    def status(self) -> BotLabStatus:
        return BotLabStatus(
            state=self._state,
            detail=self._detail,
            ready=self._tester.ready,
            user_terminal_running=self._tester.user_terminal_running(),
            checks=self.checks(),
            experts=[
                {"name": bot_name(path), "path": str(path), "file": path.name}
                for path in self._experts()
            ],
            improving=self.improving,
            model=self._settings.research.model,
            budget_usd=self._settings.research.daily_budget_usd,
            spent_today_usd=round(self._spent_today, 4),
            next_round_at=self._next_round_at,
            running=list(self._running.values()),
        )

    async def _set(
        self,
        state: LabState,
        detail: str | None = None,
        *,
        message: str = "",
        severity: Severity = Severity.DEBUG,
        next_round_at: datetime | None = None,
    ) -> None:
        self._state, self._detail = state, detail
        self._next_round_at = next_round_at.isoformat() if next_round_at else None
        await self._emit(message or detail or f"Bot Lab: {state}", severity)

    async def _emit(self, message: str, severity: Severity = Severity.DEBUG) -> None:
        self._spent_today = await self._store.spent_on(self._today().isoformat())
        status = await asyncio.to_thread(self.status)
        await self._bus.emit(
            EventType.BOTS_CHANGED,
            message=message,
            source="bots",
            severity=severity,
            payload={"bots": asdict(status)},
        )

    # Lifecycle ---------------------------------------------------------------------------

    async def start(self) -> None:
        await asyncio.to_thread(self.refresh_discovery)
        stale = await self._store.interrupted()
        if stale:
            log.info("marked %d interrupted backtest(s)", stale)
        self._spent_today = await self._store.spent_on(self._today().isoformat())
        if self.improving and self._settings.enabled:
            self._spawn()

    async def stop(self) -> None:
        self._unsubscribe()
        await self._cancel()
        for job in list(self._jobs):
            job.cancel()
        for job in list(self._jobs):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await job

    async def _on_brain(self, _: Event) -> None:
        self._wake.set()

    # Setup -------------------------------------------------------------------------------

    async def set_up(self) -> BotLabStatus:
        await self._set(LabState.WORKING, "Setting up the test terminal (copying MetaTrader)…")
        try:
            await asyncio.to_thread(self._tester.set_up)
        except (Mt5Error, OSError) as exc:
            await self._set(LabState.ERROR, f"Setup failed: {exc}", severity=Severity.WARNING)
            raise BotLabError(str(exc)) from exc
        await self._set(LabState.IDLE, message="Test terminal ready", severity=Severity.INFO)
        return self.status()

    async def open_test_terminal(self) -> None:
        try:
            await self._tester.open_test_terminal()
        except Mt5Error as exc:
            raise BotLabError(str(exc)) from exc

    # Bots --------------------------------------------------------------------------------

    def _default_settings(self) -> dict[str, Any]:
        s = self._settings
        return {
            "symbol": s.symbol,
            "period": s.period,
            "model": s.model,
            "deposit": s.deposit,
            "currency": s.currency,
            "leverage": s.leverage,
        }

    async def import_bot(self, name: str) -> dict[str, Any]:
        """Version 0 of an EA from the user's MQL5 folder (re-imported if changed)."""
        path = next((p for p in self._experts() if bot_name(p) == name), None)
        if path is None:
            raise BotLabError(f"There is no EA called {name} in your MQL5 folder.")
        source = await asyncio.to_thread(read_source, path)
        known = await self._store.bot(name)
        if known is None:
            await self._store.add_bot(name, str(path), self._default_settings())
            await self._store.add_version(
                name,
                parent=None,
                title="Original",
                hypothesis="",
                source=source.text,
                compiled=False,
                errors=[],
                diff=None,
            )
        else:
            original = await self._store.version(name, 0)
            if original and original["source"] != source.text:
                await self._store.set_source(name, 0, source.text)
                await self._store.set_compiled(name, 0, False, [])
        await self._emit(f"EA {name} imported", Severity.INFO)
        detail = await self.detail(name)
        assert detail is not None
        return detail

    async def update_settings(self, name: str, changes: dict[str, Any]) -> dict[str, Any]:
        bot = await self._store.bot(name)
        if bot is None:
            raise BotLabError(f"{name} isn't imported.")
        settings = {**bot["settings"]}
        for key, value in changes.items():
            if value is None:
                continue
            if key == "symbol" and isinstance(value, str) and re.fullmatch(r"[\w.#-]{1,30}", value):
                settings[key] = value
            elif key == "period" and value in PERIODS:
                settings[key] = value
            elif key == "model" and value in MODELS:
                settings[key] = value
            elif key in ("deposit", "leverage") and isinstance(value, int | float) and value > 0:
                settings[key] = int(value) if key == "leverage" else float(value)
            else:
                raise BotLabError(f"invalid {key}: {value!r}")
        await self._store.set_settings(name, settings)
        await self._emit(f"{name}: tester settings changed")
        return settings

    async def detail(self, name: str) -> dict[str, Any] | None:
        bot = await self._store.bot(name)
        if bot is None:
            return None
        original = await self._store.version(name, 0)
        inputs = parse_inputs(original["source"]) if original else []
        tests = await self._store.tests(name, limit=60)
        best = self._best(tests)
        reference = best or next((t for t in tests if t["status"] == "done"), None)
        return {
            "name": name,
            "path": bot["path"],
            "settings": bot["settings"],
            "inputs": [asdict(i) for i in inputs],
            "versions": await self._store.versions(name),
            "tests": tests,
            "notes": await self._store.notes(name),
            "rounds": await self._store.rounds(name, limit=10),
            "best_test": best["number"] if best else None,
            "projection": self._projection(reference, bot["settings"]) if reference else None,
            "periods": self._periods(),
        }

    def _best(self, tests: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The validated test with the best out-of-sample return per drawdown."""
        validated = [t for t in tests if t["validated"] and t["out_of_sample"]]

        def score(t: dict[str, Any]) -> float:
            oos = t["out_of_sample"]
            return float(oos["return_pct"]) / max(float(oos["max_drawdown_pct"]), 0.5)

        return max(validated, key=score) if validated else None

    def _projection(self, test: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any] | None:
        """What 10k a month would take, from the months the EA wasn't tuned on."""
        unseen = test.get("unseen")
        if not unseen:
            return None
        fields = Stats.__dataclass_fields__
        stats = Stats(**{k: v for k, v in unseen.items() if k in fields and k != "monthly"})
        prop = self._settings.prop_firm
        target = self._settings.target_monthly_usd
        return {
            "test": test["number"],
            "basis": "out-of-sample + holdout",
            "prop_firm": scaling(
                stats, account=prop.account, drawdown_limit_pct=prop.max_loss_pct, target=target
            ),
            "own_account": scaling(
                stats,
                account=float(settings["deposit"]),
                drawdown_limit_pct=self._settings.own_drawdown_limit_pct,
                target=target,
            ),
        }

    def _periods(self) -> dict[str, str]:
        s = self._settings
        end = self._today() - timedelta(days=1)
        return {
            "in_sample": f"{s.data_start} to {s.oos_start - timedelta(days=1)}",
            "out_of_sample": f"{s.oos_start} to {s.holdout_start - timedelta(days=1)}",
            "holdout": f"{s.holdout_start} to {end}",
        }

    async def source(self, name: str, number: int) -> dict[str, Any]:
        version = await self._store.version(name, number)
        if version is None:
            raise BotLabError(f"{name} has no version {number}.")
        diff = ""
        if version["parent"] is not None:
            parent = await self._store.version(name, version["parent"])
            if parent:
                diff = "\n".join(
                    difflib.unified_diff(
                        parent["source"].split("\n"),
                        version["source"].split("\n"),
                        f"v{parent['number']}",
                        f"v{number}",
                        lineterm="",
                    )
                )
        return {"number": number, "source": version["source"], "diff": diff}

    # Versions and compiling ----------------------------------------------------------------

    def _version_file(self, name: str, number: int) -> Path:
        return self._tester.experts_dir() / "JARVIS" / name / f"{name}_v{number}.mq5"

    async def _compile(self, name: str, number: int) -> CompileResult:
        """Write the version with the deal export into the test terminal and compile it."""
        version = await self._store.version(name, number)
        bot = await self._store.bot(name)
        if version is None or bot is None:
            raise BotLabError(f"{name} has no version {number}.")
        target = self._version_file(name, number)

        def write() -> None:
            self._tester.sync()
            self._copy_includes(Path(bot["path"]), target.parent)
            text = instrument(version["source"], _export_name(name, number))
            write_source(target, Source(text, "utf-8-sig", "\r\n"))

        await asyncio.to_thread(write)
        result = await self._tester.compile(target)
        await self._store.set_compiled(name, number, result.ok, result.errors)
        return result

    def _copy_includes(self, original: Path, folder: Path) -> None:
        """Files the EA includes with quotes go along, at the same relative place."""
        data = self._paths.data_dir
        root = data / "Experts" if data else original.parent
        for include in local_includes(original, root):
            if original.parent in include.parents:
                relative = include.relative_to(original.parent)
            else:
                relative = Path(include.name)
            destination = folder / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(include.read_bytes())

    async def create_version(
        self, name: str, base: int, title: str, hypothesis: str, edits: list[Edit]
    ) -> tuple[int, CompileResult]:
        parent = await self._store.version(name, base)
        if parent is None:
            raise BotLabError(f"There is no version {base}.")
        try:
            text = apply_edits(parent["source"], edits)
        except EditError as exc:
            raise BotLabError(str(exc)) from exc
        bad = forbidden_additions(parent["source"], text)
        if bad:
            raise BotLabError(
                f"Refused: the edits add {', '.join(bad)} — not allowed in an EA version."
            )
        number = await self._store.add_version(
            name,
            parent=base,
            title=title[:80] or f"Version from v{base}",
            hypothesis=hypothesis[:500],
            source=text,
            compiled=False,
            errors=[],
            diff=diff_summary(parent["source"], text),
        )
        result = await self._compile(name, number)
        await self._emit(
            f"{name} v{number} — {title}: " + ("compiled" if result.ok else "compile errors"),
            Severity.INFO,
        )
        return number, result

    # Backtests ---------------------------------------------------------------------------

    def start_backtest(self, name: str, version: int, inputs: dict[str, str]) -> None:
        """A backtest on the user's request, in the background."""
        job = asyncio.create_task(self.backtest(name, version, inputs, origin="user"))
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)

    async def backtest(
        self, name: str, version: int, inputs: dict[str, str], *, origin: str
    ) -> dict[str, Any]:
        bot = await self._store.bot(name)
        info = await self._store.version(name, version)
        if bot is None or info is None:
            raise BotLabError(f"{name} has no version {version}.")
        if not self._tester.ready:
            raise BotLabError("Set up the test terminal first (Bots page → Set up).")
        settings = bot["settings"]
        test_id, number = await self._store.start_test(name, version, inputs, settings, origin)
        key = f"{name}#{number}"
        self._running[key] = {
            "bot": name,
            "test": number,
            "version": version,
            "started_at": utcnow().isoformat(),
        }
        if self._state in (LabState.IDLE, LabState.ERROR):
            await self._set(LabState.WORKING, f"Backtesting {name} v{version}")
        else:
            await self._emit(f"Backtesting {name} v{version}")
        started = utcnow()
        try:
            # Always compiled afresh: the test terminal may have been set up again.
            compiled = await self._compile(name, version)
            if not compiled.ok:
                raise BotLabError("doesn't compile: " + "; ".join(compiled.errors[:3]))
            spec = RunSpec(
                expert=f"JARVIS\\{name}\\{name}_v{version}",
                symbol=settings["symbol"],
                period=settings["period"],
                model=MODELS[settings["model"]],
                start=self._settings.data_start,
                end=self._today() - timedelta(days=1),
                deposit=float(settings["deposit"]),
                currency=settings.get("currency", "USD"),
                leverage=int(settings["leverage"]),
                export_file=_export_name(name, version),
                report=f"JARVIS\\reports\\{name}_T{number}",
                inputs=inputs,
            )
            run = await self._tester.backtest(spec)
            if run.deals is None:
                raise BotLabError(run.error or "no results")
            periods = await asyncio.to_thread(self._periods_of, run.deals)
        except (BotLabError, Mt5Error, DealsError, OSError) as exc:
            seconds = (utcnow() - started).total_seconds()
            await self._store.finish_test(test_id, status="failed", seconds=seconds, error=str(exc))
            self._running.pop(key, None)
            await self._after_job(f"{name} T{number} failed: {exc}", Severity.WARNING)
            test = await self._store.test(name, number)
            assert test is not None
            return test
        except asyncio.CancelledError:
            seconds = (utcnow() - started).total_seconds()
            await self._store.finish_test(
                test_id, status="failed", seconds=seconds, error="stopped"
            )
            self._running.pop(key, None)
            raise
        seconds = (utcnow() - started).total_seconds()
        await self._store.finish_test(test_id, status="done", seconds=seconds, periods=periods)
        self._running.pop(key, None)
        ins = periods["in_sample"]
        await self._after_job(
            f"{name} T{number} (v{version}): in-sample {ins['trades']} trades, "
            f"PF {ins['profit_factor']}, max DD {ins['max_drawdown_pct']} %",
            Severity.INFO,
        )
        test = await self._store.test(name, number)
        assert test is not None
        return test

    async def _after_job(self, message: str, severity: Severity) -> None:
        if self._state == LabState.WORKING and not self._running:
            await self._set(LabState.IDLE, message=message, severity=severity)
        else:
            await self._emit(message, severity)

    def _periods_of(self, deals: str) -> dict[str, Any]:
        ledger = parse_deals(deals)
        s = self._settings
        prop = self._prop()
        out: dict[str, Any] = {}
        for key, lo, hi in (
            ("in_sample", s.data_start, s.oos_start),
            ("out_of_sample", s.oos_start, s.holdout_start),
            ("holdout", s.holdout_start, None),
            ("unseen", s.oos_start, None),
        ):
            stats = summarize(ledger, lo, hi)
            out[key] = {**asdict(stats), "prop": prop_check(stats, prop)}
        return out

    def _prop(self) -> PropRules:
        p = self._settings.prop_firm
        return PropRules(daily_loss_pct=p.daily_loss_pct, max_loss_pct=p.max_loss_pct)

    # Validation --------------------------------------------------------------------------

    async def validate(self, name: str, number: int) -> tuple[bool, str]:
        """Out-of-sample gate for a finished backtest; the holdout verdict is kept for the user."""
        test = await self._store.test(name, number)
        if test is None or test["status"] != "done":
            raise BotLabError(f"T{number} isn't a finished backtest.")
        if test["validated"] is not None:
            return bool(test["validated"]), test["validation_reason"] or ""
        gates = self._settings.gates
        ins, oos, hold = test["in_sample"], test["out_of_sample"], test["holdout"]
        if ins["trades"] < gates.min_trades_in_sample:
            return (
                False,
                f"in-sample: only {ins['trades']} trades (needs {gates.min_trades_in_sample})",
            )
        if ins["net"] <= 0 or (ins["profit_factor"] or 0) < gates.min_profit_factor:
            return (
                False,
                f"in-sample: not profitable enough (profit factor below {gates.min_profit_factor})",
            )
        bar = t_required(await self._store.validations(name) + 1, self._settings.research.oos_alpha)
        prop = self._prop()
        reason = ""
        if oos["trades"] < gates.min_trades_out_of_sample:
            need = gates.min_trades_out_of_sample
            reason = f"out-of-sample: only {oos['trades']} trades (needs {need})"
        elif oos["net"] <= 0:
            reason = "out-of-sample: lost money"
        elif (oos["profit_factor"] or 0) < gates.min_profit_factor:
            reason = f"out-of-sample: profit factor below {gates.min_profit_factor}"
        elif oos["t_stat"] < bar:
            reason = f"out-of-sample: not significant (t below the current bar of {bar:.2f})"
        elif (
            oos["max_drawdown_pct"] >= prop.max_loss_pct
            or oos["max_daily_loss_pct"] >= prop.daily_loss_pct
        ):
            reason = "out-of-sample: broke the prop-firm loss limits"
        passed = not reason
        confirmed = None
        if passed:
            reason = "passed out-of-sample"
            confirmed = bool(
                hold
                and hold["trades"] >= max(1, gates.min_trades_out_of_sample // 2)
                and hold["net"] > 0
                and (hold["profit_factor"] or 0) > 1.0
                and hold["t_stat"] >= HOLDOUT_T
            )
        await self._store.set_validation(
            name, number, validated=passed, reason=reason, confirmed=confirmed
        )
        await self._emit(
            f"{name} T{number}: " + ("validated out-of-sample" if passed else reason),
            Severity.IMPORTANT if passed else Severity.INFO,
        )
        return passed, reason

    # Install -----------------------------------------------------------------------------

    async def install(self, name: str, number: int) -> dict[str, str]:
        """Copy a version into the user's MQL5 folder, next to (never over) the original."""
        version = await self._store.version(name, number)
        bot = await self._store.bot(name)
        data = self._paths.data_dir
        if version is None or bot is None or data is None:
            raise BotLabError(f"{name} has no version {number}, or your MQL5 folder wasn't found.")
        folder = data / "Experts" / "JARVIS" / name
        target = folder / f"{name}_v{number}.mq5"

        def write() -> None:
            self._copy_includes(Path(bot["path"]), folder)
            header = (
                f"// {name} v{number} — written by JARVIS: {version['title']}\n"
                f"// {version['hypothesis'] or ''}\n"
            )
            write_source(target, Source(header + version["source"], "utf-8-sig", "\r\n"))

        await asyncio.to_thread(write)
        await self._emit(
            f"{name} v{number} copied to your MetaTrader ({target.name})", Severity.INFO
        )
        return {
            "path": str(target),
            "hint": "In MetaTrader: Navigator → Expert Advisors → JARVIS → right-click → Refresh, "
            "then open it in MetaEditor and compile (F7). Test it on demo first.",
        }

    # Improving ---------------------------------------------------------------------------

    async def start_improving(self, name: str) -> BotLabStatus:
        if await self._store.bot(name) is None:
            await self.import_bot(name)
        self._prefs.update(**{IMPROVING: name})
        await self._store.set_stall_since(name, utcnow().isoformat())
        if self._task is None or self._task.done():
            self._spawn()
        self._wake.set()
        await self._set(LabState.IMPROVING, f"Improving {name}…", severity=Severity.INFO)
        return self.status()

    async def stop_improving(self) -> BotLabStatus:
        self._prefs.update(**{IMPROVING: None})
        await self._cancel()
        await self._set(LabState.IDLE, message="Bot improvement stopped", severity=Severity.INFO)
        return self.status()

    def _spawn(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="bot-lab")

    async def _cancel(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _wait(self, seconds: float) -> None:
        self._wake.clear()
        await wait_wall(self._wake, seconds)

    async def _loop(self) -> None:
        while self.improving:
            try:
                if not await self._cycle(self.improving):
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("bot lab cycle failed")
                await self._set(
                    LabState.ERROR,
                    f"Something went wrong ({type(exc).__name__}). I'll try again in 10 minutes.",
                    severity=Severity.WARNING,
                )
                await self._wait(600)

    async def _cycle(self, name: str) -> bool:
        research = self._settings.research
        model = self._model_factory()
        if model is None:
            await self._set(LabState.NEEDS_BRAIN, "Connect Claude under Settings → Brain.")
            await self._wait(3600)
            return True
        if research.model not in self._prices:
            await self._set(
                LabState.ERROR, f"No price for {research.model} in config/learning.yaml."
            )
            await self._wait(3600)
            return True
        if not self._tester.ready:
            await self._set(LabState.ERROR, "Set up the test terminal first (Bots page → Set up).")
            await self._wait(1800)
            return True
        bot = await self._store.bot(name)
        stalled = await self._store.rounds_without_progress(
            name, bot["stall_since"] if bot else None
        )
        if stalled >= research.stall_limit:
            self._prefs.update(**{IMPROVING: None})
            await self._set(
                LabState.STALLED,
                f"No new validated version of {name} in {research.stall_limit} rounds — stopped. "
                "Start it again to give it more rounds.",
                severity=Severity.WARNING,
            )
            return False
        self._spent_today = await self._store.spent_on(self._today().isoformat())
        if not self._room(SYSTEM_PROMPT, []):
            tomorrow = datetime.combine(self._today() + timedelta(days=1), datetime.min.time())
            resume = tomorrow.replace(tzinfo=self._now().tzinfo) + timedelta(minutes=1)
            await self._set(
                LabState.BUDGET,
                f"Today's ${research.daily_budget_usd:.2f} are used up — continues tomorrow.",
                next_round_at=resume,
            )
            await self._wait((resume - self._now()).total_seconds())
            return True
        try:
            await self._round(model, name)
        except ModelError as exc:
            retry = exc.code in _RETRYABLE
            await self._set(
                LabState.ERROR,
                f"{exc.message} " + ("I'll try again in 5 minutes." if retry else ""),
                severity=Severity.WARNING,
            )
            await self._wait(300 if retry else 1800)
            return True
        interval = research.round_interval_minutes * 60
        await self._set(LabState.WAITING, next_round_at=self._now() + timedelta(seconds=interval))
        await self._wait(interval)
        return True

    async def _round(self, model: ResearchModel, name: str) -> None:
        research = self._settings.research
        round_id, number = await self._store.start_round(name, self._today().isoformat())
        await self._set(LabState.IMPROVING, f"{name}: round {number}")
        messages: list[Any] = [model.user_message([await self._briefing(name)])]
        backtests = 0
        summary = ""
        try:
            for _ in range(research.max_steps_per_round):
                self._spent_today = await self._store.spent_on(self._today().isoformat())
                if not self._room(SYSTEM_PROMPT, messages):
                    summary = summary or "Stopped early: today's budget is used up."
                    break
                reply: ModelReply = await model.complete(
                    system=SYSTEM_PROMPT, messages=messages, tools=TOOLS
                )
                await self._store.add_cost(
                    round_id, self._cost(reply.usage) if reply.route == "api" else 0.0
                )
                messages.append(reply.assistant_message)
                if not reply.tool_calls:
                    summary = summary or reply.text[:400]
                    break
                outcomes: list[ToolOutcome] = []
                finished = False
                for call in reply.tool_calls:
                    text, ok = "", True
                    try:
                        if call.name == "create_version":
                            text = await self._tool_create(name, call.input)
                        elif call.name == "backtest":
                            if backtests >= research.max_backtests_per_round:
                                text, ok = (
                                    "This round's backtests are used up — finish the round.",
                                    False,
                                )
                            else:
                                backtests += 1
                                await self._set(
                                    LabState.IMPROVING,
                                    f"{name}: round {number}, backtest {backtests}",
                                )
                                text = await self._tool_backtest(name, call.input)
                        elif call.name == "validate":
                            passed, reason = await self.validate(
                                name, int(call.input.get("test", 0))
                            )
                            text = json.dumps({"passed": passed, "reason": reason})
                        elif call.name == "write_note":
                            note = str(call.input.get("text", "")).strip()[:600]
                            if not note:
                                text, ok = "A note needs text.", False
                            else:
                                text = f"Saved as note {await self._store.add_note(name, note)}."
                        elif call.name == "finish_round":
                            summary = str(call.input.get("summary", ""))[:400]
                            finished = True
                            text = "Round finished."
                        else:
                            text, ok = f"Unknown tool {call.name}.", False
                    except BotLabError as exc:
                        text, ok = str(exc), False
                    outcomes.append(ToolOutcome(call.id, text, is_error=not ok))
                if finished:
                    break
                messages.append(model.tool_results(outcomes))
        except ModelError as exc:
            await self._store.finish_round(round_id, "failed", error=exc.message)
            raise
        except asyncio.CancelledError:
            await self._store.finish_round(round_id, "interrupted")
            raise
        await self._store.finish_round(round_id, "completed", summary=summary)
        await self._emit(f"{name} round {number}: {summary or 'done'}", Severity.INFO)

    async def _briefing(self, name: str) -> str:
        bot = await self._store.bot(name)
        assert bot is not None
        versions = await self._store.versions(name)
        tests = await self._store.tests(name, limit=25)
        validated = [t for t in tests if t["validated"]]
        focus = validated[0]["version"] if validated else 0
        base = await self._store.version(name, focus)
        original = await self._store.version(name, 0)
        assert base is not None and original is not None
        validations = await self._store.validations(name)
        prop = self._settings.prop_firm
        return briefing(
            bot=name,
            settings=bot["settings"],
            periods=self._periods(),
            prop={"daily_loss_pct": prop.daily_loss_pct, "max_loss_pct": prop.max_loss_pct},
            inputs=[asdict(i) for i in parse_inputs(original["source"])],
            versions=versions,
            tests=tests,
            notes=await self._store.notes(name),
            validations=validations,
            next_bar=t_required(validations + 1, self._settings.research.oos_alpha),
            budget_left=max(0.0, self._settings.research.daily_budget_usd - self._spent_today),
            backtests_per_round=self._settings.research.max_backtests_per_round,
            base=base,
        )

    async def _tool_create(self, name: str, raw: dict[str, Any]) -> str:
        edits = [
            Edit(str(e.get("find", "")), str(e.get("replace", "")))
            for e in (raw.get("edits") or [])
            if isinstance(e, dict)
        ]
        number, result = await self.create_version(
            name,
            int(raw.get("base_version", 0)),
            str(raw.get("title", "")),
            str(raw.get("hypothesis", "")),
            edits,
        )
        if result.ok:
            return json.dumps({"version": number, "compiled": True, "warnings": result.warnings})
        return json.dumps({"version": number, "compiled": False, "errors": result.errors[:15]})

    async def _tool_backtest(self, name: str, raw: dict[str, Any]) -> str:
        inputs = {str(k): str(v) for k, v in (raw.get("inputs") or {}).items()}
        test = await self.backtest(name, int(raw.get("version", 0)), inputs, origin="research")
        if test["status"] != "done":
            raise BotLabError(f"T{test['number']} failed: {test['error']}")
        ins = test["in_sample"]
        monthly = ins.pop("monthly", [])
        return json.dumps(
            {
                "test": test["number"],
                "version": test["version"],
                "in_sample": ins,
                "monthly": [
                    {"month": m["month"], "pct": m["pct"], "trades": m["trades"]} for m in monthly
                ],
            }
        )

    # Money -------------------------------------------------------------------------------

    def _cost(self, usage: Usage) -> float:
        price = self._prices[self._settings.research.model]
        return (
            usage.input_tokens * price.input
            + usage.output_tokens * price.output
            + usage.cache_read_tokens * price.cache_read
            + usage.cache_write_tokens * price.cache_write
        ) / 1_000_000

    def _room(self, system: str, messages: list[Any]) -> bool:
        price = self._prices[self._settings.research.model]
        chars = len(system) + len(json.dumps(messages, default=str)) + 4000
        reserve = (
            chars / 3 * max(price.input, price.cache_write)
            + self._settings.research.max_tokens * price.output
        ) / 1_000_000
        return self._spent_today + reserve <= self._settings.research.daily_budget_usd


def _export_name(name: str, version: int) -> str:
    """The deals file a version's build writes (runs of one version never overlap)."""
    return f"jarvis_{name}_v{version}.csv"
