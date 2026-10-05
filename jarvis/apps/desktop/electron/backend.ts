/**
 * Supervises the Python backend.
 *
 * - A backend already answering /health that runs the same code (git commit)
 *   is reused — e.g. one started manually during development.
 * - A JARVIS backend left over from an older version is stopped and replaced,
 *   instead of the new app silently talking to old code.
 * - Otherwise the backend is started from backend/.venv and stopped again
 *   when JARVIS quits.
 * - If the backend it started stops unexpectedly (a crash), it is started
 *   again — after 1, 3, 10, 30, then every 60 seconds. JARVIS runs in the
 *   background for days; it must not stay dead after one failure.
 */

import { type ChildProcess, execFileSync, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

export type BackendMode = "external" | "spawned" | "unavailable";

interface Health {
  status: string;
  build?: string;
  pid?: number;
  system_backend?: string;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const RESTART_DELAYS_MS = [1_000, 3_000, 10_000, 30_000, 60_000];
/** A backend that ran this long before it stopped gets the quick retries again. */
const STABLE_AFTER_MS = 120_000;
export const short = (build: string | undefined) => (build ? build.slice(0, 7) : "pre-build-id");

export class BackendSupervisor {
  private child: ChildProcess | null = null;
  private stopping = false;
  private restarts = 0;
  private startedAt = 0;
  private restartTimer: NodeJS.Timeout | null = null;
  /** Code version of this app (git commit), also reported by the backend it starts. */
  readonly build: string;

  constructor(
    private readonly url: string,
    private readonly projectRoot: string,
    private readonly extraOrigins: string[] = [],
  ) {
    this.build = gitCommit(projectRoot);
  }

  async ensureRunning(): Promise<BackendMode> {
    const running = await this.probe();
    if (running && this.isOutdated(running)) {
      console.log(
        `[jarvis] the backend on ${this.url} runs older code (${short(running.build)}, this app is ${short(this.build)}) — replacing it`,
      );
      if (!(await this.terminate(running))) {
        console.warn("[jarvis] WARNING: could not stop the old backend. Stop it manually and start JARVIS again.");
        return "external";
      }
    } else if (running) {
      console.log(`[jarvis] using running backend at ${this.url} (pid ${running.pid}, ${running.system_backend})`);
      if (this.extraOrigins.length > 0) {
        console.log(
          `[jarvis] if the dashboard stays on CONNECTING, that backend does not allow ${this.extraOrigins.join(", ")}` +
            " — stop it and restart `npm run dev` so JARVIS starts its own.",
        );
      }
      return "external";
    }
    const python = this.findPython();
    if (!python) {
      console.error(
        "[jarvis] no Python environment found. Run scripts/setup.ps1 (Windows) or scripts/setup.sh, " +
          "or set JARVIS_PYTHON.",
      );
      return "unavailable";
    }
    const port = new URL(this.url).port || "8765";
    console.log(`[jarvis] starting backend: ${python} -m jarvis (port ${port})`);
    this.child = spawn(python, ["-m", "jarvis"], {
      cwd: path.join(this.projectRoot, "backend"),
      env: {
        ...process.env,
        JARVIS_ROOT: this.projectRoot,
        JARVIS_PORT: port,
        JARVIS_EXTRA_ORIGINS: this.extraOrigins.join(","),
        JARVIS_UI_PID: String(process.pid), // lets JARVIS recognise its own window
        PYTHONUNBUFFERED: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    this.child.stdout?.on("data", (chunk: Buffer) => process.stdout.write(`[backend] ${chunk}`));
    this.child.stderr?.on("data", (chunk: Buffer) => process.stderr.write(`[backend] ${chunk}`));
    this.startedAt = Date.now();
    this.child.on("exit", (code, signal) => {
      console.log(`[jarvis] backend exited (${code ?? signal ?? "unknown"})`);
      this.child = null;
      if (!this.stopping) this.scheduleRestart();
    });

    const deadline = Date.now() + 60_000; // first start compiles bytecode; slow machines need time
    while (Date.now() < deadline && this.child) {
      if (await this.healthy()) return "spawned";
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    console.error("[jarvis] backend did not become healthy in time");
    return "unavailable";
  }

  stop(): void {
    this.stopping = true;
    if (this.restartTimer) clearTimeout(this.restartTimer);
    this.restartTimer = null;
    if (this.child && !this.child.killed) {
      this.child.kill();
      this.child = null;
    }
  }

  private scheduleRestart(): void {
    if (Date.now() - this.startedAt > STABLE_AFTER_MS) this.restarts = 0;
    const delay = RESTART_DELAYS_MS[Math.min(this.restarts, RESTART_DELAYS_MS.length - 1)] ?? 60_000;
    this.restarts += 1;
    console.log(`[jarvis] the backend stopped unexpectedly — starting it again in ${delay / 1000} s`);
    this.restartTimer = setTimeout(() => {
      this.restartTimer = null;
      if (!this.stopping) void this.ensureRunning();
    }, delay);
  }

  /** Stop the backend this app started and wait until it is gone (port free). */
  async shutdown(): Promise<void> {
    const child = this.child;
    if (!child || child.exitCode !== null || child.signalCode !== null) return;
    const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
    this.stop();
    await Promise.race([exited, sleep(5000)]);
  }

  /** Build of the backend answering on our URL; null when none answers. */
  async runningBuild(): Promise<string | null> {
    const running = await this.health();
    return running ? (running.build ?? "pre-build-id") : null;
  }

  private isOutdated(running: Health): boolean {
    if (this.build === "unknown") return false; // not a git checkout: can't tell
    if (running.build === undefined) return true; // predates build ids
    return running.build !== "unknown" && running.build !== this.build;
  }

  /** Stop an old JARVIS backend (identified by its /health answer). */
  private async terminate(running: Health): Promise<boolean> {
    const pid = running.pid ?? listeningPid(new URL(this.url).port || "8765");
    if (!pid) return false;
    for (const signal of ["SIGTERM", "SIGKILL"] as const) {
      try {
        process.kill(pid, signal);
      } catch {
        // already gone, or not ours to kill
      }
      for (let i = 0; i < 32; i += 1) {
        if (!(await this.healthy())) return true;
        await sleep(250);
      }
    }
    return false;
  }

  private async healthy(): Promise<boolean> {
    return (await this.health()) !== null;
  }

  /**
   * Is a backend already running? Asked patiently: right at app start the main
   * process is busy, and one short request can time out although a backend
   * answers — then a second one would be started and fail on the busy port.
   */
  private async probe(): Promise<Health | null> {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const running = await this.health(2500);
      if (running) return running;
      await sleep(200);
    }
    return null;
  }

  private async health(timeoutMs = 800): Promise<Health | null> {
    try {
      const response = await fetch(`${this.url}/health`, { signal: AbortSignal.timeout(timeoutMs) });
      if (!response.ok) return null;
      const body = (await response.json()) as Health;
      return body.status === "ok" ? body : null;
    } catch {
      return null;
    }
  }

  private findPython(): string | null {
    const candidates = [
      process.env.JARVIS_PYTHON,
      path.join(this.projectRoot, "backend", ".venv", "Scripts", "python.exe"),
      path.join(this.projectRoot, "backend", ".venv", "bin", "python"),
    ];
    return candidates.find((candidate): candidate is string => !!candidate && existsSync(candidate)) ?? null;
  }
}

function gitCommit(cwd: string): string {
  const override = process.env.JARVIS_BUILD?.trim(); // tests; the backend honours it too
  if (override) return override;
  try {
    const commit = execFileSync("git", ["rev-parse", "HEAD"], {
      cwd,
      timeout: 2000,
      stdio: ["ignore", "pipe", "ignore"],
    })
      .toString()
      .trim();
    return commit || "unknown";
  } catch {
    return "unknown";
  }
}

/** PID listening on a local TCP port (macOS/Linux; old backends didn't report theirs). */
function listeningPid(port: string): number | null {
  if (process.platform === "win32") return null;
  try {
    const output = execFileSync("lsof", ["-ti", `tcp:${port}`, "-sTCP:LISTEN"], {
      timeout: 2000,
      stdio: ["ignore", "pipe", "ignore"],
    }).toString();
    const pid = Number.parseInt(output.trim().split("\n")[0] ?? "", 10);
    return Number.isFinite(pid) ? pid : null;
  } catch {
    return null;
  }
}
