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
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, symlinkSync, writeFileSync } from "node:fs";
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
    await page.getByTestId("connection-status").filter({ hasText: "System online" }).waitFor({ timeout: 30_000 });
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
    await page.getByTestId("agent-operator").and(page.locator('[data-status="active"]')).waitFor();
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
    await page.getByRole("button", { name: "Settings" }).click();
    await page.getByText("Strong confirmation").waitFor();
    await page.getByText("~/Documents").first().waitFor(); // folders the file tools may see
    await page.getByText("Updates are turned off").waitFor();
    await page.waitForTimeout(300);
    await shot("08-settings");
    await page.getByRole("button", { name: "Home" }).click();
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
    await page.getByRole("button", { name: "Home" }).click();
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
    await page.getByRole("button", { name: "Home" }).click();
    await page.getByTestId("voice-off").waitFor();
  });

  await step("learning starts and stops from its page; without Claude it says what it needs", async () => {
    await page.getByTestId("learning-row").filter({ hasText: "Off" }).waitFor();
    await page.getByRole("button", { name: "Learning", exact: true }).click();
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
    await page.getByTestId("trained-model").scrollIntoViewIfNeeded();
    await page.getByTestId("training-state").filter({ hasText: "Off" }).waitFor();
    await page.getByTestId("training-verdict").filter({ hasText: /No edge over the baseline|Not confirmed/ }).waitFor();
    await page.getByTestId("training-history").filter({ hasText: "Run 1" }).waitFor();
    await page.getByTestId("training-calibration").waitFor();
    await page.getByTestId("training-toggle").filter({ hasText: "Daily: off" }).waitFor();
    await page.waitForTimeout(300);
    await shot("10e-trained-model");
    await page.getByRole("button", { name: "Home" }).click();
    await page.getByTestId("learning-row").filter({ hasText: "Off" }).waitFor();
  });

  await step("Bot Lab: MetaTrader found, test terminal set up, an EA imported, backtested, copied", async () => {
    await page.getByRole("button", { name: "Bots", exact: true }).click();
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
    await page.getByRole("button", { name: "Home" }).click();
  });

  await step("QuantLab: broken data refused, synthetic data passported, a spec frozen, a run judged critically", async () => {
    const top = () => page.getByTestId("quantlab-page").evaluate((el) => el.scrollTo(0, 0));
    await page.getByRole("button", { name: "QuantLab", exact: true }).click();
    await page.getByTestId("idea-inbox").waitFor(); // QuantLab opens on the Idea Inbox
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
    await page.getByRole("button", { name: "Home" }).click();
  });

  await step("QuantLab futures: Databento connect, quote, approval, download, dataset, JARVIS draft, validation, report", async () => {
    const lab = () => page.getByTestId("quantlab-page");
    const top = () => lab().evaluate((el) => el.scrollTo(0, 0));
    await page.getByRole("button", { name: "QuantLab", exact: true }).click();
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
    await page.getByRole("button", { name: "Home" }).click();
  });

  await step("QuantLab Idea-to-Edge: video → speech, on-screen text, keyframes, claims, blueprint, questions", async () => {
    const lab = () => page.getByTestId("quantlab-page");
    const top = () => lab().evaluate((el) => el.scrollTo(0, 0));
    await page.getByRole("button", { name: "QuantLab", exact: true }).click();
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
    await page.getByRole("button", { name: "Home" }).click();
  });

  await step("ULTRON: a goal becomes a plan; AXIOM specs, FORGE codes and retries, SENTINEL verifies, export approved", async () => {
    await page.getByRole("button", { name: "ULTRON", exact: true }).click();
    await page.getByTestId("ul-overview").waitFor();
    await page.getByTestId("ul-scripted").waitFor(); // the test model is labelled, always
    await page.getByTestId("ul-goal").fill("Build a tested slugify(text) utility in a new Python package.");
    await page.getByTestId("ul-start").click();
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
    await page.getByTestId("ul-view-agent-matrix").click();
    await page.getByTestId("ul-agents").waitFor();
    await page.waitForTimeout(300); // let the tab's colour transition finish
    await shot("11j-ultron-agents");
    await page.getByTestId("ul-view-overview").click();
    await page.getByTestId("ul-mission-card").first().waitFor();
    await page.waitForTimeout(300);
    await shot("11k-ultron-overview");
    await page.getByRole("button", { name: "Home" }).click();
  });

  await step("memory: added on Home, kept, forgotten; the morning briefing is set in Settings", async () => {
    await page.getByTestId("memory-input").fill("Prefers short answers.");
    await page.getByTestId("memory-input").press("Enter");
    await page.getByTestId("memory-item").filter({ hasText: "Prefers short answers." }).waitFor();
    await page.waitForTimeout(300);
    await shot("10d-memory");
    await page.getByTestId("memory-item").first().hover();
    await page.getByRole("button", { name: "Forget M1" }).click();
    await page.getByTestId("memory-item").waitFor({ state: "detached" });

    await page.getByRole("button", { name: "Settings" }).click();
    await page.getByTestId("briefing-toggle").waitFor();
    assert.equal(await page.getByTestId("briefing-toggle").getAttribute("aria-checked"), "false"); // off in tests
    await page.getByTestId("briefing-toggle").click();
    await page.getByTestId("briefing-toggle").and(page.locator('[aria-checked="true"]')).waitFor();
    await page.getByTestId("briefing-next").filter({ hasText: ":00" }).waitFor();
    await page.getByTestId("briefing-toggle").click(); // back off: no real market data here
    await page.getByTestId("briefing-toggle").and(page.locator('[aria-checked="false"]')).waitFor();
    await page.getByRole("button", { name: "Home" }).click();
  });

  await step("AI & Billing: plan first (test stand-in), its limit pauses the brain, paid fallback only with explicit caps", async () => {
    await page.getByRole("button", { name: "Settings" }).click();
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

    await page.getByRole("button", { name: "Home" }).click();
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

    await page.getByRole("button", { name: "Settings" }).click();
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
    await page.getByRole("button", { name: "Home" }).click();
  });

  await step("closing the window keeps JARVIS running; it comes back from the menu bar", async () => {
    await page.getByRole("button", { name: "Settings" }).click();
    await page.getByTestId("background-mode").filter({ hasText: "keeps running" }).waitFor();
    await page.getByTestId("background-mode").scrollIntoViewIfNeeded();
    await shot("10f-always-on");
    await page.getByRole("button", { name: "Home" }).click();
    const visible = () => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]?.isVisible());
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]?.close());
    await new Promise((resolve) => setTimeout(resolve, 500));
    assert.equal(await visible(), false, "the window should be hidden, not closed");
    assert.equal((await health())?.status, "ok", "the backend should keep running");
    await app.evaluate(({ app: electronApp }) => electronApp.emit("activate")); // Dock / menu bar
    for (let i = 0; i < 20 && !(await visible()); i += 1) await new Promise((resolve) => setTimeout(resolve, 100));
    assert.equal(await visible(), true);
  });

  await step("a crashed backend is started again", async () => {
    const before = await health();
    process.kill(before.pid, "SIGKILL");
    let after = null;
    for (let i = 0; i < 120 && !(after && after.pid !== before.pid); i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 250));
      after = await health();
    }
    assert.ok(after && after.pid !== before.pid, "the backend did not come back");
    await headline.filter({ hasText: "Everything is nominal." }).waitFor(); // the dashboard reconnected
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
    page.getByTestId("connection-status").filter({ hasText: "System online" }).waitFor({ timeout: 60_000 });

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
    await page.getByTestId("connection-status").filter({ hasText: "System online" }).waitFor();
  } finally {
    await newer.close();
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
    page.getByTestId("connection-status").filter({ hasText: "System online" }).waitFor({ timeout: 60_000 });

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
    await page.getByTestId("connection-status").filter({ hasText: "System online" }).waitFor({ timeout: 60_000 });
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
    await after.page.getByTestId("connection-status").filter({ hasText: "System online" }).waitFor({ timeout: 60_000 });
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
