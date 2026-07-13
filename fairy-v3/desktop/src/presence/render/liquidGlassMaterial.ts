import type { PresenceInteractionSnapshot } from "../domain/interaction";
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
const INTERACTIVE_SHAPE: LiquidShapeTarget = Object.freeze({
  droplet: 1,
  bridge: 1,
  capsule: 1,
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
    case "interactive":
    case "returning":
      return INTERACTIVE_SHAPE;
    default:
      return HIDDEN_SHAPE;
  }
}

export function liquidDirectionForSnapshot(
  snapshot: PresenceRenderSnapshot,
): LiquidDirection {
  const interaction = snapshot.interaction;
  const expansion = interaction?.placement.expansion_direction === "left" ? -1 : 1;
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
  uniform vec2 uGaze;
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
    vec2 end = mix(
      vec2(64.0 * sizeScale, 0.0),
      vec2(178.0 + sizeOffset, 0.0),
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

  float capsuleDistance(vec2 point, vec2 start, vec2 end, float radius) {
    return segmentDistance(point, start, end) - radius;
  }

  float liquidDistance(vec2 point) {
    vec2 axis = normalize(uDirection);
    vec2 normalAxis = vec2(-axis.y, axis.x);
    vec2 local = vec2(dot(point, axis), dot(point, normalAxis));
    float animatedTime = uTime * uPulseSpeed;
    float breathe = sin(animatedTime * 1.35)
      * (1.25 * uEnergy + 2.2 * uSpeechLevel) * uDpr;
    float core = length(point) - (72.0 * uDpr * uSizeScale + breathe);

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

    float capsuleMorph = smoothstep(0.0, 1.0, saturate(uShape.z));
    float capsuleEnd = 500.0 - (uSizeScale - 1.0) * 64.0;
    float capsule = capsuleDistance(
      local,
      vec2(176.0 + sizeOffset, 0.0) * uDpr,
      vec2(mix(176.0 + sizeOffset, capsuleEnd, capsuleMorph), 0.0) * uDpr,
      mix(0.0, 32.0 * uSizeScale, capsuleMorph) * uDpr
    );
    capsule += (1.0 - capsuleMorph) * 16.0 * uDpr;

    float distanceField = smoothMinimum(core, droplet, 11.0 * uDpr);
    distanceField = smoothMinimum(distanceField, bridge, 14.0 * uDpr);
    distanceField = smoothMinimum(distanceField, capsule, 13.0 * uDpr);
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

  vec3 environmentLight(vec2 coordinate) {
    float horizon = 0.5 + 0.5 * sin(coordinate.y * 5.7 + coordinate.x * 2.2);
    float reflection = 0.5 + 0.5 * sin((coordinate.x - coordinate.y) * 11.0 + 0.8);
    vec3 cool = vec3(0.54, 0.61, 0.68);
    vec3 light = vec3(0.96, 0.98, 1.0);
    return mix(cool, light, horizon * 0.46 + reflection * 0.14);
  }

  void main() {
    vec2 pixel = vec2(vUv.x * uResolution.x, vUv.y * uResolution.y);
    vec2 point = pixel - uAnchor;
    float distanceField = liquidDistance(point);
    float antialias = max(fwidth(distanceField), 0.65 * uDpr);
    float coverage = 1.0 - smoothstep(-antialias, antialias, distanceField);
    float particle = particleField(point);
    if (coverage + particle <= 0.001) discard;

    vec2 gradient = vec2(dFdx(distanceField), dFdy(distanceField));
    vec3 surfaceNormal = normalize(vec3(gradient * 1.55, 0.86));
    float edge = exp(-abs(distanceField) / (4.2 * uDpr));
    float fresnel = pow(1.0 - max(surfaceNormal.z, 0.0), 2.2);
    vec3 lightDirection = normalize(vec3(-0.42, 0.66, 0.62));
    float highlight = pow(max(dot(surfaceNormal, lightDirection), 0.0), 18.0);

    vec2 screenCoordinate = pixel / max(uResolution, vec2(1.0));
    vec2 distortion = surfaceNormal.xy * (0.018 + edge * 0.012);
    vec3 redSample = environmentLight(screenCoordinate + distortion * 1.16);
    vec3 greenSample = environmentLight(screenCoordinate + distortion);
    vec3 blueSample = environmentLight(screenCoordinate + distortion * 0.84);
    vec3 glassColor = vec3(redSample.r, greenSample.g, blueSample.b);
    glassColor += vec3(1.0, 0.99, 0.96) * highlight * 0.34;
    glassColor += vec3(0.30, 0.48, 0.58) * fresnel * 0.18;

    vec2 coreOffset = point - uGaze * 3.5 * uDpr;
    float coreDistance = length(coreOffset);
    float innerCore = 1.0 - smoothstep(
      5.0 * uDpr * uSizeScale,
      14.0 * uDpr * uSizeScale,
      coreDistance
    );
    float innerHalo = exp(-coreDistance / (20.0 * uDpr * uSizeScale));
    float coreRing = exp(
      -abs(coreDistance - 30.0 * uDpr * uSizeScale)
        / (2.2 * uDpr * uSizeScale)
    );
    vec3 aiLight = mix(vec3(0.8, 0.97, 1.0), uAccent, innerHalo * 0.72);
    glassColor = mix(glassColor, aiLight, saturate(innerHalo * 0.34 + coreRing * 0.48));
    glassColor = mix(glassColor, vec3(0.98, 1.0, 1.0), innerCore * 0.78);

    float glassAlpha = (0.075 + edge * 0.19 + fresnel * 0.075 + highlight * 0.035)
      * coverage;
    float speechPulse = uSpeechLevel * (0.5 + 0.5 * sin(uTime * 10.0));
    float lightAlpha = (
      innerCore * (0.42 + speechPulse * 0.18)
      + coreRing * (0.23 + uEnergy * 0.04)
      + innerHalo * 0.07
    )
      * coverage;
    float particleAlpha = particle * mix(0.2, 0.34, uEnergy);
    glassColor = mix(glassColor, uAccent, saturate(particle * 0.9 + coreRing * 0.18));
    float alpha = clamp(glassAlpha + lightAlpha + particleAlpha, 0.0, 0.76)
      * uOpacity;
    gl_FragColor = vec4(glassColor * alpha, alpha);
  }
`;
