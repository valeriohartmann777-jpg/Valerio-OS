'use client'

import { useCallback, useEffect, useRef } from 'react'
import { tileStore } from './tileStore'

/**
 * useTileHost is the host side of the Vitality bridge, fixed for MANY tiles.
 *
 * The bug it fixes: the BUILD71 host closed a single storage key over one
 * message listener, so ANY tile's save overwrote whatever the host last
 * pointed at. Render two tiles and they clobber each other.
 *
 * The fix: keep a live Window to tileId map keyed by each iframe's
 * contentWindow. That WindowProxy is a stable reference and it equals
 * event.source for messages that frame posts, so every save/load is routed to
 * the tile whose window actually sent it. N tiles on one page each resolve to
 * their own key. No clobber.
 *
 * register(win, tileId) is called from each iframe instance (see TilePreview),
 * which captures its own tileId, so a late onLoad can never bind a window to
 * the wrong tile. unregister(win) drops a window when its iframe unmounts.
 *
 * The map is reset whenever userId changes, so a stale window mapping from a
 * previous user can never route one user's data into another's namespace.
 */
export function useTileHost(
  userId: string,
  onActivity?: (info: { tileId: string; type: 'save' | 'load' | 'report'; count: number }) => void,
  /**
   * Injected handler for a tile's Vitality.report() stream (one numeric life-stream
   * into Vee). Passed in (not imported) so this hook stays decoupled from the
   * server action; the create page wires reportStream here. The host only routes
   * and forwards; the server action validates + RLS-writes.
   */
  onReport?: (stream: unknown, tileId: string) => void,
) {
  const reg = useRef<WeakMap<Window, string>>(new WeakMap())

  // reset the map synchronously when the user changes (before any new register)
  const lastUser = useRef(userId)
  if (lastUser.current !== userId) {
    reg.current = new WeakMap()
    lastUser.current = userId
  }

  const activity = useRef(onActivity)
  activity.current = onActivity

  const report = useRef(onReport)
  report.current = onReport

  const register = useCallback((win: Window | null, tileId: string) => {
    if (win) reg.current.set(win, tileId)
  }, [])

  const unregister = useCallback((win: Window | null) => {
    if (win) reg.current.delete(win)
  }, [])

  useEffect(() => {
    async function onMessage(e: MessageEvent) {
      const msg = e.data
      if (!msg || msg.source !== 'vitality-tile') return
      const src = e.source as Window | null
      if (!src) return
      const tileId = reg.current.get(src)
      if (!tileId) {
        // Sender is not in our registry (a race, or the registry was reset while
        // a tile was open). Still settle any id-bearing request so the tile's
        // `await window.Vitality.save/load(...)` can never hang forever.
        if (msg.id && msg.type === 'save') {
          src.postMessage({ source: 'vitality-host', type: 'save:error', id: msg.id, reason: 'unregistered_sender' }, '*')
        } else if (msg.id && msg.type === 'load') {
          src.postMessage({ source: 'vitality-host', type: 'load:result', id: msg.id, data: [] }, '*')
        }
        return
      }

      if (msg.type === 'save') {
        const ok = await tileStore.saveData(userId, tileId, msg.data)
        if (!ok) {
          // the write was dropped (over the per-tile cap or the storage quota).
          // Tell the tile instead of silently letting it believe it saved.
          src.postMessage({ source: 'vitality-host', type: 'save:error', id: msg.id, reason: 'too_large_or_full' }, '*')
          return
        }
        // ack success so a tile's `await window.Vitality.save(...)` resolves truthfully
        src.postMessage({ source: 'vitality-host', type: 'save:ok', id: msg.id }, '*')
        const count = Array.isArray(msg.data) ? msg.data.length : 0
        activity.current?.({ tileId, type: 'save', count })
        return
      }

      if (msg.type === 'load') {
        const data = await tileStore.loadData(userId, tileId)
        // reply to the exact sender, never a broadcast. targetOrigin stays '*'
        // because a sealed srcDoc tile has an opaque (null) origin; the sender
        // is already verified via the registered e.source, and the payload is
        // the tile's own data going back to it.
        src.postMessage({ source: 'vitality-host', type: 'load:result', id: msg.id, data }, '*')
        const count = Array.isArray(data) ? data.length : 0
        activity.current?.({ tileId, type: 'load', count })
        return
      }

      if (msg.type === 'report') {
        // One numeric life-stream into Vee. The host only forwards the raw stream
        // plus the SENDER's tileId (from our own registry, never the iframe's
        // claim) so the stream's per-tile identity is trustworthy; the injected
        // handler (the server action) validates it and RLS-writes it under the
        // session user. Fire-and-forget: a tile never blocks on Vee.
        report.current?.(msg.stream, tileId)
        activity.current?.({ tileId, type: 'report', count: 1 })
      }
    }

    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [userId])

  return { register, unregister }
}
