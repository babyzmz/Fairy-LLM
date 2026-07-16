import { describe, expect, it } from "vitest";

import { parseNativeBackdropFrame } from "./nativeBackdrop";

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

describe("native Presence backdrop", () => {
  it("parses a bounded RGBA packet without copying its pixels", () => {
    const bytes = packet(2, 1);
    const frame = parseNativeBackdropFrame(bytes);
    expect(frame).toMatchObject({
      width: 2,
      height: 1,
      stride: 8,
      sequence: 7,
      capturedAtMs: 11,
    });
    expect(frame.rgba).toEqual(new Uint8Array([9, 9, 9, 9, 9, 9, 9, 9]));
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
});
