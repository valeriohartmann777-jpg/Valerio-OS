// Builds the dashboard (renderer) and the Electron main process for running
// without the dev server — used by the Mac app installer and the in-app updater.
//
// Both are built into staging folders and only swapped in when everything
// succeeded, so a failed build never leaves JARVIS without a working version.
// (Type checking is CI's job: `npm run build` / scripts/check.sh.)
import { spawnSync } from "node:child_process";
import { existsSync, renameSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const desktop = path.join(root, "apps", "desktop");
const require = createRequire(path.join(desktop, "package.json"));
const bin = (pkg, file) => path.join(path.dirname(require.resolve(`${pkg}/package.json`)), file);

function run(label, script, args) {
  console.log(`[build] ${label}`);
  const result = spawnSync(process.execPath, [script, ...args], { cwd: desktop, stdio: "inherit" });
  if (result.status !== 0) {
    console.error(`[build] ${label} failed — the current version stays in place.`);
    process.exit(1);
  }
}

const targets = ["dist", "dist-electron"];
for (const target of targets) rmSync(path.join(desktop, `${target}.next`), { recursive: true, force: true });

run("dashboard", bin("vite", "bin/vite.js"), ["build", "--outDir", "dist.next", "--emptyOutDir", "--logLevel", "warn"]);
run("electron", bin("typescript", "bin/tsc"), ["-p", "tsconfig.electron.json", "--outDir", "dist-electron.next"]);

for (const target of targets) {
  const live = path.join(desktop, target);
  const old = path.join(desktop, `${target}.old`);
  rmSync(old, { recursive: true, force: true });
  if (existsSync(live)) renameSync(live, old);
  renameSync(path.join(desktop, `${target}.next`), live);
  rmSync(old, { recursive: true, force: true });
}
console.log("[build] done");
