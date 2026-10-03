// Fails fast with an actionable message when JARVIS has not been set up yet.
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const windows = process.platform === "win32";
const python = path.join(root, "backend", ".venv", windows ? "Scripts/python.exe" : "bin/python");

const missing = [];
if (!existsSync(path.join(root, "node_modules"))) missing.push("npm packages");
if (!process.env.JARVIS_PYTHON && !existsSync(python)) missing.push("Python environment (backend/.venv)");

if (missing.length > 0) {
  const setup = windows ? "powershell -ExecutionPolicy Bypass -File scripts\\setup.ps1" : "./scripts/setup.sh";
  console.error(`\nJARVIS is not set up yet — missing: ${missing.join(", ")}.\nRun:  ${setup}\n`);
  process.exit(1);
}
