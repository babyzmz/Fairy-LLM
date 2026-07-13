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
  uniform float uTime;
  uniform float uEnergy;
  uniform float uDpr;
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

  float bezierBridgeDistance(vec2 point, float scale, float curve) {
    vec2 start = vec2(52.0, 0.0) * scale;
    vec2 control = vec2(112.0, curve) * scale;
    vec2 end = vec2(178.0, 0.0) * scale;
    vec2 previous = start;
    float result = 100000.0;
    for (int index = 1; index <= 8; index += 1) {
      float progress = float(index) / 8.0;
      vec2 current = quadraticBezier(start, control, end, progress);
      float radius = mix(15.0, 20.0, smoothstep(0.0, 1.0, progress)) * scale;
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
    float breathe = sin(uTime * 1.35) * 1.25 * uEnergy * uDpr;
    float core = length(point) - (72.0 * uDpr + breathe);

    float droplet = length(local - vec2(104.0, 0.0) * uDpr) - 18.0 * uDpr;
    droplet += (1.0 - uShape.x) * 220.0 * uDpr;

    float curve = clamp(uGaze.y, -1.0, 1.0) * 10.0;
    float bridge = bezierBridgeDistance(local, uDpr, curve);
    bridge += (1.0 - uShape.y) * 240.0 * uDpr;

    float capsule = capsuleDistance(
      local,
      vec2(176.0, 0.0) * uDpr,
      vec2(500.0, 0.0) * uDpr,
      32.0 * uDpr
    );
    capsule += (1.0 - uShape.z) * 560.0 * uDpr;

    float distanceField = smoothMinimum(core, droplet, 11.0 * uDpr);
    distanceField = smoothMinimum(distanceField, bridge, 14.0 * uDpr);
    distanceField = smoothMinimum(distanceField, capsule, 13.0 * uDpr);
    float ripple = sin(point.x / (31.0 * uDpr) + uTime * 0.23)
      * sin(point.y / (27.0 * uDpr) - uTime * 0.19);
    return distanceField + ripple * 0.55 * uDpr * uEnergy;
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
    if (coverage <= 0.001) discard;

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
    float innerCore = 1.0 - smoothstep(5.0 * uDpr, 14.0 * uDpr, coreDistance);
    float innerHalo = exp(-coreDistance / (20.0 * uDpr));
    float coreRing = exp(-abs(coreDistance - 30.0 * uDpr) / (2.2 * uDpr));
    vec3 aiLight = mix(vec3(0.72, 0.96, 1.0), vec3(0.24, 0.78, 0.94), innerHalo);
    glassColor = mix(glassColor, aiLight, saturate(innerHalo * 0.34 + coreRing * 0.48));
    glassColor = mix(glassColor, vec3(0.98, 1.0, 1.0), innerCore * 0.78);

    float glassAlpha = (0.075 + edge * 0.19 + fresnel * 0.075 + highlight * 0.035)
      * coverage;
    float lightAlpha = (innerCore * 0.42 + coreRing * 0.23 + innerHalo * 0.07)
      * coverage;
    float alpha = clamp(glassAlpha + lightAlpha, 0.0, 0.76);
    gl_FragColor = vec4(glassColor * alpha, alpha);
  }
`;
