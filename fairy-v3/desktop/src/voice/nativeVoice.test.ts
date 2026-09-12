import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const channels: Array<{ onmessage(value: unknown): void }> = [];
  class Channel<T> {
    onmessage: (value: T) => void = () => undefined;

    constructor() {
      channels.push(this as { onmessage(value: unknown): void });
    }
  }
  return { invoke: vi.fn(), channels, Channel,
    focusListeners: new Set<(value: { sequence: number; realtime_active: boolean }) => void>() };
});

vi.mock("./audioFocus", () => ({ subscribeAudioFocus: (callback: (value: { sequence: number; realtime_active: boolean }) => void) => {
  mocks.focusListeners.add(callback);
  return () => mocks.focusListeners.delete(callback);
} }));

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

import { startAmbientVoice, startNativeVoice } from "./nativeVoice";

describe("native voice channel", () => {
  beforeEach(() => {
    mocks.invoke.mockReset();
    mocks.channels.length = 0;
    mocks.focusListeners.clear();
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

  it("realtime preempts ordinary speech and ignores old PCM and stop handles", async () => {
    let next = 0;
    mocks.invoke.mockImplementation(async (command: string, args?: Record<string, unknown>) => {
      if (command === "desktop_preferences_get") return { voice_volume_percent: 80, voice_rate_percent: 100 };
      if (command === "voice_session_start") {
        const id = `focus-${++next}`;
        (args?.events as MockChannel<Record<string, unknown>>).onmessage({
          type: "started", session_id: id, sample_rate: 24_000, channels: 1, scope_digest: "c".repeat(64),
        });
        return { id };
      }
      return undefined;
    });
    const input = { task_id: "task-focus", turn_id: "turn-focus", message_id: "message-focus",
      start_offset: 0, end_offset: 4, idempotency_key: "voice:focus" };
    const old = await startNativeVoice(input);
    const oldPcm = mocks.channels[1];
    for (const deliver of [...mocks.focusListeners]) deliver({ sequence: 10, realtime_active: true });
    await expect(old.finished).resolves.toBeUndefined();
    expect(mocks.invoke).toHaveBeenCalledWith("voice_session_cancel", { sessionId: "focus-1" });
    const current = await startNativeVoice({ ...input, idempotency_key: "voice:focus-new" });
    const count = posted.length;
    oldPcm.onmessage(new Uint8Array([0, 0]));
    old.stop();
    expect(posted).toHaveLength(count);
    current.stop();
    await current.finished;
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

  it("uses the isolated ambient commands and exact projected text", async () => {
    mocks.invoke.mockImplementation(async (command: string, args?: Record<string, unknown>) => {
      if (command === "desktop_preferences_get") {
        return { voice_volume_percent: 80, voice_rate_percent: 100 };
      }
      if (command === "ambient_voice_start") {
        const events = args?.events as MockChannel<Record<string, unknown>>;
        events.onmessage({
          type: "started",
          session_id: "ambient-session",
          sample_rate: 24_000,
          channels: 1,
          scope_digest: "c".repeat(64),
        });
        return { id: "ambient-session" };
      }
      return undefined;
    });

    const playback = await startAmbientVoice("Exact reviewed text.");
    expect(mocks.invoke).toHaveBeenCalledWith(
      "ambient_voice_start",
      expect.objectContaining({ text: "Exact reviewed text." }),
    );
    playback.stop();
    expect(mocks.invoke).toHaveBeenCalledWith("ambient_voice_cancel", {
      sessionId: "ambient-session",
    });
  });
});
