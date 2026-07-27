import { describe, expect, it, vi } from "vitest";

import type { NativeVoicePlayback } from "../voice/nativeVoice";
import {
  RealtimeSpeechPipeline,
  RealtimeSpeechSegmenter,
  type RealtimeSpeechCaption,
} from "./realtimeSpeech";

const caption = (
  text: string,
  stable: boolean,
  generation = 1,
): RealtimeSpeechCaption => ({
  session_id: "session-1",
  segment_id: "segment-1",
  context_epoch: 1,
  speech_generation: generation,
  text,
  stable,
});

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function playback(
  readyForNext: Promise<void>,
  finished: Promise<void>,
): NativeVoicePlayback {
  return { readyForNext, finished, stop: vi.fn() };
}

describe("RealtimeSpeechSegmenter", () => {
  it("emits complete short phrases from cumulative unstable captions", () => {
    const segmenter = new RealtimeSpeechSegmenter();
    expect(segmenter.push("Move", false)).toEqual([]);
    expect(segmenter.push("Move back. Hold", false)).toEqual(["Move back."]);
    expect(segmenter.push("Move back. Hold position.", true)).toEqual([
      "Hold position.",
    ]);
  });

  it("flushes a stable remainder and resets for the next response", () => {
    const segmenter = new RealtimeSpeechSegmenter();
    expect(segmenter.push("Short answer", true)).toEqual(["Short answer"]);
    expect(segmenter.push("Next one。", false)).toEqual(["Next one。"]);
  });

  it("bounds a long unpunctuated phrase without losing its remainder", () => {
    const segmenter = new RealtimeSpeechSegmenter();
    const first = "word ".repeat(18);
    const phrases = segmenter.push(first, false);
    expect(phrases).toHaveLength(1);
    expect(phrases[0].length).toBeLessThanOrEqual(72);
    expect(segmenter.push(first, true).join(" ")).not.toBe("");
  });
});

describe("RealtimeSpeechPipeline", () => {
  it("pipelines the next phrase after generation readiness, not audio drain", async () => {
    const firstReady = deferred();
    const firstFinished = deferred();
    const start = vi.fn()
      .mockResolvedValueOnce(playback(firstReady.promise, firstFinished.promise))
      .mockResolvedValueOnce(playback(Promise.resolve(), Promise.resolve()));
    const report = vi.fn();
    const pipeline = new RealtimeSpeechPipeline(start, report, vi.fn());

    pipeline.push(caption("First. Second.", true));
    await vi.waitFor(() => expect(start).toHaveBeenCalledTimes(1));
    let firstDrained = false;
    void firstFinished.promise.then(() => {
      firstDrained = true;
    });
    firstReady.resolve();
    await vi.waitFor(() => expect(start).toHaveBeenCalledTimes(2));
    expect(firstDrained).toBe(false);
  });

  it("cancels active and late playbacks across a barge-in generation", async () => {
    const lateStart = deferred();
    const activeFinished = deferred();
    const active = playback(Promise.resolve(), activeFinished.promise);
    const late = playback(Promise.resolve(), Promise.resolve());
    const start = vi.fn()
      .mockResolvedValueOnce(active)
      .mockImplementationOnce(() => lateStart.promise.then(() => late));
    const pipeline = new RealtimeSpeechPipeline(start, vi.fn(), vi.fn());

    pipeline.push(caption("First. Second.", true));
    await vi.waitFor(() => expect(start).toHaveBeenCalledTimes(2));
    pipeline.interrupt({ ...caption("", false, 2), speech_generation: 2 });
    expect(active.stop).toHaveBeenCalledOnce();
    lateStart.resolve();
    await vi.waitFor(() => expect(late.stop).toHaveBeenCalledOnce());
  });

  it("reports speaking only for the live generation and ignores stale captions", async () => {
    const finished = deferred();
    const report = vi.fn();
    const start = vi.fn(async () => playback(Promise.resolve(), finished.promise));
    const pipeline = new RealtimeSpeechPipeline(start, report, vi.fn());

    pipeline.push(caption("Live.", true, 3));
    await vi.waitFor(() => expect(report).toHaveBeenCalledWith(
      expect.objectContaining({ speech_generation: 3 }),
      true,
    ));
    pipeline.push(caption("Stale.", true, 2));
    expect(start).toHaveBeenCalledOnce();
    finished.resolve();
    await vi.waitFor(() => expect(report).toHaveBeenLastCalledWith(
      expect.objectContaining({ speech_generation: 3 }),
      false,
    ));
  });
});
