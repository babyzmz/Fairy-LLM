import { describe, expect, it, vi } from "vitest";

import {
  createPresenceInputPresentationChannel,
  isNewerInputPresentation,
  type PresenceInputPresentation,
} from "./inputPresentation";
import { DEFAULT_FAIRY_MOTION_SNAPSHOT } from "../domain/motionState";

describe("Presence input presentation channel", () => {
  it("accepts only bounded presentation snapshots", () => {
    const port = {
      onmessage: null as ((event: MessageEvent<unknown>) => void) | null,
      postMessage: vi.fn(),
      close: vi.fn(),
    };
    const channel = createPresenceInputPresentationChannel(() => port);
    const listener = vi.fn();
    channel.onPresentation(listener);

    const presentation: PresenceInputPresentation = {
      schema_version: 5,
      session_id: 7,
      sequence: 4,
      layout: "compact",
      capsule_visible: true,
      capsule_width: 320,
      capsule_height: 84,
      motion: {
        ...DEFAULT_FAIRY_MOTION_SNAPSHOT,
        revision: 2,
        state: "input",
        surface: "input",
        capsule_visible: true,
      },
    };
    channel.publish(presentation);
    expect(port.postMessage).toHaveBeenCalledWith({
      kind: "input-presentation.snapshot",
      presentation,
    });

    port.onmessage?.({
      data: { kind: "input-presentation.snapshot", presentation },
    } as MessageEvent<unknown>);
    port.onmessage?.({
      data: {
        kind: "input-presentation.snapshot",
        presentation: { ...presentation, transcript: "secret" },
      },
    } as MessageEvent<unknown>);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledWith(presentation);
  });

  it("accepts a newer native session even when its local sequence restarts", () => {
    const current: PresenceInputPresentation = {
      schema_version: 5,
      session_id: 7,
      sequence: 80,
      layout: "compact",
      capsule_visible: true,
      capsule_width: 320,
      capsule_height: 64,
      motion: DEFAULT_FAIRY_MOTION_SNAPSHOT,
    };

    expect(isNewerInputPresentation({
      ...current,
      session_id: 8,
      sequence: 1,
    }, current)).toBe(true);
    expect(isNewerInputPresentation({
      ...current,
      sequence: 79,
    }, current)).toBe(false);
  });
});
