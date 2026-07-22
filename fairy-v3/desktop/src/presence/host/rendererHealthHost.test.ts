import { describe, expect, it, vi } from "vitest";

import { createPresenceRendererHealthHost } from "./rendererHealthHost";

describe("renderer health host", () => {
  it("sends only the bounded health contract to the native supervisor", async () => {
    const invoke = vi.fn(async () => "force_compatibility");
    const host = createPresenceRendererHealthHost(invoke);
    await expect(host.report({
      requested_mode: "liquid",
      mode: "liquid",
      actual_backend: "webgl_compatibility",
      optics_source: "webgl_texture",
      status: "context_lost",
      error_code: "WEBGL_CONTEXT_LOST",
      fallback_reason: "WEBGL_CONTEXT_LOST",
      monitor_refresh_hz: 0,
      effective_fps: 60,
    })).resolves.toBe("force_compatibility");
    expect(invoke).toHaveBeenCalledWith("pet_renderer_report_health", {
      report: {
        schema_version: 2,
        requested_mode: "liquid",
        mode: "liquid",
        actual_backend: "webgl_compatibility",
        optics_source: "webgl_texture",
        status: "context_lost",
        error_code: "WEBGL_CONTEXT_LOST",
        fallback_reason: "WEBGL_CONTEXT_LOST",
        monitor_refresh_hz: 0,
        effective_fps: 60,
      },
    });
    expect(JSON.stringify(invoke.mock.calls)).not.toContain("project_id");
  });

  it("fails closed to a local continue directive on malformed native output", async () => {
    const host = createPresenceRendererHealthHost(async () => ({
      action: "run.sandboxed",
    }));
    await expect(host.report({
      requested_mode: "compatibility",
      mode: "compatibility",
      actual_backend: "none",
      optics_source: "none",
      status: "failed",
      error_code: "CANVAS2D_UNAVAILABLE",
      fallback_reason: "CANVAS2D_UNAVAILABLE",
      monitor_refresh_hz: 0,
      effective_fps: 0,
    })).resolves.toBe("continue");
  });
});
