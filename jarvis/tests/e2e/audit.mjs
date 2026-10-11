#!/usr/bin/env node
/**
 * Feature audit crawler (JARVIS 2.0, P0).
 *
 * Starts the real desktop app the way a fresh Mac sees it — no Claude Code, no API key, no
 * Databento key — visits every top-level page and every sub-section it can find, and records:
 *   - console errors and uncaught exceptions in the renderer,
 *   - backend requests answered with 4xx/5xx (or not answered),
 *   - visible error texts,
 *   - disabled buttons that give no reason (no title / aria-description),
 *   - every GET route of the backend without path parameters, called directly.
 * It clicks only navigation, never actions. Output: JSON + Markdown summary + screenshots.
 *
 *   npm run build && xvfb-run -a node tests/e2e/audit.mjs <out-dir>
 */

import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { _electron as electron } from "playwright-core";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const appDir = path.join(root, "apps", "desktop");
const outDir = path.resolve(process.argv[2] ?? path.join(root, "tests", "e2e", "audit-output"));
mkdirSync(outDir, { recursive: true });
const backendUrl = "http://127.0.0.1:8798";
const require = createRequire(path.join(appDir, "package.json"));

process.env.JARVIS_BRIEFING = "off";
process.env.JARVIS_TRAINING = "off";
const app = await electron.launch({
  executablePath: require("electron"),
  args: [appDir, ...(process.platform === "linux" ? ["--no-sandbox"] : [])],
  env: {
    ...process.env,
    JARVIS_BACKEND_URL: backendUrl,
    JARVIS_SYSTEM_BACKEND: "simulated",
    JARVIS_DATA_DIR: mkdtempSync(path.join(tmpdir(), "jarvis-audit-")),
    JARVIS_UPDATES: "off",
    // A fresh Mac: Claude Code not installed, no keys in the keystore.
    JARVIS_AI_CLI: path.join(tmpdir(), "no-claude-here"),
    JARVIS_AI_KEYSTORE: "memory",
    JARVIS_QUANTLAB_KEYSTORE: "memory",
    JARVIS_QUANTLAB_PROVIDER: "fixture",
  },
});

const report = { pages: [], api: [], chat: [], started: new Date().toISOString() };
let current = null;
const page = await app.firstWindow();
await page.setViewportSize({ width: 1440, height: 900 }).catch(() => {});
page.on("console", (msg) => {
  if (msg.type() === "error" && current) current.console.push(msg.text().slice(0, 400));
});
page.on("pageerror", (err) => current?.exceptions.push(String(err).slice(0, 400)));
page.on("response", (res) => {
  const url = res.url();
  if (current && url.startsWith(backendUrl) && res.status() >= 400) {
    current.failed.push(`${res.request().method()} ${url.slice(backendUrl.length)} → ${res.status()}`);
  }
});
page.on("requestfailed", (req) => {
  if (current && req.url().startsWith(backendUrl)) {
    current.failed.push(`${req.method()} ${req.url().slice(backendUrl.length)} → ${req.failure()?.errorText}`);
  }
});

const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

async function inspect(name) {
  await page.waitForTimeout(1500);
  const info = await page.evaluate(() => {
    const visible = (el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
    };
    const buttons = [...document.querySelectorAll("button")].filter(visible);
    const disabledNoReason = buttons
      .filter((b) => b.disabled && !b.title && !b.getAttribute("aria-description") && !b.getAttribute("aria-describedby"))
      .map((b) => (b.innerText || b.getAttribute("aria-label") || "?").trim().slice(0, 60));
    const errors = [...document.querySelectorAll("p, span, div")]
      .filter((el) => visible(el) && el.children.length === 0)
      .map((el) => el.innerText.trim())
      .filter((t) => /\b(error|failed|unavailable|not found|traceback|exception|cannot|can't)\b/i.test(t))
      .slice(0, 25);
    const smallText = [...document.querySelectorAll("body *")]
      .filter((el) => visible(el) && el.children.length === 0 && el.innerText?.trim())
      .filter((el) => parseFloat(getComputedStyle(el).fontSize) < 11).length;
    return { buttons: buttons.length, disabledNoReason, errors, smallText };
  });
  await page.screenshot({ path: path.join(outDir, `${slug(name)}.png`) });
  return info;
}

async function visit(name, open) {
  current = { name, console: [], exceptions: [], failed: [] };
  try {
    await open();
    Object.assign(current, await inspect(name));
  } catch (err) {
    current.crawl_error = String(err).slice(0, 300);
  }
  report.pages.push(current);
  console.log(`· ${name}: ${current.failed.length} failed req, ${current.console.length} console errors`);
}

try {
  await page.getByTestId("connection-status").filter({ hasText: "System online" }).waitFor({ timeout: 60_000 });
  const main = page.locator('nav[aria-label="Main"] button');
  const names = (await main.allInnerTexts()).map((t) => t.trim()).filter(Boolean);
  for (const name of names) {
    await visit(name, () => main.filter({ hasText: name }).first().click());
    // Sub-sections: any other <nav> with buttons on this page (QuantLab, ULTRON, …).
    const subnavs = page.locator("main nav button, nav:not([aria-label='Main']) button");
    const labels = [...new Set((await subnavs.allInnerTexts()).map((t) => t.trim()).filter(Boolean))];
    for (const label of labels.slice(0, 20)) {
      await visit(`${name} › ${label}`, async () => {
        await main.filter({ hasText: name }).first().click();
        await page.locator("nav:not([aria-label='Main']) button").filter({ hasText: label }).first().click();
      });
    }
    // QuantLab detail levels reveal more sections.
    for (const level of ["Research", "Institutional"]) {
      const radio = page.getByRole("radio", { name: level });
      if (name === "QuantLab" && (await radio.count())) {
        await radio.first().click();
        const more = [...new Set((await page.locator("nav[aria-label='QuantLab'] button").allInnerTexts()).map((t) => t.trim()))];
        for (const label of more.filter((l) => !labels.includes(l))) {
          labels.push(label);
          await visit(`${name} (${level}) › ${label}`, () =>
            page.locator("nav[aria-label='QuantLab'] button").filter({ hasText: label }).first().click(),
          );
        }
      }
    }
  }

  // Chat on a fresh install: what does the owner see?
  current = { name: "chat", console: [], exceptions: [], failed: [] };
  await main.filter({ hasText: "Home" }).first().click();
  for (const text of ["Hallo JARVIS", "What time is it?", "Open Notepad."]) {
    await page.getByTestId("command-input").fill(text);
    await page.getByTestId("command-input").press("Enter");
    await page.waitForTimeout(4000);
    const replies = await page.getByTestId("jarvis-reply").allInnerTexts();
    report.chat.push({ sent: text, last_reply: (replies.at(-1) ?? "(no reply)").slice(0, 400) });
  }
  report.chat_page = current;
  await page.screenshot({ path: path.join(outDir, "chat.png") });

  // Every GET route without path parameters, called directly.
  const spec = await fetch(`${backendUrl}/openapi.json`).then((r) => r.json());
  for (const [route, ops] of Object.entries(spec.paths)) {
    if (!ops.get || route.includes("{")) continue;
    const started = Date.now();
    let status;
    let detail = "";
    try {
      const res = await fetch(`${backendUrl}${route}`, { headers: { Origin: "app://jarvis" } });
      status = res.status;
      if (status >= 400) detail = (await res.text()).slice(0, 200);
    } catch (err) {
      status = "network";
      detail = String(err).slice(0, 200);
    }
    report.api.push({ route, status, ms: Date.now() - started, detail });
  }
} finally {
  writeFileSync(path.join(outDir, "audit.json"), JSON.stringify(report, null, 2));
  const lines = ["# Audit crawl", "", `Started ${report.started}`, ""];
  for (const p of report.pages) {
    lines.push(`## ${p.name}`);
    if (p.crawl_error) lines.push(`- crawl error: ${p.crawl_error}`);
    for (const k of ["exceptions", "console", "failed"]) for (const x of p[k] ?? []) lines.push(`- ${k}: ${x}`);
    if (p.disabledNoReason?.length) lines.push(`- disabled without reason: ${p.disabledNoReason.join(" | ")}`);
    if (p.errors?.length) lines.push(`- error texts: ${p.errors.join(" | ")}`);
    if (p.smallText) lines.push(`- text below 11px: ${p.smallText} elements`);
    lines.push("");
  }
  lines.push("## Chat", ...report.chat.map((c) => `- “${c.sent}” → ${c.last_reply.replace(/\n/g, " ")}`), "");
  lines.push("## API GET routes", ...report.api.filter((a) => a.status !== 200).map((a) => `- ${a.route} → ${a.status} ${a.detail}`));
  lines.push(`- ${report.api.filter((a) => a.status === 200).length} routes answered 200`);
  writeFileSync(path.join(outDir, "audit.md"), lines.join("\n"));
  await app.close().catch(() => {});
}
