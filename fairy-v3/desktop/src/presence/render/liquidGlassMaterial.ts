import type { PresenceInteractionSnapshot } from "../domain/interaction";
import { LIQUID_GLASS_COMPOSITE_GLSL } from "./liquidGlassCompositeShader";
import { LIQUID_GLASS_OPTICS_GLSL } from "./liquidGlassOpticsShader";
import type { PresenceRenderSnapshot } from "./presenceRenderer";

export interface LiquidShapeTarget {
  droplet: number;
  bridge: number;
  capsule: number;
}

export interface LiquidDirection {
  x: number;
  y: number;
}

const HIDDEN_SHAPE: LiquidShapeTarget = Object.freeze({
  droplet: 0,
  bridge: 0,
  capsule: 0,
});
const DROPLET_SHAPE: LiquidShapeTarget = Object.freeze({
  droplet: 1,
  bridge: 0,
  capsule: 0,
});
const BRIDGE_SHAPE: LiquidShapeTarget = Object.freeze({
  droplet: 1,
  bridge: 1,
  capsule: 0,
});
const INPUT_REVEAL_SHAPE: LiquidShapeTarget = Object.freeze({
  droplet: 0,
  bridge: 1,
  capsule: 1,
});
const RETURNING_SHAPE: LiquidShapeTarget = Object.freeze({
  droplet: 0,
  bridge: 1,
  capsule: 0,
});

export function liquidShapeTargetForPhase(
  phase: PresenceInteractionSnapshot["phase"] | null,
): LiquidShapeTarget {
  switch (phase) {
    case "droplet":
      return DROPLET_SHAPE;
    case "stretching":
      return BRIDGE_SHAPE;
    case "input_reveal":
      return INPUT_REVEAL_SHAPE;
    case "returning":
      return RETURNING_SHAPE;
    default:
      return HIDDEN_SHAPE;
  }
}

export function liquidShapeTargetForSnapshot(
  snapshot: PresenceRenderSnapshot,
): LiquidShapeTarget {
  const target = liquidShapeTargetForPhase(snapshot.interaction?.phase ?? null);
  if (!snapshot.input_capsule_visible) {
    return target.capsule > 0 ? { ...target, capsule: 0 } : target;
  }
  return target;
}

export function liquidDirectionForSnapshot(
  snapshot: PresenceRenderSnapshot,
): LiquidDirection {
  const interaction = snapshot.interaction;
  const expansion = interaction?.placement.expansion_direction === "left" ? -1 : 1;
  if (snapshot.input_capsule_visible) {
    const width = Math.min(420, Math.max(280, snapshot.input_capsule_width));
    const capsuleCenterX = expansion > 0 ? 24 + width / 2 : 640 - 24 - width / 2;
    const coreX = expansion > 0 ? 96 : 544;
    const x = capsuleCenterX - coreX;
    const y = 220 - 88;
    const length = Math.hypot(x, y);
    return { x: x / length, y: y / length };
  }
  const gaze = interaction?.cursor.direction ?? { x: 0, y: 0 };
  const x = expansion * 0.96 + gaze.x * 0.08;
  const y = gaze.y * 0.18;
  const length = Math.hypot(x, y);
  if (length <= Number.EPSILON) return { x: expansion, y: 0 };
  return { x: x / length, y: y / length };
}

export const LIQUID_GLASS_VERTEX_SHADER = `
  varying vec2 vUv;

  void main() {
    vUv = uv;
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`;

export const LIQUID_GLASS_FRAGMENT_SHADER = `
  precision highp float;

  uniform vec2 uResolution;
  uniform vec2 uAnchor;
  uniform vec2 uDirection;
  uniform vec2 uCapsuleOffset;
  uniform vec2 uGaze;
  uniform vec2 uRenderOrigin;
  uniform vec2 uMonitorOrigin;
  uniform vec2 uMonitorSize;
  uniform vec3 uShape;
  uniform vec3 uAccent;
  uniform float uTime;
  uniform float uEnergy;
  uniform float uDpr;
  uniform float uParticleCount;
  uniform float uParticleSeed;
  uniform float uPulseSpeed;
  uniform float uSpeechLevel;
  uniform float uSizeScale;
  uniform float uOpacity;
  uniform float uReturnBounce;
  uniform float uRefractionPx;
  uniform float uDispersionPx;
  uniform float uCausticStrength;
  uniform float uLensStrength;
  uniform float uRimStrength;
  uniform float uShadowStrength;
  uniform float uReducedTransparency;
  uniform float uIncreasedContrast;
  uniform sampler2D uBackdropTexture;
  uniform vec2 uBackdropSize;
  uniform float uBackdropReady;
  uniform float uCapsuleHalfWidth;
  varying vec2 vUv;

  float saturate(float value) {
    return clamp(value, 0.0, 1.0);
  }

  float smoothMinimum(float left, float right, float radius) {
    float safeRadius = max(radius, 0.0001);
    float blend = saturate(0.5 + 0.5 * (right - left) / safeRadius);
    return mix(right, left, blend) - safeRadius * blend * (1.0 - blend);
  }

  float segmentDistance(vec2 point, vec2 start, vec2 end) {
    vec2 segment = end - start;
    float lengthSquared = max(dot(segment, segment), 0.0001);
    float progress = saturate(dot(point - start, segment) / lengthSquared);
    return length(point - start - segment * progress);
  }

  vec2 quadraticBezier(vec2 start, vec2 control, vec2 end, float progress) {
    float inverse = 1.0 - progress;
    return inverse * inverse * start
      + 2.0 * inverse * progress * control
      + progress * progress * end;
  }

  float bezierBridgeDistance(
    vec2 point,
    float scale,
    float curve,
    float morph,
    float sizeScale
  ) {
    float shapeProgress = smoothstep(0.0, 1.0, saturate(morph));
    float sizeOffset = (sizeScale - 1.0) * 44.0;
    vec2 start = vec2(52.0 * sizeScale, 0.0) * scale;
    vec2 control = mix(
      vec2(58.0 * sizeScale, 0.0),
      vec2(112.0 + sizeOffset, curve),
      shapeProgress
    ) * scale;
    float capsuleReach = max(82.0 * scale, length(uCapsuleOffset) - uCapsuleHalfWidth * 0.72);
    vec2 end = mix(
      vec2(64.0 * sizeScale, 0.0),
      vec2(capsuleReach / scale + sizeOffset, 0.0),
      shapeProgress
    ) * scale;
    vec2 previous = start;
    float result = 100000.0;
    for (int index = 1; index <= 8; index += 1) {
      float segmentProgress = float(index) / 8.0;
      vec2 current = quadraticBezier(start, control, end, segmentProgress);
      float targetRadius = mix(
        15.0,
        20.0,
        smoothstep(0.0, 1.0, segmentProgress)
      );
      float radius = mix(2.0, targetRadius, morph) * scale * sizeScale;
      result = min(result, segmentDistance(point, previous, current) - radius);
      previous = current;
    }
    return result;
  }

  float capsuleDistance(vec2 point, float morph) {
    float shapeProgress = smoothstep(0.0, 1.0, saturate(morph));
    vec2 center = mix(normalize(uCapsuleOffset) * 82.0 * uDpr, uCapsuleOffset, shapeProgress);
    float halfWidth = mix(2.0 * uDpr, uCapsuleHalfWidth, shapeProgress);
    float radius = mix(2.0, 26.0, shapeProgress) * uDpr;
    float halfSegment = max(0.0, halfWidth - radius);
    float distanceField = segmentDistance(
      point,
      center - vec2(halfSegment, 0.0),
      center + vec2(halfSegment, 0.0)
    ) - radius;
    return mix(100000.0, distanceField, step(0.001, shapeProgress));
  }

  float liquidDistance(vec2 point) {
    vec2 axis = normalize(uDirection);
    vec2 normalAxis = vec2(-axis.y, axis.x);
    vec2 local = vec2(dot(point, axis), dot(point, normalAxis));
    float animatedTime = uTime * uPulseSpeed;
    float breathe = sin(animatedTime * 1.35)
      * (1.25 * uEnergy + 2.2 * uSpeechLevel) * uDpr;
    float coreRadius = 72.0 * uDpr * uSizeScale;
    float core = length(point) - (
      coreRadius + breathe + coreRadius * uReturnBounce
    );

    float dropletMorph = smoothstep(0.0, 1.0, saturate(uShape.x));
    float sizeOffset = (uSizeScale - 1.0) * 44.0;
    float dropletCenter = mix(62.0 * uSizeScale, 104.0 + sizeOffset, dropletMorph);
    float dropletRadius = mix(2.0, 18.0, dropletMorph) * uSizeScale;
    float droplet = length(local - vec2(dropletCenter, 0.0) * uDpr)
      - dropletRadius * uDpr;

    float curve = clamp(uGaze.y, -1.0, 1.0) * 10.0;
    float bridge = bezierBridgeDistance(
      local,
      uDpr,
      curve,
      saturate(uShape.y),
      uSizeScale
    );
    float capsule = capsuleDistance(point, saturate(uShape.z));

    float distanceField = core;
    if (dropletMorph >= 0.001) {
      distanceField = smoothMinimum(distanceField, droplet, 11.0 * uDpr);
    }
    float bridgeMorph = smoothstep(0.0, 1.0, saturate(uShape.y));
    if (bridgeMorph >= 0.001) {
      distanceField = smoothMinimum(distanceField, bridge, 14.0 * uDpr);
    }
    distanceField = min(distanceField, capsule);
    float ripple = sin(point.x / (31.0 * uDpr) + animatedTime * 0.23)
      * sin(point.y / (27.0 * uDpr) - animatedTime * 0.19);
    return distanceField + ripple * 0.55 * uDpr * uEnergy;
  }

  float hashValue(float value) {
    return fract(sin(value * 91.3458 + 17.234) * 47453.5453);
  }

  float particleField(vec2 point) {
    float result = 0.0;
    float animatedTime = uTime * uPulseSpeed;
    for (int index = 0; index < 18; index += 1) {
      float particleIndex = float(index);
      float enabled = 1.0 - step(uParticleCount, particleIndex + 0.5);
      float seed = hashValue(particleIndex * 2.399 + uParticleSeed);
      float orbit = mix(84.0, 122.0, hashValue(seed * 13.7)) * uDpr * uSizeScale;
      float velocity = mix(0.035, 0.085, hashValue(seed * 29.1));
      float angle = seed * 6.2831853 + animatedTime * velocity;
      vec2 position = vec2(cos(angle), sin(angle) * 0.82) * orbit;
      float radius = mix(0.7, 1.55, hashValue(seed * 41.3)) * uDpr;
      vec2 delta = point - position;
      float glow = exp(-dot(delta, delta) / max(0.001, radius * radius * 3.4));
      result = max(result, glow * enabled);
    }
    return result;
  }

${LIQUID_GLASS_OPTICS_GLSL}

${LIQUID_GLASS_COMPOSITE_GLSL}
`;
