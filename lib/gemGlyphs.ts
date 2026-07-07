/**
 * Gem glyphs — section marks engraved into the Vitality crystal.
 *
 * Each glyph is a draw function that paints a single architectural stroke
 * (open or simple closed) into a 512² canvas centered at (256, 256). The
 * SectionGem component wraps each draw with the five-layer mint glow stack
 * to match the hero V engraving.
 *
 * Glyphs are mirrored from `design-iterations/crystal-library/index.html`
 * and `public/section-gems-preview.html` — the canonical visual source.
 * Keep them in sync when adding new marks.
 */

export type GlyphDraw = (ctx: CanvasRenderingContext2D) => void
export type GlyphName =
  | 'V' | 'CHECK'
  | 'BAR' | 'SPARK' | 'BOLT' | 'CHART' | 'ASCEND'
  | 'TALLY' | 'DROP' | 'CAPSULE' | 'SPARKLINE' | 'PULSE' | 'SINE' | 'RINGS'
  | 'ROBOT' | 'FLAME' | 'SLIDERS' | 'NOTE'
  | 'ARROW_UP' | 'SCALE'
  | 'FLAG' | 'LISTEN' | 'GIFT' | 'RAYS' | 'TROPHY' | 'STAR' | 'MUSIC'
  | 'ROCKET' | 'CHEVRONS' | 'PLANE'
  | 'STEPS' | 'TIERS' | 'BREAKTHROUGH'
  | 'LINK' | 'RADIAL'

/**
 * An animated glyph draw — same single-stroke convention as GlyphDraw, but
 * parameterised by a loop fraction `t` (0..1). The SectionGem wraps it in the
 * same five-layer mint glow and repaints it each frame. Glyphs that have an
 * entry in ANIMATED_GLYPHS play their continuous loop; everything else bakes
 * once and holds. The static GLYPHS entry for the same name is the resting
 * pose (used as the reduced-motion fallback + the crystal-library mark).
 */
export type AnimatedGlyphDraw = (ctx: CanvasRenderingContext2D, t: number) => void

export const GLYPHS: Record<GlyphName, GlyphDraw> = {
  V: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(146, 174); ctx.lineTo(256, 360); ctx.lineTo(366, 174)
    ctx.stroke()
  },
  CHECK: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(146, 268); ctx.lineTo(226, 348); ctx.lineTo(370, 172)
    ctx.stroke()
  },
  BAR: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(150, 196); ctx.lineTo(196, 196); ctx.lineTo(196, 316); ctx.lineTo(150, 316)
    ctx.closePath(); ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(316, 196); ctx.lineTo(362, 196); ctx.lineTo(362, 316); ctx.lineTo(316, 316)
    ctx.closePath(); ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(196, 256); ctx.lineTo(316, 256)
    ctx.stroke()
  },
  SPARK: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(256, 140); ctx.lineTo(256, 220)
    ctx.moveTo(256, 292); ctx.lineTo(256, 372)
    ctx.moveTo(168, 256); ctx.lineTo(228, 256)
    ctx.moveTo(284, 256); ctx.lineTo(344, 256)
    ctx.stroke()
  },
  BOLT: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(286, 158); ctx.lineTo(216, 256)
    ctx.lineTo(280, 256); ctx.lineTo(228, 358)
    ctx.stroke()
  },
  CHART: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(160, 296); ctx.lineTo(204, 296); ctx.lineTo(204, 360); ctx.lineTo(160, 360)
    ctx.closePath(); ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(234, 232); ctx.lineTo(278, 232); ctx.lineTo(278, 360); ctx.lineTo(234, 360)
    ctx.closePath(); ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(308, 160); ctx.lineTo(352, 160); ctx.lineTo(352, 360); ctx.lineTo(308, 360)
    ctx.closePath(); ctx.stroke()
  },
  ASCEND: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(140, 344); ctx.lineTo(216, 344)
    ctx.lineTo(216, 268); ctx.lineTo(296, 268)
    ctx.lineTo(296, 192); ctx.lineTo(372, 192)
    ctx.stroke()
  },
  TALLY: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(208, 188); ctx.lineTo(208, 324)
    ctx.moveTo(240, 188); ctx.lineTo(240, 324)
    ctx.moveTo(272, 188); ctx.lineTo(272, 324)
    ctx.moveTo(304, 188); ctx.lineTo(304, 324)
    ctx.moveTo(188, 316); ctx.lineTo(324, 196)
    ctx.stroke()
  },
  DROP: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(256, 162)
    ctx.bezierCurveTo(208, 234, 184, 282, 184, 314)
    ctx.bezierCurveTo(184, 354, 218, 376, 256, 376)
    ctx.bezierCurveTo(294, 376, 328, 354, 328, 314)
    ctx.bezierCurveTo(328, 282, 304, 234, 256, 162)
    ctx.closePath(); ctx.stroke()
  },
  CAPSULE: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(196, 218); ctx.lineTo(316, 218)
    ctx.arc(316, 256, 38, -Math.PI / 2, Math.PI / 2)
    ctx.lineTo(196, 294)
    ctx.arc(196, 256, 38, Math.PI / 2, -Math.PI / 2)
    ctx.closePath(); ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(256, 218); ctx.lineTo(256, 294)
    ctx.stroke()
  },
  SPARKLINE: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(140, 316)
    ctx.lineTo(196, 268)
    ctx.lineTo(244, 296)
    ctx.lineTo(300, 220)
    ctx.lineTo(362, 168)
    ctx.stroke()
    ctx.beginPath()
    ctx.arc(362, 168, 12, 0, Math.PI * 2)
    ctx.stroke()
  },
  PULSE: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(140, 256); ctx.lineTo(216, 256)
    ctx.lineTo(238, 296); ctx.lineTo(258, 180)
    ctx.lineTo(282, 320); ctx.lineTo(302, 256)
    ctx.lineTo(372, 256); ctx.stroke()
  },
  SINE: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(140, 256)
    ctx.bezierCurveTo(180, 188, 220, 188, 256, 256)
    ctx.bezierCurveTo(292, 324, 332, 324, 372, 256)
    ctx.stroke()
  },
  RINGS: (ctx) => {
    ctx.beginPath()
    ctx.arc(256, 256, 96, 0, Math.PI * 2); ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(256, 134); ctx.lineTo(256, 174)
    ctx.moveTo(256, 338); ctx.lineTo(256, 378)
    ctx.moveTo(134, 256); ctx.lineTo(174, 256)
    ctx.moveTo(338, 256); ctx.lineTo(378, 256)
    ctx.stroke()
  },
  ROBOT: (ctx) => {
    const x1 = 168, y1 = 168, x2 = 344, y2 = 344, r = 28
    ctx.beginPath()
    ctx.moveTo(x1 + r, y1)
    ctx.lineTo(x2 - r, y1)
    ctx.quadraticCurveTo(x2, y1, x2, y1 + r)
    ctx.lineTo(x2, y2 - r)
    ctx.quadraticCurveTo(x2, y2, x2 - r, y2)
    ctx.lineTo(x1 + r, y2)
    ctx.quadraticCurveTo(x1, y2, x1, y2 - r)
    ctx.lineTo(x1, y1 + r)
    ctx.quadraticCurveTo(x1, y1, x1 + r, y1)
    ctx.closePath(); ctx.stroke()
    ctx.beginPath()
    ctx.arc(220, 256, 14, 0, Math.PI * 2); ctx.stroke()
    ctx.beginPath()
    ctx.arc(292, 256, 14, 0, Math.PI * 2); ctx.stroke()
  },
  FLAME: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(256, 148)
    ctx.bezierCurveTo(326, 232, 340, 272, 340, 308)
    ctx.bezierCurveTo(340, 352, 304, 376, 256, 376)
    ctx.bezierCurveTo(208, 376, 172, 352, 172, 308)
    ctx.bezierCurveTo(172, 272, 186, 232, 256, 148)
    ctx.closePath(); ctx.stroke()
  },
  SLIDERS: (ctx) => {
    const lines = [
      { y: 192, knobX: 308 },
      { y: 256, knobX: 208 },
      { y: 320, knobX: 272 },
    ]
    for (const { y, knobX } of lines) {
      ctx.beginPath()
      ctx.moveTo(152, y); ctx.lineTo(360, y); ctx.stroke()
      ctx.beginPath()
      ctx.arc(knobX, y, 20, 0, Math.PI * 2); ctx.stroke()
    }
  },
  NOTE: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(160, 204); ctx.lineTo(352, 204)
    ctx.moveTo(160, 256); ctx.lineTo(352, 256)
    ctx.moveTo(160, 308); ctx.lineTo(288, 308)
    ctx.stroke()
  },
  // Encourage — a plain upward arrow ("keep going, you're rising").
  ARROW_UP: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(256, 360); ctx.lineTo(256, 168)
    ctx.moveTo(192, 232); ctx.lineTo(256, 162); ctx.lineTo(320, 232)
    ctx.stroke()
  },
  // Consider — a balance scale ("weighing the options").
  SCALE: (ctx) => {
    ctx.beginPath()
    // beam
    ctx.moveTo(112, 188); ctx.lineTo(400, 188)
    // central post + base
    ctx.moveTo(256, 188); ctx.lineTo(256, 388)
    ctx.moveTo(196, 388); ctx.lineTo(316, 388)
    // hanger stub
    ctx.moveTo(256, 188); ctx.lineTo(256, 140)
    ctx.stroke()
    // left pan
    ctx.beginPath()
    ctx.moveTo(112, 188); ctx.lineTo(72, 268); ctx.lineTo(152, 268); ctx.closePath()
    ctx.stroke()
    // right pan
    ctx.beginPath()
    ctx.moveTo(400, 188); ctx.lineTo(360, 268); ctx.lineTo(440, 268); ctx.closePath()
    ctx.stroke()
  },
  // Let's go — a pennant flag on a pole (race-start "go!").
  FLAG: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(184, 148); ctx.lineTo(184, 376)
    ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(184, 168); ctx.lineTo(338, 200); ctx.lineTo(184, 232)
    ctx.stroke()
  },
  // Listening — a source dot + two sound waves ("I hear you"). Kept to two
  // arcs so it reads clean.
  LISTEN: (ctx) => {
    const a = Math.PI * 0.28
    ctx.beginPath(); ctx.arc(180, 256, 16, 0, Math.PI * 2); ctx.stroke()
    ctx.beginPath(); ctx.arc(180, 256, 100, -a, a); ctx.stroke()
    ctx.beginPath(); ctx.arc(180, 256, 168, -a, a); ctx.stroke()
  },
  // Celebrate — a wrapped gift box with a bow ("reward unlocked").
  GIFT: (ctx) => {
    // lid (overhangs the box a touch)
    ctx.beginPath()
    ctx.moveTo(150, 214); ctx.lineTo(362, 214); ctx.lineTo(362, 252); ctx.lineTo(150, 252)
    ctx.closePath(); ctx.stroke()
    // box body
    ctx.beginPath()
    ctx.moveTo(168, 252); ctx.lineTo(168, 392); ctx.lineTo(344, 392); ctx.lineTo(344, 252)
    ctx.stroke()
    // vertical ribbon
    ctx.beginPath()
    ctx.moveTo(256, 214); ctx.lineTo(256, 392); ctx.stroke()
    // bow loops
    ctx.beginPath()
    ctx.moveTo(256, 210); ctx.bezierCurveTo(198, 150, 168, 190, 244, 212); ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(256, 210); ctx.bezierCurveTo(314, 150, 344, 190, 268, 212); ctx.stroke()
  },
  // Cheer — short rays bursting outward around an open center.
  RAYS: (ctx) => {
    const cx = 256, cy = 256, r0 = 118, r1 = 178
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * Math.PI * 2 - Math.PI / 2
      ctx.beginPath()
      ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0)
      ctx.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1)
      ctx.stroke()
    }
  },
  // Win — a trophy cup with handles.
  TROPHY: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(192, 178); ctx.lineTo(320, 178); ctx.lineTo(320, 196)
    ctx.bezierCurveTo(320, 266, 294, 304, 256, 304)
    ctx.bezierCurveTo(218, 304, 192, 266, 192, 196)
    ctx.closePath(); ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(256, 304); ctx.lineTo(256, 346)
    ctx.moveTo(232, 346); ctx.lineTo(280, 346)
    ctx.moveTo(208, 366); ctx.lineTo(304, 366)
    ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(192, 188); ctx.bezierCurveTo(150, 194, 150, 248, 196, 254); ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(320, 188); ctx.bezierCurveTo(362, 194, 362, 248, 316, 254); ctx.stroke()
  },
  // Twinkle — a five-point star.
  STAR: (ctx) => {
    const cx = 256, cy = 262, R = 128, r = 52
    ctx.beginPath()
    for (let i = 0; i < 10; i++) {
      const rad = i % 2 === 0 ? R : r
      const a = -Math.PI / 2 + (i * Math.PI) / 5
      const x = cx + Math.cos(a) * rad, y = cy + Math.sin(a) * rad
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
    }
    ctx.closePath(); ctx.stroke()
  },
  // Groove — an eighth note (dance / come alive).
  MUSIC: (ctx) => {
    ctx.beginPath(); ctx.ellipse(210, 332, 38, 27, -0.35, 0, Math.PI * 2); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(245, 322); ctx.lineTo(245, 164); ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(245, 164)
    ctx.bezierCurveTo(302, 178, 314, 216, 292, 254)
    ctx.stroke()
  },
  // Let's go (variant 1) — a rocket lifting off.
  ROCKET: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(256, 96)
    ctx.bezierCurveTo(320, 176, 320, 272, 296, 336)
    ctx.lineTo(216, 336)
    ctx.bezierCurveTo(192, 272, 192, 176, 256, 96)
    ctx.closePath(); ctx.stroke()
    ctx.beginPath(); ctx.arc(256, 208, 27, 0, Math.PI * 2); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(216, 336); ctx.lineTo(160, 400); ctx.lineTo(216, 368); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(296, 336); ctx.lineTo(352, 400); ctx.lineTo(296, 368); ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(240, 348); ctx.lineTo(240, 392)
    ctx.moveTo(272, 348); ctx.lineTo(272, 392)
    ctx.stroke()
  },
  // Let's go (variant 2) — two chevrons climbing.
  CHEVRONS: (ctx) => {
    ctx.beginPath(); ctx.moveTo(160, 272); ctx.lineTo(256, 192); ctx.lineTo(352, 272); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(160, 368); ctx.lineTo(256, 288); ctx.lineTo(352, 368); ctx.stroke()
  },
  // Let's go (variant 3) — a paper plane gliding forward.
  PLANE: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(416, 112); ctx.lineTo(112, 240); ctx.lineTo(240, 280); ctx.lineTo(272, 384)
    ctx.closePath(); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(240, 280); ctx.lineTo(416, 112); ctx.stroke()
  },
  // Level up (variant 1) — a staircase climbing off a ground line.
  STEPS: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(112, 400); ctx.lineTo(208, 400)
    ctx.lineTo(208, 304); ctx.lineTo(304, 304)
    ctx.lineTo(304, 208); ctx.lineTo(400, 208)
    ctx.lineTo(400, 112)
    ctx.stroke()
    ctx.beginPath(); ctx.moveTo(80, 432); ctx.lineTo(432, 432); ctx.stroke()
  },
  // Level up (variant 2) — three rising tier bars on a baseline.
  TIERS: (ctx) => {
    ctx.beginPath()
    ctx.moveTo(104, 272); ctx.lineTo(184, 272); ctx.lineTo(184, 400); ctx.lineTo(104, 400); ctx.closePath()
    ctx.moveTo(216, 192); ctx.lineTo(296, 192); ctx.lineTo(296, 400); ctx.lineTo(216, 400); ctx.closePath()
    ctx.moveTo(328, 112); ctx.lineTo(408, 112); ctx.lineTo(408, 400); ctx.lineTo(328, 400); ctx.closePath()
    ctx.stroke()
    ctx.beginPath(); ctx.moveTo(80, 416); ctx.lineTo(432, 416); ctx.stroke()
  },
  // Level up (variant 3) — an arrow breaking up through a ceiling line.
  BREAKTHROUGH: (ctx) => {
    ctx.beginPath(); ctx.moveTo(96, 368); ctx.lineTo(416, 368); ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(256, 352); ctx.lineTo(256, 128)
    ctx.moveTo(176, 208); ctx.lineTo(256, 112); ctx.lineTo(336, 208)
    ctx.stroke()
  },
  // Connected — two nodes joined by a wire. The resting pose of the LINK
  // animation (see ANIMATED_GLYPHS.LINK): used for the crystal library + as
  // the reduced-motion fallback when the looping link can't play.
  LINK: (ctx) => {
    ctx.beginPath(); ctx.moveTo(LK_LX + LK_R, LK_Y); ctx.lineTo(LK_RX - LK_R, LK_Y); ctx.stroke()
    ctx.beginPath(); ctx.arc(LK_LX, LK_Y, LK_R, 0, Math.PI * 2); ctx.stroke()
    ctx.beginPath(); ctx.arc(LK_RX, LK_Y, LK_R, 0, Math.PI * 2); ctx.stroke()
  },
  // The radial — a progress ring with the check resolved inside it. Resting
  // pose of the RADIAL animation (the quiz mark): a questionnaire that fills
  // to full and confirms. Sibling to LINK; shares the ring + check-inside
  // vocabulary, but earns its check by sweeping round rather than by a wire.
  RADIAL: (ctx) => {
    ctx.beginPath(); ctx.arc(256, 256, RD_R, 0, Math.PI * 2); ctx.stroke()
    const s = RD_CHECK
    ctx.beginPath()
    ctx.moveTo(256 - 15 * s, 256 + 2 * s); ctx.lineTo(256 - 4 * s, 256 + 13 * s); ctx.lineTo(256 + 18 * s, 256 - 14 * s)
    ctx.stroke()
  },
}

/* ── The link → check loop ─────────────────────────────────────────────────
   Engraving stroke for the wearable-pairing congrats: a pulse charges in the
   left node, glides the wire, lands in the right node, and a check draws
   inside it, holds, dissolves, and loops forever. Ported 1:1 from the locked
   motion spec in public/vitals-link-final.html — keep the timeline constants
   in sync if that spec changes. Drawn in the same 512² / centred-(256,256)
   space as the static glyphs, so it composes with the same glow stack. */

const lerp = (a: number, b: number, t: number) => a + (b - a) * t
const clamp01 = (x: number) => Math.max(0, Math.min(1, x))
const span = (t: number, a: number, b: number) => clamp01((t - a) / (b - a))
// smootherstep — zero velocity at both ends, no choppy starts/stops
const ss = (t: number) => { t = clamp01(t); return t * t * t * (t * (6 * t - 15) + 10) }
// easeOut cubic — swift launch, soft landing
const easeOut = (p: number) => { p = clamp01(p); return 1 - Math.pow(1 - p, 3) }

function lkRing(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, alpha: number) {
  if (alpha <= 0 || r <= 0) return
  ctx.save(); ctx.globalAlpha = alpha
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke()
  ctx.restore()
}
function lkSeg(ctx: CanvasRenderingContext2D, x1: number, y1: number, x2: number, y2: number, alpha: number) {
  if (alpha <= 0) return
  ctx.save(); ctx.globalAlpha = alpha
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke()
  ctx.restore()
}
function lkArc(ctx: CanvasRenderingContext2D, cx: number, cy: number, r: number, a0: number, a1: number, alpha: number) {
  if (alpha <= 0 || r <= 0 || a1 <= a0) return
  ctx.save(); ctx.globalAlpha = Math.min(1, alpha)
  ctx.beginPath(); ctx.arc(cx, cy, r, a0, a1); ctx.stroke()
  ctx.restore()
}

// Geometry (512² space, centred on Y=256). Sized to fill the face like the V
// so the mark — and the check inside the right node — read large.
const LK_LX = 128, LK_RX = 384, LK_Y = 256, LK_R = 30
// Timeline — fractions of one loop. Snappy, with eased crossfades so the
// pulse hands straight off to the check (overlapping window, no dead gap).
const LK_CHARGE = 0.13, LK_ARRIVE = 0.50, LK_HANDOFF = 0.06, LK_CHECKDONE = 0.74, LK_HOLD = 0.88
const lkPulseX = (p: number) => lerp(LK_LX + LK_R, LK_RX - LK_R, ss(p)) // eases out of L, into R

function lkCheck(ctx: CanvasRenderingContext2D, cx: number, cy: number, scale: number, reveal: number, alpha: number) {
  if (reveal <= 0 || alpha <= 0) return
  const pts = [
    [cx - 15 * scale, cy + 2 * scale],
    [cx - 4 * scale, cy + 13 * scale],
    [cx + 18 * scale, cy - 14 * scale],
  ]
  let total = 0; const slen: number[] = []
  for (let i = 1; i < pts.length; i++) {
    const l = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
    slen.push(l); total += l
  }
  const rev = reveal * total
  ctx.save(); ctx.globalAlpha = alpha
  ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1])
  let acc = 0
  for (let i = 1; i < pts.length; i++) {
    if (acc + slen[i - 1] <= rev) { ctx.lineTo(pts[i][0], pts[i][1]); acc += slen[i - 1] }
    else { const r = (rev - acc) / slen[i - 1]; ctx.lineTo(lerp(pts[i - 1][0], pts[i][0], r), lerp(pts[i - 1][1], pts[i][1], r)); break }
  }
  ctx.stroke(); ctx.restore()
}

const drawLink: AnimatedGlyphDraw = (ctx, t) => {
  // ── the wire (always linked) ──
  ctx.beginPath(); ctx.moveTo(LK_LX + LK_R, LK_Y); ctx.lineTo(LK_RX - LK_R, LK_Y); ctx.stroke()

  // ── left node: smooth charge swell, then an emit ripple as the pulse leaves ──
  const chg = span(t, 0, LK_CHARGE)
  const leftSwell = 1 + 0.16 * Math.sin(ss(chg) * Math.PI)
  lkRing(ctx, LK_LX, LK_Y, LK_R * leftSwell, 0.85)
  const emit = span(t, LK_CHARGE - 0.02, LK_CHARGE + 0.12)
  if (emit > 0 && emit < 1) lkRing(ctx, LK_LX, LK_Y, lerp(LK_R, LK_R + 30, ss(emit)), (1 - ss(emit)) * 0.75)

  // ── right ring: rest → gentle swell to host the check → eases back ──
  let rr = LK_R
  if (t >= LK_ARRIVE - 0.08 && t < LK_ARRIVE) rr = lerp(LK_R, LK_R + 10, ss(span(t, LK_ARRIVE - 0.08, LK_ARRIVE)))
  else if (t >= LK_ARRIVE && t < LK_HOLD) rr = LK_R + 10
  else if (t >= LK_HOLD) rr = lerp(LK_R + 10, LK_R, ss(span(t, LK_HOLD, 1)))
  lkRing(ctx, LK_RX, LK_Y, rr, 0.85)
  // soft arrival halo, peaking right as the pulse lands
  const ack = span(t, LK_ARRIVE - 0.06, LK_ARRIVE + 0.12)
  if (ack > 0 && ack < 1) lkRing(ctx, LK_RX, LK_Y, lerp(LK_R, LK_R + 38, ss(ack)), (1 - ss(ack)) * 0.6)

  // ── travelling pulse: comet streak + head, fading out across the handoff ──
  if (t >= LK_CHARGE && t < LK_ARRIVE + LK_HANDOFF) {
    const p = span(t, LK_CHARGE, LK_ARRIVE)
    const head = lkPulseX(clamp01(p))
    const env = ss(span(t, LK_CHARGE, LK_CHARGE + 0.06)) * (1 - ss(span(t, LK_ARRIVE - 0.02, LK_ARRIVE + LK_HANDOFF)))
    const tailLen = lerp(50, 105, ss(clamp01(p * 1.6)))
    const tailX = Math.max(LK_LX + LK_R, head - tailLen)
    lkSeg(ctx, tailX, LK_Y, head, LK_Y, 0.42 * env) // comet streak
    lkRing(ctx, head, LK_Y, 16, 0.95 * env)         // bright head
  }

  // ── confirm "click" — a soft halo blooms exactly as the check finishes,
  //    so the moment reads as locked-in (a deliberate beat, not just a fade) ──
  const conf = span(t, LK_CHECKDONE - 0.05, LK_CHECKDONE + 0.16)
  if (conf > 0 && conf < 1) lkRing(ctx, LK_RX, LK_Y, lerp(LK_R, LK_R + 24, ss(conf)), (1 - ss(conf)) * 0.5)

  // ── the check, growing out of where the pulse landed (overlaps the handoff) ──
  if (t >= LK_ARRIVE - 0.02) {
    const rev = ss(span(t, LK_ARRIVE - 0.02, LK_CHECKDONE))
    const fadeIn = ss(span(t, LK_ARRIVE - 0.02, LK_ARRIVE + LK_HANDOFF))
    const fadeOut = 1 - ss(span(t, LK_HOLD, 1))
    const breath = (t >= LK_CHECKDONE && t < LK_HOLD) ? 0.94 + 0.06 * Math.sin(span(t, LK_CHECKDONE, LK_HOLD) * Math.PI * 2) : 1
    // a brief brightness flash right at completion (globalAlpha clamps at 1)
    const flash = 1 + 0.45 * Math.max(0, 1 - Math.abs(t - LK_CHECKDONE) / 0.05)
    lkCheck(ctx, LK_RX, LK_Y, 0.95, rev, fadeIn * fadeOut * breath * flash)
  }
}

/* ── The radial loop (the quiz mark) ───────────────────────────────────────
   Sibling to the link: a progress ring sweeps swiftly to full (easeOut), snaps
   to confirm, and the check draws INSIDE the ring — the same check-inside
   signature as the link — then breathes and dissolves, forever. Earns its
   check by completing the ring rather than by a travelling wire. */

const RD_R = 128, RD_CHECK = 1.85
const RD_SWEEP0 = 0.06, RD_SWEEPDONE = 0.45
const RD_CHK0 = 0.43, RD_CHKDONE = 0.64, RD_HOLD = 0.84
const RD_A0 = -Math.PI / 2 // start at 12 o'clock

const drawRadial: AnimatedGlyphDraw = (ctx, t) => {
  const cx = 256, cy = 256
  const fade = 1 - ss(span(t, RD_HOLD, 1))

  // faint track the ring fills into
  lkRing(ctx, cx, cy, RD_R, 0.12 * fade)

  const prog = easeOut(span(t, RD_SWEEP0, RD_SWEEPDONE))
  if (prog < 1) {
    // sweeping arc + a brighter comet head and a leading dot
    const a1 = RD_A0 + Math.PI * 2 * prog
    lkArc(ctx, cx, cy, RD_R, RD_A0, a1, 0.82 * fade)
    const lead = Math.min(a1 - RD_A0, 0.42)
    lkArc(ctx, cx, cy, RD_R, a1 - lead, a1, 0.55 * fade)
    lkRing(ctx, cx + Math.cos(a1) * RD_R, cy + Math.sin(a1) * RD_R, 8.5, 0.95 * fade)
  } else {
    // settled container ring, with a gentle breath under the check
    const breathR = (t >= RD_CHKDONE && t < RD_HOLD) ? RD_R * (1 + 0.012 * Math.sin(span(t, RD_CHKDONE, RD_HOLD) * Math.PI * 2)) : RD_R
    lkRing(ctx, cx, cy, breathR, 0.85 * fade)
  }

  // confirm "snap" — a soft halo blooms out the instant the ring closes
  const conf = span(t, RD_SWEEPDONE - 0.02, RD_SWEEPDONE + 0.2)
  if (conf > 0 && conf < 1) lkRing(ctx, cx, cy, lerp(RD_R, RD_R + 26, ss(conf)), (1 - ss(conf)) * 0.5 * fade)

  // the check, drawn INSIDE the ring (signature shared with the link)
  if (t >= RD_CHK0) {
    const rev = ss(span(t, RD_CHK0, RD_CHKDONE))
    const breath = (t >= RD_CHKDONE && t < RD_HOLD) ? 0.94 + 0.06 * Math.sin(span(t, RD_CHKDONE, RD_HOLD) * Math.PI * 2) : 1
    const flash = 1 + 0.45 * Math.max(0, 1 - Math.abs(t - RD_CHKDONE) / 0.045)
    lkCheck(ctx, cx, cy, RD_CHECK, rev, fade * breath * flash)
  }
}

/**
 * Animated glyphs — names here play a continuous loop on the gem instead of
 * baking a single static texture. The matching GLYPHS[name] entry is the
 * resting pose (reduced-motion fallback). Loop period + speed are owned by the
 * gem; the draw fn just maps a 0..1 phase to a frame.
 */
export const ANIMATED_GLYPHS: Partial<Record<GlyphName, AnimatedGlyphDraw>> = {
  LINK: drawLink,
  RADIAL: drawRadial,
}

/** Per-glyph loop tuning.
 *  - `speed` is a wall-time divisor (higher = calmer/slower).
 *  - `period` is the loop length in seconds.
 *  - `holdLoops` is how many full loops to play before flicking to the V.
 *  - `brandHold` is how long (s) the V shows before flicking back.
 *  - `glitchDur` is the section⇄V transition length (s); animated marks use a
 *    smooth cross-dissolve over this, longer = more deliberate/professional. */
export type AnimLoop = {
  period: number
  speed: number
  holdLoops?: number
  brandHold?: number
  glitchDur?: number
}
export const ANIM_LOOP: Record<string, AnimLoop> = {
  // swift, loops several times, then a quick flick to V
  LINK: { period: 2.3, speed: 0.78, holdLoops: 4, brandHold: 1.8, glitchDur: 0.55 },
  // slow + deliberate: one full loop, then a slow dissolve to V and back
  RADIAL: { period: 2.4, speed: 1.5, holdLoops: 1, brandHold: 1.6, glitchDur: 1.0 },
}
export const DEFAULT_LOOP: AnimLoop = { period: 2.3, speed: 1, holdLoops: 4, brandHold: 1.8, glitchDur: 0.55 }
