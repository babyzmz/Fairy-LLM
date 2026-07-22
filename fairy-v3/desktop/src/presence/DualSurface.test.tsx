import "@testing-library/jest-dom/vitest";

import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DesktopPreferences } from "../settings/client";
import type { PresenceInteractionSnapshot } from "./domain/interaction";
import type { PresenceProjectionState } from "./domain/projection";
import type { PetHost } from "./host/petHost";
import type { StorageLike } from "./host/persistence";
import { PresenceInputApp } from "./input/PresenceInputApp";
import { NativePresenceRendererHost } from "./render/NativePresenceRendererHost";
import {
  nativeRendererOccludesFallback,
  PresenceRenderApp,
} from "./render/PresenceRenderApp";
import type {
  PresenceChannel,
  PresenceSubmissionUpdate,
} from "./transport/presenceChannel";
import type { PresenceInteractionSource } from "./transport/interactionEvents";
import type {
  NativeRendererLifecycleSignal,
  NativeRendererLifecycleSource,
} from "./transport/nativeRendererLifecycle";
import type {
  PresenceInputPresentation,
  PresenceInputPresentationChannel,
} from "./transport/inputPresentation";
import type { PresenceRendererHealth } from "./transport/rendererHealth";
import {
  DEFAULT_PRESENCE_RENDER_SETTINGS,
  type PresenceRenderSettings,
  type PresenceRenderSettingsChannel,
} from "./transport/renderSettings";

function channelHarness() {
  let listener: ((state: PresenceProjectionState) => void) | null = null;
  let submissionListener: ((update: PresenceSubmissionUpdate) => void) | null = null;
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
    onSubmission(next) {
      submissionListener = next;
      return () => {
        submissionListener = null;
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
    emitSubmission(update: PresenceSubmissionUpdate) {
      submissionListener?.(update);
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
    pet_remember_position: true,
    pet_renderer_mode: "auto",
    pet_optics_mode: "standard",
    pet_activation_style: "fluid_response",
    pet_target_fps: 60,
    pet_anchor: null,
    developer_mode: false,
  };
}

function hostHarness() {
  let inputListener: (() => void) | null = null;
  let inputToggleListener: (() => void) | null = null;
  const setInputLayout = vi.fn<PetHost["setInputLayout"]>(async () => undefined);
  const setInputInteractive = vi.fn<PetHost["setInputInteractive"]>(
    async () => undefined,
  );
  const requestInputFocus = vi.fn<PetHost["requestInputFocus"]>(
    async () => undefined,
  );
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
    onInputToggleRequested: vi.fn(async (listener) => {
      inputToggleListener = listener;
      return () => {
        inputToggleListener = null;
      };
    }),
    onMenuRequested: vi.fn(async () => () => undefined),
    onNewChatRequested: vi.fn(async () => () => undefined),
    setExpanded: vi.fn(async () => undefined),
    setInputLayout,
    setInputInteractive,
    requestInputFocus,
    beginInputPresentationSession: vi.fn(async () => ({ session_id: 1, revision: 0 })),
    applyInputPresentation: vi.fn(async (input) => {
      await setInputInteractive(false);
      if (input.compact_width === undefined) await setInputLayout(input.layout);
      else await setInputLayout(input.layout, input.compact_width);
      if (input.interactive) await setInputInteractive(true);
      if (input.request_focus) await requestInputFocus();
      return { session_id: input.session_id, revision: input.revision };
    }),
    resetPosition: vi.fn(async () => preferences()),
    openMain: vi.fn(async () => undefined),
    openSettings: vi.fn(async () => undefined),
    exit: vi.fn(async () => undefined),
  };
  return {
    host,
    requestInput() {
      inputListener?.();
    },
    requestInputToggle() {
      inputToggleListener?.();
    },
  };
}

function expectNativeDragOnly(host: PetHost) {
  expect(host).not.toHaveProperty("beginGroupDrag");
  expect(host).not.toHaveProperty("moveGroupDrag");
  expect(host).not.toHaveProperty("endGroupDrag");
}

function interactionHarness() {
  const listeners = new Set<(snapshot: PresenceInteractionSnapshot) => void>();
  const source: PresenceInteractionSource = {
    async subscribe(next) {
      listeners.add(next);
      return () => {
        listeners.delete(next);
      };
    },
  };
  return {
    source,
    emit(snapshot: PresenceInteractionSnapshot) {
      for (const listener of listeners) listener(snapshot);
    },
  };
}

function renderSettingsHarness() {
  let listener: ((settings: PresenceRenderSettings) => void) | null = null;
  const channel: PresenceRenderSettingsChannel = {
    publish: vi.fn(),
    request: vi.fn(),
    onSettings(next) {
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
    emit(settings: PresenceRenderSettings) {
      listener?.(settings);
    },
  };
}

function nativeLifecycleHarness() {
  const listeners = new Set<(signal: NativeRendererLifecycleSignal) => void>();
  const source: NativeRendererLifecycleSource = {
    async subscribe(next) {
      listeners.add(next);
      return () => {
        listeners.delete(next);
      };
    },
  };
  return {
    source,
    emit(reason: NativeRendererLifecycleSignal["reason"]) {
      for (const listener of listeners) listener({ schema_version: 1, reason });
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
      input_compact_frame: { x: 0, y: 58, width: 616, height: 144 },
      input_expanded_frame: { x: 0, y: -158, width: 616, height: 360 },
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
  it("never draws the compatibility identity while a native surface can be visible", () => {
    expect(nativeRendererOccludesFallback(true, "idle")).toBe(false);
    expect(nativeRendererOccludesFallback(true, "starting")).toBe(false);
    expect(nativeRendererOccludesFallback(true, "running")).toBe(true);
    expect(nativeRendererOccludesFallback(false, "stopping")).toBe(true);
    expect(nativeRendererOccludesFallback(true, "fallback")).toBe(false);
    expect(nativeRendererOccludesFallback(false, "idle")).toBe(false);
    expect(nativeRendererOccludesFallback(false, "suspended")).toBe(false);
  });

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

  it("keeps fallback through native startup and unloads it after native readiness", async () => {
    const harness = channelHarness();
    const coordinator = interactionHarness();
    const settings = renderSettingsHarness();
    const lifecycle = nativeLifecycleHarness();
    const rendererHealth = vi.fn(async (_health: PresenceRendererHealth) =>
      "continue" as const
    );
    let releaseNativeStart!: () => void;
    const nativeStartGate = new Promise<void>((resolve) => {
      releaseNativeStart = resolve;
    });
    const commands: Array<{ command: string; args?: Record<string, unknown> }> = [];
    render(
      <StrictMode>
        <PresenceRenderApp
          channel={harness.channel}
          interactionSource={coordinator.source}
          renderSettingsChannel={settings.channel}
          nativeLifecycleSource={lifecycle.source}
          rendererHealthHost={{ report: rendererHealth }}
          nativeRendererHostFactory={(options) => new NativePresenceRendererHost({
            ...options,
            watchdogIntervalMs: 60_000,
            invokeCommand: async (command, args) => {
              commands.push({ command, args });
              if (command === "pet_native_gpu_start") await nativeStartGate;
              return {
                backend: "windows_dda_d3d11_composition",
                optics_source: "desktop_duplication",
                lifecycle: "running",
                host_backdrop_composition: true,
                backdrop_pixel_access: true,
                continuous_displacement_supported: true,
                pixel_ipc: false,
                hdr_composition: false,
                dda_exclusion: "applied",
                source_format: "bgra8",
                adapter_luid: "00000000:00000001",
                source_frame_age_ms: 1,
                capture_to_present_p95_ms: 2,
                access_lost_count: 0,
                monitor_handoff: "ready",
                target_frame_rate: 144,
                effective_frame_rate: 144,
                display_refresh_rate_hz: 144,
                frames_presented: 2,
                present_fps_avg: 144,
                frame_interval_p1_fps: 120,
                present_p95_ms: 0.2,
                surface_width: 640,
                surface_height: 260,
                target_x: 0,
                target_y: 0,
                monitor_x: 0,
                monitor_y: 0,
                monitor_width: 1920,
                monitor_height: 1080,
                monitor_handle: "0x0000000000000001",
                monitor_device_name: "\\\\.\\DISPLAY1",
                monitor_friendly_name: "Test display",
                adapter_name: "Test adapter",
                adapter_index: 0,
                output_device_name: "\\\\.\\DISPLAY1",
                output_index: 0,
                hdr_color_space: "0",
                composition_stage: "desktop_duplication_ready",
                composition_hresult: null,
                started_at_ms: 1,
                last_presented_at_ms: 2,
                presentation_revision: 0,
                visual_sequence: 0,
                error_code: null,
              };
            },
          })}
          now={() => Date.now()}
        />
      </StrictMode>,
    );

    act(() => settings.emit({
      ...DEFAULT_PRESENCE_RENDER_SETTINGS,
      optics_mode: "enhanced",
      target_frame_rate: 144,
    }));
    await waitFor(() => expect(screen.getByTestId("presence-render-surface")).toHaveAttribute(
      "data-interaction-ready",
      "true",
    ));
    expect(commands.some((entry) => entry.command === "pet_native_gpu_start")).toBe(false);

    act(() => coordinator.emit(interaction(1)));

    await waitFor(() => {
      expect(commands.some((entry) => entry.command === "pet_native_gpu_start")).toBe(true);
    });
    expect(screen.getByTestId("presence-renderer")).toBeInTheDocument();
    act(() => releaseNativeStart());

    await waitFor(() => expect(screen.getByTestId("presence-render-surface")).toHaveAttribute(
      "data-native-renderer-state",
      "running",
    ));
    expect(screen.getByTestId("presence-render-surface")).toHaveAttribute(
      "data-native-renderer-requested",
      "true",
    );
    expect(screen.queryByTestId("presence-renderer")).not.toBeInTheDocument();
    expect(rendererHealth.mock.calls.at(-1)?.[0]).toEqual(expect.objectContaining({
      actual_backend: "native_liquid_glass",
      optics_source: "desktop_duplication",
      status: "running",
    }));
    const start = commands.find((entry) => entry.command === "pet_native_gpu_start");
    expect(start?.args).toEqual({
      request: expect.objectContaining({
        target_frame_rate: 144,
        frame_rate_limit: 60,
      }),
    });
    expect(JSON.stringify(start?.args)).not.toMatch(/rgba|pixel|data_url|backdrop/i);

    const startCount = () => commands.filter(
      (entry) => entry.command === "pet_native_gpu_start",
    ).length;
    const stopCount = () => commands.filter(
      (entry) => entry.command === "pet_native_gpu_stop",
    ).length;
    const startsBeforeDrag = startCount();
    const stopsBeforeDrag = stopCount();

    act(() => lifecycle.emit("surface_changed"));
    await waitFor(() => expect(commands.filter(
      (entry) => entry.command === "pet_native_gpu_rebind",
    )).toHaveLength(1));
    expect(startCount()).toBe(startsBeforeDrag);

    act(() => lifecycle.emit("drag_ended"));
    await act(async () => Promise.resolve());
    expect(commands.filter(
      (entry) => entry.command === "pet_native_gpu_rebind",
    )).toHaveLength(1);
    expect(startCount()).toBe(startsBeforeDrag);
    expect(stopCount()).toBe(stopsBeforeDrag);
  });

  it("keeps only a circular idle proxy and expands for input or projected cards", async () => {
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

    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenCalledWith("core"));
    expect(host.host.applyInputPresentation).toHaveBeenCalledWith(expect.objectContaining({
      layout: "core",
      interactive: true,
      request_focus: false,
    }));
    expect(screen.getByRole("button", { name: "Open Fairy quick input" })).toBeInTheDocument();
    act(() => host.requestInput());
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenCalledWith("compact", 220));
    const textarea = screen.getByLabelText("Quick message to Fairy");
    const inputField = screen.getByTestId("presence-input-field");
    expect(inputField).toContainElement(textarea);
    expect(inputField).toHaveAttribute(
      "data-optical-layer",
      "transparent-overlay",
    );
    expect(inputField.querySelector("canvas")).toBeNull();
    const focusRequestsBeforePointer = vi.mocked(host.host.requestInputFocus).mock.calls.length;
    fireEvent.pointerDown(inputField);
    expect(textarea).toHaveFocus();
    expect(host.host.requestInputFocus).toHaveBeenCalledTimes(
      focusRequestsBeforePointer + 1,
    );

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

  it("toggles explicit quick input ownership on consecutive native core taps", async () => {
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

    await waitFor(() => expect(host.host.onInputToggleRequested).toHaveBeenCalledOnce());
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("core"));

    act(() => host.requestInputToggle());
    await waitFor(() =>
      expect(host.host.setInputLayout).toHaveBeenLastCalledWith("compact", 220)
    );
    expect(screen.getByTestId("presence-input-surface")).toHaveAttribute(
      "data-layout",
      "compact",
    );

    act(() => host.requestInputToggle());
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("core"));
    expect(screen.getByTestId("presence-input-surface")).toHaveAttribute(
      "data-layout",
      "core",
    );

    act(() => host.requestInputToggle());
    await waitFor(() =>
      expect(host.host.setInputLayout).toHaveBeenLastCalledWith("compact", 220)
    );
  });

  it("keeps hover closed after a core tap until the pointer fully exits", async () => {
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

    act(() => coordinator.emit(interactionAt(40, "input_reveal", 430, 300)));
    await waitFor(() =>
      expect(host.host.setInputLayout).toHaveBeenLastCalledWith("compact", 220)
    );

    act(() => host.requestInputToggle());
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("core"));

    act(() => coordinator.emit(interactionAt(41, "interactive", 520, 520)));
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("core"));

    act(() => coordinator.emit(interactionAt(42, "idle", 1_000, 1_000)));
    act(() => coordinator.emit(interactionAt(43, "input_reveal", 1_430, 1_300)));
    await waitFor(() =>
      expect(host.host.setInputLayout).toHaveBeenLastCalledWith("compact", 220)
    );
  });

  it("pins a transient hover input once the user starts interacting with it", async () => {
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

    act(() => coordinator.emit(interactionAt(50, "interactive", 520, 520)));
    const inputField = await screen.findByTestId("presence-input-field");
    fireEvent.pointerDown(inputField);
    act(() => coordinator.emit(interactionAt(51, "returning", 900, 900)));
    act(() => coordinator.emit(interactionAt(52, "idle", 1_100, 1_100)));

    await waitFor(() =>
      expect(host.host.setInputLayout).toHaveBeenLastCalledWith("compact", 220)
    );
    expect(screen.getByTestId("presence-input-surface")).toHaveAttribute(
      "data-layout",
      "compact",
    );
  });

  it("publishes a non-interactive core snapshot after native presentation failure", async () => {
    const channel = channelHarness();
    const host = hostHarness();
    const published: PresenceInputPresentation[] = [];
    const inputPresentationChannel: PresenceInputPresentationChannel = {
      publish: vi.fn((presentation) => published.push(presentation)),
      request: vi.fn(),
      onPresentation: vi.fn(() => () => undefined),
      onRequest: vi.fn(() => () => undefined),
      close: vi.fn(),
    };
    vi.mocked(host.host.applyInputPresentation).mockImplementation(async (input) => {
      if (input.layout === "compact") {
        throw new Error("PET_INPUT_REGION_APPLY_FAILED");
      }
      return { session_id: input.session_id, revision: input.revision };
    });

    render(
      <PresenceInputApp
        channel={channel.channel}
        host={host.host}
        inputPresentationChannel={inputPresentationChannel}
        now={() => Date.now()}
        storage={storage}
      />,
    );

    await waitFor(() => expect(host.host.applyInputPresentation).toHaveBeenCalled());
    act(() => host.requestInput());
    await waitFor(() => expect(host.host.applyInputPresentation).toHaveBeenCalledWith(
      expect.objectContaining({
        layout: "core",
        interactive: false,
        request_focus: false,
      }),
    ));
    expect(published.at(-1)).toEqual(expect.objectContaining({
      layout: "core",
      capsule_visible: false,
      motion: expect.objectContaining({
        surface: "core",
        surface_interactive: false,
        capsule_visible: false,
      }),
    }));
  });

  it("opens the companion menu from the core context target without a second input shell", async () => {
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

    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("core"));
    fireEvent.contextMenu(screen.getByRole("button", { name: "Open Fairy quick input" }));
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("expanded"));
    expect(screen.getByRole("menu", { name: "Fairy menu" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Quick message to Fairy")).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    const surface = screen.getByTestId("presence-input-surface");
    expect(surface).toHaveAttribute("data-layout", "core");
    expect(surface).toHaveAttribute("data-content-visible", "false");
    expect(surface).toHaveAttribute("data-interactive", "true");
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("core"));
    expect(screen.queryByRole("menu", { name: "Fairy menu" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open Fairy quick input" }));
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("compact", 220));
    expect(screen.getByLabelText("Quick message to Fairy")).toBeInTheDocument();
  });

  it("closes the menu after 600ms of window focus loss", async () => {
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
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("core"));
    fireEvent.contextMenu(screen.getByRole("button", { name: "Open Fairy quick input" }));
    expect(await screen.findByRole("menu", { name: "Fairy menu" })).toBeInTheDocument();

    fireEvent.blur(window);
    await waitFor(
      () => expect(screen.queryByRole("menu", { name: "Fairy menu" })).not.toBeInTheDocument(),
      { timeout: 1_000 },
    );
  });

  it("gates materialization at 300ms, content at 430ms, and clicks at 520ms", async () => {
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
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("core"));

    act(() => coordinator.emit(interactionAt(20, "input_reveal", 300, 300)));
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("compact", 220));
    await waitFor(() => expect(host.host.setInputInteractive).toHaveBeenLastCalledWith(false));
    const passiveCall = vi.mocked(host.host.setInputInteractive).mock.invocationCallOrder.at(-1);
    const layoutCall = vi.mocked(host.host.setInputLayout).mock.invocationCallOrder.at(-1);
    expect(passiveCall).toBeLessThan(layoutCall ?? 0);
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
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenCalledWith("core"));
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
    expect(channel.channel.requestChatSend).toHaveBeenCalledWith(
      "\u4f60\u597d Fairy",
      expect.any(String),
    );
  });

  it("moves from send status to one streaming reply with isolated controls", async () => {
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
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenCalledWith("core"));
    act(() => host.requestInput());
    const input = await screen.findByLabelText("Quick message to Fairy");
    fireEvent.change(input, { target: { value: "Stream this reply" } });
    fireEvent.keyDown(input, { key: "Enter" });
    const submissionId = vi.mocked(channel.channel.requestChatSend).mock.calls[0]?.[1];
    expect(submissionId).toEqual(expect.any(String));
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByTestId("presence-input-surface")).toHaveAttribute(
      "data-motion-state",
      "submitting",
    );
    expect(screen.getByTestId("presence-input-surface")).toHaveAttribute(
      "data-motion-activity",
      "model",
    );
    expect(screen.getByTestId("presence-input-surface")).toHaveAttribute(
      "data-layout",
      "core",
    );

    act(() => channel.emitSubmission({
      submission_id: submissionId ?? "missing",
      status: "accepted",
      failure: null,
    }));
    act(() => channel.emit(projection({
      activity: "working",
      work_state: "streaming",
      status_text: "Writing the reply",
      reply: {
        id: "reply-stream",
        kind: "scratch",
        streaming: true,
        text: "A single streamed reply",
      },
      speaking: true,
    })));

    expect(screen.getAllByText("A single streamed reply")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Stop reading" }));
    expect(channel.channel.requestVoiceStop).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Stop reply" }));
    expect(channel.channel.requestChatCancel).toHaveBeenCalledWith(submissionId);
    expect(screen.getByRole("button", { name: "Open reply in Fairy" })).toBeInTheDocument();
  });

  it("shows offline failure without duplicating a reply and retries the same text", async () => {
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
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenCalledWith("core"));
    act(() => host.requestInput());
    const input = await screen.findByLabelText("Quick message to Fairy");
    fireEvent.change(input, { target: { value: "Retry safely" } });
    fireEvent.keyDown(input, { key: "Enter" });
    const firstId = vi.mocked(channel.channel.requestChatSend).mock.calls[0]?.[1];
    act(() => channel.emitSubmission({
      submission_id: firstId ?? "missing",
      status: "failed",
      failure: "offline",
    }));
    expect(screen.getByRole("status")).toHaveTextContent("Fairy is offline");
    fireEvent.click(screen.getByRole("button", { name: "Retry request" }));
    expect(channel.channel.requestChatSend).toHaveBeenCalledTimes(2);
    expect(vi.mocked(channel.channel.requestChatSend).mock.calls[1]?.[0]).toBe(
      "Retry safely",
    );
    expect(vi.mocked(channel.channel.requestChatSend).mock.calls[1]?.[1]).not.toBe(firstId);
  });

  it("retracts a completed reply after five seconds without interaction", async () => {
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
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenCalledWith("core"));
    vi.useFakeTimers();
    act(() => channel.emit(projection({
      reply: {
        id: "reply-complete",
        kind: "scratch",
        streaming: false,
        text: "Finished once",
      },
    })));
    expect(screen.getByText("Finished once")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(4_999));
    expect(screen.getByText("Finished once")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByText("Finished once")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
    vi.useRealTimers();
  });

  it("routes approvals to the main window without decision controls", async () => {
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
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenCalledWith("core"));
    fireEvent.contextMenu(screen.getByRole("button", { name: "Open Fairy quick input" }));
    expect(await screen.findByRole("menu", { name: "Fairy menu" })).toBeInTheDocument();
    act(() => channel.emit(projection({
      activity: "needs_attention",
      work_state: "awaiting_confirmation",
      status_text: "Waiting for your decision",
      notice: {
        id: "approval-1",
        tone: "critical",
        text: "An approval needs your decision",
      },
    })));
    await waitFor(() => {
      expect(screen.queryByRole("menu", { name: "Fairy menu" })).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("status")).toBeNull();
    const surface = screen.getByTestId("presence-input-surface");
    expect(surface).toHaveAttribute("data-layout", "core");
    expect(surface).toHaveAttribute("data-motion-state", "awaiting_confirmation");
    expect(surface).toHaveAttribute("data-motion-activity", "approval");
    fireEvent.click(screen.getByRole("button", { name: "Open Fairy quick input" }));
    expect(channel.channel.requestWorkspaceOpen).toHaveBeenCalledOnce();
    expect(host.host.openMain).toHaveBeenCalledOnce();
  });

  it("migrates a legacy monitor-relative position without deleting local data", async () => {
    const channel = channelHarness();
    const host = hostHarness();
    const legacy = JSON.stringify({
      positions: {
        "DISPLAY-2:-1920:0:1920:1040": { x_ratio: 0.25, y_ratio: 0.75 },
      },
      last_monitor_id: "DISPLAY-2:-1920:0:1920:1040",
      scale: 1,
      quiet_mode: false,
      dismissed_notice_ids: [],
      reduced_motion_override: "system",
    });
    const legacyStorage: StorageLike = {
      getItem: vi.fn(() => legacy),
      setItem: vi.fn(),
    };
    render(
      <PresenceInputApp
        channel={channel.channel}
        host={host.host}
        now={() => Date.now()}
        storage={legacyStorage}
      />,
    );

    await waitFor(() => expect(host.host.updatePreferences).toHaveBeenCalledWith({
      expected_revision: 0,
      pet_anchor: {
        monitor_id: "DISPLAY-2:-1920:0:1920:1040",
        x_ratio: 0.25,
        y_ratio: 0.75,
      },
    }));
    expect(legacyStorage.setItem).not.toHaveBeenCalled();
  });

  it("removes the separate drag control from quick input and the pet menu", async () => {
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
    act(() => host.requestInput());
    expect(await screen.findByLabelText("Quick message to Fairy")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Move Fairy" })).toBeNull();

    fireEvent.contextMenu(screen.getByTestId("presence-input-surface"));
    expect(await screen.findByRole("menu", { name: "Fairy menu" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Move Fairy" })).toBeNull();
  });

  it("keeps a short press on the Fairy body as quick-input activation", async () => {
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
    const core = await screen.findByRole("button", { name: "Open Fairy quick input" });
    fireEvent.pointerDown(core, {
      button: 0,
      pointerId: 8,
      screenX: 520,
      screenY: 420,
    });
    fireEvent.pointerUp(core, {
      button: 0,
      pointerId: 8,
      screenX: 520,
      screenY: 420,
    });
    fireEvent.click(core);

    expectNativeDragOnly(host.host);
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("compact", 220));
  });

  it("never starts a WebView drag session after a long press", async () => {
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

    const core = await screen.findByRole("button", { name: "Open Fairy quick input" });
    const surface = screen.getByTestId("presence-input-surface");
    fireEvent.pointerDown(core, {
      button: 0,
      pointerId: 9,
      clientX: 20,
      clientY: 20,
      screenX: 520,
      screenY: 420,
    });
    expectNativeDragOnly(host.host);

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 340));
    });
    expectNativeDragOnly(host.host);
    expect(surface).toHaveAttribute("data-moving", "false");

    fireEvent.pointerMove(core, {
      pointerId: 9,
      clientX: 22,
      clientY: 21,
      screenX: 552,
      screenY: 431,
    });
    fireEvent.pointerUp(window, {
      pointerId: 9,
      clientX: 22,
      clientY: 21,
      screenX: 552,
      screenY: 431,
    });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 80));
    });
    expectNativeDragOnly(host.host);
    expect(surface).toHaveAttribute("data-moving", "false");
    expect(surface).toHaveAttribute("data-layout", "core");
  });

  it("does not let pointer movement create a second drag owner", async () => {
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

    const core = await screen.findByRole("button", { name: "Open Fairy quick input" });
    fireEvent.pointerDown(core, {
      button: 0,
      pointerId: 10,
      screenX: 520,
      screenY: 420,
    });
    fireEvent.pointerMove(core, {
      pointerId: 10,
      screenX: 548,
      screenY: 432,
    });
    expectNativeDragOnly(host.host);

    fireEvent.pointerUp(window, {
      pointerId: 10,
      screenX: 548,
      screenY: 432,
    });
    expectNativeDragOnly(host.host);
  });

  it("captures the pointer without creating a second drag RPC owner", async () => {
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

    const core = await screen.findByRole("button", { name: "Open Fairy quick input" });
    const capturedPointers = new Set<number>();
    const releasePointerCapture = vi.fn((pointerId: number) => {
      capturedPointers.delete(pointerId);
    });
    Object.defineProperties(core, {
      setPointerCapture: {
        configurable: true,
        value: vi.fn((pointerId: number) => capturedPointers.add(pointerId)),
      },
      hasPointerCapture: {
        configurable: true,
        value: vi.fn((pointerId: number) => capturedPointers.has(pointerId)),
      },
      releasePointerCapture: {
        configurable: true,
        value: releasePointerCapture,
      },
    });
    const surface = screen.getByTestId("presence-input-surface");
    fireEvent.pointerDown(core, {
      button: 0,
      pointerId: 11,
      screenX: 520,
      screenY: 420,
    });
    expect(core.setPointerCapture).toHaveBeenCalledWith(11);
    expect(capturedPointers.has(11)).toBe(true);
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 340));
    });
    expect(surface).toHaveAttribute("data-moving", "false");

    fireEvent.pointerUp(core, {
      button: 0,
      pointerId: 11,
      screenX: 540,
      screenY: 430,
    });
    expectNativeDragOnly(host.host);
    expect(releasePointerCapture).toHaveBeenCalledWith(11);
    expect(capturedPointers.has(11)).toBe(false);
    expect(surface).toHaveAttribute("data-moving", "false");
  });

  it("keeps repeated pointer gestures out of the native drag command surface", async () => {
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

    const core = await screen.findByRole("button", { name: "Open Fairy quick input" });
    const drag = async (pointerId: number, startX: number) => {
      fireEvent.pointerDown(core, {
        button: 0,
        pointerId,
        screenX: startX,
        screenY: 420,
      });
      fireEvent.pointerMove(core, {
        pointerId,
        screenX: startX + 24,
        screenY: 432,
      });
      fireEvent.pointerUp(window, {
        pointerId,
        screenX: startX + 24,
        screenY: 432,
      });
      await Promise.resolve();
    };

    await drag(21, 520);
    await drag(22, 580);

    expectNativeDragOnly(host.host);
  });

  it("projects native coordinator repositioning without opening a WebView drag session", async () => {
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

    act(() => coordinator.emit(interactionAt(30, "idle", 0, 0)));
    const core = await screen.findByRole("button", { name: "Open Fairy quick input" });
    fireEvent.pointerDown(core, {
      button: 0,
      pointerId: 31,
      screenX: 520,
      screenY: 420,
    });
    fireEvent.pointerMove(core, {
      pointerId: 31,
      screenX: 544,
      screenY: 432,
    });
    fireEvent.pointerUp(window, {
      pointerId: 31,
      screenX: 544,
      screenY: 432,
    });
    expectNativeDragOnly(host.host);

    act(() => coordinator.emit(interactionAt(31, "repositioning", 300, 300)));
    await waitFor(() => expect(screen.getByTestId("presence-input-surface"))
      .toHaveAttribute("data-moving", "true"));
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("core"));

    act(() => coordinator.emit(interactionAt(32, "input_reveal", 600, 500)));
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("core"));

    act(() => coordinator.emit(interactionAt(33, "idle", 700, 700)));
    await waitFor(() => expect(screen.getByTestId("presence-input-surface"))
      .toHaveAttribute("data-moving", "false"));
    act(() => coordinator.emit(interactionAt(34, "input_reveal", 1_000, 1_000)));
    await waitFor(() =>
      expect(host.host.setInputLayout).toHaveBeenLastCalledWith("compact", 220)
    );
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
