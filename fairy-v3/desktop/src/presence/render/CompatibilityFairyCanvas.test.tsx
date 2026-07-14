import { describe, expect, it } from "vitest";

import {
  COMPATIBILITY_GLASS_STYLE,
  visualState,
} from "./CompatibilityFairyCanvas";

const base = {
  workState: "idle" as const,
  hovered: false,
  listening: false,
  speaking: false,
  sleeping: false,
  dragging: false,
};

describe("FairyCanvas visual state", () => {
  it("keeps the compatibility shell transparent with restrained spectral rims", () => {
    expect(COMPATIBILITY_GLASS_STYLE.center_alpha).toBeGreaterThanOrEqual(0.05);
    expect(COMPATIBILITY_GLASS_STYLE.center_alpha).toBeLessThanOrEqual(0.11);
    expect(COMPATIBILITY_GLASS_STYLE.rim_alpha).toBeGreaterThanOrEqual(0.2);
    expect(COMPATIBILITY_GLASS_STYLE.rim_alpha).toBeLessThanOrEqual(0.31);
    expect(COMPATIBILITY_GLASS_STYLE.warm_rim).not.toBe(
      COMPATIBILITY_GLASS_STYLE.cool_rim,
    );
  });

  it("shows booting before any work state", () => {
    expect(visualState(base, 1_599)).toBe("booting");
  });

  it("prioritizes direct interaction over projected work", () => {
    expect(visualState({ ...base, workState: "tool", dragging: true }, 2_000)).toBe("dragging");
    expect(visualState({ ...base, workState: "tool", speaking: true }, 2_000)).toBe("speaking");
    expect(visualState({ ...base, workState: "tool", listening: true }, 2_000)).toBe("listening");
  });

  it("maps ambient hover and sleep without hiding durable work", () => {
    expect(visualState({ ...base, hovered: true }, 2_000)).toBe("hover");
    expect(visualState({ ...base, sleeping: true }, 2_000)).toBe("sleeping");
    expect(visualState({ ...base, workState: "awaiting_confirmation", sleeping: true }, 2_000)).toBe("awaiting_confirmation");
  });
});
