import { SIZE_PRESETS, clampSpanToCols, type TileSize } from './tileSkin'

/**
 * The dashboard's tile layout, computed as a PURE FUNCTION of (order, sizes, cols).
 *
 * This is the whole reason edit-mode drag can be bulletproof: positions are never
 * stored and never guessed — they are derived by first-fit packing, which only ever
 * places a tile in cells that are already free. Two tiles overlapping, or a tile
 * escaping the grid, is therefore impossible by construction, for any user, ever.
 * `__tests__/packLayout.test.ts` proves exactly that with property tests.
 */

export type Footprint = { id: string; w: number; h: number }
export type PackedPos = { x: number; y: number; w: number; h: number }
export type PackResult = { positions: Map<string, PackedPos>; rows: number }

/** A tile id + its size → the footprint the packer places, width clamped to cols. */
export function footprintFor(id: string, size: TileSize, cols: number): Footprint {
  const span = clampSpanToCols(SIZE_PRESETS[size], cols)
  return { id, w: span.cols, h: span.rows }
}

/**
 * Pack tiles top-left into a `cols`-wide grid, in order, with no gaps left behind
 * (dense first-fit). Returns each tile's cell rect plus the total row count.
 */
export function packTiles(tiles: Footprint[], cols: number): PackResult {
  const occupied: boolean[][] = [] // occupied[y][x]
  const ensureRow = (y: number) => {
    while (occupied.length <= y) occupied.push(new Array(cols).fill(false))
  }
  const fits = (x: number, y: number, w: number, h: number): boolean => {
    if (x < 0 || x + w > cols) return false
    for (let dy = 0; dy < h; dy++) {
      ensureRow(y + dy)
      for (let dx = 0; dx < w; dx++) if (occupied[y + dy][x + dx]) return false
    }
    return true
  }
  const occupy = (x: number, y: number, w: number, h: number) => {
    for (let dy = 0; dy < h; dy++) {
      ensureRow(y + dy)
      for (let dx = 0; dx < w; dx++) occupied[y + dy][x + dx] = true
    }
  }

  const positions = new Map<string, PackedPos>()
  for (const t of tiles) {
    const w = Math.max(1, Math.min(t.w, cols))
    const h = Math.max(1, t.h)
    let placed = false
    for (let y = 0; !placed; y++) {
      ensureRow(y)
      for (let x = 0; x + w <= cols; x++) {
        if (fits(x, y, w, h)) {
          occupy(x, y, w, h)
          positions.set(t.id, { x, y, w, h })
          placed = true
          break
        }
      }
    }
  }
  return { positions, rows: occupied.length }
}
