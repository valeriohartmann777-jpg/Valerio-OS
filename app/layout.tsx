import type { Metadata, Viewport } from 'next'
import { Inter, Instrument_Serif, Hanken_Grotesk, JetBrains_Mono } from 'next/font/google'
import './globals.css'

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  display: 'swap',
})

const instrumentSerif = Instrument_Serif({
  subsets: ['latin'],
  weight: '400',
  style: ['normal', 'italic'],
  variable: '--font-serif',
  display: 'swap',
})

// Sans-serif body face used by the landing hero (matches the Claude Design v1
// prototype — Hanken Grotesk reads softer than Inter at small sizes and gives
// the editorial chrome a magazine-typesetting feel).
const hankenGrotesk = Hanken_Grotesk({
  subsets: ['latin'],
  weight: ['300', '400', '500', '600'],
  variable: '--font-hanken',
  display: 'swap',
})

// Monospace used by the landing eyebrow, footer coordinates, and museum caps.
const jetBrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  variable: '--font-mono',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'Vitality',
  description: 'A personal dashboard built around your goals.',
  applicationName: 'Vitality',
  // Installable PWA: opens fullscreen from the home screen, no Safari chrome.
  // (manifest.ts + app/icon.tsx + app/apple-icon.tsx are auto-linked by Next.)
  appleWebApp: {
    capable: true,
    title: 'Vitality',
    statusBarStyle: 'black-translucent',
  },
  // Modern equivalent of the (deprecated) apple-mobile-web-app-capable meta that
  // `appleWebApp.capable` emits — keeps both so non-iOS browsers stop warning.
  other: { 'mobile-web-app-capable': 'yes' },
  formatDetection: { telephone: false },
}

// Mobile scaling + brand-dark browser/status-bar chrome. viewportFit:'cover'
// lets the standalone app paint under the iPhone notch / home indicator;
// userScalable:false stops double-tap-zoom fighting tap targets in the dense
// logger UI.
export const viewport: Viewport = {
  themeColor: '#04060a',
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: 'cover',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${instrumentSerif.variable} ${hankenGrotesk.variable} ${jetBrainsMono.variable}`}
    >
      <body>{children}</body>
    </html>
  )
}
