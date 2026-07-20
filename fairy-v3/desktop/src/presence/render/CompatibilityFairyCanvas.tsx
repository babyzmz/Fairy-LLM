import { useEffect, useRef } from "react";

import type { PresenceWorkState } from "../domain/projection";

export type FairyVisualState =
  | "booting"
  | "idle"
  | "hover"
  | "listening"
  | "analyzing"
  | "tool"
  | "streaming"
  | "speaking"
  | "awaiting_confirmation"
  | "ready"
  | "error"
  | "sleeping"
  | "dragging";

interface FairyCanvasProps {
  workState: PresenceWorkState;
  hovered: boolean;
  listening: boolean;
  speaking: boolean;
  sleeping: boolean;
  dragging: boolean;
  reducedMotion: boolean;
  gaze: { x: number; y: number };
}

export interface StateStyle {
  accent: string;
  secondary: string;
  speed: number;
  energy: number;
  particles: number;
}

const STYLES: Record<FairyVisualState, StateStyle> = {
  booting: style("#7dc8ff", "#f4fbff", 1.5, 0.9, 18),
  idle: style("#1f74d6", "#e8f6ff", 0.35, 0.35, 10),
  hover: style("#3492f2", "#ffffff", 0.55, 0.55, 10),
  listening: style("#62d7c5", "#eefefa", 0.8, 0.8, 12),
  analyzing: style("#65b9ff", "#f4fbff", 1.15, 0.85, 15),
  tool: style("#f2b84b", "#fff4cf", 1.3, 0.95, 14),
  streaming: style("#65d7c5", "#effffb", 1.45, 0.9, 16),
  speaking: style("#86e5cb", "#ffffff", 1.1, 1, 18),
  awaiting_confirmation: style("#f0b44d", "#fff1c2", 0.8, 0.82, 11),
  ready: style("#63d1ad", "#edfff8", 0.5, 0.62, 9),
  error: style("#f47d72", "#fff0ee", 0.9, 0.95, 8),
  sleeping: style("#70869b", "#dce7ee", 0.12, 0.16, 8),
  dragging: style("#8abdf0", "#ffffff", 1.05, 0.72, 10),
};

export const COMPATIBILITY_GLASS_STYLE = Object.freeze({
  center_alpha: 0.075,
  rim_alpha: 0.27,
  warm_rim: "#ffd7b0",
  cool_rim: "#86d8ff",
});

function style(
  accent: string,
  secondary: string,
  speed: number,
  energy: number,
  particles: number,
): StateStyle {
  return { accent, secondary, speed, energy, particles };
}

export function CompatibilityFairyCanvas(props: FairyCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const propsRef = useRef(props);
  propsRef.current = props;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    const context = canvas.getContext("2d", { alpha: true });
    if (context === null) return;
    let frame = 0;
    let started = performance.now();
    let lastDraw = 0;
    let disposed = false;
    let hasRendered = false;

    const render = (now: number) => {
      if (disposed) return;
      const current = propsRef.current;
      const state = visualState(current, now - started);
      const idle = state === "idle" || state === "sleeping";
      const interval = current.reducedMotion ? Number.POSITIVE_INFINITY : 1_000 / (idle ? 15 : 60);
      if (!document.hidden && (now - lastDraw >= interval || lastDraw === 0)) {
        resizeCanvas(canvas);
        drawFairyFrame(context, canvas, state, current.gaze, current.reducedMotion ? 0 : now / 1_000);
        if (!hasRendered) {
          hasRendered = true;
          canvas.dataset.rendered = "true";
        }
        lastDraw = now;
      }
      if (!current.reducedMotion) frame = requestAnimationFrame(render);
    };

    const onVisibility = () => {
      if (!document.hidden && !propsRef.current.reducedMotion) {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(render);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    frame = requestAnimationFrame(render);
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [props.reducedMotion]);

  return <canvas aria-label="Fairy" className="fairy-canvas" ref={canvasRef} role="img" />;
}

export { CompatibilityFairyCanvas as FairyCanvas };

export function visualState(
  props: Omit<FairyCanvasProps, "reducedMotion" | "gaze">,
  elapsedMs: number,
): FairyVisualState {
  if (elapsedMs < 1_600) return "booting";
  if (props.dragging) return "dragging";
  if (props.speaking) return "speaking";
  if (props.listening) return "listening";
  if (props.hovered && props.workState === "idle") return "hover";
  if (props.sleeping && props.workState === "idle") return "sleeping";
  return props.workState;
}

export function drawFairyFrame(
  context: CanvasRenderingContext2D,
  canvas: HTMLCanvasElement,
  state: FairyVisualState,
  gaze: { x: number; y: number },
  time: number,
  options: {
    opacity?: number;
    reducedTransparency?: boolean;
    increasedContrast?: boolean;
    particles?: boolean;
    sizeScale?: number;
    center?: { x: number; y: number };
    capsuleDirection?: -1 | 1 | null;
    capsuleGeometry?: { center: { x: number; y: number }; width: number } | null;
    stateStyle?: StateStyle;
  } = {},
): void {
  const width = canvas.width;
  const height = canvas.height;
  const scale = width / Math.max(1, canvas.clientWidth);
  const requestedCenter = options.center ?? { x: width / 2, y: height / 2 };
  const centerX = requestedCenter.x + gaze.x * 4 * scale;
  const centerY = requestedCenter.y + gaze.y * 3 * scale
    + Math.sin(time * 1.2) * 1.2 * scale;
  const radius = Math.min(Math.min(width, height) * 0.32, 72 * scale)
    * (options.sizeScale ?? 1);
  const stateStyle = options.stateStyle ?? STYLES[state];
  const phase = time * stateStyle.speed;
  context.clearRect(0, 0, width, height);
  context.save();
  context.globalAlpha = options.opacity ?? 1;

  if (options.capsuleGeometry !== null && options.capsuleGeometry !== undefined) {
    drawCompatibilityCapsuleGeometry(
      context,
      options.capsuleGeometry,
      scale,
      options.reducedTransparency === true,
      options.increasedContrast === true,
    );
  } else if (options.capsuleDirection !== null && options.capsuleDirection !== undefined) {
    drawCompatibilityCapsule(
      context,
      requestedCenter,
      options.capsuleDirection,
      scale,
    );
  }

  if (options.reducedTransparency === true) {
    context.fillStyle = "rgba(18, 23, 24, 0.22)";
    context.beginPath();
    context.arc(centerX, centerY, radius, 0, Math.PI * 2);
    context.fill();
  }
  if (options.increasedContrast === true) {
    context.strokeStyle = "rgba(255, 255, 255, 0.86)";
    context.lineWidth = Math.max(1, scale);
    context.beginPath();
    context.arc(centerX, centerY, radius, 0, Math.PI * 2);
    context.stroke();
  }

  const aura = context.createRadialGradient(
    centerX,
    centerY,
    radius * 0.15,
    centerX,
    centerY,
    radius * 1.35,
  );
  aura.addColorStop(0, colorWithAlpha(stateStyle.accent, 0.045 + stateStyle.energy * 0.02));
  aura.addColorStop(0.55, colorWithAlpha(stateStyle.accent, 0.018));
  aura.addColorStop(1, colorWithAlpha(stateStyle.accent, 0));
  context.fillStyle = aura;
  context.beginPath();
  context.arc(centerX, centerY, radius * 1.35, 0, Math.PI * 2);
  context.fill();

  if (options.particles ?? true) {
    drawParticles(context, centerX, centerY, radius, phase, stateStyle);
  }
  drawShell(context, centerX, centerY, radius, phase, stateStyle);
  drawRings(context, centerX, centerY, radius, phase, stateStyle);
  drawCore(context, centerX, centerY, radius, gaze, phase, stateStyle);
  drawOrbitLight(context, centerX, centerY, radius, phase, stateStyle);
  drawStateMark(context, centerX, centerY, radius, phase, state, stateStyle);
  context.restore();
}

function drawCompatibilityCapsuleGeometry(
  context: CanvasRenderingContext2D,
  geometry: { center: { x: number; y: number }; width: number },
  scale: number,
  reducedTransparency = false,
  increasedContrast = false,
) {
  drawCompatibilityCapsulePath(context, {
    x: geometry.center.x - geometry.width / 2,
    y: geometry.center.y - 26 * scale,
    width: geometry.width,
    height: 52 * scale,
    radius: 26 * scale,
  }, reducedTransparency, increasedContrast);
}

export function compatibilityCapsuleGeometry(
  center: { x: number; y: number },
  direction: -1 | 1,
  scale: number,
) {
  const width = 400 * scale;
  const height = 52 * scale;
  const centerX = center.x + direction * 280 * scale;
  return {
    x: centerX - width / 2,
    y: center.y - height / 2,
    width,
    height,
    radius: height / 2,
  };
}

function drawCompatibilityCapsule(
  context: CanvasRenderingContext2D,
  center: { x: number; y: number },
  direction: -1 | 1,
  scale: number,
) {
  const geometry = compatibilityCapsuleGeometry(center, direction, scale);
  drawCompatibilityCapsulePath(context, geometry);
}

function drawCompatibilityCapsulePath(
  context: CanvasRenderingContext2D,
  geometry: { x: number; y: number; width: number; height: number; radius: number },
  reducedTransparency = false,
  increasedContrast = false,
) {
  const { x, y, width, height, radius } = geometry;
  context.save();
  const fill = context.createLinearGradient(x, y, x, y + height);
  fill.addColorStop(0, reducedTransparency
    ? "rgba(255, 255, 255, 0.24)"
    : "rgba(255, 255, 255, 0.15)");
  fill.addColorStop(0.44, reducedTransparency
    ? "rgba(24, 31, 33, 0.22)"
    : "rgba(210, 229, 235, 0.055)");
  fill.addColorStop(1, reducedTransparency
    ? "rgba(18, 23, 24, 0.32)"
    : "rgba(100, 124, 132, 0.11)");
  context.fillStyle = fill;
  context.strokeStyle = increasedContrast
    ? "rgba(255, 255, 255, 0.86)"
    : "rgba(238, 249, 252, 0.28)";
  context.lineWidth = Math.max(1, height / 52);
  context.beginPath();
  context.moveTo(x + radius, y);
  context.lineTo(x + width - radius, y);
  context.arc(x + width - radius, y + radius, radius, -Math.PI / 2, Math.PI / 2);
  context.lineTo(x + radius, y + height);
  context.arc(x + radius, y + radius, radius, Math.PI / 2, Math.PI * 1.5);
  context.closePath();
  context.fill();
  context.stroke();
  context.restore();
}

function drawShell(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  radius: number,
  phase: number,
  stateStyle: StateStyle,
) {
  const shell = context.createRadialGradient(
    x - radius * 0.28,
    y - radius * 0.34,
    radius * 0.04,
    x,
    y,
    radius * 1.03,
  );
  shell.addColorStop(0, "rgba(255, 255, 255, 0.16)");
  shell.addColorStop(
    0.42,
    `rgba(220, 231, 237, ${COMPATIBILITY_GLASS_STYLE.center_alpha})`,
  );
  shell.addColorStop(0.76, "rgba(116, 132, 142, 0.065)");
  shell.addColorStop(1, "rgba(247, 251, 253, 0.18)");
  context.fillStyle = shell;
  context.strokeStyle = `rgba(248, 252, 255, ${COMPATIBILITY_GLASS_STYLE.rim_alpha})`;
  context.lineWidth = radius * 0.025;
  context.beginPath();
  for (let index = 0; index <= 72; index += 1) {
    const angle = (index / 72) * Math.PI * 2;
    const wobble = 1 + Math.sin(angle * 8 + phase) * 0.012 * stateStyle.energy;
    const px = x + Math.cos(angle) * radius * wobble;
    const py = y + Math.sin(angle) * radius * wobble;
    if (index === 0) context.moveTo(px, py);
    else context.lineTo(px, py);
  }
  context.closePath();
  context.fill();
  context.stroke();

  context.save();
  context.translate(x, y);
  context.rotate(-0.12 + Math.sin(phase * 0.08) * 0.025);
  context.globalCompositeOperation = "screen";
  context.lineCap = "round";
  context.strokeStyle = colorWithAlpha(COMPATIBILITY_GLASS_STYLE.warm_rim, 0.24);
  context.lineWidth = radius * 0.038;
  context.beginPath();
  context.arc(0, 0, radius * 0.985, Math.PI * 1.08, Math.PI * 1.78);
  context.stroke();
  context.strokeStyle = colorWithAlpha(COMPATIBILITY_GLASS_STYLE.cool_rim, 0.25);
  context.beginPath();
  context.arc(0, 0, radius * 0.985, -0.08, Math.PI * 0.66);
  context.stroke();
  context.strokeStyle = "rgba(255, 252, 242, 0.2)";
  context.lineWidth = radius * 0.045;
  context.beginPath();
  context.arc(0, 0, radius * 0.83, Math.PI * 1.12, Math.PI * 1.56);
  context.stroke();
  context.restore();
}

function drawRings(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  radius: number,
  phase: number,
  stateStyle: StateStyle,
) {
  const breath = 0.5 + 0.5 * Math.sin(phase * 1.18);
  context.save();
  context.translate(x, y);
  context.globalCompositeOperation = "screen";
  context.strokeStyle = colorWithAlpha(
    stateStyle.secondary,
    0.12 + stateStyle.energy * 0.08 + breath * 0.035,
  );
  context.lineWidth = Math.max(0.8, radius * 0.014);
  context.beginPath();
  context.arc(0, 0, radius * (0.5 + breath * 0.006), 0, Math.PI * 2);
  context.stroke();
  context.strokeStyle = colorWithAlpha(
    stateStyle.accent,
    0.09 + stateStyle.energy * 0.07 + (1 - breath) * 0.025,
  );
  context.lineWidth = Math.max(0.7, radius * 0.011);
  context.beginPath();
  context.arc(0, 0, radius * (0.34 - breath * 0.004), 0, Math.PI * 2);
  context.stroke();
  context.restore();
}

function drawCore(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  radius: number,
  gaze: { x: number; y: number },
  phase: number,
  stateStyle: StateStyle,
) {
  const coreX = x + gaze.x * radius * 0.06;
  const coreY = y + gaze.y * radius * 0.05;
  const coreRadius = radius * (0.18 + Math.sin(phase * 1.7) * 0.008 * stateStyle.energy);
  const core = context.createRadialGradient(coreX - coreRadius * 0.25, coreY - coreRadius * 0.3, 0, coreX, coreY, coreRadius);
  core.addColorStop(0, stateStyle.secondary);
  core.addColorStop(0.32, stateStyle.accent);
  core.addColorStop(1, "#16468e");
  context.fillStyle = core;
  context.beginPath();
  context.arc(coreX, coreY, coreRadius, 0, Math.PI * 2);
  context.fill();
}

function drawOrbitLight(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  radius: number,
  phase: number,
  stateStyle: StateStyle,
) {
  const angle = -0.72 + Math.sin(phase * 0.65) * 0.16;
  const dotX = x + Math.cos(angle) * radius * 0.58;
  const dotY = y + Math.sin(angle) * radius * 0.58;
  context.fillStyle = stateStyle.secondary;
  context.shadowColor = stateStyle.accent;
  context.shadowBlur = radius * 0.16;
  context.beginPath();
  context.arc(dotX, dotY, radius * 0.065, 0, Math.PI * 2);
  context.fill();
  context.shadowBlur = 0;
}

function drawParticles(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  radius: number,
  phase: number,
  stateStyle: StateStyle,
) {
  context.fillStyle = colorWithAlpha(stateStyle.accent, 0.48);
  for (let index = 0; index < stateStyle.particles; index += 1) {
    const seed = index * 2.399;
    const orbit = radius * (0.78 + ((index * 37) % 20) / 30);
    const angle = seed + phase * (0.08 + (index % 3) * 0.025);
    const px = x + Math.cos(angle) * orbit;
    const py = y + Math.sin(angle) * orbit * 0.82;
    context.beginPath();
    context.arc(px, py, radius * (0.008 + (index % 3) * 0.004), 0, Math.PI * 2);
    context.fill();
  }
}

function drawStateMark(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  radius: number,
  phase: number,
  state: FairyVisualState,
  stateStyle: StateStyle,
) {
  if (!["tool", "awaiting_confirmation", "error", "speaking"].includes(state)) return;
  context.strokeStyle = colorWithAlpha(stateStyle.accent, 0.82);
  context.lineWidth = radius * 0.025;
  context.setLineDash(state === "awaiting_confirmation" ? [radius * 0.1, radius * 0.07] : []);
  context.beginPath();
  context.arc(x, y, radius * 1.12, phase, phase + Math.PI * (state === "error" ? 0.85 : 1.45));
  context.stroke();
  context.setLineDash([]);
}

function resizeCanvas(canvas: HTMLCanvasElement) {
  const ratio = Math.min(2, window.devicePixelRatio || 1);
  const width = Math.max(1, Math.round(canvas.clientWidth * ratio));
  const height = Math.max(1, Math.round(canvas.clientHeight * ratio));
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
}

function colorWithAlpha(color: string, alpha: number): string {
  const value = color.replace("#", "");
  const red = Number.parseInt(value.slice(0, 2), 16);
  const green = Number.parseInt(value.slice(2, 4), 16);
  const blue = Number.parseInt(value.slice(4, 6), 16);
  return `rgba(${red}, ${green}, ${blue}, ${Math.max(0, Math.min(1, alpha))})`;
}
