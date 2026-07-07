import React from 'react'

/**
 * Canonical Vitality glyphs — flat SVG ports of the crystal-library
 * canvas paths (design-iterations/crystal-library/index.html). Used as
 * the per-quiz icon that overlays the V crystal on the celebration
 * screen: the quiz's own mark flickers out and the CHECK glyph
 * flickers in to mark the moment.
 *
 * All glyphs share the source library's 512² coord system so stroke
 * weights and proportions match across the catalogue.
 */
export type GlyphKey =
  | 'v'
  | 'check'
  | 'rings'
  | 'dot'
  | 'chevron'
  | 'drop'
  | 'plus'
  | 'bolt'
  | 'bar'

const GLYPH_BODIES: Record<GlyphKey, React.ReactNode> = {
  v: <path d="M 146 174 L 256 360 L 366 174" />,
  check: <path d="M 146 268 L 226 348 L 370 172" />,
  rings: (
    <>
      <circle cx="256" cy="256" r="96" />
      <path d="M 256 134 L 256 174 M 256 338 L 256 378 M 134 256 L 174 256 M 338 256 L 378 256" />
    </>
  ),
  dot: (
    <>
      <circle cx="256" cy="256" r="96" />
      <circle cx="256" cy="256" r="22" />
    </>
  ),
  chevron: <path d="M 166 304 L 256 216 L 346 304" />,
  drop: <path d="M 256 162 C 208 234 184 282 184 314 C 184 354 218 376 256 376 C 294 376 328 354 328 314 C 328 282 304 234 256 162 Z" />,
  plus: <path d="M 256 160 L 256 352 M 160 256 L 352 256" />,
  bolt: <path d="M 286 158 L 216 256 L 280 256 L 228 358" />,
  bar: <path d="M 150 196 L 196 196 L 196 316 L 150 316 Z M 316 196 L 362 196 L 362 316 L 316 316 Z M 196 256 L 316 256" />,
}

interface QuizGlyphProps {
  glyph: GlyphKey
  className?: string
  /** Stroke width in 512-unit space. Default tuned for ~180px render. */
  strokeWidth?: number
}

export function QuizGlyph({ glyph, className, strokeWidth = 26 }: QuizGlyphProps) {
  return (
    <svg
      viewBox="0 0 512 512"
      width="46%"
      height="46%"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      {GLYPH_BODIES[glyph]}
    </svg>
  )
}
