/**
 * Supervises the Python backend.
 *
 * - A backend already answering /health that runs the same code (git commit)
 *   is reused — e.g. one started manually during development.
 * - A JARVIS backend left over from an older version is stopped and replaced,
 *   instead of the new app silently talking to old code.
 * - Otherwise the backend is started from backend/.venv and stopped again
 *   when JARVIS quits. Before that, changed Python dependencies are installed
 *   (the Dock app skips `npm start`'s preflight, so it does this itself).
 * - If the backend it started stops unexpectedly (a crash), it is started
 *   again — after 1, 3, 10 and 30 seconds. Five quick crashes in a row stop
 *   the retries: the window then shows why (the diagnosis below) instead of
 *   a restart loop. A backend that ran stably gets the retries again.
 * - Its stdout/stderr are kept (last lines + a log file) and classified into
 *   a diagnosis with the action that fixes it: missing package → repair,
 *   port in use → which program holds it, no Python → setup, …
 */

import { type ChildProcess, execFileSync, spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";

export type BackendMode = "external" | "spawned" | "unavailable";

export type SupervisorState =
  | "starting" // spawned, waiting for /health
  | "installing" // installing changed Python dependencies
  | "online" // ours, answering /health
  | "external" // someone else's backend answers (dev, or an old one we couldn't stop)
  | "restarting" // crashed, a restart is scheduled
  | "crashed" // crashed repeatedly — no more automatic restarts
  | "unavailable" // can't start at all (no Python, …)
  | "stopped";

export type BackendAction = "retry" | "restart" | "repair";

export interface Diagnosis {
  code: string;
  title: string;
  detail: string;
  remedy: string;
  actions: BackendAction[];
}

export interface BackendStatus {
  state: SupervisorState;
  url: string;
  port: string;
  python: string | null;
  build: string;
  pid: number | null;
  /** Crashes since the last stable run. */
  attempts: number;
  lastExit: { code: number | null; signal: string | null; at: number; uptimeMs: number } | null;
  nextRetryAt: number | null;
  diagnosis: Diagnosis | null;
  /** Last lines the backend printed (stdout and stderr). */
  log: string[];
  logFile: string | null;
}

interface Health {
  status: string;
  build?: string;
  pid?: number;
  system_backend?: string;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const RESTART_DELAYS_MS = [1_000, 3_000, 10_000, 30_000];
/** This many quick crashes in a row stop the automatic restarts. */
export const MAX_QUICK_CRASHES = 5;
/** A backend that ran this long before it stopped gets the quick retries again. */
const STABLE_AFTER_MS = 120_000;
const LOG_LINES = 200;
const LOG_FILE_MAX = 2_000_000;
export const short = (build: string | undefined) => (build ? build.slice(0, 7) : "pre-build-id");

/**
 * What went wrong, from what the backend printed. Pure (tested): the order matters — the
 * most specific cause wins.
 */
export function classify(
  log: string[],
  context: { port: string; portOwner?: string | null; exitCode?: number | null; python?: string | null },
): Diagnosis {
  const text = log.join("\n");
  const missing = /ModuleNotFoundError: No module named '([^']+)'/.exec(text);
  if (missing || /ImportError: /.test(text)) {
    return {
      code: "MISSING_DEPENDENCY",
      title: "A Python package of the backend is missing",
      detail: missing ? `No module named '${missing[1]}'.` : lastError(log) ?? "An import failed.",
      remedy:
        "Usually after an update whose new packages weren't installed. Repair installs the backend's " +
        "packages into backend/.venv and starts it again.",
      actions: ["repair", "restart"],
    };
  }
  if (/address already in use|Errno 48|Errno 98|error while attempting to bind/i.test(text)) {
    return {
      code: "PORT_IN_USE",
      title: `Port ${context.port} is used by another program`,
      detail: context.portOwner ? `It is held by ${context.portOwner}.` : "Another program listens on it.",
      remedy:
        "Quit that program (or an old JARVIS backend still running), then press Restart backend. " +
        "Or start JARVIS on another port with JARVIS_BACKEND_URL.",
      actions: ["restart"],
    };
  }
  if (/database disk image is malformed|sqlite3\.(DatabaseError|OperationalError)|migrat/i.test(text) && /Traceback/.test(text)) {
    return {
      code: "DATABASE",
      title: "The database couldn't be opened or migrated",
      detail: lastError(log) ?? "A database error stopped the start.",
      remedy:
        "Your data is untouched: before every migration a copy is written to backups/ next to jarvis.db. " +
        "Send the log below to support (it contains no keys) or restore the newest backup.",
      actions: ["restart"],
    };
  }
  if (/PermissionError|Operation not permitted|EACCES/i.test(text)) {
    return {
      code: "PERMISSION",
      title: "The backend may not read or write a file it needs",
      detail: lastError(log) ?? "Permission denied.",
      remedy: "Check that the JARVIS folder and ~/Library/Application Support/JARVIS belong to your user.",
      actions: ["restart"],
    };
  }
  return {
    code: "CRASHED",
    title: "The backend stopped",
    detail: lastError(log) ?? `It exited with code ${context.exitCode ?? "unknown"} without an error message.`,
    remedy: "Restart it. If it stops again, the log below says why.",
    actions: ["restart", "repair"],
  };
}

/** The last exception line of a Python traceback (or the last non-empty line). */
function lastError(log: string[]): string | null {
  const lines = log.map((l) => l.trim()).filter(Boolean);
  const error = [...lines].reverse().find((l) => /^[A-Za-z_.]*(Error|Exception)\b|^[A-Za-z_.]+Error:/.test(l));
  return error ?? lines[lines.length - 1] ?? null;
}

export class BackendSupervisor {
  private child: ChildProcess | null = null;
  /** Every backend this app started and that hasn't exited — all are stopped on quit. */
  private readonly children = new Set<ChildProcess>();
  /** One start at a time: a restart request during a start waits for it instead of racing. */
  private inflight: Promise<BackendMode> | null = null;
  private stopping = false;
  private restarts = 0;
  private startedAt = 0;
  private restartTimer: NodeJS.Timeout | null = null;
  private current: BackendStatus;
  /** Code version of this app (git commit), also reported by the backend it starts. */
  readonly build: string;

  constructor(
    private readonly url: string,
    private readonly projectRoot: string,
    private readonly extraOrigins: string[] = [],
    private readonly options: {
      onChange?: (status: BackendStatus) => void;
      logFile?: string;
      /** Waits before restarts after a crash (tests shorten them). */
      restartDelaysMs?: number[];
    } = {},
  ) {
    this.build = gitCommit(projectRoot);
    this.current = {
      state: "starting",
      url,
      port: new URL(url).port || "8765",
      python: null,
      build: this.build,
      pid: null,
      attempts: 0,
      lastExit: null,
      nextRetryAt: null,
      diagnosis: null,
      log: [],
      logFile: options.logFile ?? null,
    };
  }

  get status(): BackendStatus {
    return { ...this.current, log: [...this.current.log] };
  }

  private update(change: Partial<BackendStatus>): void {
    this.current = { ...this.current, ...change };
    this.options.onChange?.(this.status);
  }

  private record(source: "out" | "err", chunk: Buffer): void {
    const lines = chunk
      .toString()
      .split("\n")
      .map((l) => l.replace(/\r$/, ""))
      .filter((l) => l.length > 0)
      .map((l) => redact(l));
    if (lines.length === 0) return;
    this.current.log = [...this.current.log, ...lines].slice(-LOG_LINES);
    (source === "out" ? process.stdout : process.stderr).write(lines.map((l) => `[backend] ${l}\n`).join(""));
    if (this.options.logFile) {
      try {
        rotate(this.options.logFile);
        appendFileSync(this.options.logFile, lines.map((l) => `${new Date().toISOString()} ${source} ${l}\n`).join(""));
      } catch {
        // the log file is a convenience; the in-memory lines still reach the window
      }
    }
  }

  ensureRunning(): Promise<BackendMode> {
    if (!this.inflight) {
      this.inflight = this.start().finally(() => {
        this.inflight = null;
      });
    }
    return this.inflight;
  }

  private async start(): Promise<BackendMode> {
    this.stopping = false;
    const running = await this.probe();
    const ours = running?.pid ? [...this.children].find((c) => c.pid === running.pid) : undefined;
    if (ours) {
      // A backend we started earlier answers (e.g. after a restart request raced a scheduled
      // restart): adopt it instead of calling it someone else's.
      this.child = ours;
      this.update({ state: "online", pid: ours.pid ?? null, diagnosis: null });
      return "spawned";
    }
    if (running && this.isOutdated(running)) {
      console.log(
        `[jarvis] the backend on ${this.url} runs older code (${short(running.build)}, this app is ${short(this.build)}) — replacing it`,
      );
      if (!(await this.terminate(running))) {
        console.warn("[jarvis] WARNING: could not stop the old backend. Stop it manually and start JARVIS again.");
        this.update({
          state: "external",
          pid: running.pid ?? null,
          diagnosis: {
            code: "OLD_BACKEND",
            title: "An older JARVIS backend is still running",
            detail: `It runs ${short(running.build)}, this app is ${short(this.build)}, and it couldn't be stopped.`,
            remedy: "Quit JARVIS, stop that process (Activity Monitor → python), and start JARVIS again.",
            actions: ["restart"],
          },
        });
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
      this.update({ state: "external", pid: running.pid ?? null, diagnosis: null });
      return "external";
    }
    const python = this.findPython();
    if (!python) {
      console.error(
        "[jarvis] no Python environment found. Run scripts/setup.ps1 (Windows) or scripts/setup.sh, " +
          "or set JARVIS_PYTHON.",
      );
      this.update({
        state: "unavailable",
        python: null,
        diagnosis: {
          code: "NO_PYTHON",
          title: "JARVIS's Python environment is missing",
          detail: `There is no ${path.join(this.projectRoot, "backend", ".venv")}.`,
          remedy: "In Terminal, in the JARVIS folder: ./scripts/setup.sh — then press Retry.",
          actions: ["retry"],
        },
      });
      return "unavailable";
    }
    this.update({ python });
    if (!(await this.syncDependencies(python, false))) return "unavailable";
    if (this.stopping) return "unavailable"; // quit while starting: start nothing more
    return this.spawnBackend(python);
  }

  private async spawnBackend(python: string): Promise<BackendMode> {
    const port = this.current.port;
    console.log(`[jarvis] starting backend: ${python} -m jarvis (port ${port})`);
    this.update({ state: "starting", diagnosis: null, nextRetryAt: null });
    const child = spawn(python, ["-m", "jarvis"], {
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
    this.child = child;
    this.children.add(child);
    child.stdout?.on("data", (chunk: Buffer) => this.record("out", chunk));
    child.stderr?.on("data", (chunk: Buffer) => this.record("err", chunk));
    child.on("error", (error) => this.record("err", Buffer.from(`spawn failed: ${error.message}`)));
    this.startedAt = Date.now();
    this.update({ pid: child.pid ?? null });
    child.on("exit", (code, signal) => {
      console.log(`[jarvis] backend exited (${code ?? signal ?? "unknown"})`);
      this.children.delete(child);
      if (this.child !== child) return; // an extra one that lost the port: not "the" backend
      this.child = null;
      const uptimeMs = Date.now() - this.startedAt;
      this.update({ pid: null, lastExit: { code, signal: signal ?? null, at: Date.now(), uptimeMs } });
      if (!this.stopping) this.scheduleRestart(code);
    });

    const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
    const deadline = Date.now() + 60_000; // first start compiles bytecode; slow machines need time
    while (Date.now() < deadline && this.child === child && child.exitCode === null && child.signalCode === null) {
      if (await this.healthy()) {
        this.update({ state: "online", diagnosis: null });
        return "spawned";
      }
      await Promise.race([sleep(250), exited]);
    }
    if (this.child === child) {
      console.error("[jarvis] backend did not become healthy in time");
      this.update({
        diagnosis: {
          code: "SLOW_START",
          title: "The backend is taking long to start",
          detail: "It runs but hasn't answered /health within 60 seconds (a large database migration can take this long).",
          remedy: "Wait a moment — the window connects as soon as it answers. If not, Restart backend.",
          actions: ["restart"],
        },
      });
    }
    return "unavailable";
  }

  /** Install the backend's Python packages when backend/pyproject.toml changed (or always: force). */
  private async syncDependencies(python: string, force: boolean): Promise<boolean> {
    if (process.env.JARVIS_PYTHON && !force) return true; // a custom Python is managed by whoever set it
    const manifest = path.join(this.projectRoot, "backend", "pyproject.toml");
    const stamp = path.join(this.projectRoot, "backend", ".venv", ".jarvis-deps");
    if (!existsSync(manifest)) return true;
    const current = createHash("sha256").update(readFileSync(manifest)).digest("hex");
    const previous = existsSync(stamp) ? readFileSync(stamp, "utf8").trim() : "";
    if (current === previous && !force) return true;
    console.log("[jarvis] backend/pyproject.toml changed — installing the backend's Python packages…");
    this.update({ state: "installing", diagnosis: null });
    const result = await runLogged(
      python,
      ["-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-e", `${path.join(this.projectRoot, "backend")}[dev]`],
      this.projectRoot,
      (chunk) => this.record("out", chunk),
      15 * 60_000,
    );
    if (result !== 0) {
      this.update({
        state: "unavailable",
        diagnosis: {
          code: "DEPENDENCY_INSTALL_FAILED",
          title: "Installing the backend's Python packages failed",
          detail: lastError(this.current.log) ?? `pip exited with code ${result}.`,
          remedy: "Check the internet connection, then press Repair. Or in Terminal: ./scripts/setup.sh",
          actions: ["repair"],
        },
      });
      return false;
    }
    try {
      writeFileSync(stamp, `${current}\n`);
    } catch {
      // without the stamp the next start checks again — harmless
    }
    return true;
  }

  /** Restart on request: stop ours (if any) and start again; crash counters are reset. */
  async restart(): Promise<BackendMode> {
    if (this.restartTimer) clearTimeout(this.restartTimer);
    this.restartTimer = null;
    await this.inflight?.catch(() => undefined); // let a start in progress finish first
    await this.shutdown();
    this.stopping = false;
    this.restarts = 0;
    this.update({ attempts: 0, nextRetryAt: null });
    return this.ensureRunning();
  }

  /** Reinstall the backend's packages, then restart. */
  async repair(): Promise<BackendMode> {
    if (this.restartTimer) clearTimeout(this.restartTimer);
    this.restartTimer = null;
    await this.inflight?.catch(() => undefined);
    await this.shutdown();
    this.stopping = false;
    this.restarts = 0;
    const python = this.findPython();
    if (!python) return this.ensureRunning(); // reports NO_PYTHON
    this.update({ python, attempts: 0 });
    if (!(await this.syncDependencies(python, true))) return "unavailable";
    return this.ensureRunning();
  }

  stop(): void {
    this.stopping = true;
    if (this.restartTimer) clearTimeout(this.restartTimer);
    this.restartTimer = null;
    for (const child of this.children) if (!child.killed) child.kill();
    this.child = null;
    this.update({ state: "stopped", nextRetryAt: null });
  }

  private scheduleRestart(code: number | null): void {
    if (Date.now() - this.startedAt > STABLE_AFTER_MS) this.restarts = 0;
    const owner = listeningOwner(this.current.port);
    const diagnosis = classify(this.current.log.slice(-60), {
      port: this.current.port,
      portOwner: owner,
      exitCode: code,
      python: this.current.python,
    });
    this.restarts += 1;
    if (this.restarts >= MAX_QUICK_CRASHES) {
      console.error(`[jarvis] the backend crashed ${this.restarts} times in a row — not restarting it again`);
      this.update({ state: "crashed", attempts: this.restarts, nextRetryAt: null, diagnosis });
      return;
    }
    const delays = this.options.restartDelaysMs?.length ? this.options.restartDelaysMs : RESTART_DELAYS_MS;
    const delay = delays[Math.min(this.restarts - 1, delays.length - 1)] ?? 30_000;
    console.log(`[jarvis] the backend stopped unexpectedly — starting it again in ${delay / 1000} s`);
    this.update({ state: "restarting", attempts: this.restarts, nextRetryAt: Date.now() + delay, diagnosis });
    this.restartTimer = setTimeout(() => {
      this.restartTimer = null;
      // after the start that just failed has finished — never instead of a new one
      void (this.inflight ?? Promise.resolve()).finally(() => {
        if (!this.stopping) void this.ensureRunning();
      });
    }, delay);
  }

  /** Stop the backend this app started and wait until it is gone (port free). */
  async shutdown(): Promise<void> {
    const alive = [...this.children].filter((c) => c.exitCode === null && c.signalCode === null);
    if (alive.length === 0) return;
    const exited = Promise.all(alive.map((c) => new Promise<void>((resolve) => c.once("exit", () => resolve()))));
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
    const pid = running.pid ?? listeningPid(this.current.port);
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

/** Keys and tokens never reach the window or the log file. */
export function redact(line: string): string {
  return line
    .replace(/sk-ant-[A-Za-z0-9_-]{8,}/g, "sk-ant-…")
    .replace(/\b(db|sk|xi|el)-[A-Za-z0-9]{16,}\b/g, "$1-…")
    .replace(/((?:api[_-]?key|token|secret|password)["']?\s*[:=]\s*["']?)[^\s"',]+/gi, "$1…");
}

function rotate(file: string): void {
  mkdirSync(path.dirname(file), { recursive: true });
  if (existsSync(file) && statSync(file).size > LOG_FILE_MAX) renameSync(file, `${file}.1`);
}

function runLogged(
  command: string,
  args: string[],
  cwd: string,
  onOutput: (chunk: Buffer) => void,
  timeoutMs: number,
): Promise<number> {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    const timer = setTimeout(() => child.kill(), timeoutMs);
    child.stdout?.on("data", onOutput);
    child.stderr?.on("data", onOutput);
    child.on("error", (error) => {
      onOutput(Buffer.from(`${command} failed: ${error.message}`));
      clearTimeout(timer);
      resolve(-1);
    });
    child.on("exit", (code) => {
      clearTimeout(timer);
      resolve(code ?? -1);
    });
  });
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

/** "python3.12 (pid 4711)" for whoever listens on the port, if anyone. */
function listeningOwner(port: string): string | null {
  const pid = listeningPid(port);
  if (!pid) return null;
  try {
    const name = execFileSync("ps", ["-p", String(pid), "-o", "comm="], { timeout: 2000, stdio: ["ignore", "pipe", "ignore"] })
      .toString()
      .trim();
    return `${path.basename(name) || "a program"} (pid ${pid})`;
  } catch {
    return `pid ${pid}`;
  }
}
