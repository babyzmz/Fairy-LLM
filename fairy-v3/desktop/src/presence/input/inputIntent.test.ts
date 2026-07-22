import { describe, expect, it } from "vitest";

import {
  INITIAL_PRESENCE_INPUT_INTENT,
  reducePresenceInputIntent,
} from "./inputIntent";

describe("presence input intent", () => {
  it("pins a closed input and closes a pinned input on consecutive taps", () => {
    const opened = reducePresenceInputIntent(INITIAL_PRESENCE_INPUT_INTENT, {
      type: "toggle",
      transient_visible: false,
    });
    expect(opened).toEqual({ pinned: true, hover_blocked_until_exit: false });

    expect(reducePresenceInputIntent(opened, {
      type: "toggle",
      transient_visible: true,
    })).toEqual({ pinned: false, hover_blocked_until_exit: true });
  });

  it("lets a tap close transient hover without immediately reopening it", () => {
    const closed = reducePresenceInputIntent(INITIAL_PRESENCE_INPUT_INTENT, {
      type: "toggle",
      transient_visible: true,
    });
    expect(closed).toEqual({ pinned: false, hover_blocked_until_exit: true });
    expect(reducePresenceInputIntent(closed, { type: "pointer_exited" }))
      .toEqual(INITIAL_PRESENCE_INPUT_INTENT);
  });

  it("suppresses transient hover across a drag while preserving explicit input", () => {
    const hoverDrag = reducePresenceInputIntent(INITIAL_PRESENCE_INPUT_INTENT, {
      type: "drag_started",
    });
    expect(hoverDrag).toEqual({ pinned: false, hover_blocked_until_exit: true });

    const pinned = reducePresenceInputIntent(INITIAL_PRESENCE_INPUT_INTENT, { type: "open" });
    expect(reducePresenceInputIntent(pinned, { type: "drag_started" }))
      .toEqual({ pinned: true, hover_blocked_until_exit: true });
  });

  it("promotes an interacted hover input to explicit ownership", () => {
    const blocked = reducePresenceInputIntent(INITIAL_PRESENCE_INPUT_INTENT, {
      type: "close",
    });
    expect(reducePresenceInputIntent(blocked, { type: "engage" }))
      .toEqual({ pinned: true, hover_blocked_until_exit: false });
  });
});
