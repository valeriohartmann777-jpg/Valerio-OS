import { ImageResponse } from 'next/og'

/**
 * Apple touch icon (iOS "Add to Home Screen"). 180×180, fully opaque dark
 * background (iOS ignores transparency and adds its own rounding). Same
 * Vitality gem mark as app/icon.tsx, sized for the smaller canvas.
 */
export const size = { width: 180, height: 180 }
export const contentType = 'image/png'

// Vitality gem mark — the "D · Brilliant" flat-faceted gem (7 facets radiating
// to the culet), solid fills on near-black. viewBox is tight around the gem.
const GEM = `
<svg xmlns="http://www.w3.org/2000/svg" width="150" height="150" viewBox="282 328 460 460">
  <path d="M392 372 L632 372 L724 470 L512 744 L300 470 Z" fill="#1f4d3d"/>
  <path d="M392 372 L300 470 L392 470 Z" fill="#A7F3D0"/>
  <path d="M392 372 L632 372 L632 470 L392 470 Z" fill="#C9F7E1"/>
  <path d="M632 372 L724 470 L632 470 Z" fill="#46B488"/>
  <path d="M300 470 L392 470 L512 744 Z" fill="#6EE7B7"/>
  <path d="M392 470 L512 470 L512 744 Z" fill="#46B488"/>
  <path d="M512 470 L632 470 L512 744 Z" fill="#1f4d3d"/>
  <path d="M632 470 L724 470 L512 744 Z" fill="#1f4d3d"/>
</svg>`

export default function AppleIcon() {
  const gemUri = `data:image/svg+xml;base64,${Buffer.from(GEM).toString('base64')}`
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#04060a',
        }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={gemUri} width={150} height={150} alt="" />
      </div>
    ),
    { ...size },
  )
}
