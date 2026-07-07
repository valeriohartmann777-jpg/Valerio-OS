'use client'

import { useEffect, useRef } from 'react'
import styles from './WelcomeBackdrop.module.css'
import { DEFAULT_CHROME, type Background } from '@/lib/tiles/dashboardChrome'

/**
 * Shared atmospheric backdrop — the dashboard's "world". Prop-driven so a user can
 * theme it (the wallpaper picker writes lib/tiles/dashboardChrome):
 *   - world:    aurora wash + distant mountains + drifting particles, all tinted
 *               to a chosen accent (the default, identical to the original look)
 *   - gradient: a calm two-stop gradient
 *   - solid:    a flat color
 *
 * World renders three fixed layers at z 0/1/2 and publishes --wall-accent so the
 * aurora + particles theme in unison. Parents put their content on top at z 5+.
 */
export default function WelcomeBackdrop({ background }: { background?: Background }) {
  const bg = background ?? DEFAULT_CHROME.background
  const particlesRef = useRef<HTMLDivElement | null>(null)

  const world = bg.mode === 'world' ? bg : null
  const count = world ? world.particles : 0
  const speed = world ? world.speed : 1

  // Drifting particle field. Fewer on narrow viewports; count + speed are themed.
  useEffect(() => {
    const root = particlesRef.current
    if (!root) return
    root.innerHTML = ''
    if (count <= 0) return
    const N = window.innerWidth < 640 ? Math.round(count * 0.6) : count
    const created: HTMLSpanElement[] = []
    for (let i = 0; i < N; i++) {
      const s = document.createElement('span')
      s.style.left = Math.random() * 100 + '%'
      s.style.top = 60 + Math.random() * 40 + '%'
      const size = 1.2 + Math.random() * 1.2
      s.style.width = s.style.height = size + 'px'
      const dur = (22 + Math.random() * 28) / Math.max(0.2, speed)
      s.style.animationDuration = dur + 's'
      s.style.animationDelay = -Math.random() * dur + 's'
      s.style.setProperty('--dx', Math.random() * 30 - 15 + 'px')
      s.style.setProperty('--dy', -(60 + Math.random() * 50) + 'vh')
      root.appendChild(s)
      created.push(s)
    }
    return () => { created.forEach((s) => s.remove()) }
  }, [count, speed])

  if (bg.mode === 'gradient') {
    return <div className={styles.flat} aria-hidden style={{ background: `linear-gradient(${bg.angle}deg, ${bg.c1}, ${bg.c2})` }} />
  }
  if (bg.mode === 'solid') {
    return <div className={styles.flat} aria-hidden style={{ background: bg.color }} />
  }

  return (
    <div style={{ ['--wall-accent' as string]: world!.accent }}>
      <div className={styles.atmosphere} aria-hidden />
      {world!.mountains && (
        <div className={styles.mountainsLayer} aria-hidden>
          <svg viewBox="0 0 1600 420" preserveAspectRatio="none">
            <defs>
              <linearGradient id="welcome-backdrop-mt-far" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#0d1a17" stopOpacity="0" />
                <stop offset="55%" stopColor="#0d1a17" stopOpacity=".55" />
                <stop offset="100%" stopColor="#0d1a17" stopOpacity=".95" />
              </linearGradient>
              <linearGradient id="welcome-backdrop-mt-near" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#050a09" stopOpacity=".4" />
                <stop offset="60%" stopColor="#050a09" stopOpacity=".95" />
                <stop offset="100%" stopColor="#050a09" stopOpacity="1" />
              </linearGradient>
            </defs>
            <path d="M0,300 L120,230 L210,260 L320,180 L430,220 L560,150 L680,210 L820,170 L960,220 L1100,180 L1240,240 L1380,200 L1500,250 L1600,220 L1600,420 L0,420 Z" fill="url(#welcome-backdrop-mt-far)" />
            <path d="M0,360 L100,320 L220,340 L340,290 L460,330 L590,300 L720,340 L860,310 L1000,350 L1140,310 L1280,355 L1420,320 L1540,360 L1600,340 L1600,420 L0,420 Z" fill="url(#welcome-backdrop-mt-near)" />
          </svg>
        </div>
      )}
      <div className={styles.particles} ref={particlesRef} aria-hidden />
    </div>
  )
}
