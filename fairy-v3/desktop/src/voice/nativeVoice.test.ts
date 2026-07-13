import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const channels: Array<{ onmessage(value: unknown): void }> = [];
  class Channel<T> {
    onmessage: (value: T) => void = () => undefined;

    constructor() {
      channels.push(this as { onmessage(value: unknown): void });
    }
  }
  return { invoke: vi.fn(), channels, Channel };
});

type MockChannel<T> = { onmessage(value: T): void };

vi.mock("@tauri-apps/api/core", () => ({
  Channel: mocks.Channel,
  invoke: mocks.invoke,
  isTauri: () => true,
}));

const posted: unknown[] = [];
const port = {
  onmessage: null as ((event: MessageEvent) => void) | null,
  postMessage: vi.fn((message: unknown) => posted.push(message)),
};

class MockAudioContext {
  audioWorklet = { addModule: vi.fn(async () => undefined) };
  destination = {};
  resume = vi.fn(async () => undefined);
}

class MockAudioWorkletNode {
  port = port;
  connect = vi.fn();
}

vi.stubGlobal("AudioContext", MockAudioContext);
vi.stubGlobal("AudioWorkletNode", MockAudioWorkletNode);

import { startNativeVoice } from "./nativeVoice";

describe("native voice channel", () => {
  beforeEach(() => {
    mocks.invoke.mockReset();
    mocks.channels.length = 0;
    posted.length = 0;
    port.postMessage.mockClear();
    port.onmessage = null;
  });

  it("separates generation completion from AudioWorklet drain", async () => {
    mocks.invoke.mockImplementation(async (command: string, args?: Record<string, unknown>) => {
      if (command === "desktop_preferences_get") {
        return { voice_volume_percent: 80, voice_rate_percent: 100 };
      }
      if (command === "voice_session_start") {
        const events = args?.events as MockChannel<Record<string, unknown>>;
        const audio = args?.audio as MockChannel<ArrayBuffer | Uint8Array>;
        events.onmessage({
          type: "started",
          session_id: "session-1",
          sample_rate: 24_000,
          channels: 1,
          scope_digest: "a".repeat(64),
        });
        audio.onmessage(new Uint8Array([0, 0, 1, 0]));
        events.onmessage({ type: "completed", session_id: "session-1", pcm_bytes: 4 });
        return { id: "session-1" };
      }
      return undefined;
    });

    const playback = await startNativeVoice({
      task_id: "task-1",
      turn_id: "turn-1",
      message_id: "message-1",
      start_offset: 0,
      end_offset: 4,
      idempotency_key: "voice:test",
    });
    await expect(playback.readyForNext).resolves.toBeUndefined();
    let drained = false;
    void playback.finished.then(() => {
      drained = true;
    });
    await Promise.resolve();
    expect(drained).toBe(false);
    expect(posted).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "configure", sessionId: "session-1" }),
      expect.objectContaining({ type: "append", sessionId: "session-1" }),
      { type: "complete", sessionId: "session-1" },
    ]));
    const append = posted.find(
      (message) => (message as { type?: string }).type === "append",
    ) as { pcm: unknown };
    expect(append.pcm).toBeInstanceOf(ArrayBuffer);

    port.onmessage?.({ data: { type: "drained", sessionId: "session-1" } } as MessageEvent);
    await expect(playback.finished).resolves.toBeUndefined();
  });

  it("clears buffered audio before requesting cancellation", async () => {
    mocks.invoke.mockImplementation(async (command: string, args?: Record<string, unknown>) => {
      if (command === "desktop_preferences_get") {
        return { voice_volume_percent: 80, voice_rate_percent: 100 };
      }
      if (command === "voice_session_start") {
        const events = args?.events as MockChannel<Record<string, unknown>>;
        events.onmessage({
          type: "started",
          session_id: "session-stop",
          sample_rate: 24_000,
          channels: 1,
          scope_digest: "b".repeat(64),
        });
        return { id: "session-stop" };
      }
      return undefined;
    });
    const playback = await startNativeVoice({
      task_id: "task-1",
      turn_id: "turn-1",
      message_id: "message-1",
      start_offset: 0,
      end_offset: 4,
      idempotency_key: "voice:stop",
    });

    playback.stop();

    expect(posted.at(-1)).toEqual({ type: "clear" });
    expect(mocks.invoke).toHaveBeenCalledWith("voice_session_cancel", {
      sessionId: "session-stop",
    });
  });

  it("preserves a stable Tauri error code from a rejected native command", async () => {
    mocks.invoke.mockImplementation(async (command: string) => {
      if (command === "desktop_preferences_get") {
        return { voice_volume_percent: 80, voice_rate_percent: 100 };
      }
      if (command === "voice_session_start") throw "CORE_PROTOCOL_ERROR";
      return undefined;
    });

    await expect(startNativeVoice({
      task_id: "task-1",
      turn_id: "turn-1",
      message_id: "message-1",
      start_offset: 0,
      end_offset: 4,
      idempotency_key: "voice:error",
    })).rejects.toThrow("CORE_PROTOCOL_ERROR");
  });
});
