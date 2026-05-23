import { type CSSProperties, useId, useMemo } from "react";

export type FairyWorkState = "standby" | "relaxed" | "thinking" | "focused" | "uncertain" | "alert";

export type FairyAvatarMode =
  | FairyWorkState
  | "idle"
  | "booting"
  | "warming_up"
  | "analyzing"
  | "replying"
  | "error"
  | "sleeping";

export interface FairyAvatarSignal {
  state: FairyWorkState;
  certainty?: number;
  urgency?: number;
}

interface FairyAvatarProps {
  size?: number;
  animated?: boolean;
  className?: string;
  mode?: FairyAvatarMode;
  signal?: FairyAvatarSignal;
  glow?: boolean;
}

interface FairyPhysicalParams {
  baseFreq: number;
  rippleSpeed: number;
  amplitude: number;
  jitter: number;
  glow: number;
}

export interface FairyResolvedDynamics extends FairyPhysicalParams {
  state: FairyWorkState;
  certainty: number;
  urgency: number;
  freq: number;
}

const FAIRY_STATE_PARAMS: Record<FairyWorkState, FairyPhysicalParams> = {
  standby: { baseFreq: 0.18, rippleSpeed: 0.15, amplitude: 0.18, jitter: 0.02, glow: 0.25 },
  relaxed: { baseFreq: 0.28, rippleSpeed: 0.25, amplitude: 0.25, jitter: 0.03, glow: 0.35 },
  thinking: { baseFreq: 0.55, rippleSpeed: 0.6, amplitude: 0.22, jitter: 0.08, glow: 0.4 },
  focused: { baseFreq: 0.35, rippleSpeed: 0.45, amplitude: 0.32, jitter: 0.03, glow: 0.55 },
  uncertain: { baseFreq: 0.25, rippleSpeed: 0.3, amplitude: 0.2, jitter: 0.06, glow: 0.3 },
  alert: { baseFreq: 0.75, rippleSpeed: 0.8, amplitude: 0.28, jitter: 0.1, glow: 0.65 },
};

const DEFAULT_SIGNAL_BY_STATE: Record<FairyWorkState, Required<FairyAvatarSignal>> = {
  standby: { state: "standby", certainty: 0.82, urgency: 0.08 },
  relaxed: { state: "relaxed", certainty: 0.78, urgency: 0.18 },
  thinking: { state: "thinking", certainty: 0.58, urgency: 0.58 },
  focused: { state: "focused", certainty: 0.88, urgency: 0.32 },
  uncertain: { state: "uncertain", certainty: 0.34, urgency: 0.34 },
  alert: { state: "alert", certainty: 0.7, urgency: 0.88 },
};

const COLORS = {
  deepNavy: "#0F2A5A",
  navy: "#132E60",
  blue: "#1F4FA3",
  cyan: "#4F86FF",
  softBlue: "#8FB5FF",
  whiteRing: "#ECF0F6",
  dotWhite: "#F4F7FB",
  particle: "#C2E8FF",
};

function clamp01(value: number | undefined, fallback: number): number {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return fallback;
  }
  return Math.max(0, Math.min(1, value));
}

function lerp(start: number, end: number, ratio: number): number {
  return start + (end - start) * ratio;
}

export function resolveFairyAvatarSignal(mode: FairyAvatarMode): Required<FairyAvatarSignal> {
  switch (mode) {
    case "standby":
    case "relaxed":
    case "thinking":
    case "focused":
    case "uncertain":
    case "alert":
      return DEFAULT_SIGNAL_BY_STATE[mode];
    case "booting":
      return { state: "standby", certainty: 0.45, urgency: 0.36 };
    case "warming_up":
      return { state: "relaxed", certainty: 0.58, urgency: 0.28 };
    case "analyzing":
      return { state: "focused", certainty: 0.76, urgency: 0.52 };
    case "replying":
      return { state: "focused", certainty: 0.9, urgency: 0.28 };
    case "error":
      return { state: "alert", certainty: 0.62, urgency: 0.94 };
    case "sleeping":
      return { state: "standby", certainty: 0.84, urgency: 0.03 };
    case "idle":
    default:
      return DEFAULT_SIGNAL_BY_STATE.standby;
  }
}

export function resolveFairyAvatarDynamics(signal: FairyAvatarSignal): FairyResolvedDynamics {
  const fallback = DEFAULT_SIGNAL_BY_STATE[signal.state];
  const certainty = clamp01(signal.certainty, fallback.certainty);
  const urgency = clamp01(signal.urgency, fallback.urgency);
  const base = FAIRY_STATE_PARAMS[signal.state];
  const amplitude = base.amplitude * lerp(0.85, 1.25, certainty);
  const freq = base.baseFreq * lerp(0.8, 1.8, urgency);
  const jitter = base.jitter * lerp(1.2, 0.6, certainty);
  return {
    ...base,
    state: signal.state,
    certainty,
    urgency,
    amplitude,
    freq,
    jitter,
  };
}

function polar(cx: number, cy: number, radius: number, angle: number) {
  return {
    x: cx + Math.cos(angle) * radius,
    y: cy + Math.sin(angle) * radius,
  };
}

function buildShellPath(size: number): string {
  const cx = size / 2;
  const cy = size / 2;
  const radius = size / 2 / 1.04;
  const baseRadius = radius * 0.7;
  const numPoints = 64;
  const spin = 0;
  const edgeWobble = 0.011;
  const points = Array.from({ length: numPoints }, (_, index) => {
    const theta = (index / numPoints) * Math.PI * 2;
    const wave = 0.68 * Math.cos(theta * 8 + spin * 0.55) + 0.18 * Math.sin(theta * 16 - spin * 0.42);
    const r = baseRadius * (1 + edgeWobble * wave);
    return polar(cx, cy, r, theta);
  });
  return points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(3)} ${point.y.toFixed(3)}`)
    .join(" ")
    .concat(" Z");
}

function buildParticles(size: number) {
  const cx = size / 2;
  const cy = size / 2;
  const radius = size / 2 / 1.04;
  const count = 9;
  return Array.from({ length: count }, (_, index) => {
    const angle = index * ((Math.PI * 2) / count);
    const orbit = radius * (0.84 + 0.05 * Math.sin(index * 0.9));
    return {
      ...polar(cx, cy, orbit, angle),
      r: 1.3 + (index % 3) * 0.6,
      delay: `${index * 0.28}s`,
    };
  });
}

export function FairyAvatar({
  size = 180,
  animated = true,
  className = "",
  mode = "idle",
  signal,
  glow = true,
}: FairyAvatarProps): JSX.Element {
  const gradientId = useId().replace(/:/g, "");
  const shellPath = useMemo(() => buildShellPath(size), [size]);
  const particles = useMemo(() => buildParticles(size), [size]);
  const effectiveSignal = useMemo(
    () => signal ?? resolveFairyAvatarSignal(mode),
    [mode, signal?.certainty, signal?.state, signal?.urgency],
  );
  const dynamics = useMemo(() => resolveFairyAvatarDynamics(effectiveSignal), [effectiveSignal]);
  const cx = size / 2;
  const cy = size / 2;
  const outerRingRadius = size * 0.1635;
  const innerRingRadius = size * 0.084;
  const orbitRadius = size * 0.118;
  const orbitDotRadius = size * 0.0435;
  const coreRadius = size * 0.0695;
  const breathDuration = 1 / Math.max(dynamics.freq, 0.08);
  const avatarStyle = {
    width: size,
    height: size,
    "--fairy-ring-duration": `${breathDuration.toFixed(2)}s`,
    "--fairy-inner-duration": `${(breathDuration * 0.86).toFixed(2)}s`,
    "--fairy-core-duration": `${(breathDuration * 0.78).toFixed(2)}s`,
    "--fairy-orbit-duration": `${Math.max(8, 24 - dynamics.rippleSpeed * 14).toFixed(2)}s`,
    "--fairy-shell-duration": `${Math.max(7, 20 - dynamics.rippleSpeed * 11).toFixed(2)}s`,
    "--fairy-particle-duration": `${Math.max(1.6, 5.4 - dynamics.rippleSpeed * 3.2).toFixed(2)}s`,
    "--fairy-ring-scale": (1 + dynamics.amplitude * 0.08).toFixed(4),
    "--fairy-inner-scale": (1 + dynamics.amplitude * 0.05).toFixed(4),
    "--fairy-core-scale": (1 + dynamics.amplitude * 0.16).toFixed(4),
    "--fairy-glow-opacity": Math.min(1, 0.54 + dynamics.glow * 0.74).toFixed(3),
    "--fairy-jitter-distance": `${(dynamics.jitter * size * 0.16).toFixed(2)}px`,
    "--fairy-particle-opacity": (0.26 + dynamics.glow * 0.42).toFixed(3),
  } as CSSProperties;

  return (
    <div
      className={[
        "fairy-avatar",
        animated ? "fairy-avatar--animated" : "fairy-avatar--static",
        glow ? "fairy-avatar--glow" : "",
        `fairy-avatar--${mode}`,
        mode !== dynamics.state ? `fairy-avatar--${dynamics.state}` : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      style={avatarStyle}
    >
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} aria-hidden="true">
        <defs>
          <radialGradient id={`${gradientId}-base`} cx="50%" cy="50%" r="58%">
            <stop offset="0%" stopColor="#204A8E" />
            <stop offset="62%" stopColor="#183D7A" />
            <stop offset="100%" stopColor={COLORS.deepNavy} />
          </radialGradient>
          <radialGradient id={`${gradientId}-haze`} cx="50%" cy="50%" r="55%">
            <stop offset="0%" stopColor="rgba(143,181,255,0.14)" />
            <stop offset="72%" stopColor="rgba(110,151,230,0.04)" />
            <stop offset="100%" stopColor="rgba(45,92,168,0)" />
          </radialGradient>
          <radialGradient id={`${gradientId}-shell`} cx="50%" cy="50%" r="55%">
            <stop offset="0%" stopColor="#0E245C" />
            <stop offset="82%" stopColor="#0A1B46" />
            <stop offset="100%" stopColor="#081536" />
          </radialGradient>
          <radialGradient id={`${gradientId}-core`} cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#245FC2" />
            <stop offset="32%" stopColor="#134495" />
            <stop offset="100%" stopColor="#0A235A" />
          </radialGradient>
          <radialGradient id={`${gradientId}-dotGlow`} cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="rgba(232,238,246,0.48)" />
            <stop offset="100%" stopColor="rgba(136,178,214,0)" />
          </radialGradient>
          <radialGradient id={`${gradientId}-outerGlow`} cx="50%" cy="50%" r="65%">
            <stop offset="0%" stopColor="rgba(154,196,255,0.05)" />
            <stop offset="46%" stopColor="rgba(96,150,226,0.22)" />
            <stop offset="100%" stopColor="rgba(56,108,182,0)" />
          </radialGradient>
        </defs>

        <circle className="fairy-avatar__outer-glow" cx={cx} cy={cy} r={size * 0.505} fill={`url(#${gradientId}-outerGlow)`} />
        <circle className="fairy-avatar__base" cx={cx} cy={cy} r={size * 0.466} fill={`url(#${gradientId}-base)`} />
        <circle className="fairy-avatar__base-outline" cx={cx} cy={cy} r={size * 0.471} fill="none" stroke="rgba(227,235,246,0.55)" strokeWidth={size * 0.0105} />
        <circle className="fairy-avatar__haze" cx={cx} cy={cy} r={size * 0.423} fill={`url(#${gradientId}-haze)`} />
        <path className="fairy-avatar__shell" d={shellPath} fill={`url(#${gradientId}-shell)`} stroke="rgba(82,122,188,0.28)" strokeWidth={size * 0.0055} />
        <circle className="fairy-avatar__mid-disc" cx={cx} cy={cy} r={size * 0.279} fill="rgba(24,60,128,0.14)" />

        <circle className="fairy-avatar__ring fairy-avatar__ring--outer" cx={cx} cy={cy} r={outerRingRadius} fill="none" stroke={COLORS.whiteRing} strokeWidth={size * 0.058} strokeLinecap="round" />
        <circle className="fairy-avatar__ring fairy-avatar__ring--inner" cx={cx} cy={cy} r={innerRingRadius} fill="none" stroke="rgba(177,195,228,0.82)" strokeWidth={size * 0.024} strokeLinecap="round" />

        <circle className="fairy-avatar__core" cx={cx} cy={cy} r={coreRadius} fill={`url(#${gradientId}-core)`} />
        <circle className="fairy-avatar__core-outline" cx={cx} cy={cy} r={size * 0.071} fill="none" stroke="rgba(140,188,232,0.30)" strokeWidth={size * 0.006} />

        <g className="fairy-avatar__orbit">
          <circle cx={cx + orbitRadius} cy={cy + size * 0.012} r={size * 0.076} fill={`url(#${gradientId}-dotGlow)`} />
          <circle
            className="fairy-avatar__orbit-dot"
            cx={cx + orbitRadius}
            cy={cy + size * 0.012}
            r={orbitDotRadius}
            fill={COLORS.dotWhite}
            stroke="rgba(214,224,238,0.95)"
            strokeWidth={size * 0.0055}
          />
        </g>

        <g className="fairy-avatar__particles">
          {particles.map((particle, index) => (
            <circle
              key={`${particle.x}-${particle.y}-${index}`}
              className="fairy-avatar__particle"
              cx={particle.x}
              cy={particle.y}
              r={particle.r}
              fill={COLORS.particle}
              style={{ animationDelay: particle.delay }}
            />
          ))}
        </g>
      </svg>
    </div>
  );
}
