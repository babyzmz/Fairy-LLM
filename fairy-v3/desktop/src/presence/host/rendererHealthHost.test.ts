import { describe, expect, it, vi } from "vitest";

import { createPresenceRendererHealthHost } from "./rendererHealthHost";

describe("renderer health host", () => {
  it("sends only the bounded health contract to the native supervisor", async () => {
    const invoke = vi.fn(async () => "force_compatibility");
    const host = createPresenceRendererHealthHost(invoke);
    await expect(host.report({
      mode: "liquid",
      status: "context_lost",
      error_code: "WEBGL_CONTEXT_LOST",
    })).resolves.toBe("force_compatibility");
    expect(invoke).toHaveBeenCalledWith("pet_renderer_report_health", {
      report: {
        schema_version: 1,
        mode: "liquid",
        status: "context_lost",
        error_code: "WEBGL_CONTEXT_LOST",
      },
    });
    expect(JSON.stringify(invoke.mock.calls)).not.toContain("project_id");
  });

  it("fails closed to a local continue directive on malformed native output", async () => {
    const host = createPresenceRendererHealthHost(async () => ({
      action: "run.sandboxed",
    }));
    await expect(host.report({
      mode: "compatibility",
      status: "failed",
      error_code: "CANVAS2D_UNAVAILABLE",
    })).resolves.toBe("continue");
  });
});
