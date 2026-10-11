/**
 * The five top-level tabs and their URLs (JARVIS 2.0, docs/JARVIS_2_IA_UX.md).
 *
 * Every view has a stable hash route, so reload keeps the page, back/forward work and a link
 * can open a QuantLab section or an ULTRON room directly:
 *
 *   #/home                    #/home/mission/<id>
 *   #/training/<section>      #/quantlab/<section>
 *   #/ultron/<room>           #/ultron/<room>/mission/<id>
 *   #/settings/<section>
 *
 * Old names keep working: #/learning → Training Hub, #/bots → QuantLab › MT5 Bots,
 * #/mission/<id> → Home › mission. Tab names never change at runtime.
 */

import type { IconName } from "../components/ui/icons";

export type Tab = "home" | "training" | "quantlab" | "ultron" | "settings";

export type View =
  | { name: "home" }
  | { name: "mission"; id: string }
  | { name: "training"; section?: string }
  | { name: "quantlab"; section?: string }
  | { name: "ultron"; room?: string; mission?: string }
  | { name: "settings"; section?: string }
  // Legacy names from JARVIS 1 — accepted everywhere, normalised on navigation.
  | { name: "learning" }
  | { name: "bots" };

export interface TabDef {
  id: Tab;
  label: string;
  icon: IconName;
  /** CSS colour variable of the area accent. */
  color: string;
  hint: string;
}

export const TABS: readonly TabDef[] = [
  { id: "home", label: "Home", icon: "home", color: "var(--color-home)", hint: "Talk to JARVIS, your tasks and activity" },
  { id: "training", label: "Training Hub", icon: "training", color: "var(--color-training)", hint: "Learning, knowledge, feedback and evaluations" },
  { id: "quantlab", label: "QuantLab", icon: "quantlab", color: "var(--color-quant)", hint: "Your private trading research: strategies, data, backtests" },
  { id: "ultron", label: "Team ULTRON", icon: "ultron", color: "var(--color-ultron)", hint: "The robot team, its rooms and missions" },
  { id: "settings", label: "Settings", icon: "settings", color: "var(--color-settings)", hint: "AI & billing, voice, permissions, interface" },
];

export function normalize(view: View): View {
  if (view.name === "learning") return { name: "training" };
  if (view.name === "bots") return { name: "quantlab", section: "bots" };
  return view;
}

export function tabOf(view: View): Tab {
  const v = normalize(view);
  return v.name === "mission" ? "home" : (v.name as Tab);
}

const SEGMENT = /^[a-z0-9][a-z0-9-]{0,48}$/;
const ID = /^[A-Za-z0-9_.:-]{1,80}$/;

export function toHash(view: View): string {
  const v = normalize(view);
  switch (v.name) {
    case "home":
      return "#/home";
    case "mission":
      return `#/home/mission/${encodeURIComponent(v.id)}`;
    case "ultron":
      return `#/ultron${v.room ? `/${v.room}` : ""}${v.mission ? `/mission/${encodeURIComponent(v.mission)}` : ""}`;
    case "training":
    case "quantlab":
    case "settings":
      return `#/${v.name}${v.section ? `/${v.section}` : ""}`;
    default:
      return "#/home";
  }
}

export function fromHash(hash: string): View {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [head, a, b, c] = parts;
  const seg = (s: string | undefined) => (s && SEGMENT.test(s) ? s : undefined);
  switch (head) {
    case undefined:
    case "home":
      return a === "mission" && b && ID.test(b) ? { name: "mission", id: b } : { name: "home" };
    case "mission":
      return a && ID.test(a) ? { name: "mission", id: a } : { name: "home" };
    case "learning":
      return { name: "training" };
    case "bots":
      return { name: "quantlab", section: "bots" };
    case "training":
    case "quantlab":
    case "settings":
      return seg(a) ? { name: head, section: seg(a) } : { name: head };
    case "ultron": {
      const room = seg(a);
      const mission = b === "mission" && c && ID.test(c) ? c : undefined;
      return { name: "ultron", ...(room ? { room } : {}), ...(mission ? { mission } : {}) };
    }
    default:
      return { name: "home" };
  }
}

export function sameView(a: View, b: View): boolean {
  return toHash(a) === toHash(b);
}

// -- the last sub-section per tab (a per-viewer convenience; Home stays the launch page) ------

const KEY = "jarvis.route.";

function readLast(tab: Tab): string | undefined {
  try {
    const value = localStorage.getItem(KEY + tab);
    return value && SEGMENT.test(value) ? value : undefined;
  } catch {
    return undefined;
  }
}

function writeLast(tab: Tab, value: string): void {
  try {
    localStorage.setItem(KEY + tab, value);
  } catch {
    /* storage unavailable: nothing to remember */
  }
}

/** A bare tab click reopens the sub-section used last; an explicit one is remembered. */
export function withMemory(view: View): View {
  const v = normalize(view);
  if (v.name === "training" || v.name === "quantlab" || v.name === "settings") {
    if (v.section) writeLast(v.name, v.section);
    else {
      const last = readLast(v.name);
      return last ? { ...v, section: last } : v;
    }
  }
  if (v.name === "ultron") {
    if (v.room) writeLast("ultron", v.room);
    else if (!v.mission) {
      const last = readLast("ultron");
      return last ? { ...v, room: last } : v;
    }
  }
  return v;
}
