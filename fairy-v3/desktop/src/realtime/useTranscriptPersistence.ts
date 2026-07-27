import { useCallback, useEffect, useRef, useState } from "react";

export interface TranscriptAppendRequest {
  session_id: string;
  speaker: "user" | "assistant";
  text: string;
}

interface TranscriptPersistenceOptions {
  sessionId: string | null;
  append(request: TranscriptAppendRequest): Promise<unknown>;
}

interface QueueEntry {
  id: number;
  request: TranscriptAppendRequest;
  attempts: number;
}

export interface TranscriptPersistenceState {
  unsavedCount: number;
  enqueue(request: TranscriptAppendRequest): void;
  retryUnsaved(): void;
  reset(): void;
}

const MAX_ATTEMPTS = 3;
const RETRY_DELAYS_MS = [250, 1_000] as const;
const DUPLICATE_EVENT_WINDOW_MS = 1_500;

class TranscriptPersistenceQueue {
  private pending: QueueEntry[] = [];
  private exhausted: QueueEntry[] = [];
  private running = false;
  private retryTimer: number | null = null;
  private generation = 0;
  private nextId = 1;
  private recentFingerprint: { value: string; receivedAt: number } | null = null;

  constructor(
    private readonly append: (request: TranscriptAppendRequest) => Promise<unknown>,
    private readonly currentSessionId: () => string | null,
    private readonly onUnsavedCount: (count: number) => void,
  ) {}

  enqueue(request: TranscriptAppendRequest): void {
    if (request.session_id !== this.currentSessionId()) return;
    const fingerprint = `${request.session_id}\u0000${request.speaker}\u0000${request.text}`;
    const receivedAt = Date.now();
    if (
      this.recentFingerprint?.value === fingerprint
      && receivedAt - this.recentFingerprint.receivedAt <= DUPLICATE_EVENT_WINDOW_MS
    ) {
      return;
    }
    this.recentFingerprint = { value: fingerprint, receivedAt };
    this.pending.push({ id: this.nextId, request, attempts: 0 });
    this.nextId += 1;
    this.drain();
  }

  retryUnsaved(): void {
    if (this.exhausted.length === 0) return;
    this.pending.push(
      ...this.exhausted.map((entry) => ({ ...entry, attempts: 0 })),
    );
    this.exhausted = [];
    this.onUnsavedCount(0);
    this.drain();
  }

  reset(notify = true): void {
    this.generation += 1;
    if (this.retryTimer !== null) window.clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.running = false;
    this.pending = [];
    this.exhausted = [];
    this.recentFingerprint = null;
    if (notify) this.onUnsavedCount(0);
  }

  private drain(): void {
    if (this.running || this.retryTimer !== null) return;
    const entry = this.pending.shift();
    if (entry === undefined) return;

    this.running = true;
    const generation = this.generation;
    void this.append(entry.request).then(
      () => {
        if (generation !== this.generation) return;
        this.running = false;
        this.drain();
      },
      () => {
        if (generation !== this.generation) return;
        this.running = false;
        entry.attempts += 1;
        if (entry.attempts >= MAX_ATTEMPTS) {
          this.exhausted.push(entry);
          this.onUnsavedCount(this.exhausted.length);
          this.drain();
          return;
        }
        const delay = RETRY_DELAYS_MS[entry.attempts - 1] ?? RETRY_DELAYS_MS.at(-1)!;
        this.retryTimer = window.setTimeout(() => {
          if (generation !== this.generation) return;
          this.retryTimer = null;
          this.pending.unshift(entry);
          this.drain();
        }, delay);
      },
    );
  }
}

export function useTranscriptPersistence({
  sessionId,
  append,
}: TranscriptPersistenceOptions): TranscriptPersistenceState {
  const [unsavedCount, setUnsavedCount] = useState(0);
  const appendRef = useRef(append);
  appendRef.current = append;
  const sessionIdRef = useRef(sessionId);
  const queueRef = useRef<TranscriptPersistenceQueue | null>(null);
  if (queueRef.current === null) {
    queueRef.current = new TranscriptPersistenceQueue(
      (request) => appendRef.current(request),
      () => sessionIdRef.current,
      setUnsavedCount,
    );
  }

  useEffect(() => {
    sessionIdRef.current = sessionId;
    queueRef.current?.reset();
  }, [sessionId]);

  useEffect(() => () => queueRef.current?.reset(false), []);

  const enqueue = useCallback((request: TranscriptAppendRequest) => {
    queueRef.current?.enqueue(request);
  }, []);
  const retryUnsaved = useCallback(() => {
    queueRef.current?.retryUnsaved();
  }, []);
  const reset = useCallback(() => {
    queueRef.current?.reset();
  }, []);

  return { unsavedCount, enqueue, retryUnsaved, reset };
}
