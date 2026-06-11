/**
 * SolarOrb — Canvas-based solar visualization for voice agent states.
 *
 * Renders a radial-gradient sun with corona, surface activity, and a
 * transit (eclipse) effect driven by the agent's conversation state.
 *
 * States:
 *   idle           → still, gentle glow, no animation
 *   user-speaking  → gentle expansion, brighter corona, subtle surface
 *   speaking       → active pulsing, corona flicker, surface shimmer
 *   thinking       → transit: dark sphere crosses sun, corona intensifies
 *   offline        → dim, cold, no animation
 *
 * A "season" prop (0–1) interpolates colors from Winter (#00d4ff) to
 * Summer (#ff4500), passing through teal → golden → amber.
 */

import { useRef, useEffect } from 'react'

export type SolarOrbState = 'idle' | 'speaking' | 'user-speaking' | 'thinking' | 'offline'

export interface SolarOrbProps {
  state: SolarOrbState
  /** Canvas pixel size (default 140). */
  size?: number
  /** Season warmth: 0 = Winter (#00d4ff) → 1 = Summer (#ff4500). Default 1.0 (hot). */
  season?: number
  className?: string
}

// ── Color helpers ────────────────────────────────────────────

type RGB = [number, number, number]

function clamp(v: number, lo: number, hi: number) {
  return v < lo ? lo : v > hi ? hi : v
}

function lerp(a: number, b: number, t: number) {
  return a + (b - a) * clamp(t, 0, 1)
}

function lerpRGB(a: RGB, b: RGB, t: number): RGB {
  return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)]
}

function rgba(c: RGB, a: number): string {
  return `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`
}

function brighten(c: RGB, n: number): RGB {
  return [Math.min(255, c[0] + n), Math.min(255, c[1] + n), Math.min(255, c[2] + n)]
}

function darken(c: RGB, n: number): RGB {
  return [Math.max(0, c[0] - n), Math.max(0, c[1] - n), Math.max(0, c[2] - n)]
}

// ── Season color ramp (cold → hot) ──────────────────────────

const RAMP: Array<{ t: number; c: RGB }> = [
  { t: 0,    c: [0, 212, 255] },   // Winter: icy cyan
  { t: 0.25, c: [0, 210, 160] },   // Early spring: teal
  { t: 0.5,  c: [255, 210, 0] },   // Late spring: golden
  { t: 0.75, c: [255, 140, 0] },   // Early summer: amber
  { t: 1,    c: [255, 69, 0] },    // Summer: #ff4500
]

function seasonColor(s: number): RGB {
  s = clamp(s, 0, 1)
  for (let i = 0; i < RAMP.length - 1; i++) {
    if (s <= RAMP[i + 1].t) {
      const local = (s - RAMP[i].t) / (RAMP[i + 1].t - RAMP[i].t)
      return lerpRGB(RAMP[i].c, RAMP[i + 1].c, local)
    }
  }
  return RAMP[RAMP.length - 1].c
}

// ── Per-state animation parameter targets ────────────────────

interface Params {
  pulseSpeed: number
  pulseAmp: number
  coronaIntensity: number
  coronaPulseSpeed: number
  coronaPulseAmp: number
  dim: number
  surface: number
}

const STATE_PARAMS: Record<SolarOrbState, Params> = {
  idle: {
    pulseSpeed: 0, pulseAmp: 0,
    coronaIntensity: 0.2, coronaPulseSpeed: 0, coronaPulseAmp: 0,
    dim: 1.0, surface: 0,
  },
  'user-speaking': {
    pulseSpeed: 0, pulseAmp: 0,
    coronaIntensity: 0.5, coronaPulseSpeed: 1.8, coronaPulseAmp: 0,
    dim: 1.0, surface: 0.35,
  },
  speaking: {
    pulseSpeed: 0, pulseAmp: 0,
    coronaIntensity: 0.65, coronaPulseSpeed: 2.5, coronaPulseAmp: 0,
    dim: 1.0, surface: 0.6,
  },
  thinking: {
    pulseSpeed: 0, pulseAmp: 0,
    coronaIntensity: 0.9, coronaPulseSpeed: 1.0, coronaPulseAmp: 0,
    dim: 0.7, surface: 0.2,
  },
  offline: {
    pulseSpeed: 0, pulseAmp: 0,
    coronaIntensity: 0.06, coronaPulseSpeed: 0, coronaPulseAmp: 0,
    dim: 0.3, surface: 0,
  },
}

// ── Component ────────────────────────────────────────────────

export function SolarOrb({
  state,
  size = 140,
  season = 1.0,
  className,
}: SolarOrbProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rafRef = useRef(0)

  // Mutable refs read inside the render loop — avoids re-creating the loop
  // when props change. The loop smoothly interpolates toward targets.
  const stateRef = useRef(state)
  const seasonRef = useRef(season)
  const sizeRef = useRef(size)

  const paramsRef = useRef<Params>({ ...STATE_PARAMS[state] })
  const transitRef = useRef(0)
  const lastTimeRef = useRef(0)

  useEffect(() => { stateRef.current = state }, [state])
  useEffect(() => { seasonRef.current = season }, [season])
  useEffect(() => { sizeRef.current = size }, [size])

  // Single RAF loop — stable across prop changes
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    function frame(now: number) {
      const dt = lastTimeRef.current
        ? Math.min((now - lastTimeRef.current) / 1000, 0.1)
        : 0.016
      lastTimeRef.current = now

      const s = sizeRef.current
      const dpr = window.devicePixelRatio || 1

      // Resize canvas if needed
      if (canvas!.width !== s * dpr || canvas!.height !== s * dpr) {
        canvas!.width = s * dpr
        canvas!.height = s * dpr
      }
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0)

      // ── Smooth param transitions ──
      const target = STATE_PARAMS[stateRef.current]
      const p = paramsRef.current
      const rate = 3 // transition speed (higher = faster)

      p.pulseSpeed       = lerp(p.pulseSpeed,       target.pulseSpeed,       dt * rate)
      p.pulseAmp         = lerp(p.pulseAmp,         target.pulseAmp,         dt * rate)
      p.coronaIntensity  = lerp(p.coronaIntensity,  target.coronaIntensity,  dt * rate)
      p.coronaPulseSpeed = lerp(p.coronaPulseSpeed, target.coronaPulseSpeed, dt * rate)
      p.coronaPulseAmp   = lerp(p.coronaPulseAmp,   target.coronaPulseAmp,   dt * rate)
      p.dim              = lerp(p.dim,              target.dim,              dt * rate)
      p.surface          = lerp(p.surface,          target.surface,          dt * rate)

      // ── Transit progression ──
      if (stateRef.current === 'thinking') {
        transitRef.current += dt * 0.18
        if (transitRef.current > 1) transitRef.current = 0 // seamless loop
      } else if (transitRef.current > 0) {
        // Exit transit: slide off to the right quickly
        transitRef.current += dt * 0.6
        if (transitRef.current >= 1) transitRef.current = 0
      }

      // ── Computed animation values ──
      const t = now / 1000
      const cx = s / 2
      const cy = s / 2
      const baseR = s * 0.22

      const pulse = p.pulseAmp > 0.001
        ? 1 + Math.sin(t * p.pulseSpeed * Math.PI * 2) * p.pulseAmp
        : 1
      const cPulse = p.coronaPulseAmp > 0.001
        ? 1 + Math.sin(t * p.coronaPulseSpeed * Math.PI * 2) * p.coronaPulseAmp
        : 1

      const sunR = baseR * pulse
      const coronaR = baseR * 1.6 * cPulse
      const dim = p.dim
      const transit = transitRef.current

      // Season-based colors
      const base = seasonColor(seasonRef.current)
      const bright = brighten(base, 80)
      const dark = darken(base, 60)

      // ── Clear ──
      ctx!.clearRect(0, 0, s, s)

      // ── 1. Ambient background glow ──
      const bg = ctx!.createRadialGradient(cx, cy, 0, cx, cy, s * 0.48)
      bg.addColorStop(0, rgba(base, 0.14 * dim))
      bg.addColorStop(0.5, rgba(base, 0.04 * dim))
      bg.addColorStop(1, 'rgba(0,0,0,0)')
      ctx!.fillStyle = bg
      ctx!.fillRect(0, 0, s, s)

      // ── 2. Corona (outer glow ring) ──
      const cg = ctx!.createRadialGradient(cx, cy, sunR * 0.85, cx, cy, coronaR * 1.5)
      cg.addColorStop(0, rgba(base, p.coronaIntensity * dim * 0.55))
      cg.addColorStop(0.25, rgba(bright, p.coronaIntensity * dim * 0.3))
      cg.addColorStop(0.55, rgba(base, p.coronaIntensity * dim * 0.12))
      cg.addColorStop(1, 'rgba(0,0,0,0)')
      ctx!.fillStyle = cg
      ctx!.beginPath()
      ctx!.arc(cx, cy, coronaR * 1.5, 0, Math.PI * 2)
      ctx!.fill()

      // ── 3. Sun body (radial gradient, warm center — no white) ──
      const gx = cx - sunR * 0.1
      const gy = cy - sunR * 0.1
      const sg = ctx!.createRadialGradient(gx, gy, 0, cx, cy, sunR)
      sg.addColorStop(0, rgba(bright, 0.95 * dim))
      sg.addColorStop(0.3, rgba(base, 0.95 * dim))
      sg.addColorStop(0.7, rgba(dark, 0.9 * dim))
      sg.addColorStop(1, rgba(darken(dark, 30), 0.8 * dim))
      ctx!.fillStyle = sg
      ctx!.beginPath()
      ctx!.arc(cx, cy, sunR, 0, Math.PI * 2)
      ctx!.fill()

      // Limb darkening ring (subtle outer edge)
      const limb = ctx!.createRadialGradient(cx, cy, sunR * 0.75, cx, cy, sunR)
      limb.addColorStop(0, 'rgba(0,0,0,0)')
      limb.addColorStop(1, rgba(darken(dark, 40), 0.25 * dim))
      ctx!.fillStyle = limb
      ctx!.beginPath()
      ctx!.arc(cx, cy, sunR, 0, Math.PI * 2)
      ctx!.fill()

      // ── 4. Surface activity (animated bright spots) ──
      if (p.surface > 0.01) {
        ctx!.save()
        ctx!.globalCompositeOperation = 'screen'
        // Clip to sun disc so spots don't leak
        ctx!.beginPath()
        ctx!.arc(cx, cy, sunR - 1, 0, Math.PI * 2)
        ctx!.clip()

        for (let i = 0; i < 6; i++) {
          const angle = (t * (0.3 + i * 0.12) + i * 1.05) % (Math.PI * 2)
          const dist = sunR * (0.25 + Math.sin(t * 0.4 + i * 0.8) * 0.2)
          const sx = cx + Math.cos(angle) * dist
          const sy = cy + Math.sin(angle) * dist
          const spotR = sunR * 0.2 * p.surface

          const sp = ctx!.createRadialGradient(sx, sy, 0, sx, sy, spotR)
          sp.addColorStop(0, rgba(bright, 0.4 * p.surface * dim))
          sp.addColorStop(1, 'rgba(0,0,0,0)')
          ctx!.fillStyle = sp
          ctx!.beginPath()
          ctx!.arc(sx, sy, spotR, 0, Math.PI * 2)
          ctx!.fill()
        }
        ctx!.restore()
      }

      // ── 5. Transit dark sphere (solar eclipse / "thinking") ──
      if (transit > 0.001) {
        // Ease the transit path so it decelerates at center
        const eased = (1 - Math.cos(transit * Math.PI)) / 2
        // Multiplier 1.8: sphere is fully outside the clipped sun at 0 and 1
        const tx = cx + (eased * 2 - 1) * sunR * 1.8
        const ty = cy - sunR * 0.04
        const tr = sunR * 0.65

        // Clip to sun disc — sphere only appears over the sun face
        ctx!.save()
        ctx!.beginPath()
        ctx!.arc(cx, cy, sunR + 1, 0, Math.PI * 2)
        ctx!.clip()

        // Dark sphere body
        const dg = ctx!.createRadialGradient(tx, ty, 0, tx, ty, tr)
        dg.addColorStop(0, 'rgba(5,5,10,0.97)')
        dg.addColorStop(0.85, 'rgba(8,8,15,0.94)')
        dg.addColorStop(1, 'rgba(15,15,25,0.4)')
        ctx!.fillStyle = dg
        ctx!.beginPath()
        ctx!.arc(tx, ty, tr, 0, Math.PI * 2)
        ctx!.fill()

        ctx!.restore()

        // Corona edge glow around the transiting sphere
        const edgeVis = clamp(transit * 3, 0, 1)
        const eg = ctx!.createRadialGradient(tx, ty, tr * 0.9, tx, ty, tr * 1.5)
        eg.addColorStop(0, 'rgba(0,0,0,0)')
        eg.addColorStop(0.35, rgba(bright, edgeVis * 0.5 * dim))
        eg.addColorStop(0.7, rgba(base, edgeVis * 0.2 * dim))
        eg.addColorStop(1, 'rgba(0,0,0,0)')
        ctx!.fillStyle = eg
        ctx!.beginPath()
        ctx!.arc(tx, ty, tr * 1.5, 0, Math.PI * 2)
        ctx!.fill()
      }

      rafRef.current = requestAnimationFrame(frame)
    }

    rafRef.current = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(rafRef.current)
  }, []) // Stable — reads mutable refs

  return (
    <canvas
      ref={canvasRef}
      className={className}
      style={{ width: size, height: size }}
    />
  )
}
