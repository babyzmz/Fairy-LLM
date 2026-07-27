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
let realtimeVoiceMode: "native" | "fairy" = "native";

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
  audio_input_ms: 1_250,
  audio_output_ms: 400,
  video_frame_count: 8,
  interruption_count: 1,
  tool_call_count: 0,
});

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
    realtimeVoiceMode = "native";
    localStorage.clear();
    localStorage.setItem("fairy.realtime.device-id", "test-device");
    invoke.mockImplementation(async (command: string) => {
      if (command === "desktop_preferences_get") return {
        realtime_provider: "auto",
        realtime_voice_mode: realtimeVoiceMode,
        realtime_game_audio_default: false,
        realtime_memory_enabled: true,
        realtime_max_session_minutes: 30,
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
        status: vi.fn(async () => workerStatus(false)),
      },
    } as unknown as CoreClient["realtime"];

    const { rerender } = render(
      <RealtimeCompanion client={client} openRequest={0} />,
    );

    expect(screen.queryByTitle("Game companion")).toBeNull();
    expect(screen.queryByRole("dialog", { name: "Game companion" })).toBeNull();

    rerender(<RealtimeCompanion client={client} openRequest={1} />);

    expect(
      await screen.findByRole("dialog", { name: "Game companion" }),
    ).not.toBeNull();
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
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(async () => workerStatus(false)),
        toolResult: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    const consents = screen.getAllByRole("checkbox");
    fireEvent.click(consents[0]);
    fireEvent.click(consents[1]);
    fireEvent.click(screen.getByRole("button", { name: "Start companion" }));
    await waitFor(() => expect(client.worker.start).toHaveBeenCalledOnce());

    await act(async () => eventListener?.({ payload: {
      type: "session_state", session_id: session("active", 2).id, status: "active",
    } }));
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
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(),
        toolResult: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: /Test Game/ });
    const consents = screen.getAllByRole("checkbox");
    fireEvent.click(consents[0]);
    fireEvent.click(consents[1]);
    fireEvent.click(screen.getByRole("button", { name: "Start companion" }));
    await waitFor(() => expect(client.worker.start).toHaveBeenCalledOnce());
    await act(async () => eventListener?.({ payload: {
      type: "session_state", session_id: session("active", 2).id, status: "active",
    } }));
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
    expect(screen.getByText("Active")).not.toBeNull();

    append.mockResolvedValue({});
    fireEvent.click(screen.getByRole("button", { name: "Retry saving" }));

    await waitFor(() => expect(append).toHaveBeenCalledTimes(4));
    await waitFor(() => expect(screen.queryByText("1 caption unsaved")).toBeNull());
    expect(screen.getByText("Active")).not.toBeNull();
  });

  it("gates microphone and screen input through the collapsible session controls", async () => {
    const setInput = vi.fn(async () => undefined);
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
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(),
        toolResult: vi.fn(),
        setInput,
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    const consents = screen.getAllByRole("checkbox");
    fireEvent.click(consents[0]);
    fireEvent.click(consents[1]);
    fireEvent.click(screen.getByRole("button", { name: "Start companion" }));
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
    await waitFor(() => expect(setInput).toHaveBeenLastCalledWith(
      expect.objectContaining({ microphone: false, video: false }),
    ));
  });

  it("no longer offers a game or system audio consent control", async () => {
    const client = {
      sessions: { list: vi.fn(async () => ({ items: [] })) },
      memories: { save: vi.fn() },
      worker: { status: vi.fn(async () => workerStatus(false)) },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    // Only microphone and window-image consent remain.
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    expect(screen.queryByText(/game audio/i)).toBeNull();
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
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(async () => workerStatus(false)),
        toolResult: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    const consents = screen.getAllByRole("checkbox");
    fireEvent.click(consents[0]);
    fireEvent.click(consents[1]);
    fireEvent.click(screen.getByRole("button", { name: "Start companion" }));
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
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(async () => workerStatus(false)),
        toolResult: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    const consents = screen.getAllByRole("checkbox");
    fireEvent.click(consents[0]);
    fireEvent.click(consents[1]);
    fireEvent.click(screen.getByRole("button", { name: "Start companion" }));
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
    realtimeVoiceMode = "fairy";
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
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(),
        toolResult: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    const consents = screen.getAllByRole("checkbox");
    fireEvent.click(consents[0]);
    fireEvent.click(consents[1]);
    fireEvent.click(screen.getByRole("button", { name: "Start companion" }));
    await waitFor(() => expect(client.worker.start).toHaveBeenCalledOnce());
    await act(async () => eventListener?.({ payload: {
      type: "session_state", session_id: session("active", 2).id, status: "active",
    } }));
    await act(async () => eventListener?.({ payload: {
      type: "public_caption",
      session_id: session("active", 2).id,
      text: "Watch the next attack.",
      stable: true,
      speaker: "assistant",
    } }));
    await waitFor(() => expect(voiceMocks.startRealtimeVoice).toHaveBeenCalledOnce());
    await act(async () => eventListener?.({ payload: {
      type: "public_caption",
      session_id: session("active", 2).id,
      text: "This queued sentence must be discarded.",
      stable: true,
      speaker: "assistant",
    } }));

    await act(async () => eventListener?.({ payload: {
      type: "barge_in", session_id: session("active", 2).id,
    } }));

    expect(stopPlayback).toHaveBeenCalledOnce();
    expect(screen.getByText("Listening")).not.toBeNull();
    await act(async () => {
      finishPlayback();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(voiceMocks.startRealtimeVoice).toHaveBeenCalledOnce();
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
        status: vi.fn(async () => workerStatus(false)),
        start: vi.fn(async () => workerStatus(true)),
        stop: vi.fn(),
        toolResult: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanion client={client} openRequest={1} />);
    await screen.findByRole("option", { name: "Test Game · 1280×720" });
    const consents = screen.getAllByRole("checkbox");
    fireEvent.click(consents[0]);
    fireEvent.click(consents[1]);
    fireEvent.click(screen.getByRole("button", { name: "Start companion" }));
    await waitFor(() => expect(client.worker.start).toHaveBeenCalledOnce());

    await act(async () => eventListener?.({ payload: {
      type: "session_state", session_id: session("active", 2).id, status: "active",
    } }));

    expect(await screen.findByText("Realtime state could not be saved.")).not.toBeNull();
    expect(get).toHaveBeenCalledWith(session("active", 2).id);
  });
});

describe("credentialProviderFor", () => {
  it("selects the account used by Auto and manual provider modes", () => {
    expect(credentialProviderFor("auto", "zh-CN")).toBe("zhipu");
    expect(credentialProviderFor("auto", "en-AU")).toBe("gemini");
    expect(credentialProviderFor("glm_realtime_air", "en-AU")).toBe("zhipu");
    expect(credentialProviderFor("gemini_live", "zh-CN")).toBe("gemini");
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
