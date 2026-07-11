export interface SentenceDelta {
  modelRound: number;
  chunkIndex: number;
  text: string;
}

export interface SentenceChunk {
  turnId: string;
  sequence: number;
  text: string;
  startOffset: number;
  endOffset: number;
}

interface TurnQueue {
  modelRound: number;
  expectedChunk: number;
  pending: Map<number, string>;
  seen: Set<string>;
  buffer: string;
  bufferStartOffset: number;
  nextSequence: number;
}

const TERMINATORS = new Set([".", "?", "!", "。", "？", "！"]);
const CLOSERS = new Set(['"', "'", ")", "]", "}", "”", "’", "）", "】"]);
const ABBREVIATIONS = new Set([
  "dr.",
  "mr.",
  "mrs.",
  "ms.",
  "prof.",
  "sr.",
  "jr.",
  "st.",
  "vs.",
  "etc.",
  "e.g.",
  "i.e.",
]);

export class SentenceQueue {
  private readonly turns = new Map<string, TurnQueue>();
  private readonly cancelled = new Set<string>();

  push(turnId: string, delta: SentenceDelta): SentenceChunk[] {
    if (this.cancelled.has(turnId)) return [];
    validateDelta(delta);
    const state = this.turns.get(turnId) ?? createTurnQueue(delta.modelRound);
    this.turns.set(turnId, state);
    if (delta.modelRound < state.modelRound) return [];
    if (delta.modelRound > state.modelRound) {
      if (delta.chunkIndex !== 0) return [];
      state.modelRound = delta.modelRound;
      state.expectedChunk = 0;
      state.pending.clear();
    }
    const coordinate = `${delta.modelRound}:${delta.chunkIndex}`;
    if (state.seen.has(coordinate)) return [];
    state.seen.add(coordinate);
    state.pending.set(delta.chunkIndex, delta.text);
    while (state.pending.has(state.expectedChunk)) {
      state.buffer += state.pending.get(state.expectedChunk) ?? "";
      state.pending.delete(state.expectedChunk);
      state.expectedChunk += 1;
    }
    return extractCompleted(turnId, state);
  }

  flush(turnId: string): SentenceChunk[] {
    if (this.cancelled.has(turnId)) return [];
    const state = this.turns.get(turnId);
    if (state === undefined) return [];
    const completed = extractCompleted(turnId, state);
    stripLeadingWhitespace(state);
    const points = Array.from(state.buffer);
    let end = points.length;
    while (end > 0 && isWhitespace(points[end - 1])) end -= 1;
    if (end === 0) {
      state.buffer = "";
      return completed;
    }
    const text = points.slice(0, end).join("");
    completed.push(chunk(turnId, state, text, state.bufferStartOffset, end));
    state.bufferStartOffset += points.length;
    state.buffer = "";
    return completed;
  }

  cancel(turnId: string): void {
    this.turns.delete(turnId);
    this.cancelled.add(turnId);
  }

  reset(turnId: string): void {
    this.turns.delete(turnId);
    this.cancelled.delete(turnId);
  }
}

function createTurnQueue(modelRound: number): TurnQueue {
  return {
    modelRound,
    expectedChunk: 0,
    pending: new Map(),
    seen: new Set(),
    buffer: "",
    bufferStartOffset: 0,
    nextSequence: 0,
  };
}

function extractCompleted(turnId: string, state: TurnQueue): SentenceChunk[] {
  const result: SentenceChunk[] = [];
  stripLeadingWhitespace(state);
  while (state.buffer.length > 0) {
    const points = Array.from(state.buffer);
    const boundary = sentenceBoundary(points);
    if (boundary === null) break;
    const text = points.slice(0, boundary).join("");
    result.push(chunk(turnId, state, text, state.bufferStartOffset, boundary));
    let consumed = boundary;
    while (consumed < points.length && isWhitespace(points[consumed])) consumed += 1;
    state.buffer = points.slice(consumed).join("");
    state.bufferStartOffset += consumed;
  }
  return result;
}

function sentenceBoundary(points: string[]): number | null {
  for (let index = 0; index < points.length; index += 1) {
    const value = points[index];
    if (!TERMINATORS.has(value)) continue;
    if (value === "." && !periodEndsSentence(points, index)) continue;
    let end = index + 1;
    while (end < points.length && TERMINATORS.has(points[end])) end += 1;
    while (end < points.length && CLOSERS.has(points[end])) end += 1;
    if (
      value === "." &&
      end < points.length &&
      !isWhitespace(points[end]) &&
      isAlphaNumeric(points[index - 1]) &&
      isAlphaNumeric(points[end])
    ) {
      continue;
    }
    return end;
  }
  return null;
}

function periodEndsSentence(points: string[], index: number): boolean {
  if (index > 0 && index + 1 < points.length) {
    if (isDigit(points[index - 1]) && isDigit(points[index + 1])) return false;
  }
  let start = index;
  while (start > 0 && !isWhitespace(points[start - 1])) start -= 1;
  const token = points.slice(start, index + 1).join("").toLowerCase();
  if (ABBREVIATIONS.has(token)) return false;
  if (/^(?:[a-z]\.){2,}$/i.test(token)) return false;
  return true;
}

function stripLeadingWhitespace(state: TurnQueue): void {
  const points = Array.from(state.buffer);
  let start = 0;
  while (start < points.length && isWhitespace(points[start])) start += 1;
  if (start === 0) return;
  state.buffer = points.slice(start).join("");
  state.bufferStartOffset += start;
}

function chunk(
  turnId: string,
  state: TurnQueue,
  text: string,
  startOffset: number,
  length: number,
): SentenceChunk {
  const value = {
    turnId,
    sequence: state.nextSequence,
    text,
    startOffset,
    endOffset: startOffset + length,
  };
  state.nextSequence += 1;
  return value;
}

function validateDelta(delta: SentenceDelta): void {
  if (!Number.isSafeInteger(delta.modelRound) || delta.modelRound < 0) {
    throw new TypeError("modelRound must be a non-negative integer");
  }
  if (!Number.isSafeInteger(delta.chunkIndex) || delta.chunkIndex < 0) {
    throw new TypeError("chunkIndex must be a non-negative integer");
  }
}

function isWhitespace(value: string | undefined): boolean {
  return value !== undefined && /^\s$/u.test(value);
}

function isDigit(value: string | undefined): boolean {
  return value !== undefined && /^\d$/u.test(value);
}

function isAlphaNumeric(value: string | undefined): boolean {
  return value !== undefined && /^[\p{L}\p{N}]$/u.test(value);
}
