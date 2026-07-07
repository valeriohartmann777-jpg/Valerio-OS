/**
 * Leading per-tile metric type. In the base the tiles are inert slots, so nothing
 * populates these — the type is kept only because `coreTiles` still declares a
 * `stat()` shape on each tile for parity with the full app.
 */
export interface DashboardTileStats {
  trainDay: string | null
  fuelKcalToday: number | null
}
