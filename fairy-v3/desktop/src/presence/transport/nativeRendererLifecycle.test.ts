import { describe, expect, it } from "vitest";

import { nativeRendererLifecycleSignalSchema } from "./nativeRendererLifecycle";

describe("native renderer lifecycle signal", () => {
  it("accepts only versioned, public lifecycle reasons", () => {
    expect(nativeRendererLifecycleSignalSchema.safeParse({
      schema_version: 1,
      reason: "surface_changed",
    }).success).toBe(true);
    expect(nativeRendererLifecycleSignalSchema.safeParse({
      schema_version: 1,
      reason: "drag_ended",
    }).success).toBe(true);
    expect(nativeRendererLifecycleSignalSchema.safeParse({
      schema_version: 1,
      reason: "drag_suspended",
    }).success).toBe(false);
    expect(nativeRendererLifecycleSignalSchema.safeParse({
      schema_version: 1,
      reason: "resume",
      monitor_pixels: "forbidden",
    }).success).toBe(false);
    expect(nativeRendererLifecycleSignalSchema.safeParse({
      schema_version: 2,
      reason: "shutdown",
    }).success).toBe(false);
  });
});
