export const LIQUID_GLASS_OPTICS_GLSL = `
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

  float sceneSdf(vec2 point) {
    return liquidDistance(point);
  }

  float thicknessField(vec2 point, float distanceField) {
    vec2 axis = normalize(uDirection);
    vec2 normalAxis = vec2(-axis.y, axis.x);
    vec2 local = vec2(dot(point, axis), dot(point, normalAxis));
    float interiorDepth = max(-distanceField, 0.0);
    float edgeWidth = max(1.0, 48.0 * uDpr * uSizeScale);
    float edgeProfile = 1.0 - smoothstep(0.0, edgeWidth, interiorDepth);
    float clearInterior = 1.0 - smoothstep(
      18.0 * uDpr * uSizeScale,
      25.0 * uDpr * uSizeScale,
      interiorDepth
    );
    edgeProfile *= clearInterior;
    float centerThinness = 0.065;
    float edgeBulge = pow(edgeProfile, 1.65) * 0.84;

    float verticalRadius = max(18.0, 72.0 * uDpr * uSizeScale);
    float bottomCoordinate = saturate((-local.y / verticalRadius + 1.0) * 0.5);
    float bottomLip = smoothstep(0.56, 1.0, bottomCoordinate)
      * pow(edgeProfile, 1.45)
      * 0.3;

    vec2 bridgeScale = vec2(
      max(1.0, 72.0 * uDpr),
      max(1.0, 26.0 * uDpr * uSizeScale)
    );
    vec2 bridgePoint = (local - vec2(118.0 * uDpr, 0.0)) / bridgeScale;
    float bridgeThickness = saturate(uShape.y)
      * exp(-dot(bridgePoint, bridgePoint) * 1.7)
      * 0.15;

    float capsuleHalfWidth = max(1.0, 200.0 * uDpr);
    float capsuleCorner = smoothstep(
      0.62,
      1.0,
      abs(local.x - 280.0 * uDpr) / capsuleHalfWidth
    )
      * saturate(uShape.z)
      * edgeProfile
      * 0.14;
    float interactionBulge = max(max(uShape.x, uShape.y), uShape.z)
      * edgeProfile
      * (0.045 + 0.035 * saturate(dot(normalize(point + vec2(0.001)), uGaze)));

    return clamp(
      centerThinness
        + edgeBulge
        + bottomLip
        + bridgeThickness
        + capsuleCorner
        + interactionBulge,
      centerThinness,
      1.3
    );
  }

  vec2 surfaceNormal(vec2 point) {
    float distanceField = sceneSdf(point);
    float thickness = thicknessField(point, distanceField);
    vec2 thicknessGradient = vec2(dFdx(thickness), dFdy(thickness));
    vec2 distanceGradient = vec2(dFdx(distanceField), dFdy(distanceField));
    float edgeWeight = saturate((thickness - 0.065) / 1.1);
    vec2 slope = thicknessGradient * 2.4
      + normalize(distanceGradient + vec2(0.0001)) * edgeWeight * 0.18;
    float slopeLength = length(slope);
    return slope / max(0.0001, slopeLength) * step(0.0001, slopeLength);
  }

  float curvatureApprox(vec2 point) {
    float epsilon = max(1.0, 1.5 * uDpr);
    float center = sceneSdf(point);
    float laplacian = sceneSdf(point + vec2(epsilon, 0.0))
      + sceneSdf(point - vec2(epsilon, 0.0))
      + sceneSdf(point + vec2(0.0, epsilon))
      + sceneSdf(point - vec2(0.0, epsilon))
      - 4.0 * center;
    return saturate(abs(laplacian) * 7.5 / epsilon);
  }

  float edgeLensResponse(
    float distanceField,
    float thickness,
    float curvature
  ) {
    float interiorContinuity = smoothstep(
      0.0,
      1.8 * uDpr,
      max(-distanceField, 0.0)
    );
    float edgeProfile = saturate((thickness - 0.065) / 1.08);
    float edgeLens = pow(edgeProfile, 1.55);
    return edgeLens
      * interiorContinuity
      * mix(0.72, 1.0, curvature)
      * uLensStrength;
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
    float luminance = saturate(0.3 + broad * 0.34 + cells * 0.18 + fine * 0.12);
    vec3 environment = mix(
      vec3(0.34, 0.36, 0.38),
      vec3(0.93, 0.95, 0.97),
      luminance
    );
    environment *= mix(0.94, 1.055, diagonal);
    environment += vec3(1.0, 0.985, 0.95) * keyReflection * 0.12;
    environment += vec3(0.94, 0.97, 1.0) * hairline * 0.05;
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

  float perceivedLuminance(vec3 color) {
    return dot(color, vec3(0.2126, 0.7152, 0.0722));
  }

  struct BackdropAdaptation {
    float luminance;
    float contrast;
    float variance;
    float saturation;
    float edgeDensity;
  };

  BackdropAdaptation analyzeBackdrop(vec2 screenPixel, vec2 normal) {
    vec2 tangent = vec2(-normal.y, normal.x);
    float sampleRadius = 2.25 * uDpr;
    vec3 center = screenSpaceEnvironment(screenPixel);
    vec3 normalA = screenSpaceEnvironment(screenPixel + normal * sampleRadius);
    vec3 normalB = screenSpaceEnvironment(screenPixel - normal * sampleRadius);
    vec3 tangentA = screenSpaceEnvironment(screenPixel + tangent * sampleRadius);
    vec3 tangentB = screenSpaceEnvironment(screenPixel - tangent * sampleRadius);
    float centerLuma = perceivedLuminance(center);
    vec4 neighbors = vec4(
      perceivedLuminance(normalA),
      perceivedLuminance(normalB),
      perceivedLuminance(tangentA),
      perceivedLuminance(tangentB)
    );
    float mean = dot(neighbors, vec4(0.25));
    vec4 deviations = neighbors - vec4(mean);
    float variance = dot(deviations, deviations) * 0.25;
    float maximum = max(center.r, max(center.g, center.b));
    float minimum = min(center.r, min(center.g, center.b));
    BackdropAdaptation result;
    result.luminance = centerLuma;
    result.contrast = saturate(abs(centerLuma - mean) * 3.2);
    result.variance = saturate(variance * 9.0);
    result.saturation = saturate((maximum - minimum) / max(maximum, 0.001));
    result.edgeDensity = saturate(
      (abs(neighbors.x - neighbors.y) + abs(neighbors.z - neighbors.w)) * 1.8
    );
    return result;
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

  float caustic(
    vec2 point,
    float distanceField,
    vec2 rimNormal,
    float bottomLip
  ) {
    float innerBand = exp(-pow(
      abs(distanceField + 9.5 * uDpr) / max(1.0, 3.6 * uDpr),
      1.45
    ));
    float keyArc = pow(
      max(dot(rimNormal, normalize(vec2(-0.62, 0.78))), 0.0),
      1.6
    );
    float returnArc = pow(
      max(dot(rimNormal, normalize(vec2(0.82, -0.58))), 0.0),
      2.8
    ) * 0.26;
    float movingFocus = 0.9 + 0.1 * sin(
      uTime * 0.24 + dot(rimNormal, vec2(3.1, -2.7))
    );
    float lowerFocus = 1.0 + bottomLip * 0.32
      * smoothstep(-0.15, 0.9, -point.y / max(1.0, 72.0 * uDpr));
    return innerBand
      * (keyArc + returnArc)
      * movingFocus
      * lowerFocus
      * uCausticStrength;
  }

  float narrowContactShadow(vec2 point, float distanceField) {
    vec2 shadowOffset = vec2(1.2, -2.6) * uDpr;
    float shiftedDistance = sceneSdf(point - shadowOffset);
    float softShape = 1.0 - smoothstep(0.0, 4.2 * uDpr, shiftedDistance);
    float exterior = smoothstep(-0.15 * uDpr, 1.0 * uDpr, distanceField);
    return softShape * exterior * uShadowStrength;
  }
`;
