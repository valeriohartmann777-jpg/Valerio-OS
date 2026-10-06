"""Bot Lab: MQL5 handling, MetaTrader on a Mac, statistics, the lab and its API."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jarvis.api.app import create_app
from jarvis.bots.mql import (
    EXPORT_MARKER,
    Edit,
    EditError,
    apply_edits,
    instrument,
    parse_inputs,
    read_source,
    write_source,
)
from jarvis.bots.mt5 import (
    MetaTrader,
    Mt5Error,
    RunSpec,
    config_ini,
    discover,
    list_experts,
    parse_compile_log,
    windows_path,
)
from jarvis.bots.service import BotLabError, BotLabService, forbidden_additions, local_includes
from jarvis.bots.stats import PropRules, parse_deals, prop_check, scaling, summarize
from jarvis.bots.store import BotStore
from jarvis.core.trace import TraceContext
from jarvis.events.bus import EventBus
from jarvis.llm.base import Usage
from jarvis.runtime import Runtime
from jarvis.settings import BotsSettings, Mt5LocationSettings, Settings, load_settings
from jarvis.storage.database import Database
from jarvis.storage.preferences import Preferences
from tests.bot_fakes import GOLD_EA, FakeTester, deals_csv, mac_install
from tests.conftest import Recorder, make_settings
from tests.fakes import ScriptedChatModel, call

TODAY = date(2025, 10, 1)


# MQL5 ------------------------------------------------------------------------------


def test_inputs_are_read_from_the_source() -> None:
    inputs = {i.name: i for i in parse_inputs(GOLD_EA)}
    assert list(inputs) == ["InpLots", "InpStopPoints", "InpMagic", "InpTrendTF", "InpUseSession"]
    assert inputs["InpLots"].type == "double" and inputs["InpLots"].default == "0.10"
    assert inputs["InpLots"].comment == "Lot size" and inputs["InpLots"].numeric
    assert inputs["InpStopPoints"].default == "300"
    assert not inputs["InpMagic"].optimizable  # sinput
    assert inputs["InpTrendTF"].type == "ENUM_TIMEFRAMES" and not inputs["InpTrendTF"].numeric


def test_the_export_is_added_and_an_own_ontester_still_runs() -> None:
    plain = instrument(GOLD_EA, "jarvis_Gold_v0.csv")
    assert plain.startswith(GOLD_EA.rstrip("\n"))
    assert plain.count("double OnTester()") == 1
    assert "double jarvis_result = 0.0;" in plain
    assert 'FileOpen("jarvis_Gold_v0.csv"' in plain and "FILE_COMMON" in plain

    own = GOLD_EA + "\ndouble OnTester()\n  {\n   return TesterStatistics(STAT_PROFIT);\n  }\n"
    wrapped = instrument(own, "x.csv")
    assert "double Jarvis_UserOnTester()" in wrapped
    assert "double jarvis_result = Jarvis_UserOnTester();" in wrapped
    assert wrapped.count("double OnTester()") == 1
    assert instrument(wrapped, "x.csv") == wrapped  # adding it twice changes nothing
    with pytest.raises(ValueError):
        instrument(GOLD_EA, 'x.csv"); DeleteAll("')


def test_edits_must_match_exactly_once() -> None:
    edited = apply_edits(
        GOLD_EA, [Edit("input int    InpStopPoints=300;", "input int    InpStopPoints=450;")]
    )
    assert "InpStopPoints=450" in edited
    with pytest.raises(EditError, match="isn't in the source"):
        apply_edits(GOLD_EA, [Edit("InpStopPoints = 300", "x")])
    with pytest.raises(EditError, match=r"occurs \d+ times"):
        apply_edits(GOLD_EA, [Edit("input int", "input long")])
    with pytest.raises(EditError):
        apply_edits(GOLD_EA, [])


def test_sources_keep_their_encoding(tmp_path: Path) -> None:
    path = tmp_path / "Bot.mq5"
    path.write_bytes("// Ölpreis\r\ninput int A = 1;\r\n".encode("utf-16"))
    source = read_source(path)
    assert source.encoding == "utf-16" and source.newline == "\r\n"
    assert source.text == "// Ölpreis\ninput int A = 1;\n"
    write_source(tmp_path / "Copy.mq5", source)
    assert (tmp_path / "Copy.mq5").read_bytes() == path.read_bytes()


def test_versions_may_not_reach_outside_the_trading_logic(tmp_path: Path) -> None:
    assert forbidden_additions(GOLD_EA, GOLD_EA + 'int r = WebRequest("GET", url);') == [
        "WebRequest"
    ]
    assert forbidden_additions(GOLD_EA, GOLD_EA + '#import "kernel32.dll"') == ["#import"]
    assert forbidden_additions(GOLD_EA, GOLD_EA.replace("0.10", "0.20")) == []
    home = mac_install(tmp_path)
    experts = next(home.rglob("MQL5")) / "Experts"
    found = local_includes(experts / "Gold" / "GoldScalper.mq5", experts)
    assert [p.name for p in found] == ["GoldTools.mqh"]


# MetaTrader on a Mac ---------------------------------------------------------------


def test_metatrader_is_found_on_a_mac(tmp_path: Path) -> None:
    home = mac_install(tmp_path)
    paths = discover(BotsSettings(), home=home)
    assert paths.app == home / "Applications" / "MetaTrader 5.app"
    assert paths.wine is not None and paths.wine.name == "wine64"
    assert paths.prefix is not None and paths.prefix.name == "net.metaquotes.wine.metatrader5"
    assert paths.terminal_dir is not None and (paths.terminal_dir / "terminal64.exe").exists()
    assert paths.data_dir == paths.terminal_dir / "MQL5"
    assert paths.common_files is not None and paths.common_files.parts[-3:] == (
        "Terminal",
        "Common",
        "Files",
    )
    assert paths.test_dir == paths.prefix / "drive_c" / "JARVIS" / "MT5"
    assert [p.name for p in list_experts(paths.data_dir)] == ["GoldScalper.mq5"]
    assert discover(BotsSettings(), home=tmp_path / "nobody").app is None


def test_windows_paths_and_the_tester_configuration(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    assert windows_path(prefix / "drive_c" / "JARVIS" / "runs" / "a.ini", prefix) == (
        "C:\\JARVIS\\runs\\a.ini"
    )
    assert windows_path(Path("/Users/v/x.mq5"), prefix) == "Z:\\Users\\v\\x.mq5"
    spec = RunSpec(
        expert="JARVIS\\Gold\\Gold_v2",
        symbol="XAUUSD.m",
        period="M15",
        model=4,
        start=date(2021, 1, 1),
        end=date(2025, 9, 30),
        deposit=10000,
        currency="USD",
        leverage=100,
        export_file="jarvis_Gold_v2.csv",
        report="JARVIS\\reports\\Gold_T3",
        inputs={"InpLots": "0.2"},
    )
    ini = config_ini(spec)
    expected = ["Expert=JARVIS\\Gold\\Gold_v2", "Symbol=XAUUSD.m", "Model=4",
                "FromDate=2021.01.01", "ToDate=2025.09.30", "Deposit=10000", "Leverage=100",
                "ShutdownTerminal=1", "[TesterInputs]", "InpLots=0.2"]  # fmt: skip
    for line in expected:
        assert line + "\r\n" in ini
    bad = RunSpec(**{**spec.__dict__, "inputs": {"InpLots": "0.2\r\nExpert=evil"}})
    with pytest.raises(Mt5Error):
        config_ini(bad)


def test_compile_log_parsing() -> None:
    ok = "compiling 'Gold_v2.mq5'\r\nResult: 0 errors, 2 warnings, 345 msec elapsed\r\n"
    assert parse_compile_log(ok) == (0, 2, [])
    bad = "Gold_v2.mq5(20,4) : error 256: 'x' - undeclared identifier\nResult: 1 errors, 0 warnings"
    errors, warnings, lines = parse_compile_log(bad)
    assert errors == 1 and warnings == 0 and lines[0].startswith("Gold_v2.mq5(20,4) : error 256")
    assert parse_compile_log("garbage")[0] is None


def test_the_test_terminal_is_a_copy_without_history(tmp_path: Path) -> None:
    home = mac_install(tmp_path)
    paths = discover(BotsSettings(), home=home)
    mt5 = MetaTrader(paths, BotsSettings())
    assert not mt5.ready
    mt5.set_up()
    test = paths.test_dir
    assert test is not None and mt5.ready
    assert (test / "terminal64.exe").exists() and (test / "MetaEditor64.exe").exists()
    assert not (test / "Bases").exists()  # history is downloaded again, not copied
    assert (test / "config" / "accounts.dat").exists()  # the saved demo login
    assert (test / "MQL5" / "Include" / "Trade" / "Trade.mqh").exists()
    jarvis = test / "MQL5" / "Experts" / "JARVIS" / "Gold" / "Gold_v1.mq5"
    jarvis.parent.mkdir(parents=True)
    jarvis.write_text("// a version")
    mt5.sync()  # the user's files again; JARVIS's versions stay
    assert jarvis.exists()


async def test_compile_and_backtest_through_wine(tmp_path: Path) -> None:
    home = mac_install(tmp_path)
    paths = discover(BotsSettings(), home=home)
    test_dir, prefix = paths.test_dir, paths.prefix
    assert test_dir is not None and prefix is not None
    commands: list[list[str]] = []
    attempts = {"backtest": 0}

    async def runner(command: list[str], env: dict[str, str], limit: float) -> tuple[int, str]:
        commands.append(command)
        assert env["WINEPREFIX"] == str(paths.prefix) and env["WINEDEBUG"] == "-all"
        if command[1].endswith("MetaEditor64.exe"):
            source = test_dir / "MQL5" / "Experts" / "JARVIS" / "Gold" / "Gold_v0.mq5"
            source.with_suffix(".ex5").write_bytes(b"EX5")
            log = next(a for a in command if a.startswith("/log:"))[5:]
            log_path = prefix / "drive_c" / Path(*log[3:].split("\\"))
            log_path.write_bytes("Result: 0 errors, 0 warnings\r\n".encode("utf-16"))
        else:
            attempts["backtest"] += 1
            if attempts["backtest"] == 2:  # the first run only downloaded history
                assert paths.common_files is not None
                (paths.common_files / "jarvis_Gold_v0.csv").write_text(
                    deals_csv(0.2, date(2024, 1, 1), date(2024, 2, 1))
                )
        return 0, ""

    mt5 = MetaTrader(paths, BotsSettings(), runner=runner)
    mt5.set_up()
    assert paths.test_dir is not None
    source = paths.test_dir / "MQL5" / "Experts" / "JARVIS" / "Gold" / "Gold_v0.mq5"
    source.parent.mkdir(parents=True)
    source.write_text(instrument(GOLD_EA, "jarvis_Gold_v0.csv"))
    result = await mt5.compile(source)
    assert result.ok and result.errors == []
    wine, editor, *args = commands[0]
    assert wine.endswith("wine64") and editor.endswith("MetaEditor64.exe")
    assert "/compile:C:\\JARVIS\\MT5\\MQL5\\Experts\\JARVIS\\Gold\\Gold_v0.mq5" in args

    spec = RunSpec("JARVIS\\Gold\\Gold_v0", "XAUUSD", "M15", 4, date(2024, 1, 1),
                    date(2024, 2, 1), 10000, "USD", 100, "jarvis_Gold_v0.csv", "r")  # fmt: skip
    run = await mt5.backtest(spec)
    assert run.deals is not None and run.deals.startswith("time,type")
    assert attempts["backtest"] == 2
    assert commands[-1][2:] == ["/portable", "/config:C:\\JARVIS\\runs\\jarvis_Gold_v0.csv.ini"]


# Statistics --------------------------------------------------------------------------


def _ts(day: date, hour: int = 10) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=UTC).timestamp())


def test_deals_become_trades_and_periods() -> None:
    rows = [
        "time,type,entry,volume,price,profit,commission,swap,symbol,position",
        f"{_ts(date(2024, 1, 1), 0)},2,0,0,0,10000,0,0,,0",
        # position 7: opened once, closed in two parts, commission and swap
        f"{_ts(date(2024, 1, 2))},0,0,0.20,2000,0,-4,0,XAUUSD,7",
        f"{_ts(date(2024, 1, 2), 11)},1,1,0.10,2005,50,-2,0,XAUUSD,7",
        f"{_ts(date(2024, 1, 3))},1,1,0.10,2010,100,-2,-3,XAUUSD,7",
        # position 8: a loser in February
        f"{_ts(date(2024, 2, 5))},1,0,0.10,2000,0,-2,0,XAUUSD,8",
        f"{_ts(date(2024, 2, 5), 12)},0,1,0.10,2030,-300,-2,0,XAUUSD,8",
    ]
    ledger = parse_deals("\n".join(rows))
    assert ledger.deposit == 10000
    [first, second] = ledger.trades
    assert first.pnl == pytest.approx(50 + 100 - 4 - 2 - 2 - 3)
    assert first.side == "buy" and first.volume == pytest.approx(0.2)
    assert first.closed == _ts(date(2024, 1, 3))
    assert second.side == "sell" and second.pnl == pytest.approx(-304)

    stats = summarize(ledger, date(2024, 1, 1), None)
    assert stats.trades == 2 and stats.net == pytest.approx(139 - 304)
    assert stats.profit_factor == pytest.approx(139 / 304, abs=1e-3)
    assert [m["month"] for m in stats.monthly] == ["2024-01", "2024-02"]
    assert stats.monthly[1]["pct"] == pytest.approx(-304 / 10139 * 100, abs=0.01)
    assert stats.max_drawdown_pct == pytest.approx(304 / 10139 * 100, abs=0.01)
    assert stats.max_daily_loss_pct == pytest.approx(304 / 10139 * 100, abs=0.01)
    assert summarize(ledger, date(2024, 2, 1), None).trades == 1
    assert summarize(ledger, date(2024, 1, 1), date(2024, 2, 1)).trades == 1


def test_prop_check_and_10k_scaling() -> None:
    ledger = parse_deals(deals_csv(0.3, date(2023, 1, 1), date(2024, 1, 1)))
    stats = summarize(ledger, date(2023, 1, 1), None)
    assert stats.months == 12 and stats.trades > 200
    check = prop_check(stats, PropRules(daily_loss_pct=5, max_loss_pct=10))
    assert check["daily_loss_ok"] == (stats.max_daily_loss_pct < 5)
    plan = scaling(stats, account=100000, drawdown_limit_pct=10, target=10000)
    assert plan is not None
    assert stats.median_month_pct is not None
    k = 10 / (max(stats.max_drawdown_pct, 0.5) * 1.5)
    assert plan["risk_scale"] == pytest.approx(k, abs=0.01)
    assert plan["monthly_usd"] == pytest.approx(100000 * stats.median_month_pct * k / 100, rel=0.01)
    assert plan["account_for_target"] == pytest.approx(
        10000 / (stats.median_month_pct * k / 100), rel=0.01, abs=1000
    )
    assert plan["expected_drawdown_pct"] == pytest.approx(10 / 1.5, abs=0.1)
    losing = summarize(parse_deals(deals_csv(-0.5, date(2023, 1, 1), date(2024, 1, 1))),
                       date(2023, 1, 1), None)  # fmt: skip
    plan = scaling(losing, account=100000, drawdown_limit_pct=10, target=10000)
    assert plan is not None and plan["account_for_target"] is None


# The lab -----------------------------------------------------------------------------


async def make_lab(
    tmp_path: Path, model: ScriptedChatModel | None = None
) -> tuple[BotLabService, FakeTester, Database, Recorder]:
    home = mac_install(tmp_path / "home")
    settings = load_settings(environ={}).bots
    db = Database(tmp_path / "jarvis.db")
    await db.connect()
    bus = EventBus()
    testers: list[FakeTester] = []

    def tester(paths: Any) -> FakeTester:
        testers.append(FakeTester(paths))
        return testers[-1]

    lab = BotLabService(
        settings=settings,
        prices=load_settings(environ={}).learning.prices,
        store=BotStore(db),
        bus=bus,
        preferences=Preferences(tmp_path / "prefs.json"),
        model_factory=lambda: model,
        tester_factory=tester,
        discover_paths=lambda: discover(settings, home=home),
        today=lambda: TODAY,
    )
    recorder = Recorder(bus)
    await lab.start()
    return lab, testers[-1], db, recorder


async def test_a_bot_is_imported_backtested_and_judged(tmp_path: Path) -> None:
    lab, tester, db, recorder = await make_lab(tmp_path)
    try:
        status = lab.status()
        assert [e["name"] for e in status.experts] == ["GoldScalper"]
        assert all(c["ok"] for c in status.checks)
        detail = await lab.import_bot("GoldScalper")
        assert [v["title"] for v in detail["versions"]] == ["Original"]
        assert [i["name"] for i in detail["inputs"]][:2] == ["InpLots", "InpStopPoints"]

        test = await lab.backtest("GoldScalper", 0, {}, origin="user")
        assert test["status"] == "done", test["error"]
        spec = tester.specs[-1]
        assert spec.expert == "JARVIS\\GoldScalper\\GoldScalper_v0" and spec.symbol == "XAUUSD"
        assert spec.start == date(2021, 1, 1) and spec.end == date(2025, 9, 30)
        ins, oos, hold = test["in_sample"], test["out_of_sample"], test["holdout"]
        assert ins["trades"] > 600 and oos["trades"] > 150 and hold["trades"] > 100
        assert ins["monthly"][0]["month"] == "2021-01"
        assert oos["monthly"][0]["month"] == "2024-07" and hold["monthly"][0]["month"] == "2025-04"
        assert "prop" in ins and test["unseen"]["trades"] == oos["trades"] + hold["trades"]
        # The original has no edge: it doesn't pass.
        passed, reason = await lab.validate("GoldScalper", test["number"])
        assert not passed and reason.startswith(("in-sample", "out-of-sample"))
        # The user's file is untouched; the tested copy carries the export.
        original = next((tmp_path / "home").rglob("GoldScalper.mq5"))
        assert original.read_text(encoding="utf-8") == GOLD_EA
        copy = tester.experts_dir() / "JARVIS" / "GoldScalper" / "GoldScalper_v0.mq5"
        assert EXPORT_MARKER in copy.read_text(encoding="utf-8-sig")
        assert (copy.parent / "GoldTools.mqh").exists()  # its quoted include came along
        found = await lab.detail("GoldScalper")
        assert found is not None and found["projection"]["test"] == test["number"]
        assert "bots.changed" in recorder.types()
    finally:
        await lab.stop()
        await db.close()


async def test_versions_are_edits_compiled_and_installed(tmp_path: Path) -> None:
    lab, _, db, _ = await make_lab(tmp_path)
    try:
        await lab.import_bot("GoldScalper")
        number, result = await lab.create_version(
            "GoldScalper", 0, "Session filter", "Gold trends in London",
            [Edit("// EDGE=0.00", "// EDGE=0.40")],
        )  # fmt: skip
        assert number == 1 and result.ok
        broken, failed = await lab.create_version(
            "GoldScalper", 1, "Oops", "", [Edit("CTrade trade;", "CTrade trade; SYNTAX_ERROR")]
        )
        assert broken == 2 and not failed.ok and "error 256" in failed.errors[0]
        with pytest.raises(BotLabError, match="WebRequest"):
            await lab.create_version(
                "GoldScalper",
                1,
                "Phone home",
                "",
                [Edit("CTrade trade;", "CTrade trade; WebRequest();")],
            )
        with pytest.raises(BotLabError, match="isn't in the source"):
            await lab.create_version("GoldScalper", 1, "x", "", [Edit("nope", "x")])
        versions = await lab.detail("GoldScalper")
        assert versions is not None
        assert [(v["number"], v["compiled"]) for v in versions["versions"]] == [
            (0, False), (1, True), (2, False)
        ]  # fmt: skip
        diff = (await lab.source("GoldScalper", 1))["diff"]
        assert "-// EDGE=0.00" in diff and "+// EDGE=0.40" in diff

        installed = await lab.install("GoldScalper", 1)
        target = Path(installed["path"])
        assert target.name == "GoldScalper_v1.mq5" and target.parent.name == "GoldScalper"
        assert target.parent.parent.name == "JARVIS"
        text = target.read_text(encoding="utf-8-sig")  # noqa: ASYNC240
        assert "EDGE=0.40" in text and EXPORT_MARKER not in text
        assert (target.parent / "GoldTools.mqh").exists()
    finally:
        await lab.stop()
        await db.close()


async def test_the_bar_rises_with_every_validation(tmp_path: Path) -> None:
    lab, _, db, _ = await make_lab(tmp_path)
    try:
        await lab.import_bot("GoldScalper")
        number, _ = await lab.create_version(
            "GoldScalper", 0, "Edge", "", [Edit("// EDGE=0.00", "// EDGE=0.40")]
        )
        test = await lab.backtest("GoldScalper", number, {}, origin="research")
        passed, reason = await lab.validate("GoldScalper", test["number"])
        assert passed, reason
        stored = await lab.detail("GoldScalper")
        assert stored is not None and stored["best_test"] == test["number"]
        tests = {t["number"]: t for t in stored["tests"]}
        assert tests[test["number"]]["holdout_confirmed"] is True
        assert await lab.validate("GoldScalper", test["number"]) == (True, "passed out-of-sample")
        assert await BotStore(db).validations("GoldScalper") == 1  # asked twice, counted once
        projection = stored["projection"]
        assert projection["prop_firm"]["account"] == 100000
        assert projection["prop_firm"]["account_for_target"] > 0
    finally:
        await lab.stop()
        await db.close()


async def test_claude_improves_the_bot_in_rounds(tmp_path: Path) -> None:
    usage = Usage(input_tokens=2000, output_tokens=500)
    model = ScriptedChatModel(
        [
            call("create_version", {
                "base_version": 0, "title": "London session only",
                "hypothesis": "Gold's liquidity is highest in London",
                "edits": [{"find": "// EDGE=0.00", "replace": "// EDGE=0.40"}],
            }, "c1", usage),
            call("backtest", {"version": 1}, "c2", usage),
            call("validate", {"test": 1}, "c3", usage),
            call("write_note", {"text": "The session filter carries the edge."}, "c4", usage),
            call("finish_round", {"summary": "Session filter validated."}, "c5", usage),
        ]
    )  # fmt: skip
    lab, _, db, _ = await make_lab(tmp_path, model)
    store = BotStore(db)
    try:
        await lab.import_bot("GoldScalper")
        await lab._round(model, "GoldScalper")
        briefing = model.calls[0]["messages"][0]["content"][0]["text"]
        assert "EA: GoldScalper" in briefing and "// EDGE=0.00" in briefing
        assert "next one needs out-of-sample t ≥ 1.64" in briefing
        assert "max loss < 10.0 %" in briefing
        tool_reply = model.calls[2]["messages"][-1]["content"][0]
        assert '"in_sample"' in tool_reply["content"] and '"holdout"' not in tool_reply["content"]
        assert '"passed": true' in model.calls[3]["messages"][-1]["content"][0]["content"]
        [round_] = await store.rounds("GoldScalper")
        assert round_["status"] == "completed" and round_["summary"] == "Session filter validated."
        assert round_["cost_usd"] > 0
        assert [n["text"] for n in await store.notes("GoldScalper")] == [
            "The session filter carries the edge."
        ]
        first = await store.test("GoldScalper", 1)
        assert first is not None and first["validated"] is True
        assert await store.rounds_without_progress("GoldScalper", None) == 0
    finally:
        await lab.stop()
        await db.close()


# API -----------------------------------------------------------------------------------


def lab_settings(tmp_path: Path) -> Settings:
    home = mac_install(tmp_path / "home")
    settings = make_settings(tmp_path)
    location = Mt5LocationSettings(
        app=str(home / "Applications" / "MetaTrader 5.app"),
        prefix=str(home / "Library" / "Application Support" / "net.metaquotes.wine.metatrader5"),
    )
    bots = settings.bots.model_copy(update={"mt5": location})
    return settings.model_copy(update={"bots": bots})


def test_bots_api(tmp_path: Path) -> None:
    runtime = Runtime(lab_settings(tmp_path), bot_tester=FakeTester)
    with TestClient(create_app(runtime=runtime)) as client:
        lab = client.get("/bots").json()
        assert lab["state"] == "idle" and lab["ready"] is True
        assert [e["name"] for e in lab["experts"]] == ["GoldScalper"]
        assert client.get("/snapshot").json()["bots"]["experts"][0]["file"] == "GoldScalper.mq5"
        assert client.get("/bots/GoldScalper").status_code == 404
        assert client.post("/bots/Nope/import").status_code == 422
        detail = client.post("/bots/GoldScalper/import").json()
        assert detail["settings"]["symbol"] == "XAUUSD"
        changed = client.post("/bots/GoldScalper/settings", json={"symbol": "XAUUSD.m"}).json()
        assert changed["symbol"] == "XAUUSD.m"
        assert client.post("/bots/GoldScalper/settings", json={"period": "M7"}).status_code == 422
        assert client.post("/bots/GoldScalper/backtest", json={"version": 0}).json()["started"]
        for _ in range(300):
            tests = client.get("/bots/GoldScalper").json()["tests"]
            if tests and tests[0]["status"] != "running":
                break
            time.sleep(0.02)
        assert tests[0]["status"] == "done" and tests[0]["settings"]["symbol"] == "XAUUSD.m"
        source = client.get("/bots/GoldScalper/versions/0/source").json()
        assert source["source"] == GOLD_EA and source["diff"] == ""
        installed = client.post("/bots/GoldScalper/versions/0/install").json()
        assert installed["path"].endswith("GoldScalper_v0.mq5")

        result = client.portal.call(  # type: ignore[union-attr]
            lambda: runtime.executor.run("bot_report", {}, ctx=TraceContext.new(), reason="test")
        )
        assert result.success and result.data["bots"][0]["name"] == "GoldScalper"
        assert result.data["bots"][0]["backtests"][0]["status"] == "done"
