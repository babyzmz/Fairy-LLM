import { describe, expect, it } from "vitest";

import { resolvePresenceAccessibilityPreferences } from "./usePresenceAccessibility";

describe("presence accessibility preferences", () => {
  it("keeps every adaptation disabled by default", () => {
    expect(resolvePresenceAccessibilityPreferences({
      reduced_motion: false,
      reduced_transparency: false,
      increased_contrast: false,
      forced_colors: false,
    })).toEqual({
      reduced_motion: false,
      reduced_transparency: false,
      increased_contrast: false,
    });
  });

  it("treats Windows forced colors as increased contrast", () => {
    expect(resolvePresenceAccessibilityPreferences({
      reduced_motion: true,
      reduced_transparency: true,
      increased_contrast: false,
      forced_colors: true,
    })).toEqual({
      reduced_motion: true,
      reduced_transparency: true,
      increased_contrast: true,
    });
  });
});
