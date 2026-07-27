import type { NativeVoicePlayback } from "../voice/nativeVoice";

const SOFT_PHRASE_LENGTH = 36;
const MAX_PHRASE_LENGTH = 72;

export interface RealtimeSpeechIdentity {
  session_id: string;
  segment_id: string;
  context_epoch: number;
  speech_generation: number;
}

export interface RealtimeSpeechCaption extends RealtimeSpeechIdentity {
  text: string;
  stable: boolean;
}

export type RealtimeSpeechStarter = (
  text: string,
) => Promise<NativeVoicePlayback>;

export class RealtimeSpeechSegmenter {
  private aggregate = "";
  private spokenOffset = 0;

  push(text: string, stable: boolean): string[] {
    this.aggregate = mergeCumulativeText(this.aggregate, text);
    const phrases: string[] = [];
    let pending = this.aggregate.slice(this.spokenOffset);

    while (pending.trim() !== "") {
      const boundary = nextPhraseBoundary(pending, stable);
      if (boundary === null) break;
      const phrase = pending.slice(0, boundary).trim();
      this.spokenOffset += boundary;
      pending = this.aggregate.slice(this.spokenOffset);
      if (phrase !== "") phrases.push(phrase);
    }

    if (stable) this.reset();
    return phrases;
  }

  reset(): void {
    this.aggregate = "";
    this.spokenOffset = 0;
  }
}

export class RealtimeSpeechPipeline {
  private readonly segmenter = new RealtimeSpeechSegmenter();
  private readonly active = new Set<NativeVoicePlayback>();
  private identity: RealtimeSpeechIdentity | null = null;
  private launchTail = Promise.resolve();
  private token = 0;
  private speaking = false;
  private pendingStarts = 0;

  constructor(
    private readonly startPlayback: RealtimeSpeechStarter,
    private readonly reportSpeaking: (
      identity: RealtimeSpeechIdentity,
      speaking: boolean,
    ) => void | Promise<void>,
    private readonly onFailure: () => void,
  ) {}

  push(caption: RealtimeSpeechCaption): void {
    if (!validIdentity(caption) || caption.text.trim() === "") return;
    if (this.identity !== null) {
      if (caption.speech_generation < this.identity.speech_generation) return;
      if (
        caption.speech_generation !== this.identity.speech_generation
        || !sameScope(caption, this.identity)
      ) {
        this.cancel(false);
      }
    }
    this.identity = identityOf(caption);
    for (const phrase of this.segmenter.push(caption.text, caption.stable)) {
      this.enqueue(phrase, this.identity);
    }
  }

  interrupt(identity: RealtimeSpeechIdentity): void {
    if (!validIdentity(identity)) return;
    if (
      this.identity !== null
      && identity.speech_generation < this.identity.speech_generation
    ) return;
    this.cancel(false);
    this.identity = identityOf(identity);
  }

  stop(): void {
    this.cancel(true);
    this.identity = null;
  }

  private enqueue(text: string, identity: RealtimeSpeechIdentity): void {
    const token = this.token;
    this.launchTail = this.launchTail
      .catch(() => undefined)
      .then(async () => {
        if (token !== this.token || !sameIdentity(identity, this.identity)) return;
        this.pendingStarts += 1;
        let playback: NativeVoicePlayback;
        try {
          playback = await this.startPlayback(text);
        } catch {
          if (token === this.token) {
            this.pendingStarts -= 1;
            this.onFailure();
          }
          return;
        }
        if (token === this.token) this.pendingStarts -= 1;
        if (token !== this.token || !sameIdentity(identity, this.identity)) {
          playback.stop();
          return;
        }
        this.active.add(playback);
        if (!this.speaking) {
          this.speaking = true;
          void Promise.resolve()
            .then(() => this.reportSpeaking(identity, true))
            .catch(() => undefined);
        }
        void playback.finished.then(
          () => this.finishPlayback(playback, identity, token),
          () => {
            if (token === this.token) this.onFailure();
            this.finishPlayback(playback, identity, token);
          },
        );
        try {
          await playback.readyForNext;
        } catch {
          if (token === this.token) this.onFailure();
        }
      });
  }

  private finishPlayback(
    playback: NativeVoicePlayback,
    identity: RealtimeSpeechIdentity,
    token: number,
  ): void {
    this.active.delete(playback);
    if (
      token !== this.token
      || this.active.size !== 0
      || this.pendingStarts !== 0
      || !this.speaking
      || !sameIdentity(identity, this.identity)
    ) return;
    this.speaking = false;
    void Promise.resolve()
      .then(() => this.reportSpeaking(identity, false))
      .catch(() => undefined);
  }

  private cancel(reportStopped: boolean): void {
    this.token += 1;
    this.segmenter.reset();
    this.launchTail = Promise.resolve();
    for (const playback of this.active) playback.stop();
    this.active.clear();
    this.pendingStarts = 0;
    if (reportStopped && this.speaking && this.identity !== null) {
      const identity = this.identity;
      void Promise.resolve()
        .then(() => this.reportSpeaking(identity, false))
        .catch(() => undefined);
    }
    this.speaking = false;
  }
}

function nextPhraseBoundary(text: string, stable: boolean): number | null {
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (/[.!?。！？；;\n]/u.test(character)) return index + 1;
    if (
      index + 1 >= SOFT_PHRASE_LENGTH
      && /[,，、:：]/u.test(character)
    ) return index + 1;
  }
  if (text.length >= MAX_PHRASE_LENGTH) {
    const candidate = text.slice(0, MAX_PHRASE_LENGTH);
    const whitespace = Math.max(
      candidate.lastIndexOf(" "),
      candidate.lastIndexOf("\t"),
    );
    return whitespace >= SOFT_PHRASE_LENGTH ? whitespace + 1 : MAX_PHRASE_LENGTH;
  }
  return stable ? text.length : null;
}

function mergeCumulativeText(current: string, incoming: string): string {
  if (incoming === "") return current;
  if (current === "" || incoming.startsWith(current)) return incoming;
  if (current.startsWith(incoming) || current.endsWith(incoming)) return current;
  return `${current}${incoming}`;
}

function validIdentity(identity: RealtimeSpeechIdentity): boolean {
  return (
    typeof identity.session_id === "string"
    && identity.session_id.trim() !== ""
    && typeof identity.segment_id === "string"
    && identity.segment_id.trim() !== ""
    && Number.isSafeInteger(identity.context_epoch)
    && identity.context_epoch > 0
    && Number.isSafeInteger(identity.speech_generation)
    && identity.speech_generation > 0
  );
}

function identityOf(identity: RealtimeSpeechIdentity): RealtimeSpeechIdentity {
  return {
    session_id: identity.session_id,
    segment_id: identity.segment_id,
    context_epoch: identity.context_epoch,
    speech_generation: identity.speech_generation,
  };
}

function sameScope(
  left: RealtimeSpeechIdentity,
  right: RealtimeSpeechIdentity,
): boolean {
  return (
    left.session_id === right.session_id
    && left.segment_id === right.segment_id
    && left.context_epoch === right.context_epoch
  );
}

function sameIdentity(
  left: RealtimeSpeechIdentity,
  right: RealtimeSpeechIdentity | null,
): boolean {
  return (
    right !== null
    && sameScope(left, right)
    && left.speech_generation === right.speech_generation
  );
}
