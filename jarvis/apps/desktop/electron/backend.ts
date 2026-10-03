/**
 * Supervises the Python backend. If a backend is already answering /health
 * (e.g. started manually during development) it is used as-is; otherwise the
 * backend is started from backend/.venv and stopped again when JARVIS quits.
 */

import { type ChildProcess, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

export type BackendMode = "external" | "spawned" | "unavailable";

export class BackendSupervisor {
  private child: ChildProcess | null = null;

  constructor(
    private readonly url: string,
    private readonly projectRoot: string,
    private readonly extraOrigins: string[] = [],
  ) {}

  async ensureRunning(): Promise<BackendMode> {
    const running = await this.health();
    if (running) {
      console.log(`[jarvis] using running backend at ${this.url} (pid ${running.pid}, ${running.system_backend})`);
      const realPlatform = process.platform === "darwin" || process.platform === "win32";
      if (realPlatform && running.system_backend === "simulated") {
        console.warn(
          `[jarvis] WARNING: that backend is SIMULATED — probably left over from an older run. ` +
            `Quit JARVIS, stop it (${process.platform === "win32" ? `taskkill /PID ${running.pid} /F` : `kill ${running.pid}`}) and start again.`,
        );
      }
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
        PYTHONUNBUFFERED: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    this.child.stdout?.on("data", (chunk: Buffer) => process.stdout.write(`[backend] ${chunk}`));
    this.child.stderr?.on("data", (chunk: Buffer) => process.stderr.write(`[backend] ${chunk}`));
    this.child.on("exit", (code) => {
      console.log(`[jarvis] backend exited (${code ?? "signal"})`);
      this.child = null;
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
    if (this.child && !this.child.killed) {
      this.child.kill();
      this.child = null;
    }
  }

  private async healthy(): Promise<boolean> {
    return (await this.health()) !== null;
  }

  private async health(): Promise<{ pid: number; system_backend: string } | null> {
    try {
      const response = await fetch(`${this.url}/health`, { signal: AbortSignal.timeout(800) });
      return response.ok ? ((await response.json()) as { pid: number; system_backend: string }) : null;
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
