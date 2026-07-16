import { invoke, isTauri } from "@tauri-apps/api/core";

const BACKDROP_HEADER_BYTES = 32;
const BACKDROP_MAGIC = [0x46, 0x42, 0x47, 0x31] as const;

export interface NativeBackdropGeometry {
  visible: boolean;
  left: number;
  top: number;
  width: number;
  height: number;
  border_radius: number;
  device_pixel_ratio: number;
}

export interface NativeBackdropFrame {
  width: number;
  height: number;
  stride: number;
  sequence: number;
  capturedAtMs: number;
  rgba: Uint8Array;
}

interface NativeBackdropStreamOptions {
  framesPerSecond: number;
  geometry?: NativeBackdropGeometry;
  onFrame(frame: NativeBackdropFrame): void;
  onError?(error: unknown): void;
}

type NativeBackdropPayload = ArrayBuffer | Uint8Array;

export class NativeBackdropStream {
  private generation = 0;
  private framesPerSecond = 30;

  start(options: NativeBackdropStreamOptions): void {
    this.stop();
    this.framesPerSecond = boundedFrameRate(options.framesPerSecond);
    if (!isTauri()) return;
    const generation = this.generation;
    void this.pullFrames(generation, options);
  }

  setFrameRate(framesPerSecond: number): void {
    this.framesPerSecond = boundedFrameRate(framesPerSecond);
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
        const payload = await invoke<NativeBackdropPayload>("pet_backdrop_capture", {
          geometry: options.geometry ?? null,
          sequence,
        });
        const frame = parseNativeBackdropFrame(payload);
        if (frame.sequence > lastAcceptedSequence && generation === this.generation) {
          lastAcceptedSequence = frame.sequence;
          options.onFrame(frame);
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
  if (bytes.byteLength < BACKDROP_HEADER_BYTES) {
    throw new Error("PRESENCE_BACKDROP_PACKET_TRUNCATED");
  }
  for (let index = 0; index < BACKDROP_MAGIC.length; index += 1) {
    if (bytes[index] !== BACKDROP_MAGIC[index]) {
      throw new Error("PRESENCE_BACKDROP_PACKET_INVALID");
    }
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const width = view.getUint32(4, true);
  const height = view.getUint32(8, true);
  const stride = view.getUint32(12, true);
  const sequence = Number(view.getBigUint64(16, true));
  const capturedAtMs = Number(view.getBigUint64(24, true));
  const expectedBytes = width * height * 4;
  if (
    width < 1 ||
    height < 1 ||
    width > 1_024 ||
    height > 512 ||
    stride !== width * 4 ||
    !Number.isSafeInteger(sequence) ||
    !Number.isSafeInteger(capturedAtMs) ||
    bytes.byteLength !== BACKDROP_HEADER_BYTES + expectedBytes
  ) {
    throw new Error("PRESENCE_BACKDROP_PACKET_INVALID");
  }
  return {
    width,
    height,
    stride,
    sequence,
    capturedAtMs,
    rgba: bytes.subarray(BACKDROP_HEADER_BYTES),
  };
}

function boundedFrameRate(value: number): number {
  if (!Number.isFinite(value)) return 30;
  return Math.min(60, Math.max(15, Math.round(value)));
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
