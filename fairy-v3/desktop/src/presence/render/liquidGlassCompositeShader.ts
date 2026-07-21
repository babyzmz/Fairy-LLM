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
    float edgeBand = exp(-abs(distanceField) / (3.2 * uDpr));

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
    float chromaMask = smoothstep(0.76, 1.0, edgeLens)
      * mix(0.6, 1.0, curvature);
    float spectralDistance = min(0.45, uDispersionPx) * chromaMask;
    if (uReducedTransparency > 0.5 || uIncreasedContrast > 0.5) {
      spectralDistance = 0.0;
    }
    vec3 primaryTransmission = screenSpaceEnvironment(refractedPixel);
    vec3 spectralTransmission = chromaticDispersion(
      refractedPixel,
      screenNormal,
      spectralDistance
    );
    vec3 transmitted = mix(
      primaryTransmission,
      spectralTransmission,
      chromaMask * 0.42
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
    if (uReducedTransparency > 0.5) {
      float accessibilityFillLuminance = adaptation.luminance > 0.58 ? 0.08 : 0.94;
      glassColor = mix(glassColor, vec3(accessibilityFillLuminance), 0.2);
    }

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
    if (uIncreasedContrast > 0.5) {
      float contrastTone = adaptation.luminance > 0.58 ? 0.01 : 0.99;
      glassColor = mix(glassColor, vec3(contrastTone), saturate(edgeBand * 0.24));
    }

    float spectralPolarity = dot(rimNormal, normalize(vec2(-0.76, 0.65)));
    float warmDispersion = max(spectralPolarity, 0.0) * chromaMask;
    float coolDispersion = max(-spectralPolarity, 0.0) * chromaMask;
    glassColor += vec3(0.18, 0.006, -0.075) * warmDispersion;
    glassColor += vec3(-0.03, 0.006, 0.065) * coolDispersion;

    vec2 internalRefraction = screenNormal * refractionDistance * 0.16;
    float particle = particleField(point + internalRefraction);
    float shadow = narrowContactShadow(point, distanceField);
    float windowEdgeDistance = min(
      min(pixel.x, uResolution.x - pixel.x),
      min(pixel.y, uResolution.y - pixel.y)
    );
    shadow *= smoothstep(0.0, 7.0 * uDpr, windowEdgeDistance);
    if (coverage + particle + shadow <= 0.001) discard;

    // Fairy's identity marks live above the optical material. Their geometry must remain in
    // undisplaced surface coordinates so refraction, dispersion and caustics cannot bend them.
    vec2 identityOffset = point - uGaze * 3.5 * uDpr;
    float coreDistance = length(identityOffset);
    float identityScale = max(0.5, uDpr * uSizeScale);
    float identityPoint = exp(
      -(coreDistance * coreDistance) / (42.0 * identityScale * identityScale)
    );
    float identityGlow = exp(
      -(coreDistance * coreDistance) / (210.0 * identityScale * identityScale)
    );
    float identityBreath = 0.82 + 0.18 * sin(uTime * max(0.35, uPulseSpeed));
    float identityAtmosphereOuter = exp(
      -abs(coreDistance - 36.0 * identityScale) / (1.25 * identityScale)
    );
    float identityAtmosphereInner = exp(
      -abs(coreDistance - 24.0 * identityScale) / (1.05 * identityScale)
    );
    float identityAtmosphere = (
      identityAtmosphereOuter * (0.055 + uEnergy * 0.045)
        + identityAtmosphereInner * (0.035 + uEnergy * 0.035)
    ) * mix(0.82, 1.0, identityBreath);
    vec3 identityLight = mix(uAccent, vec3(0.95, 0.99, 1.0), 0.72);

    float centerAlpha = mix(
      0.038 + adaptation.variance * 0.008,
      0.22,
      uReducedTransparency
    );
    float displacedReplacement = smoothstep(
      0.3 * uDpr,
      1.15 * uDpr,
      abs(refractionDistance)
    );
    float replacementMaterial = max(pow(edgeLens, 1.2), displacedReplacement);
    const float maximumGlassAlpha = 0.50;
    float glassAlpha = clamp(
      centerAlpha
        + replacementMaterial * 0.93 * uRimStrength
        + edgeBand * 0.18 * uRimStrength
        + fresnel * edgeLens * 0.08
        + keyHighlight * 0.12
        + counterHighlight * 0.07
        + causticLight * 0.16,
      0.0,
      maximumGlassAlpha
    ) * coverage;
    float speechPulse = uSpeechLevel * (0.5 + 0.5 * sin(uTime * 10.0));
    float atmosphereLayerAlpha = clamp(
      identityAtmosphere * 1.55 * coverage * uOpacity,
      0.0,
      0.92
    );
    float glowLayerAlpha = clamp(
      identityGlow * (0.045 + uEnergy * 0.075) * coverage * uOpacity,
      0.0,
      0.92
    );
    float pointLayerAlpha = clamp(
      identityPoint
        * (0.48 + uEnergy * 0.18 + speechPulse * 0.18)
        * identityBreath
        * coverage
        * uOpacity,
      0.0,
      0.92
    );
    float identityAlpha = 1.0
      - (1.0 - atmosphereLayerAlpha)
        * (1.0 - glowLayerAlpha)
        * (1.0 - pointLayerAlpha);
    float particleAlpha = particle * mix(0.18, 0.3, uEnergy);
    glassColor = mix(
      glassColor,
      uAccent,
      saturate(particle * 0.85 + identityGlow * uEnergy * 0.08)
    );
    float materialAlpha = clamp(
      glassAlpha + particleAlpha,
      0.0,
      0.96
    ) * uOpacity;
    vec3 materialPremultiplied = clamp(glassColor, 0.0, 1.0) * materialAlpha;
    vec3 identityPremultiplied = identityLight * atmosphereLayerAlpha;
    identityPremultiplied += mix(uAccent, identityLight, 0.55)
      * glowLayerAlpha
      * (1.0 - atmosphereLayerAlpha);
    identityPremultiplied += vec3(0.97, 0.99, 1.0)
      * pointLayerAlpha
      * (1.0 - atmosphereLayerAlpha)
      * (1.0 - glowLayerAlpha);
    vec3 foregroundPremultiplied = identityPremultiplied
      + materialPremultiplied * (1.0 - identityAlpha);
    float foregroundAlpha = identityAlpha + materialAlpha * (1.0 - identityAlpha);
    float shadowAlpha = shadow * (1.0 - foregroundAlpha) * uOpacity;
    float alpha = clamp(foregroundAlpha + shadowAlpha, 0.0, 0.96);
    gl_FragColor = vec4(foregroundPremultiplied, alpha);
  }
`;
