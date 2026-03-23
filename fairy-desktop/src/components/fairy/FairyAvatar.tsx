import { useId, useMemo } from "react";

type FairyAvatarMode = "default" | "idle" | "active";

interface FairyAvatarProps {
  size?: number;
  animated?: boolean;
  className?: string;
  mode?: FairyAvatarMode;
  glow?: boolean;
}

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
  mode = "default",
  glow = true,
}: FairyAvatarProps): JSX.Element {
  const gradientId = useId().replace(/:/g, "");
  const shellPath = useMemo(() => buildShellPath(size), [size]);
  const particles = useMemo(() => buildParticles(size), [size]);
  const cx = size / 2;
  const cy = size / 2;
  const outerRingRadius = size * 0.1635;
  const innerRingRadius = size * 0.084;
  const orbitRadius = size * 0.118;
  const orbitDotRadius = size * 0.0435;
  const coreRadius = size * 0.0695;

  return (
    <div
      className={[
        "fairy-avatar",
        animated ? "fairy-avatar--animated" : "fairy-avatar--static",
        glow ? "fairy-avatar--glow" : "",
        `fairy-avatar--${mode}`,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      style={{ width: size, height: size }}
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
