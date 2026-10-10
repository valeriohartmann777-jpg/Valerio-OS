/**
 * Electron main process: window, secure app:// protocol, backend supervision,
 * and running in the background (menu bar / tray, start at login).
 */

import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import {
  BrowserWindow,
  type IpcMainInvokeEvent,
  Menu,
  type MenuItemConstructorOptions,
  Notification,
  Tray,
  app,
  ipcMain,
  nativeImage,
  net,
  powerSaveBlocker,
  protocol,
  shell,
} from "electron";

import { BackendSupervisor, short } from "./backend";
import { type UpdateStatus, Updater } from "./updater";

const BACKEND_URL = process.env.JARVIS_BACKEND_URL ?? "http://127.0.0.1:8765";
const DEV_RENDERER_URL = process.env.JARVIS_RENDERER_URL; // set by `npm run dev`
const RENDERER_DIR = path.join(__dirname, "..", "dist");
const PROJECT_ROOT = path.resolve(__dirname, "..", "..", "..");
const APP_ORIGIN = "app://jarvis";
const BACKGROUND = "#07080a";
const ASSETS = path.join(__dirname, "..", "assets");
const ICON = path.join(ASSETS, "icon.png");

/** dev: `npm run dev` · app: the installed JARVIS.app · start: `npm start`. */
type LaunchMode = "dev" | "app" | "start";
const MODE: LaunchMode = DEV_RENDERER_URL ? "dev" : process.env.JARVIS_APP === "1" ? "app" : "start";

/**
 * Closing the window keeps JARVIS running — learning, model training and the
 * morning briefing go on; it lives in the menu bar (tray on Windows) until it
 * is quit. Linux has no reliable tray, so there closing quits (unless
 * JARVIS_BACKGROUND=on); JARVIS_BACKGROUND=off turns it off everywhere.
 */
const BACKGROUND_MODE =
  process.env.JARVIS_BACKGROUND === "on" || (process.env.JARVIS_BACKGROUND !== "off" && process.platform !== "linux");

// One settings folder — and so one single-instance lock — for every way JARVIS
// starts (the Mac app's loader has a different package location).
app.setPath("userData", path.join(app.getPath("appData"), "@jarvis", "desktop"));
app.setName("JARVIS");

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
let tray: Tray | null = null;
/** Set once JARVIS is really quitting: then closing the window may close it. */
let quitting = false;

function contentSecurityPolicy(): string {
  const backend = new URL(BACKEND_URL);
  const ws = `ws://${backend.host}`;
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self' data:",
    // Keyframes and the owner's own uploaded video are served by the local backend only.
    `img-src 'self' data: ${backend.origin}`,
    `media-src ${backend.origin}`,
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
    icon: isMac ? undefined : ICON, // macOS takes the app bundle's (or the Dock's) icon
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

  mainWindow.once("ready-to-show", () => {
    if (!startedHidden) mainWindow?.show();
  });
  mainWindow.on("close", (event) => {
    if (quitting || !BACKGROUND_MODE) return;
    event.preventDefault(); // keep running: hide instead
    mainWindow?.hide();
    backgroundHint();
  });
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

// --- running in the background ------------------------------------------------------

/**
 * Started by macOS at login: stay in the menu bar instead of opening the window.
 * Only older macOS reports this (wasOpenedAtLogin); on macOS 13+ the window
 * simply opens at login.
 */
let startedHidden = false;

interface BackgroundState {
  loginOffered?: boolean; // start-at-login was switched on once (the user may turn it off)
  hintShown?: boolean; // the "still running" notification was shown once
}

function backgroundFile(): string {
  return path.join(app.getPath("userData"), "background.json");
}

function readBackgroundState(): BackgroundState {
  try {
    return JSON.parse(readFileSync(backgroundFile(), "utf8")) as BackgroundState;
  } catch {
    return {};
  }
}

function writeBackgroundState(update: BackgroundState): void {
  try {
    writeFileSync(backgroundFile(), `${JSON.stringify({ ...readBackgroundState(), ...update })}\n`);
  } catch {
    // a missing note only means a hint is shown again
  }
}

export interface LoginItem {
  supported: boolean;
  enabled: boolean;
  /** macOS 13+: registered, but the user still has to allow it in System Settings. */
  needsApproval: boolean;
}

/** Only the installed Mac app can start at login (a terminal JARVIS is a checkout). */
function loginItemSupported(): boolean {
  return MODE === "app" && process.platform === "darwin";
}

function loginItem(): LoginItem {
  if (!loginItemSupported()) return { supported: false, enabled: false, needsApproval: false };
  const settings = app.getLoginItemSettings() as { openAtLogin: boolean; status?: string };
  return { supported: true, enabled: settings.openAtLogin, needsApproval: settings.status === "requires-approval" };
}

function setStartAtLogin(enabled: boolean): LoginItem {
  if (loginItemSupported()) app.setLoginItemSettings({ openAtLogin: enabled });
  refreshTray();
  return loginItem();
}

function showWindow(): void {
  if (!mainWindow) {
    startedHidden = false;
    void createWindow();
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function quit(): void {
  quitting = true;
  app.quit();
}

/** Once: tell the user that closing the window didn't stop JARVIS. */
function backgroundHint(): void {
  if (readBackgroundState().hintShown || !Notification.isSupported()) return;
  writeBackgroundState({ hintShown: true });
  const where = process.platform === "darwin" ? "the menu bar" : "the tray";
  const quitKey = process.platform === "darwin" ? "⌘Q" : "Quit in the tray menu";
  new Notification({
    title: "JARVIS keeps running",
    body: `Learning, model training and the morning briefing go on. Open JARVIS from ${where}; quit with ${quitKey}.`,
    silent: true,
  }).show();
}

function createTray(): void {
  try {
    const image =
      process.platform === "darwin"
        ? nativeImage.createFromPath(path.join(ASSETS, "trayTemplate.png")) // + @2x, tinted by macOS
        : nativeImage.createFromPath(ICON).resize({ width: 16, height: 16 });
    tray = new Tray(image);
    tray.setToolTip("JARVIS");
    if (process.platform !== "darwin") tray.on("click", showWindow);
    refreshTray();
  } catch (error) {
    console.warn(`[jarvis] no tray icon: ${String(error)}`);
  }
}

function refreshTray(): void {
  if (!tray) return;
  const login = loginItem();
  const items: MenuItemConstructorOptions[] = [
    { label: "JARVIS is running", enabled: false },
    { label: "Open JARVIS", click: showWindow },
    { type: "separator" },
  ];
  if (login.supported) {
    items.push(
      {
        label: login.needsApproval ? "Start at Login (allow in System Settings)" : "Start at Login",
        type: "checkbox",
        checked: login.enabled,
        click: (item) => setStartAtLogin(item.checked),
      },
      { type: "separator" },
    );
  }
  items.push({ label: "Quit JARVIS", click: quit });
  tray.setContextMenu(Menu.buildFromTemplate(items));
}

function startBackgroundMode(): void {
  if (!BACKGROUND_MODE) return;
  createTray();
  // Hidden apps get "App Nap"-throttled on macOS; JARVIS has work to do in the background.
  powerSaveBlocker.start("prevent-app-suspension");
  app.on("activate", showWindow); // Dock icon clicked
  if (loginItemSupported()) {
    // Asked for an assistant that keeps running: on by default, once — Settings can turn it off.
    if (!readBackgroundState().loginOffered) {
      setStartAtLogin(true);
      writeBackgroundState({ loginOffered: true });
    }
    startedHidden = (app.getLoginItemSettings() as { wasOpenedAtLogin?: boolean }).wasOpenedAtLogin === true;
  }
}

function registerAppIpc(): void {
  ipcMain.handle("jarvis:app:background", () => ({ keepsRunning: BACKGROUND_MODE, login: loginItem() }));
  ipcMain.handle("jarvis:app:login", (event, enabled: unknown) => {
    if (!trusted(event) || typeof enabled !== "boolean") return loginItem();
    return setStartAtLogin(enabled);
  });
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
/** How long a newly started version waits for the running one to hand over. */
const HANDOVER_TIMEOUT_MS = 15_000;
let handingOver = false;

/**
 * One JARVIS at a time. Starting the same version again brings the open window
 * to the front; starting a different version (after `git pull`) makes the
 * running one stop its backend and quit, so the new code takes over. The Mac
 * app also takes over from a JARVIS started in the terminal.
 */
async function acquireInstanceLock(): Promise<boolean> {
  const data = { build: supervisor.build, mode: MODE };
  if (app.requestSingleInstanceLock(data)) return true;
  if (MODE !== "app" && (await supervisor.runningBuild()) === supervisor.build) {
    console.log("[jarvis] this version of JARVIS is already running — switched to the open window.");
    return false;
  }
  console.log(`[jarvis] another JARVIS is running — taking over with ${short(supervisor.build)}…`);
  const deadline = Date.now() + HANDOVER_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await sleep(250);
    if (app.requestSingleInstanceLock(data)) return true;
  }
  console.log(
    "[jarvis] the running JARVIS did not hand over (same version, or it predates automatic " +
      "takeover). Quit it with ⌘Q / Alt+F4, then start again.",
  );
  return false;
}

function onSecondInstance(additionalData: unknown): void {
  const incoming = additionalData as { build?: unknown; mode?: unknown } | null;
  const newerCode = typeof incoming?.build === "string" && incoming.build !== supervisor.build;
  const appReplacesTerminal = incoming?.mode === "app" && MODE !== "app";
  if (newerCode || appReplacesTerminal) {
    if (handingOver) return;
    handingOver = true;
    const why = newerCode ? `JARVIS ${short(String(incoming?.build))} was started` : "the JARVIS app was started";
    console.log(`[jarvis] ${why} — handing over (this one is ${short(supervisor.build)}).`);
    void supervisor.shutdown().then(() => app.quit());
    return;
  }
  showWindow();
}

// --- updates ------------------------------------------------------------------------

let updater: Updater | null = null;
let updateStatus: UpdateStatus = { state: "off", reason: "" };

/** The installed app bundle's Info.plist (app mode on macOS only). */
function bundlePlist(): string {
  const file = path.join(process.resourcesPath, "..", "Info.plist");
  try {
    const text = readFileSync(file, "utf8");
    if (!text.startsWith("bplist")) return text;
    // Binary plist: let macOS convert it.
    const xml = spawnSync("plutil", ["-convert", "xml1", "-o", "-", file], { encoding: "utf8" });
    return xml.status === 0 ? xml.stdout : "";
  } catch {
    return "";
  }
}

function plistString(plist: string, key: string): string | null {
  const match = new RegExp(`<key>${key}</key>\\s*<string>([^<]*)</string>`).exec(plist);
  return match ? (match[1] ?? "") : null;
}

/** The bundle predates what this checkout's installer would build. */
function bundleOutdated(): boolean {
  if (MODE !== "app" || process.platform !== "darwin") return false;
  try {
    const expected = readFileSync(path.join(PROJECT_ROOT, "scripts", "mac-app", "VERSION"), "utf8").trim();
    return plistString(bundlePlist(), "JARVISBundleVersion") !== expected;
  } catch {
    return false;
  }
}

/**
 * macOS kills a process that opens the microphone when the responsible app has
 * no usage text — so voice may only use the mic once the bundle carries it.
 */
function microphoneAllowed(): boolean {
  if (MODE !== "app" || process.platform !== "darwin") return true;
  return plistString(bundlePlist(), "NSMicrophoneUsageDescription") !== null;
}

/** Rebuild the app bundle in place (new permission texts, new Electron) and restart. */
function reinstallBundle(): boolean {
  console.log("[jarvis] the JARVIS app bundle is older than this version — updating it");
  const result = spawnSync("bash", ["scripts/install-mac-app.sh", "--skip-build", "--no-open"], {
    cwd: PROJECT_ROOT,
    stdio: "inherit",
    timeout: 180_000,
  });
  if (result.status !== 0) {
    console.error("[jarvis] updating the app bundle failed; voice stays off until it succeeds");
    return false;
  }
  return true;
}

function electronChanged(): boolean {
  try {
    const manifest = path.join(PROJECT_ROOT, "node_modules", "electron", "package.json");
    return JSON.parse(readFileSync(manifest, "utf8")).version !== process.versions.electron;
  } catch {
    return false;
  }
}

function startUpdater(): void {
  if (process.env.JARVIS_UPDATES === "off") {
    updateStatus = { state: "off", reason: "Updates are turned off (JARVIS_UPDATES=off)." };
    return;
  }
  if (MODE === "dev") {
    updateStatus = { state: "off", reason: "Started with npm run dev — update with git pull." };
    return;
  }
  updater = new Updater({
    root: PROJECT_ROOT,
    onChange: (status) => {
      updateStatus = status;
      mainWindow?.webContents.send("jarvis:update:status", status);
    },
    steps: [
      { label: "Installing packages", command: "node", args: ["scripts/preflight.mjs"] },
      { label: "Building", command: "node", args: ["scripts/build-app.mjs"] },
      {
        label: "Updating the app",
        command: "bash",
        args: ["scripts/install-mac-app.sh", "--skip-build", "--no-open"],
        when: () => MODE === "app" && (electronChanged() || bundleOutdated()),
      },
    ],
  });
  updateStatus = updater.status;
  setTimeout(() => void updater?.check(), 5_000);
  setInterval(() => void updater?.check(), 60 * 60_000);
}

/** Only our own window may drive updates. */
function trusted(event: IpcMainInvokeEvent): boolean {
  const origin = event.senderFrame?.url ?? "";
  return origin.startsWith(DEV_RENDERER_URL ?? APP_ORIGIN);
}

/** Stop the backend, then start this app again on the new code. */
async function restart(): Promise<void> {
  await supervisor.shutdown();
  app.relaunch();
  app.exit(0);
}

function registerUpdateIpc(): void {
  ipcMain.handle("jarvis:update:status", () => updateStatus);
  ipcMain.handle("jarvis:update:check", async (event) => {
    if (!trusted(event) || !updater) return updateStatus;
    return updater.check();
  });
  ipcMain.handle("jarvis:update:apply", async (event) => {
    if (!trusted(event) || !updater) return updateStatus;
    if (await updater.apply()) {
      console.log("[jarvis] update installed — restarting");
      setTimeout(() => void restart(), 100); // answer the renderer first
    }
    return updater.status;
  });
}

void (async () => {
  if (!(await acquireInstanceLock())) {
    app.quit();
    return;
  }
  app.on("second-instance", (_event, _argv, _cwd, additionalData) => onSecondInstance(additionalData));
  app.on("window-all-closed", () => {
    if (!BACKGROUND_MODE) app.quit();
  });
  app.on("before-quit", () => {
    quitting = true;
    supervisor.stop();
  });

  await app.whenReady();
  // One attempt per start: if the reinstalled bundle still looks old, don't loop.
  if (bundleOutdated() && process.env.JARVIS_BUNDLE_REINSTALLED !== "1" && reinstallBundle()) {
    process.env.JARVIS_BUNDLE_REINSTALLED = "1"; // inherited by the relaunched app
    app.relaunch();
    app.exit(0);
    return;
  }
  process.env.JARVIS_MIC = microphoneAllowed() ? "1" : "0"; // inherited by the backend
  if (process.platform === "darwin" && MODE !== "app") app.dock?.setIcon(ICON);
  registerAppProtocol();
  registerUpdateIpc();
  registerAppIpc();
  startBackgroundMode();
  startUpdater();
  void supervisor.ensureRunning(); // the dashboard shows CONNECTING until it is up
  await createWindow();
})();
