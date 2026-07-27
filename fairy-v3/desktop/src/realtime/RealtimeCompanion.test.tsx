import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CoreClient, RealtimeWorkerStatus } from "../core/client";
import type { RealtimeSession } from "../core/contracts";
import {
  credentialProviderFor,
  RealtimeCompanion,
  mergeCaptionDelta,
  realtimeProviderErrorMessage,
  todaysRealtimeMinutes,
} from "./RealtimeCompanion";

const invoke = vi.fn();
const voiceMocks = vi.hoisted(() => ({ startRealtimeVoice: vi.fn() }));
let eventListener: ((event: { payload: Record<string, unknown> }) => void) | null = null;
let realtimeVoiceOutput: "provider_native_voice" | "fairy_voice" = "provider_native_voice";
let realtimeBetaEnabled = true;
let realtimeBackend: "auto" | "local_mini_cpm_o45" | "cloud_live" = "cloud_live";
let localBackendReady = false;

vi.mock("@tauri-apps/api/core", () => ({ invoke: (...args: unknown[]) => invoke(...args) }));
vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async (_name: string, listener: typeof eventListener) => {
    eventListener = listener;
    return () => { eventListener = null; };
  }),
}));
vi.mock("../voice/nativeVoice", () => ({
  startRealtimeVoice: voiceMocks.startRealtimeVoice,
}));

const session = (status: RealtimeSession["status"], revision: number): RealtimeSession => ({
  id: "01900000-0000-7000-8000-000000000001",
  device_id: "test-device",
  conversation_id: null,
  provider: "glm_realtime_flash",
  model_id: "glm-realtime-flash",
  voice_mode: "native",
  memory_mode: "progress_digest",
  status,
  microphone_consent: true,
  screen_consent: true,
  game_audio_consent: false,
  audio_input_ms: 0,
  audio_output_ms: 0,
  video_frame_count: 0,
  interruption_count: 0,
  tool_call_count: 0,
  last_error_code: null,
  started_at: "2026-07-20T00:00:00Z",
  ended_at: null,
  revision,
});

const workerStatus = (running: boolean): RealtimeWorkerStatus => ({
  running,
  session_id: running ? session("active", 2).id : null,
  segment_id: running ? "segment-1" : null,
  context_epoch: running ? 1 : null,
  backend: running ? "cloud_live" : null,
  cloud_provider: running ? "glm_realtime_flash" : null,
  action_required: false,
  presence_projection: null,
  audio_input_ms: 1_250,
  audio_output_ms: 400,
  video_frame_count: 8,
  interruption_count: 1,
  tool_call_count: 0,
});

const presenceProjection = (
  state: string,
  sequence: number,
): Record<string, unknown> => ({
  type: "presence_projection",
  session_id: session("active", 2).id,
  segment_id: "segment-1",
  context_epoch: 1,
  sequence,
  state,
  level: null,
  persona_digest: "a".repeat(64),
});

const backendPreview = async (input: {
  cloud_microphone_upload_consent: boolean;
  cloud_screen_upload_consent: boolean;
}) => {
  const consented = input.cloud_microphone_upload_consent
    && input.cloud_screen_upload_consent;
  const local = realtimeBackend === "local_mini_cpm_o45";
  const available = realtimeBetaEnabled && (local ? localBackendReady : consented);
  return {
    schema_version: 1 as const,
    resolution_token: "a".repeat(64),
    available,
    backend: available ? (local ? "local_mini_cpm_o45" as const : "cloud_live" as const) : null,
    cloud_provider: available && !local
      ? "glm_realtime_flash" as const
      : null,
    reason: !realtimeBetaEnabled
      ? "REALTIME_BETA_DISABLED"
      : local
        ? localBackendReady ? null : "LOCAL_MODEL_MISSING"
        : consented
          ? null
          : "CLOUD_UPLOAD_CONSENT_REQUIRED",
    requires_cloud_upload_consent: !local,
    preference_revision: 1,
  };
};

async function grantMediaConsentAndStart(includeApplicationAudio = false): Promise<void> {
  const consents = screen.getAllByRole("checkbox");
  fireEvent.click(consents[0]);
  fireEvent.click(consents[1]);
  if (includeApplicationAudio) fireEvent.click(consents[2]);
  const startButton = screen.getByRole("button", { name: "Start Realtime" });
  await waitFor(() => expect((startButton as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(startButton);
}

describe("RealtimeCompanion", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    eventListener = null;
    invoke.mockReset();
    voiceMocks.startRealtimeVoice.mockReset();
    voiceMocks.startRealtimeVoice.mockResolvedValue({
      readyForNext: Promise.resolve(),
      finished: Promise.resolve(),
      stop: vi.fn(),
    });
    realtimeVoiceOutput = "provider_native_voice";
    realtimeBetaEnabled = true;
    realtimeBackend = "cloud_live";
    localBackendReady = false;
    localStorage.clear();
    localStorage.setItem("fairy.realtime.device-id", "test-device");
    invoke.mockImplementation(async (command: string) => {
      if (command === "desktop_preferences_get") return {
        realtime_beta_enabled: realtimeBetaEnabled,
        realtime_backend: realtimeBackend,
        realtime_cloud_provider: "glm_realtime_flash",
        realtime_allow_cloud_fallback: false,
        realtime_activity_profile: "auto",
        realtime_interaction_intensity: "standard",
        realtime_voice_output: realtimeVoiceOutput,
        realtime_game_audio_default: false,
        realtime_online_assistance_enabled: false,
        realtime_memory_enabled: true,
        realtime_presence_max_minutes: 240,
        realtime_cloud_daily_limit_minutes: 180,
        realtime_local_keep_warm_minutes: 10,
      };
      if (command === "list_capture_surfaces") return [{
        kind: "window", source_id: "42", label: "Test Game", width: 1280, height: 720,
      }];
      if (command === "provider_realtime_status") return { provider: "zhipu", configured: true };
      throw new Error(`unexpected invoke: ${command}`);
    });
  });

  it("has no persistent workspace launcher and opens only from an explicit request", async () => {
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
      },
      memories: { save: vi.fn() },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
      },
    } as unknown as CoreClient["realtime"];

    const { rerender } = render(
      <RealtimeCompanion client={client} openRequest={0} />,
    );

    expect(screen.queryByTitle("Realtime Companion Beta")).toBeNull();
    expect(screen.queryByRole("dialog", { name: "Realtime Companion Beta" })).toBeNull();

    rerender(<RealtimeCompanion client={client} openRequest={1} />);

    expect(
      await screen.findByRole("dialog", { name: "Realtime Companion Beta" }),
    ).not.toBeNull();
  });

  it("cannot start while Realtime Beta is disabled", async () => {
    realtimeBetaEnabled = false;
    const start = vi.fn();
    const client = {
      sessions: { list: vi.fn(async () => ({ items: [] })) },
      memories: { save: vi.fn() },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
        start,
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getAllByRole("checkbox")[1]);

    expect(screen.getByText("Enable Realtime Beta in Settings before starting.")).not.toBeNull();
    expect((screen.getByRole("button", { name: "Start Realtime" }) as HTMLButtonElement).disabled)
      .toBe(true);
    expect(start).not.toHaveBeenCalled();
  });

  it("fails closed for Local Beta until a real readiness report exists", async () => {
    realtimeBackend = "local_mini_cpm_o45";
    const start = vi.fn();
    const client = {
      sessions: { list: vi.fn(async () => ({ items: [] })) },
      memories: { save: vi.fn() },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
        start,
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });

    expect(await screen.findByText(
      "Install and verify the Local MiniCPM model in Settings.",
    )).not.toBeNull();
    expect((screen.getByRole("button", { name: "Start Realtime" }) as HTMLButtonElement).disabled)
      .toBe(true);
    expect(start).not.toHaveBeenCalled();
    expect(invoke.mock.calls.some(([command]) => command === "provider_realtime_status")).toBe(false);
  });

  it("projects a ready Local backend and keeps media consent device-scoped", async () => {
    realtimeBackend = "local_mini_cpm_o45";
    localBackendReady = true;
    const start = vi.fn(async () => ({
      ...workerStatus(true),
      backend: "local_mini_cpm_o45" as const,
      cloud_provider: null,
    }));
    const startSession = vi.fn(async () => ({
      ...session("starting", 1),
      provider: "local_mini_cpm_o45" as const,
      model_id: "openbmb/minicpm-o-4.5-fairy-beta@4.5-q4-502eec5",
    }));
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
        start: startSession,
      },
      memories: { save: vi.fn() },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
        start,
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: /Test Game/ });

    expect(await screen.findByText(/Local .* MiniCPM-o 4\.5/)).not.toBeNull();
    expect(screen.getByText(/Local MiniCPM processes enabled microphone/)).not.toBeNull();
    expect(screen.getByRole("checkbox", {
      name: "Use microphone for this Local session",
    })).not.toBeNull();
    expect(invoke.mock.calls.some(([command]) => command === "provider_realtime_status")).toBe(false);

    await grantMediaConsentAndStart();
    await waitFor(() => expect(start).toHaveBeenCalledWith(expect.objectContaining({
      backend: "local_mini_cpm_o45",
      cloud_provider: null,
      cloud_microphone_upload_consent: true,
      cloud_screen_upload_consent: true,
    })));
    expect(startSession).toHaveBeenCalledWith(expect.objectContaining({
      provider: "local_mini_cpm_o45",
    }));
  });

  it("restores the native session and presence projection after the panel remounts", async () => {
    const activeSession = session("active", 2);
    const authoritativePresence = {
      ...presenceProjection("standby", 7),
      type: undefined,
    } as unknown as RealtimeWorkerStatus["presence_projection"];
    const status = {
      ...workerStatus(true),
      presence_projection: authoritativePresence,
    };
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [activeSession] })),
        get: vi.fn(async () => activeSession),
        report: vi.fn(),
      },
      memories: { save: vi.fn() },
      transcript: { append: vi.fn(), list: vi.fn(async () => ({ items: [] })) },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => status),
        stop: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);

    expect(await screen.findByText("Standing by")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Stop" })).not.toBeNull();
    expect(client.sessions.get).not.toHaveBeenCalled();
    expect(client.worker.stop).not.toHaveBeenCalled();
  });

  it("persists stable captions to the linked transcript, never into the usage report", async () => {
    const report = vi.fn(async (input: { status: RealtimeSession["status"] }) =>
      session(input.status, input.status === "active" ? 2 : 4));
    const stop = vi.fn(async () => session("stopping", 3));
    const append = vi.fn(async () => ({}));
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
        start: vi.fn(async () => session("starting", 1)),
        report,
        stop,
      },
      memories: { save: vi.fn() },
      transcript: { append, list: vi.fn(async () => ({ items: [] })) },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(async () => workerStatus(false)),
        toolResult: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    await grantMediaConsentAndStart();
    await waitFor(() => expect(client.worker.start).toHaveBeenCalledOnce());

    await act(async () => eventListener?.({ payload: {
      type: "session_state", session_id: session("active", 2).id, status: "active",
    } }));
    await act(async () => eventListener?.({
      payload: presenceProjection("standby", 2),
    }));
    // A partial caption only updates the live draft; nothing is persisted yet.
    await act(async () => eventListener?.({ payload: {
      type: "public_caption", session_id: session("active", 2).id,
      text: "Boss at ", stable: false, speaker: "assistant",
    } }));
    expect(append).not.toHaveBeenCalled();
    // The stable caption completes the line and is written to the transcript.
    await act(async () => eventListener?.({ payload: {
      type: "public_caption", session_id: session("active", 2).id,
      text: "half health", stable: true, speaker: "assistant",
    } }));
    expect(screen.getByText("Boss at half health")).not.toBeNull();
    await waitFor(() => expect(append).toHaveBeenCalledWith({
      session_id: session("active", 2).id,
      speaker: "assistant",
      text: "Boss at half health",
    }));

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(stop).toHaveBeenCalledOnce());
    await waitFor(() => expect(report).toHaveBeenCalledWith(expect.objectContaining({
      status: "completed",
      audio_input_ms: 1_250,
      audio_output_ms: 400,
      video_frame_count: 8,
      interruption_count: 1,
      tool_call_count: 0,
    })));
    // Captions live only in the local transcript, never in the cloud-synced usage report.
    expect(JSON.stringify(report.mock.calls)).not.toContain("Boss at half health");
  });

  it("keeps the session active while an unsaved caption is retried manually", async () => {
    const append = vi.fn().mockRejectedValue(new Error("offline"));
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
        start: vi.fn(async () => session("starting", 1)),
        get: vi.fn(async () => session("active", 2)),
        report: vi.fn(async () => session("active", 2)),
        stop: vi.fn(),
      },
      memories: { save: vi.fn() },
      transcript: { append, list: vi.fn(async () => ({ items: [] })) },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(),
        toolResult: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: /Test Game/ });
    await grantMediaConsentAndStart();
    await waitFor(() => expect(client.worker.start).toHaveBeenCalledOnce());
    await act(async () => eventListener?.({ payload: {
      type: "session_state", session_id: session("active", 2).id, status: "active",
    } }));
    await act(async () => eventListener?.({
      payload: presenceProjection("standby", 2),
    }));
    await act(async () => eventListener?.({ payload: {
      type: "public_caption",
      session_id: session("active", 2).id,
      text: "Hold this position.",
      stable: true,
      speaker: "assistant",
    } }));

    expect(
      await screen.findByText("1 caption unsaved", {}, { timeout: 3_000 }),
    ).not.toBeNull();
    expect(append).toHaveBeenCalledTimes(3);
    expect(screen.getByText("Standing by")).not.toBeNull();

    append.mockResolvedValue({});
    fireEvent.click(screen.getByRole("button", { name: "Retry saving" }));

    await waitFor(() => expect(append).toHaveBeenCalledTimes(4));
    await waitFor(() => expect(screen.queryByText("1 caption unsaved")).toBeNull());
    expect(screen.getByText("Standing by")).not.toBeNull();
  });

  it("renders only fresh persona-bound Coordinator presence projections", async () => {
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
        start: vi.fn(async () => session("starting", 1)),
        get: vi.fn(async () => session("active", 2)),
        report: vi.fn(async () => session("active", 2)),
        stop: vi.fn(),
      },
      memories: { save: vi.fn() },
      transcript: { append: vi.fn(), list: vi.fn(async () => ({ items: [] })) },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(),
        toolResult: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: /Test Game/ });
    await grantMediaConsentAndStart();
    await waitFor(() => expect(client.worker.start).toHaveBeenCalledOnce());
    await act(async () => eventListener?.({ payload: {
      type: "session_state",
      session_id: session("active", 2).id,
      status: "active",
    } }));
    expect(screen.getByText("Idle")).not.toBeNull();

    await act(async () => eventListener?.({
      payload: presenceProjection("thinking", 4),
    }));
    expect(screen.getByText("Thinking")).not.toBeNull();

    await act(async () => eventListener?.({
      payload: presenceProjection("speaking", 3),
    }));
    const drift = presenceProjection("speaking", 5);
    drift.persona_digest = "b".repeat(64);
    await act(async () => eventListener?.({ payload: drift }));
    expect(screen.getByText("Thinking")).not.toBeNull();
    expect(screen.queryByText("Fairy is speaking")).toBeNull();
  });

  it("routes mute and privacy pause through their separate native controls", async () => {
    const setInput = vi.fn(async () => undefined);
    const pausePrivacy = vi.fn(async () => workerStatus(true));
    const resumePrivacy = vi.fn(async () => workerStatus(true));
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
        start: vi.fn(async () => session("starting", 1)),
        get: vi.fn(async () => session("active", 2)),
        report: vi.fn(async () => session("active", 2)),
        stop: vi.fn(),
      },
      memories: { save: vi.fn() },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(),
        toolResult: vi.fn(),
        setInput,
        pausePrivacy,
        resumePrivacy,
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    await grantMediaConsentAndStart();
    await waitFor(() => expect(client.worker.start).toHaveBeenCalledOnce());
    await act(async () => eventListener?.({ payload: {
      type: "session_state", session_id: session("active", 2).id, status: "active",
    } }));

    fireEvent.click(screen.getByRole("button", { name: "展开控制" }));
    fireEvent.click(screen.getByRole("button", { name: /闭麦/ }));
    await waitFor(() => expect(setInput).toHaveBeenLastCalledWith(
      expect.objectContaining({ microphone: false, video: true }),
    ));
    fireEvent.click(screen.getByRole("button", { name: /暂停/ }));
    await waitFor(() => expect(pausePrivacy).toHaveBeenCalledWith({
      session_id: session("active", 2).id,
    }));
    expect(setInput).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /恢复/ }));
    await waitFor(() => expect(resumePrivacy).toHaveBeenCalledWith({
      session_id: session("active", 2).id,
    }));
    await waitFor(() => expect(setInput).toHaveBeenLastCalledWith(
      expect.objectContaining({ microphone: false, video: true }),
    ));
  });

  it("keeps application audio as an explicit selected-app session consent", async () => {
    const client = {
      sessions: { list: vi.fn(async () => ({ items: [] })) },
      memories: { save: vi.fn() },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    expect(screen.getAllByRole("checkbox")).toHaveLength(3);
    expect((screen.getByRole("checkbox", {
      name: "Upload selected application audio",
    }) as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText(/System-wide audio is never captured/)).not.toBeNull();
  });

  it("sends the governed cloud controls without credential or Persona bodies", async () => {
    const start = vi.fn(async (_input: Record<string, unknown>) => workerStatus(true));
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
        start: vi.fn(async () => session("starting", 1)),
      },
      memories: { save: vi.fn() },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
        start,
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    await grantMediaConsentAndStart(true);

    await waitFor(() => expect(start).toHaveBeenCalledWith(expect.objectContaining({
      session_id: session("starting", 1).id,
      resolution_token: "a".repeat(64),
      backend: "cloud_live",
      cloud_provider: "glm_realtime_flash",
      activity_profile: "auto",
      interaction_intensity: "standard",
      voice_output: "provider_native_voice",
      microphone_enabled: true,
      screen_enabled: true,
      application_audio_enabled: true,
      online_assistance_enabled: false,
      cloud_microphone_upload_consent: true,
      cloud_screen_upload_consent: true,
    })));
    const request = start.mock.calls[0]?.[0] as unknown as Record<string, unknown>;
    expect(request).not.toHaveProperty("credential");
    expect(request).not.toHaveProperty("persona_snapshot");
  });

  it("cancels a session that is still connecting without reporting completion", async () => {
    const report = vi.fn();
    const stop = vi.fn(async () => session("cancelled", 2));
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
        start: vi.fn(async () => session("starting", 1)),
        get: vi.fn(),
        report,
        stop,
      },
      memories: { save: vi.fn() },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(async () => workerStatus(false)),
        toolResult: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    await grantMediaConsentAndStart();
    await waitFor(() => expect(client.worker.start).toHaveBeenCalledOnce());

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));

    await waitFor(() => expect(stop).toHaveBeenCalledWith(expect.objectContaining({
      expected_revision: 1,
    })));
    await waitFor(() => expect(client.worker.stop).toHaveBeenCalledOnce());
    expect(report).not.toHaveBeenCalled();
    await act(async () => eventListener?.({ payload: {
      type: "worker_interrupted", error_code: "WORKER_INTERRUPTED",
    } }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("stops local capture and retries once when an active event wins the revision race", async () => {
    const conflict = Object.assign(new Error("stale realtime revision"), {
      errorCode: "VERSION_CONFLICT",
    });
    const stop = vi.fn()
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce(session("stopping", 4));
    const get = vi.fn(async () => session("active", 3));
    const report = vi.fn(async (input: { status: RealtimeSession["status"] }) =>
      session(input.status, 5));
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
        start: vi.fn(async () => session("starting", 1)),
        get,
        report,
        stop,
      },
      memories: { save: vi.fn() },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(async () => workerStatus(false)),
        toolResult: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    await grantMediaConsentAndStart();
    await waitFor(() => expect(client.worker.start).toHaveBeenCalledOnce());

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));

    await waitFor(() => expect(stop).toHaveBeenCalledTimes(2));
    expect(stop.mock.calls[1]?.[0]).toEqual(expect.objectContaining({ expected_revision: 3 }));
    expect(get).toHaveBeenCalledWith(session("active", 3).id);
    expect(client.worker.stop).toHaveBeenCalledOnce();
    await waitFor(() => expect(report).toHaveBeenCalledWith(expect.objectContaining({
      status: "completed",
    })));
  });

  it("stops queued Fairy playback immediately on the shared barge-in event", async () => {
    realtimeVoiceOutput = "fairy_voice";
    let finishPlayback!: () => void;
    const stopPlayback = vi.fn();
    voiceMocks.startRealtimeVoice.mockResolvedValue({
      readyForNext: Promise.resolve(),
      finished: new Promise<void>((resolve) => { finishPlayback = resolve; }),
      stop: stopPlayback,
    });
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
        start: vi.fn(async () => session("starting", 1)),
        get: vi.fn(async () => session("active", 2)),
        report: vi.fn(async () => session("active", 2)),
        stop: vi.fn(),
      },
      memories: { save: vi.fn() },
      transcript: { append: vi.fn(async () => ({})), list: vi.fn(async () => ({ items: [] })) },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(),
        toolResult: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    await grantMediaConsentAndStart();
    await waitFor(() => expect(client.worker.start).toHaveBeenCalledOnce());
    await act(async () => eventListener?.({ payload: {
      type: "session_state", session_id: session("active", 2).id, status: "active",
    } }));
    await act(async () => eventListener?.({
      payload: presenceProjection("standby", 2),
    }));
    await act(async () => eventListener?.({ payload: {
      type: "public_caption",
      session_id: session("active", 2).id,
      segment_id: "segment-1",
      context_epoch: 1,
      text: "Watch the next attack.",
      stable: true,
      speaker: "assistant",
      speech_output: "fairy_voice",
      speech_generation: 1,
      persona_digest: "a".repeat(64),
    } }));
    await waitFor(() => expect(voiceMocks.startRealtimeVoice).toHaveBeenCalledOnce());
    await act(async () => eventListener?.({ payload: {
      type: "public_caption",
      session_id: session("active", 2).id,
      segment_id: "segment-1",
      context_epoch: 1,
      text: "This queued sentence must be discarded.",
      stable: true,
      speaker: "assistant",
      speech_output: "fairy_voice",
      speech_generation: 1,
      persona_digest: "a".repeat(64),
    } }));

    await act(async () => eventListener?.({ payload: {
      type: "barge_in", session_id: session("active", 2).id,
      segment_id: "segment-1", context_epoch: 1, speech_generation: 2,
    } }));
    await act(async () => eventListener?.({
      payload: presenceProjection("listening", 3),
    }));

    expect(stopPlayback).toHaveBeenCalledOnce();
    expect(screen.getByText("Listening")).not.toBeNull();
    await act(async () => {
      finishPlayback();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(voiceMocks.startRealtimeVoice).toHaveBeenCalledTimes(2);
  });

  it("shows non-conflict persistence failures and reloads the authoritative session", async () => {
    const persistenceError = Object.assign(new Error("Realtime state could not be saved."), {
      errorCode: "CORE_WRITE_FAILED",
    });
    const get = vi.fn(async () => session("active", 2));
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
        start: vi.fn(async () => session("starting", 1)),
        get,
        report: vi.fn(async () => { throw persistenceError; }),
        stop: vi.fn(),
      },
      memories: { save: vi.fn() },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(),
        toolResult: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    await grantMediaConsentAndStart();
    await waitFor(() => expect(client.worker.start).toHaveBeenCalledOnce());

    await act(async () => eventListener?.({ payload: {
      type: "session_state", session_id: session("active", 2).id, status: "active",
    } }));

    expect(await screen.findByText("Realtime state could not be saved.")).not.toBeNull();
    expect(get).toHaveBeenCalledWith(session("active", 2).id);
  });
});

describe("credentialProviderFor", () => {
  it("selects the account used by the explicit cloud provider", () => {
    expect(credentialProviderFor("glm_realtime_flash")).toBe("zhipu");
    expect(credentialProviderFor("glm_realtime_air")).toBe("zhipu");
    expect(credentialProviderFor("gemini_live")).toBe("gemini");
  });
});

describe("todaysRealtimeMinutes", () => {
  it("sums only today's sessions by their larger audio direction", async () => {
    const nowIso = new Date().toISOString();
    const yesterdayIso = new Date(Date.now() - 26 * 3_600_000).toISOString();
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [
          { ...session("completed", 3), started_at: nowIso, audio_input_ms: 120 * 60_000, audio_output_ms: 30 * 60_000 },
          { ...session("completed", 3), started_at: yesterdayIso, audio_input_ms: 999 * 60_000, audio_output_ms: 0 },
        ] })),
      },
    } as unknown as CoreClient["realtime"];
    expect(await todaysRealtimeMinutes(client)).toBeCloseTo(120);
  });

  it("returns zero when the usage lookup fails", async () => {
    const client = {
      sessions: { list: vi.fn(async () => { throw new Error("offline"); }) },
    } as unknown as CoreClient["realtime"];
    expect(await todaysRealtimeMinutes(client)).toBe(0);
  });
});

describe("mergeCaptionDelta", () => {
  it("supports both cumulative provider text and token deltas", () => {
    expect(mergeCaptionDelta("Boss", "Boss incoming")).toBe("Boss incoming");
    expect(mergeCaptionDelta("Boss ", "incoming")).toBe("Boss incoming");
    expect(mergeCaptionDelta("Boss incoming", "incoming")).toBe("Boss incoming");
  });
});

describe("realtimeProviderErrorMessage", () => {
  it("turns safe worker codes into actionable user messages", () => {
    expect(realtimeProviderErrorMessage("REALTIME_PROVIDER_QUOTA_EXHAUSTED"))
      .toContain("balance or quota");
    expect(realtimeProviderErrorMessage("REALTIME_PROVIDER_AUTHENTICATION_FAILED"))
      .toContain("API key");
    expect(realtimeProviderErrorMessage("REALTIME_CREDENTIAL_MISSING"))
      .toContain("Settings");
    expect(realtimeProviderErrorMessage("provider secret leaked here"))
      .toBe("Realtime session failed.");
    expect(realtimeProviderErrorMessage("UNRECOGNIZED_INTERNAL_CODE"))
      .toBe("Realtime session failed.");
  });
});
