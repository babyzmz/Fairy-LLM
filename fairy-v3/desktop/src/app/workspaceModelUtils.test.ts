import { describe, expect, it } from "vitest";

import type { Task } from "../core/client";
import { selectWorkspaceTask, workspaceDisplayError } from "./workspaceModelUtils";

describe("selectWorkspaceTask", () => {
  it("keeps an active Task bound while a new candidate is being produced", () => {
    const active = task("active", "planning", null, "2026-07-22T00:02:00Z");
    const ready = task("ready", "ready", "version-ready", "2026-07-22T00:01:00Z");

    expect(selectWorkspaceTask([ready, active], active.id)?.id).toBe(active.id);
  });

  it("restores the latest valid Preview after a later tool-only Task fails", () => {
    const ready = task("ready", "ready", "version-ready", "2026-07-22T00:01:00Z");
    const failed = task("failed", "failed", "version-failed", "2026-07-22T00:02:00Z");

    expect(selectWorkspaceTask([ready, failed], failed.id)?.id).toBe(ready.id);
  });

  it("falls back to the latest Task when no Preview can be restored", () => {
    const failed = task("failed", "failed", null, "2026-07-22T00:02:00Z");

    expect(selectWorkspaceTask([failed], null)?.id).toBe(failed.id);
  });
});

describe("workspaceDisplayError", () => {
  it("gives explicit action failures priority over Event Stream recovery state", () => {
    expect(workspaceDisplayError(
      { message: "Action failed", code: "VERSION_CONFLICT" },
      { message: "Event Stream offline", code: "TRANSPORT_ERROR" },
    )).toEqual({ message: "Action failed", code: "VERSION_CONFLICT" });
  });

  it("falls back to the Event Stream failure and clears when both sources recover", () => {
    expect(workspaceDisplayError(
      { message: null, code: null },
      { message: "Event Stream offline", code: "TRANSPORT_ERROR" },
    )).toEqual({ message: "Event Stream offline", code: "TRANSPORT_ERROR" });
    expect(workspaceDisplayError(
      { message: null, code: null },
      { message: null, code: null },
    )).toEqual({ message: null, code: null });
  });
});

function task(
  id: string,
  status: Task["status"],
  targetVersionId: string | null,
  updatedAt: string,
): Task {
  return {
    id,
    status,
    target_version_id: targetVersionId,
    updated_at: updatedAt,
  } as Task;
}
