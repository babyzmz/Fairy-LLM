import { invoke, isTauri } from "@tauri-apps/api/core";

import {
  backdropCommandMode,
  type PresenceExperimentMode,
} from "../diagnostics/experimentMode";

const BACKDROP_V1_HEADER_BYTES = 32;
const BACKDROP_V1_MAGIC = [0x46, 0x42, 0x47, 0x31] as const;
const BACKDROP_V2_HEADER_BYTES = 64;
const BACKDROP_V2_MAGIC = [0x46, 0x42, 0x47, 0x32] as const;

export interface NativeBackdropFrame {
  width: number;
  height: number;
  stride: number;
  sequence: number;
  capturedAtMs: number;
  rgba: Uint8Array;
  sourceWidth: number;
  sourceHeight: number;
  kind: "captured" | "capture-only" | "synthetic";
  captureTotalMs: number | null;
  framePackMs: number | null;
  ipcRoundtripMs: number;
  jsParseMs: number;
}

interface NativeBackdropStreamOptions {
  framesPerSecond: number;
  experimentMode?: PresenceExperimentMode;
  onFrame(frame: NativeBackdropFrame): void;
  onError?(error: unknown): void;
}

type NativeBackdropPayload = ArrayBuffer | Uint8Array;
type NativeBackdropRequest = (input: {
  sequence: number;
  experimentMode: "normal" | "capture-only" | "ipc-upload-only";
}) => Promise<NativeBackdropPayload>;

export class NativeBackdropStream {
  private generation = 0;
  private framesPerSecond = 15;

  constructor(
    private readonly request: NativeBackdropRequest = (input) =>
      invoke<NativeBackdropPayload>("pet_backdrop_capture", input),
    private readonly nativeAvailable: () => boolean = isTauri,
  ) {}

  start(options: NativeBackdropStreamOptions): void {
    this.stop();
    this.framesPerSecond = boundedBackdropFrameRate(options.framesPerSecond);
    if (!this.nativeAvailable()) return;
    const generation = this.generation;
    void this.pullFrames(generation, options);
  }

  setFrameRate(framesPerSecond: number): void {
    this.framesPerSecond = boundedBackdropFrameRate(framesPerSecond);
  }

  stop(): void {
    this.generation += 1;
  }

  private async pullFrames(
    generation: number,
    options: NativeBackdropStreamOptions,
  ): Promise<void> {
    let sequence = 0;
    let lastAcceptedSequence = -1;
    while (generation === this.generation) {
      const startedAt = performance.now();
      try {
        const payload = await this.request({
          sequence,
          experimentMode: backdropCommandMode(options.experimentMode ?? "normal"),
        });
        const receivedAt = performance.now();
        const frame = parseNativeBackdropFrame(payload);
        const parsedAt = performance.now();
        frame.ipcRoundtripMs = receivedAt - startedAt;
        frame.jsParseMs = parsedAt - receivedAt;
        if (frame.sequence > lastAcceptedSequence && generation === this.generation) {
          lastAcceptedSequence = frame.sequence;
          options.onFrame(frame);
          if (options.experimentMode === "static-backdrop") return;
        }
        sequence += 1;
      } catch (error) {
        if (generation !== this.generation) return;
        options.onError?.(error);
        const retryDelay = String(error).includes("PRESENCE_BACKDROP_WINDOW_MOVED")
          ? 0
          : 100;
        if (retryDelay > 0) await delay(retryDelay);
      }
      const interval = 1_000 / this.framesPerSecond;
      const remaining = interval - (performance.now() - startedAt);
      if (remaining > 0) await delay(remaining);
    }
  }
}

export function parseNativeBackdropFrame(
  payload: NativeBackdropPayload,
): NativeBackdropFrame {
  const bytes = payload instanceof Uint8Array ? payload : new Uint8Array(payload);
  if (bytes.byteLength < BACKDROP_V1_HEADER_BYTES) {
    throw new Error("PRESENCE_BACKDROP_PACKET_TRUNCATED");
  }
  const version = matchesMagic(bytes, BACKDROP_V2_MAGIC)
    ? 2
    : matchesMagic(bytes, BACKDROP_V1_MAGIC) ? 1 : 0;
  if (version === 0) throw new Error("PRESENCE_BACKDROP_PACKET_INVALID");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const width = view.getUint32(4, true);
  const height = view.getUint32(8, true);
  const stride = view.getUint32(12, true);
  const sequence = Number(view.getBigUint64(16, true));
  const capturedAtMs = Number(view.getBigUint64(24, true));
  const expectedHeaderBytes = version === 2
    ? BACKDROP_V2_HEADER_BYTES
    : BACKDROP_V1_HEADER_BYTES;
  const headerBytes = version === 2 ? view.getUint32(52, true) : expectedHeaderBytes;
  const flags = version === 2 ? view.getUint32(48, true) : 0;
  const sourceWidth = version === 2 ? view.getUint32(56, true) : width;
  const sourceHeight = version === 2 ? view.getUint32(60, true) : height;
  const expectedBytes = width * height * 4;
  if (
    width < 1 ||
    height < 1 ||
    width > 1_024 ||
    height > 512 ||
    stride !== width * 4 ||
    !Number.isSafeInteger(sequence) ||
    !Number.isSafeInteger(capturedAtMs) ||
    headerBytes !== expectedHeaderBytes ||
    sourceWidth < 1 ||
    sourceHeight < 1 ||
    bytes.byteLength !== headerBytes + expectedBytes
  ) {
    throw new Error("PRESENCE_BACKDROP_PACKET_INVALID");
  }
  return {
    width,
    height,
    stride,
    sequence,
    capturedAtMs,
    rgba: bytes.subarray(headerBytes),
    sourceWidth,
    sourceHeight,
    kind: flags === 1 ? "capture-only" : flags === 2 ? "synthetic" : "captured",
    captureTotalMs: version === 2 ? microsecondsToMilliseconds(view.getBigUint64(32, true)) : null,
    framePackMs: version === 2 ? microsecondsToMilliseconds(view.getBigUint64(40, true)) : null,
    ipcRoundtripMs: 0,
    jsParseMs: 0,
  };
}

function matchesMagic(bytes: Uint8Array, magic: readonly number[]): boolean {
  return magic.every((value, index) => bytes[index] === value);
}

function microsecondsToMilliseconds(value: bigint): number | null {
  const numeric = Number(value);
  return Number.isSafeInteger(numeric) ? numeric / 1_000 : null;
}

export function boundedBackdropFrameRate(value: number): number {
  if (!Number.isFinite(value)) return 15;
  return Math.min(15, Math.max(5, Math.round(value)));
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
