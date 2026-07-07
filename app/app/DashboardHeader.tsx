'use client'

import { useState, useEffect } from 'react'
import styles from './dashboard.module.css'
import { DEFAULT_CHROME, type Greeting, type DateConfig } from '@/lib/tiles/dashboardChrome'

interface DashboardHeaderProps {
  firstName?: string | null
  greeting?: Greeting
  date?: DateConfig
}

/**
 * The editorial greeting + date. Prop-driven so a user can personalise it
 * (lib/tiles/dashboardChrome): keep the auto time-of-day line or write their own,
 * show / accent their name, scale it, and pick the date format (or hide it). The
 * FONT stays Instrument Serif italic (the unified Vitality voice) — only wording,
 * name, accent, and scale are exposed.
 */
export default function DashboardHeader({ firstName, greeting, date }: DashboardHeaderProps) {
  const g = greeting ?? DEFAULT_CHROME.greeting
  const d = date ?? DEFAULT_CHROME.date
  const [autoWord, setAutoWord] = useState('')
  const [fullDate, setFullDate] = useState('')
  const [todayDate, setTodayDate] = useState('')

  useEffect(() => {
    const now = new Date()
    const hour = now.getHours()
    setAutoWord(hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening')
    setFullDate(now.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' }))
    setTodayDate(now.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }))
  }, [])

  // Custom wording falls back to the auto line if blank (never render empty).
  const word = g.mode === 'custom' && g.text.trim() ? g.text.trim() : autoWord
  const includesName = !!(firstName && word.toLowerCase().includes(firstName.toLowerCase()))
  const renderName = g.showName && firstName && !includesName
  const dateText = d.format === 'today' ? todayDate : fullDate

  return (
    <div className={styles.header} style={{ ['--greet-scale' as string]: g.scale }}>
      <h1 className={styles.greeting}>
        {word}
        {renderName ? (
          <>
            ,&nbsp;<span className={g.accentName ? styles.greetingName : undefined}>{firstName}</span>
          </>
        ) : null}
      </h1>
      {d.show && <p className={styles.date}>{dateText}</p>}
    </div>
  )
}
