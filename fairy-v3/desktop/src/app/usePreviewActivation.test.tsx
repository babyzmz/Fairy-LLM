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

  it("retries automatically after a transient first activation failure", async () => {
    vi.useFakeTimers();
    try {
      let attempt = 0;
      const activate = vi.fn(async () => {
        attempt += 1;
        if (attempt === 1) throw new Error("transient activation failure");
        return activation("ready", "vite");
      });
      const hook = renderHook(() => usePreviewActivation({
        client: previewClient(activate),
        enabled: true,
        task: task(firstTaskId),
        workspace: workspace(4),
      }));

      // The first automatic attempt fails and surfaces an error.
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(activate).toHaveBeenCalledTimes(1);
      expect(hook.result.current.error).not.toBeNull();

      // The bounded error retry fires and recovers without a task reselection.
      await act(async () => { await vi.advanceTimersByTimeAsync(8_000); });
      expect(activate).toHaveBeenCalledTimes(2);
      expect(hook.result.current.activation?.outcome).toBe("ready");
      expect(hook.result.current.error).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops after the initial activation and two automatic retries", async () => {
    vi.useFakeTimers();
    try {
      const activate = vi.fn(async () => {
        throw new Error("preview host is offline");
      });
      const hook = renderHook(() => usePreviewActivation({
        client: previewClient(activate),
        enabled: true,
        task: task(firstTaskId),
        workspace: workspace(4),
      }));

      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      await act(async () => { await vi.advanceTimersByTimeAsync(8_000); });
      await act(async () => { await vi.advanceTimersByTimeAsync(16_000); });
      expect(activate).toHaveBeenCalledTimes(3);
      expect(hook.result.current.error).toBe("preview host is offline");

      await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
      expect(activate).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([
    "CAPABILITY_NOT_AVAILABLE",
    "PERMISSION_DENIED",
    "VALIDATION_ERROR",
    "NOT_FOUND",
    "VERSION_CONFLICT",
  ])("does not retry terminal Core error %s", async (errorCode) => {
    vi.useFakeTimers();
    try {
      const activate = vi.fn(async () => {
        throw Object.assign(new Error(`terminal ${errorCode}`), { errorCode });
      });
      renderHook(() => usePreviewActivation({
        client: previewClient(activate),
        enabled: true,
        task: task(firstTaskId),
        workspace: workspace(4),
      }));

      await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
      expect(activate).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
  });

  it("lets an explicit retry reset an exhausted transient budget", async () => {
    vi.useFakeTimers();
    try {
      const activate = vi.fn().mockRejectedValue(new Error("offline"));
      const hook = renderHook(() => usePreviewActivation({
        client: previewClient(activate),
        enabled: true,
        task: task(firstTaskId),
        workspace: workspace(4),
      }));

      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      await act(async () => { await vi.advanceTimersByTimeAsync(8_000); });
      await act(async () => { await vi.advanceTimersByTimeAsync(16_000); });
      expect(activate).toHaveBeenCalledTimes(3);

      activate.mockResolvedValue(activation("ready", "vite"));
      await act(async () => {
        hook.result.current.retry();
        await vi.advanceTimersByTimeAsync(0);
      });

      expect(activate).toHaveBeenCalledTimes(4);
      expect(hook.result.current.activation?.outcome).toBe("ready");
      expect(hook.result.current.error).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("resets the retry budget for a new activation identity", async () => {
    vi.useFakeTimers();
    try {
      const activate = vi.fn(async (input: { task_id: string }) => {
        if (input.task_id === firstTaskId) throw new Error("offline");
        return activation("ready", "astro");
      });
      const hook = renderHook(
        ({ selectedTask }: { selectedTask: Task }) => usePreviewActivation({
          client: previewClient(activate as (input: never) => Promise<PreviewActivation>),
          enabled: true,
          task: selectedTask,
          workspace: workspace(4),
        }),
        { initialProps: { selectedTask: task(firstTaskId) } },
      );

      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      await act(async () => { await vi.advanceTimersByTimeAsync(8_000); });
      await act(async () => { await vi.advanceTimersByTimeAsync(16_000); });
      expect(activate).toHaveBeenCalledTimes(3);

      hook.rerender({ selectedTask: task(secondTaskId) });
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(activate).toHaveBeenCalledTimes(4);
      expect(hook.result.current.activation?.adapter).toBe("astro");
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not retry after unmount", async () => {
    vi.useFakeTimers();
    try {
      const activate = vi.fn(async () => {
        throw new Error("offline");
      });
      const hook = renderHook(() => usePreviewActivation({
        client: previewClient(activate),
        enabled: true,
        task: task(firstTaskId),
        workspace: workspace(4),
      }));

      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(activate).toHaveBeenCalledOnce();
      hook.unmount();
      await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
      expect(activate).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
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
