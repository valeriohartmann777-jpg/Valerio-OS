'use client'

import { useEffect, useState } from 'react'
import styles from './dashboard.module.css'
import DashboardHeader from './DashboardHeader'
import WelcomeBackdrop from '@/components/WelcomeBackdrop'
import DashboardHeaderGem from './DashboardHeaderGem'
import DashboardGrid from './DashboardGrid'
import '@/components/veeTiles.css'
import { dashboardChrome, backgroundAccent, DEFAULT_CHROME, type DashboardChrome } from '@/lib/tiles/dashboardChrome'

interface DashboardProps {
  firstName: string | null
  userId: string
}

/**
 * The whole base app: one dashboard. The Vitality character lives in the header
 * gem next to the greeting; below sits the animated-orb tile grid. Every tile is
 * an inert "slot" you fill with your own sealed HTML (see public/tiles/README.md).
 *
 * Zero backend: chrome (wallpaper + greeting) is localStorage, tiles are static
 * files under /public/tiles, and there's no auth. `userId` is a constant so the
 * localStorage namespaces (chrome, tile skins, layout) stay stable per browser.
 */
export default function Dashboard({ firstName, userId }: DashboardProps) {
  const avatarInitial = (firstName?.trim()?.[0] || 'V').toUpperCase()
  const [chrome, setChrome] = useState<DashboardChrome | undefined>(undefined)

  useEffect(() => {
    setChrome(dashboardChrome.get(userId))
  }, [userId])

  const wallAccent = chrome ? backgroundAccent(chrome.background) : '#6EE7B7'
  const showGem = chrome?.gem.show ?? true

  return (
    <main className={`${styles.page} ${styles.oneScreen} grain-overlay`} style={{ ['--wall-accent' as string]: wallAccent }}>
      <WelcomeBackdrop background={chrome?.background} />

      <div className={styles.shell}>
        <div className={styles.headerRow}>
          {showGem && <DashboardHeaderGem className={styles.headerGem} />}
          <DashboardHeader firstName={firstName} greeting={chrome?.greeting} date={chrome?.date} />
          {/* Decorative identity gem (top-right). No account/settings in the base. */}
          <div className={styles.profileAvatar} aria-hidden>
            <span>{avatarInitial}</span>
          </div>
        </div>

        <DashboardGrid userId={userId} chrome={chrome ?? DEFAULT_CHROME} />
      </div>
    </main>
  )
}
