import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PresenceChannel } from "./channel";
import type { PresenceProjectionState } from "./projection";
import type { PresenceWindowPort, StorageLike } from "./persistence";

import { PresenceApp } from "./PresenceApp";

const position = { x: 12, y: 12 };
const size = { width: 180, height: 220 };
const monitor = {
  id: "primary:0:0:1920:1080",
  x: 0,
  y: 0,
  width: 1920,
  height: 1040,
  scale_factor: 1,
  is_primary: true,
};

function channelHarness() {
  let listener: ((state: PresenceProjectionState) => void) | null = null;
  const channel: PresenceChannel = {
    publishProjection: vi.fn(),
    requestProjection: vi.fn(),
    requestWorkspaceToggle: vi.fn(),
    onProjection(next) {
      listener = next;
      return () => {
        listener = null;
      };
    },
    onRequest: vi.fn(() => () => undefined),
    close: vi.fn(),
  };
  return {
    channel,
    emit(state: PresenceProjectionState) {
      listener?.(state);
    },
  };
}

function projection(
  overrides: Partial<PresenceProjectionState> = {},
): PresenceProjectionState {
  return {
    activity: "ready",
    status_text: "Ready for review",
    last_cursor: 4,
    last_event_id: "event-4",
    updated_at_ms: Date.parse("2026-07-11T08:00:00.000Z"),
    recent_activity_ms: [Date.parse("2026-07-11T08:00:00.000Z")],
    notice: {
      id: "notice-4",
      tone: "info",
      text: "Preview is ready",
    },
    reply: { id: "reply-4", text: "The task update is ready" },
    ...overrides,
  };
}

function windowPort(): PresenceWindowPort {
  return {
    monitors: vi.fn(async () => [monitor]),
    position: vi.fn(async () => position),
    size: vi.fn(async () => size),
    setPosition: vi.fn(async () => undefined),
    startDragging: vi.fn(async () => undefined),
    onMoved: vi.fn(async () => () => undefined),
  };
}

function memoryStorage(initial?: string): StorageLike {
  let value = initial ?? null;
  return {
    getItem: () => value,
    setItem: (_key, next) => {
      value = next;
    },
  };
}

afterEach(cleanup);

describe("PresenceApp", () => {
  it("reveals local controls on hover and starts native drag at five pixels", async () => {
    const user = userEvent.setup();
    const harness = channelHarness();
    const port = windowPort();
    render(
      <PresenceApp
        channel={harness.channel}
        storage={memoryStorage()}
        windowPort={port}
      />,
    );

    const surface = screen.getByTestId("presence-surface");
    expect(screen.queryByRole("toolbar", { name: "Presence controls" })).toBeNull();
    await user.hover(surface);
    expect(screen.getByRole("toolbar", { name: "Presence controls" })).toBeVisible();

    fireEvent.pointerDown(surface, { clientX: 10, clientY: 10 });
    fireEvent.pointerMove(surface, { clientX: 13, clientY: 13 });
    expect(port.startDragging).not.toHaveBeenCalled();
    fireEvent.pointerMove(surface, { clientX: 15, clientY: 10 });
    expect(port.startDragging).toHaveBeenCalledTimes(1);
  });

  it("supports quiet mode, dismissible notices, closable replies, and scale", async () => {
    const user = userEvent.setup();
    const harness = channelHarness();
    const storage = memoryStorage();
    render(
      <PresenceApp
        channel={harness.channel}
        storage={storage}
        windowPort={windowPort()}
        now={() => Date.parse("2026-07-11T08:00:01.000Z")}
      />,
    );

    harness.emit(projection());
    expect(await screen.findByRole("alert")).toHaveTextContent("Preview is ready");
    expect(screen.getByRole("status")).toHaveTextContent("The task update is ready");

    await user.click(screen.getByRole("button", { name: "Close reply" }));
    expect(screen.queryByText("The task update is ready")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Dismiss notice" }));
    expect(screen.queryByText("Preview is ready")).toBeNull();

    await user.hover(screen.getByTestId("presence-surface"));
    fireEvent.click(screen.getByRole("button", { name: "Enable quiet mode" }));
    expect(JSON.parse(storage.getItem("settings") ?? "{}").quiet_mode).toBe(true);
    expect(screen.getByTestId("presence-surface")).toHaveAttribute(
      "data-quiet",
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: "Increase Presence scale" }));
    expect(screen.getByTestId("presence-surface")).toHaveStyle({
      "--presence-scale": "1.1",
    });
  });

  it("applies the reduced-motion override without changing domain state", () => {
    const harness = channelHarness();
    render(
      <PresenceApp
        channel={harness.channel}
        storage={memoryStorage(
          JSON.stringify({
            positions: {},
            last_monitor_id: null,
            scale: 1,
            quiet_mode: false,
            dismissed_notice_ids: [],
            reduced_motion_override: "reduce",
          }),
        )}
        windowPort={windowPort()}
      />,
    );

    expect(screen.getByTestId("presence-surface")).toHaveAttribute(
      "data-reduced-motion",
      "true",
    );
    fireEvent.mouseEnter(screen.getByTestId("presence-surface"));
    fireEvent.click(screen.getByRole("button", { name: "Motion preference: reduce" }));
    expect(screen.getByTestId("presence-surface")).toHaveAttribute(
      "data-reduced-motion",
      "false",
    );
  });
});
