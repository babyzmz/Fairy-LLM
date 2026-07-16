import { describe, expect, it } from "vitest";

import {
  INPUT_GLASS_FRAGMENT_SHADER,
  INPUT_GLASS_VERTEX_SHADER,
} from "./inputLiquidGlassRenderer";

describe("input liquid glass material", () => {
  it("uses WebGL2 and keeps refraction continuous at the outer boundary", () => {
    expect(INPUT_GLASS_VERTEX_SHADER).toContain("#version 300 es");
    expect(INPUT_GLASS_FRAGMENT_SHADER).toContain("#version 300 es");
    expect(INPUT_GLASS_FRAGMENT_SHADER).toContain("roundedRectDistance");
    expect(INPUT_GLASS_FRAGMENT_SHADER).toContain("boundaryContinuity");
    expect(INPUT_GLASS_FRAGMENT_SHADER).toContain("sampleBackdrop");
    expect(INPUT_GLASS_FRAGMENT_SHADER).toContain("redSample");
    expect(INPUT_GLASS_FRAGMENT_SHADER).toContain("greenSample");
    expect(INPUT_GLASS_FRAGMENT_SHADER).toContain("blueSample");
    expect(INPUT_GLASS_FRAGMENT_SHADER).toContain("fresnel");
    expect(INPUT_GLASS_FRAGMENT_SHADER).toContain("keyHighlight");
    expect(INPUT_GLASS_FRAGMENT_SHADER).toContain("fillHighlight");
    expect(INPUT_GLASS_FRAGMENT_SHADER).toContain("caustic");
    expect(INPUT_GLASS_FRAGMENT_SHADER).toContain("lowerShadow");
  });
});
