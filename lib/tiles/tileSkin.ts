/**
 * tileSkin holds how a placed tile LOOKS, separate from homeLayout (which holds
 * which tiles are placed + their order). A user can resize, recolor, restyle,
 * and rename a tile on their dashboard without touching the tile's HTML.
 *
 * v1 is localStorage, per user, mirroring tileStore + homeLayout so the same
 * Supabase swap seam applies later.
 *
 * Key:
 *   vitality:<userId>:tileSkins  -> Record<tileId, Skin>
 */

/**
 * The size vocabulary. Richer than a plain S/M/L because the loved old dashboard
 * needed a 3-wide hero, 1x2 talls, and full-width bands — a 4-token enum could
 * only make boxy 2-wide bars. Each preset resolves to a column/row span; the
 * grid CSS reads data-size and the spans drive a future free-resize handle.
 *
 *   s    1x1  utility (Vitals, Water)
 *   m    2x1  standard band
 *   tall 1x2  vertical accent (old Fuel / Peak / Brand)
 *   hero 3x1  the hero (old Train)
 *   big  2x2  centrepiece (old Vee)
 *   band 4x1  full-width (old Finance / Create)
 *   l    4x2  showcase — a user tile can outdo the hero
 */
export const TILE_SIZES = ['s', 'm', 'tall', 'hero', 'big', 'band', 'l'] as const
export type TileSize = (typeof TILE_SIZES)[number]

export interface Span {
  cols: 1 | 2 | 3 | 4
  rows: 1 | 2 | 3
}

export const SIZE_PRESETS: Record<TileSize, Span> = {
  s: { cols: 1, rows: 1 },
  m: { cols: 2, rows: 1 },
  tall: { cols: 1, rows: 2 },
  hero: { cols: 3, rows: 1 },
  big: { cols: 2, rows: 2 },
  band: { cols: 4, rows: 1 },
  l: { cols: 4, rows: 2 },
}

/** Short label for the resize chip / size segments. */
export const SIZE_LABELS: Record<TileSize, string> = {
  s: 'S',
  m: 'M',
  tall: 'Tall',
  hero: 'Hero',
  big: 'Big',
  band: 'Band',
  l: 'L',
}

/** Cycle to the next size (the resize chip on a tile), wrapping at the end. */
export function nextSize(s: TileSize): TileSize {
  return TILE_SIZES[(TILE_SIZES.indexOf(s) + 1) % TILE_SIZES.length]
}

/**
 * On a narrower grid (phone = 2 cols) a tile's columns cap at the grid width so
 * a 3-wide hero or full-width band fills the row instead of overflowing. The
 * persisted size is never mutated — this is a render-time clamp, so a tile
 * restores its true width on desktop. One source of truth, no phone/desktop drift.
 */
export function clampSpanToCols(span: Span, cols: number): Span {
  return { cols: Math.min(span.cols, cols) as Span['cols'], rows: span.rows }
}

export interface Skin {
  size: TileSize
  /** A design key from lib/tiles/designs, or null for the tile's own art. */
  design: string | null
  /** A hex accent color, or null for the default mint. */
  color: string | null
  /** A display-name override, or null to use the tile's own name. */
  name: string | null
  /** Living dots on the chosen design (the staggered pulse). On by default. */
  livingDots: boolean
}

const DEFAULT: Skin = { size: 's', design: null, color: null, name: null, livingDots: true }

/**
 * Legacy size tokens map forward losslessly so a returning user's stored sizes
 * survive the richer vocabulary. The old set was s / m / lw (full-width) / lt
 * (2x2 tall): lw -> band, lt -> big, s + m unchanged.
 */
const LEGACY_SIZE: Record<string, TileSize> = { lw: 'band', lt: 'big' }
function normalizeSize(v: unknown): TileSize {
  if (typeof v === 'string') {
    if ((TILE_SIZES as readonly string[]).includes(v)) return v as TileSize
    if (v in LEGACY_SIZE) return LEGACY_SIZE[v]
  }
  return DEFAULT.size
}

const key = (userId: string) => `vitality:${userId}:tileSkins`
const hasStorage = () => typeof window !== 'undefined' && !!window.localStorage

function readAll(userId: string): Record<string, Skin> {
  if (!hasStorage()) return {}
  try {
    const raw = window.localStorage.getItem(key(userId))
    const obj = raw ? JSON.parse(raw) : {}
    return obj && typeof obj === 'object' ? obj : {}
  } catch {
    return {}
  }
}

function writeAll(userId: string, all: Record<string, Skin>) {
  if (!hasStorage()) return
  try {
    window.localStorage.setItem(key(userId), JSON.stringify(all))
  } catch {
    /* quota / blocked. fail quiet */
  }
}

function get(userId: string, tileId: string): Skin {
  const stored = readAll(userId)[tileId]
  const merged = { ...DEFAULT, ...stored }
  merged.size = normalizeSize(merged.size)
  return merged
}

function set(userId: string, tileId: string, patch: Partial<Skin>): Skin {
  const all = readAll(userId)
  const merged = { ...DEFAULT, ...all[tileId], ...patch }
  merged.size = normalizeSize(merged.size)
  all[tileId] = merged
  writeAll(userId, all)
  return merged
}

function all(userId: string): Record<string, Skin> {
  return readAll(userId)
}

/** Drop a tile's skin entirely. Called when a tile is deleted so a later
 *  server re-adopt (or re-add) can never restore its old look. */
function remove(userId: string, tileId: string): void {
  const skins = readAll(userId)
  if (skins[tileId] === undefined) return
  delete skins[tileId]
  writeAll(userId, skins)
}

export const tileSkin = { get, set, all, remove }
