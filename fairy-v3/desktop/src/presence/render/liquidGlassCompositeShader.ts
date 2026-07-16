export const LIQUID_GLASS_COMPOSITE_GLSL = `
  void main() {
    vec2 pixel = vec2(vUv.x * uResolution.x, vUv.y * uResolution.y);
    vec2 point = pixel - uAnchor;
    float distanceField = sceneSdf(point);
    float antialias = max(fwidth(distanceField), 0.65 * uDpr);
    float coverage = 1.0 - smoothstep(-antialias, antialias, distanceField);
    vec2 distanceGradient = vec2(dFdx(distanceField), dFdy(distanceField));
    vec2 rimNormal = normalize(distanceGradient + vec2(0.0001));
    float thickness = thicknessField(point, distanceField);
    float curvature = curvatureApprox(point);
    float edgeLens = edgeLensResponse(distanceField, thickness, curvature);

    vec2 localScreenPixel = vec2(pixel.x, uResolution.y - pixel.y);
    vec2 absoluteScreenPixel = uRenderOrigin + localScreenPixel;
    vec2 opticalNormal = surfaceNormal(point);
    float microSlope = sin(
      absoluteScreenPixel.x * 0.012
        + absoluteScreenPixel.y * 0.009
        + uTime * 0.1
    ) * 0.022 * uEnergy * edgeLens;
    opticalNormal += vec2(microSlope, -microSlope * 0.68);
    float opticalLength = length(opticalNormal);
    opticalNormal = opticalNormal
      / max(0.0001, opticalLength)
      * step(0.0001, opticalLength);
    vec2 screenNormal = vec2(opticalNormal.x, -opticalNormal.y);
    vec3 normal3 = normalize(vec3(opticalNormal * (0.8 + edgeLens * 0.45), 1.0));

    float refractionDistance = uRefractionPx
      * edgeLens
      * mix(0.72, 1.0, curvature);
    vec2 refractedPixel = absoluteScreenPixel + screenNormal * refractionDistance;
    float chromaMask = pow(edgeLens, 2.5)
      * saturate(curvature * 1.15);
    float spectralDistance = min(1.0, uDispersionPx) * chromaMask;
    vec3 transmitted = chromaticDispersion(
      refractedPixel,
      screenNormal,
      spectralDistance
    );
    vec3 directEnvironment = screenSpaceEnvironment(absoluteScreenPixel);
    vec2 adaptationNormal = mix(
      normalize(vec2(-0.58, 0.82)),
      screenNormal,
      step(0.001, length(screenNormal))
    );
    BackdropAdaptation adaptation = analyzeBackdrop(
      absoluteScreenPixel,
      adaptationNormal
    );

    vec3 refractionDelta = transmitted - directEnvironment;
    vec3 glassColor = directEnvironment + refractionDelta * (1.08 + edgeLens * 0.5);
    float localDimming = adaptation.contrast * (0.035 + edgeLens * 0.085)
      + adaptation.edgeDensity * edgeLens * 0.035;
    glassColor *= 1.0 - localDimming;
    glassColor = mix(
      glassColor,
      vec3(perceivedLuminance(glassColor)),
      adaptation.saturation * 0.035
    );

    float bottomLip = saturate((thickness - 0.88) / 0.42)
      * smoothstep(-0.1, 0.9, -point.y / max(1.0, 72.0 * uDpr));
    float causticLight = caustic(
      point,
      distanceField,
      rimNormal,
      bottomLip
    );
    float oppositeAttenuation = exp(
      -abs(distanceField + 4.2 * uDpr) / max(1.0, 4.4 * uDpr)
    ) * pow(max(dot(rimNormal, normalize(vec2(0.62, -0.78))), 0.0), 1.55);
    glassColor += vec3(1.0, 0.985, 0.93) * causticLight * 0.68;
    glassColor *= 1.0 - oppositeAttenuation * 0.09;

    vec3 keyDirection = normalize(vec3(
      -0.48 + uGaze.x * 0.1,
      0.7 - uGaze.y * 0.08,
      0.62
    ));
    vec3 fillDirection = normalize(vec3(0.56, -0.32, 0.82));
    float keyHighlight = pow(max(dot(normal3, keyDirection), 0.0), 42.0)
      * edgeLens;
    float counterHighlight = pow(max(dot(normal3, fillDirection), 0.0), 30.0)
      * exp(-abs(distanceField + 6.5 * uDpr) / max(1.0, 3.8 * uDpr));
    float fresnel = schlickFresnel(max(normal3.z, 0.0), 0.032);
    glassColor += vec3(1.0, 0.997, 0.98) * keyHighlight * 0.42;
    glassColor += vec3(0.82, 0.94, 1.0) * counterHighlight * 0.2;

    float edgeBand = exp(-abs(distanceField) / (3.2 * uDpr));
    float rimLighting = 0.5
      + 0.5 * dot(rimNormal, normalize(vec2(-0.58, 0.82)));
    vec3 lightRim = mix(vec3(0.64, 0.68, 0.72), vec3(0.99), rimLighting);
    vec3 darkRim = mix(vec3(0.08, 0.1, 0.12), vec3(0.34, 0.37, 0.4), rimLighting);
    vec3 adaptiveRim = mix(
      lightRim,
      darkRim,
      smoothstep(0.68, 0.9, adaptation.luminance)
    );
    float rimMix = (
      edgeBand * 0.075
        + edgeLens * (0.105 + fresnel * 0.11)
    ) * uRimStrength;
    glassColor = mix(glassColor, adaptiveRim, saturate(rimMix));

    float spectralPolarity = dot(rimNormal, normalize(vec2(-0.76, 0.65)));
    vec3 spectralHint = vec3(0.035, 0.0, -0.035)
      * spectralPolarity
      * chromaMask;
    glassColor += spectralHint;

    vec2 internalRefraction = screenNormal * refractionDistance * 0.16;
    float particle = particleField(point + internalRefraction);
    float shadow = narrowContactShadow(point, distanceField);
    float windowEdgeDistance = min(
      min(pixel.x, uResolution.x - pixel.x),
      min(pixel.y, uResolution.y - pixel.y)
    );
    shadow *= smoothstep(0.0, 7.0 * uDpr, windowEdgeDistance);
    if (coverage + particle + shadow <= 0.001) discard;

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
      max(
        dot(
          normalize(coreOffset + vec2(0.0001)),
          normalize(vec2(-0.7, 0.72))
        ),
        0.0
      ),
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
    vec3 aiLight = mix(
      uAccent * 0.82,
      vec3(0.88, 0.98, 1.0),
      innerCore * 0.72
    );
    glassColor = mix(glassColor, vec3(0.04, 0.12, 0.17), coreChannel * 0.18);
    glassColor = mix(
      glassColor,
      aiLight,
      saturate(innerHalo * 0.25 + coreRing * 0.68 + outerCoreRing * 0.3)
    );
    glassColor += vec3(0.94, 0.99, 1.0) * orbitHighlight * 0.28;
    glassColor = mix(glassColor, vec3(0.98, 1.0, 1.0), innerCore * 0.76);

    float centerAlpha = 0.052 + adaptation.variance * 0.008;
    float glassAlpha = clamp(
      centerAlpha
        + edgeLens * 0.17 * uRimStrength
        + edgeBand * 0.055 * uRimStrength
        + fresnel * edgeLens * 0.045
        + keyHighlight * 0.05
        + counterHighlight * 0.03
        + causticLight * 0.1,
      0.0,
      0.42
    ) * coverage;
    float speechPulse = uSpeechLevel * (0.5 + 0.5 * sin(uTime * 10.0));
    float lightAlpha = (
      innerCore * (0.42 + speechPulse * 0.18)
        + coreRing * (0.34 + uEnergy * 0.05)
        + outerCoreRing * 0.12
        + innerHalo * 0.08
    ) * coverage;
    float particleAlpha = particle * mix(0.18, 0.3, uEnergy);
    glassColor = mix(
      glassColor,
      uAccent,
      saturate(particle * 0.85 + coreRing * 0.16)
    );
    float foregroundAlpha = clamp(
      glassAlpha + lightAlpha + particleAlpha,
      0.0,
      0.78
    ) * uOpacity;
    float shadowAlpha = shadow * (1.0 - foregroundAlpha) * uOpacity;
    float alpha = clamp(foregroundAlpha + shadowAlpha, 0.0, 0.78);
    vec3 premultiplied = clamp(glassColor, 0.0, 1.0) * foregroundAlpha;
    gl_FragColor = vec4(premultiplied, alpha);
  }
`;
