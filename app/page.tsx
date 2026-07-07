import Dashboard from './app/Dashboard'
import { site } from '@/content/site'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Dashboard · Vitality',
}

// The base is a single page: the dashboard. No auth, no backend — a fixed
// per-browser userId keeps the localStorage namespaces (chrome, tile skins,
// layout) stable.
export default function Page() {
  return <Dashboard firstName={site.name || null} userId="me" />
}
