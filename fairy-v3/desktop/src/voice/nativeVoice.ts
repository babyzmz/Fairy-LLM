import { Channel, invoke, isTauri } from "@tauri-apps/api/core";

import type { VoiceSession, VoiceSessionStartInput } from "../core/client";
import type { DesktopPreferences } from "../settings/client";
import { publishPresenceVoiceLevel } from "../presence/transport/voiceLevelEvents";
import workletUrl from "./fairy-pcm-worklet.js?url&no-inline";

export type NativeVoiceEvent =
  | { type: "started"; session_id: string; sample_rate: number; channels: number; scope_digest: string }
  | { type: "progress"; session_id: string; pcm_bytes: number }
  | { type: "completed"; session_id: string; pcm_bytes: number }
  | { type: "cancelled"; session_id: string }
  | { type: "failed"; session_id: string; error_code: string; message: string };

type NativePcmChunk = ArrayBuffer | Uint8Array;

export interface NativeVoicePlayback {
  readonly readyForNext: Promise<void>;
  readonly finished: Promise<void>;
  stop(): void;
}

interface WorkletEvent {
  type: "drained" | "overflow" | "level";
  sessionId: string | null;
  level?: number;
}

interface PendingPlayback {
  sessionId: string | null;
  terminal: boolean;
  receivedBytes: number;
  completedBytes: number | null;
  completionPosted: boolean;
  queuedPcm: ArrayBuffer[];
  readyForNext: Promise<void>;
  finished: Promise<void>;
  resolveReady(): void;
  rejectReady(error: Error): void;
  resolveFinished(): void;
  rejectFinished(error: Error): void;
}

class NativeVoiceHost {
  private context: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private readonly pending = new Map<string, PendingPlayback>();

  async start(input: VoiceSessionStartInput): Promise<NativeVoicePlayback> {
    return this.startCommand("voice_session_start", "voice_session_cancel", { input });
  }

  async test(): Promise<NativeVoicePlayback> {
    return this.startCommand("voice_test_start", "voice_test_cancel", {});
  }

  async realtime(text: string): Promise<NativeVoicePlayback> {
    return this.startCommand("realtime_voice_start", "realtime_voice_cancel", { text });
  }

  private async startCommand(
    command: "voice_session_start" | "voice_test_start" | "realtime_voice_start",
    cancelCommand: "voice_session_cancel" | "voice_test_cancel" | "realtime_voice_cancel",
    args: Record<string, unknown>,
  ): Promise<NativeVoicePlayback> {
    const node = await this.readyNode();
    const preferences = await invoke<DesktopPreferences>("desktop_preferences_get");
    await this.context?.resume();
    const playback = pendingPlayback();
    const events = new Channel<NativeVoiceEvent>();
    const audio = new Channel<NativePcmChunk>();
    events.onmessage = (event) => {
      if (event.type === "started") {
        this.attachSession(playback, event.session_id);
        node.port.postMessage({
          type: "configure",
          sessionId: event.session_id,
          sampleRate: event.sample_rate,
          volume: preferences.voice_volume_percent / 100,
          rate: preferences.voice_rate_percent / 100,
        });
        for (const pcm of playback.queuedPcm.splice(0)) {
          this.appendPcm(node, playback, pcm);
        }
      } else if (event.type === "completed") {
        playback.completedBytes = event.pcm_bytes;
        playback.resolveReady();
        this.maybeComplete(node, playback);
      } else if (event.type === "cancelled") {
        playback.terminal = true;
        playback.resolveReady();
        playback.resolveFinished();
        this.stopAll(node);
      } else if (event.type === "failed") {
        playback.terminal = true;
        const error = new Error(event.error_code || event.message);
        playback.rejectReady(error);
        playback.rejectFinished(error);
        this.failAll(node, error);
      }
    };
    audio.onmessage = (chunk) => {
      const pcm = transferablePcm(chunk);
      if (playback.sessionId === null) playback.queuedPcm.push(pcm);
      else this.appendPcm(node, playback, pcm);
    };
    let session: VoiceSession;
    try {
      session = await invoke<VoiceSession>(command, { ...args, audio, events });
    } catch (error) {
      const failure = normalizedError(error, "VOICE_WORKER_UNAVAILABLE");
      playback.rejectReady(failure);
      playback.rejectFinished(failure);
      throw failure;
    }
    this.attachSession(playback, session.id);
    return {
      readyForNext: playback.readyForNext,
      finished: playback.finished,
      stop: () => {
        this.stopAll(node);
        void invoke(cancelCommand, { sessionId: session.id });
      },
    };
  }

  private attachSession(playback: PendingPlayback, sessionId: string): void {
    if (playback.sessionId !== null && playback.sessionId !== sessionId) {
      this.failAll(this.node, new Error("VOICE_SESSION_SCOPE_MISMATCH"));
      return;
    }
    playback.sessionId = sessionId;
    if (!playback.terminal) this.pending.set(sessionId, playback);
  }

  private appendPcm(
    node: AudioWorkletNode,
    playback: PendingPlayback,
    pcm: ArrayBuffer,
  ): void {
    playback.receivedBytes += pcm.byteLength;
    node.port.postMessage(
      { type: "append", sessionId: playback.sessionId, pcm },
      [pcm],
    );
    this.maybeComplete(node, playback);
  }

  private maybeComplete(node: AudioWorkletNode, playback: PendingPlayback): void {
    if (
      playback.sessionId === null ||
      playback.completedBytes === null ||
      playback.receivedBytes < playback.completedBytes ||
      playback.completionPosted
    ) return;
    playback.completionPosted = true;
    node.port.postMessage({ type: "complete", sessionId: playback.sessionId });
  }

  private stopAll(node: AudioWorkletNode): void {
    node.port.postMessage({ type: "clear" });
    for (const playback of this.pending.values()) {
      playback.terminal = true;
      playback.resolveReady();
      playback.resolveFinished();
    }
    this.pending.clear();
  }

  private failAll(node: AudioWorkletNode | null, error: Error): void {
    node?.port.postMessage({ type: "clear" });
    for (const playback of this.pending.values()) {
      playback.terminal = true;
      playback.rejectReady(error);
      playback.rejectFinished(error);
    }
    this.pending.clear();
  }

  private async readyNode(): Promise<AudioWorkletNode> {
    if (this.node !== null) return this.node;
    const context = new AudioContext({ latencyHint: "interactive" });
    await context.audioWorklet.addModule(workletUrl);
    const node = new AudioWorkletNode(context, "fairy-pcm-ring", {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    node.port.onmessage = (event: MessageEvent<WorkletEvent>) => {
      if (event.data.type === "level") {
        publishPresenceVoiceLevel(event.data.level ?? 0);
        return;
      }
      const sessionId = event.data.sessionId;
      if (sessionId === null) return;
      const playback = this.pending.get(sessionId);
      if (playback === undefined) return;
      if (event.data.type === "drained") {
        publishPresenceVoiceLevel(0);
        playback.resolveFinished();
        this.pending.delete(sessionId);
      } else {
        this.failAll(node, new Error("VOICE_AUDIO_BUFFER_OVERFLOW"));
      }
    };
    node.connect(context.destination);
    this.context = context;
    this.node = node;
    return node;
  }
}

function pendingPlayback(): PendingPlayback {
  const ready = deferred();
  const finished = deferred();
  void ready.promise.catch(() => undefined);
  void finished.promise.catch(() => undefined);
  return {
    sessionId: null,
    terminal: false,
    receivedBytes: 0,
    completedBytes: null,
    completionPosted: false,
    queuedPcm: [],
    readyForNext: ready.promise,
    finished: finished.promise,
    resolveReady: ready.resolve,
    rejectReady: ready.reject,
    resolveFinished: finished.resolve,
    rejectFinished: finished.reject,
  };
}

function deferred(): {
  promise: Promise<void>;
  resolve(): void;
  reject(error: Error): void;
} {
  let settled = false;
  let resolvePromise!: () => void;
  let rejectPromise!: (error: Error) => void;
  const promise = new Promise<void>((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  return {
    promise,
    resolve: () => {
      if (settled) return;
      settled = true;
      resolvePromise();
    },
    reject: (error) => {
      if (settled) return;
      settled = true;
      rejectPromise(error);
    },
  };
}

function transferablePcm(chunk: NativePcmChunk): ArrayBuffer {
  if (chunk instanceof ArrayBuffer) return chunk;
  const copy = new Uint8Array(chunk.byteLength);
  copy.set(chunk);
  return copy.buffer;
}

function normalizedError(error: unknown, fallback: string): Error {
  if (error instanceof Error) return error;
  if (typeof error === "string" && error.trim() !== "") return new Error(error);
  return new Error(fallback);
}

let sharedHost: NativeVoiceHost | null = null;

export function nativeVoiceAvailable(): boolean {
  return isTauri() && typeof AudioContext !== "undefined" && typeof AudioWorkletNode !== "undefined";
}

export function startNativeVoice(input: VoiceSessionStartInput): Promise<NativeVoicePlayback> {
  sharedHost ??= new NativeVoiceHost();
  return sharedHost.start(input);
}

export function startNativeVoiceTest(): Promise<NativeVoicePlayback> {
  sharedHost ??= new NativeVoiceHost();
  return sharedHost.test();
}

export function startRealtimeVoice(text: string): Promise<NativeVoicePlayback> {
  sharedHost ??= new NativeVoiceHost();
  return sharedHost.realtime(text);
}
