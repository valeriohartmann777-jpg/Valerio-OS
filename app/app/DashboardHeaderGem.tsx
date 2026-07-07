'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import dynamic from 'next/dynamic'
import styles from './dashboardHeaderGem.module.css'

/**
 * DashboardHeaderGem — the canonical Vitality V crystal mounted next to
 * the dashboard greeting. Uses the REAL HeroCrystal component in
 * character mode so the gem behaves byte-for-byte like the lab at
 * /gem-library and the V crystal on /welcome.
 *
 * Behaviors (from HeroCrystal character mode):
 *   - Mint glass icosahedron with engraved V + micro-flicker
 *   - Sporadic Destiny-Ghost rotation jolts + periodic forward nod
 *   - Autonomous happy event every 9-16s firing onHappyStart, plus the
 *     full happyHello scripted move with eyebrows, slight tilt, and V→
 *     waving-hand→V flicker (matches the v2 lab "happy hello" exactly)
 *
 * Pulse bursts (CSS-driven DOM overlays, fired on each happy event):
 *   - rings:     3 mint rings expanding outward, staggered
 *   - particles: 14 mint motes radiating out at evenly-spaced angles
 *   - sparkles:  14 sparkle dots at random perimeter positions, twinkling
 *
 * No ponder or curious mood: those are dropped (no question mark).
 */

const HeroCrystal = dynamic(() => import('@/components/HeroCrystal'), {
  ssr: false,
  loading: () => <div className={styles.gemFallback} aria-hidden />,
})

type PulseKind = 'rings' | 'particles' | 'sparkles'
const PULSE_KINDS: PulseKind[] = ['rings', 'particles', 'sparkles']

interface DashboardHeaderGemProps {
  /** CSS pixel size of the gem stage. Default 200px. */
  size?: number
  className?: string
}

export default function DashboardHeaderGem({
  size = 200,
  className,
}: DashboardHeaderGemProps) {
  const [pulse, setPulse] = useState<{ tick: number; kind: PulseKind }>({ tick: 0, kind: 'rings' })
  const lastKindRef = useRef<PulseKind | null>(null)
  // HeroCrystal assigns its move-trigger here so the dashboard can play
  // scripted moods on a slow timer (below).
  const gemControl = useRef<((move: string) => void) | null>(null)

  // Fire a random different-than-last burst. Called from the gem's
  // autonomous happy event so every happyHello also carries a burst.
  function firePulse() {
    let pick: PulseKind = PULSE_KINDS[Math.floor(Math.random() * PULSE_KINDS.length)]
    while (pick === lastKindRef.current && PULSE_KINDS.length > 1) {
      pick = PULSE_KINDS[Math.floor(Math.random() * PULSE_KINDS.length)]
    }
    lastKindRef.current = pick
    setPulse(p => ({ tick: p.tick + 1, kind: pick }))
  }

  // Random sparkle positions around the gem perimeter, re-rolled each fire.
  const sparkleDots = useMemo(() => {
    return Array.from({ length: 14 }).map(() => {
      const angle = Math.random() * Math.PI * 2
      const radius = 32 + Math.random() * 16
      return {
        x: 50 + Math.cos(angle) * radius,
        y: 50 + Math.sin(angle) * radius,
        delay: Math.random() * 0.35,
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pulse.tick])

  // Beyond the gem's own autonomous happy hello, rotate through the other
  // moods on a slow timer so the dashboard character feels alive. 'curious'
  // is excluded (no question mark); 'sleepy' is excluded here until
  // the floating-z's sleep effect is wired into the dashboard (it's lab-only
  // for now, and the ~5s droop reads as frozen without the z's).
  useEffect(() => {
    const MOODS = ['excited', 'love', 'proud', 'spin', 'focus']
    let cancelled = false
    let id: ReturnType<typeof setTimeout>
    const schedule = () => {
      const delay = (20 + Math.random() * 18) * 1000
      id = setTimeout(() => {
        if (cancelled) return
        gemControl.current?.(MOODS[Math.floor(Math.random() * MOODS.length)])
        schedule()
      }, delay)
    }
    schedule()
    return () => { cancelled = true; clearTimeout(id) }
  }, [])

  // When a className is supplied, the container owns the gem's size (the
  // dashboard's .headerGem media queries shrink it 180→140→100 down the
  // breakpoints). Hard-coding an inline width/height here would beat those
  // media queries (inline > stylesheet) and the gem would stay 200px on
  // phones — overflowing the header and bleeding off the left edge. So only
  // fall back to the `size` prop for standalone (no-className) usage; the
  // gem stage then fills 100% of whatever box the container gives it.
  const sizeStyle = className ? undefined : { width: size, height: size }

  return (
    <div
      className={className}
      style={{ ...sizeStyle, pointerEvents: 'none' }}
      aria-hidden
    >
      <div className={styles.gemStage} style={sizeStyle}>
        {pulse.tick > 0 && pulse.kind === 'rings' && (
          <div key={`rings-${pulse.tick}`} className={styles.pulseLayer} aria-hidden>
            <span className={`${styles.pulseRing} ${styles.pulseRing1}`} />
            <span className={`${styles.pulseRing} ${styles.pulseRing2}`} />
            <span className={`${styles.pulseRing} ${styles.pulseRing3}`} />
          </div>
        )}
        {pulse.tick > 0 && pulse.kind === 'particles' && (
          <div key={`particles-${pulse.tick}`} className={styles.pulseLayer} aria-hidden>
            {Array.from({ length: 14 }).map((_, i) => (
              <span
                key={i}
                className={styles.particleDot}
                style={{ ['--angle' as string]: `${(360 / 14) * i}deg` }}
              />
            ))}
          </div>
        )}
        {pulse.tick > 0 && pulse.kind === 'sparkles' && (
          <div key={`sparkles-${pulse.tick}`} className={styles.pulseLayer} aria-hidden>
            {sparkleDots.map((s, i) => (
              <span
                key={i}
                className={styles.sparkleDot}
                style={{ left: `${s.x}%`, top: `${s.y}%`, animationDelay: `${s.delay}s` }}
              />
            ))}
          </div>
        )}

        <HeroCrystal
          mode="character"
          onHappyStart={firePulse}
          controlRef={gemControl}
        />
      </div>
    </div>
  )
}
