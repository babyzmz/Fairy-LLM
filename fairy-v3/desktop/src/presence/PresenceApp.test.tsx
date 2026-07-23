import "@testing-library/jest-dom/vitest";

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DesktopPreferences } from "../settings/client";
import type { PresenceChannel } from "./channel";
import type { PetHost } from "./petHost";
import type { PresenceWindowPort, StorageLike } from "./persistence";
import type { PresenceProjectionState } from "./projection";
import { PresenceApp } from "./PresenceApp";

const NOW = Date.parse("2026-07-11T08:00:01.000Z");
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
    publishSubmission: vi.fn(),
    requestProjection: vi.fn(),
    requestWorkspaceOpen: vi.fn(),
    requestNewChat: vi.fn(),
    requestChatSend: vi.fn(),
    requestChatCancel: vi.fn(),
    requestVoiceStop: vi.fn(),
    onProjection(next) {
      listener = next;
      return () => {
        listener = null;
      };
    },
    onSubmission: vi.fn(() => () => undefined),
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
    activity: "ready",
    work_state: "ready",
    status_text: "Ready for review",
    last_cursor: 4,
    last_event_id: "event-4",
    updated_at_ms: Date.parse("2026-07-11T08:00:00.000Z"),
    recent_activity_ms: [Date.parse("2026-07-11T08:00:00.000Z")],
    notice: null,
    reply: null,
    ambient_dialogue: null,
    speaking: false,
    ...overrides,
  };
}

function windowPort(): PresenceWindowPort {
  return {
    monitors: vi.fn(async () => [monitor]),
    position: vi.fn(async () => ({ x: 12, y: 12 })),
    size: vi.fn(async () => ({ width: 176, height: 176 })),
    setPosition: vi.fn(async () => undefined),
    startDragging: vi.fn(async () => undefined),
    onMoved: vi.fn(async () => () => undefined),
  };
}

function preferences(): DesktopPreferences {
  return {
    schema_version: 3,
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
    analytics_enabled: false,
    realtime_provider: "auto",
    realtime_voice_mode: "native",
    realtime_game_audio_default: false,
    realtime_memory_enabled: true,
    realtime_max_session_minutes: 30,
    trash_auto_purge_30_days: false,
    pet_enabled: true,
    pet_always_on_top: true,
    pet_muted: false,
    pet_size_percent: 100,
    pet_opacity_percent: 92,
    pet_motion_enabled: true,
    pet_particles_enabled: true,
    pet_hover_enabled: true,
    pet_hover_dwell_ms: 250,
    pet_do_not_disturb: false,
    ambient_dialogue_enabled: true,
    ambient_dialogue_voice_enabled: false,
    ambient_generated_dialogue_enabled: false,
    pet_remember_position: true,
    pet_renderer_mode: "auto",
    pet_optics_mode: "standard",
    pet_activation_style: "fluid_response",
    pet_target_fps: 60,
    pet_anchor: null,
    developer_mode: false,
  };
}

function petHost(): PetHost {
  let current = preferences();
  return {
    getPreferences: vi.fn(async () => current),
    updatePreferences: vi.fn(async (input) => {
      current = { ...current, ...input, revision: current.revision + 1 };
      return current;
    }),
    onPreferences: vi.fn(async () => () => undefined),
    onInputRequested: vi.fn(async () => () => undefined),
    onInputToggleRequested: vi.fn(async () => () => undefined),
    onInputCloseRequested: vi.fn(async () => () => undefined),
    onMenuRequested: vi.fn(async () => () => undefined),
    onNewChatRequested: vi.fn(async () => () => undefined),
    setExpanded: vi.fn(async () => undefined),
    setInputLayout: vi.fn(async () => undefined),
    setInputInteractive: vi.fn(async () => undefined),
    setInputOpenIntent: vi.fn(async () => 1),
    requestInputFocus: vi.fn(async () => undefined),
    beginInputPresentationSession: vi.fn(async () => ({ session_id: 1, revision: 0 })),
    applyInputPresentation: vi.fn(async (input) => ({
      session_id: input.session_id,
      revision: input.revision,
    })),
    resetPosition: vi.fn(async () => current),
    openMain: vi.fn(async () => undefined),
    openSettings: vi.fn(async () => undefined),
    exit: vi.fn(async () => undefined),
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

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("PresenceApp", () => {
  it("opens quick chat on click and the workspace on double click", () => {
    vi.useFakeTimers();
    const harness = channelHarness();
    const host = petHost();
    render(
      <PresenceApp
        channel={harness.channel}
        host={host}
        storage={memoryStorage()}
        windowPort={windowPort()}
      />,
    );

    const core = screen.getByRole("button", { name: "Fairy companion" });
    fireEvent.click(core);
    act(() => vi.advanceTimersByTime(220));
    expect(screen.getByRole("textbox", { name: "Quick message to Fairy" })).toBeVisible();
    fireEvent.doubleClick(core);
    expect(harness.channel.requestWorkspaceOpen).toHaveBeenCalledOnce();
    expect(host.openMain).toHaveBeenCalledOnce();
  });

  it("starts native dragging only after five pixels", () => {
    const port = windowPort();
    render(
      <PresenceApp
        channel={channelHarness().channel}
        host={petHost()}
        storage={memoryStorage()}
        windowPort={port}
      />,
    );
    const surface = screen.getByTestId("presence-surface");
    fireEvent.pointerDown(surface, { button: 0, clientX: 10, clientY: 10 });
    fireEvent.pointerMove(surface, { clientX: 13, clientY: 13 });
    expect(port.startDragging).not.toHaveBeenCalled();
    fireEvent.pointerMove(surface, { clientX: 15, clientY: 10 });
    expect(port.startDragging).toHaveBeenCalledOnce();
  });

  it("renders replies while routing approvals through the Fairy core", async () => {
    const user = userEvent.setup();
    const harness = channelHarness();
    const host = petHost();
    render(
      <PresenceApp
        channel={harness.channel}
        host={host}
        now={() => NOW}
        storage={memoryStorage()}
        windowPort={windowPort()}
      />,
    );

    act(() => harness.emit(projection({
      reply: { id: "reply-4", text: "A streamed answer", kind: "scratch", streaming: true },
    })));
    expect(screen.getByRole("status")).toHaveTextContent("A streamed answer");
    await user.click(screen.getByRole("button", { name: "Close reply" }));
    expect(screen.queryByText("A streamed answer")).toBeNull();

    act(() => harness.emit(projection({
      activity: "needs_attention",
      work_state: "awaiting_confirmation",
      status_text: "Waiting for your decision",
      notice: { id: "notice-4", tone: "critical", text: "An approval needs your decision" },
    })));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByTestId("presence-surface")).toHaveAttribute("data-expanded", "false");
    await user.click(screen.getByRole("button", { name: "Fairy companion" }));
    expect(harness.channel.requestWorkspaceOpen).toHaveBeenCalledOnce();
    expect(host.openMain).toHaveBeenCalledOnce();
  });

  it("does not submit while a Chinese IME composition is active", () => {
    vi.useFakeTimers();
    const harness = channelHarness();
    render(
      <PresenceApp
        channel={harness.channel}
        host={petHost()}
        storage={memoryStorage()}
        windowPort={windowPort()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Fairy companion" }));
    act(() => vi.advanceTimersByTime(220));
    const input = screen.getByRole("textbox", { name: "Quick message to Fairy" });
    fireEvent.change(input, { target: { value: "你好 Fairy" } });
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(harness.channel.requestChatSend).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(harness.channel.requestChatSend).toHaveBeenCalledWith(
      "你好 Fairy",
      expect.any(String),
    );
  });

  it("applies the reduced-motion override", () => {
    render(
      <PresenceApp
        channel={channelHarness().channel}
        host={petHost()}
        storage={memoryStorage(JSON.stringify({
          positions: {},
          last_monitor_id: null,
          scale: 1,
          quiet_mode: false,
          dismissed_notice_ids: [],
          reduced_motion_override: "reduce",
        }))}
        windowPort={windowPort()}
      />,
    );
    expect(screen.getByTestId("presence-surface")).toHaveAttribute(
      "data-reduced-motion",
      "true",
    );
  });
});
