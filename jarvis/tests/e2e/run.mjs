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
import { mkdirSync, mkdtempSync } from "node:fs";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { _electron as electron } from "playwright-core";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const appDir = path.join(root, "apps", "desktop");
const outDir = path.resolve(process.argv[2] ?? path.join(root, "tests", "e2e", "output"));
const backendUrl = "http://127.0.0.1:8799";
mkdirSync(outDir, { recursive: true });

const require = createRequire(path.join(appDir, "package.json"));
const electronBinary = require("electron");

const steps = [];
const step = async (name, fn) => {
  const started = Date.now();
  await fn();
  steps.push(`✓ ${name} (${Date.now() - started} ms)`);
  console.log(steps.at(-1));
};

// A backend from an "older version" already occupies the port — the app must replace it.
const python = path.join(
  root, "backend", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
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

const app = await electron.launch({
  executablePath: electronBinary,
  args: [appDir, ...(process.platform === "linux" ? ["--no-sandbox"] : [])],
  env: {
    ...process.env,
    JARVIS_BACKEND_URL: backendUrl,
    JARVIS_SYSTEM_BACKEND: "simulated",
    JARVIS_DATA_DIR: mkdtempSync(path.join(tmpdir(), "jarvis-e2e-")),
  },
});

try {
  const page = await app.firstWindow();
  await page.setViewportSize({ width: 1480, height: 920 }).catch(() => {});
  const shot = (name) => page.screenshot({ path: path.join(outDir, `${name}.png`) });
  const headline = page.getByTestId("jarvis-headline");
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
    await headline.filter({ hasText: "Notepad is open." }).waitFor({ timeout: 10_000 });
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
    await headline.filter({ hasText: "PowerShell is open." }).waitFor();
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
    await headline.filter({ hasText: "Registry Editor is open." }).waitFor();
  });

  await step("rejection stops the mission and JARVIS acknowledges", async () => {
    await command("open cmd");
    await page.getByTestId("approval-card").getByTestId("reject").click();
    await headline.filter({ hasText: "Understood. I won't open Command Prompt." }).waitFor();
  });

  await step("open-ended request without a key explains how to connect the model", async () => {
    await command("plan my evening");
    await headline.filter({ hasText: "I can't reason about that yet." }).waitFor();
    await page.getByRole("definition").filter({ hasText: "Settings" }).first().waitFor();
  });

  await step("failures are explained, not dumped", async () => {
    await command("open blender");
    await headline.filter({ hasText: "I couldn't open Blender." }).waitFor();
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

await step("starting a newer version takes over from the running one; the same version doesn't", async () => {
  const launchArgs = [appDir, ...(process.platform === "linux" ? ["--no-sandbox"] : [])];
  const env = (build) => ({
    ...process.env,
    JARVIS_BUILD: build,
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

console.log(`\nAll ${steps.length} end-to-end checks passed. Screenshots: ${outDir}`);
