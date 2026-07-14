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
import type {
  PresenceChannel,
  PresenceSubmissionUpdate,
} from "./transport/presenceChannel";
import type { PresenceInteractionSource } from "./transport/interactionEvents";

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
    schema_version: 2,
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
    pet_size_percent: 100,
    pet_opacity_percent: 92,
    pet_motion_enabled: true,
    pet_particles_enabled: true,
    pet_hover_enabled: true,
    pet_hover_dwell_ms: 250,
    pet_do_not_disturb: false,
    pet_remember_position: true,
    pet_renderer_mode: "auto",
    pet_anchor: null,
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
    onNewChatRequested: vi.fn(async () => () => undefined),
    setExpanded: vi.fn(async () => undefined),
    setInputLayout: vi.fn(async () => undefined),
    setInputInteractive: vi.fn(async () => undefined),
    requestInputFocus: vi.fn(async () => undefined),
    beginGroupDrag: vi.fn(async () => undefined),
    moveGroupDrag: vi.fn(async () => undefined),
    endGroupDrag: vi.fn(async () => preferences()),
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

  it("keeps a core hit proxy available and expands for input or projected cards", async () => {
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
    expect(screen.getByRole("button", { name: "Open Fairy quick input" })).toBeInTheDocument();
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
    await waitFor(() => expect(host.host.setInputLayout).toHaveBeenLastCalledWith("core"));

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
    expect(screen.getByRole("status")).toHaveTextContent("Sending to Fairy");

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
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /reject/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Review in Fairy" }));
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

  it("moves the native window group only from the visible grip", async () => {
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
    const grip = await screen.findByRole("button", { name: "Move Fairy" });
    fireEvent.pointerDown(grip, { pointerId: 4, clientX: 10, clientY: 20 });
    fireEvent.pointerMove(grip, { pointerId: 4, clientX: 48, clientY: 35 });
    fireEvent.pointerUp(grip, { pointerId: 4, clientX: 48, clientY: 35 });

    await waitFor(() => expect(host.host.beginGroupDrag).toHaveBeenCalledOnce());
    await waitFor(() => expect(host.host.moveGroupDrag).toHaveBeenCalledWith(38, 15));
    await waitFor(() => expect(host.host.endGroupDrag).toHaveBeenCalledWith(0));
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
