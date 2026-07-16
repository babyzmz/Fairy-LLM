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
const INPUT_REVEAL_SHAPE: LiquidShapeTarget = Object.freeze({
  droplet: 0,
  bridge: 1,
  capsule: 1,
});
const INTERACTIVE_SHAPE: LiquidShapeTarget = Object.freeze({
  droplet: 0,
  bridge: 0,
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
      return INPUT_REVEAL_SHAPE;
    case "interactive":
      return INTERACTIVE_SHAPE;
    default:
      return HIDDEN_SHAPE;
  }
}

export function liquidShapeTargetForSnapshot(
  snapshot: PresenceRenderSnapshot,
): LiquidShapeTarget {
  const target = liquidShapeTargetForPhase(snapshot.interaction?.phase ?? null);
  if (!snapshot.input_capsule_visible) {
    return target.capsule > 0 ? HIDDEN_SHAPE : target;
  }
  return target.capsule > 0 ? target : INTERACTIVE_SHAPE;
}

export function liquidDirectionForSnapshot(
  snapshot: PresenceRenderSnapshot,
): LiquidDirection {
  const interaction = snapshot.interaction;
  const expansion = interaction?.placement.expansion_direction === "left" ? -1 : 1;
  if (snapshot.input_capsule_visible) {
    return { x: expansion, y: 0 };
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
  uniform sampler2D uBackdropTexture;
  uniform vec2 uBackdropSize;
  uniform float uBackdropReady;
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

  float capsuleDistance(vec2 point, float morph) {
    float shapeProgress = smoothstep(0.0, 1.0, saturate(morph));
    float center = mix(178.0, 280.0, shapeProgress) * uDpr;
    float halfWidth = mix(2.0, 200.0, shapeProgress) * uDpr;
    float radius = mix(2.0, 26.0, shapeProgress) * uDpr;
    float halfSegment = max(0.0, halfWidth - radius);
    vec2 segmentCenter = vec2(center, 0.0);
    float distanceField = segmentDistance(
      point,
      segmentCenter - vec2(halfSegment, 0.0),
      segmentCenter + vec2(halfSegment, 0.0)
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
    float capsule = capsuleDistance(local, saturate(uShape.z));

    float distanceField = smoothMinimum(core, droplet, 11.0 * uDpr);
    distanceField = smoothMinimum(distanceField, bridge, 14.0 * uDpr);
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
    float depthScale = max(1.0, 24.0 * uDpr * uSizeScale);
    float normalizedDepth = saturate(interior / depthScale);
    return sqrt(max(0.0, normalizedDepth * (2.0 - normalizedDepth)));
  }

  float thickEdgeProfile(float distanceField) {
    float interiorDepth = max(-distanceField, 0.0);
    float inside = 1.0 - smoothstep(
      2.0 * uDpr,
      18.0 * uDpr * uSizeScale,
      interiorDepth
    );
    float outside = 1.0 - smoothstep(0.0, 2.4 * uDpr, max(distanceField, 0.0));
    return inside * outside;
  }

  float edgeLensing(float distanceField, float thickness) {
    float interiorDepth = max(-distanceField, 0.0);
    float boundaryContinuity = smoothstep(
      0.0,
      2.2 * uDpr,
      interiorDepth
    );
    float cleanCenter = pow(1.0 - thickness, 0.72);
    return saturate(
      boundaryContinuity
        * thickEdgeProfile(distanceField)
        * mix(0.72, 1.0, cleanCenter)
        * uLensStrength
    );
  }

  float schlickFresnel(float cosine, float baseReflectance) {
    float inverse = 1.0 - saturate(cosine);
    return baseReflectance + (1.0 - baseReflectance) * pow(inverse, 5.0);
  }

  vec3 proceduralEnvironment(vec2 screenPixel) {
    vec2 safeMonitorSize = max(uMonitorSize, vec2(1.0));
    vec2 screenUv = (screenPixel - uMonitorOrigin) / safeMonitorSize;
    float aspect = safeMonitorSize.x / safeMonitorSize.y;
    vec2 field = vec2(screenUv.x * aspect, screenUv.y);
    float broad = 0.5 + 0.5 * sin(field.x * 5.2 + field.y * 3.4 + 0.35);
    float diagonal = 0.5 + 0.5 * sin(
      screenPixel.x * 0.018 - screenPixel.y * 0.013 + 0.7
    );
    float fine = 0.5 + 0.5 * sin(
      screenPixel.x * 0.064 + screenPixel.y * 0.041 + 1.4
    );
    float cells = cellNoise(field * vec2(17.0, 11.0));
    float hairline = pow(
      0.5 + 0.5 * sin(screenPixel.x * 0.027 + screenPixel.y * 0.086 + cells * 3.2),
      12.0
    );
    vec2 keyDelta = (screenUv - vec2(0.76, 0.18)) / vec2(0.34, 0.26);
    float keyReflection = exp(-dot(keyDelta, keyDelta) * 2.2);
    float luminance = saturate(0.27 + broad * 0.38 + cells * 0.2 + fine * 0.13);
    vec3 environment = mix(
      vec3(0.31, 0.34, 0.37),
      vec3(0.94, 0.96, 0.98),
      luminance
    );
    environment *= mix(0.91, 1.08, diagonal);
    environment += vec3(1.0, 0.985, 0.95) * keyReflection * 0.18;
    environment += vec3(0.94, 0.97, 1.0) * hairline * 0.075;
    return clamp(environment, 0.0, 1.0);
  }

  vec3 screenSpaceEnvironment(vec2 screenPixel) {
    vec2 safeResolution = max(uResolution, vec2(1.0));
    vec2 halfTexel = 0.5 / max(uBackdropSize, vec2(1.0));
    vec2 backdropUv = clamp(
      (screenPixel - uRenderOrigin) / safeResolution,
      halfTexel,
      vec2(1.0) - halfTexel
    );
    vec3 captured = texture2D(uBackdropTexture, backdropUv).rgb;
    return mix(proceduralEnvironment(screenPixel), captured, uBackdropReady);
  }

  vec3 chromaticDispersion(
    vec2 refractedPixel,
    vec2 spectralAxis,
    float spectralDistance
  ) {
    vec3 redSample = screenSpaceEnvironment(
      refractedPixel + spectralAxis * spectralDistance * 1.16
    );
    vec3 greenSample = screenSpaceEnvironment(refractedPixel);
    vec3 blueSample = screenSpaceEnvironment(
      refractedPixel - spectralAxis * spectralDistance * 0.92
    );
    vec3 split = vec3(redSample.r, greenSample.g, blueSample.b);
    vec2 tangent = vec2(-spectralAxis.y, spectralAxis.x);
    vec3 softA = screenSpaceEnvironment(refractedPixel + tangent * 0.7 * uDpr);
    vec3 softB = screenSpaceEnvironment(refractedPixel - tangent * 0.7 * uDpr);
    return mix(split, (split + softA + softB) / 3.0, 0.16);
  }

  float caustic(float distanceField, vec2 rimNormal) {
    float innerBand = exp(-pow(
      abs(distanceField + 10.5 * uDpr) / max(1.0, 3.8 * uDpr),
      1.35
    ));
    float keyArc = pow(
      max(dot(rimNormal, normalize(vec2(-0.62, 0.78))), 0.0),
      1.35
    );
    float returnArc = pow(
      max(dot(rimNormal, normalize(vec2(0.82, -0.58))), 0.0),
      2.6
    ) * 0.34;
    float movingFocus = 0.82 + 0.18 * sin(
      uTime * 0.32 + dot(rimNormal, vec2(3.1, -2.7))
    );
    return innerBand * (keyArc + returnArc) * movingFocus * uCausticStrength;
  }

  float narrowContactShadow(vec2 point, float distanceField) {
    vec2 shadowOffset = vec2(1.5, -3.0) * uDpr;
    float shiftedDistance = liquidDistance(point - shadowOffset);
    float softShape = 1.0 - smoothstep(0.0, 5.2 * uDpr, shiftedDistance);
    float exterior = smoothstep(-0.2 * uDpr, 1.2 * uDpr, distanceField);
    return softShape * exterior * uShadowStrength;
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
    float lensing = edgeLensing(distanceField, thickness);
    vec2 localScreenPixel = vec2(pixel.x, uResolution.y - pixel.y);
    vec2 absoluteScreenPixel = uRenderOrigin + localScreenPixel;
    float surfacePerturbation = sin(
      absoluteScreenPixel.x * 0.012 + absoluteScreenPixel.y * 0.009 + uTime * 0.12
    ) * 0.055 * uEnergy;
    vec2 surfaceSlope = rimNormal * mix(1.82, 0.08, thickness) * uLensStrength;
    surfaceSlope += vec2(surfacePerturbation, -surfacePerturbation * 0.72);
    vec3 surfaceNormal = normalize(vec3(
      surfaceSlope,
      mix(0.58, 1.0, thickness)
    ));
    vec2 internalRefraction = surfaceNormal.xy
      * uRefractionPx * (0.04 + lensing * 0.1);
    float particle = particleField(point + internalRefraction);
    float shadow = narrowContactShadow(point, distanceField);
    float windowEdgeDistance = min(
      min(pixel.x, uResolution.x - pixel.x),
      min(pixel.y, uResolution.y - pixel.y)
    );
    shadow *= smoothstep(0.0, 7.0 * uDpr, windowEdgeDistance);
    if (coverage + particle + shadow <= 0.001) discard;

    float edge = exp(-abs(distanceField) / (3.4 * uDpr));
    float thickEdge = thickEdgeProfile(distanceField);
    float fresnel = schlickFresnel(max(surfaceNormal.z, 0.0), 0.035);
    vec3 lightDirection = normalize(vec3(
      -0.46 + uGaze.x * 0.12,
      0.68 - uGaze.y * 0.1,
      0.6
    ));
    vec3 fillDirection = normalize(vec3(0.54, -0.28, 0.8));
    float keyHighlight = pow(max(dot(surfaceNormal, lightDirection), 0.0), 34.0)
      * (0.38 + thickEdge * 0.62);
    float counterHighlight = pow(max(dot(surfaceNormal, fillDirection), 0.0), 24.0)
      * exp(-abs(distanceField + 7.0 * uDpr) / max(1.0, 4.2 * uDpr));

    vec2 screenNormal = normalize(vec2(surfaceNormal.x, -surfaceNormal.y) + vec2(0.0001));
    float incidence = saturate(length(surfaceNormal.xy));
    float interiorContinuity = smoothstep(
      0.0,
      2.2 * uDpr,
      max(-distanceField, 0.0)
    );
    float bulkLensing = interiorContinuity
      * thickness
      * (1.0 - thickness)
      * uLensStrength;
    float refractionDistance = uRefractionPx
      * (
        lensing * (0.52 + incidence * 0.48)
        + bulkLensing * 0.22
      );
    vec2 refractedPixel = absoluteScreenPixel + screenNormal * refractionDistance;
    float spectralDistance = uDispersionPx
      * lensing
      * (0.34 + incidence * 0.66);
    vec3 transmitted = chromaticDispersion(
      refractedPixel,
      screenNormal,
      spectralDistance
    );
    vec3 directEnvironment = screenSpaceEnvironment(absoluteScreenPixel);
    vec3 refractionDelta = transmitted - directEnvironment;
    vec3 glassColor = directEnvironment + refractionDelta * 1.42;
    glassColor = mix(directEnvironment, glassColor, 0.2 + lensing * 0.8);
    float causticLight = caustic(distanceField, rimNormal);
    float oppositeAttenuation = exp(
      -abs(distanceField + 4.0 * uDpr) / max(1.0, 4.8 * uDpr)
    ) * pow(max(dot(rimNormal, normalize(vec2(0.62, -0.78))), 0.0), 1.35);
    glassColor += vec3(1.0, 0.985, 0.92) * causticLight * 1.18;
    glassColor *= 1.0 - oppositeAttenuation * 0.14;
    glassColor += vec3(1.0, 0.995, 0.97) * keyHighlight * 0.56;
    glassColor += vec3(0.78, 0.94, 1.0) * counterHighlight * 0.31;
    glassColor += vec3(0.76, 0.84, 0.9) * fresnel * thickEdge * 0.54;
    float rimLighting = 0.5 + 0.5 * dot(rimNormal, normalize(vec2(-0.58, 0.82)));
    vec3 rimColor = mix(vec3(0.30, 0.34, 0.38), vec3(0.99, 1.0, 1.0), rimLighting);
    glassColor = mix(
      glassColor,
      rimColor,
      (edge * 0.46 + thickEdge * fresnel * 0.22) * uRimStrength
    );
    float prismPolarity = dot(rimNormal, normalize(vec2(-0.76, 0.65)));
    vec3 warmPrism = vec3(0.28, 0.035, -0.10);
    vec3 coolPrism = vec3(-0.08, 0.03, 0.28);
    vec3 prismTint = mix(coolPrism, warmPrism, smoothstep(-0.2, 0.2, prismPolarity));
    float prismStrength = edge * incidence * lensing
      * min(uDispersionPx / max(uDpr * 2.25, 0.001), 1.0);
    glassColor += prismTint * prismStrength * 0.48;
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
      warmSpectralRim * warmDirection * spectralScale * lensing * 0.68
    );
    glassColor = mix(
      glassColor,
      vec3(0.39, 0.75, 1.0),
      coolSpectralRim * coolDirection * spectralScale * lensing * 0.56
    );
    float warmDirectionalRim = edge * pow(
      max(dot(rimNormal, vec2(1.0, 0.0)), 0.0),
      1.1
    ) * spectralScale * lensing;
    glassColor = mix(
      glassColor,
      vec3(1.0, 0.46, 0.2),
      warmDirectionalRim * 0.68
    );
    glassColor += vec3(0.34, 0.02, -0.18) * warmDirectionalRim;
    float coolDirectionalRim = edge * pow(
      max(dot(rimNormal, vec2(0.0, -1.0)), 0.0),
      1.1
    ) * spectralScale * lensing;
    glassColor = mix(
      glassColor,
      vec3(0.2, 0.66, 1.0),
      coolDirectionalRim * 0.5
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
    float outerCoreRing = exp(
      -abs(coreDistance - 44.0 * uDpr * uSizeScale)
        / (1.5 * uDpr * uSizeScale)
    );
    float orbitHighlight = outerCoreRing * pow(
      max(dot(normalize(coreOffset + vec2(0.0001)), normalize(vec2(-0.7, 0.72))), 0.0),
      4.0
    );
    float coreChannel = smoothstep(
      15.0 * uDpr * uSizeScale,
      21.0 * uDpr * uSizeScale,
      coreDistance
    ) * (1.0 - smoothstep(
      35.0 * uDpr * uSizeScale,
      43.0 * uDpr * uSizeScale,
      coreDistance
    ));
    vec3 aiLight = mix(uAccent * 0.82, vec3(0.88, 0.98, 1.0), innerCore * 0.72);
    glassColor = mix(glassColor, vec3(0.04, 0.12, 0.17), coreChannel * 0.2);
    glassColor = mix(
      glassColor,
      aiLight,
      saturate(innerHalo * 0.28 + coreRing * 0.72 + outerCoreRing * 0.34)
    );
    glassColor += vec3(0.94, 0.99, 1.0) * orbitHighlight * 0.32;
    glassColor = mix(glassColor, vec3(0.98, 1.0, 1.0), innerCore * 0.78);

    float centerAlpha = mix(0.028, 0.012, thickness);
    float glassAlpha = clamp(
      centerAlpha
        + thickEdge * 0.115 * uRimStrength
        + edge * 0.29 * uRimStrength
        + fresnel * thickEdge * 0.16
        + keyHighlight * 0.075
        + counterHighlight * 0.046
        + causticLight * 0.24,
      0.0,
      0.5
    ) * coverage;
    float speechPulse = uSpeechLevel * (0.5 + 0.5 * sin(uTime * 10.0));
    float lightAlpha = (
      innerCore * (0.42 + speechPulse * 0.18)
      + coreRing * (0.36 + uEnergy * 0.06)
      + outerCoreRing * 0.13
      + innerHalo * 0.085
    )
      * coverage;
    float particleAlpha = particle * mix(0.2, 0.34, uEnergy);
    glassColor = mix(glassColor, uAccent, saturate(particle * 0.9 + coreRing * 0.18));
    float foregroundAlpha = clamp(
      glassAlpha + lightAlpha + particleAlpha,
      0.0,
      0.78
    ) * uOpacity;
    float sampledGlassAlpha = coverage
      * uBackdropReady
      * (0.055 + lensing * 0.68 + bulkLensing * 0.12)
      * uOpacity;
    foregroundAlpha = max(foregroundAlpha, sampledGlassAlpha);
    float shadowAlpha = shadow * (1.0 - foregroundAlpha) * uOpacity;
    float alpha = clamp(foregroundAlpha + shadowAlpha, 0.0, 0.82);
    vec3 premultiplied = clamp(glassColor, 0.0, 1.0) * foregroundAlpha;
    gl_FragColor = vec4(premultiplied, alpha);
  }
`;
