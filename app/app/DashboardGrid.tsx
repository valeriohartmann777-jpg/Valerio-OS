'use client'

import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { footprintFor, packTiles } from '@/lib/tiles/packLayout'
import { CORE_TILES, VEE_TILE, DEFAULT_HOME_ORDER, coreDefaultSize, type CoreTile } from '@/lib/tiles/coreTiles'
import type { TileSize } from '@/lib/tiles/tileSkin'
import { initVeeTiles } from '@/components/veeTilesAnim'
import { useTileHost } from '@/lib/tiles/useTileHost'
import { withBridge } from '@/lib/tiles/tileBridge'
import type { DashboardChrome } from '@/lib/tiles/dashboardChrome'

/**
 * The base dashboard grid. Every tile is an inert SLOT: the beautiful poster is
 * fixed, and clicking a tile either opens the sealed HTML you dropped into
 * public/tiles/<slot>.html (Patreon command or your own `/tile`), or — if the slot
 * is empty — opens the "how to build this" ConnectorOverlay.
 *
 * No auth, no drag/customize, no server. Layout is a pure function of
 * (order, sizes, cols) via the shared packer; the living orbs are animated by
 * initVeeTiles, exactly as in the full app.
 */

// The fixed slot roster (the seeded order + sizes), minus the Library tile.
const SLOT_ORDER = DEFAULT_HOME_ORDER.filter((id) => id !== 'library') as string[]

type FilledMap = Record<string, string> // slotId -> sealed HTML

/* ── the Vee centre art (wire feeds + ring pulse), animated by veeTilesAnim ── */
function VeeArt() {
  return (
    <>
      <div className="disc" />
      <svg className="art" viewBox="0 0 434 250">
        <path className="wire" style={{ stroke: 'rgba(167,243,208,.2)' }} d="M216 66 V2" />
        <path className="wire" style={{ stroke: 'rgba(185,163,255,.2)' }} d="M262 96 H300 V40 H760" />
        <path className="wire" style={{ stroke: 'rgba(232,200,120,.2)' }} d="M262 140 H320 V192 H760" />
        <path className="wire" style={{ stroke: 'rgba(167,243,208,.2)' }} d="M190 158 V248" />
        <path className="wire" style={{ stroke: 'rgba(185,163,255,.2)' }} d="M170 140 H114 V192 H-326" />
        <path className="wire" style={{ stroke: 'rgba(232,200,120,.2)' }} d="M170 96 H134 V56 H-326" />
        <g className="feedgrp">
          <path className="feed" pathLength="100" d="M216 2 V66" />
          <path className="feed" pathLength="100" d="M760 40 H300 V96 H262" />
          <path className="feed" pathLength="100" d="M760 192 H320 V140 H262" />
          <path className="feed" pathLength="100" d="M190 248 V158" />
          <path className="feed" pathLength="100" d="M-326 192 H114 V140 H170" />
          <path className="feed" pathLength="100" d="M-326 56 H134 V96 H170" />
        </g>
        <rect className="chip" x="170" y="66" width="92" height="92" rx="24" />
        <g className="ringgrp">
          <rect className="ring-soft" x="170" y="66" width="92" height="92" rx="24" />
          <rect className="ring-line" x="170" y="66" width="92" height="92" rx="24" />
        </g>
        <g className="vgrp">
          <path className="v-base" d="M201 96 L216 129 L231 96" />
          <path className="vm" d="M201 96 L216 129 L231 96" />
        </g>
      </svg>
      <div className="scrim" />
    </>
  )
}

/* ── one tile face (core poster or Vee), inert: the hit layer opens a slot ── */
function TileFace({
  id,
  isVee,
  core,
  pos,
  onOpen,
}: {
  id: string
  isVee: boolean
  core: CoreTile | null
  pos: { x: number; y: number; w: number; h: number } | undefined
  onOpen: () => void
}) {
  const label = isVee ? VEE_TILE.label : core!.label
  const index = isVee ? VEE_TILE.index : core!.index
  const variant = (core?.variant || (isVee ? 'vee' : undefined)) as string | undefined
  const orb = !isVee && core ? core.orb : undefined
  const style: CSSProperties = {
    ['--x' as string]: pos?.x ?? 0,
    ['--y' as string]: pos?.y ?? 0,
    ['--w' as string]: pos?.w ?? 1,
    ['--h' as string]: pos?.h ?? 1,
  }
  return (
    <div
      data-size={isVee ? coreDefaultSize('vee') : coreDefaultSize(id as Parameters<typeof coreDefaultSize>[0])}
      data-orb={orb?.mode}
      data-roam={orb?.roam}
      data-pt={orb?.pt}
      className={`tile${variant ? ' ' + variant : ''} editable`}
      style={style}
    >
      <div className="aurora" />

      {isVee ? <VeeArt /> : core!.art}

      <span className="index">{index}</span>
      {!isVee && core && <span className="glyph">{core.glyph}</span>}
      {isVee && <span className="kicker">{VEE_TILE.kicker}</span>}

      {isVee ? (
        <span className="label">{label}</span>
      ) : (
        <div className="cap">
          <span className="label">{label}</span>
        </div>
      )}
      <span className="arrow">→</span>

      {/* Inert: clicking opens the slot (filled tile or connector), never navigates. */}
      <button type="button" className="hit" aria-label={`Open ${label}`} onClick={onOpen} />
    </div>
  )
}

/* ── open a filled slot's sealed HTML in a sandboxed iframe ── */
function OpenTileOverlay({
  slot,
  register,
  unregister,
  onClose,
}: {
  slot: { id: string; name: string; html: string }
  register: (w: Window | null, id: string) => void
  unregister: (w: Window | null) => void
  onClose: () => void
}) {
  const winRef = useRef<Window | null>(null)
  return (
    <div className="openOverlay openFull" role="dialog" aria-modal="true" aria-label={slot.name}>
      <div className="openCard">
        <div className="openTop">
          <button type="button" className="openBack" onClick={onClose}>
            <span aria-hidden="true">←</span> Dashboard
          </button>
          <span className="openSlotName">{slot.name}</span>
        </div>
        <div className="openStage">
          <iframe
            ref={(el) => {
              if (el) {
                winRef.current = el.contentWindow
                register(el.contentWindow, slot.id)
              } else if (winRef.current) {
                unregister(winRef.current)
                winRef.current = null
              }
            }}
            onLoad={(e) => {
              winRef.current = e.currentTarget.contentWindow
              register(e.currentTarget.contentWindow, slot.id)
            }}
            className="openFrame"
            srcDoc={withBridge(slot.html)}
            sandbox="allow-scripts"
            title={slot.name}
          />
        </div>
      </div>
    </div>
  )
}

/* ── the connector: how to build (and hook up) an empty slot ── */
function ConnectorOverlay({ id, label, onClose }: { id: string; label: string; onClose: () => void }) {
  const path = `public/tiles/${id}.html`
  const prompt = `Build a "${label}" tile for my Vitality dashboard as ONE self-contained HTML file (all CSS and JS inline, no external requests). Dark background, mint #6EE7B7. Save and load with await window.Vitality.save(data) and await window.Vitality.load() (the dashboard provides window.Vitality, do not use localStorage). Write it to ${path}.`
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard?.writeText(prompt).then(() => {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    })
  }
  return (
    <div
      className="openOverlay"
      role="dialog"
      aria-modal="true"
      aria-label={`Build the ${label} tile`}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="openCard" style={{ maxWidth: 620 }}>
        <div className="openTop">
          <span className="openTitle">Build the {label} tile</span>
          <button type="button" className="openClose" aria-label="Close" onClick={onClose}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <line x1="6" y1="6" x2="18" y2="18" />
              <line x1="18" y1="6" x2="6" y2="18" />
            </svg>
          </button>
        </div>
        <div className="openStage" style={{ display: 'block', overflow: 'auto', padding: '22px 24px' }}>
          <p style={{ color: 'var(--muted)', lineHeight: 1.6, marginTop: 0 }}>
            This tile is a <strong style={{ color: 'var(--fg)' }}>slot</strong>. It fills when a
            file exists at <code style={{ color: 'var(--mint)' }}>{path}</code>. Two ways to fill it:
          </p>

          <ol style={{ color: 'var(--muted)', lineHeight: 1.7, paddingLeft: 18 }}>
            <li>
              <strong style={{ color: 'var(--fg)' }}>From a Patreon episode:</strong> drop the
              episode's command into <code>.claude/commands/</code> and run it in Claude Code (e.g.
              <code style={{ color: 'var(--mint)' }}> /logger</code>). It writes this exact file.
            </li>
            <li style={{ marginTop: 8 }}>
              <strong style={{ color: 'var(--fg)' }}>Build your own:</strong> run
              <code style={{ color: 'var(--mint)' }}> /tile {id}</code>, or paste this into Claude Code:
            </li>
          </ol>

          <pre
            style={{
              background: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
              borderRadius: 12,
              padding: '12px 14px',
              whiteSpace: 'pre-wrap',
              color: 'var(--fg)',
              fontSize: 13,
              lineHeight: 1.55,
            }}
          >
            {prompt}
          </pre>

          <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 0 }}>
            Then commit + redeploy (or reload locally) and the tile appears right here.
          </p>

          <button
            type="button"
            onClick={copy}
            style={{
              marginTop: 14,
              padding: '0.65rem 1.2rem',
              borderRadius: 999,
              background: 'var(--mint)',
              color: 'var(--mint-ink, #042a1c)',
              fontWeight: 600,
              border: 'none',
              cursor: 'pointer',
            }}
          >
            {copied ? 'Copied ✓' : 'Copy build prompt'}
          </button>
        </div>
      </div>
    </div>
  )
}

interface DashboardGridProps {
  userId: string
  chrome?: DashboardChrome
}

export default function DashboardGrid({ userId }: DashboardGridProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [mounted, setMounted] = useState(false)
  const [cols, setCols] = useState(4)
  const [filled, setFilled] = useState<FilledMap>({})
  const [openId, setOpenId] = useState<string | null>(null) // filled slot opened live
  const [connectId, setConnectId] = useState<string | null>(null) // empty slot connector

  const { register, unregister } = useTileHost(userId, undefined, () => {})

  useEffect(() => setMounted(true), [])

  // Column bucket, matching the CSS: 4 desktop / 2 phone.
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 760px)')
    const apply = () => setCols(mq.matches ? 2 : 4)
    apply()
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [])

  // Discover which slots are filled: fetch /tiles/<id>.html; 404 → empty.
  useEffect(() => {
    let alive = true
    Promise.all(
      SLOT_ORDER.map(async (id) => {
        try {
          const res = await fetch(`/tiles/${id}.html`, { cache: 'no-store' })
          if (!res.ok) return null // 404 → slot is empty
          const html = await res.text()
          if (!html.trim()) return null
          return [id, html] as const
        } catch {
          return null
        }
      }),
    ).then((pairs) => {
      if (!alive) return
      const map: FilledMap = {}
      for (const p of pairs) if (p) map[p[0]] = p[1]
      setFilled(map)
    })
    return () => {
      alive = false
    }
  }, [])

  // Layout: pure function of (order, sizes, cols).
  const { positions, rows } = useMemo(() => {
    const feet = SLOT_ORDER.map((id) => {
      const size = (id === 'vee' ? coreDefaultSize('vee') : coreDefaultSize(id as Parameters<typeof coreDefaultSize>[0])) as TileSize
      return footprintFor(id, size, cols)
    })
    return packTiles(feet, cols)
  }, [cols])

  // (Re)bind the living orbs whenever the packed layout changes.
  useEffect(() => {
    if (!ref.current || !mounted) return
    return initVeeTiles(ref.current, { score: null, showNumber: false })
  }, [mounted, cols])

  // Esc closes any overlay.
  useEffect(() => {
    if (!openId && !connectId) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpenId(null)
        setConnectId(null)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [openId, connectId])

  if (!mounted) return null

  const openSlot = (id: string) => {
    if (filled[id]) setOpenId(id)
    else setConnectId(id)
  }

  const labelFor = (id: string) => (id === 'vee' ? VEE_TILE.label : CORE_TILES[id as keyof typeof CORE_TILES].label)

  return (
    <div className="veeTiles" ref={ref}>
      <div className="grid" style={{ ['--rows' as string]: rows }}>
        {SLOT_ORDER.map((id) => {
          const isVee = id === 'vee'
          return (
            <TileFace
              key={id}
              id={id}
              isVee={isVee}
              core={isVee ? null : CORE_TILES[id as keyof typeof CORE_TILES]}
              pos={positions.get(id)}
              onOpen={() => openSlot(id)}
            />
          )
        })}
      </div>

      {openId && filled[openId] && (
        <OpenTileOverlay
          key={openId}
          slot={{ id: openId, name: labelFor(openId), html: filled[openId] }}
          register={register}
          unregister={unregister}
          onClose={() => setOpenId(null)}
        />
      )}

      {connectId && (
        <ConnectorOverlay id={connectId} label={labelFor(connectId)} onClose={() => setConnectId(null)} />
      )}
    </div>
  )
}
