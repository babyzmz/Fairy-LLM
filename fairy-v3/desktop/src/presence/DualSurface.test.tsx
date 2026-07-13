import "@testing-library/jest-dom/vitest";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DesktopPreferences } from "../settings/client";
import type { PresenceInteractionSnapshot } from "./domain/interaction";
import type { PresenceProjectionState } from "./domain/projection";
import type { PetHost } from "./host/petHost";
import type { StorageLike } from "./host/persistence";
import { PresenceInputApp } from "./input/PresenceInputApp";
import { PresenceRenderApp } from "./render/PresenceRenderApp";
import type { PresenceChannel } from "./transport/presenceChannel";
import type { PresenceInteractionSource } from "./transport/interactionEvents";

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
    setInputInteractive: vi.fn(async () => undefined),
    requestInputFocus: vi.fn(async () => undefined),
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

function interactionHarness() {
  let listener: ((snapshot: PresenceInteractionSnapshot) => void) | null = null;
  const source: PresenceInteractionSource = {
    async subscribe(next) {
      listener = next;
      return () => {
        listener = null;
      };
    },
  };
  return {
    source,
    emit(snapshot: PresenceInteractionSnapshot) {
      listener?.(snapshot);
    },
  };
}

function interaction(sequence: number): PresenceInteractionSnapshot {
  return {
    schema_version: 1,
    sequence,
    sampled_at_ms: sequence * 16,
    phase: "aware",
    phase_started_at_ms: sequence * 16,
    reduced_motion: false,
    cursor: {
      point: { x: 120, y: 160 },
      direction: { x: 0.624695, y: 0.780869 },
      distance_px: 40,
      speed_px_s: 240,
      dwell_ms: 32,
      band: "active",
    },
    placement: {
      anchor: { x: 96, y: 130 },
      render_frame: { x: 0, y: 0, width: 640, height: 260 },
      input_compact_frame: { x: 0, y: 0, width: 372, height: 72 },
      input_expanded_frame: { x: 0, y: 0, width: 420, height: 360 },
      monitor_work_area: { x: 0, y: 0, width: 1920, height: 1040 },
      scale_factor: 1,
      expansion_direction: "left",
    },
  };
}

const storage: StorageLike = {
  getItem: () => null,
  setItem: () => undefined,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("dual presence surfaces", () => {
  it("keeps the render surface projection-only and ignores stale native snapshots", async () => {
    const harness = channelHarness();
    const coordinator = interactionHarness();
    render(
      <PresenceRenderApp
        channel={harness.channel}
        interactionSource={coordinator.source}
        now={() => Date.now()}
      />,
    );

    expect(screen.getByTestId("presence-render-surface")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(
      screen.getByTestId("presence-renderer").querySelector("canvas.presence-webgl-canvas"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(harness.channel.requestProjection).toHaveBeenCalledOnce();

    await waitFor(() => expect(coordinator.source).toBeDefined());
    act(() => coordinator.emit(interaction(3)));
    expect(screen.getByTestId("presence-render-surface")).toHaveAttribute(
      "data-cursor-band",
      "active",
    );
    act(() => coordinator.emit({
      ...interaction(2),
      cursor: { ...interaction(2).cursor, band: "outside" },
    }));
    expect(screen.getByTestId("presence-render-surface")).toHaveAttribute(
      "data-cursor-band",
      "active",
    );
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

  it("gates hover reveal at 300ms, content at 430ms, and clicks at 520ms", async () => {
    const channel = channelHarness();
    const host = hostHarness();
    const coordinator = interactionHarness();
    render(
      <PresenceInputApp
        channel={channel.channel}
        host={host.host}
        interactionSource={coordinator.source}
        now={() => Date.now()}
        storage={storage}
      />,
    );
    const surface = screen.getByTestId("presence-input-surface");
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("hidden"));

    act(() => coordinator.emit(interactionAt(20, "input_reveal", 300, 300)));
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("compact"));
    await waitFor(() => expect(host.host.setInputInteractive).toHaveBeenLastCalledWith(false));
    expect(surface).toHaveAttribute("data-content-visible", "false");
    const input = screen.getByLabelText("Quick message to Fairy");
    expect(input).not.toHaveFocus();

    act(() => coordinator.emit(interactionAt(21, "input_reveal", 430, 300)));
    expect(surface).toHaveAttribute("data-content-visible", "true");
    expect(surface).toHaveAttribute("data-interactive", "false");
    expect(host.host.requestInputFocus).not.toHaveBeenCalled();

    act(() => coordinator.emit(interactionAt(22, "interactive", 520, 520)));
    await waitFor(() => expect(host.host.setInputInteractive).toHaveBeenLastCalledWith(true));
    expect(surface).toHaveAttribute("data-interactive", "true");
    expect(input).not.toHaveFocus();
    fireEvent.pointerDown(input);
    expect(host.host.requestInputFocus).toHaveBeenCalledOnce();
  });

  it("keeps Shift+Enter and Chinese IME confirmation from submitting", async () => {
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
    const input = await screen.findByLabelText("Quick message to Fairy");
    fireEvent.change(input, { target: { value: "\u4f60\u597d Fairy" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(channel.channel.requestChatSend).not.toHaveBeenCalled();

    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    expect(channel.channel.requestChatSend).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(channel.channel.requestChatSend).toHaveBeenCalledOnce();
    expect(channel.channel.requestChatSend).toHaveBeenCalledWith("\u4f60\u597d Fairy");
  });
});

function interactionAt(
  sequence: number,
  phase: PresenceInteractionSnapshot["phase"],
  sampled_at_ms: number,
  phase_started_at_ms: number,
): PresenceInteractionSnapshot {
  return {
    ...interaction(sequence),
    sequence,
    phase,
    sampled_at_ms,
    phase_started_at_ms,
  };
}
