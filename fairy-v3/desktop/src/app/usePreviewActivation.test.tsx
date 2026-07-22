import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PreviewActivation, Task, Workspace } from "../core/client";
import type { WorkspaceClient } from "./workspaceTypes";
import { usePreviewActivation } from "./usePreviewActivation";

const firstTaskId = "019f6c00-0000-7000-8000-000000000001";
const secondTaskId = "019f6c00-0000-7000-8000-000000000002";
const workspaceId = "019f6c00-0000-7000-8000-000000000003";
const versionId = "019f6c00-0000-7000-8000-000000000004";

afterEach(() => vi.restoreAllMocks());

describe("usePreviewActivation", () => {
  it("activates a selected runnable Workspace once across equivalent rerenders", async () => {
    const activate = vi.fn(async () => activation("ready", "vite"));
    const client = previewClient(activate);
    const hook = renderHook(
      ({ task, workspace }: { task: Task; workspace: Workspace }) => usePreviewActivation({
        client,
        enabled: true,
        task,
        workspace,
      }),
      { initialProps: { task: task(firstTaskId), workspace: workspace(4) } },
    );

    await waitFor(() => expect(hook.result.current.activation?.outcome).toBe("ready"));
    expect(activate).toHaveBeenCalledWith({
      task_id: firstTaskId,
      workspace_id: workspaceId,
      version_id: versionId,
      expected_workspace_revision: 4,
      idempotency_key: `desktop:preview-activate:${firstTaskId}:${versionId}`,
    });

    hook.rerender({ task: task(firstTaskId), workspace: workspace(4) });
    await act(async () => Promise.resolve());
    expect(activate).toHaveBeenCalledTimes(1);
  });

  it("ignores a late activation result after switching conversations", async () => {
    const first = deferred<PreviewActivation>();
    const second = deferred<PreviewActivation>();
    const activate = vi.fn((input: { task_id: string }) =>
      input.task_id === firstTaskId ? first.promise : second.promise,
    );
    const hook = renderHook(
      ({ selectedTask }: { selectedTask: Task }) => usePreviewActivation({
        client: previewClient(activate),
        enabled: true,
        task: selectedTask,
        workspace: workspace(1),
      }),
      { initialProps: { selectedTask: task(firstTaskId) } },
    );

    await waitFor(() => expect(activate).toHaveBeenCalledTimes(1));
    hook.rerender({ selectedTask: task(secondTaskId) });
    await waitFor(() => expect(activate).toHaveBeenCalledTimes(2));

    await act(async () => second.resolve(activation("ready", "astro")));
    await waitFor(() => expect(hook.result.current.activation?.adapter).toBe("astro"));
    await act(async () => first.resolve(activation("ready", "vite")));
    expect(hook.result.current.activation?.adapter).toBe("astro");
  });
});

function previewClient(
  activate: (input: never) => Promise<PreviewActivation>,
): WorkspaceClient["previews"] {
  return { activate } as unknown as WorkspaceClient["previews"];
}

function task(id: string): Task {
  return {
    id,
    workspace_id: workspaceId,
    target_version_id: versionId,
  } as Task;
}

function workspace(revision: number): Workspace {
  return { id: workspaceId, revision } as Workspace;
}

function activation(
  outcome: PreviewActivation["outcome"],
  adapter: string,
): PreviewActivation {
  return {
    outcome,
    context: null,
    adapter,
    capacity: 3,
    active_count: 1,
    evicted_preview_id: null,
    public_reason: null,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}
