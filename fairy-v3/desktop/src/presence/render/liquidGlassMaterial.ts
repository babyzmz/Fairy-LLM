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

  float hashCell(vec2 coordinate) {
    return fract(sin(dot(coordinate, vec2(127.1, 311.7))) * 43758.5453);
  }

  float cellNoise(vec2 coordinate) {
    vec2 cell = floor(coordinate);
    vec2 local = fract(coordinate);
    vec2 blend = local * local * (3.0 - 2.0 * local);
    float topLeft = hashCell(cell);
    float topRight = hashCell(cell + vec2(1.0, 0.0));
    float bottomLeft = hashCell(cell + vec2(0.0, 1.0));
    float bottomRight = hashCell(cell + vec2(1.0, 1.0));
    return mix(
      mix(topLeft, topRight, blend.x),
      mix(bottomLeft, bottomRight, blend.x),
      blend.y
    );
  }

  float opticalThickness(float distanceField) {
    float interior = max(-distanceField, 0.0);
    float depthScale = max(1.0, 18.0 * uDpr * uSizeScale);
    float roundedDepth = 1.0 - exp(-interior / depthScale);
    return pow(saturate(roundedDepth), 0.62);
  }

  vec3 screenSpaceEnvironment(vec2 screenPixel) {
    vec2 safeMonitorSize = max(uMonitorSize, vec2(1.0));
    vec2 screenUv = (screenPixel - uMonitorOrigin) / safeMonitorSize;
    float aspect = safeMonitorSize.x / safeMonitorSize.y;
    vec2 field = vec2(screenUv.x * aspect, screenUv.y);
    float broad = 0.5 + 0.5 * sin(field.x * 4.8 + field.y * 3.1 + 0.35);
    float diagonal = 0.5 + 0.5 * sin(
      screenPixel.x * 0.018 - screenPixel.y * 0.013 + 0.7
    );
    float fine = 0.5 + 0.5 * sin(
      screenPixel.x * 0.051 + screenPixel.y * 0.034 + 1.4
    );
    float cells = cellNoise(field * vec2(17.0, 11.0));
    vec2 keyDelta = (screenUv - vec2(0.76, 0.18)) / vec2(0.34, 0.26);
    float keyReflection = exp(-dot(keyDelta, keyDelta) * 2.2);
    float luminance = saturate(0.34 + broad * 0.34 + cells * 0.18 + fine * 0.09);
    vec3 environment = mix(
      vec3(0.31, 0.34, 0.37),
      vec3(0.94, 0.96, 0.98),
      luminance
    );
    environment *= mix(0.91, 1.08, diagonal);
    environment += vec3(1.0, 0.985, 0.95) * keyReflection * 0.18;
    return clamp(environment, 0.0, 1.0);
  }

  vec3 chromaticDispersion(
    vec2 refractedPixel,
    vec2 spectralAxis,
    float spectralDistance
  ) {
    vec3 redSample = screenSpaceEnvironment(
      refractedPixel + spectralAxis * spectralDistance
    );
    vec3 greenSample = screenSpaceEnvironment(refractedPixel);
    vec3 blueSample = screenSpaceEnvironment(
      refractedPixel - spectralAxis * spectralDistance
    );
    return vec3(redSample.r, greenSample.g, blueSample.b);
  }

  float caustic(float distanceField, vec2 rimNormal) {
    float innerBand = exp(
      -abs(distanceField + 5.5 * uDpr) / max(1.0, 3.2 * uDpr)
    );
    float directional = pow(
      max(dot(rimNormal, normalize(vec2(-0.62, 0.78))), 0.0),
      1.55
    );
    return innerBand * directional * uCausticStrength;
  }

  void main() {
    vec2 pixel = vec2(vUv.x * uResolution.x, vUv.y * uResolution.y);
    vec2 point = pixel - uAnchor;
    float distanceField = liquidDistance(point);
    float antialias = max(fwidth(distanceField), 0.65 * uDpr);
    float coverage = 1.0 - smoothstep(-antialias, antialias, distanceField);
    vec2 gradient = vec2(dFdx(distanceField), dFdy(distanceField));
    vec2 rimNormal = normalize(gradient + vec2(0.0001));
    float thickness = opticalThickness(distanceField);
    float rimSlope = 1.0 - smoothstep(
      0.0,
      28.0 * uDpr * uSizeScale,
      max(-distanceField, 0.0)
    );
    vec2 localScreenPixel = vec2(pixel.x, uResolution.y - pixel.y);
    vec2 absoluteScreenPixel = uRenderOrigin + localScreenPixel;
    float surfacePerturbation = sin(
      absoluteScreenPixel.x * 0.012 + absoluteScreenPixel.y * 0.009 + uTime * 0.12
    ) * 0.055 * uEnergy;
    vec2 surfaceSlope = rimNormal * mix(1.62, 0.16, thickness);
    surfaceSlope += vec2(surfacePerturbation, -surfacePerturbation * 0.72);
    vec3 surfaceNormal = normalize(vec3(
      surfaceSlope,
      mix(0.58, 1.0, thickness)
    ));
    vec2 internalRefraction = surfaceNormal.xy
      * uRefractionPx * thickness * 0.12;
    float particle = particleField(point + internalRefraction);
    if (coverage + particle <= 0.001) discard;

    float edge = exp(-abs(distanceField) / (4.4 * uDpr));
    float fresnel = pow(1.0 - max(surfaceNormal.z, 0.0), 2.2);
    vec3 lightDirection = normalize(vec3(-0.42, 0.66, 0.62));
    vec3 fillDirection = normalize(vec3(0.54, -0.28, 0.8));
    float highlight = pow(max(dot(surfaceNormal, lightDirection), 0.0), 22.0);
    float fillHighlight = pow(max(dot(surfaceNormal, fillDirection), 0.0), 34.0);

    vec2 screenNormal = normalize(vec2(surfaceNormal.x, -surfaceNormal.y) + vec2(0.0001));
    float incidence = saturate(length(surfaceNormal.xy));
    float refractionDistance = uRefractionPx
      * (0.24 + incidence * 0.76)
      * (0.42 + thickness * 0.58)
      * (0.72 + rimSlope * 0.28);
    vec2 refractedPixel = absoluteScreenPixel + screenNormal * refractionDistance;
    float spectralDistance = uDispersionPx
      * (0.18 + edge * 0.82)
      * (0.32 + incidence * 0.68);
    vec3 transmitted = chromaticDispersion(
      refractedPixel,
      screenNormal,
      spectralDistance
    );
    vec3 directEnvironment = screenSpaceEnvironment(absoluteScreenPixel);
    vec3 glassColor = mix(directEnvironment, transmitted, 0.82);
    float causticLight = caustic(distanceField, rimNormal);
    float oppositeAttenuation = exp(
      -abs(distanceField + 4.0 * uDpr) / max(1.0, 4.8 * uDpr)
    ) * pow(max(dot(rimNormal, normalize(vec2(0.62, -0.78))), 0.0), 1.35);
    glassColor += vec3(1.0, 0.985, 0.94) * causticLight * 0.86;
    glassColor *= 1.0 - oppositeAttenuation * 0.11;
    glassColor += vec3(1.0, 0.99, 0.96) * highlight * 0.32;
    glassColor += vec3(0.9, 0.97, 1.0) * fillHighlight * 0.14;
    glassColor += vec3(0.42, 0.5, 0.56) * fresnel * 0.17;
    float rimLighting = 0.5 + 0.5 * dot(rimNormal, normalize(vec2(-0.58, 0.82)));
    vec3 rimColor = mix(vec3(0.30, 0.34, 0.38), vec3(0.99, 1.0, 1.0), rimLighting);
    glassColor = mix(glassColor, rimColor, edge * 0.38);
    float prismPolarity = dot(rimNormal, normalize(vec2(-0.76, 0.65)));
    vec3 warmPrism = vec3(0.28, 0.035, -0.10);
    vec3 coolPrism = vec3(-0.08, 0.03, 0.28);
    vec3 prismTint = mix(coolPrism, warmPrism, smoothstep(-0.2, 0.2, prismPolarity));
    float prismStrength = edge * incidence
      * min(uDispersionPx / max(uDpr * 2.25, 0.001), 1.0);
    glassColor += prismTint * prismStrength * 0.18;
    float spectralScale = min(uDispersionPx / max(uDpr * 2.25, 0.001), 1.0);
    float warmSpectralRim = exp(
      -abs(distanceField + uDispersionPx * 0.55) / max(0.8, uDpr * 0.92)
    );
    float coolSpectralRim = exp(
      -abs(distanceField + uDispersionPx * 1.55) / max(0.8, uDpr * 0.92)
    );
    float warmDirection = 0.34 + 0.66 * pow(
      max(dot(rimNormal, normalize(vec2(0.74, -0.67))), 0.0),
      0.85
    );
    float coolDirection = 0.34 + 0.66 * pow(
      max(dot(rimNormal, normalize(vec2(-0.74, 0.67))), 0.0),
      0.85
    );
    glassColor = mix(
      glassColor,
      vec3(1.0, 0.58, 0.3),
      warmSpectralRim * warmDirection * spectralScale * 0.3
    );
    glassColor = mix(
      glassColor,
      vec3(0.39, 0.75, 1.0),
      coolSpectralRim * coolDirection * spectralScale * 0.17
    );
    float warmDirectionalRim = edge * pow(
      max(dot(rimNormal, vec2(1.0, 0.0)), 0.0),
      1.1
    ) * spectralScale;
    glassColor = mix(
      glassColor,
      vec3(1.0, 0.46, 0.2),
      warmDirectionalRim * 0.68
    );
    float coolDirectionalRim = edge * pow(
      max(dot(rimNormal, vec2(0.0, -1.0)), 0.0),
      1.1
    ) * spectralScale;
    glassColor = mix(
      glassColor,
      vec3(0.2, 0.66, 1.0),
      coolDirectionalRim * 0.4
    );

    vec2 coreOffset = point + internalRefraction - uGaze * 3.5 * uDpr;
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

    float centerAlpha = mix(0.075, 0.052, thickness);
    float glassAlpha = clamp(
      centerAlpha
        + edge * 0.185
        + fresnel * 0.09
        + highlight * 0.035
        + fillHighlight * 0.018
        + causticLight * 0.18,
      0.0,
      0.42
    ) * coverage;
    float speechPulse = uSpeechLevel * (0.5 + 0.5 * sin(uTime * 10.0));
    float lightAlpha = (
      innerCore * (0.42 + speechPulse * 0.18)
      + coreRing * (0.23 + uEnergy * 0.04)
      + innerHalo * 0.07
    )
      * coverage;
    float particleAlpha = particle * mix(0.2, 0.34, uEnergy);
    glassColor = mix(glassColor, uAccent, saturate(particle * 0.9 + coreRing * 0.18));
    float alpha = clamp(glassAlpha + lightAlpha + particleAlpha, 0.0, 0.78)
      * uOpacity;
    gl_FragColor = vec4(clamp(glassColor, 0.0, 1.0) * alpha, alpha);
  }
`;
