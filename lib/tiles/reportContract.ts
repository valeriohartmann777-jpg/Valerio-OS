/**
 * The tile-to-Vee report contract — the narrow waist every MCP-built (or
 * hand-built) tile uses to feed the "Vitality noticed" engine.
 *
 * A sealed tile talks to Vitality through two channels: Vitality.save / load for
 * its OWN private data, and Vitality.report(stream) for ONE numeric life-stream
 * into Vee. This file is the single source of truth for that stream's shape, so
 * the host (write side) and the noticed engine (read side) can never build
 * mismatched halves. Import it on both sides.
 *
 * The whole trick is `kind`: a small fixed taxonomy that tells the generic engine
 * how to treat a number it has never seen before, with zero per-tile code. A
 * canonical catalog (beer -> alcohol) normalizes keys across users so ten people's
 * beer tiles become one comparable family.
 *
 * Pure + IO-free + unit-tested (see __tests__/reportContract.test.ts).
 */

/** The fixed ~7-member taxonomy. Each tile number maps to exactly one. */
export const REPORT_KINDS = ['intake', 'count', 'duration', 'rating', 'measure', 'money', 'done'] as const
export type ReportKind = (typeof REPORT_KINDS)[number]

/** What "good" looks like for a stream, so downstream copy reads right. */
export const GOAL_DIRECTIONS = ['up', 'down', 'neutral'] as const
export type GoalDirection = (typeof GOAL_DIRECTIONS)[number]

/** The payload a tile posts via Vitality.report(). The deliberately small,
 *  deliberately fixed boundary. UIs are free; this is disciplined. */
export interface ReportedStream {
  key: string
  label: string
  value: number
  date: string // local YYYY-MM-DD
  kind: ReportKind
  goalDirection?: GoalDirection
}

/** A row of `tile_streams` — the identity/definition of one stream. Since
 *  PATCH21 a stream's row identity is (user_id, tile_id, key): two different
 *  tiles may honestly report the same key without clobbering each other.
 *  canonicalKey still names the cross-user family (beer -> alcohol). id and
 *  tileId are optional so pure fixtures/tests can omit them. */
export interface TileStreamRow {
  id?: string
  tileId?: string
  key: string
  canonicalKey: string
  label: string
  kind: ReportKind
  goalDirection: GoalDirection | null
}

/** A row of `tile_reports` — one logged datapoint. streamId ties it to its
 *  stream ROW (per-tile identity); streamKey rides along for readability. */
export interface TileReportRow {
  streamId?: string
  streamKey: string
  value: number
  date: string
}

/**
 * Group raw report rows under their stream definitions by the PER-TILE row
 * identity: a report with a streamId joins only the stream row carrying that
 * id; legacy rows without one fall back to the raw key, and that fallback can
 * never cross tiles (it only matches when exactly one stream owns the key).
 * The one place the join lives, so the score, the chat context, and the
 * connections engine can never disagree about which datapoint belongs where.
 */
export function groupReportRows(
  streams: TileStreamRow[],
  reports: TileReportRow[],
): Array<{ def: TileStreamRow; rows: TileReportRow[] }> {
  const byId = new Map<string, TileReportRow[]>()
  const byKey = new Map<string, TileReportRow[]>()
  for (const r of reports) {
    const map = r.streamId ? byId : byKey
    const k = r.streamId ?? r.streamKey
    const arr = map.get(k)
    if (arr) arr.push(r)
    else map.set(k, [r])
  }
  const keyOwners = new Map<string, number>()
  for (const s of streams) keyOwners.set(s.key, (keyOwners.get(s.key) ?? 0) + 1)

  return streams.map((def) => {
    const rows = [...(def.id ? (byId.get(def.id) ?? []) : [])]
    // key fallback only when this stream is the key's sole owner — a legacy
    // row must never be double-counted into two same-key tiles.
    if (keyOwners.get(def.key) === 1) rows.push(...(byKey.get(def.key) ?? []))
    return { def, rows }
  })
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

/** Canonical families: aliases people will naturally use, mapped to one key so
 *  cross-user priors line up. Intentionally tiny at launch (just alcohol); grows
 *  as real tiles arrive. An unknown key passes through cleaned. */
const CANONICAL: Record<string, string> = {
  beer: 'alcohol',
  beers: 'alcohol',
  brew: 'alcohol',
  brews: 'alcohol',
  pint: 'alcohol',
  pints: 'alcohol',
  drink: 'alcohol',
  drinks: 'alcohol',
  alcohol: 'alcohol',
}

/** Lowercase + trim a key, then map it to its canonical family when known. */
export function normalizeKey(key: string): string {
  const clean = key.trim().toLowerCase()
  return CANONICAL[clean] ?? clean
}

export type ValidateResult =
  | { ok: true; stream: ReportedStream }
  | { ok: false; error: string }

/** Validate an untrusted payload (it crosses an iframe boundary) into a
 *  ReportedStream, or explain why it is rejected. Never throws. */
export function validateReport(input: unknown): ValidateResult {
  if (typeof input !== 'object' || input === null) return { ok: false, error: 'not an object' }
  const o = input as Record<string, unknown>

  if (typeof o.key !== 'string' || o.key.trim() === '') return { ok: false, error: 'key must be a non-empty string' }
  if (o.key.trim().length > 64) return { ok: false, error: 'key must be 64 characters or fewer' }
  if (typeof o.label !== 'string' || o.label.trim() === '') return { ok: false, error: 'label must be a non-empty string' }
  if (o.label.trim().length > 120) return { ok: false, error: 'label must be 120 characters or fewer' }
  if (typeof o.value !== 'number' || !Number.isFinite(o.value)) return { ok: false, error: 'value must be a finite number' }
  if (Math.abs(o.value) > 1e9) return { ok: false, error: 'value is out of range' }
  if (typeof o.date !== 'string' || !DATE_RE.test(o.date)) return { ok: false, error: 'date must be YYYY-MM-DD' }
  if (typeof o.kind !== 'string' || !(REPORT_KINDS as readonly string[]).includes(o.kind)) {
    return { ok: false, error: 'kind must be one of the fixed taxonomy' }
  }
  if (o.goalDirection !== undefined) {
    if (typeof o.goalDirection !== 'string' || !(GOAL_DIRECTIONS as readonly string[]).includes(o.goalDirection)) {
      return { ok: false, error: 'goalDirection must be up, down, or neutral' }
    }
  }

  const stream: ReportedStream = {
    key: o.key.trim(),
    label: o.label.trim(),
    value: o.value,
    date: o.date,
    kind: o.kind as ReportKind,
    ...(o.goalDirection !== undefined ? { goalDirection: o.goalDirection as GoalDirection } : {}),
  }
  return { ok: true, stream }
}
