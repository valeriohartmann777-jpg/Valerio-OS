/**
 * The per-user tile registry data model.
 *
 * A Tile is one sealed single-file HTML widget the user built (in Claude Code
 * or anywhere) and Kept. The registry is the index of these; each tile's
 * runtime data (whatever its `Vitality.save()` persists) lives separately so a
 * growing payload never rewrites the html. See lib/tiles/tileStore.ts.
 */
export interface Tile {
  id: string // crypto.randomUUID()
  name: string // user-facing, e.g. "My to-dos"
  html: string // the sealed tile source (becomes the iframe srcDoc)
  createdAt: number // Date.now(); the rail sorts newest first
  updatedAt: number // bumped on rename / html edit
  category?: string // display category, e.g. 'Intake'; defaults from kind, else 'Custom'
  // The tile's DECLARED stream (what its envelope said it reports). Carried on
  // the tile so share/publish round-trips keep the report contract intact — a
  // shared beer tile must not silently lose goalDirection:'down' (PATCH21).
  key?: string
  label?: string
  kind?: ReportKind
  goalDirection?: 'up' | 'down'
}

/** Opaque payload a tile persists through the bridge. Whatever the tile sends. */
export type TileData = unknown

/**
 * The kind of value a tile reports through the bridge. Drives the default
 * display category and how the value is read across the platform.
 */
export type ReportKind = 'intake' | 'count' | 'duration' | 'rating' | 'measure' | 'money' | 'done'

/**
 * The install envelope every pillar drops a tile through: the Library upload
 * paste box, the MCP's upload_tile, and the Arts District all hand this to
 * tileStore.importTile. Only name and html are required; the rest tune the
 * tile's display category, report contract, and skin.
 */
export interface TileEnvelope {
  name: string
  html: string
  category?: string
  key?: string
  label?: string
  kind?: ReportKind
  goalDirection?: 'up' | 'down'
  design?: string
  color?: string
  size?: import('./tileSkin').TileSize
}
