/**
 * Electron main process: window, secure app:// protocol, backend supervision.
 */

import path from "node:path";
import { pathToFileURL } from "node:url";

import { BrowserWindow, app, net, protocol, shell } from "electron";

import { BackendSupervisor } from "./backend";

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

const supervisor = new BackendSupervisor(BACKEND_URL, PROJECT_ROOT);
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

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  void app.whenReady().then(async () => {
    registerAppProtocol();
    void supervisor.ensureRunning(); // the dashboard shows CONNECTING until it is up
    await createWindow();
  });

  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", () => supervisor.stop());
}
