// Development launcher: Vite dev server on a free port, then Electron pointed at it.
// Replaces a fixed port (5173), which fails whenever another dev server is running.
import { spawn } from "node:child_process";
import { createRequire } from "node:module";

import { createServer } from "vite";

const require = createRequire(import.meta.url);

const server = await createServer({ server: { strictPort: false } });
await server.listen();
server.printUrls();

const url = (server.resolvedUrls?.local[0] ?? "").replace(/\/$/, "");
if (!url) {
  console.error("Vite did not report a local URL.");
  await server.close();
  process.exit(1);
}

const electronBinary = require("electron"); // downloads the binary on first use
const electron = spawn(electronBinary, [".", ...process.argv.slice(2)], {
  stdio: "inherit",
  env: { ...process.env, JARVIS_RENDERER_URL: url },
});

let stopping = false;
const stop = async (code) => {
  if (stopping) return;
  stopping = true;
  await server.close();
  process.exit(code);
};

electron.on("exit", (code) => void stop(code ?? 0));
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    electron.kill(signal);
    void stop(0);
  });
}
