/**
 * dashboardChrome holds the per-user "world" around the tiles: the background
 * (wallpaper) and, later, the greeting/header. It is the chrome a user themes to
 * make the dashboard THEIRS, separate from homeLayout (which tiles) and tileSkin
 * (how each tile looks).
 *
 * v1 is localStorage, per user, mirroring tileStore / homeLayout / tileSkin so the
 * same Supabase swap seam applies later.
 *
 * Key:
 *   vitality:<userId>:chrome  -> DashboardChrome (JSON)
 */

/** The background can be the animated brand World, a two-stop Gradient, or a Solid. */
export type Background =
  | { mode: 'world'; accent: string; particles: number; mountains: boolean; speed: number }
  | { mode: 'gradient'; c1: string; c2: string; angle: number }
  | { mode: 'solid'; color: string }

/** The greeting line. The FONT is locked to Instrument Serif italic (the unified
 *  voice); only the wording, name, accent, and scale are user-editable. */
export interface Greeting {
  mode: 'auto' | 'custom'
  /** Used when mode === 'custom'. */
  text: string
  showName: boolean
  accentName: boolean
  /** Size multiplier on the responsive clamp, ~0.8–1.3. */
  scale: number
}
export interface DateConfig {
  show: boolean
  format: 'today' | 'full'
}
export interface GemConfig {
  show: boolean
  tint: 'mint' | 'accent'
}

export interface DashboardChrome {
  background: Background
  greeting: Greeting
  date: DateConfig
  gem: GemConfig
}

/** The default reproduces today's look exactly: the mint World, the auto
 *  time-of-day greeting with the accented name, the full date, the mint gem.
 *  Existing users see zero change until they touch something. */
export const DEFAULT_CHROME: DashboardChrome = {
  background: { mode: 'world', accent: '#6EE7B7', particles: 24, mountains: true, speed: 1 },
  greeting: { mode: 'auto', text: '', showName: true, accentName: true, scale: 1 },
  date: { show: true, format: 'full' },
  gem: { show: true, tint: 'mint' },
}

/** Wallpaper accent swatches (World tint + Solid + theme-from-wallpaper). */
export const WALLPAPER_ACCENTS: { name: string; hex: string }[] = [
  { name: 'Mint', hex: '#6EE7B7' },
  { name: 'Azure', hex: '#6EA8FF' },
  { name: 'Ice', hex: '#CFE9FF' },
  { name: 'Amber', hex: '#F5B044' },
  { name: 'Violet', hex: '#B794F6' },
  { name: 'Rose', hex: '#F49AC2' },
]

/** A few curated gradient presets (c1, c2, angle) — calm, on-brand, dark. */
export const GRADIENT_PRESETS: { name: string; c1: string; c2: string; angle: number }[] = [
  { name: 'Forest', c1: '#0b1f17', c2: '#040608', angle: 160 },
  { name: 'Deep sea', c1: '#0a1722', c2: '#040810', angle: 165 },
  { name: 'Dusk', c1: '#1a1124', c2: '#070410', angle: 155 },
  { name: 'Ember', c1: '#241405', c2: '#0c0604', angle: 150 },
  { name: 'Slate', c1: '#10161c', c2: '#060809', angle: 170 },
]

const key = (userId: string) => `vitality:${userId}:chrome`
const hasStorage = () => typeof window !== 'undefined' && !!window.localStorage

function mergeBackground(b: unknown): Background {
  if (!b || typeof b !== 'object') return DEFAULT_CHROME.background
  const bg = b as Background
  // Only a World blob is merged up to full (older / partial data); gradient +
  // solid pass through clean (no stray world fields).
  if (bg.mode === 'world') return { ...DEFAULT_CHROME.background, ...bg }
  if (bg.mode === 'gradient' || bg.mode === 'solid') return bg
  return DEFAULT_CHROME.background
}

function obj(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' ? (v as Record<string, unknown>) : {}
}

function read(userId: string): DashboardChrome {
  if (!hasStorage()) return DEFAULT_CHROME
  try {
    const raw = window.localStorage.getItem(key(userId))
    if (!raw) return DEFAULT_CHROME
    const o = obj(JSON.parse(raw))
    return {
      background: mergeBackground(o.background),
      greeting: { ...DEFAULT_CHROME.greeting, ...obj(o.greeting) },
      date: { ...DEFAULT_CHROME.date, ...obj(o.date) },
      gem: { ...DEFAULT_CHROME.gem, ...obj(o.gem) },
    }
  } catch {
    return DEFAULT_CHROME
  }
}

function write(userId: string, chrome: DashboardChrome) {
  if (!hasStorage()) return
  try {
    window.localStorage.setItem(key(userId), JSON.stringify(chrome))
  } catch {
    /* quota / blocked. fail quiet */
  }
}

function get(userId: string): DashboardChrome {
  return read(userId)
}

/** Merge a partial chrome (the header / wallpaper editors write one section). */
function update(userId: string, patch: Partial<DashboardChrome>): DashboardChrome {
  const next = { ...read(userId), ...patch }
  write(userId, next)
  return next
}

/** Replace the background (the wallpaper editor writes a whole Background). */
function setBackground(userId: string, background: Background): DashboardChrome {
  return update(userId, { background })
}

function reset(userId: string): DashboardChrome {
  if (hasStorage()) {
    try {
      window.localStorage.removeItem(key(userId))
    } catch {
      /* fail quiet */
    }
  }
  return DEFAULT_CHROME
}

/** The accent a chosen background publishes into --wall-accent (themes the UI). */
export function backgroundAccent(bg: Background): string {
  if (bg.mode === 'world') return bg.accent
  if (bg.mode === 'gradient') return '#6EE7B7'
  return '#6EE7B7'
}

export const dashboardChrome = { get, update, setBackground, reset }
