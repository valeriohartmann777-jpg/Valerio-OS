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
import { cpSync, existsSync, mkdirSync, mkdtempSync, symlinkSync, writeFileSync } from "node:fs";
import { execFileSync, spawn } from "node:child_process";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { _electron as electron, chromium } from "playwright-core";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const appDir = path.join(root, "apps", "desktop");
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

const mainDataDir = mkdtempSync(path.join(tmpdir(), "jarvis-e2e-"));
const app = await electron.launch({
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
    await page.getByTestId("learning-toggle").click();
    await page.getByTestId("learning-state").filter({ hasText: "Needs Claude" }).waitFor();
    await page.getByText("Connect Claude under Settings").waitFor();
    await page.waitForTimeout(300);
    await shot("10c-learning");
    await page.getByTestId("learning-toggle").filter({ hasText: "Stop learning" }).click();
    await page.getByTestId("learning-state").filter({ hasText: "Off" }).waitFor();
    await page.getByRole("button", { name: "Home" }).click();
    await page.getByTestId("learning-row").filter({ hasText: "Off" }).waitFor();
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
