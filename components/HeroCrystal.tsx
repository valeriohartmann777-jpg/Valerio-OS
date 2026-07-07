'use client'

import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import type { GlyphKey } from '@/lib/quizzes/glyphs'
import { GLYPHS } from '@/lib/gemGlyphs'

/**
 * HeroCrystal — Three.js icosahedron centerpiece for the landing hero.
 *
 * Direct port of `design-iterations/v1/project/crystal.js` (the Claude Design
 * v1 prototype) into a Next.js client component. Vanilla Three.js, no R3F,
 * because the prototype was already framework-free and r3f adds bundle size
 * without buying us anything for a single static scene.
 *
 * The component mounts a `<canvas>` and runs the scene against it. Cleanup
 * disposes geometry / material / textures / PMREM and removes the resize
 * and pointer listeners. SSR-disabled via the dynamic() wrapper in the
 * landing page so Three.js doesn't enter the server bundle.
 *
 * Behavior preserved from v1:
 *   • Procedural equirect → PMREM environment (no HDRI download)
 *   • Mint glass MeshPhysicalMaterial + cool-mint wireframe overlay
 *   • Warm key + cool-mint rim + faint underglow fill lights
 *   • Slow Y rotation + sin X wobble + spring-eased cursor tilt
 *   • prefers-reduced-motion freezes all animation
 *   • Falls back gracefully to the SVG fallback (sibling element in the
 *     landing) by adding `crystal-fallback` to <html> if WebGL init throws
 */

const PARAMS = {
  rotationSpeed: 0.08,
  wobbleAmplitude: 0.10,
  cursorTilt: 0.18,
  spring: 0.06,
  mintTint: 0.12,
  roughness: 0.08,
  transmission: 0.82,
  thickness: 1.8,
  ior: 1.55,
  keyWarmth: 0.55,
}

const MINT = new THREE.Color('#6EE7B7')
const WARM = new THREE.Color('#FFE2B5')
const COOL_MINT = new THREE.Color('#A7F3D0')
const NEUTRAL = new THREE.Color('#F2FFF8')

// Gem colorways for the coach family. Each retints the transmission
// attenuation, emissive, wireframe, glass, and the two mint env-gradients so
// the whole gem shifts hue cohesively (mint is the canonical default).
export type GemTint = 'mint' | 'amber' | 'iris'
const TINTS: Record<GemTint, {
  glass: string | null
  atten: string
  emissive: string
  wire: number
  env1: [string, string, string]
  env3: [string, string]
}> = {
  mint:  { glass: null,      atten: '#6EE7B7', emissive: '#0d4a36', wire: 0xa7f3d0,
           env1: ['rgba(180,255,220,1)', 'rgba(110,231,183,0.45)', 'rgba(110,231,183,0)'], env3: ['rgba(140,220,190,0.9)', 'rgba(140,220,190,0)'] },
  amber: { glass: '#F2DBB0', atten: '#E8A33D', emissive: '#3a2406', wire: 0xffd9a0,
           env1: ['rgba(255,240,205,1)', 'rgba(245,185,95,0.45)', 'rgba(245,185,95,0)'], env3: ['rgba(225,175,95,0.85)', 'rgba(225,175,95,0)'] },
  iris:  { glass: '#D8CFFF', atten: '#8E7BFF', emissive: '#1c1640', wire: 0xc7bcff,
           env1: ['rgba(228,218,255,1)', 'rgba(150,125,255,0.45)', 'rgba(150,125,255,0)'], env3: ['rgba(170,150,235,0.85)', 'rgba(170,150,235,0)'] },
}

function computeGlassColor() {
  return NEUTRAL.clone().lerp(MINT, PARAMS.mintTint)
}

function computeKeyColor() {
  return MINT.clone().lerp(WARM, PARAMS.keyWarmth)
}

interface HeroCrystalProps {
  /** Geometry to render. Defaults to icosahedron (the original gem). */
  shape?: 'icosahedron' | 'dodecahedron' | 'tetrahedron' | 'octahedron'
  /** Colorway. Default mint (the canonical gem). amber + iris are for the
   *  coach family — they retint glass / attenuation / emissive / wire / env. */
  tint?: GemTint
  /** Render mode.
   *   - 'landing' (default): the original behavior — slow Y rotation +
   *     cursor tilt + cycling per-face V flicker. Byte-identical to the
   *     landing-page gem.
   *   - 'character': the gem becomes a Destiny-Ghost-style companion.
   *     One face stays locked DEAD-ON toward the camera (no continuous
   *     spin, gem is pre-rotated so the active face's normal aligns
   *     with +Z). The V is on ~90% of the time with subtle flicker,
   *     and the body does sporadic small rotation jolts + a periodic
   *     friendly nod so the character feels alive and friendly. Every
   *     ~6–9s the gem enters a brief "happy" state: eyebrows fade in
   *     above the V and an onHappyStart callback fires so the host
   *     page can emit a synchronized particle burst. */
  mode?: 'landing' | 'character'
  /** Fires once each time the gem enters its happy state in character
   *  mode. The welcome screen uses it to trigger the green-energy
   *  particle burst in sync with the eyebrow expression. Ignored in
   *  landing mode. */
  onHappyStart?: () => void
  /** When true, skip the V glyph entirely so a DOM overlay can own the
   *  crystal face without ghosting against the baked-in V. Used by the
   *  quiz celebration screen, which animates a per-quiz icon then a
   *  check mark on top of the bare crystal. */
  hideGlyph?: boolean
  /** Which glyph the face-cycling plane paints. Accepts any canonical
   *  Vitality glyph (see GlyphKey in lib/quizzes/glyphs.tsx). Defaults
   *  to 'v' so the landing/hero gem is unchanged. Ignored when
   *  hideGlyph is true. */
  glyph?: GlyphKey
  /** Optional secondary glyph. When set, the face-cycling plane
   *  alternates between `glyph` and `secondaryGlyph` on each new
   *  face cycle — so a quiz celebration can show "check ... mentor
   *  icon ... check ... mentor icon" without an HTML overlay. */
  secondaryGlyph?: GlyphKey
  /** Optional control handle for the gem-library lab. When provided, the
   *  component assigns an imperative trigger fn to controlRef.current that
   *  plays a named scripted "move" (e.g. 'floatHappy') on the gem. Only
   *  active in character mode. Left unset everywhere in production, so the
   *  /welcome + landing gems are completely unaffected by it. */
  controlRef?: { current: ((move: string) => void) | null }
  /** Fires once per scripted move, the moment that move's mood glyph
   *  flickers onto the face (e.g. the "?" for 'curious'). The lab uses it to
   *  fire a glyph-synchronized burst. Passes the move name so the host can
   *  pick an effect. Character-mode only; unset in production. */
  onMoveGlyph?: (move: string, phase?: string) => void
  /** Loading / thinking state. When true the gem drops into a calm looping
   *  "breath": gentle scale + emissive swell on a slightly-organic ~2s beat,
   *  jolts + happy events suppressed so it reads patient, not anxious. When it
   *  flips back to false the gem plays one bright resolve pop then settles into
   *  the normal idle. Reusable on any page (the macro tracker pulses its header
   *  gem during photo analysis). Character mode only; unset in production gems. */
  loading?: boolean
  /** Fires on each loading beat ('beat') and once when loading completes
   *  ('resolve'). The host renders the sonar ring DOM on these so the ring can
   *  extend past the gem/canvas bounds (same pattern as PeakGem's surge ring). */
  onLoadingBeat?: (phase: 'beat' | 'resolve') => void
  /** Ambient repertoire — the pool of gentle moves a character-mode gem plays
   *  on its own when idle (every ~11-19s). This is the shared "life" of every
   *  Vitality gem: add a move to AMBIENT_DEFAULT and every gem app-wide inherits
   *  it instantly. Pass a custom list to flavor a gem (e.g. a coach), or `null`
   *  to disable autonomous moves (host drives everything). Character mode only. */
  ambient?: string[] | null
}

/**
 * The shared "life" of every Vitality gem. A character-mode gem idly plays a
 * random move from this pool every ~11-19s. Keep these GENTLE (no big
 * celebrations — those fire on real events). Add a move here and every gem in
 * the app inherits it the next time the engine loads — no per-page wiring.
 */
// NOTE: 'curious' (V -> "?") is deliberately NOT in the idle pool — a gem
// randomly flashing "?" at the user reads as confused / rude. It stays a
// host-driven move only (e.g. Echo's coaching "thinking" signature).
export const AMBIENT_DEFAULT = ['happyHello', 'nod', 'focus', 'groove', 'wobble', 'idea', 'spin']

export default function HeroCrystal({
  shape = 'icosahedron',
  tint = 'mint',
  mode = 'landing',
  onHappyStart,
  hideGlyph = false,
  glyph = 'v',
  secondaryGlyph,
  controlRef,
  onMoveGlyph,
  loading = false,
  onLoadingBeat,
  ambient,
}: HeroCrystalProps = {}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  // Bridge the latest onHappyStart into the Three.js loop without
  // forcing it to remount when the callback identity changes.
  const onHappyStartRef = useRef<typeof onHappyStart>(onHappyStart)
  useEffect(() => { onHappyStartRef.current = onHappyStart }, [onHappyStart])
  const onMoveGlyphRef = useRef<typeof onMoveGlyph>(onMoveGlyph)
  useEffect(() => { onMoveGlyphRef.current = onMoveGlyph }, [onMoveGlyph])
  // Loading is read live inside the Three.js loop, so bridge it (and its beat
  // callback) through refs — toggling loading must NOT remount the WebGL gem.
  const loadingRef = useRef<boolean>(loading)
  useEffect(() => { loadingRef.current = loading }, [loading])
  const onLoadingBeatRef = useRef<typeof onLoadingBeat>(onLoadingBeat)
  useEffect(() => { onLoadingBeatRef.current = onLoadingBeat }, [onLoadingBeat])
  // Ambient move pool, bridged so changing it never remounts the gem.
  // undefined → default pool; null → autonomous moves off.
  const ambientRef = useRef<string[] | null>(ambient === undefined ? AMBIENT_DEFAULT : ambient)
  useEffect(() => { ambientRef.current = ambient === undefined ? AMBIENT_DEFAULT : ambient }, [ambient])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true })
    } catch (err) {
      console.warn('[HeroCrystal] WebGL init failed, leaving SVG fallback in place.', err)
      document.documentElement.classList.add('crystal-fallback')
      return
    }

    renderer.setClearColor(0x000000, 0)
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.05

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(28, 1, 0.1, 100)
    camera.position.set(0, 0.05, 4.6)
    camera.lookAt(0, 0, 0)

    /* ── Procedural PMREM environment ─────────────────────────────
       Cheaper than an HDRI. The transmissive material needs something
       in the env to refract; pure black leaves the glass invisible. */
    const envCanvas = document.createElement('canvas')
    envCanvas.width = 1024
    envCanvas.height = 512
    const pal = TINTS[tint]
    const ctx = envCanvas.getContext('2d')!
    ctx.fillStyle = '#0a1a18'
    ctx.fillRect(0, 0, envCanvas.width, envCanvas.height)

    const g1 = ctx.createRadialGradient(280, 170, 0, 280, 170, 520)
    g1.addColorStop(0, pal.env1[0])
    g1.addColorStop(0.4, pal.env1[1])
    g1.addColorStop(1, pal.env1[2])
    ctx.fillStyle = g1
    ctx.fillRect(0, 0, envCanvas.width, envCanvas.height)

    const g2 = ctx.createRadialGradient(760, 200, 0, 760, 200, 500)
    g2.addColorStop(0, 'rgba(255, 235, 195, 1)')
    g2.addColorStop(0.4, 'rgba(255, 220, 170, 0.42)')
    g2.addColorStop(1, 'rgba(255, 220, 170, 0)')
    ctx.fillStyle = g2
    ctx.fillRect(0, 0, envCanvas.width, envCanvas.height)

    const g3 = ctx.createRadialGradient(512, 470, 0, 512, 470, 380)
    g3.addColorStop(0, pal.env3[0])
    g3.addColorStop(1, pal.env3[1])
    ctx.fillStyle = g3
    ctx.fillRect(0, 0, envCanvas.width, envCanvas.height)

    const g4 = ctx.createRadialGradient(420, 90, 0, 420, 90, 80)
    g4.addColorStop(0, 'rgba(255,255,255,.85)')
    g4.addColorStop(1, 'rgba(255,255,255,0)')
    ctx.fillStyle = g4
    ctx.fillRect(0, 0, envCanvas.width, envCanvas.height)

    const envSource = new THREE.CanvasTexture(envCanvas)
    envSource.mapping = THREE.EquirectangularReflectionMapping
    envSource.colorSpace = THREE.SRGBColorSpace
    envSource.needsUpdate = true

    const pmrem = new THREE.PMREMGenerator(renderer)
    const envTex = pmrem.fromEquirectangular(envSource).texture
    envSource.dispose()
    scene.environment = envTex

    /* ── Crystal mesh + wireframe ─────────────────────────────── */
    const geo =
      shape === 'dodecahedron' ? new THREE.DodecahedronGeometry(1, 0)
      : shape === 'tetrahedron' ? new THREE.TetrahedronGeometry(1, 0)
      : shape === 'octahedron' ? new THREE.OctahedronGeometry(1, 0)
      : new THREE.IcosahedronGeometry(1, 0)
    const mat = new THREE.MeshPhysicalMaterial({
      color: pal.glass ? new THREE.Color(pal.glass) : computeGlassColor(),
      transmission: PARAMS.transmission,
      thickness: PARAMS.thickness,
      ior: PARAMS.ior,
      roughness: PARAMS.roughness,
      metalness: 0,
      attenuationColor: new THREE.Color(pal.atten),
      attenuationDistance: 1.0,
      clearcoat: 0.7,
      clearcoatRoughness: 0.04,
      envMapIntensity: 2.8,
      transparent: true,
      side: THREE.DoubleSide,
      emissive: new THREE.Color(pal.emissive),
      emissiveIntensity: 0.5,
    })
    const mesh = new THREE.Mesh(geo, mat)
    scene.add(mesh)

    const edgesGeo = new THREE.EdgesGeometry(geo, 1)
    const wireMat = new THREE.LineBasicMaterial({
      color: pal.wire,
      transparent: true,
      opacity: 0.7,
      depthTest: false,
    })
    const wire = new THREE.LineSegments(edgesGeo, wireMat)
    wire.renderOrder = 2
    wire.scale.setScalar(1.001)
    mesh.add(wire)

    /* ── Per-face flickering V engraving ─────────────────────────
       The Vitality V appears on whichever face is currently dead-on
       facing the camera. Cycle per face:

         1. A face rotates into peak alignment (its world-space normal
            dot-product with +Z crosses CLAIM_DOT) → claim it.
         2. Slow flicker ON over FADE_IN_DUR.
         3. HOLD_DUR of subtle micro-flicker — per-face pattern so the
            character feels different face-to-face.
         4. Fast strobe OFF over FADE_OUT_DUR.
         5. Idle until a NEW face crosses CLAIM_DOT.

       A geometric visibility envelope (smoothstep over the active
       face's current dot product) gates the whole cycle — so the V
       is guaranteed to be off before the face rotates out of view.

       The V plane is positioned at the face center, oriented to the
       face normal, AND up-corrected so the V always reads upright
       (tip pointing down within the face plane, world-up projected).
       The result: V is always symmetrical and engraved in the dead
       middle of whichever face is currently in frame. */

    /* Three.js stores every polyhedron face as triangles. Icosahedron
       faces are already triangles (1 triangle = 1 face). Dodecahedron
       faces are pentagons — each gets fan-triangulated into 3
       triangles that all share the same surface normal. To get the
       TRUE face center for a dodecahedron (and any future >3-vertex
       polyhedron), merge consecutive triangles that share a normal,
       then compute the centroid from the unique vertices of the
       merged face. For icosahedron this is a no-op (each triangle
       has a unique normal, no merging happens). */
    type FaceData = { center: THREE.Vector3; normal: THREE.Vector3 }
    const faces: FaceData[] = []
    const faceVerts: THREE.Vector3[][] = []
    const NORMAL_MATCH = 0.999 // dot product threshold for "same normal"

    const posAttr = geo.attributes.position
    for (let i = 0; i < posAttr.count; i += 3) {
      const v0 = new THREE.Vector3().fromBufferAttribute(posAttr, i)
      const v1 = new THREE.Vector3().fromBufferAttribute(posAttr, i + 1)
      const v2 = new THREE.Vector3().fromBufferAttribute(posAttr, i + 2)
      const normal = new THREE.Vector3()
        .subVectors(v1, v0)
        .cross(new THREE.Vector3().subVectors(v2, v0))
        .normalize()

      // Find an existing face whose normal matches (same flat face)
      let matchIdx = -1
      for (let j = 0; j < faces.length; j++) {
        if (faces[j].normal.dot(normal) > NORMAL_MATCH) {
          matchIdx = j
          break
        }
      }
      if (matchIdx >= 0) {
        faceVerts[matchIdx].push(v0, v1, v2)
      } else {
        faces.push({ center: new THREE.Vector3(), normal })
        faceVerts.push([v0, v1, v2])
      }
    }

    // For each merged face, dedupe vertices and compute the true centroid.
    for (let i = 0; i < faces.length; i++) {
      const unique: THREE.Vector3[] = []
      for (const v of faceVerts[i]) {
        if (!unique.some((u) => u.distanceTo(v) < 1e-5)) unique.push(v)
      }
      const center = new THREE.Vector3()
      for (const v of unique) center.add(v)
      center.divideScalar(unique.length)
      faces[i].center = center
    }

    /* 512² canvas — needed so the outer glow layers (shadowBlur up
       to 90) don't clip against the texture edge. */
    const vCanvas = document.createElement('canvas')
    vCanvas.width = 512
    vCanvas.height = 512
    const vCtx = vCanvas.getContext('2d')!
    vCtx.lineCap = 'round'
    vCtx.lineJoin = 'round'

    /* Five-layer glow stack — outermost haze, outer halo, mid bloom,
       inner glow, crisp white core. Skinnier strokes than v1 + wider
       blur radii give the glyph a more mystical, ethereal feel. Glyph
       is centered in the 512² canvas so when mapped to a plane and
       placed at a face center, it sits dead-center on the face.

       The path coords are pulled from lib/quizzes/glyphs.tsx so the
       face-painted check matches the canonical Vitality glyph library
       exactly — no drift between the celebration screen's flickering
       face mark and the same glyph rendered elsewhere as SVG. */
    // Trace any canonical glyph path on the given canvas context. The
    // 512² coord system mirrors GLYPH_BODIES in lib/quizzes/glyphs.tsx
    // exactly, so the face-painted mark matches its SVG counterpart
    // everywhere else (welcome checklist, quiz progress, etc.) — no
    // drift between the celebration screen's flickering face mark and
    // the same glyph rendered as flat SVG elsewhere.
    function tracePathOn(c: CanvasRenderingContext2D, path: GlyphKey) {
      c.beginPath()
      switch (path) {
        case 'check':
          c.moveTo(146, 268); c.lineTo(226, 348); c.lineTo(370, 172)
          break
        case 'rings':
          c.arc(256, 256, 96, 0, Math.PI * 2)
          c.stroke(); c.beginPath()
          c.moveTo(256, 134); c.lineTo(256, 174)
          c.moveTo(256, 338); c.lineTo(256, 378)
          c.moveTo(134, 256); c.lineTo(174, 256)
          c.moveTo(338, 256); c.lineTo(378, 256)
          break
        case 'dot':
          c.arc(256, 256, 96, 0, Math.PI * 2)
          c.stroke(); c.beginPath()
          c.arc(256, 256, 22, 0, Math.PI * 2)
          break
        case 'chevron':
          c.moveTo(166, 304); c.lineTo(256, 216); c.lineTo(346, 304)
          break
        case 'drop':
          c.moveTo(256, 162)
          c.bezierCurveTo(208, 234, 184, 282, 184, 314)
          c.bezierCurveTo(184, 354, 218, 376, 256, 376)
          c.bezierCurveTo(294, 376, 328, 354, 328, 314)
          c.bezierCurveTo(328, 282, 304, 234, 256, 162)
          break
        case 'plus':
          c.moveTo(256, 160); c.lineTo(256, 352)
          c.moveTo(160, 256); c.lineTo(352, 256)
          break
        case 'bolt':
          c.moveTo(286, 158); c.lineTo(216, 256); c.lineTo(280, 256); c.lineTo(228, 358)
          break
        case 'bar':
          // Dumbbell: left weight box, right weight box, bar between.
          c.moveTo(150, 196); c.lineTo(196, 196); c.lineTo(196, 316); c.lineTo(150, 316); c.closePath()
          c.stroke(); c.beginPath()
          c.moveTo(316, 196); c.lineTo(362, 196); c.lineTo(362, 316); c.lineTo(316, 316); c.closePath()
          c.stroke(); c.beginPath()
          c.moveTo(196, 256); c.lineTo(316, 256)
          break
        case 'v':
        default:
          c.moveTo(146, 174); c.lineTo(256, 360); c.lineTo(366, 174)
          break
      }
      c.stroke()
    }

    // Paint the 5-layer neon glow stack of one glyph onto a canvas
    // context. Skinnier strokes than v1 + wider blur radii give a more
    // mystical, ethereal feel.
    function paintGlyphOn(c: CanvasRenderingContext2D, path: GlyphKey) {
      const layers: Array<[string, number, number]> = [
        ['rgba(110, 231, 183, 0.10)', 18, 90], // outermost haze
        ['rgba(110, 231, 183, 0.22)', 13, 52], // outer halo
        ['rgba(167, 243, 208, 0.50)',  7, 26], // mid bloom
        ['rgba(196, 250, 220, 0.85)',  4, 12], // inner glow
        ['rgba(240, 255, 245, 1.00)', 1.8, 4], // crisp core
      ]
      for (const [color, lineWidth, blur] of layers) {
        c.shadowColor = '#6EE7B7'
        c.shadowBlur = blur
        c.strokeStyle = color
        c.lineWidth = lineWidth
        tracePathOn(c, path)
      }
    }

    // ── Hello glyph (waving hand) — a greeting mark in the SAME engraved
    //    glow format as the V. Used only by the "happy hello" scripted move,
    //    which flickers the V into this and back. Kept stroke-minimal so it
    //    reads at the gem's render size like every other glyph. ──
    function traceHelloOn(c: CanvasRenderingContext2D) {
      // Open waving hand: four spread fingers (middle longest), thumb out
      // to the side, and a deep cupped palm bezier underneath.
      const fingers: Array<[number, number, number, number]> = [
        [230, 280, 226, 206],
        [256, 280, 256, 194],
        [282, 280, 286, 200],
        [306, 280, 312, 224],
      ]
      for (const [x1, y1, x2, y2] of fingers) {
        c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.stroke()
      }
      // thumb
      c.beginPath(); c.moveTo(224, 286); c.lineTo(182, 262); c.stroke()
      // cupped palm
      c.beginPath(); c.moveTo(214, 280); c.bezierCurveTo(214, 344, 308, 344, 308, 280); c.stroke()
    }
    function paintHelloOn(c: CanvasRenderingContext2D) {
      // Slightly thinner than the V's stack (more strokes here) so the hand
      // stays crisp instead of blooming into a slab.
      const layers: Array<[string, number, number]> = [
        ['rgba(110, 231, 183, 0.10)', 13, 70],
        ['rgba(110, 231, 183, 0.22)',  9, 40],
        ['rgba(167, 243, 208, 0.50)',  5, 20],
        ['rgba(196, 250, 220, 0.85)',  3,  9],
        ['rgba(240, 255, 245, 1.00)', 1.5, 3],
      ]
      for (const [color, lineWidth, blur] of layers) {
        c.shadowColor = '#6EE7B7'
        c.shadowBlur = blur
        c.strokeStyle = color
        c.lineWidth = lineWidth
        traceHelloOn(c)
      }
    }

    // ── Question glyph (curious "?") — used by the "curious" move. Same
    //    engraved glow recipe as the V (the wider stack, since it's a single
    //    clean stroke + dot, not a busy hand). ──
    function traceQuestionOn(c: CanvasRenderingContext2D) {
      // top hook curving down into a short stem
      c.beginPath()
      c.moveTo(202, 196)
      c.bezierCurveTo(206, 156, 300, 156, 300, 200)
      c.bezierCurveTo(300, 238, 256, 242, 256, 292)
      c.stroke()
      // dot
      c.beginPath(); c.moveTo(256, 330); c.lineTo(256, 334); c.stroke()
    }
    // Sleepy "Z" — a clean upright Z for the sleepy mood.
    function traceZOn(c: CanvasRenderingContext2D) {
      c.beginPath()
      c.moveTo(204, 202); c.lineTo(304, 202); c.lineTo(206, 318); c.lineTo(308, 318)
      c.stroke()
    }
    // Excited "!" — vertical stroke + dot for the excited mood.
    function traceBangOn(c: CanvasRenderingContext2D) {
      c.beginPath(); c.moveTo(256, 166); c.lineTo(256, 300); c.stroke()
      c.beginPath(); c.moveTo(256, 336); c.lineTo(256, 340); c.stroke()
    }
    // Shared painter for the single-stroke mood glyphs (V-family glow stack).
    function paintMoodGlyph(c: CanvasRenderingContext2D, trace: (c: CanvasRenderingContext2D) => void) {
      const layers: Array<[string, number, number]> = [
        ['rgba(110, 231, 183, 0.10)', 16, 90],
        ['rgba(110, 231, 183, 0.22)', 11, 52],
        ['rgba(167, 243, 208, 0.50)',  6, 26],
        ['rgba(196, 250, 220, 0.85)', 3.5, 12],
        ['rgba(240, 255, 245, 1.00)', 1.8, 4],
      ]
      for (const [color, lineWidth, blur] of layers) {
        c.shadowColor = '#6EE7B7'
        c.shadowBlur = blur
        c.strokeStyle = color
        c.lineWidth = lineWidth
        trace(c)
      }
    }
    // Love heart — used by the "love" mood. Uses the canonical icon-library
    // HEART path (24-viewBox), baked into the gem's glyph box via a matrix so
    // the shape matches the icon library and the engraved stroke weight is kept.
    function traceHeartOn(c: CanvasRenderingContext2D) {
      const s = 10
      const m = new DOMMatrix([s, 0, 0, s, 256 - 12 * s, 250 - 13.35 * s])
      const heart = new Path2D()
      heart.addPath(
        new Path2D('M12 20.3 4.6 12.9a4.6 4.6 0 0 1 6.5-6.5l.9.9.9-.9a4.6 4.6 0 0 1 6.5 6.5L12 20.3Z'),
        m,
      )
      c.stroke(heart)
    }
    // Check — used by the "proud" mood.
    function traceCheckOn(c: CanvasRenderingContext2D) {
      c.beginPath(); c.moveTo(188, 258); c.lineTo(234, 306); c.lineTo(332, 192); c.stroke()
    }
    // Three loading dots — the loading moods' glyph. Filled circles (not
    // stroked) with the same engraved mint glow as the V. `active` picks the
    // brightness pattern: 0/1/2 = that dot bright + the others dim (the sweep
    // frames); -1 = all three lit equally (steady, for the spin + pulse moods).
    function paintDotsOn(c: CanvasRenderingContext2D, active: number) {
      const xs = [168, 256, 344]
      const cy = 256
      for (let i = 0; i < 3; i++) {
        // 2 = swept-bright, 1 = all-lit steady, 0 = dim
        const level = active < 0 ? 1 : i === active ? 2 : 0
        const r = level === 2 ? 30 : level === 1 ? 26 : 19
        const layers: Array<[string, number]> =
          level === 2
            ? [['rgba(110, 231, 183, 0.16)', 90], ['rgba(167, 243, 208, 0.55)', 32], ['rgba(240, 255, 245, 1.00)', 12]]
            : level === 1
              ? [['rgba(110, 231, 183, 0.13)', 64], ['rgba(167, 243, 208, 0.48)', 24], ['rgba(228, 252, 240, 0.95)', 11]]
              : [['rgba(110, 231, 183, 0.10)', 48], ['rgba(150, 220, 190, 0.42)', 16]]
        for (const [color, blur] of layers) {
          c.shadowColor = '#6EE7B7'
          c.shadowBlur = blur
          c.fillStyle = color
          c.beginPath()
          c.arc(xs[i], cy, r, 0, Math.PI * 2)
          c.fill()
        }
      }
    }
    const paintQuestionOn = (c: CanvasRenderingContext2D) => paintMoodGlyph(c, traceQuestionOn)
    const paintZOn = (c: CanvasRenderingContext2D) => paintMoodGlyph(c, traceZOn)
    const paintBangOn = (c: CanvasRenderingContext2D) => paintMoodGlyph(c, traceBangOn)
    const paintHeartOn = (c: CanvasRenderingContext2D) => paintMoodGlyph(c, traceHeartOn)
    const paintCheckOn = (c: CanvasRenderingContext2D) => paintMoodGlyph(c, traceCheckOn)

    paintGlyphOn(vCtx, glyph)

    const vTex = new THREE.CanvasTexture(vCanvas)
    vTex.colorSpace = THREE.SRGBColorSpace
    vTex.anisotropy = 4
    vTex.needsUpdate = true

    // Optional secondary glyph — paint a second 512² canvas and texture.
    // The face-cycle below alternates vMat.map between this and vTex so
    // each new face shows the next glyph in the rotation (e.g.
    // check → mentor-icon → check → mentor-icon on the celebration screen).
    // Hello glyph texture — painted lazily in character init (the only mode
    // with the move wired up). Stays null otherwise.
    let helloTex: THREE.CanvasTexture | null = null
    let questionTex: THREE.CanvasTexture | null = null
    let sleepyTex: THREE.CanvasTexture | null = null
    let excitedTex: THREE.CanvasTexture | null = null
    let heartTex: THREE.CanvasTexture | null = null
    let checkTex: THREE.CanvasTexture | null = null
    let ideaTex: THREE.CanvasTexture | null = null   // V→bolt (aha)
    // Level up shows one of three glyphs (steps / tiers / break-through),
    // picked at random each time the move fires.
    let levelupTex: (THREE.CanvasTexture | null)[] = []
    let levelupVariant = 0
    let liftTex: THREE.CanvasTexture | null = null   // V→dumbbell (lift / strength)
    let encourageTex: THREE.CanvasTexture | null = null // V→up-arrow (Lift)
    let considerTex: THREE.CanvasTexture | null = null  // V→scale (Weigh it up)
    let explainTex: THREE.CanvasTexture | null = null   // V→text lines
    // Let's go shows one of three glyphs (rocket / chevrons / plane), picked
    // at random each time the move fires.
    let letsGoTex: (THREE.CanvasTexture | null)[] = []
    let letsGoVariant = 0
    let listeningTex: THREE.CanvasTexture | null = null // V→sound waves (listening)
    let celebrateTex: THREE.CanvasTexture | null = null // V→sparkle (celebrate)
    let cheerTex: THREE.CanvasTexture | null = null     // V→rays (cheer)
    let winTex: THREE.CanvasTexture | null = null       // V→trophy (win)
    let twinkleTex: THREE.CanvasTexture | null = null   // V→star (twinkle)
    let grooveTex: THREE.CanvasTexture | null = null    // V→music note (groove)
    // The "loading" mood cycles through three dot-frames (one dot lit per
    // frame) for a classic loading sweep engraved on the gem face;
    // loadingTexAll is the steady all-lit dots used by the spin + pulse loaders.
    let loadingTex: THREE.CanvasTexture[] = []
    let loadingTexAll: THREE.CanvasTexture | null = null
    let vTex2: THREE.CanvasTexture | null = null
    if (secondaryGlyph) {
      const vCanvas2 = document.createElement('canvas')
      vCanvas2.width = 512
      vCanvas2.height = 512
      const vCtx2 = vCanvas2.getContext('2d')!
      vCtx2.lineCap = 'round'
      vCtx2.lineJoin = 'round'
      paintGlyphOn(vCtx2, secondaryGlyph)
      vTex2 = new THREE.CanvasTexture(vCanvas2)
      vTex2.colorSpace = THREE.SRGBColorSpace
      vTex2.anisotropy = 4
      vTex2.needsUpdate = true
    }
    const glyphTextures: THREE.CanvasTexture[] = vTex2 ? [vTex, vTex2] : [vTex]
    let activeGlyphIdx = 0

    const vMat = new THREE.MeshBasicMaterial({
      map: vTex,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      depthTest: true,
      side: THREE.FrontSide,
      opacity: 0,
    })
    /* Bigger plane (was 0.42 → 0.62) — V reads more prominently while
       still fitting within the icosahedron face triangle (side ≈ 1.05). */
    const vGeo = new THREE.PlaneGeometry(0.62, 0.62)
    const vMesh = new THREE.Mesh(vGeo, vMat)
    vMesh.renderOrder = 3
    // hideGlyph: keep the mesh allocated so animation code can still
    // touch its position/quaternion safely, but never insert it into the
    // scene graph — the V never renders.
    if (!hideGlyph) mesh.add(vMesh)

    /* ── Loading "jump" dots ──
       Three small camera-facing mint dots in front of the gem, animated
       (bouncing up/down) ONLY during the loadJump* moods and hidden otherwise.
       Parented to the SCENE (not the gem) so they stay a level upright row
       while the gem bobs. Real per-frame motion → smooth jumps (no texture
       frames). Used to prototype 3 jumping-dots loading icons. */
    const dotCanvas = document.createElement('canvas')
    dotCanvas.width = dotCanvas.height = 128
    {
      const dc = dotCanvas.getContext('2d')!
      const g = dc.createRadialGradient(64, 64, 0, 64, 64, 62)
      g.addColorStop(0, 'rgba(245, 255, 250, 1)')
      g.addColorStop(0.34, 'rgba(167, 243, 208, 0.95)')
      g.addColorStop(0.7, 'rgba(110, 231, 183, 0.45)')
      g.addColorStop(1, 'rgba(110, 231, 183, 0)')
      dc.fillStyle = g
      dc.beginPath(); dc.arc(64, 64, 62, 0, Math.PI * 2); dc.fill()
    }
    const dotTex = new THREE.CanvasTexture(dotCanvas)
    dotTex.colorSpace = THREE.SRGBColorSpace
    dotTex.needsUpdate = true
    const dotMat = new THREE.MeshBasicMaterial({
      map: dotTex,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      depthTest: false,
      opacity: 0,
    })
    const dotGeo = new THREE.PlaneGeometry(0.16, 0.16)
    const dotMeshes: THREE.Mesh[] = []
    const DOT_X = [-0.32, 0, 0.32]
    for (let i = 0; i < 3; i++) {
      const dm = new THREE.Mesh(dotGeo, dotMat)
      dm.position.set(DOT_X[i], 0, 1.12)
      dm.renderOrder = 5
      scene.add(dm)
      dotMeshes.push(dm)
    }
    // Vertical offset (world units, up = +) for dot `i` at local move-time
    // `lt`, per jump style. 1 = staggered wave, 2 = synced hop, 3 = smooth flow.
    const JUMP_H = 0.2
    const JUMP_P = 0.95
    const jumpY = (style: number, lt: number, i: number): number => {
      if (style === 1) {
        let p = lt / JUMP_P - i * 0.18
        p -= Math.floor(p)
        return JUMP_H * (p < 0.5 ? Math.sin((p / 0.5) * Math.PI) : 0)
      } else if (style === 2) {
        let p = lt / JUMP_P
        p -= Math.floor(p)
        return JUMP_H * (p < 0.45 ? Math.sin((p / 0.45) * Math.PI) : 0)
      }
      return 0.5 * JUMP_H * (0.5 + 0.5 * Math.sin((lt / JUMP_P) * Math.PI * 2 - i * ((Math.PI * 2) / 3)))
    }

    /* Hold-phase flicker patterns — one per face index modulo. Mix of
       characters so the V doesn't repeat itself face-to-face. */
    const HOLD_FLICKERS: Array<(t: number, s: number) => number> = [
      // subtle micro-flickers (neon at rest)
      (t, s) =>
        0.86 + 0.10 * Math.sin(t * 7 + s) +
        (Math.sin(t * 19 + s * 2) > 0.85 ? -0.45 : 0),
      // mid-frequency tremor
      (t, s) =>
        0.84 + 0.14 * Math.sin(t * 5.2 + s) * Math.sin(t * 2.1 + s * 0.5),
      // mostly clean with rare deep flickers
      (t, s) =>
        0.94 + (Math.sin(t * 23 + s) * Math.sin(t * 11 + s * 0.3) > 0.65 ? -0.55 : 0),
      // calm breath
      (t, s) => 0.90 + 0.07 * Math.sin(t * 2.0 + s),
    ]

    const FADE_IN_DUR  = 1.1   // slow flicker on
    const HOLD_DUR     = 3.4   // ~4.5s visible
    const FADE_OUT_DUR = 0.45  // fast strobe off
    const CYCLE_DUR    = FADE_IN_DUR + HOLD_DUR + FADE_OUT_DUR

    /* Tighter thresholds — V only appears when the face is essentially
       dead-on facing the camera, and the envelope reaches full opacity
       at near-peak alignment. Max achievable dot for icosahedron face
       under Y-rotation is ~0.947 (without cursor tilt), so we tune
       around that. */
    const CLAIM_DOT = 0.92
    const ENV_LO    = 0.82
    const ENV_HI    = 0.94
    const smoothstep = (lo: number, hi: number, x: number) => {
      const tt = Math.max(0, Math.min(1, (x - lo) / (hi - lo)))
      return tt * tt * (3 - 2 * tt)
    }

    /* Scratch math objects — reused across frames to avoid GC churn. */
    const _q = new THREE.Quaternion()
    const _e = new THREE.Euler()
    const _n = new THREE.Vector3()
    const _planeUp  = new THREE.Vector3()
    const _desiredUp = new THREE.Vector3()
    const _cross    = new THREE.Vector3()
    const _correct  = new THREE.Quaternion()
    const _Y        = new THREE.Vector3(0, 1, 0)
    const _Z        = new THREE.Vector3(0, 0, 1)
    const CAMERA_DIR = new THREE.Vector3(0, 0, 1)

    const getFaceWorldDot = (idx: number): number => {
      _e.set(mesh.rotation.x, mesh.rotation.y, mesh.rotation.z)
      _q.setFromEuler(_e)
      _n.copy(faces[idx].normal).applyQuaternion(_q)
      return _n.dot(CAMERA_DIR)
    }

    /* Place V at the face center, oriented to the face normal, with an
       additional up-correction rotation about the normal so the V is
       always rendered upright (tip points "down" in the face plane,
       toward world-down projected onto that plane). Without this the V
       can read sideways/upside-down on faces whose default plane up
       isn't aligned with world up. */
    const placeOnFace = (idx: number) => {
      const f = faces[idx]
      // Position: face center + small outward offset so V sits proud
      // of the face plane (avoids z-fight, reads engraved-on-top).
      vMesh.position.copy(f.center).add(_n.copy(f.normal).multiplyScalar(0.012))

      // Step 1: align plane +Z to face normal (shortest-arc rotation).
      vMesh.quaternion.setFromUnitVectors(_Z, f.normal)

      // Step 2: where does plane +Y end up after that rotation?
      _planeUp.copy(_Y).applyQuaternion(vMesh.quaternion)

      // Step 3: where SHOULD plane +Y be? — world +Y projected onto the
      // face plane (perpendicular to face normal).
      _desiredUp.copy(_Y).sub(_n.copy(f.normal).multiplyScalar(_Y.dot(f.normal)))
      if (_desiredUp.lengthSq() < 1e-4) return // polar face — no unique up; skip
      _desiredUp.normalize()

      // Step 4: rotation angle about face normal to take planeUp → desiredUp.
      const cosA = _planeUp.dot(_desiredUp)
      _cross.crossVectors(_planeUp, _desiredUp)
      const sinA = _cross.dot(f.normal)
      const angle = Math.atan2(sinA, cosA)

      // Step 5: apply correction.
      _correct.setFromAxisAngle(f.normal, angle)
      vMesh.quaternion.premultiply(_correct)
    }

    let activeFaceIdx  = -1
    let cycleStart     = 0
    let cycleComplete  = true
    let activePattern  = 0

    /* ── Lights ───────────────────────────────────────────────── */
    scene.add(new THREE.AmbientLight(0x1a3a2c, 0.15))

    const key = new THREE.DirectionalLight(computeKeyColor().getHex(), 4.0)
    key.position.set(2.6, 1.8, 2.2)
    scene.add(key)

    const rim = new THREE.DirectionalLight(COOL_MINT.getHex(), 2.2)
    rim.position.set(-2.8, 1.2, -2.0)
    scene.add(rim)

    const fill = new THREE.DirectionalLight(0x88c4b0, 0.6)
    fill.position.set(0, -2.5, 1)
    scene.add(fill)

    /* ── Sizing ───────────────────────────────────────────────── */
    // Local non-null binding — TS closure narrowing doesn't propagate the
    // `if (!canvas) return` check into nested function declarations.
    const node: HTMLCanvasElement = canvas
    const applyResize = () => {
      const w = node.clientWidth
      const h = node.clientHeight
      const dpr = Math.min(window.devicePixelRatio || 1, 1.75)
      renderer.setPixelRatio(dpr)
      renderer.setSize(w, h, false)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
    }
    applyResize()
    // ResizeObserver fires every frame of a CSS size transition (e.g. the
    // onboarding gem animating from 320px → 160px over 520ms). Each
    // renderer.setSize() reallocates the WebGL framebuffer and can paint
    // one blank frame on some GPUs, which surfaces as a "gem disappears
    // and comes back" flash. Debounce so we only call setSize once the
    // size has stayed constant for ~120ms — during the transition the
    // canvas buffer keeps its old dimensions and CSS scales it (visually
    // imperceptible for half a second).
    let resizeTimer: ReturnType<typeof setTimeout> | null = null
    const onResize = () => {
      if (resizeTimer) clearTimeout(resizeTimer)
      resizeTimer = setTimeout(() => {
        resizeTimer = null
        applyResize()
      }, 120)
    }
    const resizeObserver = new ResizeObserver(onResize)
    resizeObserver.observe(node)

    /* ── Cursor tilt ──────────────────────────────────────────── */
    const target = { x: 0, y: 0 }
    const current = { x: 0, y: 0 }
    let baseY = 0

    function onPointerMove(clientX: number, clientY: number) {
      const nx = (clientX / window.innerWidth) * 2 - 1
      const ny = (clientY / window.innerHeight) * 2 - 1
      target.x = ny * PARAMS.cursorTilt
      target.y = nx * PARAMS.cursorTilt
    }
    const handleMouse = (e: MouseEvent) => onPointerMove(e.clientX, e.clientY)
    const handleTouch = (e: TouchEvent) => {
      if (e.touches[0]) onPointerMove(e.touches[0].clientX, e.touches[0].clientY)
    }
    window.addEventListener('mousemove', handleMouse, { passive: true })
    window.addEventListener('touchmove', handleTouch, { passive: true })

    /* ── Tick loop ────────────────────────────────────────────── */
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const clock = new THREE.Clock()
    let rafId = 0

    /* ── Character-mode state (Destiny Ghost-style companion) ────
       Initialized once before the loop starts. Jolt state holds the
       current rotational impulse offsets that spring back to zero;
       nextJoltAt is the world-time at which we kick a new impulse. */
    const jolt = { x: 0, y: 0, z: 0, vx: 0, vy: 0, vz: 0 }
    let nextJoltAt = 0.8
    let nextNodAt = 2.4

    /* Happy-pulse state. Every ~6–9s the gem enters a brief HAPPY
       phase: eyebrow opacity fades in above the V, the parent page
       is notified via onHappyStart (so it can fire a particle burst),
       then everything fades back to neutral. Brows live OUTSIDE the
       happy window — they're invisible otherwise — which keeps the
       resting expression a clean, untouched V. */
    const HAPPY_DUR    = 1.8   // total happy phase length
    const HAPPY_IN     = 0.35  // fade-in
    const HAPPY_OUT    = 0.6   // fade-out
    let happyStart    = -Infinity
    let nextHappyAt   = 2.0    // first happy event 2s after mount
    let happyFired    = false  // notified the host yet for current happy?

    /* Loading / thinking pulse state. A calm looping "breath" the gem drops
       into while a host operation runs (e.g. macro photo analysis). beatPhase
       walks 0→1 over beatPeriod; each wrap is one breath + one sonar ring
       (fired to the host via onLoadingBeat). beatPeriod is re-rolled 1.8–2.2s
       each beat so the rhythm reads alive, not metronomic. Exiting loading
       plays a short bright resolve, then idle resumes. */
    let loadingActive = false  // currently in the looping breath
    let beatPhase     = 0      // 0..1 within the current breath
    let beatPeriod    = 2.0    // seconds for this breath (organic 1.8–2.2)
    let resolving     = false  // playing the one-shot complete pop
    let resolveStart  = 0
    const RESOLVE_DUR = 0.72   // length of the resolve pop

    /* Rest pose: a 3D quaternion that brings the active face's normal
       perpendicular to the camera. Computed once in character mode;
       the bob/jolt rotation deltas in tick() are applied on top. */
    let restQuat: THREE.Quaternion | undefined
    // Emotion-keyed brow textures: happy, excited, love, proud, sleepy.
    // Each mood shows its own brow so the gem's expression matches the glyph.
    const browTexMap: Record<string, THREE.CanvasTexture> = {}
    let browGeo: THREE.PlaneGeometry | undefined
    let browMat: THREE.MeshBasicMaterial | undefined
    let browMesh: THREE.Mesh | undefined

    /* ── Scripted-move state (gem-library lab) ──
       A named choreography the lab can trigger on demand. moveActive gates
       the override block at the tail of the character tick; production gems
       never set controlRef, so this stays dormant and mesh.position holds
       at origin exactly as before. */
    let moveActive = false
    let moveStart = 0
    let moveName = ''
    // When true the move's happy phase shows brows but does NOT fire the
    // burst callback (a quiet mood like "curious" wants no celebration).
    let suppressBurst = false
    // Fires onMoveGlyph once per move, the frame the mood glyph swaps in.
    let moveGlyphFired = false
    // Sleepy fires a second onMoveGlyph(...'wake') once, at the wake beat.
    let moveWakeFired = false

    if (mode === 'character') {
      // Find the face most aligned with +Z at the geometry's default
      // orientation — we'll rotate the whole gem so THAT face's
      // normal points dead-on at the camera. After this rotation the
      // V engraving lands centered, not at an oblique angle.
      let bestIdx = 0
      let bestDot = -Infinity
      for (let i = 0; i < faces.length; i++) {
        const d = faces[i].normal.dot(CAMERA_DIR)
        if (d > bestDot) { bestDot = d; bestIdx = i }
      }
      activeFaceIdx = bestIdx
      cycleComplete = false // never re-cycles in character mode
      activePattern = bestIdx % HOLD_FLICKERS.length

      restQuat = new THREE.Quaternion().setFromUnitVectors(
        faces[bestIdx].normal.clone(),
        CAMERA_DIR,
      )
      // Capture as Euler so the existing bob/jolt Euler-arithmetic
      // continues to work, just offset from the rest pose.
      const restEuler = new THREE.Euler().setFromQuaternion(restQuat, 'YXZ')
      mesh.rotation.set(restEuler.x, restEuler.y, restEuler.z)
      placeOnFace(activeFaceIdx)

      /* Three brow textures — minimalist, modern, Pixar-robot cute.
         Three layers only (was five) so the strokes read as crisp
         accents instead of glowing slabs. Thinner core line + softer
         halo. The active variant is swapped onto browMat.map each
         happy event; all three textures dispose together on cleanup.

         V tips sit at (146, 174) and (366, 174) in the 512² canvas.
         Brows draw above those tips with a ~70–90px span, peak ~14–20px
         tall — small enough to feel cute, big enough to read at the
         gem's render size. */
      type BrowDraw = (ctx: CanvasRenderingContext2D) => void

      // Three layers: soft halo → mid glow → crisp mint core. The
      // brows are slightly less white than the V's core (220/250/232
      // vs V's 240/255/245) so they feel like an accent, not a peer.
      const layered = (ctx: CanvasRenderingContext2D, drawStrokes: (color: string, w: number, blur: number) => void) => {
        drawStrokes('rgba(110, 231, 183, 0.18)', 7, 28)
        drawStrokes('rgba(167, 243, 208, 0.55)', 3.5, 11)
        drawStrokes('rgba(220, 250, 232, 0.96)', 1.4, 3)
      }

      // One minimal brow expression per emotion, drawn with the same 3-layer
      // glow. Each is a symmetric pair (left + right) above the V tips. The
      // SHAPE carries the emotion: cheerful arch (happy), high peaks
      // (excited), inner-raised tender (love), high+flat composed (proud),
      // outer-drooping heavy (sleepy).
      const stroke2 = (
        left: (c: CanvasRenderingContext2D) => void,
        right: (c: CanvasRenderingContext2D) => void,
      ): BrowDraw => (c) => {
        c.lineCap = 'round'; c.lineJoin = 'round'
        layered(c, (color, w, blur) => {
          c.shadowColor = '#6EE7B7'; c.shadowBlur = blur
          c.strokeStyle = color; c.lineWidth = w
          c.beginPath(); left(c); c.stroke()
          c.beginPath(); right(c); c.stroke()
        })
      }
      // Each emotion uses a genuinely different brow STYLE, not the same
      // arch at different heights: round arch / sharp peak / flat line /
      // straight slant / heavy down-arch.
      const browDraws: Record<string, BrowDraw> = {
        // happy — round upward arches (a smile in brow form)
        happy: stroke2(
          (c) => { c.moveTo(120, 126); c.quadraticCurveTo(158, 106, 192, 126) },
          (c) => { c.moveTo(320, 126); c.quadraticCurveTo(354, 106, 388, 126) }),
        // excited — sharp angular peaks (^ ^)
        excited: stroke2(
          (c) => { c.moveTo(118, 130); c.lineTo(156, 98); c.lineTo(194, 130) },
          (c) => { c.moveTo(318, 130); c.lineTo(356, 98); c.lineTo(394, 130) }),
        // love — tall lifted arches: delighted "yay", not romantic
        love: stroke2(
          (c) => { c.moveTo(118, 124); c.quadraticCurveTo(158, 96, 196, 124) },
          (c) => { c.moveTo(316, 124); c.quadraticCurveTo(354, 96, 394, 124) }),
        // proud — straight flat raised lines (composed, self-assured)
        proud: stroke2(
          (c) => { c.moveTo(118, 116); c.lineTo(192, 116) },
          (c) => { c.moveTo(320, 116); c.lineTo(394, 116) }),
        // sleepy — heavy downward arches (drooping lids)
        sleepy: stroke2(
          (c) => { c.moveTo(120, 114); c.quadraticCurveTo(158, 130, 194, 118) },
          (c) => { c.moveTo(318, 118); c.quadraticCurveTo(354, 130, 392, 114) }),
        // focus — straight slants pulled DOWN toward the center (furrowed,
        // determined). Opposite tilt to love so the two never confuse.
        focus: stroke2(
          (c) => { c.moveTo(116, 110); c.lineTo(196, 128) },
          (c) => { c.moveTo(316, 128); c.lineTo(396, 110) }),
      }

      // Paint the hello glyph texture once (character mode only).
      {
        const helloCanvas = document.createElement('canvas')
        helloCanvas.width = 512
        helloCanvas.height = 512
        const helloCtx = helloCanvas.getContext('2d')!
        helloCtx.lineCap = 'round'
        helloCtx.lineJoin = 'round'
        paintHelloOn(helloCtx)
        helloTex = new THREE.CanvasTexture(helloCanvas)
        helloTex.colorSpace = THREE.SRGBColorSpace
        helloTex.anisotropy = 4
        helloTex.needsUpdate = true
      }
      // Paint the question glyph texture once (character mode only).
      {
        const qCanvas = document.createElement('canvas')
        qCanvas.width = 512
        qCanvas.height = 512
        const qCtx = qCanvas.getContext('2d')!
        qCtx.lineCap = 'round'
        qCtx.lineJoin = 'round'
        paintQuestionOn(qCtx)
        questionTex = new THREE.CanvasTexture(qCanvas)
        questionTex.colorSpace = THREE.SRGBColorSpace
        questionTex.anisotropy = 4
        questionTex.needsUpdate = true
      }
      // Paint the sleepy "Z" + excited "!" glyph textures once.
      const makeMoodTex = (paint: (c: CanvasRenderingContext2D) => void): THREE.CanvasTexture => {
        const cv = document.createElement('canvas')
        cv.width = 512; cv.height = 512
        const c = cv.getContext('2d')!
        c.lineCap = 'round'; c.lineJoin = 'round'
        paint(c)
        const tex = new THREE.CanvasTexture(cv)
        tex.colorSpace = THREE.SRGBColorSpace
        tex.anisotropy = 4
        tex.needsUpdate = true
        return tex
      }
      sleepyTex = makeMoodTex(paintZOn)
      excitedTex = makeMoodTex(paintBangOn)
      heartTex = makeMoodTex(paintHeartOn)
      checkTex = makeMoodTex(paintCheckOn)
      loadingTex = [0, 1, 2].map((i) => makeMoodTex((c) => paintDotsOn(c, i)))
      loadingTexAll = makeMoodTex((c) => paintDotsOn(c, -1))
      // New glyph-flicker moods reuse marks the gem already draws (icon library).
      ideaTex = makeMoodTex((c) => paintGlyphOn(c, 'bolt'))
      levelupTex = [GLYPHS.STEPS, GLYPHS.TIERS, GLYPHS.BREAKTHROUGH].map((g) =>
        makeMoodTex((c) => paintMoodGlyph(c, g)),
      )
      liftTex = makeMoodTex((c) => paintGlyphOn(c, 'bar')) // dumbbell — the existing 'bar' path
      // These reuse the canonical icon-library marks (lib/gemGlyphs) so the gem
      // and the icon library stay in sync.
      encourageTex = makeMoodTex((c) => paintMoodGlyph(c, GLYPHS.ARROW_UP))
      considerTex = makeMoodTex((c) => paintMoodGlyph(c, GLYPHS.SCALE))
      explainTex = makeMoodTex((c) => paintMoodGlyph(c, GLYPHS.NOTE))
      letsGoTex = [GLYPHS.ROCKET, GLYPHS.CHEVRONS, GLYPHS.PLANE].map((g) =>
        makeMoodTex((c) => paintMoodGlyph(c, g)),
      )
      listeningTex = makeMoodTex((c) => paintMoodGlyph(c, GLYPHS.LISTEN))
      celebrateTex = makeMoodTex((c) => paintMoodGlyph(c, GLYPHS.GIFT))
      cheerTex = makeMoodTex((c) => paintMoodGlyph(c, GLYPHS.RAYS))
      winTex = makeMoodTex((c) => paintMoodGlyph(c, GLYPHS.TROPHY))
      twinkleTex = makeMoodTex((c) => paintMoodGlyph(c, GLYPHS.STAR))
      grooveTex = makeMoodTex((c) => paintMoodGlyph(c, GLYPHS.MUSIC))

      for (const [name, draw] of Object.entries(browDraws)) {
        const c = document.createElement('canvas')
        c.width = 512; c.height = 512
        draw(c.getContext('2d')!)
        const tex = new THREE.CanvasTexture(c)
        tex.colorSpace = THREE.SRGBColorSpace
        tex.anisotropy = 4
        tex.needsUpdate = true
        browTexMap[name] = tex
      }

      browGeo = new THREE.PlaneGeometry(0.62, 0.62)
      browMat = new THREE.MeshBasicMaterial({
        map: browTexMap.happy,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        depthTest: true,
        side: THREE.FrontSide,
        opacity: 0,
      })
      browMesh = new THREE.Mesh(browGeo, browMat)
      browMesh.renderOrder = 3
      mesh.add(browMesh)
      placeOnFace(activeFaceIdx)
      browMesh.position.copy(vMesh.position)
      browMesh.quaternion.copy(vMesh.quaternion)
    }

    /* Lab control: play a named move on the gem. No-op unless we're in
       character mode (the only mode with brows + rest pose wired up). The
       move itself runs in the character branch of tick(); this just arms
       it and drives the happy face so brows + the synchronized burst land
       during the float/pulse portion. */
    function triggerMove(name: string) {
      if (mode !== 'character') return
      const now = clock.getElapsedTime()
      moveActive = true
      moveStart = now
      moveName = name
      moveGlyphFired = false
      moveWakeFired = false
      // Let's go randomizes its glyph (rocket / chevrons / plane) per trigger.
      if (name === 'pumpUp' && letsGoTex.length) {
        letsGoVariant = Math.floor(Math.random() * letsGoTex.length)
      }
      // Level up randomizes its glyph (steps / tiers / break-through) per trigger.
      if (name === 'levelup' && levelupTex.length) {
        levelupVariant = Math.floor(Math.random() * levelupTex.length)
      }
      // Which moods show a brow expression, and which fire a celebration
      // burst. happyHello/excited/love/proud do both; sleepy shows brows
      // (droopy, fading as it nods off) but no burst; curious + spin are
      // faceless. The brow KEY matches the mood name (happyHello → 'happy').
      const showBrows = name === 'happyHello' || name === 'excited' || name === 'love' || name === 'proud' || name === 'sleepy' || name === 'focus' || name === 'lift'
      const fireBurst = name === 'happyHello' || name === 'excited' || name === 'love' || name === 'proud' || name === 'lift'
      suppressBurst = !fireBurst
      // Always begin the move showing the V — the glyph swap happens
      // mid-move, so a prior move must not leave a glyph on the face.
      if (vMat.map !== vTex) { vMat.map = vTex; vMat.needsUpdate = true }
      dotMat.opacity = 0 // clear any lingering jump dots from a prior move
      // Push the autonomous happy timer past this move so an idle happy
      // doesn't interrupt it.
      nextHappyAt = now + HAPPY_DUR + 6
      if (showBrows) {
        happyStart = now
        happyFired = false
        const browName = name === 'happyHello' ? 'happy' : name === 'lift' ? 'proud' : name
        const tex = browTexMap[browName]
        if (browMat && tex) { browMat.map = tex; browMat.needsUpdate = true }
      } else {
        // No face — keep the happy window closed so no brows fade in.
        happyStart = -Infinity
      }
    }
    if (controlRef) controlRef.current = triggerMove

    function tick() {
      const dt = Math.min(clock.getDelta(), 0.05)
      const t = clock.getElapsedTime()

      if (!prefersReducedMotion && mode === 'character') {
        /* ── Character branch: dead-on face + sporadic friendly jolts ── */
        /* ── Loading lifecycle (runs first; gates the lively stuff) ──
           While loading the gem stays calm — no random jolts, nods, or happy
           events — so the breath reads patient. */
        const isLoading = loadingRef.current === true
        if (isLoading && !loadingActive) {
          // Enter the breath. Close any open happy window so no brows linger,
          // and ping the first ring immediately so loading reads instantly.
          loadingActive = true
          resolving = false
          beatPhase = 0
          beatPeriod = 2.0
          happyStart = -Infinity
          onLoadingBeatRef.current?.('beat')
        } else if (!isLoading && loadingActive) {
          // Leave the breath → play the one bright resolve pop.
          loadingActive = false
          resolving = true
          resolveStart = t
          onLoadingBeatRef.current?.('resolve')
        }
        if (loadingActive) {
          beatPhase += dt / beatPeriod
          if (beatPhase >= 1) {
            beatPhase -= 1
            beatPeriod = 1.8 + Math.random() * 0.4   // organic, never metronomic
            onLoadingBeatRef.current?.('beat')
          }
        }
        if (resolving && t - resolveStart >= RESOLVE_DUR) {
          resolving = false
          mesh.scale.setScalar(1)
          // Don't let a stale timer fire a happy/jolt the instant we resume.
          nextHappyAt = t + 9 + Math.random() * 7
          nextJoltAt = t + 1.5 + Math.random() * 2.0
          nextNodAt = t + 4 + Math.random() * 2
        }
        const calm = isLoading || resolving  // suppress liveliness during both

        const JOLT_SPRING = 6.0  // higher = snappier return
        const JOLT_DAMP   = 4.5
        jolt.vx += (-JOLT_SPRING * jolt.x - JOLT_DAMP * jolt.vx) * dt
        jolt.vy += (-JOLT_SPRING * jolt.y - JOLT_DAMP * jolt.vy) * dt
        jolt.vz += (-JOLT_SPRING * jolt.z - JOLT_DAMP * jolt.vz) * dt
        jolt.x += jolt.vx * dt
        jolt.y += jolt.vy * dt
        jolt.z += jolt.vz * dt

        if (!calm && t >= nextJoltAt) {
          // Small magnitudes — the Ghost twitches, it doesn't tumble.
          const mag = 0.05 + Math.random() * 0.08
          const sign = () => (Math.random() > 0.5 ? 1 : -1)
          jolt.vx += sign() * mag * 4
          jolt.vy += sign() * mag * 4
          jolt.vz += sign() * mag * 1.6
          nextJoltAt = t + 1.5 + Math.random() * 2.3
        }
        if (!calm && t >= nextNodAt) {
          // Consistent forward "yes" nod.
          jolt.vx += 0.5
          nextNodAt = t + 4 + Math.random() * 2
        }

        const bobX = Math.sin(t * 0.9) * 0.012
        const bobY = Math.sin(t * 0.7 + 0.5) * 0.010

        // Cursor look — half-strength so the gem turns toward where the
        // user is looking without abandoning eye contact.
        current.x += (target.x * 0.5 - current.x) * (PARAMS.spring * 0.7)
        current.y += (target.y * 0.5 - current.y) * (PARAMS.spring * 0.7)

        // Base pose is the pre-computed rest rotation; bob/jolt are
        // applied as deltas, then we rebuild the mesh quaternion.
        // We could go full-quaternion here, but the existing tick
        // arithmetic is Euler-based and the deltas are small enough
        // that gimbal isn't a concern at these magnitudes.
        if (restQuat) {
          const e = new THREE.Euler().setFromQuaternion(restQuat, 'YXZ')
          mesh.rotation.x = e.x + bobX + jolt.x + current.x
          mesh.rotation.y = e.y + bobY + jolt.y + current.y
          mesh.rotation.z = e.z + jolt.z
        }

        /* ── Happy-pulse state machine ──
           Every ~6–9s the gem enters a brief HAPPY phase. Brows fade
           in above the V; the parent page is notified once via
           onHappyStart so it can run a synchronized particle burst. */
        if (!calm && !moveActive && t >= nextHappyAt && t - happyStart > HAPPY_DUR) {
          // Autonomous idle "life". Pull a random gentle move from the ambient
          // pool and play it through the normal move path — so every gem stays
          // alive and inherits new ambient moves automatically. If the pool is
          // disabled (null/empty), fall back to the plain happy-brow warmth.
          const pool = ambientRef.current
          if (pool && pool.length) {
            triggerMove(pool[Math.floor(Math.random() * pool.length)])
          } else {
            happyStart = t
            happyFired = false
            if (browMat && browTexMap.happy) {
              browMat.map = browTexMap.happy
              browMat.needsUpdate = true
            }
          }
          // Spaced out so it reads as genuine life, not a stuck loop. 11–19s.
          nextHappyAt = t + 11 + Math.random() * 8
        }
        let happyAmt = 0 // 0 = neutral, 1 = brows fully visible
        if (t - happyStart < HAPPY_DUR) {
          const localT = t - happyStart
          if (localT < HAPPY_IN) {
            happyAmt = localT / HAPPY_IN
          } else if (localT > HAPPY_DUR - HAPPY_OUT) {
            happyAmt = Math.max(0, (HAPPY_DUR - localT) / HAPPY_OUT)
          } else {
            happyAmt = 1
          }
          if (!happyFired) {
            happyFired = true
            if (!suppressBurst) onHappyStartRef.current?.()
          }
        }

        /* ── Breathing emissive + V glow + brows ── */
        const breath = 0.5 + 0.5 * Math.sin(t * 1.6) // ~3.9s cycle
        // Emissive pulses slightly brighter at peak happiness.
        mat.emissiveIntensity = 0.46 + 0.28 * breath + 0.15 * happyAmt
        const baseGlow = 0.78 + 0.16 * breath
        const microFlicker = 0.04 * Math.sin(t * 6.3 + 0.4)
        const rareBlink = (Math.sin(t * 17 + 1.1) * Math.sin(t * 5 + 0.8)) > 0.82
          ? -0.45
          : 0
        vMat.opacity = Math.max(0.25, Math.min(1, baseGlow + microFlicker + rareBlink + 0.1 * happyAmt))
        if (browMat) {
          // Brows are entirely gated by happyAmt — invisible at rest,
          // peak visibility during the happy window, plus a tiny
          // micro-flicker so they read alive when on.
          const browFlicker = 0.05 * Math.sin(t * 7.4 + 1.1)
          browMat.opacity = Math.max(0, Math.min(1, happyAmt * (0.95 + browFlicker)))
        }

        /* ── Loading breath / resolve override ──
           Runs after the idle breathing so it owns scale + emissive while
           active. Mutually exclusive with scripted moves (which run later and
           would win anyway). */
        if (loadingActive) {
          // Smooth 0→1→0 swell across the beat: a calm inhale / exhale that
          // gently brightens the glow and scales the gem ~4%.
          const swell = 0.5 - 0.5 * Math.cos(beatPhase * Math.PI * 2)
          mesh.scale.setScalar(1 + 0.04 * swell)
          mat.emissiveIntensity = 0.40 + 0.55 * swell
        } else if (resolving) {
          // One satisfying pop: a quick scale bump + bright flash that eases
          // back down into the normal idle morph.
          const r = (t - resolveStart) / RESOLVE_DUR            // 0..1
          const pop = smoothstep(0, 0.16, r) - smoothstep(0.16, 0.72, r) // 0→1→0
          mesh.scale.setScalar(1 + 0.09 * pop)
          mat.emissiveIntensity = 0.5 + 0.9 * (1 - smoothstep(0, 1, r))
        }
        // Re-place face each frame so it follows the bob/jolt/nod.
        placeOnFace(activeFaceIdx)
        if (browMesh) {
          browMesh.position.copy(vMesh.position)
          browMesh.quaternion.copy(vMesh.quaternion)
        }

        /* ── Scripted move override (lab-triggered) ──
           One state machine, four moods. All move IN PLACE (no translation)
           so the gem never drifts past its canvas edge, flicker the engraved
           V into a mood glyph and back (each swap hidden in a sharp opacity
           dip), and run last so they win over the autonomous transform above.

           happyHello (~2.7s) — brisk friendly tilt, V→waving hand, glow pulse
             + celebration burst, then back.
           curious    (~3.2s) — slow tilt the other way, V→"?", side-to-side
             glance, soft ponder pulse, then back. No burst.
           sleepy     (~3.6s) — slow droop forward + slight lean, gem dims,
             V→"z", then slowly wakes back up. No burst.
           excited    (~2.0s) — raised brows, V→"!", energetic shakes + a
             brighter glow + celebration burst, then back. */
        if (moveActive) {
          const mt = t - moveStart
          let END = 2.7, swapIn = 0.5, swapOut = 2.05, glyphBase = 0.92
          let moodTex: THREE.CanvasTexture | null = helloTex
          if (moveName === 'curious') { END = 3.2; swapOut = 2.45; moodTex = questionTex }
          else if (moveName === 'sleepy') { END = 8.0; swapIn = 0.6; swapOut = 6.8; moodTex = sleepyTex; glyphBase = 0.6 } // ~6s asleep, then a startled wake
          else if (moveName === 'excited') { END = 2.0; swapIn = 0.3; swapOut = 1.5; moodTex = excitedTex; glyphBase = 1.0 }
          else if (moveName === 'love') { END = 3.0; swapOut = 2.4; moodTex = heartTex }
          else if (moveName === 'proud') { END = 2.0; swapIn = 0.4; swapOut = 1.5; moodTex = checkTex; glyphBase = 1.0 }
          else if (moveName === 'spin') { END = 2.6; moodTex = null } // keeps the V, just spins
          else if (moveName === 'focus') { END = 2.6; moodTex = null } // keeps the V, leans in
          else if (moveName === 'loading') { END = 3.6; swapIn = 0.5; swapOut = 3.0; moodTex = loadingTex[0] ?? null; glyphBase = 0.95 } // V→cycling dots→V
          else if (moveName === 'loadSpin') { END = 4.0; swapIn = 0.5; swapOut = 3.4; moodTex = loadingTexAll; glyphBase = 0.95 } // steady dots, gem spins in-plane
          else if (moveName === 'loadPulse') { END = 3.6; swapIn = 0.5; swapOut = 3.0; moodTex = loadingTexAll; glyphBase = 0.95 } // steady dots, gem heartbeat throb
          else if (moveName === 'loadJump1' || moveName === 'loadJump2' || moveName === 'loadJump3') { END = 4.2; moodTex = null } // V hidden; jumping dot meshes carry it
          else if (moveName === 'nod') { END = 1.7; moodTex = null } // coaching: approving nod
          else if (moveName === 'encourage') { END = 1.9; swapIn = 0.4; swapOut = 1.5; moodTex = encourageTex; glyphBase = 1.0 } // coaching: peppy cheer + spark
          else if (moveName === 'explain') { END = 2.6; swapIn = 0.45; swapOut = 2.1; moodTex = explainTex } // coaching: talking + text lines
          else if (moveName === 'consider') { END = 2.8; swapIn = 0.5; swapOut = 2.3; moodTex = considerTex } // coaching: weighing + gauge
          else if (moveName === 'highfive') { END = 1.5; moodTex = null } // new: wind-up + snap
          else if (moveName === 'pumpUp') { END = 1.9; swapIn = 0.4; swapOut = 1.5; moodTex = letsGoTex[letsGoVariant] ?? null; glyphBase = 1.0 } // lets-go: random rocket/chevrons/plane
          else if (moveName === 'concern') { END = 2.4; moodTex = null } // new: caring head shake
          else if (moveName === 'wobble') { END = 1.7; moodTex = null } // new: jelly boing
          else if (moveName === 'idea') { END = 2.1; swapIn = 0.45; swapOut = 1.55; moodTex = ideaTex; glyphBase = 1.0 } // new: V→bolt aha
          else if (moveName === 'levelup') { END = 2.2; swapIn = 0.45; swapOut = 1.6; moodTex = levelupTex[levelupVariant] ?? null; glyphBase = 1.0 } // level up: random steps/tiers/break-through
          else if (moveName === 'lift') { END = 2.1; swapIn = 0.45; swapOut = 1.6; moodTex = liftTex; glyphBase = 1.0 } // strength: V→dumbbell, confident press
          else if (moveName === 'listening') { END = 3.8; swapIn = 0.5; swapOut = 3.2; moodTex = listeningTex } // new: attentive throb + sine
          else if (moveName === 'glitch') { END = 1.3; moodTex = null } // new: digital glitch
          else if (moveName === 'celebrate') { END = 2.2; swapIn = 0.35; swapOut = 1.7; moodTex = celebrateTex; glyphBase = 1.0 } // celebration: V→sparkle, bouncy pop
          else if (moveName === 'cheer') { END = 2.0; swapIn = 0.35; swapOut = 1.55; moodTex = cheerTex; glyphBase = 1.0 } // celebration: V→rays, peppy burst
          else if (moveName === 'win') { END = 2.6; swapIn = 0.45; swapOut = 2.0; moodTex = winTex; glyphBase = 1.0 } // celebration: V→trophy, proud rise + shimmer
          else if (moveName === 'twinkle') { END = 2.4; swapIn = 0.45; swapOut = 1.9; moodTex = twinkleTex; glyphBase = 1.0 } // celebration: V→star, gentle twinkle
          else if (moveName === 'groove') { END = 3.0; swapIn = 0.5; swapOut = 2.4; moodTex = grooveTex; glyphBase = 1.0 } // alive: V→music, dance sway
          if (mt >= END) {
            moveActive = false
            mesh.scale.setScalar(1) // reset any scale puff (love/proud)
            dotMat.opacity = 0 // hide the loading jump dots
            if (vMat.map !== vTex) { vMat.map = vTex; vMat.needsUpdate = true }
          } else {
            // Glyph swaps, timed to the flicker troughs below.
            if (mt >= swapIn && mt < swapOut && moodTex) {
              // Loading cycles through its three dot-frames for a sweep; every
              // other mood holds a single glyph.
              let tex = moodTex
              if (moveName === 'loading' && loadingTex.length) {
                tex = loadingTex[Math.floor((mt - swapIn) / 0.26) % loadingTex.length]
              }
              if (vMat.map !== tex) { vMat.map = tex; vMat.needsUpdate = true }
              // Fire the glyph-synced burst hook once, as the mood glyph
              // lands. For sleepy, the "z" landing IS the moment it falls
              // asleep, so pass the 'sleep' phase (the lab starts the
              // floating-z's then).
              if (!moveGlyphFired) {
                moveGlyphFired = true
                onMoveGlyphRef.current?.(moveName, moveName === 'sleepy' ? 'sleep' : undefined)
              }
            } else if (mt >= swapOut && vMat.map !== vTex) {
              vMat.map = vTex; vMat.needsUpdate = true
            }

            if (moveName === 'curious') {
              // Slow inquisitive tilt the other way + a side-to-side glance.
              const TILT = -0.18
              let tilt = TILT
              if (mt < 0.9) tilt = TILT * smoothstep(0, 0.9, mt)
              else if (mt > 2.4) tilt = TILT * (1 - smoothstep(2.4, 3.2, mt))
              mesh.rotation.z += tilt
              const glanceEnv = smoothstep(0.9, 1.3, mt) * (1 - smoothstep(2.4, 3.0, mt))
              mesh.rotation.y += 0.15 * Math.sin((mt - 0.9) * 3.2) * glanceEnv
            } else if (moveName === 'sleepy') {
              // Nod off, sleep ~6s, then a startled funny wake. WAKE at 6.8
              // (matches swapOut above so the z→V flash lands on wake).
              const WAKE = 6.8
              if (mt < WAKE) {
                // Droop forward + lean + dim over 0-1s, then hold asleep with
                // little living sleep movements: a slow breathing bob, a soft
                // rock, and a faint scale + glow breath — all gentle, until
                // the jolt.
                const d = mt < 1.0 ? smoothstep(0, 1.0, mt) : 1
                const s = mt > 1.0 ? mt - 1.0 : 0          // seconds asleep
                const breath = Math.sin(s * 1.8)            // ~3.5s breaths
                const rock = Math.sin(s * 0.8 + 0.6)        // slower rocking sway
                mesh.rotation.x += 0.26 * d + 0.025 * breath * d   // head bobs with breath
                mesh.rotation.z += 0.10 * d + 0.03 * rock * d      // gentle rock
                mesh.rotation.y += 0.02 * Math.sin(s * 0.5) * d    // faint lull
                mesh.scale.setScalar(1 + 0.018 * breath * d)
                mat.emissiveIntensity = (0.5 - 0.34 * d) + 0.03 * breath
              } else {
                // SHOCK AWAKE: snap upright with a boing, pop bigger, jolt-
                // shake, and flash bright. Once, at the wake beat: re-open the
                // brow window with wide surprised brows + tell the host (so it
                // can stop the z's and fire a startle burst).
                const w = mt - WAKE
                if (!moveWakeFired) {
                  moveWakeFired = true
                  happyStart = t
                  if (browMat && browTexMap.excited) { browMat.map = browTexMap.excited; browMat.needsUpdate = true }
                  onMoveGlyphRef.current?.('sleepy', 'wake')
                }
                const release = 1 - smoothstep(0, 0.18, w)        // droop snaps off
                const boing = Math.sin(w * 17) * Math.exp(-w * 5) * 0.07
                mesh.rotation.x += 0.26 * release - boing
                mesh.rotation.z += 0.10 * release
                const pop = smoothstep(0, 0.1, w) - smoothstep(0.1, 0.7, w) // 0→1→0
                // Keep the wake "pop" small enough that the gem never crosses
                // the canvas edge (at rest it already fills ~87% of the frame,
                // so a big scale-up clipped the silhouette at a hard line). The
                // boing + jolt-shake carry the startle energy instead.
                mesh.scale.setScalar(1 + 0.1 * pop)
                const shake = 1 - smoothstep(0, 0.45, w)
                mesh.rotation.y += 0.06 * shake * Math.sin(w * 58)
                mesh.rotation.x += 0.04 * shake * Math.sin(w * 49 + 1)
                mat.emissiveIntensity = 0.5 + 0.7 * (1 - smoothstep(0, 0.5, w))
              }
            } else if (moveName === 'excited') {
              // Energetic shakes + a brighter glow.
              let env = 1
              if (mt < 0.2) env = smoothstep(0, 0.2, mt)
              else if (mt > 1.5) env = 1 - smoothstep(1.5, 2.0, mt)
              mesh.rotation.x += 0.045 * env * Math.sin(t * 46)
              mesh.rotation.y += 0.045 * env * Math.sin(t * 41 + 1)
              mesh.rotation.z += 0.03 * env * Math.sin(t * 53 + 0.5)
              mat.emissiveIntensity = 0.5 + 0.5 * env
            } else if (moveName === 'love') {
              // Slow warm sway, a gentle lean-in, and a soft warm glow.
              let env = 1
              if (mt < 0.6) env = smoothstep(0, 0.6, mt)
              else if (mt > 2.4) env = 1 - smoothstep(2.4, 3.0, mt)
              mesh.rotation.z += 0.13 * Math.sin(mt * 2.2) * env
              mesh.scale.setScalar(1 + 0.05 * env)
              mat.emissiveIntensity = 0.5 + 0.35 * env
            } else if (moveName === 'proud') {
              // A proud scale-up "puff" + chin lift + brighter glow.
              let up = 1
              if (mt < 0.5) up = smoothstep(0, 0.5, mt)
              else if (mt > 1.5) up = 1 - smoothstep(1.5, 2.0, mt)
              mesh.scale.setScalar(1 + 0.15 * up)
              mesh.rotation.x += -0.08 * up
              mat.emissiveIntensity = 0.5 + 0.4 * up
            } else if (moveName === 'lift') {
              // Confident press: a quick load (small dip), then a strong pop
              // up + chin lift + brighter glow, like pressing a weight overhead.
              let up = 1
              if (mt < 0.55) up = smoothstep(0, 0.55, mt)
              else if (mt > 1.5) up = 1 - smoothstep(1.5, 2.1, mt)
              const load = mt < 0.24 ? Math.sin((mt / 0.24) * Math.PI) * 0.05 : 0 // brief dip before the press
              mesh.scale.setScalar(1 + 0.17 * up - load)
              mesh.rotation.x += -0.09 * up
              mat.emissiveIntensity = 0.5 + 0.45 * up
            } else if (moveName === 'spin') {
              // One full show-off revolution about Y, eased start + stop.
              mesh.rotation.y += Math.PI * 2 * smoothstep(0, 2.4, mt)
            } else if (moveName === 'focus') {
              // Determined lean-in: scale up, brighten, tiny lock-on scan.
              let env = 1
              if (mt < 0.7) env = smoothstep(0, 0.7, mt)
              else if (mt > 2.0) env = 1 - smoothstep(2.0, 2.6, mt)
              mesh.scale.setScalar(1 + 0.06 * env)
              mesh.rotation.y += 0.04 * Math.sin(mt * 4) * env
              mat.emissiveIntensity = 0.5 + 0.45 * env
            } else if (moveName === 'loading') {
              // Calm "thinking" sway — a slow side-to-side lull + a soft steady
              // glow, while the three dots cycle on the face. Patient, cozy.
              let env = 1
              if (mt < 0.5) env = smoothstep(0, 0.5, mt)
              else if (mt > END - 0.6) env = 1 - smoothstep(END - 0.6, END, mt)
              mesh.rotation.z += 0.05 * Math.sin(mt * 2.0) * env
              mesh.rotation.y += 0.045 * Math.sin(mt * 1.4 + 0.5) * env
              mat.emissiveIntensity = 0.5 + 0.16 * env
            } else if (moveName === 'loadSpin') {
              // Loading wheel: the gem rolls in-plane (face stays forward) so
              // the steady dots spin around the center like a spinner. Smooth
              // ease in + out over the whole move.
              const p = smoothstep(0, 1, mt / END)
              mesh.rotation.z += Math.PI * 2 * 2.5 * p
              let env = 1
              if (mt < 0.5) env = smoothstep(0, 0.5, mt)
              else if (mt > END - 0.6) env = 1 - smoothstep(END - 0.6, END, mt)
              mat.emissiveIntensity = 0.5 + 0.14 * env
            } else if (moveName === 'loadPulse') {
              // Calm heartbeat: the gem throbs + glows in repeating ~0.95s
              // beats while the dots hold lit. A breathing loader.
              let env = 1
              if (mt < 0.5) env = smoothstep(0, 0.5, mt)
              else if (mt > END - 0.6) env = 1 - smoothstep(END - 0.6, END, mt)
              const throb = 0.5 - 0.5 * Math.cos((mt * Math.PI * 2) / 0.95)
              mesh.scale.setScalar(1 + 0.07 * throb * env)
              mat.emissiveIntensity = 0.46 + 0.4 * throb * env
            } else if (moveName === 'loadJump1' || moveName === 'loadJump2' || moveName === 'loadJump3') {
              // Jumping-dots loading icon: three mint dots bounce up/down in
              // front of the gem (V hidden). Three styles to compare.
              const style = moveName === 'loadJump1' ? 1 : moveName === 'loadJump2' ? 2 : 3
              let env = 1
              if (mt < 0.3) env = smoothstep(0, 0.3, mt)
              else if (mt > END - 0.4) env = 1 - smoothstep(END - 0.4, END, mt)
              dotMat.opacity = env
              for (let i = 0; i < 3; i++) dotMeshes[i].position.y = jumpY(style, mt, i)
              mat.emissiveIntensity = 0.5 + 0.12 * env // gem holds a calm steady glow
            } else if (moveName === 'nod') {
              // Coaching: a warm approving nod — a couple of easy head dips + soft glow lift.
              let env = 1
              if (mt < 0.2) env = smoothstep(0, 0.2, mt)
              else if (mt > END - 0.3) env = 1 - smoothstep(END - 0.3, END, mt)
              mesh.rotation.x += 0.13 * env * Math.sin(mt * 7.0)
              mat.emissiveIntensity = 0.5 + 0.18 * env
            } else if (moveName === 'encourage') {
              // Coaching: peppy "you got this" — bouncy chin-up bobs + scale pop + glow swell.
              let env = 1
              if (mt < 0.25) env = smoothstep(0, 0.25, mt)
              else if (mt > END - 0.4) env = 1 - smoothstep(END - 0.4, END, mt)
              const beat = Math.abs(Math.sin(mt * 5.4))
              mesh.rotation.x += -0.07 * beat * env
              mesh.scale.setScalar(1 + 0.05 * beat * env)
              mat.emissiveIntensity = 0.5 + 0.4 * beat * env
            } else if (moveName === 'explain') {
              // Coaching: gentle talking gesture — little nods + a soft sway while it speaks.
              let env = 1
              if (mt < 0.4) env = smoothstep(0, 0.4, mt)
              else if (mt > END - 0.5) env = 1 - smoothstep(END - 0.5, END, mt)
              mesh.rotation.x += 0.04 * env * Math.sin(mt * 8.5)
              mesh.rotation.z += 0.05 * env * Math.sin(mt * 1.8)
              mesh.rotation.y += 0.03 * env * Math.sin(mt * 2.4 + 0.5)
              mat.emissiveIntensity = 0.5 + 0.13 * env
            } else if (moveName === 'consider') {
              // Coaching: weighing it up — a slow lean one way then the other, like "hmm".
              let env = 1
              if (mt < 0.5) env = smoothstep(0, 0.5, mt)
              else if (mt > END - 0.6) env = 1 - smoothstep(END - 0.6, END, mt)
              mesh.rotation.z += 0.14 * env * Math.sin(mt * 1.6)
              mesh.rotation.x += 0.04 * env * Math.sin(mt * 0.9)
              mat.emissiveIntensity = 0.5 + 0.12 * env
            } else if (moveName === 'highfive') {
              // Wind up to one side, then a fast forward swing + snap, small recoil.
              let z = 0
              if (mt < 0.35) z = -0.24 * smoothstep(0, 0.35, mt)
              else { const s = mt - 0.35; z = (-0.24 + 0.52 * smoothstep(0, 0.16, s)) * (1 - smoothstep(0.5, 1.1, s)) }
              mesh.rotation.z += z
              mesh.rotation.x += -0.05 * (1 - smoothstep(0.5, 1.0, mt))
              mat.emissiveIntensity = 0.5 + 0.32 * (1 - smoothstep(0.3, 0.9, mt))
            } else if (moveName === 'pumpUp') {
              // Two energetic chin-up lunges + scale pops + brighten.
              let env = 1
              if (mt < 0.2) env = smoothstep(0, 0.2, mt)
              else if (mt > END - 0.4) env = 1 - smoothstep(END - 0.4, END, mt)
              const beat = Math.max(0, Math.sin(mt * 7.2))
              mesh.rotation.x += -0.12 * beat * env
              mesh.scale.setScalar(1 + 0.07 * beat * env)
              mat.emissiveIntensity = 0.5 + 0.45 * beat * env
            } else if (moveName === 'concern') {
              // Slow caring head shake + a small lean + a gentle dim.
              let env = 1
              if (mt < 0.4) env = smoothstep(0, 0.4, mt)
              else if (mt > END - 0.5) env = 1 - smoothstep(END - 0.5, END, mt)
              mesh.rotation.z += 0.1 * env * Math.sin(mt * 4.0)
              mesh.rotation.x += 0.05 * env
              mat.emissiveIntensity = 0.5 - 0.14 * env
            } else if (moveName === 'wobble') {
              // Playful damped jelly wobble that settles.
              const decay = Math.exp(-mt * 2.6)
              mesh.rotation.z += 0.22 * decay * Math.sin(mt * 16)
              mesh.scale.setScalar(1 + 0.06 * decay * Math.sin(mt * 16 + 1))
              mat.emissiveIntensity = 0.5 + 0.12 * decay
            } else if (moveName === 'idea') {
              // An "aha" pop: quick scale-up + bright flash as the bolt flickers in.
              let up = 0
              if (mt < 0.5) up = smoothstep(0, 0.5, mt)
              else up = 1 - smoothstep(1.3, 2.1, mt)
              mesh.scale.setScalar(1 + 0.12 * up)
              mesh.rotation.x += -0.05 * up
              mat.emissiveIntensity = 0.5 + 0.55 * up
            } else if (moveName === 'levelup') {
              // A rising lift as the chevron flickers in — up and brighter.
              let up = 0
              if (mt < 0.5) up = smoothstep(0, 0.5, mt)
              else up = 1 - smoothstep(1.4, 2.2, mt)
              mesh.rotation.x += -0.14 * up
              mesh.scale.setScalar(1 + 0.08 * up)
              mat.emissiveIntensity = 0.5 + 0.4 * up
            } else if (moveName === 'listening') {
              // A gentle attentive throb — leans in softly and pulses, like it is
              // hearing you out.
              let env = 1
              if (mt < 0.5) env = smoothstep(0, 0.5, mt)
              else if (mt > END - 0.6) env = 1 - smoothstep(END - 0.6, END, mt)
              const throb = 0.5 - 0.5 * Math.cos((mt * Math.PI * 2) / 0.7)
              mesh.scale.setScalar(1 + 0.04 * throb * env)
              mesh.rotation.z += 0.02 * env * Math.sin(mt * 1.6)
              mat.emissiveIntensity = 0.5 + 0.2 * throb * env
            } else if (moveName === 'glitch') {
              // Digital glitch: rapid jitter, hard scale stutter, emissive strobe.
              let env = 1
              if (mt > END - 0.25) env = 1 - smoothstep(END - 0.25, END, mt)
              mesh.rotation.x += 0.05 * env * Math.sin(t * 90)
              mesh.rotation.y += 0.06 * env * Math.sin(t * 77 + 1.3)
              mesh.rotation.z += 0.045 * env * Math.sin(t * 113 + 0.6)
              const step = Math.sin(t * 47) > 0.6 ? 1 : 0
              mesh.scale.setScalar(1 + 0.06 * step * env)
              mat.emissiveIntensity = 0.5 + (Math.sin(t * 60) > 0 ? 0.6 : -0.12) * env
            } else if (moveName === 'celebrate') {
              // Celebration: a big bouncy pop + happy shimmy + bright double-flash
              // as the sparkle lands. Stays camera-facing (no spin) so the glyph reads.
              let env = 1
              if (mt < 0.3) env = smoothstep(0, 0.3, mt)
              else if (mt > END - 0.5) env = 1 - smoothstep(END - 0.5, END, mt)
              const bounce = Math.abs(Math.sin(mt * 6.2))
              mesh.scale.setScalar(1 + 0.13 * bounce * env)
              mesh.rotation.z += 0.10 * env * Math.sin(mt * 11)
              mesh.rotation.x += -0.05 * bounce * env
              mat.emissiveIntensity = 0.5 + 0.6 * bounce * env
            } else if (moveName === 'cheer') {
              // Celebration: rapid peppy bobs while the rays pulse-burst bright.
              let env = 1
              if (mt < 0.25) env = smoothstep(0, 0.25, mt)
              else if (mt > END - 0.4) env = 1 - smoothstep(END - 0.4, END, mt)
              const beat = Math.max(0, Math.sin(mt * 7.6))
              mesh.scale.setScalar(1 + 0.06 * beat * env)
              mesh.rotation.x += -0.08 * beat * env
              mat.emissiveIntensity = 0.5 + 0.5 * beat * env
            } else if (moveName === 'win') {
              // Celebration: a proud lift that holds high, with a shimmer glow and
              // a tiny victory wiggle while the trophy shows.
              let up = 0
              if (mt < 0.5) up = smoothstep(0, 0.5, mt)
              else up = 1 - smoothstep(END - 0.6, END, mt)
              mesh.rotation.x += -0.12 * up
              mesh.scale.setScalar(1 + 0.07 * up)
              mesh.rotation.z += 0.04 * up * Math.sin(mt * 5.5)
              mat.emissiveIntensity = 0.5 + 0.35 * up + 0.12 * up * Math.sin(mt * 9)
            } else if (moveName === 'twinkle') {
              // Celebration: a soft pop + a fast sparkly brightness twinkle.
              let env = 1
              if (mt < 0.4) env = smoothstep(0, 0.4, mt)
              else if (mt > END - 0.5) env = 1 - smoothstep(END - 0.5, END, mt)
              mesh.scale.setScalar(1 + 0.05 * env)
              mesh.rotation.z += 0.05 * env * Math.sin(mt * 2.2)
              const tw = 0.5 + 0.5 * Math.sin(mt * 12)
              mat.emissiveIntensity = 0.5 + 0.3 * env * tw
            } else if (moveName === 'groove') {
              // Alive: a happy dance — sways to a beat with an offbeat bob, looping.
              let env = 1
              if (mt < 0.4) env = smoothstep(0, 0.4, mt)
              else if (mt > END - 0.5) env = 1 - smoothstep(END - 0.5, END, mt)
              mesh.rotation.z += 0.16 * env * Math.sin(mt * 4.4)
              mesh.rotation.x += 0.05 * env * Math.sin(mt * 8.8)
              mesh.scale.setScalar(1 + 0.03 * env * Math.abs(Math.sin(mt * 4.4)))
              mat.emissiveIntensity = 0.5 + 0.15 * env * Math.abs(Math.sin(mt * 4.4))
            } else {
              // happyHello — brisk friendly head-tilt + a gentle glow pulse.
              const TILT = 0.2
              let tilt = TILT
              if (mt < 0.6) tilt = TILT * smoothstep(0, 0.6, mt)
              else if (mt > 2.0) tilt = TILT * (1 - smoothstep(2.0, 2.7, mt))
              mesh.rotation.z += tilt
              if (mt >= 0.6 && mt < 2.0) {
                const p = (mt - 0.6) / 1.4
                mat.emissiveIntensity = 0.6 + 0.5 * Math.sin(p * Math.PI)
              }
            }

            // Glyph opacity. Spin keeps the V and fades it naturally as the
            // resting face rotates away from the camera and back. Every other
            // move holds the glyph at its base and eases to near-zero at each
            // swap point so the texture change happens unseen (no strobe).
            let op: number
            if (moveName === 'spin') {
              op = glyphBase * smoothstep(0.45, 0.9, getFaceWorldDot(activeFaceIdx))
            } else if (moveName === 'loadJump1' || moveName === 'loadJump2' || moveName === 'loadJump3') {
              op = 0 // V is hidden; the jumping dot meshes are the glyph
            } else if (moveName === 'glitch') {
              op = glyphBase * (Math.sin(t * 80) > -0.3 ? 1 : 0.18) // V flickers like a corrupted signal
            } else if (moodTex === null) {
              op = glyphBase // focus: V stays steady, no swap to dip around
            } else {
              // Cross-fade each glyph swap: a smooth near-zero gaussian dip at
              // swapIn/swapOut, so the texture changes while invisible and eases
              // back up. (Was a shallow 10% dip + a binary strobe, which let the
              // swap show through and read as a chop.)
              const dipA = Math.exp(-Math.pow((mt - swapIn) / 0.06, 2))
              const dipB = Math.exp(-Math.pow((mt - swapOut) / 0.06, 2))
              // Sleepy holds a DIM "z" while asleep, then the V pops bright on
              // wake (swapOut is the wake beat).
              const base = (moveName === 'sleepy' && mt >= swapOut) ? 0.95 : glyphBase
              op = base * (1 - 0.98 * dipA) * (1 - 0.98 * dipB)
            }
            vMat.opacity = Math.max(0, Math.min(1, op))
          }
        }
      } else if (!prefersReducedMotion) {
        baseY += dt * PARAMS.rotationSpeed
        const wobbleX = Math.sin(t * PARAMS.rotationSpeed * 0.5) * PARAMS.wobbleAmplitude
        current.x += (target.x - current.x) * PARAMS.spring
        current.y += (target.y - current.y) * PARAMS.spring
        mesh.rotation.x = wobbleX + current.x
        mesh.rotation.y = baseY + current.y
        mesh.rotation.z = Math.sin(t * 0.03) * 0.04

        /* Per-face V state machine. */
        // If no active cycle, look for a face about to be camera-facing
        // (and not the one we just finished, so we don't immediately
        // re-fire on the same face).
        if (cycleComplete) {
          let bestIdx = -1
          let bestDot = -Infinity
          for (let i = 0; i < faces.length; i++) {
            if (i === activeFaceIdx) continue
            const d = getFaceWorldDot(i)
            if (d > bestDot) { bestDot = d; bestIdx = i }
          }
          if (bestIdx >= 0 && bestDot >= CLAIM_DOT) {
            activeFaceIdx = bestIdx
            cycleStart    = t
            cycleComplete = false
            activePattern = bestIdx % HOLD_FLICKERS.length
            placeOnFace(activeFaceIdx)
            // Alternate the painted glyph on each new face cycle so a
            // celebration screen reads "check ... mentor-icon ... check
            // ... mentor-icon" without an HTML overlay (no DOM ghost).
            if (glyphTextures.length > 1) {
              activeGlyphIdx = (activeGlyphIdx + 1) % glyphTextures.length
              vMat.map = glyphTextures[activeGlyphIdx]
              vMat.needsUpdate = true
            }
          }
        }

        // Compute the cycle's intrinsic opacity (slow on / hold / fast off).
        let cycleOpacity = 0
        if (!cycleComplete) {
          const localT = t - cycleStart
          if (localT < FADE_IN_DUR) {
            const ramp  = localT / FADE_IN_DUR
            const flick = 0.55 + 0.45 * Math.abs(Math.sin(t * 11 + activePattern))
            cycleOpacity = ramp * flick
          } else if (localT < FADE_IN_DUR + HOLD_DUR) {
            cycleOpacity = HOLD_FLICKERS[activePattern](t, activePattern)
          } else if (localT < CYCLE_DUR) {
            const off    = (localT - FADE_IN_DUR - HOLD_DUR) / FADE_OUT_DUR
            const strobe = Math.sin(t * 38 + activePattern) > -0.4 ? 1 : 0.1
            cycleOpacity = (1 - off) * strobe
          } else {
            cycleComplete = true
            cycleOpacity  = 0
          }
        }

        // Visibility envelope on the ACTIVE face — guarantees the V
        // is off before its face rotates out of view.
        const envelope = activeFaceIdx >= 0
          ? smoothstep(ENV_LO, ENV_HI, getFaceWorldDot(activeFaceIdx))
          : 0
        vMat.opacity = Math.max(0, Math.min(1, cycleOpacity * envelope))
      } else {
        /* Reduced motion: place V on the currently most-front face and
           hold a gentle steady-state — no animation. */
        if (activeFaceIdx < 0) {
          let bestIdx = 0
          let bestDot = -Infinity
          for (let i = 0; i < faces.length; i++) {
            const d = getFaceWorldDot(i)
            if (d > bestDot) { bestDot = d; bestIdx = i }
          }
          activeFaceIdx = bestIdx
          placeOnFace(activeFaceIdx)
        }
        // Reduced motion: loading degrades to a calm opacity breathe on the V
        // only — no scale, no rings (the host's ring CSS is disabled too).
        if (loadingRef.current) {
          const slow = 0.5 - 0.5 * Math.cos(t * 1.4) // ~4.5s gentle breath
          vMat.opacity = 0.3 + 0.3 * slow
          mat.emissiveIntensity = 0.42 + 0.22 * slow
        } else {
          vMat.opacity = 0.45
        }
      }

      renderer.render(scene, camera)
      rafId = requestAnimationFrame(tick)
    }

    /* Cue the wrapper that the first frame is committed; CSS can fade in. */
    requestAnimationFrame(() => {
      renderer.render(scene, camera)
      document.documentElement.classList.add('crystal-ready')
      tick()
    })

    return () => {
      cancelAnimationFrame(rafId)
      if (controlRef) controlRef.current = null
      if (resizeTimer) clearTimeout(resizeTimer)
      resizeObserver.disconnect()
      window.removeEventListener('mousemove', handleMouse)
      window.removeEventListener('touchmove', handleTouch)
      mesh.removeFromParent()
      edgesGeo.dispose()
      wireMat.dispose()
      vGeo.dispose()
      vMat.dispose()
      vTex.dispose()
      if (vTex2) vTex2.dispose()
      if (helloTex) helloTex.dispose()
      if (questionTex) questionTex.dispose()
      if (sleepyTex) sleepyTex.dispose()
      if (excitedTex) excitedTex.dispose()
      if (heartTex) heartTex.dispose()
      if (checkTex) checkTex.dispose()
      if (liftTex) liftTex.dispose()
      for (const tx of loadingTex) tx.dispose()
      if (loadingTexAll) loadingTexAll.dispose()
      for (const dm of dotMeshes) dm.removeFromParent()
      dotGeo.dispose()
      dotMat.dispose()
      dotTex.dispose()
      for (const t of Object.values(browTexMap)) t.dispose()
      browGeo?.dispose()
      browMat?.dispose()
      geo.dispose()
      mat.dispose()
      envTex.dispose()
      pmrem.dispose()
      renderer.dispose()
      document.documentElement.classList.remove('crystal-ready')
    }
  }, [shape, tint])

  return <canvas ref={canvasRef} aria-label={`Mint glass ${shape}`} />
}
