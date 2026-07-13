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

interface StateStyle {
  accent: string;
  secondary: string;
  speed: number;
  energy: number;
  particles: number;
}

const STYLES: Record<FairyVisualState, StateStyle> = {
  booting: style("#7dc8ff", "#f4fbff", 1.5, 0.9, 18),
  idle: style("#58a9ef", "#e8f6ff", 0.35, 0.35, 10),
  hover: style("#74c7ff", "#ffffff", 0.55, 0.55, 10),
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

    const render = (now: number) => {
      if (disposed) return;
      const current = propsRef.current;
      const state = visualState(current, now - started);
      const idle = state === "idle" || state === "sleeping";
      const interval = current.reducedMotion ? Number.POSITIVE_INFINITY : 1_000 / (idle ? 15 : 60);
      if (!document.hidden && (now - lastDraw >= interval || lastDraw === 0)) {
        resizeCanvas(canvas);
        drawFairyFrame(context, canvas, state, current.gaze, current.reducedMotion ? 0 : now / 1_000);
        canvas.dataset.rendered = "true";
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
    particles?: boolean;
    sizeScale?: number;
  } = {},
): void {
  const width = canvas.width;
  const height = canvas.height;
  const scale = width / Math.max(1, canvas.clientWidth);
  const centerX = width / 2 + gaze.x * 4 * scale;
  const centerY = height / 2 + gaze.y * 3 * scale + Math.sin(time * 1.2) * 1.2 * scale;
  const radius = Math.min(width, height) * 0.32 * (options.sizeScale ?? 1);
  const stateStyle = STYLES[state];
  const phase = time * stateStyle.speed;
  context.clearRect(0, 0, width, height);
  context.save();
  context.globalAlpha = options.opacity ?? 1;

  const aura = context.createRadialGradient(centerX, centerY, radius * 0.15, centerX, centerY, radius * 1.5);
  aura.addColorStop(0, colorWithAlpha(stateStyle.accent, 0.18 + stateStyle.energy * 0.08));
  aura.addColorStop(0.55, colorWithAlpha(stateStyle.accent, 0.09));
  aura.addColorStop(1, colorWithAlpha(stateStyle.accent, 0));
  context.fillStyle = aura;
  context.beginPath();
  context.arc(centerX, centerY, radius * 1.5, 0, Math.PI * 2);
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

function drawShell(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  radius: number,
  phase: number,
  stateStyle: StateStyle,
) {
  const shell = context.createRadialGradient(x, y, radius * 0.1, x, y, radius);
  shell.addColorStop(0, "#163d7d");
  shell.addColorStop(0.58, "#102f68");
  shell.addColorStop(1, "#091b43");
  context.fillStyle = shell;
  context.strokeStyle = colorWithAlpha(stateStyle.accent, 0.42);
  context.lineWidth = radius * 0.018;
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
}

function drawRings(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  radius: number,
  phase: number,
  stateStyle: StateStyle,
) {
  context.save();
  context.translate(x, y);
  context.rotate(phase * 0.16);
  context.strokeStyle = colorWithAlpha(stateStyle.secondary, 0.92);
  context.lineWidth = radius * 0.11;
  context.lineCap = "round";
  context.beginPath();
  context.arc(0, 0, radius * 0.52, -0.25, Math.PI * 1.55);
  context.stroke();
  context.strokeStyle = colorWithAlpha(stateStyle.accent, 0.64);
  context.lineWidth = radius * 0.035;
  context.beginPath();
  context.arc(0, 0, radius * 0.34, Math.PI * 0.15, Math.PI * 1.85);
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
