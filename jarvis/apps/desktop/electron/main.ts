/**
 * Electron main process: window, secure app:// protocol, backend supervision.
 */

import path from "node:path";
import { pathToFileURL } from "node:url";

import { BrowserWindow, app, net, protocol, shell } from "electron";

import { BackendSupervisor, short } from "./backend";

const BACKEND_URL = process.env.JARVIS_BACKEND_URL ?? "http://127.0.0.1:8765";
const DEV_RENDERER_URL = process.env.JARVIS_RENDERER_URL; // set by `npm run dev`
const RENDERER_DIR = path.join(__dirname, "..", "dist");
const PROJECT_ROOT = path.resolve(__dirname, "..", "..", "..");
const APP_ORIGIN = "app://jarvis";
const BACKGROUND = "#07080a";

protocol.registerSchemesAsPrivileged([
  {
    scheme: "app",
    privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true },
  },
]);

// The dev server may run on any free port; the backend must accept its origin.
const supervisor = new BackendSupervisor(
  BACKEND_URL,
  PROJECT_ROOT,
  DEV_RENDERER_URL ? [new URL(DEV_RENDERER_URL).origin] : [],
);
let mainWindow: BrowserWindow | null = null;

function contentSecurityPolicy(): string {
  const backend = new URL(BACKEND_URL);
  const ws = `ws://${backend.host}`;
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self' data:",
    "img-src 'self' data:",
    `connect-src 'self' ${backend.origin} ${ws}`,
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
  ].join("; ");
}

function registerAppProtocol(): void {
  protocol.handle("app", async (request) => {
    const { pathname } = new URL(request.url);
    const relative = decodeURIComponent(pathname === "/" ? "/index.html" : pathname);
    const filePath = path.normalize(path.join(RENDERER_DIR, relative));
    if (!filePath.startsWith(RENDERER_DIR)) {
      return new Response("Not found", { status: 404 });
    }
    const response = await net.fetch(pathToFileURL(filePath).toString());
    if (!filePath.endsWith(".html")) return response;
    const headers = new Headers(response.headers);
    headers.set("Content-Security-Policy", contentSecurityPolicy());
    return new Response(response.body, { status: response.status, headers });
  });
}

async function createWindow(): Promise<void> {
  const isWindows = process.platform === "win32";
  const isMac = process.platform === "darwin";
  mainWindow = new BrowserWindow({
    width: 1480,
    height: 920,
    minWidth: 1120,
    minHeight: 720,
    title: "JARVIS",
    show: false,
    backgroundColor: BACKGROUND,
    titleBarStyle: isWindows ? "hidden" : isMac ? "hiddenInset" : "default",
    titleBarOverlay: isWindows ? { color: BACKGROUND, symbolColor: "#8b949e", height: 44 } : undefined,
    trafficLightPosition: isMac ? { x: 18, y: 15 } : undefined, // centred in the 44 px top bar
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      spellcheck: false,
      additionalArguments: [`--jarvis-backend=${BACKEND_URL}`],
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    const allowed = DEV_RENDERER_URL ?? APP_ORIGIN;
    if (!url.startsWith(allowed)) event.preventDefault();
  });

  await mainWindow.loadURL(DEV_RENDERER_URL ?? `${APP_ORIGIN}/index.html`);
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
/** How long a newly started version waits for the running one to hand over. */
const HANDOVER_TIMEOUT_MS = 15_000;
let handingOver = false;

/**
 * One JARVIS at a time. Starting the same version again brings the open window
 * to the front; starting a different version (after `git pull`) makes the
 * running one stop its backend and quit, so the new code takes over.
 */
async function acquireInstanceLock(): Promise<boolean> {
  const data = { build: supervisor.build };
  if (app.requestSingleInstanceLock(data)) return true;
  if ((await supervisor.runningBuild()) === supervisor.build) {
    console.log("[jarvis] this version of JARVIS is already running — switched to the open window.");
    return false;
  }
  console.log(`[jarvis] another JARVIS version is running — taking over with ${short(supervisor.build)}…`);
  const deadline = Date.now() + HANDOVER_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await sleep(250);
    if (app.requestSingleInstanceLock(data)) return true;
  }
  console.log(
    "[jarvis] the running JARVIS did not hand over (it predates automatic takeover). " +
      "Quit it with ⌘Q / Alt+F4, then start again.",
  );
  return false;
}

function onSecondInstance(additionalData: unknown): void {
  const build = (additionalData as { build?: unknown } | null)?.build;
  if (typeof build === "string" && build !== supervisor.build) {
    if (handingOver) return;
    handingOver = true;
    console.log(`[jarvis] JARVIS ${short(build)} was started — handing over (this one is ${short(supervisor.build)}).`);
    void supervisor.shutdown().then(() => app.quit());
    return;
  }
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
}

void (async () => {
  if (!(await acquireInstanceLock())) {
    app.quit();
    return;
  }
  app.on("second-instance", (_event, _argv, _cwd, additionalData) => onSecondInstance(additionalData));
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", () => supervisor.stop());

  await app.whenReady();
  registerAppProtocol();
  void supervisor.ensureRunning(); // the dashboard shows CONNECTING until it is up
  await createWindow();
})();
