import { describe, expect, it } from "vitest";

import { SentenceQueue } from "./sentenceQueue";

describe("SentenceQueue", () => {
  it("emits multilingual sentences without splitting common abbreviations", () => {
    const queue = new SentenceQueue();

    const first = queue.push("turn-1", {
      modelRound: 0,
      chunkIndex: 0,
      text: "Dr. Chen said hello. 你好",
    });
    const second = queue.push("turn-1", {
      modelRound: 0,
      chunkIndex: 1,
      text: "世界！Next",
    });
    const remainder = queue.flush("turn-1");

    expect(first.map((chunk) => chunk.text)).toEqual(["Dr. Chen said hello."]);
    expect(second.map((chunk) => chunk.text)).toEqual(["你好世界！"]);
    expect(remainder.map((chunk) => chunk.text)).toEqual(["Next"]);
  });

  it("orders out-of-order deltas and suppresses replayed coordinates", () => {
    const queue = new SentenceQueue();

    expect(
      queue.push("turn-1", {
        modelRound: 0,
        chunkIndex: 1,
        text: "world.",
      }),
    ).toEqual([]);
    const complete = queue.push("turn-1", {
      modelRound: 0,
      chunkIndex: 0,
      text: "Hello ",
    });
    const replay = queue.push("turn-1", {
      modelRound: 0,
      chunkIndex: 1,
      text: "world again.",
    });

    expect(complete.map((chunk) => chunk.text)).toEqual(["Hello world."]);
    expect(replay).toEqual([]);
  });

  it("uses Unicode code-point offsets for ledger-bound synthesis ranges", () => {
    const queue = new SentenceQueue();

    const chunks = queue.push("turn-emoji", {
      modelRound: 0,
      chunkIndex: 0,
      text: "Hi 👋. Next.",
    });

    expect(chunks).toEqual([
      {
        turnId: "turn-emoji",
        sequence: 0,
        text: "Hi 👋.",
        startOffset: 0,
        endOffset: 5,
      },
      {
        turnId: "turn-emoji",
        sequence: 1,
        text: "Next.",
        startOffset: 6,
        endOffset: 11,
      },
    ]);
  });

  it("cancels one turn without leaking its remainder or duplicate state", () => {
    const queue = new SentenceQueue();
    queue.push("turn-old", { modelRound: 0, chunkIndex: 0, text: "Partial" });

    queue.cancel("turn-old");

    expect(
      queue.push("turn-old", { modelRound: 0, chunkIndex: 1, text: " sentence." }),
    ).toEqual([]);
    expect(queue.flush("turn-old")).toEqual([]);
    expect(
      queue.push("turn-new", { modelRound: 0, chunkIndex: 0, text: "Fresh." }),
    ).toMatchObject([{ turnId: "turn-new", text: "Fresh." }]);
  });

  it("flushes a final remainder once and can reset a turn for replay", () => {
    const queue = new SentenceQueue();
    queue.push("turn-1", { modelRound: 0, chunkIndex: 0, text: "No punctuation" });

    expect(queue.flush("turn-1").map((chunk) => chunk.text)).toEqual([
      "No punctuation",
    ]);
    expect(queue.flush("turn-1")).toEqual([]);
    queue.cancel("turn-1");
    queue.reset("turn-1");
    expect(
      queue.push("turn-1", { modelRound: 0, chunkIndex: 0, text: "Replay." }),
    ).toMatchObject([{ text: "Replay." }]);
  });
});
