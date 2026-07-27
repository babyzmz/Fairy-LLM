import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useTranscriptPersistence,
  type TranscriptAppendRequest,
} from "./useTranscriptPersistence";

const request = (sessionId = "session-a"): TranscriptAppendRequest => ({
  session_id: sessionId,
  speaker: "assistant",
  text: "Keep pressure on the boss.",
});

describe("useTranscriptPersistence", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("retries a transient failure within a three-attempt budget", async () => {
    const append = vi.fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockRejectedValueOnce(new Error("still offline"))
      .mockResolvedValueOnce({});
    const { result } = renderHook(() =>
      useTranscriptPersistence({ sessionId: "session-a", append }),
    );

    act(() => result.current.enqueue(request()));
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(append).toHaveBeenCalledTimes(3);
    expect(append).toHaveBeenNthCalledWith(1, request());
    expect(append).toHaveBeenNthCalledWith(2, request());
    expect(append).toHaveBeenNthCalledWith(3, request());
    expect(result.current.unsavedCount).toBe(0);
  });

  it("coalesces an immediately duplicated stable-caption event", async () => {
    const append = vi.fn().mockResolvedValue({});
    const { result } = renderHook(() =>
      useTranscriptPersistence({ sessionId: "session-a", append }),
    );

    act(() => {
      result.current.enqueue(request());
      result.current.enqueue(request());
    });
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(append).toHaveBeenCalledOnce();
  });

  it("keeps exhausted captions visible and lets manual retry restart the budget", async () => {
    const append = vi.fn().mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() =>
      useTranscriptPersistence({ sessionId: "session-a", append }),
    );

    act(() => result.current.enqueue(request()));
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(append).toHaveBeenCalledTimes(3);
    expect(result.current.unsavedCount).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(append).toHaveBeenCalledTimes(3);

    append.mockResolvedValue({});
    act(() => result.current.retryUnsaved());
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(append).toHaveBeenCalledTimes(4);
    expect(result.current.unsavedCount).toBe(0);
  });

  it("cancels scheduled work when the owning session changes", async () => {
    const append = vi.fn().mockRejectedValue(new Error("offline"));
    const { result, rerender } = renderHook(
      ({ sessionId }: { sessionId: string | null }) =>
        useTranscriptPersistence({ sessionId, append }),
      { initialProps: { sessionId: "session-a" } },
    );

    act(() => result.current.enqueue(request("session-a")));
    await act(async () => {
      await Promise.resolve();
    });
    expect(append).toHaveBeenCalledTimes(1);

    rerender({ sessionId: "session-b" });
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(append).toHaveBeenCalledTimes(1);
    expect(result.current.unsavedCount).toBe(0);
  });

  it("flushes only after every queued stable caption is durable", async () => {
    let resolveAppend: (() => void) | null = null;
    const append = vi.fn(() => new Promise<void>((resolve) => {
      resolveAppend = resolve;
    }));
    const { result } = renderHook(() =>
      useTranscriptPersistence({ sessionId: "session-a", append }),
    );

    act(() => result.current.enqueue(request()));
    const flushed = result.current.flush();
    let settled = false;
    void flushed.then(() => { settled = true; });
    await act(async () => Promise.resolve());
    expect(settled).toBe(false);

    await act(async () => {
      resolveAppend?.();
      await flushed;
    });
    await expect(flushed).resolves.toBe(true);
  });
});
