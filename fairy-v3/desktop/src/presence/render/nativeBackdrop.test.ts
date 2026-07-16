import { describe, expect, it, vi } from "vitest";

import {
  boundedBackdropFrameRate,
  NativeBackdropStream,
  parseNativeBackdropFrame,
} from "./nativeBackdrop";

function packet(width: number, height: number): Uint8Array {
  const bytes = new Uint8Array(32 + width * height * 4);
  bytes.set([0x46, 0x42, 0x47, 0x31]);
  const view = new DataView(bytes.buffer);
  view.setUint32(4, width, true);
  view.setUint32(8, height, true);
  view.setUint32(12, width * 4, true);
  view.setBigUint64(16, 7n, true);
  view.setBigUint64(24, 11n, true);
  bytes.fill(9, 32);
  return bytes;
}

function diagnosticPacket(width: number, height: number): Uint8Array {
  const bytes = new Uint8Array(64 + width * height * 4);
  bytes.set([0x46, 0x42, 0x47, 0x32]);
  const view = new DataView(bytes.buffer);
  view.setUint32(4, width, true);
  view.setUint32(8, height, true);
  view.setUint32(12, width * 4, true);
  view.setBigUint64(16, 8n, true);
  view.setBigUint64(24, 12n, true);
  view.setBigUint64(32, 4_500n, true);
  view.setBigUint64(40, 800n, true);
  view.setUint32(48, 2, true);
  view.setUint32(52, 64, true);
  view.setUint32(56, width, true);
  view.setUint32(60, height, true);
  bytes.fill(7, 64);
  return bytes;
}

describe("native Presence backdrop", () => {
  it("bounds transitional capture independently of animation fps", () => {
    expect(boundedBackdropFrameRate(5)).toBe(5);
    expect(boundedBackdropFrameRate(10)).toBe(10);
    expect(boundedBackdropFrameRate(60)).toBe(15);
    expect(boundedBackdropFrameRate(Number.NaN)).toBe(15);
  });

  it("parses a bounded RGBA packet without copying its pixels", () => {
    const bytes = packet(2, 1);
    const frame = parseNativeBackdropFrame(bytes);
    expect(frame).toMatchObject({
      width: 2,
      height: 1,
      stride: 8,
      sequence: 7,
      capturedAtMs: 11,
      kind: "captured",
      captureTotalMs: null,
    });
    expect(frame.rgba).toEqual(new Uint8Array([9, 9, 9, 9, 9, 9, 9, 9]));
    expect(frame.rgba.buffer).toBe(bytes.buffer);
  });

  it("parses diagnostic timing without copying the FBG2 payload", () => {
    const bytes = diagnosticPacket(2, 1);
    const frame = parseNativeBackdropFrame(bytes);
    expect(frame).toMatchObject({
      sequence: 8,
      kind: "synthetic",
      captureTotalMs: 4.5,
      framePackMs: 0.8,
      sourceWidth: 2,
      sourceHeight: 1,
    });
    expect(frame.rgba.buffer).toBe(bytes.buffer);
  });

  it("rejects malformed lengths and headers", () => {
    expect(() => parseNativeBackdropFrame(packet(2, 1).subarray(0, 35))).toThrow(
      "PRESENCE_BACKDROP_PACKET_INVALID",
    );
    const invalid = packet(1, 1);
    invalid[0] = 0;
    expect(() => parseNativeBackdropFrame(invalid)).toThrow(
      "PRESENCE_BACKDROP_PACKET_INVALID",
    );
  });

  it("captures exactly one frame in static mode and sends a bounded native mode", async () => {
    const request = vi.fn(async () => diagnosticPacket(2, 1));
    const onFrame = vi.fn();
    const stream = new NativeBackdropStream(request, () => true);

    stream.start({
      framesPerSecond: 60,
      experimentMode: "static-backdrop",
      onFrame,
    });

    await vi.waitFor(() => expect(onFrame).toHaveBeenCalledOnce());
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(request).toHaveBeenCalledOnce();
    expect(request).toHaveBeenCalledWith({
      sequence: 0,
      experimentMode: "normal",
    });
    stream.stop();
  });
});
