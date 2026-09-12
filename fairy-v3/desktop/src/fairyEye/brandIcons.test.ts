// @vitest-environment node
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { PNG } from "pngjs";
import { describe, expect, it } from "vitest";

describe("Fairy brand assets", () => {
  it("ships a self-contained SVG with no active flicker or motion", () => {
    const path = resolve("src/fairyEye/brand.svg");
    expect(existsSync(path)).toBe(true);
    const svg = readFileSync(path, "utf8");
    expect(svg).not.toMatch(/<script|<animate|<set\b|var\(--/);
    expect(svg).toMatch(/class="dsh-fairy-eye-flicker" display="none"/);
    expect(svg).toMatch(/class="dsh-fairy-glitch-blocks" display="none"/);
    const ids = new Set([...svg.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]));
    for (const match of svg.matchAll(/(?:url\(#|href="#)([^)"\s]+)/g)) {
      expect(ids.has(match[1])).toBe(true);
    }
  });

  it("provides transparent blue-eye pixels at Windows icon and DPI sizes", () => {
    const ico = readFileSync(resolve("src-tauri/icons/icon.ico"));
    expect(ico.readUInt16LE(2)).toBe(1);
    const count = ico.readUInt16LE(4);
    const sizes = Array.from({ length: count }, (_, index) => ico[6 + index * 16] || 256);
    expect(sizes).toEqual([16, 20, 24, 32, 40, 48, 64, 128, 256]);
    for (let index = 0; index < count; index++) {
      const entry = 6 + index * 16;
      const bytes = ico.readUInt32LE(entry + 8), offset = ico.readUInt32LE(entry + 12);
      const png = PNG.sync.read(ico.subarray(offset, offset + bytes));
      const size = sizes[index];
      expect([png.width, png.height]).toEqual([size, size]);
      expect(png.data[3]).toBe(0);
      const center = (Math.floor(size / 2) * size + Math.floor(size / 2)) * 4;
      expect(png.data[center + 3]).toBe(255);
      expect(png.data[center + 2]).toBeGreaterThan(png.data[center] + 20);
      // Sclera spans y=32..47 on the 160px source; .3 is already the blue iris.
      const white = (Math.floor(size * .24) * size + Math.floor(size / 2)) * 4;
      expect(png.data[white]).toBeGreaterThan(180);
      if (size <= 48) {
        // Optical small icons fill their canvas, not a halo-padded 160px crop.
        const nearTop = (1 * size + Math.floor(size / 2)) * 4;
        expect(png.data[nearTop + 3]).toBeGreaterThan(240);
      }
    }
  });
});
