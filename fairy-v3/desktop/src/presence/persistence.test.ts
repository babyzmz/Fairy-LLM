import { describe, expect, it } from "vitest";

import {
  defaultPresenceSettings,
  loadPresenceSettings,
  rememberMonitorPosition,
  resolveReducedMotion,
  restoreMonitorPosition,
  snapToMonitorEdges,
} from "./persistence";

const primary = {
  id: "primary:0:0:1920:1080",
  x: 0,
  y: 0,
  width: 1920,
  height: 1040,
  scale_factor: 1,
  is_primary: true,
};
const secondary = {
  id: "secondary:1920:0:2560:1440",
  x: 1920,
  y: 0,
  width: 2560,
  height: 1400,
  scale_factor: 1.25,
  is_primary: false,
};

describe("Presence persistence", () => {
  it("rejects stored domain or model context instead of retaining it", () => {
    const storage = {
      getItem: () =>
        JSON.stringify({
          ...defaultPresenceSettings(),
          project_id: "must-not-persist",
          model_context: { messages: ["secret"] },
        }),
      setItem: () => undefined,
    };

    const loaded = loadPresenceSettings(storage);
    expect(loaded).toEqual(defaultPresenceSettings());
    expect(JSON.stringify(loaded)).not.toContain("project_id");
    expect(JSON.stringify(loaded)).not.toContain("secret");
  });

  it("snaps within 20 physical pixels and leaves other positions unchanged", () => {
    expect(
      snapToMonitorEdges({ x: 18, y: 22 }, { width: 180, height: 220 }, primary),
    ).toEqual({ x: 0, y: 22 });
    expect(
      snapToMonitorEdges(
        { x: 1920 - 180 - 19, y: 1040 - 220 - 20 },
        { width: 180, height: 220 },
        primary,
      ),
    ).toEqual({ x: 1740, y: 820 });
    expect(
      snapToMonitorEdges({ x: 50, y: 50 }, { width: 180, height: 220 }, primary),
    ).toEqual({ x: 50, y: 50 });
  });

  it("restores a monitor-relative position after scale or resolution changes", () => {
    const settings = rememberMonitorPosition(
      defaultPresenceSettings(),
      secondary,
      { x: 3000, y: 400 },
      { width: 180, height: 220 },
    );
    const resized = { ...secondary, width: 3200, height: 1760 };

    const restored = restoreMonitorPosition(
      settings,
      [primary, resized],
      { width: 180, height: 220 },
    );

    expect(restored.monitor.id).toBe(secondary.id);
    expect(restored.position.x).toBeGreaterThan(3000);
    expect(restored.position.y).toBeGreaterThan(400);
  });

  it("honors explicit reduced-motion overrides", () => {
    expect(resolveReducedMotion("reduce", false)).toBe(true);
    expect(resolveReducedMotion("full", true)).toBe(false);
    expect(resolveReducedMotion("system", true)).toBe(true);
  });
});
