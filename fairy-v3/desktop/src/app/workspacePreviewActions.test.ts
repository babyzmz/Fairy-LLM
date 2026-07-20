import { describe, expect, it } from "vitest";

import { previewStartIdempotencyKey } from "./workspacePreviewActions";

describe("previewStartIdempotencyKey", () => {
  it("reuses the durable key when restarting an interrupted Assistant Preview", () => {
    expect(
      previewStartIdempotencyKey("task-1", {
        status: "interrupted",
        idempotency_key: "assistant:preview:task-1:version-1",
      }),
    ).toBe("assistant:preview:task-1:version-1");
  });

  it("creates the desktop key only when no restartable Preview exists", () => {
    expect(previewStartIdempotencyKey("task-1", null)).toBe("desktop:preview:task-1");
    expect(
      previewStartIdempotencyKey("task-1", {
        status: "ready",
        idempotency_key: "assistant:preview:task-1:version-1",
      }),
    ).toBe("desktop:preview:task-1");
  });
});
