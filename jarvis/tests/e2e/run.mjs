#!/usr/bin/env node
/**
 * End-to-end test of the real desktop app.
 *
 * Launches the built Electron app (which starts the Python backend through its
 * supervisor), drives it like a user and checks the full vertical slice:
 *   command → route → mission → Operator → tool → Sentinel → UI.
 *
 * The backend runs with the SIMULATED desktop so the run is deterministic and
 * never launches real applications. Screenshots go to tests/e2e/output/ (or
 * the directory given as the first argument).
 *
 *   npm run build && npm run test:e2e            # Windows/macOS
 *   npm run build && xvfb-run -a npm run test:e2e   # Linux without display
 */

import assert from "node:assert/strict";
import { chmodSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, symlinkSync, writeFileSync } from "node:fs";
import http from "node:http";
import { execFileSync, spawn } from "node:child_process";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { _electron as electron, chromium } from "playwright-core";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
// Every JARVIS started here inherits this: no morning briefing or model training
// fetching real market data.
process.env.JARVIS_BRIEFING = "off";
process.env.JARVIS_TRAINING = "off";
const appDir = path.join(root, "apps", "desktop");
const python = path.join(
  root, "backend", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
// AI routing: a memory keystore (labelled in the UI; never the OS keychain here) and a
// stand-in for Claude Code's `claude` binary (tests/fixtures/ai/fake_claude.py, labelled as a
// custom binary) — no JARVIS started here can reach a real Claude plan or the paid API.
process.env.JARVIS_AI_KEYSTORE = "memory";
const fakeClaudeHome = mkdtempSync(path.join(tmpdir(), "jarvis-e2e-claude-"));
const fakeClaude = path.join(fakeClaudeHome, "claude");
const fakeClaudeConfig = (config) =>
  writeFileSync(path.join(fakeClaudeHome, ".fake-claude.json"), JSON.stringify(config));
fakeClaudeConfig({ mode: "ok" });
writeFileSync(
  fakeClaude,
  `#!/bin/sh\nHOME='${fakeClaudeHome}' exec '${python}' '${path.join(root, "backend", "tests", "fixtures", "ai", "fake_claude.py")}' "$@"\n`,
  { mode: 0o755 },
);
process.env.JARVIS_AI_CLI = fakeClaude;
const outDir = path.resolve(process.argv[2] ?? path.join(root, "tests", "e2e", "output"));
const backendUrl = "http://127.0.0.1:8799";
mkdirSync(outDir, { recursive: true });

const require = createRequire(path.join(appDir, "package.json"));
const electronBinary = require("electron");

const steps = [];
const shotOf = (page, name) => page.screenshot({ path: path.join(outDir, `${name}.png`) });
const step = async (name, fn) => {
  const started = Date.now();
  await fn();
  steps.push(`✓ ${name} (${Date.now() - started} ms)`);
  console.log(steps.at(-1));
};

// A backend from an "older version" already occupies the port — the app must replace it.
const stale = spawn(python, ["-m", "jarvis"], {
  cwd: path.join(root, "backend"),
  env: {
    ...process.env,
    JARVIS_PORT: "8799",
    JARVIS_BUILD: "0000000-outdated",
    JARVIS_SYSTEM_BACKEND: "simulated",
    JARVIS_DATA_DIR: mkdtempSync(path.join(tmpdir(), "jarvis-e2e-stale-")),
  },
  stdio: "ignore",
});
const health = () => fetch(`${backendUrl}/health`).then((r) => r.json(), () => null);
for (let i = 0; i < 120 && (await health())?.build !== "0000000-outdated"; i += 1) {
  await new Promise((resolve) => setTimeout(resolve, 250));
}
assert.equal((await health())?.build, "0000000-outdated", "stale backend did not start");

const mainDataDir = mkdtempSync(path.join(tmpdir(), "jarvis-e2e-"));
// One finished model-training run (synthetic prices) for the Learning page.
execFileSync(python, [path.join(root, "tests", "e2e", "seed_training.py"), mainDataDir], {
  cwd: path.join(root, "backend"),
  stdio: "inherit",
});
// A fake "MetaTrader 5 for Mac" (its Wine plays MetaEditor and the strategy tester) and a
// config folder that points the Bot Lab at it.
const botConfig = execFileSync(
  python,
  [path.join(root, "tests", "e2e", "seed_mt5.py"), mkdtempSync(path.join(tmpdir(), "jarvis-e2e-mt5-"))],
  { cwd: path.join(root, "backend") },
)
  .toString()
  .trim();
const ultronExports = mkdtempSync(path.join(tmpdir(), "jarvis-e2e-ultron-exports-"));
const app = await electron.launch({
  executablePath: electronBinary,
  args: [appDir, ...(process.platform === "linux" ? ["--no-sandbox"] : [])],
  env: {
    ...process.env,
    JARVIS_BACKEND_URL: backendUrl,
    JARVIS_SYSTEM_BACKEND: "simulated",
    JARVIS_DATA_DIR: mainDataDir,
    JARVIS_UPDATES: "off",
    JARVIS_BACKGROUND: "on", // keep running when the window closes (default on macOS / Windows)
    JARVIS_CONFIG_DIR: botConfig,
    // ULTRON agents reply from a script here (no API key in CI); the UI labels it.
    JARVIS_ULTRON_SCRIPT: path.join(root, "tests", "e2e", "ultron_script.json"),
    JARVIS_ULTRON_EXPORT_DIR: ultronExports,
    // QuantLab: the offline fixture provider (real DBN files, synthetic prices, labelled in the
    // UI), a memory keystore (no Keychain in CI) and a scripted Strategy Architect.
    JARVIS_QUANTLAB_PROVIDER: "fixture",
    JARVIS_QUANTLAB_KEYSTORE: "memory",
    JARVIS_QUANTLAB_ARCHITECT_SCRIPT: path.join(root, "tests", "e2e", "quantlab_architect_script.json"),
    // APEX: generated test data (labelled SYNTHETIC in the UI) — no market data in CI.
    JARVIS_APEX_PROVIDER: "fixture",
  },
});

try {
  const page = await app.firstWindow();
  await page.setViewportSize({ width: 1480, height: 920 }).catch(() => {});
  const shot = (name) => page.screenshot({ path: path.join(outDir, `${name}.png`) });
  const headline = page.getByTestId("jarvis-headline");
  // JARVIS's replies live in the persistent conversation below the core.
  const reply = (text) => page.getByTestId("jarvis-reply").filter({ hasText: text }).first();
  const command = async (text) => {
    await page.getByTestId("command-input").fill(text);
    await page.getByTestId("command-input").press("Enter");
  };

  await step("an outdated backend from an earlier run is replaced", async () => {
    // The window may briefly reach the old backend before the supervisor replaces it.
    for (let i = 0; i < 120 && (await health())?.build === "0000000-outdated"; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    await page.getByTestId("connection-status").filter({ hasText: "Backend online" }).waitFor({ timeout: 30_000 });
    const current = await health();
    assert.notEqual(current?.build, "0000000-outdated");
    assert.equal(stale.exitCode !== null || stale.signalCode !== null, true, "stale backend still running");
    const shown = await page.getByTestId("build").innerText();
    assert.ok(current.build === "unknown" || shown.includes(current.build.slice(0, 7)), shown);
  });

  await step("app launches, backend starts, dashboard shows ONLINE", async () => {
    await headline.filter({ hasText: "Everything is nominal." }).waitFor();
    assert.equal(await page.getByTestId("simulated-badge").isVisible(), true);
    await page.waitForTimeout(600);
    await shot("01-online");
  });

  await step("live system information is shown; brain status is honest", async () => {
    const context = page.getByTestId("context-panel");
    await context.getByText("JARVIS", { exact: true }).waitFor();
    // the E2E backend has no API key → the brain must say so, not pretend
    await page.getByTestId("brain-offline").waitFor();
  });

  await step("“Open Notepad.” → mission → Operator → Sentinel → verified reply", async () => {
    await command("Open Notepad.");
    // The real step state while the Operator works (Home's focus panel shows the live mission).
    await page.locator('[data-testid="mission-step"][data-status="active"]').first().waitFor();
    await page.waitForTimeout(250);
    await shot("02-executing");
    await reply("Notepad is open.").waitFor({ timeout: 10_000 });
    const statuses = await page.getByTestId("mission-step").evaluateAll((els) => els.map((e) => e.dataset.status));
    assert.deepEqual(statuses, ["complete", "complete"]);
    const activity = await page.getByTestId("activity-stream").innerText();
    for (const line of [
      "Command received",
      "Intent classified",
      "Mission 001 created",
      "Operator assigned",
      "Notepad window detected",
      "Execution verified",
      "Notepad is open.",
    ]) {
      assert.ok(activity.includes(line), `activity is missing “${line}”`);
    }
    await page.waitForTimeout(400);
    await shot("03-notepad-verified");
  });

  await step("level-2 action asks for approval; approve → executed and verified", async () => {
    await command("open powershell");
    const card = page.getByTestId("approval-card");
    await card.waitFor();
    const text = await card.innerText();
    assert.ok(text.includes("PowerShell") && text.includes("arbitrary commands") && text.includes("open powershell"));
    await page.waitForTimeout(500);
    await shot("04-approval");
    await card.getByTestId("approve").click();
    await reply("PowerShell is open.").waitFor();
  });

  await step("level-4 action needs two-step confirmation", async () => {
    await command("open regedit");
    const card = page.getByTestId("approval-card");
    await card.getByTestId("approve").click();
    await card.getByText("Confirm high-risk action").waitFor();
    await page.mouse.move(0, 0);
    await page.waitForTimeout(300);
    await shot("05-strong-confirm");
    await card.getByTestId("approve").click();
    await reply("Registry Editor is open.").waitFor();
  });

  await step("rejection stops the mission and JARVIS acknowledges", async () => {
    await command("open cmd");
    await page.getByTestId("approval-card").getByTestId("reject").click();
    await reply("Understood. I won't open Command Prompt.").waitFor();
  });

  await step("open-ended request without a key explains how to connect the model", async () => {
    await command("plan my evening");
    await reply("I can't reason about that yet.").waitFor();
    await page.getByRole("definition").filter({ hasText: "Settings" }).first().waitFor();
  });

  await step("failures are explained, not dumped", async () => {
    await command("open blender");
    await reply("I couldn't open Blender.").waitFor();
    await page.getByRole("definition").filter({ hasText: "isn't installed, or it can't be found on this computer." }).waitFor();
    await page.waitForTimeout(400);
    await shot("06-failure");
  });

  await step("mission detail and settings render", async () => {
    await page.getByTestId("missions-panel").getByText("Open Notepad").click();
    await page.getByText("Notepad window detected").first().waitFor();
    await page.waitForTimeout(300);
    await shot("07-mission-detail");
    await page.getByTestId("tab-settings").click();
    await page.getByText("Strong confirmation").waitFor();
    await page.getByText("~/Documents").first().waitFor(); // folders the file tools may see
    await page.getByText("Updates are turned off").waitFor();
    await page.waitForTimeout(300);
    await shot("08-settings");
    await page.getByTestId("tab-home").click();
  });

  await step("brain can be connected from the dashboard; a bad key is refused, nothing saved", async () => {
    await page.getByTestId("brain-offline").click();
    await page.getByTestId("key-form").waitFor();
    await page.getByTestId("key-input").fill("hello");
    await page.getByTestId("key-connect").click();
    await page.getByTestId("key-error").filter({ hasText: "doesn't look like an Anthropic API key" }).waitFor();
    await page.getByTestId("brain-status").filter({ hasText: "Offline" }).waitFor();
    await page.waitForTimeout(300);
    await shot("10-connect-brain");
    await page.getByTestId("tab-home").click();
  });

  await step("voice is set up from the dashboard; a bad ElevenLabs key is refused", async () => {
    // Without a key the mic button leads to the setup instead of failing silently.
    assert.equal(await page.getByTestId("mic-button").getAttribute("data-state"), "off");
    await page.getByTestId("mic-button").click();
    await page.getByTestId("voice-status").filter({ hasText: "Off" }).waitFor();
    await page.getByTestId("voice-key-input").fill("nope");
    await page.getByTestId("voice-key-connect").click();
    await page.getByTestId("voice-key-error").filter({ hasText: "doesn't look like an ElevenLabs API key" }).waitFor();
    await page.getByTestId("voice-key-form").scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    await shot("10b-voice-setup");
    await page.getByTestId("tab-home").click();
    await page.getByTestId("voice-off").waitFor();
  });

  await step("learning starts and stops from its page; without Claude it says what it needs", async () => {
    await page.getByTestId("learning-row").filter({ hasText: "Off" }).waitFor();
    await page.getByTestId("tab-training").click(); // JARVIS 2.0: Learning lives in the Training Hub
    await page.getByTestId("training-hub").waitFor();
    await page.getByTestId("subnav-overview").click(); // the tab reopens the last section; be explicit
    await page.getByTestId("training-truth").waitFor(); // says what "learning" really is
    await page.getByTestId("subnav-research").click();
    await page.getByTestId("learning-page").waitFor();
    await page.getByTestId("learning-state").filter({ hasText: "Off" }).waitFor();
    await page.getByTestId("learning-budget").filter({ hasText: "$0.00 / $3.00" }).waitFor();
    await page.getByTestId("learning-stall").filter({ hasText: "0 / 20 rounds" }).waitFor();
    // The research focus starts with the default (support and resistance) and can be changed.
    assert.match(await page.getByTestId("learning-focus").inputValue(), /^Support and resistance on NQ/);
    await page.getByTestId("learning-focus").fill("Gold: does the Asia range hold at the London open?");
    await page.getByTestId("learning-focus-save").click();
    await page.getByText("Saved — used from the next round on").waitFor();
    await page.getByTestId("learning-toggle").click();
    await page.getByTestId("learning-state").filter({ hasText: "Needs Claude" }).waitFor();
    await page.getByText("Connect Claude under Settings").waitFor();
    await page.waitForTimeout(300);
    await shot("10c-learning");
    await page.getByTestId("learning-toggle").filter({ hasText: "Stop learning" }).click();
    await page.getByTestId("learning-state").filter({ hasText: "Off" }).waitFor();
    // The trained model: judged on months it never saw — random prices teach it nothing.
    await page.getByTestId("subnav-model").click();
    await page.getByTestId("trained-model").scrollIntoViewIfNeeded();
    await page.getByTestId("training-state").filter({ hasText: "Off" }).waitFor();
    await page.getByTestId("training-verdict").filter({ hasText: /No edge over the baseline|Not confirmed/ }).waitFor();
    await page.getByTestId("training-history").filter({ hasText: "Run 1" }).waitFor();
    await page.getByTestId("training-calibration").waitFor();
    await page.getByTestId("training-toggle").filter({ hasText: "Daily: off" }).waitFor();
    await page.waitForTimeout(300);
    await shot("10e-trained-model");
    await page.getByTestId("tab-home").click();
    await page.getByTestId("learning-row").filter({ hasText: "Off" }).waitFor();
  });

  await step("Bot Lab: MetaTrader found, test terminal set up, an EA imported, backtested, copied", async () => {
    await page.getByTestId("tab-quantlab").click(); // JARVIS 2.0: Bots is a QuantLab section
    await page.getByTestId("ql-nav-mt5-bots").click();
    await page.getByTestId("bots-page").waitFor();
    await page.getByTestId("bot-check-app").filter({ hasText: "✓" }).waitFor();
    await page.getByTestId("bot-check-data").filter({ hasText: "1 EA(s)" }).waitFor();
    await page.getByTestId("bot-setup-button").click();
    await page.getByRole("button", { name: "Open test terminal" }).waitFor();
    await page.getByRole("button", { name: "Import GoldScalper" }).click();
    await page.getByTestId("bot-panel").waitFor();
    await page.getByTestId("bot-backtest").click(); // compiled and run through the fake Wine
    await page.getByTestId("bot-results").waitFor({ timeout: 60_000 });
    await page.getByTestId("bot-months").waitFor();
    await page.getByTestId("bot-account-needed").first().waitFor();
    await page.waitForTimeout(300);
    await shot("11-bots");
    await page.getByTestId("bot-results").scrollIntoViewIfNeeded();
    await page.mouse.wheel(0, 380);
    await page.waitForTimeout(300);
    await shot("11b-bot-results");
    await page.getByTestId("bot-install-0").click();
    await page.getByText("Copied to MetaTrader").waitFor();
    await page.getByTestId("tab-home").click();
  });

  await step("QuantLab: broken data refused, synthetic data passported, a spec frozen, a run judged critically", async () => {
    const top = () => page.getByTestId("quantlab-page").evaluate((el) => el.scrollTo(0, 0));
    await page.getByTestId("tab-quantlab").click();
    await page.getByTestId("ql-nav-mt5-bots").and(page.locator('[aria-current="page"]')).waitFor(); // remembered section
    await page.getByTestId("ql-solo-banner").waitFor(); // the private Solo workspace says so
    await page.getByTestId("ql-nav-backtest-lab").click();
    await page.getByTestId("bl-launcher").waitFor();
    await page.getByTestId("ql-nav-overview").click();
    await page.getByTestId("qr-getting-started").waitFor(); // no number before a real run
    await page.getByTestId("ql-mode-research").click();
    await page.getByTestId("ql-nav-equity-lab-r1").click();
    await page.getByTestId("ql-empty").waitFor(); // the R1 lab, unchanged
    await page.getByTestId("ql-section-datasets").click();
    await page.getByTestId("ql-fixture-invalid_ohlc_duplicate.csv").click();
    await page.getByTestId("ql-data-blocked").waitFor(); // fail closed
    await page.getByTestId("ql-passport").getByText("DUPLICATE_TIMESTAMP").waitFor();
    await page.getByTestId("ql-fixture-ma_crossover_case.csv").click();
    await page.getByTestId("ql-passport").getByTestId("ql-synthetic").waitFor();
    await top();
    await page.waitForTimeout(200);
    await shot("11c-quantlab-passport");

    await page.getByTestId("ql-section-strategies").click();
    await page.getByTestId("ql-preset-fixture").click();
    await page.getByTestId("ql-spec-summary").waitFor();
    assert.equal(await page.getByTestId("ql-save-strategy").isDisabled(), true, "saved without review");
    await page.getByRole("button", { name: "Confirm all" }).click();
    await page.getByTestId("ql-reviewed").check();
    await shot("11d-quantlab-architect");
    await page.getByTestId("ql-save-strategy").click();
    await page.getByTestId("ql-run-dataset").selectOption({ index: 1 });
    await page.getByTestId("ql-run").click();
    const experiment = page.getByTestId("ql-experiment").and(page.locator('[data-status="completed"]'));
    await experiment.waitFor({ timeout: 30_000 });
    await experiment.getByTestId("ql-verdict").first().filter({ hasText: "Inconclusive" }).waitFor();
    await page.getByTestId("ql-equity-chart").waitFor();
    assert.match(await page.getByTestId("ql-metric-net").innerText(), /7\.50 USD/); // the fixture's 92.50 end
    await page.waitForTimeout(300);
    await shot("11e-quantlab-experiment");
    await page.getByTestId("ql-tab-trades").click();
    await page.getByTestId("ql-trade-row").first().click();
    await page.getByTestId("ql-trade-detail").waitFor();
    await shot("11f-quantlab-trades");
    await page.getByTestId("ql-tab-audit").click();
    await page.getByTestId("ql-reproduce").click();
    await page.getByTestId("ql-repro-result").filter({ hasText: "Identical" }).waitFor();
    await page.getByTestId("ql-section-overview").click();
    await page.getByTestId("ql-cockpit-verdict").waitFor();
    await page.waitForTimeout(300);
    await shot("11g-quantlab-cockpit");
    await page.getByTestId("tab-home").click();
  });

  await step("QuantLab futures: Databento connect, quote, approval, download, dataset, JARVIS draft, validation, report", async () => {
    const lab = () => page.getByTestId("quantlab-page");
    const top = () => lab().evaluate((el) => el.scrollTo(0, 0));
    await page.getByTestId("tab-quantlab").click();
    await page.getByTestId("ql-nav-data-hub").click();
    await page.getByTestId("qh-fixture-banner").waitFor(); // the fixture is never mistaken for Databento
    await page.getByTestId("qh-key").fill("db-REVOKED00000000000000000000");
    await page.getByTestId("qh-connect").click();
    await page.getByTestId("qh-message").filter({ hasText: "rejected" }).waitFor();
    await page.getByTestId("qh-key").fill("db-FIXTURE0000000000000000000000");
    await page.getByTestId("qh-connect").click();
    await page.getByTestId("qh-test").waitFor(); // only shown once connected
    assert.equal(await page.getByTestId("qh-key").count(), 0, "the key field is gone once connected");
    await page.getByTestId("qh-start").fill("2025-11-03");
    await page.getByTestId("qh-end").fill("2026-03-03");
    await page.getByTestId("qh-resolve").click();
    await page.getByTestId("qh-resolution").getByText("instrument").first().waitFor();
    await page.getByTestId("qh-get-quote").click();
    const quote = page.getByTestId("qh-quote");
    await quote.waitFor();
    assert.match(await page.getByTestId("qh-quote-cost").innerText(), /^\$\d/);
    assert.equal(await page.getByTestId("qh-approve").isDisabled(), true, "no download without explicit approval");
    await page.waitForTimeout(200);
    await shot("11l-quantlab-quote");
    await page.getByTestId("qh-agree").check();
    await page.getByTestId("qh-approve").click();
    await page.locator('[data-testid="qh-job"][data-status="COMPLETED"]').first().waitFor({ timeout: 90_000 });
    await page.getByTestId("qh-build").click();
    await page.getByTestId("qh-dataset-detail").getByText("ROLLS").waitFor({ timeout: 30_000 });
    await page.getByTestId("qh-preview").waitFor();
    await page.getByTestId("qh-dataset-detail").scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    await shot("11m-quantlab-dataset");

    await page.getByTestId("ql-nav-strategy-studio").click();
    await page.getByTestId("qr-idea").fill("Trade the breakout of NQ's first 15 minutes, stop at the other side of the range, 2R target, flat before the close.");
    await page.getByTestId("qr-draft").click();
    await page.getByTestId("qr-draft-notes").getByText("Scripted test model").waitFor();
    await page.getByTestId("qr-state").filter({ hasText: "DRAFT" }).waitFor(); // an unknown blocks the run
    await page.getByTestId("qr-confirm-rule.direction").click();
    await page.getByTestId("qr-state").filter({ hasText: "READY" }).waitFor();
    assert.equal(await page.getByTestId("qr-save").isDisabled(), true, "saved without review");
    await page.getByTestId("qr-reviewed").check();
    await top();
    await page.waitForTimeout(250);
    await shot("11n-quantlab-studio");
    await page.getByTestId("qr-save").click();
    await page.getByTestId("qr-save-message").filter({ hasText: "Saved as version 1" }).waitFor();
    await page.getByTestId("qr-run-validation").click();
    await page.getByTestId("qr-net").waitFor({ timeout: 120_000 }); // Backtest Lab once the run completed
    await page.getByTestId("qr-session-chart").waitFor();
    await top();
    await page.waitForTimeout(400);
    await shot("11o-quantlab-backtest");

    await page.getByTestId("ql-nav-validation").click();
    await page.getByTestId("qr-verdict-main").filter({ hasText: "Insufficient evidence" }).waitFor(); // synthetic: capped
    await page.locator('[data-testid="qr-test"][data-id="LEAKAGE"][data-status="PASSED"]').waitFor();
    await page.locator('[data-testid="qr-test"][data-id="HOLDOUT"][data-status="NOT_RUN"]').waitFor(); // sealed
    await page.locator('[data-testid="qr-test"][data-id="PARAMETER_SENSITIVITY"]').click();
    await page.getByTestId("qr-grid").waitFor();
    await top();
    await page.waitForTimeout(300);
    await shot("11p-quantlab-validation");

    await page.getByTestId("ql-nav-trade-explorer").click();
    await page.getByTestId("qr-trade-row").first().click();
    await page.getByTestId("qr-trade-story").waitFor();
    await page.getByTestId("qr-trade-chart").waitFor();
    await page.waitForTimeout(300);
    await shot("11q-quantlab-trade");

    await page.getByTestId("ql-nav-reports").click();
    await page.getByTestId("qr-report").getByText("SYNTHETIC FIXTURE DATA").first().waitFor();
    await page.getByTestId("qr-reproduce").click();
    await page.getByTestId("qr-repro").filter({ hasText: "Identical" }).waitFor({ timeout: 60_000 });

    await page.getByTestId("ql-nav-experiments").click();
    const variants = Number(await page.getByTestId("qr-variants").innerText());
    assert.ok(variants >= 6, `every grid point counts as a variant (got ${variants})`);
    await page.getByTestId("ql-mode-institutional").click();
    await page.getByTestId("ql-nav-risk-execution").click();
    await page.getByTestId("qr-audit").getByText("PASS").first().waitFor();
    assert.equal(await page.getByTestId("qr-audit").getByText("FAIL").count(), 0, "ledger audit passes");
    await page.getByTestId("ql-nav-overview").click();
    await page.getByTestId("qr-recent").waitFor();
    await page.waitForTimeout(300);
    await shot("11r-quantlab-overview");
    await page.getByTestId("ql-mode-simple").click();
    await page.getByTestId("tab-home").click();
  });

  await step("QuantLab local data first: Data Vault buys MNQ once, then Strategy → NQ → 5m → Backtest runs offline", async () => {
    const lab = () => page.getByTestId("quantlab-page");
    const top = () => lab().evaluate((el) => el.scrollTo(0, 0));
    const vault = () => fetch(`${backendUrl}/quantlab/data/vault`).then((r) => r.json());
    await page.getByTestId("tab-quantlab").click();
    await page.getByTestId("ql-nav-data-vault").click();
    await page.getByTestId("qv-vault").waitFor();
    await page.getByTestId("qv-series-row").filter({ hasText: "NQ.v.0" }).waitFor(); // the earlier download
    await page.getByTestId("qv-backup-warning").waitFor(); // the only copy is on this disk
    // One-time purchase of MNQ: free quote → explicit approval → batch job → verified files.
    await page.getByTestId("qv-acq-symbol").selectOption("MNQ");
    await page.getByTestId("qv-acq-start").fill("2025-11-03");
    await page.getByTestId("qv-acq-end").fill("2026-03-03");
    await page.getByTestId("qv-acq-quote").click();
    await page.getByTestId("qv-acq").and(page.locator('[data-status="QUOTED"]')).waitFor();
    assert.match(await page.getByTestId("qv-acq-cost").innerText(), /^Quote \$\d/);
    assert.equal(await page.getByTestId("qv-acq-approve").isDisabled(), true, "no purchase without the confirmation");
    await page.getByTestId("qv-acquire").scrollIntoViewIfNeeded();
    await page.waitForTimeout(200);
    await shot("11s-vault-quote");
    await page.getByTestId("qv-acq-confirm").check();
    await page.getByTestId("qv-acq-approve").click();
    await page.getByTestId("qv-acq-execute").click();
    await page.getByTestId("qv-acq").and(page.locator('[data-status="COMPLETED"]')).waitFor({ timeout: 90_000 });
    await page.getByTestId("qv-series-row").filter({ hasText: "MNQ.v.0" }).waitFor({ timeout: 30_000 });
    // Offline mode: from here no market-data request may leave the app.
    await page.getByTestId("qv-offline-toggle").click();
    await page.getByTestId("qv-offline-state").filter({ hasText: "Offline mode on" }).waitFor();
    await top();
    await page.waitForTimeout(300);
    await shot("11t-data-vault");
    const before = (await vault()).provider_requests;

    await page.getByTestId("ql-nav-backtest-lab").click();
    await page.getByTestId("bl-strategy").filter({ hasText: "opening range" }).first().click();
    await page.getByTestId("bl-market-NQ").click();
    await page.getByTestId("bl-tf-5m").click();
    await page.getByTestId("bl-readiness").and(page.locator('[data-status="READY"]')).waitFor();
    await page.getByTestId("bl-readiness-badge").filter({ hasText: "Ready locally" }).waitFor();
    assert.equal(await page.getByTestId("bl-tf-10m").getAttribute("data-ready"), "no", "a 15-min range can't be cut into 10-min bars");
    await page.getByTestId("bl-run").click();
    await page.getByTestId("qr-provenance").getByTestId("qr-timeframe").filter({ hasText: "NQ · 5m" }).waitFor({ timeout: 120_000 });
    await page.getByTestId("qr-net").waitFor();
    await page.getByTestId("qr-session-chart").waitFor();
    await top();
    await page.waitForTimeout(400);
    await shot("11u-backtest-lab-nq-5m");
    // The same strategy on MNQ (bought above) at 15m: one tenth of NQ's tick value.
    await page.getByTestId("bl-market-MNQ").click();
    await page.getByTestId("bl-tf-15m").click();
    await page.getByTestId("bl-readiness").and(page.locator('[data-status="READY"]')).waitFor();
    await page.getByTestId("bl-run").click();
    await page.getByTestId("qr-provenance").getByTestId("qr-timeframe").filter({ hasText: "MNQ · 15m" }).waitFor({ timeout: 120_000 });
    await page.getByTestId("bl-market-ES").click();
    await page.getByTestId("bl-readiness").and(page.locator('[data-status="NOT_AVAILABLE"]')).waitFor();
    await page.getByTestId("bl-open-vault").waitFor(); // missing data → the vault, never a silent purchase
    assert.equal(await page.getByTestId("bl-run").isDisabled(), true);
    assert.equal((await vault()).provider_requests, before, "a local backtest made a provider request");

    await page.getByTestId("ql-nav-data-vault").click();
    await page.getByTestId("qv-offline-toggle").click(); // back online for the later steps
    await page.getByTestId("qv-offline-state").filter({ hasText: "Online" }).waitFor();
    await page.getByTestId("tab-home").click();
  });

  await step("QuantLab Idea-to-Edge: video → speech, on-screen text, keyframes, claims, blueprint, questions", async () => {
    const lab = () => page.getByTestId("quantlab-page");
    const top = () => lab().evaluate((el) => el.scrollTo(0, 0));
    await page.getByTestId("tab-quantlab").click();
    await page.getByTestId("ql-nav-idea-inbox").click();
    await page.getByTestId("idea-inbox").waitFor();
    await page.getByTestId("idea-empty").waitFor();
    await page.waitForTimeout(250);
    await shot("12a-idea-inbox");
    // A saved strategy video (synthetic speech + overlays, incl. a boast and an injected instruction).
    await page.getByTestId("idea-file-input").setInputFiles(
      path.join(root, "backend", "tests", "fixtures", "quantlab", "idea_sweep_nq.mp4"),
    );
    await page.getByTestId("idea-message").filter({ hasText: "Reading" }).waitFor();
    const card = page.getByTestId("idea-source").first();
    await card.getByTestId("qs-status").filter({ hasText: /Read|Partly read/ }).waitFor({ timeout: 240_000 });
    // The research mission starts by itself and stops at the first real question.
    await card.getByTestId("idea-mission-chip").filter({ hasText: "Needs you" }).waitFor({ timeout: 120_000 });
    await card.locator("button").first().click();
    const view = page.getByTestId("source-view");
    await view.waitFor();
    await page.getByTestId("source-segments").getByText("enter short", { exact: false }).first().waitFor();
    await page.getByTestId("source-segments").getByText("90% WIN RATE").first().waitFor();
    assert.ok((await page.getByTestId("source-frames").locator("img").count()) > 0, "keyframes are shown");
    await page.getByTestId("source-injection").waitFor(); // the injected instruction is flagged, not obeyed
    await page.getByTestId("source-performance").waitFor(); // the boast is a claim, NOT TESTED
    assert.ok(
      (await page.locator('[data-testid="source-claim"][data-kind="INSTRUCTION_TO_AI"]').count()) > 0,
      "the injected text is recorded as an instruction to AI",
    );
    await page.getByTestId("source-video").waitFor();
    await top();
    await page.waitForTimeout(500);
    await shot("12b-idea-source-video");
    await page.getByTestId("blueprint-view").scrollIntoViewIfNeeded();
    await page.locator('[data-testid="blueprint-provenance"] tr[data-class="DEFAULT_RESEARCH_ASSUMPTION"], [data-testid="blueprint-provenance"] tr[data-class="EXPLICIT_SOURCE"]').first().waitFor();
    await page.waitForTimeout(250);
    await shot("12c-idea-blueprint");
    await top();
    await page.getByTestId("source-open-mission").click();
    await page.getByTestId("qm-wait-questions").waitFor();
    await page.getByTestId("qm-wait-questions").scrollIntoViewIfNeeded();
    await page.waitForTimeout(250);
    await shot("12d-research-questions");
    // This walkthrough continues with a text idea; the video's mission is cancelled from the room.
    await page.getByTestId("qm-cancel").click();
    await page.locator('[data-testid="qm-detail"][data-state="CANCELED"]').waitFor();
  });

  await step("QuantLab Idea-to-Edge: text → answers → paid-data approval → backtest → SENTINEL audit → verdict → dossier", async () => {
    const lab = () => page.getByTestId("quantlab-page");
    const top = () => lab().evaluate((el) => el.scrollTo(0, 0));
    await page.getByTestId("ql-nav-idea-inbox").click();
    await page.getByTestId("idea-text").fill(
      "NQ futures, New York open.\n\nWait for a liquidity sweep of the 15 minute opening range high.\n\n" +
        "When price reclaims the level, enter short. Stop one tick above the sweep wick, target 2R.\n\n" +
        "This strategy wins 90% of the time.",
    );
    await page.getByTestId("idea-note").fill("Only the short side, as in the text.");
    await page.getByTestId("idea-submit").click();
    const card = page.getByTestId("idea-source").first();
    await card.getByTestId("idea-mission-chip").filter({ hasText: "Needs you" }).waitFor({ timeout: 120_000 });
    await card.getByTestId("idea-mission-chip").click();
    const detail = page.getByTestId("qm-detail");
    await detail.waitFor();
    await detail.getByText("Scripted test model", { exact: false }).first().waitFor(); // labelled, always
    await page.getByTestId("qm-q-ny_open").waitFor();
    await page.getByTestId("qm-q-liquidity_sweep").waitFor();
    await page.getByTestId("qm-accept-defaults").click();
    // Databento is connected (Data Hub step): VECTOR asks before buying anything.
    await page.getByTestId("qm-wait-purchase").waitFor({ timeout: 120_000 });
    assert.match(await page.getByTestId("qm-quote-cost").innerText(), /^\$\d/);
    assert.equal(await page.getByTestId("qm-approve").isDisabled(), true, "no purchase without explicit approval");
    await page.getByTestId("qm-wait-purchase").scrollIntoViewIfNeeded();
    await page.waitForTimeout(250);
    await shot("12e-research-approval");
    await page.getByTestId("qm-confirm").check();
    await page.getByTestId("qm-approve").click();
    await page.locator('[data-testid="qm-detail"][data-state="COMPLETE"]').waitFor({ timeout: 400_000 });
    await page.getByTestId("qm-verdict-card").getByText("Insufficient evidence").waitFor(); // synthetic: capped
    await page.getByTestId("qm-verdict-card").getByTestId("qr-fixture").waitFor();
    await page.getByTestId("qm-verdict-card").getByText("SENTINEL audit passed").waitFor();
    assert.ok((await page.locator('[data-testid="qm-task"][data-agent="sentinel"]').count()) >= 2, "SENTINEL audited");
    await top();
    await page.waitForTimeout(400);
    await shot("12f-research-verdict");
    await page.getByTestId("qm-dossier-toggle").click();
    await page.getByTestId("qm-dossier-text").getByText("SYNTHETIC FIXTURE DATA", { exact: false }).first().waitFor();
    await page.getByTestId("qm-dossier").scrollIntoViewIfNeeded();
    await page.waitForTimeout(250);
    await shot("12g-research-dossier");
  });

  await step("QuantLab strategy evolution: reasoned variants, append-only trials, Pareto, one-time holdout lock", async () => {
    const lab = () => page.getByTestId("quantlab-page");
    const top = () => lab().evaluate((el) => el.scrollTo(0, 0));
    await top();
    await page.getByTestId("qm-evolve").click();
    await page.locator('[data-testid="qm-detail"]').getByText("Strategy evolution", { exact: false }).first().waitFor();
    await page.getByTestId("qm-wait-lock").waitFor({ timeout: 600_000 });
    const trials = await page.getByTestId("qm-trial").count();
    assert.ok(trials >= 3, `baseline + variants are all kept (got ${trials})`);
    await page.getByTestId("qm-pareto").waitFor();
    await page.getByTestId("qm-evolution").scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    await shot("12h-evolution");
    assert.equal(await page.getByTestId("qm-lock").isDisabled(), true, "the holdout isn't opened without a choice");
    await page.getByTestId("qm-wait-lock").locator('input[type="radio"]').first().check();
    await page.getByTestId("qm-lock-confirm").check();
    await page.getByTestId("qm-lock").click();
    await page.locator('[data-testid="qm-detail"][data-state="COMPLETE"]').waitFor({ timeout: 300_000 });
    await page.getByTestId("qm-holdout").getByText("First look", { exact: false }).waitFor();
    await page.getByTestId("qm-holdout").scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    await shot("12i-evolution-holdout");
    await page.getByTestId("ql-nav-idea-inbox").click();
    await page.getByTestId("tab-home").click();
  });

  await step("ULTRON: a goal becomes a plan; AXIOM specs, FORGE codes and retries, SENTINEL verifies, export approved", async () => {
    await page.getByTestId("tab-ultron").click();
    // JARVIS 2.0 HQ: nine robots; with no software mission running, nobody is in the Workshop.
    await page.getByTestId("hq-world").waitFor();
    assert.equal(await page.locator('[data-testid^="hq-robot-"]').count(), 9);
    assert.equal(await page.locator('[data-testid^="hq-robot-"][data-room="workshop"]').count(), 0);
    assert.equal(await page.locator('[data-testid="hq-robot-forge"][data-room="homebase"]').count(), 1);
    await page.getByTestId("hq-room-open-homebase").click();
    await page.waitForTimeout(400);
    await shot("11v-ultron-hq-docked");
    await page.getByTestId("hq-room-open-workshop").click(); // Mission Control lives in the Workshop
    await page.getByTestId("ul-overview").waitFor();
    await page.getByTestId("ul-scripted").waitFor(); // the test model is labelled, always
    await page.getByTestId("ul-goal").fill("Build a tested slugify(text) utility in a new Python package.");
    await page.getByTestId("ul-start").click();
    // A robot moves to the Workshop only while it really works on a task of this mission.
    await page.locator('[data-testid="hq-robot-forge"][data-state="working"][data-room="workshop"]').waitFor({ timeout: 90_000 });
    await page.getByTestId("hq-robot-forge").click();
    await page.getByTestId("hq-inspector-now").getByText("Mission 001").waitFor();
    await shot("11w-ultron-hq-forge-working");
    const mission = page.getByTestId("ul-mission");
    await mission.waitFor();
    await page.locator('[data-testid="ul-mission"][data-state="WAITING_APPROVAL"]').waitFor({ timeout: 90_000 });
    assert.equal(await page.locator('[data-testid="ul-node"][data-state="COMPLETE"]').count(), 3);
    await page.locator('[data-testid="ul-node"]').filter({ hasText: "Implement slugify" }).click();
    const panel = page.getByTestId("ul-task-panel");
    await panel.getByText("2 of 3").waitFor(); // the runtime's own check failed the first attempt
    await page.waitForTimeout(300);
    await shot("11h-ultron-mission");
    await panel.getByTestId("ul-artifact").filter({ hasText: "patch" }).first().click();
    await page.getByTestId("ul-artifact-viewer").getByText("sha256 intact").waitFor();
    await shot("11i-ultron-patch");
    await page.keyboard.press("Escape");
    await page.getByTestId("ul-approve").click();
    await page.locator('[data-testid="ul-mission"][data-state="COMPLETE"]').waitFor({ timeout: 30_000 });
    await page.getByTestId("ul-report").getByText("Exported").waitFor();
    const exported = readdirSync(ultronExports);
    assert.equal(exported.length, 1, "one exported project");
    assert.ok(existsSync(path.join(ultronExports, exported[0], "textutil", "__init__.py")));
    await page.getByTestId("ul-view-activity").click();
    await page.getByTestId("ul-activity").getByText("denied").first().waitFor(); // the out-of-scope write
    // Done: FORGE is back at the dock with its verified output; the matrix is in the Academy.
    await page.locator('[data-testid="hq-robot-forge"][data-room="homebase"]').waitFor({ timeout: 30_000 });
    await page.getByTestId("hq-robot-forge").click();
    await page.getByTestId("hq-inspector-last").getByText("slugify", { exact: false }).waitFor();
    await page.getByTestId("hq-room-open-academy").click();
    await page.getByTestId("ul-agents").waitFor();
    await page.waitForTimeout(300); // let the tab's colour transition finish
    await shot("11j-ultron-agents");
    await page.getByTestId("hq-room-open-workshop").click();
    await page.getByTestId("ul-view-overview").click();
    await page.getByTestId("ul-mission-card").first().waitFor();
    await page.waitForTimeout(300);
    await shot("11k-ultron-overview");
    await page.getByTestId("tab-home").click();
  });

  await step("ULTRON QuantLab: a Solo strategy is shared on purpose; SENTINEL challenges and CIPHER proposes on the team copy; Solo stays unchanged", async () => {
    const solo = await fetch(`${backendUrl}/quantlab/backtest/options`).then((r) => r.json());
    const mine = solo.strategies.find((x) => x.name.toLowerCase().includes("opening range"));
    assert.ok(mine, "a saved Solo strategy to share");
    await page.getByTestId("tab-ultron").click();
    await page.getByTestId("hq-room-open-quantlab").click();
    await page.getByTestId("team-quantlab").waitFor();
    // Nothing reaches the team without the owner's explicit share (a copy, with chosen grants).
    await page.getByTestId("tm-share-version").selectOption(mine.version_id);
    await page.getByTestId("tm-share-grant-propose_versions").check();
    assert.equal(await page.getByTestId("tm-share-submit").isDisabled(), true, "the share needs a confirmation");
    await page.getByTestId("tm-share-confirm").check();
    await page.getByTestId("tm-share-submit").click();
    await page.getByTestId("tm-project-view").waitFor();
    await page.getByTestId("workspace-team").first().waitFor();
    // The same backtest form and engine as Solo, listing only the team's copy.
    await page.getByTestId("bl-market-NQ").click();
    await page.getByTestId("bl-tf-5m").click();
    await page.getByTestId("bl-readiness").and(page.locator('[data-status="READY"]')).waitFor();
    await page.getByTestId("bl-run").click();
    const projects = () => fetch(`${backendUrl}/team/quantlab/projects`).then((r) => r.json());
    for (let i = 0; i < 300 && (await projects())[0]?.runs[0]?.status !== "COMPLETED"; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    assert.equal((await projects())[0].runs[0].status, "COMPLETED", "the team backtest finished");
    // SENTINEL challenges the run independently; CIPHER proposes variants on the team copy.
    await page.getByTestId("tm-ask-sentinel").click();
    await page.locator('[data-testid="tm-task"][data-agent="sentinel"][data-state="COMPLETE"]').waitFor({ timeout: 60_000 });
    // A 5-minute run: the 1-minute second engine is reported as not run, never as agreeing.
    await page.locator('[data-testid="tm-task"][data-agent="sentinel"]').filter({ hasText: "second engine not run" }).waitFor();
    await page.getByTestId("tm-ask-cipher").click();
    await page.locator('[data-testid="tm-task"][data-agent="cipher"][data-state="COMPLETE"]').waitFor({ timeout: 300_000 });
    await page.getByTestId("tm-trial").nth(1).waitFor();
    await page.getByTestId("tm-version").filter({ hasText: "proposed by CIPHER" }).waitFor();
    await page.getByTestId("tm-ask").scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    await shot("11x-ultron-quantlab-team");
    // The private Solo strategy is unchanged and still the only one Solo lists.
    const after = await fetch(`${backendUrl}/quantlab/backtest/options`).then((r) => r.json());
    const same = after.strategies.find((x) => x.id === mine.id);
    assert.equal(same.version_number, mine.version_number, "no new Solo version without the owner");
    assert.equal(after.strategies.length, solo.strategies.length, "the team copy isn't listed in Solo");
    const [project] = await projects();
    assert.deepEqual(project.audit.map((a) => a.action), ["share", "backtest", "ask", "ask"]);
    await page.getByTestId("tab-home").click();
  });

  await step("memory: added in the Training Hub, kept, forgotten; the morning briefing is set in Settings", async () => {
    await page.getByTestId("tab-training").click();
    await page.getByTestId("subnav-knowledge").click();
    await page.getByTestId("memory-input").fill("Prefers short answers.");
    await page.getByTestId("memory-input").press("Enter");
    await page.getByTestId("memory-item").filter({ hasText: "Prefers short answers." }).waitFor();
    await page.waitForTimeout(300);
    await shot("10d-memory");
    await page.getByTestId("memory-item").first().hover();
    await page.getByRole("button", { name: "Forget M1" }).click();
    await page.getByTestId("memory-item").waitFor({ state: "detached" });

    await page.getByTestId("tab-settings").click();
    await page.getByTestId("briefing-toggle").waitFor();
    assert.equal(await page.getByTestId("briefing-toggle").getAttribute("aria-checked"), "false"); // off in tests
    await page.getByTestId("briefing-toggle").click();
    await page.getByTestId("briefing-toggle").and(page.locator('[aria-checked="true"]')).waitFor();
    await page.getByTestId("briefing-next").filter({ hasText: ":00" }).waitFor();
    await page.getByTestId("briefing-toggle").click(); // back off: no real market data here
    await page.getByTestId("briefing-toggle").and(page.locator('[aria-checked="false"]')).waitFor();
    await page.getByTestId("tab-home").click();
  });

  await step("feedback: 👍 / 👎 on replies reach the Training Hub; a 👍 becomes a behaviour test case, a 👎 note a correction", async () => {
    await page.getByTestId("tab-home").click();
    const replies = page.getByTestId("jarvis-reply");
    const ask = async (text) => {
      const before = await replies.count();
      await command(text);
      await page.waitForFunction(
        (n) => document.querySelectorAll('[data-testid="jarvis-reply"]').length > n,
        before,
      );
      return replies.last();
    };
    const good = await ask("What time is it?");
    await good.getByTestId("rate-up").click();
    await good.locator('[data-testid="reply-rating"][data-rated="1"]').waitFor();
    const wrong = await ask("hello");
    await wrong.getByTestId("rate-down").click();
    await wrong.getByTestId("rate-note").fill("Say which app opened.");
    await wrong.getByTestId("rate-send").click();
    await wrong.locator('[data-testid="reply-rating"][data-rated="-1"]').waitFor();

    await page.getByTestId("tab-training").click();
    await page.getByTestId("subnav-feedback").click();
    await page.getByTestId("feedback-by-route").waitFor();
    const down = page.getByTestId("feedback-item").filter({ hasText: "Say which app opened." });
    assert.equal(await down.getAttribute("data-rating"), "-1");
    await down.getByTestId("feedback-keep").click();
    await down.getByTestId("feedback-keep").filter({ hasText: "Kept as correction" }).waitFor();
    const up = page.getByTestId("feedback-item").filter({ hasText: "What time is it?" }).first();
    await up.getByTestId("feedback-pin").click();
    await up.getByTestId("feedback-pin").filter({ hasText: "Pinned as test case" }).waitFor();
    await page.waitForTimeout(300);
    await shot("10e-feedback");

    // Evaluations: the behaviour check runs the curated + pinned sentences locally and is stored.
    await page.getByTestId("subnav-evaluations").click();
    await page.getByTestId("eval-pinned").filter({ hasText: "What time is it?" }).waitFor();
    await page.getByTestId("eval-run").click();
    const last = page.getByTestId("eval-last");
    await last.waitFor();
    assert.equal(await last.getAttribute("data-passed"), await last.getAttribute("data-total"), "every case understood");
    await page.getByTestId("eval-quantlab").waitFor(); // stored QuantLab verdicts from the runs above
    await page.getByTestId("eval-research").waitFor();
    await page.waitForTimeout(300);
    await shot("10f-evaluations");
    await page.getByTestId("eval-pinned").getByTestId("eval-unpin").click();
    await page.getByTestId("eval-pinned").waitFor({ state: "detached" });

    // The correction is an ordinary memory: visible under Knowledge and removable.
    await page.getByTestId("subnav-knowledge").click();
    const memory = page.getByTestId("memory-item").filter({ hasText: "Say which app opened." });
    await memory.waitFor();
    await memory.hover();
    await memory.getByRole("button", { name: /^Forget M/ }).click();
    await memory.waitFor({ state: "detached" });
    await page.getByTestId("tab-home").click();
  });

  await step("AI & Billing: plan first (test stand-in), its limit pauses the brain, paid fallback only with explicit caps", async () => {
    await page.getByTestId("tab-settings").click();
    await page.getByTestId("ai-billing").waitFor();
    // Defaults: nothing connected, paid fallback OFF, test keystore and stand-in binary labelled.
    await page.getByTestId("ai-route-chip").and(page.locator('[data-route="paused"]')).waitFor();
    await page.getByTestId("ai-paid-state").filter({ hasText: "Off" }).waitFor();
    await page.getByTestId("ai-test-keystore").waitFor();
    await page.getByTestId("ai-plan-binary").filter({ hasText: "JARVIS_AI_CLI" }).waitFor();
    assert.equal(await page.getByTestId("ai-plan-enable").isDisabled(), true, "personal use must be confirmed first");
    await page.getByTestId("ai-paid-budget").fill("20");
    await page.getByTestId("ai-paid-cap").fill("5");
    assert.equal(await page.getByTestId("ai-paid-approve").isDisabled(), true, "paid needs the explicit confirmation");

    // The owner confirms personal use → the plan route (stand-in "claude" signed in via claude.ai).
    await page.getByTestId("ai-plan-personal").check();
    await page.getByTestId("ai-plan-enable").click();
    await page.getByTestId("ai-plan-status").and(page.locator('[data-state="CONNECTED"]')).waitFor();
    await page.getByTestId("ai-route-chip").and(page.locator('[data-route="plan"]')).waitFor();
    await page.getByTestId("ai-current").filter({ hasText: "Claude plan" }).waitFor();
    await page.getByTestId("brain-status").filter({ hasText: "via Claude plan" }).waitFor();
    await page.waitForTimeout(300);
    await shot("10g-ai-billing-plan");

    await page.getByTestId("tab-home").click();
    fakeClaudeConfig({ mode: "ok", structured: { text: "Answered on your Claude plan (test stand-in).", tool_calls: [] } });
    await command("plan my evening");
    await reply("Answered on your Claude plan (test stand-in).").waitFor();

    // The plan's usage limit: paid fallback isn't approved, local is off → the brain pauses honestly.
    fakeClaudeConfig({ mode: "limit" });
    await command("plan my weekend");
    await reply("My AI is paused right now.").waitFor();
    await page.getByTestId("ai-route-chip").and(page.locator('[data-route="paused"]')).waitFor();
    const log = readFileSync(path.join(fakeClaudeHome, ".fake-claude-log.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
    for (const call of log) {
      for (const name of ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"]) {
        assert.ok(!call.env.includes(name), `${name} reached Claude Code`);
      }
    }
    const asks = log.filter((call) => call.args[0] === "-p");
    assert.ok(asks.length >= 2 && asks.every((call) => call.args.includes("--safe-mode") && call.args.includes("--no-session-persistence")));

    await page.getByTestId("tab-settings").click();
    await page.getByTestId("ai-plan-status").and(page.locator('[data-state="LIMIT_REACHED"]')).waitFor();
    await page.getByTestId("ai-current-reason").filter({ hasText: "usage limit" }).waitFor();
    await page.getByTestId("ai-events").filter({ hasText: "limit" }).waitFor();
    await page.getByTestId("ai-billing").evaluate((el) => el.scrollIntoView({ block: "start" }));
    await page.waitForTimeout(300);
    await shot("10h-ai-billing-limit");
    // Approve paid fallback with caps — still no API key, so nothing can be billed.
    await page.getByTestId("ai-paid-budget").fill("20");
    await page.getByTestId("ai-paid-cap").fill("5");
    await page.getByTestId("ai-paid-confirm").check();
    await page.getByTestId("ai-paid-approve").click();
    await page.getByTestId("ai-paid-state").filter({ hasText: "On" }).waitFor();
    await page.getByTestId("ai-paid-meter").filter({ hasText: "$0.00 of $20.00" }).waitFor();
    await page.getByTestId("ai-current-reason").filter({ hasText: "no API key" }).waitFor();
    await page.getByTestId("ai-paid-state").evaluate((el) => el.scrollIntoView({ block: "center" }));
    await page.waitForTimeout(300);
    await shot("10i-ai-billing-paid");
    const usage = await fetch(`${backendUrl}/ai/usage`).then((r) => r.json());
    assert.ok(usage.approvals.some((a) => a.kind === "paid_approved"), "the approval is logged");
    assert.ok(usage.calls.every((c) => c.billed === 0), "nothing was billed");

    // Back to the defaults for the rest of the run.
    await page.getByTestId("ai-paid-disable").click();
    await page.getByTestId("ai-paid-state").filter({ hasText: "Off" }).waitFor();
    await page.getByTestId("ai-plan-disable").click();
    await page.getByTestId("ai-plan-enable").waitFor();
    fakeClaudeConfig({ mode: "ok" });
    await page.getByTestId("tab-home").click();
  });

  await step("APEX: one trading screen — chart in five timeframes, zones, structure, long/short/no-trade, candidates, data status, JARVIS; journal, SENTINEL and track record in their sub-areas", async () => {
    await page.getByTestId("tab-quantlab").click();
    await page.getByTestId("ql-nav-apex").click();
    await page.getByTestId("apex-panel").waitFor();
    await page.getByTestId("apex-market-XAUUSD").click();
    await page.getByTestId("apex-tab-analysis").click();
    await page.getByTestId("apex-analyze").click();
    await page.getByTestId("apex-synthetic").waitFor({ timeout: 60_000 });
    assert.equal(await page.getByTestId("apex-data-chip").getAttribute("data-tone"), "warning", "test data is never shown as plain OK");
    await page.locator('[data-testid="apex-chart"][data-minutes="15"]').waitFor();
    assert.ok((await page.getByTestId("apex-zone").count()) >= 4, "zones above and below the price");
    assert.ok((await page.getByTestId("apex-chart-zone").count()) >= 1, "zones drawn on the chart");
    assert.equal(await page.getByTestId("apex-scenario").count(), 4, "hold/break for S1 and R1 (long and short cases)");
    await page.getByTestId("apex-no-trade").waitFor();
    await page.getByTestId("apex-trend-60").waitFor();
    const shown = await page.getByTestId("apex-candidate").count();
    assert.ok(shown + (await page.getByTestId("apex-rejected").count()) >= 1, "a candidate or the reason why not");
    const journal = await fetch(`${backendUrl}/apex/journal?market=XAUUSD`).then((r) => r.json());
    assert.equal(journal.candidates.length, shown, "every candidate shown is journaled");
    assert.ok(journal.candidates.every((c) => c.synthetic === true), "test-data candidates are marked");
    if (shown) {
      await page.getByTestId("apex-candidate").first().click();
      await page.getByTestId("apex-chart-candidate").waitFor();
    }
    // The chart: zoom, reset, and every timeframe — only bars known at the moment.
    const chart = page.getByTestId("apex-chart");
    const before = Number(await chart.getAttribute("data-bars"));
    await page.getByTestId("apex-chart-zoom-in").click();
    assert.ok(Number(await chart.getAttribute("data-bars")) < before, "zoom in shows fewer bars");
    await page.getByTestId("apex-chart-reset").click();
    assert.equal(Number(await chart.getAttribute("data-bars")), before);
    const box = await chart.boundingBox();
    await chart.hover({ position: { x: Math.round(box.width * 0.5), y: Math.round(box.height * 0.4) } }); // scrolls into view first
    await page.getByTestId("apex-chart-crosshair").waitFor();
    await page.getByTestId("apex-panel").scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    await shot("14a-apex-xauusd-chart");
    for (const minutes of [1, 5, 60, 240]) {
      await page.getByTestId(`apex-tf-${minutes}`).click();
      await page.locator(`[data-testid="apex-chart"][data-minutes="${minutes}"]`).waitFor();
    }
    await page.getByTestId("apex-tf-60").click();
    await page.locator('[data-testid="apex-chart"][data-minutes="60"]').waitFor();
    await page.waitForTimeout(300);
    await shot("14b-apex-xauusd-1h");
    await page.getByTestId("apex-tf-15").click();
    // JARVIS's reading: only the analysis' own numbers; "Ask" puts an editable question in the command bar.
    const headline = await page.getByTestId("apex-jarvis-headline").innerText();
    assert.ok(headline.startsWith("XAUUSD"), headline);
    await page.getByTestId("apex-jarvis").scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    await shot("14c-apex-jarvis");
    await page.getByTestId("apex-ask-jarvis").click();
    assert.match(await page.getByTestId("command-input").inputValue(), /APEX analysis for XAUUSD/);
    await page.getByTestId("command-input").press("Escape"); // nothing is sent without Enter

    // Data: the status with causes and actions (test data has nothing to repair).
    await page.getByTestId("apex-tab-data").click();
    await page.locator('[data-testid="apex-cause"][data-code="SYNTHETIC"]').waitFor();
    await page.locator('[data-testid="apex-source"]').first().waitFor();
    await page.waitForTimeout(300);
    await shot("14d-apex-data");

    // SENTINEL recomputes the stored analysis from the data on disk now and must match it.
    await page.getByTestId("apex-tab-sentinel").click();
    await page.getByTestId("apex-audit").click();
    await page.locator('[data-testid="apex-audit-result"][data-passed="yes"]').waitFor({ timeout: 60_000 });
    assert.equal(await page.locator('[data-testid="apex-audit-check"][data-passed="yes"]').count(), 3);
    const hq = await fetch(`${backendUrl}/team/hq`).then((r) => r.json());
    const sentinel = hq.robots.find((r) => r.id === "sentinel");
    assert.ok(sentinel.work.some((w) => w.source === "apex"), "SENTINEL's re-check is in the HQ");

    // MNQ: the same index as NQ at $2 per point.
    await page.getByTestId("apex-market-MNQ").click();
    await page.getByTestId("apex-tab-analysis").click();
    await page.getByTestId("apex-analyze").click();
    await page.getByTestId("apex-synthetic").waitFor({ timeout: 60_000 });
    const mnq = await fetch(`${backendUrl}/apex/analyses?market=MNQ`).then((r) => r.json());
    const latest = await fetch(`${backendUrl}/apex/analyses/${mnq[0].id}`).then((r) => r.json());
    assert.equal(latest.spec.point_value, 2);
    for (const c of latest.candidates) assert.equal(c.risk_usd, Math.round(c.risk_points * 2 * 100) / 100);
    const chart60 = await fetch(`${backendUrl}/apex/analyses/${latest.id}/chart?minutes=60&limit=100`).then((r) => r.json());
    assert.ok(chart60.same_data && chart60.bars.length === 100 && chart60.bars.every((b) => b[0] < latest.as_of));

    // The journal keeps every candidate unchanged.
    await page.getByTestId("apex-tab-journal").click();
    await page.getByTestId("apex-journal").waitFor();

    // The rule's track record: walk-forward on the last 20 days, judged by later bars.
    await page.getByTestId("apex-tab-track").click();
    await page.getByTestId("apex-days").fill("20");
    await page.getByTestId("apex-evaluate").click();
    await page.getByTestId("apex-verdict").waitFor({ timeout: 180_000 });
    assert.ok((await page.getByTestId("apex-track-row").count()) === 3, "all, in-sample, out-of-sample");
    // The stated hold rates are checked against what later touches did (calibration).
    const calibration = page.getByTestId("apex-calibration");
    await calibration.waitFor();
    assert.ok(["CALIBRATED", "MISCALIBRATED", "INSUFFICIENT"].includes(await calibration.getAttribute("data-code")));
    await page.getByTestId("apex-track").scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    await shot("14e-apex-track-record");
    await page.getByTestId("apex-tab-analysis").click();
    // There is no order path anywhere in APEX.
    const routes = await fetch(`${backendUrl}/openapi.json`).then((r) => r.json());
    const apexPaths = Object.keys(routes.paths).filter((p) => p.startsWith("/apex"));
    assert.ok(apexPaths.length >= 9 && apexPaths.every((p) => !/order|trade|broker|execute/i.test(p)), apexPaths.join(","));
    await page.getByTestId("tab-home").click();
  });

  await step("navigation: five tabs by keyboard, deep links survive reload and history, usable at 1366×768 and the minimum window; no secrets in browser storage", async () => {
    const tabIds = await page
      .getByRole("navigation", { name: "Main" })
      .locator('[data-testid^="tab-"]')
      .evaluateAll((els) => els.map((e) => e.getAttribute("data-testid")));
    assert.deepEqual(tabIds, ["tab-home", "tab-training", "tab-quantlab", "tab-ultron", "tab-settings"]);
    const current = (id) => page.getByTestId(`tab-${id}`).and(page.locator('[aria-current="page"]'));
    // Keyboard: arrows move along the tabs, Enter opens one.
    await page.getByTestId("tab-home").click();
    await page.getByTestId("tab-home").focus();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Enter");
    await current("quantlab").waitFor();
    await page.keyboard.press("End");
    await page.keyboard.press("Enter");
    await current("settings").waitFor();

    // Deep link → reload keeps the page; back/forward walk the history.
    await page.evaluate(() => (location.hash = "#/training/evaluations"));
    await page.getByTestId("training-evaluations").waitFor();
    await page.reload();
    await page.getByTestId("training-evaluations").waitFor({ timeout: 30_000 });
    await current("training").waitFor();
    await page.getByTestId("tab-ultron").click();
    await page.getByTestId("hq-world").or(page.getByTestId("hq-room-list")).first().waitFor();
    await page.evaluate(() => history.back());
    await page.getByTestId("training-evaluations").waitFor();
    await page.evaluate(() => history.forward());
    await current("ultron").waitFor();
    // Old links still land on their new homes.
    await page.evaluate(() => (location.hash = "#/learning"));
    await current("training").waitFor();
    await page.evaluate(() => (location.hash = "#/bots"));
    await current("quantlab").waitFor();
    await page.getByTestId("ql-nav-mt5-bots").and(page.locator('[aria-current="page"]')).waitFor();

    // Main controls stay reachable at 1366×768 and at the window's minimum size (1120×720).
    // The real window is resized (not an emulated viewport), as the owner would drag it.
    const resize = async (width, height) => {
      await app.evaluate(({ BrowserWindow }, [w, h]) => BrowserWindow.getAllWindows()[0].setContentSize(w, h), [width, height]);
      await page.setViewportSize({ width, height }).catch(() => {});
      await page.waitForFunction(([w, h]) => window.innerWidth === w && window.innerHeight === h, [width, height]);
    };
    for (const [width, height] of [[1366, 768], [1120, 720]]) {
      await resize(width, height);
      const inside = async (testId) => {
        const box = await page.getByTestId(testId).first().boundingBox();
        assert.ok(box, `${testId} is rendered at ${width}×${height}`);
        assert.ok(box.x >= 0 && box.x + box.width <= width + 1 && box.y >= 0 && box.y + box.height <= height + 1, `${testId} fits ${width}×${height}`);
      };
      for (const id of ["home", "training", "quantlab", "ultron", "settings"]) await inside(`tab-${id}`);
      await page.getByTestId("tab-home").click();
      await inside("command-input");
      await inside("command-send");
      await page.getByTestId("tab-quantlab").click();
      await page.getByTestId("ql-nav-backtest-lab").click();
      await page.getByTestId("bl-run").scrollIntoViewIfNeeded();
      await inside("bl-run");
      await page.getByTestId("tab-ultron").click();
      await page.getByTestId("hq-room-open-homebase").scrollIntoViewIfNeeded();
      await inside("hq-room-open-homebase");
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      assert.ok(overflow <= 0, `no sideways scrolling at ${width}×${height} (${overflow}px)`);
      await shot(`12-layout-${width}x${height}`);
    }
    await resize(1480, 920);

    // Keys typed in this run (Databento test keys) never reach browser storage.
    const stored = await page.evaluate(() =>
      [localStorage, sessionStorage].flatMap((store) => Object.keys(store).map((k) => `${k}=${store.getItem(k)}`)),
    );
    assert.ok(stored.length > 0, "the route memory is stored");
    assert.deepEqual(stored.filter((entry) => /db-[A-Z0-9]{10,}|sk-ant|api[_-]?key|secret/i.test(entry)), []);
    await page.getByTestId("tab-home").click();
  });

  await step("closing the window keeps JARVIS running; it comes back from the menu bar", async () => {
    await page.getByTestId("tab-settings").click();
    await page.getByTestId("background-mode").filter({ hasText: "keeps running" }).waitFor();
    await page.getByTestId("background-mode").scrollIntoViewIfNeeded();
    await shot("10f-always-on");
    await page.getByTestId("tab-home").click();
    const visible = () => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]?.isVisible());
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]?.close());
    await new Promise((resolve) => setTimeout(resolve, 500));
    assert.equal(await visible(), false, "the window should be hidden, not closed");
    assert.equal((await health())?.status, "ok", "the backend should keep running");
    await app.evaluate(({ app: electronApp }) => electronApp.emit("activate")); // Dock / menu bar
    for (let i = 0; i < 20 && !(await visible()); i += 1) await new Promise((resolve) => setTimeout(resolve, 100));
    assert.equal(await visible(), true);
  });

  await step("a crashed backend is started again — the window says so meanwhile and recovers", async () => {
    const before = await health();
    process.kill(before.pid, "SIGKILL");
    // While it is down the window says so (never "online"), then the banner goes away again.
    await page.getByTestId("backend-banner").waitFor({ timeout: 15_000 });
    assert.notEqual(await page.getByTestId("connection-status").innerText(), "BACKEND ONLINE");
    let after = null;
    for (let i = 0; i < 120 && !(after && after.pid !== before.pid); i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 250));
      after = await health();
    }
    assert.ok(after && after.pid !== before.pid, "the backend did not come back");
    await headline.filter({ hasText: "Everything is nominal." }).waitFor(); // the dashboard reconnected
    await page.getByTestId("connection-status").filter({ hasText: "Backend online" }).waitFor({ timeout: 30_000 });
    await page.getByTestId("backend-banner").waitFor({ state: "detached", timeout: 30_000 });
  });

  await step("idle state returns", async () => {
    await page.waitForTimeout(22_000);
    await headline.filter({ hasText: "Everything is nominal." }).waitFor();
    await shot("09-idle-after-work");
  });
} finally {
  await app.close();
}

await step("backend supervised by Electron is stopped on quit", async () => {
  await new Promise((resolve) => setTimeout(resolve, 1500));
  const alive = await fetch(`${backendUrl}/health`).then(
    () => true,
    () => false,
  );
  assert.equal(alive, false);
});

await step("the conversation is still there after a restart", async () => {
  const again = await electron.launch({
    executablePath: electronBinary,
    args: [appDir, ...(process.platform === "linux" ? ["--no-sandbox"] : [])],
    env: {
      ...process.env,
      JARVIS_BACKEND_URL: backendUrl,
      JARVIS_SYSTEM_BACKEND: "simulated",
      JARVIS_DATA_DIR: mainDataDir,
      JARVIS_UPDATES: "off",
    },
  });
  try {
    const page = await again.firstWindow();
    await page.setViewportSize({ width: 1480, height: 920 }).catch(() => {});
    const conversation = page.getByTestId("conversation");
    await conversation.getByText("Open Notepad.", { exact: true }).waitFor({ timeout: 30_000 });
    await page.getByTestId("jarvis-reply").filter({ hasText: "I couldn't open Blender." }).waitFor();
    const said = await page.getByTestId("you-said").count();
    assert.ok(said >= 6, `expected the earlier commands, found ${said}`);
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(outDir, "13-conversation-after-restart.png") });
    // ULTRON's mission ledger survived the restart too.
    const missions = await fetch(`${backendUrl}/ultron/missions`).then((r) => r.json());
    assert.equal(missions[0]?.state, "COMPLETE", "ULTRON mission persisted across the restart");
    // QuantLab research runs too.
    const research = await fetch(`${backendUrl}/quantlab/research/runs`).then((r) => r.json());
    assert.ok(research.some((r) => r.status === "COMPLETED"), "QuantLab runs persisted across the restart");
  } finally {
    await again.close();
  }
});

await step("starting a newer version takes over from the running one; the same version doesn't", async () => {
  const launchArgs = [appDir, ...(process.platform === "linux" ? ["--no-sandbox"] : [])];
  const env = (build) => ({
    ...process.env,
    JARVIS_BUILD: build,
    JARVIS_UPDATES: "off",
    JARVIS_BACKEND_URL: backendUrl,
    JARVIS_SYSTEM_BACKEND: "simulated",
    JARVIS_DATA_DIR: mkdtempSync(path.join(tmpdir(), "jarvis-e2e-takeover-")),
  });
  const online = (page) =>
    page.getByTestId("connection-status").filter({ hasText: "Backend online" }).waitFor({ timeout: 60_000 });

  const older = await electron.launch({ executablePath: electronBinary, args: launchArgs, env: env("1111111-older") });
  await online(await older.firstWindow());
  assert.equal((await health())?.build, "1111111-older");
  const olderClosed = new Promise((resolve) => older.once("close", resolve));

  const newer = await electron.launch({ executablePath: electronBinary, args: launchArgs, env: env("2222222-newer") });
  try {
    await olderClosed;
    const page = await newer.firstWindow();
    await online(page);
    assert.equal((await health())?.build, "2222222-newer");
    assert.ok((await page.getByTestId("build").innerText()).includes("2222222"));

    // Same version again: exits at once, the running window stays.
    const again = spawn(electronBinary, launchArgs, { env: env("2222222-newer"), stdio: "ignore" });
    const code = await new Promise((resolve) => again.once("exit", resolve));
    assert.equal(code, 0);
    assert.equal((await health())?.build, "2222222-newer");
    await page.getByTestId("connection-status").filter({ hasText: "Backend online" }).waitFor();
  } finally {
    await newer.close();
  }
});

await step("a backend that can't start is diagnosed in the window — missing package (Repair), busy port (who holds it); no endless restarts", async () => {
  const launchArgs = [appDir, ...(process.platform === "linux" ? ["--no-sandbox"] : [])];
  const base = {
    ...process.env,
    JARVIS_UPDATES: "off",
    JARVIS_SYSTEM_BACKEND: "simulated",
    JARVIS_BACKEND_RESTART_DELAYS_MS: "200", // the real app waits 1, 3, 10, 30 s
  };
  // 1. A backend whose package is missing (a stand-in Python that fails like one; pip "works").
  const fake = mkdtempSync(path.join(tmpdir(), "jarvis-e2e-broken-"));
  const python = path.join(fake, "python");
  writeFileSync(
    python,
    `#!/bin/sh\nif [ "$2" = "pip" ]; then touch "${fake}/pip-ran"; exit 0; fi\n` +
      `echo "Traceback (most recent call last):" 1>&2\necho "ModuleNotFoundError: No module named 'pyarrow'" 1>&2\nexit 1\n`,
  );
  chmodSync(python, 0o755);
  const broken = await electron.launch({
    executablePath: electronBinary,
    args: launchArgs,
    env: { ...base, JARVIS_BACKEND_URL: "http://127.0.0.1:8689", JARVIS_PYTHON: python, JARVIS_DATA_DIR: mkdtempSync(path.join(tmpdir(), "jarvis-e2e-broken-data-")) },
  });
  try {
    const win = await broken.firstWindow();
    const crashed = win.locator('[data-testid="backend-banner"][data-state="crashed"]');
    await crashed.waitFor({ timeout: 60_000 }); // five quick crashes, then it stops trying
    await win.locator('[data-testid="backend-diagnosis"][data-code="MISSING_DEPENDENCY"]').waitFor();
    assert.match(await win.getByTestId("backend-diagnosis").innerText(), /pyarrow/);
    assert.match(await win.getByTestId("connection-status").innerText(), /offline/i);
    await win.getByTestId("backend-diagnostics").click();
    assert.match(await win.getByTestId("backend-log").innerText(), /ModuleNotFoundError: No module named 'pyarrow'/);
    await win.waitForTimeout(300);
    await win.screenshot({ path: path.join(outDir, "15a-backend-missing-package.png") });
    await win.getByTestId("backend-repair").click();
    for (let i = 0; i < 100 && !existsSync(path.join(fake, "pip-ran")); i += 1) await new Promise((r) => setTimeout(r, 100));
    assert.ok(existsSync(path.join(fake, "pip-ran")), "Repair reinstalled the backend's packages");
    await crashed.waitFor({ timeout: 60_000 }); // the stand-in stays broken: diagnosed again, no loop
  } finally {
    await broken.close();
  }

  // 2. The port is held by another program: the real backend can't bind and the window says who holds it.
  const blocker = http.createServer((_req, res) => {
    res.statusCode = 404;
    res.end("not JARVIS");
  });
  await new Promise((resolve) => blocker.listen(8688, "127.0.0.1", resolve));
  const busy = await electron.launch({
    executablePath: electronBinary,
    args: launchArgs,
    env: { ...base, JARVIS_BACKEND_URL: "http://127.0.0.1:8688", JARVIS_DATA_DIR: mkdtempSync(path.join(tmpdir(), "jarvis-e2e-busy-")) },
  });
  try {
    const win = await busy.firstWindow();
    await win.locator('[data-testid="backend-diagnosis"][data-code="PORT_IN_USE"]').waitFor({ timeout: 120_000 });
    assert.match(await win.getByTestId("backend-diagnosis").innerText(), new RegExp(`pid ${process.pid}`));
    await win.waitForTimeout(300);
    await win.screenshot({ path: path.join(outDir, "15b-backend-port-in-use.png") });
    // Free the port (dropping kept-alive connections, or close() waits for them), press
    // Restart backend: the real backend starts and the header says so.
    blocker.close();
    blocker.closeAllConnections();
    await win.getByTestId("backend-restart").click();
    await win.getByTestId("connection-status").filter({ hasText: "Backend online" }).waitFor({ timeout: 90_000 });
    await win.getByTestId("backend-banner").waitFor({ state: "detached", timeout: 30_000 });
  } finally {
    blocker.close();
    blocker.closeAllConnections();
    await busy.close();
  }
});

await step("the Mac app's loader starts JARVIS from the checkout and replaces a terminal JARVIS", async () => {
  const launchArgs = (dir) => [dir, ...(process.platform === "linux" ? ["--no-sandbox"] : [])];
  const env = {
    ...process.env,
    JARVIS_UPDATES: "off",
    JARVIS_BACKEND_URL: backendUrl,
    JARVIS_SYSTEM_BACKEND: "simulated",
    JARVIS_DATA_DIR: mkdtempSync(path.join(tmpdir(), "jarvis-e2e-loader-")),
  };
  const online = (page) =>
    page.getByTestId("connection-status").filter({ hasText: "Backend online" }).waitFor({ timeout: 60_000 });

  const terminal = await electron.launch({ executablePath: electronBinary, args: launchArgs(appDir), env });
  await online(await terminal.firstWindow());
  const terminalClosed = new Promise((resolve) => terminal.once("close", resolve));

  // What JARVIS.app contains in Contents/Resources/app (see scripts/install-mac-app.sh).
  const loader = path.join(mkdtempSync(path.join(tmpdir(), "jarvis-e2e-app-")), "app");
  execFileSync(process.execPath, [path.join(root, "scripts", "mac-app", "bootstrap.mjs"), loader]);
  const macApp = await electron.launch({ executablePath: electronBinary, args: launchArgs(loader), env });
  try {
    await terminalClosed; // same version, but the app takes over from the terminal one
    const page = await macApp.firstWindow();
    await online(page);
    assert.equal(await macApp.evaluate(({ app }) => app.getName()), "JARVIS");
    assert.equal(await macApp.evaluate(() => process.env.JARVIS_APP), "1");
  } finally {
    await macApp.close();
  }
});

await step("the Update button installs a newer version and JARVIS restarts on it", async () => {
  // A "GitHub" remote holding this code, the user's checkout of it, and a newer
  // version pushed afterwards — then the user clicks Update.
  const base = mkdtempSync(path.join(tmpdir(), "jarvis-e2e-update-"));
  const gitEnv = { ...process.env, GIT_AUTHOR_NAME: "E2E", GIT_AUTHOR_EMAIL: "e2e@example.com" };
  Object.assign(gitEnv, { GIT_COMMITTER_NAME: "E2E", GIT_COMMITTER_EMAIL: "e2e@example.com" });
  const git = (cwd, ...args) =>
    execFileSync("git", ["-c", "init.defaultBranch=main", ...args], { cwd, env: gitEnv }).toString().trim();

  const checkout = path.join(base, "checkout");
  const files = git(root, "ls-files", "--cached", "--others", "--exclude-standard").split("\n");
  for (const file of files.filter((f) => f && existsSync(path.join(root, f)))) {
    cpSync(path.join(root, file), path.join(checkout, file), { recursive: true });
  }
  // Reuse the installed packages instead of downloading them again. Bring their
  // dependency stamps up to date first: otherwise the copy's preflight would
  // reinstall the backend into the shared venv, pointing it at the copy.
  execFileSync(process.execPath, ["scripts/preflight.mjs"], { cwd: root, stdio: "ignore" });
  symlinkSync(path.join(root, "node_modules"), path.join(checkout, "node_modules"));
  symlinkSync(path.join(root, "backend", ".venv"), path.join(checkout, "backend", ".venv"));
  git(checkout, "init", "-q");
  git(checkout, "add", "-A", ":!node_modules", ":!backend/.venv");
  git(checkout, "commit", "-q", "-m", "The version the user has");
  git(base, "init", "-q", "--bare", "remote.git");
  git(checkout, "remote", "add", "origin", path.join(base, "remote.git"));
  git(checkout, "push", "-q", "-u", "origin", "main");
  execFileSync(process.execPath, ["scripts/build-app.mjs"], { cwd: checkout, stdio: "ignore" });

  const publisher = path.join(base, "publisher");
  git(base, "clone", "-q", "remote.git", "publisher");
  writeFileSync(path.join(publisher, "UPDATE-TEST.md"), "new version\n");
  git(publisher, "add", "UPDATE-TEST.md");
  git(publisher, "commit", "-q", "-m", "Add voice control");
  git(publisher, "push", "-q", "origin", "main");
  const newer = git(publisher, "rev-parse", "HEAD");

  const updateUrl = "http://127.0.0.1:8797";
  const updateHealth = () => fetch(`${updateUrl}/health`).then((r) => r.json(), () => null);
  const appDirectory = path.join(checkout, "apps", "desktop");
  // Started like a normal launch (no Node inspector, which would keep the old
  // process alive at exit); the test attaches to the window over CDP instead —
  // the restarted app gets the same arguments, so it is reachable the same way.
  const cdpPort = 9337;
  const app = spawn(
    electronBinary,
    [appDirectory, `--remote-debugging-port=${cdpPort}`, ...(process.platform === "linux" ? ["--no-sandbox"] : [])],
    {
      env: {
        ...process.env,
        JARVIS_BACKEND_URL: updateUrl,
        JARVIS_SYSTEM_BACKEND: "simulated",
        JARVIS_DATA_DIR: mkdtempSync(path.join(tmpdir(), "jarvis-e2e-update-data-")),
      },
      stdio: "ignore",
    },
  );
  const exited = new Promise((resolve) => app.once("exit", resolve));
  const attach = async () => {
    for (let i = 0; i < 240; i += 1) {
      try {
        const browser = await chromium.connectOverCDP(`http://127.0.0.1:${cdpPort}`);
        const page = browser.contexts()[0]?.pages().find((p) => p.url().startsWith("app://"));
        if (page) return { browser, page };
        await browser.close();
      } catch {
        /* not up yet */
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    throw new Error("JARVIS window not reachable");
  };

  try {
    const before = await attach();
    const { page } = before;
    await page.getByTestId("connection-status").filter({ hasText: "Backend online" }).waitFor({ timeout: 60_000 });
    await page.getByTestId("update-button").click({ timeout: 30_000 });
    await page.getByTestId("update-panel").getByText("Add voice control").waitFor();
    await page.waitForTimeout(200);
    await shotOf(page, "11-update-available");
    await page.getByTestId("update-now").click();
    await page.getByTestId("update-progress").waitFor();
    // JARVIS quits to restart itself on the new code — or shows what went wrong.
    const problem = page.getByTestId("update-button").waitFor({ timeout: 150_000 }).then(
      async () => `update failed: ${await page.getByTestId("update-panel").innerText().catch(() => "")}`,
      () => new Promise(() => {}), // the window went away: expected while restarting
    );
    const timeout = new Promise((resolve) => setTimeout(() => resolve("no restart within 150 s"), 150_000));
    const outcome = await Promise.race([exited.then(() => "restarted"), problem, timeout]);
    assert.equal(outcome, "restarted", outcome);
    await before.browser.close().catch(() => {});

    const after = await attach();
    await after.page.getByTestId("connection-status").filter({ hasText: "Backend online" }).waitFor({ timeout: 60_000 });
    assert.ok((await after.page.getByTestId("build").innerText()).includes(newer.slice(0, 7)));
    assert.equal((await updateHealth())?.build, newer);
    assert.ok(existsSync(path.join(checkout, "UPDATE-TEST.md")));
    await after.page.waitForTimeout(300);
    await shotOf(after.page, "12-updated-and-restarted");
    await after.browser.close().catch(() => {});
  } finally {
    // The restarted app isn't a child of the test: end it like a user would.
    app.kill("SIGTERM");
    spawn("pkill", ["-TERM", "-f", appDirectory], { stdio: "ignore" });
    for (let i = 0; i < 40 && (await updateHealth()); i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  assert.equal(await updateHealth(), null, "the restarted JARVIS didn't stop its backend on quit");
});

console.log(`\nAll ${steps.length} end-to-end checks passed. Screenshots: ${outDir}`);
