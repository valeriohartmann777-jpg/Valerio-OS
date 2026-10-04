// Runs before `npm run dev` / `npm start`:
// 1. fails fast with an actionable message when JARVIS has not been set up yet,
// 2. after a `git pull` that changed dependencies, installs them — so updating
//    JARVIS stays `git pull && npm run dev`.
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const windows = process.platform === "win32";
const venv = path.join(root, "backend", ".venv");
const python = process.env.JARVIS_PYTHON ?? path.join(venv, windows ? "Scripts/python.exe" : "bin/python");
const setup = windows ? "powershell -ExecutionPolicy Bypass -File scripts\\setup.ps1" : "./scripts/setup.sh";

const missing = [];
if (!existsSync(path.join(root, "node_modules"))) missing.push("npm packages");
if (!existsSync(python)) missing.push("Python environment (backend/.venv)");
if (missing.length > 0) {
  console.error(`\nJARVIS is not set up yet — missing: ${missing.join(", ")}.\nRun:  ${setup}\n`);
  process.exit(1);
}

const fingerprint = (file) => createHash("sha256").update(readFileSync(file)).digest("hex");

/** Run `install` when `manifest` changed since the last successful install. */
function sync(label, manifest, stamp, command, args) {
  const current = fingerprint(manifest);
  const previous = existsSync(stamp) ? readFileSync(stamp, "utf8").trim() : "";
  if (current === previous) return;
  console.log(`[jarvis] ${label} changed — installing dependencies…`);
  const result = spawnSync(command, args, { cwd: root, stdio: "inherit", shell: windows && command === "npm" });
  if (result.status !== 0) {
    console.error(`\n[jarvis] installing ${label} dependencies failed. Run:  ${setup}\n`);
    process.exit(1);
  }
  writeFileSync(stamp, `${current}\n`);
}

// A JARVIS_PYTHON outside backend/.venv is managed by whoever set it.
if (!process.env.JARVIS_PYTHON) {
  sync("backend/pyproject.toml", path.join(root, "backend", "pyproject.toml"), path.join(venv, ".jarvis-deps"), python, [
    "-m",
    "pip",
    "install",
    "--quiet",
    "--disable-pip-version-check",
    "-e",
    `${path.join(root, "backend")}[dev]`,
  ]);
}
// --no-save: install exactly what the lockfile says and never rewrite it, so a
// newer npm can't leave local changes that would block the next update.
sync("package-lock.json", path.join(root, "package-lock.json"), path.join(root, "node_modules", ".jarvis-deps"), "npm", [
  "install",
  "--no-save",
  "--no-audit",
  "--no-fund",
]);
