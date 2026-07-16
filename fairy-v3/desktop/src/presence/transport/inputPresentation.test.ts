import { describe, expect, it, vi } from "vitest";

import {
  createPresenceInputPresentationChannel,
  type PresenceInputPresentation,
} from "./inputPresentation";

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
      schema_version: 1,
      sequence: 4,
      layout: "compact",
      capsule_visible: true,
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
});
