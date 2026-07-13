import "@testing-library/jest-dom/vitest";

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DesktopPreferences } from "../settings/client";
import type { PresenceProjectionState } from "./domain/projection";
import type { PetHost } from "./host/petHost";
import type { StorageLike } from "./host/persistence";
import { PresenceInputApp } from "./input/PresenceInputApp";
import { PresenceRenderApp } from "./render/PresenceRenderApp";
import type { PresenceChannel } from "./transport/presenceChannel";

function channelHarness() {
  let listener: ((state: PresenceProjectionState) => void) | null = null;
  const channel: PresenceChannel = {
    publishProjection: vi.fn(),
    requestProjection: vi.fn(),
    requestWorkspaceOpen: vi.fn(),
    requestNewChat: vi.fn(),
    requestChatSend: vi.fn(),
    requestVoiceStop: vi.fn(),
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

function projection(overrides: Partial<PresenceProjectionState> = {}): PresenceProjectionState {
  return {
    activity: "ambient",
    work_state: "idle",
    status_text: "Standing by",
    last_cursor: 0,
    last_event_id: null,
    updated_at_ms: Date.now(),
    recent_activity_ms: [],
    notice: null,
    reply: null,
    speaking: false,
    ...overrides,
  };
}

function preferences(): DesktopPreferences {
  return {
    schema_version: 1,
    revision: 0,
    language: "system",
    launch_at_startup: false,
    minimize_to_tray: true,
    theme: "system",
    reduced_motion: false,
    compact_density: false,
    selected_profile_id: null,
    voice_auto_play_chat: false,
    voice_auto_play_pet: true,
    voice_volume_percent: 80,
    voice_rate_percent: 100,
    permission_cloud_profile: "standard",
    memory_enabled: true,
    memory_retention_days: 90,
    analytics_enabled: false,
    pet_enabled: true,
    pet_always_on_top: true,
    pet_muted: false,
    developer_mode: false,
  };
}

function hostHarness() {
  let inputListener: (() => void) | null = null;
  const host: PetHost = {
    getPreferences: vi.fn(async () => preferences()),
    updatePreferences: vi.fn(async () => preferences()),
    onPreferences: vi.fn(async () => () => undefined),
    onInputRequested: vi.fn(async (listener) => {
      inputListener = listener;
      return () => {
        inputListener = null;
      };
    }),
    setExpanded: vi.fn(async () => undefined),
    setInputLayout: vi.fn(async () => undefined),
    openMain: vi.fn(async () => undefined),
    openSettings: vi.fn(async () => undefined),
    exit: vi.fn(async () => undefined),
  };
  return {
    host,
    requestInput() {
      inputListener?.();
    },
  };
}

const storage: StorageLike = {
  getItem: () => null,
  setItem: () => undefined,
};

afterEach(cleanup);

describe("dual presence surfaces", () => {
  it("keeps the render surface projection-only", () => {
    const harness = channelHarness();
    render(<PresenceRenderApp channel={harness.channel} now={() => Date.now()} />);

    expect(screen.getByTestId("presence-render-surface")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(screen.getByRole("img", { name: "Fairy", hidden: true })).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(harness.channel.requestProjection).toHaveBeenCalledOnce();
  });

  it("shows the input surface only for an explicit input request or projected card", async () => {
    const channel = channelHarness();
    const host = hostHarness();
    render(
      <PresenceInputApp
        channel={channel.channel}
        host={host.host}
        now={() => Date.now()}
        storage={storage}
      />,
    );

    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenCalledWith("hidden"));
    act(() => host.requestInput());
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenCalledWith("compact"));
    expect(screen.getByLabelText("Quick message to Fairy")).toBeInTheDocument();

    act(() => channel.emit(projection({
      activity: "working",
      work_state: "streaming",
      status_text: "Writing the reply",
      reply: {
        id: "reply-1",
        kind: "scratch",
        streaming: true,
        text: "Streaming from the shared assistant turn",
      },
    })));
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenCalledWith("expanded"));
    expect(screen.getByText("Streaming from the shared assistant turn")).toBeInTheDocument();
    expect(screen.queryByLabelText("Fairy companion")).not.toBeInTheDocument();
  });
});
