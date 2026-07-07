'use client'

// Root error boundary. Renders only if something crashes above the app's normal
// error UI (a root-layout-level failure). Shows a calm on-brand fallback instead
// of a white crash screen. Wire in your own error reporter here if you want one.
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  void error
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '20px',
          background: '#000',
          color: '#f5f5f5',
          fontFamily: 'Inter, system-ui, sans-serif',
          textAlign: 'center',
          padding: '24px',
        }}
      >
        <h2 style={{ fontWeight: 600, fontSize: '20px', margin: 0 }}>Something went wrong on our end.</h2>
        <p style={{ color: '#9a9a9a', fontSize: '14px', maxWidth: '340px', lineHeight: 1.5, margin: 0 }}>
          We have been notified and are on it. Try again in a moment.
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '14px' }}>
          <button
            onClick={() => reset()}
            style={{
              border: '1px solid rgba(120,255,210,.35)',
              background: 'rgba(120,255,210,.08)',
              color: '#7affd2',
              padding: '10px 22px',
              borderRadius: '999px',
              fontSize: '14px',
              cursor: 'pointer',
            }}
          >
            Try again
          </button>
          {/* Hard navigation (not a router Link): the root tree has crashed, so a
              full page load to the dashboard is the reliable escape hatch. */}
          <a
            href="/app"
            style={{
              color: '#9a9a9a',
              fontSize: '13px',
              textDecoration: 'none',
              borderBottom: '1px solid rgba(154,154,154,.3)',
              paddingBottom: '1px',
            }}
          >
            Back to dashboard
          </a>
        </div>
      </body>
    </html>
  )
}
