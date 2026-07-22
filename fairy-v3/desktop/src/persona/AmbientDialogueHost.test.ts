import { describe, expect, it } from "vitest";

import {
  ambientStateChanged,
  buildAmbientContext,
  resolveAmbientLocale,
} from "./AmbientDialogueHost";

describe("AmbientDialogueHost", () => {
  it("builds only the approved compressed device facts", () => {
    const context = buildAmbientContext({
      facts: {
        user_idle_seconds: 901,
        battery_percent: 18,
        charging: false,
        foreground_fullscreen: true,
        locked: false,
        microphone_active: false,
        do_not_disturb: true,
      },
      observedAt: new Date("2026-07-23T09:00:00Z"),
      locale: "en",
      startupEligible: false,
      userReturned: false,
      networkRestored: false,
      chargingStarted: false,
      inputOpen: false,
      microphoneActive: false,
      realtimeActive: false,
      activeTurn: false,
      approvalWaiting: false,
      severeError: false,
      ttsActive: false,
      doNotDisturb: false,
    });

    expect(context).toMatchObject({
      user_idle_seconds: 901,
      battery_percent: 18,
      fullscreen: true,
      typing: false,
      do_not_disturb: true,
    });
    expect(context).not.toHaveProperty("window_title");
    expect(context).not.toHaveProperty("screen_content");
    expect(context).not.toHaveProperty("clipboard");
  });

  it("treats recent device input as a suppression fact", () => {
    const context = buildAmbientContext({
      facts: {
        user_idle_seconds: 1,
        battery_percent: null,
        charging: null,
        foreground_fullscreen: false,
        locked: false,
        microphone_active: false,
        do_not_disturb: false,
      },
      observedAt: new Date("2026-07-23T09:00:00Z"),
      locale: "en",
      startupEligible: true,
      userReturned: false,
      networkRestored: false,
      chargingStarted: false,
      inputOpen: false,
      microphoneActive: false,
      realtimeActive: false,
      activeTurn: false,
      approvalWaiting: false,
      severeError: false,
      ttsActive: false,
      doNotDisturb: false,
    });

    expect(context.typing).toBe(true);
  });

  it("does not persist an unchanged cooldown projection", () => {
    const state = {
      local_date: "2026-07-23",
      startup_date: null,
      daily_text_count: 0,
      daily_voice_count: 0,
      daily_generated_count: 0,
      last_global_at: null,
      category_last_at: { idle: "2026-07-23T08:00:00Z" },
      line_last_at: {},
      returned_last_at: null,
      generated_digests: [],
    };

    expect(ambientStateChanged(state, state)).toBe(false);
    expect(ambientStateChanged(state, { ...state, daily_text_count: 1 })).toBe(true);
  });

  it("resolves the system locale to the two reviewed catalogs", () => {
    expect(resolveAmbientLocale("zh-CN")).toBe("zh-CN");
    expect(resolveAmbientLocale("en")).toBe("en");
    expect(["zh-CN", "en"]).toContain(resolveAmbientLocale("system"));
  });
});
