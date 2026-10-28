/**
 * The backend supervisor against real child processes: a stand-in "Python" that crashes the
 * way a broken backend does. No real backend is started here.
 */

import { createHash } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { type BackendStatus, BackendSupervisor, classify, MAX_QUICK_CRASHES, redact } from "./backend";

describe("diagnosis from what the backend printed", () => {
  it("names a missing package and offers repair", () => {
    const d = classify(
      ["Traceback (most recent call last):", '  File "jarvis/__main__.py", line 3', "ModuleNotFoundError: No module named 'httpx2'"],
      { port: "8765" },
    );
    expect(d.code).toBe("MISSING_DEPENDENCY");
    expect(d.detail).toContain("httpx2");
    expect(d.actions[0]).toBe("repair");
  });

  it("names the program holding the port", () => {
    const d = classify(["ERROR:    [Errno 48] error while attempting to bind on address ('127.0.0.1', 8765): address already in use"], {
      port: "8765",
      portOwner: "python3.12 (pid 4711)",
    });
    expect(d.code).toBe("PORT_IN_USE");
    expect(d.detail).toContain("pid 4711");
  });

  it("recognises a database failure and says the data is backed up", () => {
    const d = classify(["Traceback (most recent call last):", "sqlite3.DatabaseError: database disk image is malformed"], { port: "8765" });
    expect(d.code).toBe("DATABASE");
    expect(d.remedy).toContain("backups/");
  });

  it("falls back to the last exception line", () => {
    const d = classify(["Traceback (most recent call last):", "RuntimeError: boom"], { port: "8765", exitCode: 1 });
    expect(d.code).toBe("CRASHED");
    expect(d.detail).toBe("RuntimeError: boom");
  });

  it("never shows keys", () => {
    expect(redact("key sk-ant-api03-abcdefghijklmnop rejected")).toBe("key sk-ant-… rejected");
    expect(redact('ANTHROPIC_API_KEY="abc123secret"')).toBe('ANTHROPIC_API_KEY="…"');
    expect(redact("databento db-ABCDEFGHIJKLMNOPQRSTUV")).toBe("databento db-…");
  });
});

/** A project folder whose backend/.venv/bin/python prints ``lines`` to stderr and exits 1;
 * ``pip install`` (when asked) succeeds and leaves a marker. */
function brokenProject(lines: string[], { installed = true } = {}): string {
  const root = mkdtempSync(path.join(tmpdir(), "jarvis-supervisor-"));
  const bin = path.join(root, "backend", ".venv", "bin");
  mkdirSync(bin, { recursive: true });
  const script = path.join(bin, "python");
  writeFileSync(
    script,
    `#!/bin/sh\nif [ "$2" = "pip" ]; then touch "${root}/pip-ran"; exit 0; fi\n` +
      `${lines.map((l) => `echo ${JSON.stringify(l)} 1>&2`).join("\n")}\nexit 1\n`,
  );
  chmodSync(script, 0o755);
  writeFileSync(path.join(root, "backend", "pyproject.toml"), "[project]\nname='x'\n");
  // installed = the stamp matches pyproject.toml, so only the crash itself is exercised
  const hash = createHash("sha256").update(readFileSync(path.join(root, "backend", "pyproject.toml"))).digest("hex");
  writeFileSync(path.join(root, "backend", ".venv", ".jarvis-deps"), installed ? `${hash}\n` : "an-older-version\n");
  return root;
}

describe("supervisor", () => {
  it(
    "stops restarting after repeated quick crashes and says why",
    async () => {
      const root = brokenProject([
        "Traceback (most recent call last):",
        "ModuleNotFoundError: No module named 'pyarrow'",
      ]);
      const seen: BackendStatus["state"][] = [];
      const logFile = path.join(root, "logs", "backend-console.log");
      const supervisor = new BackendSupervisor("http://127.0.0.1:8691", root, [], {
        restartDelaysMs: [10],
        logFile,
        onChange: (s) => seen.push(s.state),
      });
      delete process.env.JARVIS_PYTHON;
      await supervisor.ensureRunning();
      for (let i = 0; i < 200 && supervisor.status.state !== "crashed"; i += 1) {
        await new Promise((r) => setTimeout(r, 50));
      }
      const status = supervisor.status;
      expect(status.state).toBe("crashed");
      expect(status.attempts).toBe(MAX_QUICK_CRASHES);
      expect(status.diagnosis?.code).toBe("MISSING_DEPENDENCY");
      expect(status.diagnosis?.detail).toContain("pyarrow");
      expect(status.log.some((l) => l.includes("ModuleNotFoundError"))).toBe(true);
      expect(seen).toContain("restarting");
      expect(readFileSync(logFile, "utf8")).toContain("No module named 'pyarrow'");
      supervisor.stop();
    },
    20_000,
  );
});

describe("dependencies", () => {
  it(
    "installs changed Python packages before starting (the Dock app has no preflight)",
    async () => {
      const root = brokenProject(["RuntimeError: stop here"], { installed: false });
      const seen: BackendStatus["state"][] = [];
      const supervisor = new BackendSupervisor("http://127.0.0.1:8692", root, [], {
        restartDelaysMs: [10],
        onChange: (s) => seen.push(s.state),
      });
      delete process.env.JARVIS_PYTHON;
      await supervisor.ensureRunning();
      supervisor.stop();
      const installing = seen.indexOf("installing");
      expect(installing).toBeGreaterThanOrEqual(0);
      expect(seen.indexOf("starting", installing)).toBeGreaterThan(installing); // then started
      expect(existsSync(path.join(root, "pip-ran"))).toBe(true);
      const hash = createHash("sha256").update(readFileSync(path.join(root, "backend", "pyproject.toml"))).digest("hex");
      expect(readFileSync(path.join(root, "backend", ".venv", ".jarvis-deps"), "utf8").trim()).toBe(hash);
    },
    20_000,
  );
});

/** A stand-in backend: answers /health with its own pid on JARVIS_PORT. */
function healthyProject(): string {
  const root = mkdtempSync(path.join(tmpdir(), "jarvis-supervisor-ok-"));
  const bin = path.join(root, "backend", ".venv", "bin");
  mkdirSync(bin, { recursive: true });
  const server = path.join(root, "server.mjs");
  writeFileSync(
    server,
    `import http from "node:http";
http.createServer((req, res) => {
  res.setHeader("content-type", "application/json");
  res.end(JSON.stringify({ status: "ok", pid: process.pid, build: "unknown", system_backend: "test" }));
}).listen(Number(process.env.JARVIS_PORT), "127.0.0.1");
`,
  );
  writeFileSync(path.join(bin, "python"), `#!/bin/sh\nexec ${JSON.stringify(process.execPath)} ${JSON.stringify(server)}\n`);
  chmodSync(path.join(bin, "python"), 0o755);
  writeFileSync(path.join(root, "backend", "pyproject.toml"), "[project]\nname='x'\n");
  const hash = createHash("sha256").update(readFileSync(path.join(root, "backend", "pyproject.toml"))).digest("hex");
  writeFileSync(path.join(root, "backend", ".venv", ".jarvis-deps"), `${hash}\n`);
  return root;
}

const answers = (port: number) =>
  fetch(`http://127.0.0.1:${port}/health`, { signal: AbortSignal.timeout(500) }).then(
    (r) => r.ok,
    () => false,
  );

describe("one backend at a time", () => {
  it(
    "a restart request during a start doesn't leave a second backend behind; quit stops it",
    async () => {
      const port = 8693;
      const supervisor = new BackendSupervisor(`http://127.0.0.1:${port}`, healthyProject());
      delete process.env.JARVIS_PYTHON;
      const first = supervisor.ensureRunning();
      const again = supervisor.ensureRunning(); // shares the start in progress
      const restarted = supervisor.restart(); // waits for it, then restarts cleanly
      expect(await first).toBe("spawned");
      expect(await again).toBe("spawned");
      expect(await restarted).toBe("spawned");
      expect(supervisor.status.state).toBe("online");
      expect(await answers(port)).toBe(true);
      await supervisor.shutdown();
      for (let i = 0; i < 40 && (await answers(port)); i += 1) await new Promise((r) => setTimeout(r, 50));
      expect(await answers(port)).toBe(false); // nothing left listening
    },
    30_000,
  );
});
