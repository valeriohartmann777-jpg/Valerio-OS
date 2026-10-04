/**
 * In-app updates for a git checkout of JARVIS.
 *
 * check: fetch the tracked branch and count the commits we are behind.
 * apply: fast-forward to it (never a merge, never discarding local changes),
 *        then run the configured steps (install changed packages, build). The
 *        caller restarts JARVIS afterwards.
 *
 * No Electron imports: the logic is tested against real git repositories.
 */

import { spawn } from "node:child_process";

export type UpdateStatus =
  | { state: "off"; reason: string }
  | { state: "checking" }
  | { state: "current"; checkedAt: number }
  | { state: "available"; behind: number; latest: string; checkedAt: number }
  | { state: "updating"; step: string }
  | { state: "restarting" }
  | { state: "error"; message: string; detail?: string; canRetry: boolean };

export interface CommandResult {
  code: number;
  output: string;
}

export type Runner = (command: string, args: string[], cwd: string) => Promise<CommandResult>;

const MAX_OUTPUT = 20_000;

export const runCommand: Runner = (command, args, cwd) =>
  new Promise((resolve) => {
    let output = "";
    const append = (chunk: Buffer) => {
      output = (output + chunk.toString()).slice(-MAX_OUTPUT);
    };
    const child = spawn(command, args, {
      cwd,
      // Never wait for a password prompt nobody can see.
      env: { ...process.env, GIT_TERMINAL_PROMPT: "0" },
      stdio: ["ignore", "pipe", "pipe"],
      shell: process.platform === "win32" && command === "npm",
      windowsHide: true,
    });
    child.stdout.on("data", append);
    child.stderr.on("data", append);
    child.on("error", (error) => resolve({ code: -1, output: `${output}${String(error)}` }));
    child.on("close", (code) => resolve({ code: code ?? -1, output }));
  });

export interface UpdateStep {
  label: string;
  command: string;
  args: string[];
  /** Run only when this returns true (evaluated right before the step). */
  when?: () => boolean;
}

export interface UpdaterOptions {
  /** A directory inside the git checkout. */
  root: string;
  /** Steps after the code was updated: install packages, build, … */
  steps: UpdateStep[];
  onChange: (status: UpdateStatus) => void;
  run?: Runner;
}

const tail = (text: string, lines = 12) => text.trim().split("\n").slice(-lines).join("\n");

/**
 * Generated files that package managers may rewrite locally (a newer npm on
 * the Mac reformats the lockfile). Reset before updating instead of letting
 * them block it; the install step then reinstalls from the updated version.
 */
const REGENERATED = new Set(["package-lock.json"]);

export class Updater {
  private current: UpdateStatus = { state: "checking" };
  private readonly run: Runner;

  constructor(private readonly options: UpdaterOptions) {
    this.run = options.run ?? runCommand;
  }

  get status(): UpdateStatus {
    return this.current;
  }

  private set(status: UpdateStatus): UpdateStatus {
    this.current = status;
    this.options.onChange(status);
    return status;
  }

  private git(...args: string[]): Promise<CommandResult> {
    return this.run("git", args, this.options.root);
  }

  async check(): Promise<UpdateStatus> {
    if (this.current.state === "updating" || this.current.state === "restarting") return this.current;
    this.set({ state: "checking" });
    const upstream = await this.git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}");
    if (upstream.code !== 0) {
      return this.set({ state: "off", reason: "This copy of JARVIS doesn't follow a branch to update from." });
    }
    const [remote = "origin", ...branch] = upstream.output.trim().split("/");
    const fetched = await this.git("fetch", "--quiet", remote, branch.join("/"));
    if (fetched.code !== 0) {
      return this.set({
        state: "error",
        message: "Couldn't check for updates — is the computer online?",
        detail: tail(fetched.output),
        canRetry: true,
      });
    }
    const counts = await this.git("rev-list", "--left-right", "--count", "HEAD...@{u}");
    const [ahead, behind] = counts.output.trim().split(/\s+/).map(Number);
    const checkedAt = Date.now();
    if (!behind) return this.set({ state: "current", checkedAt });
    if (ahead) {
      return this.set({
        state: "error",
        message: "This copy has its own changes, so it can't update automatically.",
        detail: `${ahead} local commit(s) that the update doesn't contain.`,
        canRetry: false,
      });
    }
    const latest = await this.git("log", "-1", "--format=%s", "@{u}");
    return this.set({ state: "available", behind, latest: latest.output.trim(), checkedAt });
  }

  /** Update the checkout and run the steps. Resolves true when a restart is due. */
  async apply(): Promise<boolean> {
    if (this.current.state !== "available") return false;
    this.set({ state: "updating", step: "Downloading" });
    const changed = await this.git("diff", "--name-only", "--relative", "HEAD");
    const regenerated = changed.output.split("\n").filter((file) => REGENERATED.has(file.trim()));
    if (regenerated.length > 0) await this.git("checkout", "--", ...regenerated);
    const merged = await this.git("merge", "--ff-only", "@{u}");
    if (merged.code !== 0) {
      const blocked = /would be overwritten|untracked working tree files/.test(merged.output);
      this.set({
        state: "error",
        message: blocked
          ? "Files changed on this computer block the update."
          : "The update couldn't be downloaded.",
        detail: tail(merged.output),
        canRetry: !blocked,
      });
      return false;
    }
    for (const step of this.options.steps) {
      if (step.when && !step.when()) continue;
      this.set({ state: "updating", step: step.label });
      const result = await this.run(step.command, step.args, this.options.root);
      if (result.code !== 0) {
        this.set({
          state: "error",
          message: `The update was downloaded, but “${step.label.toLowerCase()}” failed. JARVIS keeps running the current version.`,
          detail: tail(result.output),
          canRetry: false,
        });
        return false;
      }
    }
    this.set({ state: "restarting" });
    return true;
  }
}
