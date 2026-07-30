import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CoreClient, RealtimeWorkerStatus } from "../core/client";
import type { RealtimeSession } from "../core/contracts";
import {
  credentialProviderFor,
  RealtimeCompanion,
  mergeCaptionDelta,
  realtimeProviderErrorMessage,
} from "./RealtimeCompanion";

const invoke = vi.fn();
const voiceMocks = vi.hoisted(() => ({ startRealtimeVoice: vi.fn() }));
let eventListener: ((event: { payload: Record<string, unknown> }) => void) | null = null;
let realtimeVoiceOutput: "provider_native_voice" | "fairy_voice" = "provider_native_voice";
let realtimeBetaEnabled = true;
let realtimeBackend: "auto" | "local_mini_cpm_o45" | "cloud_live" = "cloud_live";
let localBackendReady = false;
let realtimeApplicationAudioDefault = false;
let realtimeCaptureMode: "selected_window" | "follow_foreground" = "selected_window";
let realtimeExcludedApplications: string[] = [];

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
  backend: running
    ? realtimeBackend === "local_mini_cpm_o45" ? "local_mini_cpm_o45" : "cloud_live"
    : null,
  cloud_provider: running && realtimeBackend !== "local_mini_cpm_o45"
    ? "glm_realtime_flash"
    : null,
  action_required: false,
  presence_projection: null,
  assistance: [],
  resource: null,
  sidecar: {
    restart_used: false,
    quarantined: false,
    context_interrupted: false,
    failure_count: 0,
    error_code: null,
  },
  capture_scope: running ? {
    mode: realtimeBackend === "local_mini_cpm_o45"
      ? realtimeCaptureMode
      : "selected_window",
    source_sequence: 1,
    source_available: true,
    privacy_paused: false,
    sensitive_category: null,
    error_code: null,
  } : null,
  media_channels: running ? [
    { channel: "microphone", sequence: 1, status: "active", error_code: null },
    { channel: "selected_window", sequence: 1, status: "active", error_code: null },
  ] : [],
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
  requested_activity_profile: "auto",
  effective_activity: "focus",
  interaction_intensity: "standard",
  backend: "cloud_live",
  cloud_provider: "glm_realtime_flash",
  standby_reason: state === "standby" ? "inactivity" : null,
  wake_available: state === "standby",
  duration_extension_required: false,
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
    realtimeApplicationAudioDefault = false;
    realtimeCaptureMode = "selected_window";
    realtimeExcludedApplications = [];
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
        realtime_game_audio_default: realtimeApplicationAudioDefault,
        realtime_capture_mode: realtimeCaptureMode,
        realtime_excluded_applications: realtimeExcludedApplications,
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
      if (command === "open_realtime_main_chat") return undefined;
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

  it("restores only bounded memory state for the latest completed Session", async () => {
    const completed = {
      ...session("completed", 3),
      ended_at: "2026-07-20T00:10:00Z",
    };
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [completed] })),
      },
      digests: {
        list: vi.fn(async () => ({ items: [{
          id: "01900000-0000-7000-8000-000000000002",
          session_id: completed.id,
        }] })),
      },
      memoryProposals: {
        list: vi.fn(async () => ({ items: [{
          id: "01900000-0000-7000-8000-000000000003",
          session_id: completed.id,
          digest_id: "01900000-0000-7000-8000-000000000002",
          status: "pending",
          normalized_text: "private inferred detail",
        }] })),
      },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);

    expect(await screen.findByText("Memory review available")).not.toBeNull();
    expect(screen.queryByText("private inferred detail")).toBeNull();
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open main chat" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith(
      "open_realtime_main_chat",
      { input: { session_id: completed.id } },
    ));
  });

  it("projects persisted Sidecar quarantine after a terminal Session without recovery controls", async () => {
    const status = workerStatus(false);
    status.sidecar = {
      restart_used: true,
      quarantined: true,
      context_interrupted: true,
      failure_count: 2,
      error_code: "LOCAL_SIDECAR_PROCESS_EXIT",
    };
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [session("failed", 3)] })),
      },
      digests: {
        list: vi.fn(async () => ({ items: [] })),
      },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => status),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);

    expect(await screen.findByText("Local runtime needs verification")).not.toBeNull();
    expect(screen.getByText(/Automatic recovery is disabled/)).not.toBeNull();
    expect(screen.queryByRole("button", { name: /restart|recover/i })).toBeNull();
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

  it("restores bounded Assistance state and keeps approval in the main chat", async () => {
    const linked = {
      ...session("active", 2),
      conversation_id: "01900000-0000-7000-8000-000000000099",
    };
    const runningAssistance = {
      session_id: linked.id,
      segment_id: "segment-1",
      context_epoch: 1,
      request_id: "request-1",
      public_intent: "Find the current raid route",
      status: "awaiting_approval" as const,
      error_code: null,
      public_summary: null,
    };
    const get = vi.fn(async () => ({
      status: "awaiting_approval",
      revision: 3,
    }));
    const cancel = vi.fn(async () => ({
      status: "cancelled",
      revision: 4,
      error_code: null,
      spoken_summary: null,
    }));
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [linked] })),
        get: vi.fn(async () => linked),
      },
      assistance: { get, cancel },
      memories: { save: vi.fn() },
      transcript: { append: vi.fn(), list: vi.fn(async () => ({ items: [] })) },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => ({
          ...workerStatus(true),
          assistance: [runningAssistance],
        })),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);

    expect(await screen.findByText("Find the current raid route")).not.toBeNull();
    expect(screen.getByText("Approval needed")).not.toBeNull();
    expect(screen.getByText("Approval is waiting in the main Fairy workspace.")).not.toBeNull();
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Open main chat" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith(
      "open_realtime_main_chat",
      { input: { session_id: linked.id } },
    ));

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(cancel).toHaveBeenCalledWith({
      session_id: linked.id,
      request_id: "request-1",
      expected_revision: 3,
    }));
    expect(await screen.findByText("Cancelled")).not.toBeNull();
  });

  it("renders only the bounded Assistance summary from live events", async () => {
    const linked = {
      ...session("active", 2),
      conversation_id: "01900000-0000-7000-8000-000000000099",
    };
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [linked] })),
        get: vi.fn(async () => linked),
      },
      assistance: { get: vi.fn(), cancel: vi.fn() },
      memories: { save: vi.fn() },
      transcript: { append: vi.fn(), list: vi.fn(async () => ({ items: [] })) },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(true)),
      },
    } as unknown as CoreClient["realtime"];
    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByText("Listening for the conversation and observed context…");

    await act(async () => eventListener?.({ payload: {
      type: "assistance_state",
      session_id: linked.id,
      segment_id: "segment-1",
      context_epoch: 1,
      request_id: "request-live",
      public_intent: "Locate the next checkpoint",
      status: "completed",
      error_code: null,
      public_summary: "The next checkpoint is east.",
      display_markdown: "# Full answer must not render",
      citations: [{ url: "https://private.invalid" }],
    } }));

    expect(screen.getByText("The next checkpoint is east.")).not.toBeNull();
    expect(screen.queryByText("# Full answer must not render")).toBeNull();
    expect(screen.queryByText("https://private.invalid")).toBeNull();
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

  it("keeps requested Auto, effective activity, intensity and policy changes native", async () => {
    const activeSession = session("active", 2);
    const initialProjection = {
      ...presenceProjection("listening", 7),
      type: undefined,
      effective_activity: "game",
    } as unknown as RealtimeWorkerStatus["presence_projection"];
    const updatedProjection = {
      ...presenceProjection("preparing", 8),
      type: undefined,
      effective_activity: "focus",
      interaction_intensity: "active",
    } as unknown as RealtimeWorkerStatus["presence_projection"];
    const setPolicy = vi.fn(async () => ({
      ...workerStatus(true),
      presence_projection: updatedProjection,
    }));
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
        status: vi.fn(async () => ({
          ...workerStatus(true),
          presence_projection: initialProjection,
        })),
        setPolicy,
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);

    const profile = await screen.findByRole("combobox", { name: "Profile" });
    const intensity = screen.getByRole("combobox", { name: "Intensity" });
    expect((profile as HTMLSelectElement).value).toBe("auto");
    expect((intensity as HTMLSelectElement).value).toBe("standard");
    expect(screen.getByText("Proactive Game comments wait at least 45 seconds; direct replies stay immediate.")).not.toBeNull();

    fireEvent.change(intensity, { target: { value: "active" } });
    await waitFor(() => expect(setPolicy).toHaveBeenCalledWith({
      session_id: activeSession.id,
      activity_profile: "auto",
      interaction_intensity: "active",
    }));
    expect(await screen.findByText(
      "Proactive Focus comments wait at least 3 minutes; direct replies stay immediate.",
    )).not.toBeNull();
  });

  it("uses native wake and duration-extension actions from standby projection", async () => {
    const activeSession = session("active", 2);
    const standbyProjection = {
      ...presenceProjection("standby", 7),
      type: undefined,
    } as unknown as RealtimeWorkerStatus["presence_projection"];
    const wake = vi.fn(async () => ({
      ...workerStatus(true),
      presence_projection: {
        ...presenceProjection("preparing", 8),
        type: undefined,
        standby_reason: null,
        wake_available: false,
      } as unknown as RealtimeWorkerStatus["presence_projection"],
    }));
    const extend = vi.fn(async () => workerStatus(true));
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
        status: vi.fn(async () => ({
          ...workerStatus(true),
          presence_projection: standbyProjection,
        })),
        wake,
        extend,
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);

    fireEvent.click(await screen.findByRole("button", { name: "Wake" }));
    await waitFor(() => expect(wake).toHaveBeenCalledWith({
      session_id: activeSession.id,
    }));
    expect(extend).not.toHaveBeenCalled();
  });

  it("persists stable captions to the linked transcript, never into the usage report", async () => {
    const report = vi.fn(async (input: { status: RealtimeSession["status"] }) =>
      session(input.status, input.status === "active" ? 2 : 4));
    const stop = vi.fn(async () => session("stopping", 3));
    const append = vi.fn(async () => ({}));
    const createDigest = vi.fn(async () => ({
      id: "01900000-0000-7000-8000-000000000002",
      session_id: session("completed", 4).id,
    }));
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
        start: vi.fn(async () => session("starting", 1)),
        report,
        stop,
      },
      memories: { save: vi.fn() },
      transcript: { append, list: vi.fn(async () => ({ items: [] })) },
      digests: {
        create: createDigest,
        list: vi.fn(async () => ({ items: [] })),
      },
      memoryProposals: {
        list: vi.fn(async () => ({ items: [] })),
      },
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
    await waitFor(() => expect(createDigest).toHaveBeenCalledWith({
      session_id: session("completed", 4).id,
      request_id: `desktop:realtime-digest:${session("completed", 4).id}`,
      activity: "auto",
      subject_title: "Test Game",
    }));
    expect(append.mock.invocationCallOrder[0]).toBeLessThan(
      createDigest.mock.invocationCallOrder[0],
    );
    expect(await screen.findByText("Session summary saved")).not.toBeNull();
    // Captions live only in the local transcript, never in the cloud-synced usage report.
    expect(JSON.stringify(report.mock.calls)).not.toContain("Boss at half health");
  });

  it("shows the authoritative startup stage while the backend runtime is starting", async () => {
    let resolveWorker!: (status: RealtimeWorkerStatus) => void;
    const workerStart = new Promise<RealtimeWorkerStatus>((resolve) => {
      resolveWorker = resolve;
    });
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
        start: vi.fn(async () => session("starting", 1)),
        report: vi.fn(async () => session("failed", 2)),
      },
      memories: { save: vi.fn() },
      transcript: { append: vi.fn(), list: vi.fn(async () => ({ items: [] })) },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(() => workerStart),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: /Test Game/ });
    await grantMediaConsentAndStart();

    expect(
      (await screen.findByRole("status", { name: "Realtime startup" })).textContent,
    ).toBe("Starting backend runtime");

    resolveWorker(workerStatus(true));
    await waitFor(() => expect(
      screen.queryByRole("status", { name: "Realtime startup" }),
    ).toBeNull());
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

    const stale = presenceProjection("speaking", 3);
    stale.effective_activity = "game";
    await act(async () => eventListener?.({ payload: stale }));
    const drift = presenceProjection("speaking", 5);
    drift.persona_digest = "b".repeat(64);
    await act(async () => eventListener?.({ payload: drift }));
    expect(screen.getByText("Thinking")).not.toBeNull();
    expect(screen.queryByText("Fairy is speaking")).toBeNull();
    expect(screen.getByText(
      "Proactive Focus comments wait at least 8 minutes; direct replies stay immediate.",
    )).not.toBeNull();
  });

  it("routes mute and privacy pause through their separate native controls", async () => {
    const setInput = vi.fn(async () => undefined);
    const pausePrivacy = vi.fn(async () => ({
      ...workerStatus(true),
      presence_projection: {
        ...presenceProjection("privacy_paused", 3),
        type: undefined,
        standby_reason: null,
        wake_available: true,
      } as unknown as RealtimeWorkerStatus["presence_projection"],
    }));
    const resumePrivacy = vi.fn(async () => ({
      ...workerStatus(true),
      presence_projection: {
        ...presenceProjection("standby", 4),
        type: undefined,
        standby_reason: null,
        wake_available: false,
      } as unknown as RealtimeWorkerStatus["presence_projection"],
    }));
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

  it("keeps unsupported Cloud application audio visibly disabled", async () => {
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
    const applicationAudio = screen.getByRole("checkbox", {
      name: "Application audio unavailable for this Cloud provider",
    }) as HTMLInputElement;
    expect(applicationAudio.checked).toBe(false);
    expect(applicationAudio.disabled).toBe(true);
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
    await grantMediaConsentAndStart();

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
      application_audio_enabled: false,
      online_assistance_enabled: false,
      cloud_microphone_upload_consent: true,
      cloud_screen_upload_consent: true,
      capture_mode: "selected_window",
      excluded_applications: [],
    })));
    const request = start.mock.calls[0]?.[0] as unknown as Record<string, unknown>;
    expect(request).not.toHaveProperty("credential");
    expect(request).not.toHaveProperty("persona_snapshot");
  });

  it("applies the saved Local audio default and records the actual capture scope", async () => {
    realtimeBackend = "local_mini_cpm_o45";
    localBackendReady = true;
    realtimeApplicationAudioDefault = true;
    realtimeCaptureMode = "follow_foreground";
    realtimeExcludedApplications = ["obs64.exe", "private-app.exe"];
    const startSession = vi.fn(async (input: { game_audio_consent: boolean }) => ({
      ...session("starting", 1),
      provider: "local_mini_cpm_o45",
      game_audio_consent: input.game_audio_consent,
    }));
    const startWorker = vi.fn(async () => workerStatus(true));
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
        start: startSession,
      },
      memories: { save: vi.fn() },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => workerStatus(false)),
        start: startWorker,
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: /Test Game/ });
    await screen.findByText(/Local .* MiniCPM-o 4\.5/);
    const applicationAudio = screen.getByRole("checkbox", {
      name: "Process selected application audio locally",
    }) as HTMLInputElement;
    expect(applicationAudio.checked).toBe(true);
    expect(applicationAudio.disabled).toBe(false);
    expect((screen.getByRole("combobox", {
      name: "Observation scope",
    }) as HTMLSelectElement).value).toBe("follow_foreground");

    await grantMediaConsentAndStart();

    await waitFor(() => expect(startSession).toHaveBeenCalledWith(
      expect.objectContaining({ game_audio_consent: true }),
    ));
    expect(startWorker).toHaveBeenCalledWith(expect.objectContaining({
      backend: "local_mini_cpm_o45",
      application_audio_enabled: true,
      capture_mode: "follow_foreground",
      excluded_applications: ["obs64.exe", "private-app.exe"],
    }));
  });

  it("renders native channel recovery and ignores late events from another Session", async () => {
    const degradedStatus: RealtimeWorkerStatus = {
      ...workerStatus(true),
      capture_scope: {
        mode: "selected_window",
        source_sequence: 4,
        source_available: false,
        privacy_paused: false,
        sensitive_category: null,
        error_code: "CAPTURE_SOURCE_UNAVAILABLE",
      },
      media_channels: [{
        channel: "microphone",
        sequence: 3,
        status: "unavailable",
        error_code: "MICROPHONE_DEVICE_LOST",
      }],
    };
    const retryMedia = vi.fn(async () => degradedStatus);
    const replaceSource = vi.fn(async () => ({
      ...degradedStatus,
      capture_scope: {
        ...degradedStatus.capture_scope!,
        source_sequence: 5,
        source_available: true,
        error_code: null,
      },
    }));
    const activeSession = session("active", 2);
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [activeSession] })),
        get: vi.fn(async () => activeSession),
      },
      memories: { save: vi.fn() },
      transcript: { append: vi.fn(), list: vi.fn(async () => ({ items: [] })) },
      worker: {
        preview: vi.fn(backendPreview),
        status: vi.fn(async () => degradedStatus),
        retryMedia,
        replaceSource,
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    expect(await screen.findByText("The observed window is unavailable. Microphone conversation can continue.")).not.toBeNull();
    expect(screen.getByText("Microphone")).not.toBeNull();
    expect(screen.getByText("unavailable")).not.toBeNull();

    await act(async () => eventListener?.({ payload: {
      type: "media_channel_state",
      session_id: activeSession.id,
      segment_id: "segment-late",
      context_epoch: 1,
      channel: "microphone",
      sequence: 99,
      status: "active",
      error_code: null,
    } }));
    expect(screen.getByText("unavailable")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(retryMedia).toHaveBeenCalledWith({
      session_id: activeSession.id,
      channel: "microphone",
    }));
    const useWindow = screen.getByRole("button", { name: "Use window" }) as HTMLButtonElement;
    await waitFor(() => expect(useWindow.disabled).toBe(false));
    fireEvent.click(useWindow);
    await waitFor(() => expect(replaceSource).toHaveBeenCalledWith({
      session_id: activeSession.id,
      source_id: 42,
    }));
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
    expect(realtimeProviderErrorMessage("REALTIME_CLOUD_DAILY_LIMIT_REACHED"))
      .toContain("limit");
    expect(realtimeProviderErrorMessage("REALTIME_CLOUD_USAGE_UNAVAILABLE"))
      .toContain("blocked");
    expect(realtimeProviderErrorMessage("LOCAL_RUNTIME_QUARANTINED"))
      .toContain("Local Realtime");
    expect(realtimeProviderErrorMessage("LOCAL_BACKEND_NOT_READY_AFTER_UNLOAD"))
      .toContain("wake");
    expect(realtimeProviderErrorMessage("REALTIME_PERSONA_UNAVAILABLE"))
      .toContain("Persona");
    expect(realtimeProviderErrorMessage("provider secret leaked here"))
      .toBe("Realtime session failed.");
    expect(realtimeProviderErrorMessage("UNRECOGNIZED_INTERNAL_CODE"))
      .toContain("UNRECOGNIZED_INTERNAL_CODE");
  });
});
