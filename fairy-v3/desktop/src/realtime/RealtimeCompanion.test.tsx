import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CoreClient, RealtimeWorkerStatus } from "../core/client";
import type { RealtimeSession } from "../core/contracts";
import { credentialProviderFor, RealtimeCompanion, mergeCaptionDelta } from "./RealtimeCompanion";

const invoke = vi.fn();
let eventListener: ((event: { payload: Record<string, unknown> }) => void) | null = null;

vi.mock("@tauri-apps/api/core", () => ({ invoke: (...args: unknown[]) => invoke(...args) }));
vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async (_name: string, listener: typeof eventListener) => {
    eventListener = listener;
    return () => { eventListener = null; };
  }),
}));
vi.mock("../voice/nativeVoice", () => ({
  startRealtimeVoice: vi.fn(async () => ({ finished: Promise.resolve() })),
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
  beforeEach(() => {
    eventListener = null;
    invoke.mockReset();
    localStorage.clear();
    localStorage.setItem("fairy.realtime.device-id", "test-device");
    invoke.mockImplementation(async (command: string) => {
      if (command === "desktop_preferences_get") return {
        realtime_provider: "auto",
        realtime_voice_mode: "native",
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

  it("keeps captions ephemeral and reports only aggregate media usage", async () => {
    const report = vi.fn(async (input: { status: RealtimeSession["status"] }) =>
      session(input.status, input.status === "active" ? 2 : 4));
    const stop = vi.fn(async () => session("stopping", 3));
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
        start: vi.fn(async () => session("starting", 1)),
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

    render(<RealtimeCompanion client={client} />);
    fireEvent.click(screen.getByTitle("Game companion"));
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
      type: "public_caption", session_id: session("active", 2).id, text: "Boss at ", stable: false,
    } }));
    await act(async () => eventListener?.({ payload: {
      type: "public_caption", session_id: session("active", 2).id, text: "half health", stable: false,
    } }));
    expect(screen.getByText("Boss at half health")).not.toBeNull();

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
    expect(JSON.stringify(report.mock.calls)).not.toContain("Boss at half health");
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

describe("mergeCaptionDelta", () => {
  it("supports both cumulative provider text and token deltas", () => {
    expect(mergeCaptionDelta("Boss", "Boss incoming")).toBe("Boss incoming");
    expect(mergeCaptionDelta("Boss ", "incoming")).toBe("Boss incoming");
    expect(mergeCaptionDelta("Boss incoming", "incoming")).toBe("Boss incoming");
  });
});
