/**
 * SunOrbitVisualizer — Contained canvas-based liquid sun visualization.
 *
 * Renders a fixed-size canvas widget with:
 *   - Liquid sun sphere with organic color blobs
 *   - Season cycling through 4 palettes (Winter, Spring, Summer, Autumn)
 *   - Atmospheric glow that responds to intensity
 *   - Central equalizer bars (audio-reactive when frequencyData provided)
 *
 * Props:
 *   size           — pixel size of the canvas (default 140)
 *   intensity      — 0 to 1, controls sun size and glow brightness
 *   isActive       — whether audio is active (drives equalizer bars)
 *   frequencyData  — optional Uint8Array from Web Audio API for equalizer
 *   className      — optional className for the canvas element
 */

import { useEffect, useRef, useMemo } from 'react';

export interface SunOrbitVisualizerProps {
  size?: number;
  intensity: number;
  isActive: boolean;
  frequencyData?: Uint8Array;
  className?: string;
}

interface ColorBlob {
  x: number;
  y: number;
  vx: number;
  vy: number;
  radiusFactor: number;
  phase: number;
}

interface Palette {
  base: string;
  glow: string;
  blobs: string[];
  accent: string;
}

const PALETTES: Palette[] = [
  {
    // Winter / Deep Blue
    base: '#001a2c',
    glow: 'rgba(0, 180, 255, 0.5)',
    blobs: ['#00ffff', '#00d4ff', '#ffffff', '#0066ff'],
    accent: '#00ffff',
  },
  {
    // Spring / Pink-Cyan
    base: '#1a002c',
    glow: 'rgba(255, 0, 255, 0.4)',
    blobs: ['#ff00ff', '#00e5ff', '#ffffff', '#ff00aa'],
    accent: '#ff00ff',
  },
  {
    // Summer / Golden
    base: '#2c1a00',
    glow: 'rgba(255, 180, 0, 0.5)',
    blobs: ['#ffaa00', '#ff4400', '#ffffff', '#ffd700'],
    accent: '#ffaa00',
  },
  {
    // Autumn / Magenta-Red
    base: '#2c000a',
    glow: 'rgba(255, 0, 100, 0.5)',
    blobs: ['#ff0066', '#9900ff', '#ffffff', '#ff4400'],
    accent: '#ff0066',
  },
];

export function SunOrbitVisualizer({
  size = 140,
  intensity,
  isActive,
  frequencyData,
  className,
}: SunOrbitVisualizerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animationRef = useRef<number | undefined>(undefined);
  const sizeRef = useRef(size);
  const intensityRef = useRef(intensity);
  const isActiveRef = useRef(isActive);
  const frequencyDataRef = useRef(frequencyData);

  useEffect(() => { sizeRef.current = size }, [size]);
  useEffect(() => { intensityRef.current = intensity }, [intensity]);
  useEffect(() => { isActiveRef.current = isActive }, [isActive]);
  useEffect(() => { frequencyDataRef.current = frequencyData }, [frequencyData]);

  const blobs = useMemo<ColorBlob[]>(() => {
    return Array.from({ length: 8 }).map(() => ({
      x: Math.random(),
      y: Math.random(),
      vx: (Math.random() - 0.5) * 0.005,
      vy: (Math.random() - 0.5) * 0.005,
      radiusFactor: 0.3 + Math.random() * 0.45,
      phase: Math.random() * Math.PI * 2,
    }));
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d', { alpha: true });
    if (!ctx) return;

    let time = 0;

    const render = () => {
      time += 0.015;
      const s = sizeRef.current;
      const currentIntensity = intensityRef.current;
      const active = isActiveRef.current;
      const freqData = frequencyDataRef.current;

      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== s * dpr || canvas.height !== s * dpr) {
        canvas.width = s * dpr;
        canvas.height = s * dpr;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const centerX = s / 2;
      const centerY = s / 2;

      // Season cycle
      const seasonCycle = (time * 0.2) % PALETTES.length;
      const paletteIndex = Math.floor(seasonCycle);
      const currentPalette = PALETTES[paletteIndex];

      ctx.clearRect(0, 0, s, s);

      const sunRadius = s * 0.48 * (1 + currentIntensity * 0.05);

      // Liquid Sun Sphere (fills the circular canvas)
      ctx.save();
      ctx.beginPath();
      ctx.arc(centerX, centerY, sunRadius, 0, Math.PI * 2);
      ctx.clip();

      // Base liquid fill
      ctx.fillStyle = currentPalette.base;
      ctx.fillRect(
        centerX - sunRadius, centerY - sunRadius,
        sunRadius * 2, sunRadius * 2,
      );

      // Blobs with organic blending
      blobs.forEach((blob, i) => {
        blob.x += Math.cos(time * 0.3 + blob.phase) * blob.vx;
        blob.y += Math.sin(time * 0.2 + blob.phase) * blob.vy;

        if (blob.x < 0 || blob.x > 1) blob.vx *= -1;
        if (blob.y < 0 || blob.y > 1) blob.vy *= -1;

        const bx = centerX + (blob.x - 0.5) * sunRadius * 2.4;
        const by = centerY + (blob.y - 0.5) * sunRadius * 2.4;
        const blobRadius = sunRadius * blob.radiusFactor;

        const blobColor = currentPalette.blobs[i % currentPalette.blobs.length];
        const grad = ctx.createRadialGradient(bx, by, 0, bx, by, blobRadius);
        grad.addColorStop(0, blobColor);
        grad.addColorStop(0.4, blobColor + '88');
        grad.addColorStop(1, 'rgba(0,0,0,0)');

        ctx.globalCompositeOperation = 'screen';
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(bx, by, blobRadius, 0, Math.PI * 2);
        ctx.fill();
      });

      ctx.restore();

      // 3. Central Equalizer
      const barCount = 5;
      const barWidth = 2;
      const barGap = Math.max(4, s * 0.06);
      const totalWidth = barCount * barWidth + (barCount - 1) * barGap;
      const startX = centerX - totalWidth / 2;

      ctx.strokeStyle = currentPalette.accent;
      ctx.lineWidth = barWidth;
      ctx.lineCap = 'round';
      ctx.shadowBlur = 10;
      ctx.shadowColor = currentPalette.accent;

      for (let i = 0; i < barCount; i++) {
        let magnitude = 0;
        if (active && freqData) {
          const sampleIdx = Math.floor((i / barCount) * (freqData.length / 4));
          magnitude = freqData[sampleIdx] / 255;
        } else {
          magnitude = 0.2 + Math.sin(time * 4 + i * 1.2) * 0.1;
        }

        const barHeight = s * 0.1 + magnitude * s * 0.25;
        const x = startX + i * (barWidth + barGap);

        ctx.beginPath();
        ctx.moveTo(x, centerY - barHeight / 2);
        ctx.lineTo(x, centerY + barHeight / 2);
        ctx.stroke();
      }
      ctx.shadowBlur = 0;

      animationRef.current = requestAnimationFrame(render);
    };

    animationRef.current = requestAnimationFrame(render);

    return () => {
      if (animationRef.current) cancelAnimationFrame(animationRef.current);
    };
  }, [blobs]);

  return (
    <canvas
      ref={canvasRef}
      className={className}
      style={{ width: size, height: size }}
    />
  );
}

export default SunOrbitVisualizer;
