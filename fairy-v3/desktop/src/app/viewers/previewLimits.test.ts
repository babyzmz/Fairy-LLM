import { describe, expect, it } from "vitest";

import {
  assertImagePreviewBudget,
  assertModelFileSetBudget,
  assertScenePreviewBudget,
  pdfCanvasScale,
} from "./previewLimits";

describe("built-in preview resource limits", () => {
  it("rejects model source fan-out above the aggregate byte budget", () => {
    expect(() => assertModelFileSetBudget({
      members: [
        { byte_length: 80 * 1024 * 1024 },
        { byte_length: 50 * 1024 * 1024 },
      ],
    })).toThrow(/128 MiB/);
  });

  it("rejects excessive decoded scene and image complexity", () => {
    expect(() => assertScenePreviewBudget({
      objects: 25_001,
      meshes: 1,
      triangles: 1,
    })).toThrow(/object limit/);
    expect(() => assertImagePreviewBudget(16_384, 16_384)).toThrow(/64 megapixels/);
  });

  it("bounds PDF backing canvases and rejects an oversized CSS page", () => {
    expect(pdfCanvasScale(2_000, 2_000, 2)).toBeCloseTo(2, 5);
    expect(pdfCanvasScale(4_000, 4_000, 2)).toBeCloseTo(1, 5);
    expect(() => pdfCanvasScale(5_000, 4_000, 1)).toThrow(/page dimensions/);
  });
});
