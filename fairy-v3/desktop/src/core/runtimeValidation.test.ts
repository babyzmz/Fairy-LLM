import { describe, expect, it } from "vitest";

import { parseRuntimeResult } from "./runtimeValidation";

const id = "019f6c00-0000-7000-8000-000000000001";
const timestamp = "2026-07-22T00:00:00Z";

describe("runtime result validation", () => {
  it("accepts a bounded Preview activation response", () => {
    const payload = {
      outcome: "waiting_for_slot",
      context: null,
      adapter: "vite",
      capacity: 3,
      active_count: 3,
      evicted_preview_id: null,
      public_reason: "All Preview slots are currently protected.",
    };

    expect(parseRuntimeResult("previews.activate", payload)).toEqual(payload);
  });

  it("requires the persisted Preview access watermark", () => {
    const preview = {
      id,
      project_id: null,
      workspace_id: id,
      conversation_id: id,
      task_id: id,
      version_id: id,
      runtime_id: id,
      project_root: "C:/Fairy/version",
      execution_target: "local",
      url: null,
      visibility: "chat_draft",
      status: "stopped",
      health: "stopped",
      error_code: null,
      idempotency_key: "preview:test",
      revision: 1,
      created_at: timestamp,
      updated_at: timestamp,
    };

    expect(() => parseRuntimeResult("previews.stop", preview)).toThrow();
    expect(parseRuntimeResult("previews.stop", {
      ...preview,
      last_accessed_at: timestamp,
    })).toMatchObject({ last_accessed_at: timestamp });
  });
});
