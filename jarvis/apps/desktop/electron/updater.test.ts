/**
 * The updater against real git repositories: a bare "GitHub" remote, a
 * publisher clone that pushes new versions, and the user's checkout.
 */

import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import { type UpdateStatus, type UpdateStep, Updater } from "./updater";

process.env.GIT_AUTHOR_NAME = process.env.GIT_COMMITTER_NAME = "Test";
process.env.GIT_AUTHOR_EMAIL = process.env.GIT_COMMITTER_EMAIL = "test@example.com";

const git = (cwd: string, ...args: string[]) =>
  execFileSync("git", ["-c", "init.defaultBranch=main", ...args], { cwd, stdio: "pipe" }).toString().trim();

let base: string;
let publisher: string;
let checkout: string;

function publish(file: string, content: string, message: string): void {
  writeFileSync(path.join(publisher, file), content);
  git(publisher, "add", file);
  git(publisher, "commit", "-q", "-m", message);
  git(publisher, "push", "-q", "origin", "main");
}

function updater(steps: UpdateStep[] = []): { updater: Updater; seen: UpdateStatus[] } {
  const seen: UpdateStatus[] = [];
  return { updater: new Updater({ root: checkout, steps, onChange: (s) => seen.push(s) }), seen };
}

beforeEach(() => {
  base = mkdtempSync(path.join(tmpdir(), "jarvis-update-"));
  const remote = path.join(base, "remote.git");
  git(base, "init", "-q", "--bare", remote);
  publisher = path.join(base, "publisher");
  git(base, "clone", "-q", remote, publisher);
  git(publisher, "checkout", "-q", "-b", "main");
  publish("app.txt", "v1\n", "First version");
  checkout = path.join(base, "checkout");
  git(base, "clone", "-q", "-b", "main", remote, checkout);
});

describe("Updater", () => {
  it("reports when it is current", async () => {
    const { updater: u } = updater();
    expect((await u.check()).state).toBe("current");
  });

  it("finds an update, applies it, runs the steps and asks for a restart", async () => {
    publish("app.txt", "v2\n", "Add voice control");
    publish("app.txt", "v3\n", "Polish the core");
    const build: UpdateStep = {
      label: "Building",
      command: process.execPath,
      args: ["-e", "require('fs').writeFileSync('built', 'yes')"],
    };
    const skipped: UpdateStep = { label: "Never", command: "false", args: [], when: () => false };
    const { updater: u, seen } = updater([build, skipped]);

    const status = await u.check();
    expect(status).toMatchObject({ state: "available", behind: 2, latest: "Polish the core" });

    expect(await u.apply()).toBe(true);
    expect(readFileSync(path.join(checkout, "app.txt"), "utf8")).toBe("v3\n");
    expect(readFileSync(path.join(checkout, "built"), "utf8")).toBe("yes");
    expect(seen.map((s) => (s.state === "updating" ? s.step : s.state))).toEqual([
      "checking",
      "available",
      "Downloading",
      "Building",
      "restarting",
    ]);
  });

  it("never overwrites changes made on this computer", async () => {
    writeFileSync(path.join(checkout, "app.txt"), "my edit\n");
    publish("app.txt", "v2\n", "Change app");
    const { updater: u } = updater();
    await u.check();
    expect(await u.apply()).toBe(false);
    expect(u.status).toMatchObject({
      state: "error",
      message: "Files changed on this computer block the update.",
      canRetry: false,
    });
    expect(readFileSync(path.join(checkout, "app.txt"), "utf8")).toBe("my edit\n");
  });

  it("a lockfile rewritten by a newer npm doesn't block the update", async () => {
    publish("package-lock.json", '{"lockfileVersion": 3}\n', "Add lockfile");
    git(checkout, "pull", "-q");
    writeFileSync(path.join(checkout, "package-lock.json"), '{"lockfileVersion": 3, "x": 1}\n');
    publish("package-lock.json", '{"lockfileVersion": 3, "packages": {}}\n', "New dependency");
    const { updater: u } = updater();
    await u.check();
    expect(await u.apply()).toBe(true);
    expect(readFileSync(path.join(checkout, "package-lock.json"), "utf8")).toContain('"packages"');
  });

  it("refuses to update a copy with its own commits", async () => {
    writeFileSync(path.join(checkout, "mine.txt"), "x");
    git(checkout, "add", "mine.txt");
    git(checkout, "commit", "-q", "-m", "local work");
    publish("app.txt", "v2\n", "Change app");
    const { updater: u } = updater();
    expect(await u.check()).toMatchObject({ state: "error", canRetry: false });
  });

  it("reports a failed step and keeps the downloaded code", async () => {
    publish("app.txt", "v2\n", "Change app");
    const failing: UpdateStep = {
      label: "Building",
      command: process.execPath,
      args: ["-e", "console.error('vite: syntax error in App.tsx'); process.exit(1)"],
    };
    const { updater: u } = updater([failing]);
    await u.check();
    expect(await u.apply()).toBe(false);
    expect(u.status).toMatchObject({ state: "error", canRetry: false });
    if (u.status.state === "error") {
      expect(u.status.message).toContain("“building” failed");
      expect(u.status.detail).toContain("syntax error");
    }
  });

  it("says when it can't reach the remote, and when there is nothing to follow", async () => {
    git(checkout, "remote", "set-url", "origin", path.join(base, "missing.git"));
    const { updater: offline } = updater();
    expect(await offline.check()).toMatchObject({ state: "error", canRetry: true });

    git(checkout, "checkout", "-q", "-b", "experiment");
    const { updater: detached } = updater();
    expect((await detached.check()).state).toBe("off");
    expect(existsSync(path.join(checkout, "built"))).toBe(false);
  });
});
