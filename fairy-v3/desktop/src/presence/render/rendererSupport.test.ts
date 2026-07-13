import { describe, expect, it } from "vitest";

import {
  detectPresenceRendererCapability,
  resolvePresenceRendererMode,
  type PresenceRendererCapability,
} from "./rendererSupport";

describe("presence renderer capability", () => {
  it("accepts an alpha premultiplied WebGL2 context", () => {
    const capability = detectPresenceRendererCapability(
      {
        getContext: () =>
          ({
            getContextAttributes: () => ({
              alpha: true,
              antialias: true,
              premultipliedAlpha: true,
            }),
            getExtension: () => null,
          }) as unknown as WebGL2RenderingContext,
      },
      { userAgent: "test", gpu: {} },
    );

    expect(capability).toMatchObject({
      webgl2: true,
      webgpu: true,
      alpha: true,
      premultiplied_alpha: true,
      error_code: null,
    });
    expect(resolvePresenceRendererMode("auto", capability)).toBe("liquid");
  });

  it("falls back when WebGL2 cannot preserve transparent output", () => {
    const capability: PresenceRendererCapability = {
      webgl2: true,
      webgpu: false,
      alpha: true,
      premultiplied_alpha: false,
      antialias: true,
      renderer: null,
      error_code: null,
    };

    expect(resolvePresenceRendererMode("liquid", capability)).toBe("compatibility");
    expect(resolvePresenceRendererMode("compatibility", capability)).toBe("compatibility");
  });

  it("reports a stable error when context creation fails", () => {
    const capability = detectPresenceRendererCapability(
      { getContext: () => null },
      { userAgent: "test" },
    );

    expect(capability.error_code).toBe("WEBGL2_UNAVAILABLE");
    expect(resolvePresenceRendererMode("auto", capability)).toBe("compatibility");
  });
});
