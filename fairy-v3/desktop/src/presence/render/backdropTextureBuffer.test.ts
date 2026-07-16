import { describe, expect, it } from "vitest";

import { ReusableBackdropTextureBuffer } from "./backdropTextureBuffer";

describe("ReusableBackdropTextureBuffer", () => {
  it("keeps the same allocation for equal-sized frames", () => {
    const buffer = new ReusableBackdropTextureBuffer();
    expect(buffer.write(new Uint8Array(8).fill(3), 2, 1)).toBe(true);
    const allocation = buffer.data;

    expect(buffer.write(new Uint8Array(8).fill(7), 2, 1)).toBe(false);
    expect(buffer.data).toBe(allocation);
    expect(buffer.data).toEqual(new Uint8Array(8).fill(7));
  });

  it("reallocates only when dimensions change and rejects malformed frames", () => {
    const buffer = new ReusableBackdropTextureBuffer();
    buffer.write(new Uint8Array(8), 2, 1);
    const first = buffer.data;

    expect(buffer.write(new Uint8Array(16), 2, 2)).toBe(true);
    expect(buffer.data).not.toBe(first);
    expect(buffer.width).toBe(2);
    expect(buffer.height).toBe(2);
    expect(() => buffer.write(new Uint8Array(3), 1, 1)).toThrow(
      "PRESENCE_BACKDROP_TEXTURE_INVALID",
    );
  });
});
