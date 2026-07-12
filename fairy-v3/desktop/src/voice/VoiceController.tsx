import { LoaderCircle, Mic, Square, Volume2 } from "lucide-react";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type {
  AssistantTurn,
  CoreClient,
  EventEnvelope,
  Message,
  ProviderHealth,
  ProviderProfile,
  VoiceAudio,
  VoiceSessionStartInput,
} from "../core/client";
import {
  DESKTOP_PREFERENCES_EVENT,
  type DesktopPreferences,
} from "../settings/client";
import { nativeVoiceAvailable, startNativeVoice } from "./nativeVoice";
import { SentenceQueue } from "./sentenceQueue";

const MAX_RECORDING_BYTES = 20 * 1024 * 1024;

export interface VoiceClient {
  voice: Pick<CoreClient["voice"], "transcribe" | "synthesize">;
}

export interface RecordingSession {
  mediaType: string;
  stop(): Promise<Blob>;
  cancel(): void;
}

export interface AudioPlayback {
  readyForNext?: Promise<void>;
  finished: Promise<void>;
  stop(): void;
}

export interface VoiceEnvironment {
  supported: boolean;
  startRecording(): Promise<RecordingSession>;
  startPlayback(wav: Uint8Array): AudioPlayback;
  startNativePlayback?(input: VoiceSessionStartInput): Promise<AudioPlayback>;
}

interface VoiceControllerProps {
  client: VoiceClient;
  conversationId: string | null;
  profile: ProviderProfile | null;
  health: ProviderHealth | null;
  turn?: AssistantTurn | null;
  events?: EventEnvelope[];
  petTaskId?: string | null;
  environment?: VoiceEnvironment;
  children: ReactNode;
}

type RecordingState = "idle" | "requesting" | "recording" | "transcribing";
type PlaybackState = "idle" | "preparing" | "speaking" | "failed";

interface VoiceContextValue {
  sttAvailable: boolean;
  ttsAvailable: boolean;
  recordingState: RecordingState;
  speakingMessageId: string | null;
  speakingTurnId: string | null;
  playbackState: PlaybackState;
  statusMessage: string | null;
  startRecording(onTranscript: (text: string) => void): Promise<void>;
  stopRecording(): Promise<void>;
  speak(message: Message): Promise<void>;
  stopSpeaking(): void;
}

const VoiceContext = createContext<VoiceContextValue | null>(null);

export function VoiceController({
  client,
  conversationId,
  profile,
  health,
  turn = null,
  events = [],
  petTaskId = null,
  environment: configuredEnvironment,
  children,
}: VoiceControllerProps) {
  const environment = useMemo(
    () => configuredEnvironment ?? defaultVoiceEnvironment(),
    [configuredEnvironment],
  );
  const [recordingState, setRecordingState] = useState<RecordingState>("idle");
  const [speakingMessageId, setSpeakingMessageId] = useState<string | null>(null);
  const [speakingTurnId, setSpeakingTurnId] = useState<string | null>(null);
  const [playbackState, setPlaybackState] = useState<PlaybackState>("idle");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [autoPlayChat, setAutoPlayChat] = useState(false);
  const [autoPlayPet, setAutoPlayPet] = useState(true);
  const [petMuted, setPetMuted] = useState(false);
  const recordingRef = useRef<RecordingSession | null>(null);
  const transcriptRef = useRef<((text: string) => void) | null>(null);
  const playbackRef = useRef<AudioPlayback | null>(null);
  const voiceRequestRef = useRef<AbortController | null>(null);
  const playbackEpoch = useRef(0);
  const sentenceQueue = useRef(new SentenceQueue());
  const autoTurnRef = useRef<string | null>(null);
  const autoEventIds = useRef(new Set<string>());
  const autoCoordinates = useRef(new Set<string>());
  const autoChunkIndex = useRef(0);
  const autoFlushed = useRef(false);
  const autoQueue = useRef(Promise.resolve());
  const providerAvailable =
    profile !== null &&
    profile.enabled &&
    (!profile.credential_required || profile.credential_configured) &&
    health?.status !== "unavailable";
  const sttAvailable =
    environment.supported &&
    conversationId !== null &&
    providerAvailable &&
    profile.capabilities.includes("stt");
  const nativeTtsAvailable = environment.startNativePlayback !== undefined;
  const ttsAvailable =
    nativeTtsAvailable ||
    (environment.supported && providerAvailable && profile?.capabilities.includes("tts") === true);

  const stopSpeaking = useCallback(() => {
    playbackEpoch.current += 1;
    voiceRequestRef.current?.abort();
    voiceRequestRef.current = null;
    playbackRef.current?.stop();
    playbackRef.current = null;
    setSpeakingMessageId(null);
    setSpeakingTurnId(null);
    setPlaybackState("idle");
  }, []);

  useEffect(() => {
    const update = (event: Event) => {
      const preferences = (event as CustomEvent<DesktopPreferences>).detail;
      setAutoPlayChat(preferences.voice_auto_play_chat);
      setAutoPlayPet(preferences.voice_auto_play_pet);
      setPetMuted(preferences.pet_muted);
    };
    window.addEventListener(DESKTOP_PREFERENCES_EVENT, update);
    return () => window.removeEventListener(DESKTOP_PREFERENCES_EVENT, update);
  }, []);

  useEffect(() => {
    if (petMuted && turn?.task_id === petTaskId) stopSpeaking();
  }, [petMuted, petTaskId, stopSpeaking, turn?.task_id]);

  const startRecording = useCallback(
    async (onTranscript: (text: string) => void) => {
      if (!sttAvailable) {
        setStatusMessage("Speech transcription unavailable");
        return;
      }
      stopSpeaking();
      setStatusMessage(null);
      setRecordingState("requesting");
      try {
        const session = await environment.startRecording();
        recordingRef.current = session;
        transcriptRef.current = onTranscript;
        setRecordingState("recording");
      } catch (error) {
        setRecordingState("idle");
        setStatusMessage(
          isPermissionDenied(error)
            ? "Microphone permission denied"
            : "Microphone unavailable",
        );
      }
    },
    [environment, stopSpeaking, sttAvailable],
  );

  const stopRecording = useCallback(async () => {
    const session = recordingRef.current;
    const onTranscript = transcriptRef.current;
    if (session === null || profile === null || conversationId === null) return;
    recordingRef.current = null;
    transcriptRef.current = null;
    setRecordingState("transcribing");
    setStatusMessage(null);
    try {
      const blob = await session.stop();
      if (blob.size === 0 || blob.size > MAX_RECORDING_BYTES) {
        throw new Error("Recording exceeds the supported size");
      }
      const result = await client.voice.transcribe({
        conversation_id: conversationId,
        profile_id: profile.id,
        media_type: recordingMediaType(session.mediaType || blob.type),
        audio_base64: await blobToBase64(blob),
        language: null,
      });
      onTranscript?.(result.text);
    } catch (error) {
      setStatusMessage(errorMessage(error, "Transcription failed"));
    } finally {
      setRecordingState("idle");
    }
  }, [client.voice, conversationId, profile]);

  const speak = useCallback(
    async (message: Message) => {
      if (
        !ttsAvailable ||
        message.role !== "assistant" ||
        message.visibility !== "user" ||
        message.turn_id === null
      ) {
        setStatusMessage("Speech playback unavailable");
        return;
      }
      stopSpeaking();
      const epoch = playbackEpoch.current;
      const requestController = new AbortController();
      voiceRequestRef.current = requestController;
      const turnId = message.turn_id;
      sentenceQueue.current.reset(turnId);
      const chunks = [
        ...sentenceQueue.current.push(turnId, {
          modelRound: 0,
          chunkIndex: 0,
          text: message.content,
        }),
        ...sentenceQueue.current.flush(turnId),
      ];
      setStatusMessage(null);
      setSpeakingMessageId(message.id);
      setSpeakingTurnId(turnId);
      setPlaybackState("preparing");
      let playbackFailed = false;
      try {
        if (environment.startNativePlayback !== undefined) {
          const playback = await environment.startNativePlayback({
            task_id: message.task_id,
            turn_id: turnId,
            message_id: message.id,
            start_offset: 0,
            end_offset: Array.from(message.content).length,
            idempotency_key: voiceIdempotencyKey(message.id),
          });
          if (epoch !== playbackEpoch.current) {
            playback.stop();
            return;
          }
          playbackRef.current = playback;
          setPlaybackState("speaking");
          await playback.finished;
          if (epoch !== playbackEpoch.current) return;
          playbackRef.current = null;
          return;
        }
        if (profile === null) throw new Error("Speech provider unavailable");
        for (const chunk of chunks) {
          if (epoch !== playbackEpoch.current) return;
          const response = await client.voice.synthesize(
            {
              task_id: message.task_id,
              turn_id: turnId,
              message_id: message.id,
              profile_id: profile.id,
              voice: "alloy",
              start_offset: chunk.startOffset,
              end_offset: chunk.endOffset,
            },
            requestController.signal,
          );
          if (epoch !== playbackEpoch.current) return;
          const wav = await validateVoiceAudio(response, message, chunk.startOffset, chunk.endOffset);
          if (epoch !== playbackEpoch.current) return;
          const playback = environment.startPlayback(wav);
          playbackRef.current = playback;
          setPlaybackState("speaking");
          await playback.finished;
          if (epoch !== playbackEpoch.current) return;
          playbackRef.current = null;
        }
      } catch (error) {
        if (epoch === playbackEpoch.current) {
          playbackFailed = true;
          setStatusMessage(errorMessage(error, "Speech playback failed"));
          setPlaybackState("failed");
        }
      } finally {
        if (epoch === playbackEpoch.current) {
          playbackRef.current = null;
          voiceRequestRef.current = null;
          setSpeakingMessageId(null);
          if (!playbackFailed) {
            setSpeakingTurnId(null);
            setPlaybackState("idle");
          }
        }
      }
    },
    [client.voice, environment, profile, stopSpeaking, ttsAvailable],
  );

  useEffect(() => {
    const autoPlay =
      turn !== null && turn.task_id === petTaskId
        ? autoPlayPet && !petMuted
        : autoPlayChat;
    if (!autoPlay || environment.startNativePlayback === undefined || turn === null) return;
    if (autoTurnRef.current !== turn.id) {
      if (autoTurnRef.current !== null) stopSpeaking();
      autoTurnRef.current = turn.id;
      autoEventIds.current.clear();
      autoCoordinates.current.clear();
      autoChunkIndex.current = 0;
      autoFlushed.current = false;
      sentenceQueue.current.reset(turn.id);
    }
    const chunks = events
      .filter((event) => event.event_type === "assistant.message.delta")
      .filter((event) => event.payload.turn_id === turn.id)
      .sort((left, right) =>
        numericPayload(left, "model_round") - numericPayload(right, "model_round") ||
        numericPayload(left, "chunk_index") - numericPayload(right, "chunk_index") ||
        left.cursor - right.cursor,
      )
      .flatMap((event) => {
        const modelRound = event.payload.model_round;
        const sourceChunk = event.payload.chunk_index;
        if (
          autoEventIds.current.has(event.id) ||
          typeof event.payload.text !== "string" ||
          typeof modelRound !== "number" ||
          typeof sourceChunk !== "number"
        ) {
          return [];
        }
        const coordinate = `${modelRound}:${sourceChunk}`;
        autoEventIds.current.add(event.id);
        if (autoCoordinates.current.has(coordinate)) return [];
        autoCoordinates.current.add(coordinate);
        const chunkIndex = autoChunkIndex.current;
        autoChunkIndex.current += 1;
        return sentenceQueue.current.push(turn.id, {
          modelRound: 0,
          chunkIndex,
          text: event.payload.text,
        });
      });
    if (turn.status === "completed" && !autoFlushed.current) {
      autoFlushed.current = true;
      chunks.push(...sentenceQueue.current.flush(turn.id));
    }
    for (const chunk of chunks) {
      const epoch = playbackEpoch.current;
      autoQueue.current = autoQueue.current.then(async () => {
        if (epoch !== playbackEpoch.current || environment.startNativePlayback === undefined) return;
        setSpeakingMessageId(`auto:${turn.id}`);
        setSpeakingTurnId(turn.id);
        setPlaybackState("preparing");
        const playback = await environment.startNativePlayback({
          task_id: turn.task_id,
          turn_id: turn.id,
          message_id: null,
          start_offset: chunk.startOffset,
          end_offset: chunk.endOffset,
          idempotency_key: `desktop-voice:auto:${turn.id}:${chunk.startOffset}:${chunk.endOffset}`,
        });
        if (epoch !== playbackEpoch.current) {
          playback.stop();
          return;
        }
        playbackRef.current = playback;
        setPlaybackState("speaking");
        await (playback.readyForNext ?? playback.finished);
        if (epoch === playbackEpoch.current) {
          void playback.finished.then(() => {
            if (epoch === playbackEpoch.current && playbackRef.current === playback) {
              playbackRef.current = null;
              setSpeakingMessageId(null);
              setSpeakingTurnId(null);
              setPlaybackState("idle");
            }
          });
        }
      }).catch((error) => {
        if (epoch === playbackEpoch.current) {
          playbackRef.current = null;
          setSpeakingMessageId(null);
          setPlaybackState("failed");
          setStatusMessage(errorMessage(error, "Speech playback failed"));
        }
      });
    }
  }, [autoPlayChat, autoPlayPet, environment, events, petMuted, petTaskId, stopSpeaking, turn]);

  useEffect(
    () => () => {
      recordingRef.current?.cancel();
      playbackRef.current?.stop();
      voiceRequestRef.current?.abort();
      playbackEpoch.current += 1;
    },
    [],
  );

  const value = useMemo<VoiceContextValue>(
    () => ({
      sttAvailable,
      ttsAvailable,
      recordingState,
      speakingMessageId,
      speakingTurnId,
      playbackState,
      statusMessage:
        statusMessage ??
        (!environment.supported
          ? "Voice device APIs unavailable"
          : !sttAvailable
            ? "Speech transcription unavailable"
            : null),
      startRecording,
      stopRecording,
      speak,
      stopSpeaking,
    }),
    [
      environment.supported,
      recordingState,
      speak,
      speakingMessageId,
      speakingTurnId,
      playbackState,
      startRecording,
      statusMessage,
      stopRecording,
      stopSpeaking,
      sttAvailable,
      ttsAvailable,
    ],
  );

  return <VoiceContext.Provider value={value}>{children}</VoiceContext.Provider>;
}

export function useVoicePlaybackState(turnId: string): PlaybackState {
  const voice = useContext(VoiceContext);
  return voice?.speakingTurnId === turnId ? voice.playbackState : "idle";
}

export function useVoicePresence(): { speaking: boolean; stopSpeaking(): void } {
  const voice = useContext(VoiceContext);
  return {
    speaking: voice?.playbackState === "preparing" || voice?.playbackState === "speaking",
    stopSpeaking: voice?.stopSpeaking ?? (() => undefined),
  };
}

export function VoiceRecordControl({
  disabled = false,
  onTranscript,
}: {
  disabled?: boolean;
  onTranscript(text: string): void;
}) {
  const voice = useContext(VoiceContext);
  if (voice === null) {
    return (
      <button
        className="icon-button voice-button"
        type="button"
        aria-label="Start recording"
        title="Voice input unavailable"
        disabled
      >
        <Mic size={17} />
      </button>
    );
  }
  const recording = voice.recordingState === "recording";
  const waiting = ["requesting", "transcribing"].includes(voice.recordingState);
  const denied = voice.statusMessage === "Microphone permission denied";
  return (
    <>
      <button
        className={`icon-button voice-button ${recording ? "active" : ""}`}
        type="button"
        aria-label={
          recording
            ? "Stop recording"
            : waiting
              ? "Transcribing recording"
              : "Start recording"
        }
        title={recording ? "Stop recording" : "Start recording"}
        disabled={disabled || waiting || (!recording && !voice.sttAvailable)}
        onClick={() =>
          recording
            ? void voice.stopRecording()
            : void voice.startRecording(onTranscript)
        }
      >
        {recording ? (
          <Square size={15} />
        ) : waiting ? (
          <LoaderCircle className="spin" size={16} />
        ) : (
          <Mic size={17} />
        )}
      </button>
      {voice.statusMessage ? (
        <span className="voice-control-status" role={denied ? "alert" : "status"}>
          {voice.statusMessage}
        </span>
      ) : null}
    </>
  );
}

export function VoiceSpeakControl({
  message,
  disabled = false,
}: {
  message: Message;
  disabled?: boolean;
}) {
  const voice = useContext(VoiceContext);
  if (voice === null) return null;
  if (
    message.role !== "assistant" ||
    message.visibility !== "user" ||
    message.turn_id === null
  ) {
    return null;
  }
  const speaking = voice.speakingMessageId === message.id;
  return (
    <button
      className={`message-speak-button ${speaking ? "active" : ""}`}
      type="button"
      aria-label={speaking ? "Stop speaking" : "Speak message"}
      title={speaking ? "Stop speaking" : "Speak message"}
      disabled={disabled || (!speaking && !voice.ttsAvailable)}
      onClick={() => (speaking ? voice.stopSpeaking() : void voice.speak(message))}
    >
      {speaking ? <Square size={12} /> : <Volume2 size={14} />}
    </button>
  );
}

async function validateVoiceAudio(
  response: VoiceAudio,
  message: Message,
  startOffset: number,
  endOffset: number,
): Promise<Uint8Array> {
  if (
    response.task_id !== message.task_id ||
    response.turn_id !== message.turn_id ||
    response.message_id !== message.id ||
    response.start_offset !== startOffset ||
    response.end_offset !== endOffset ||
    response.media_type !== "audio/wav"
  ) {
    throw new Error("Voice response scope does not match the message")
  }
  const bytes = decodeBase64(response.audio_base64);
  const metadata = parsePcmWav(bytes);
  if (
    metadata.sampleRate !== response.sample_rate ||
    metadata.channels !== response.channels ||
    metadata.frames !== response.frames
  ) {
    throw new Error("Voice response WAV metadata mismatch");
  }
  const digestInput = Uint8Array.from(bytes);
  const digest = await crypto.subtle.digest("SHA-256", digestInput.buffer);
  const hash = Array.from(new Uint8Array(digest), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
  if (hash !== response.content_hash) throw new Error("Voice response hash mismatch");
  return bytes;
}

function parsePcmWav(bytes: Uint8Array): {
  sampleRate: number;
  channels: number;
  frames: number;
} {
  if (
    bytes.length < 44 ||
    ascii(bytes, 0, 4) !== "RIFF" ||
    ascii(bytes, 8, 4) !== "WAVE"
  ) {
    throw new Error("Voice response is not RIFF WAV")
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(4, true) + 8 !== bytes.length) {
    throw new Error("Voice response RIFF length mismatch")
  }
  let offset = 12;
  let format: { sampleRate: number; channels: number; blockAlign: number } | null = null;
  let dataLength: number | null = null;
  while (offset + 8 <= bytes.length) {
    const id = ascii(bytes, offset, 4);
    const length = view.getUint32(offset + 4, true);
    const start = offset + 8;
    const end = start + length;
    if (end > bytes.length) throw new Error("Voice response WAV chunk overflow");
    if (id === "fmt " && format === null) {
      if (length < 16 || view.getUint16(start, true) !== 1) {
        throw new Error("Voice response WAV must be PCM")
      }
      const channels = view.getUint16(start + 2, true);
      const sampleRate = view.getUint32(start + 4, true);
      const blockAlign = view.getUint16(start + 12, true);
      const bits = view.getUint16(start + 14, true);
      if (bits !== 16 || ![1, 2].includes(channels) || blockAlign !== channels * 2) {
        throw new Error("Voice response PCM format is unsupported")
      }
      format = { sampleRate, channels, blockAlign };
    }
    if (id === "data" && dataLength === null) dataLength = length;
    offset = end + (length % 2);
  }
  if (offset !== bytes.length || format === null || dataLength === null) {
    throw new Error("Voice response WAV is incomplete")
  }
  if (dataLength % format.blockAlign !== 0) {
    throw new Error("Voice response WAV frames are incomplete")
  }
  return {
    sampleRate: format.sampleRate,
    channels: format.channels,
    frames: dataLength / format.blockAlign,
  };
}

function defaultVoiceEnvironment(): VoiceEnvironment {
  const supported =
    typeof navigator !== "undefined" &&
    navigator.mediaDevices?.getUserMedia !== undefined &&
    typeof MediaRecorder !== "undefined" &&
    typeof Audio !== "undefined";
  const environment: VoiceEnvironment = {
    supported,
    startRecording: () => startBrowserRecording(),
    startPlayback: (wav) => startBrowserPlayback(wav),
  };
  if (nativeVoiceAvailable()) environment.startNativePlayback = startNativeVoice;
  return environment;
}

function voiceIdempotencyKey(messageId: string): string {
  const nonce = typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `desktop-voice:${messageId}:${nonce}`;
}

function numericPayload(event: EventEnvelope, key: string): number {
  const value = event.payload[key];
  return typeof value === "number" && Number.isFinite(value) ? value : Number.MAX_SAFE_INTEGER;
}

async function startBrowserRecording(): Promise<RecordingSession> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const recorder = new MediaRecorder(stream);
  const chunks: BlobPart[] = [];
  recorder.addEventListener("dataavailable", (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  });
  recorder.start();
  let stopped: Promise<Blob> | null = null;
  const stopTracks = () => stream.getTracks().forEach((track) => track.stop());
  return {
    mediaType: recorder.mimeType || "audio/webm",
    stop() {
      if (stopped !== null) return stopped;
      stopped = new Promise<Blob>((resolve, reject) => {
        recorder.addEventListener(
          "stop",
          () => {
            stopTracks();
            resolve(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
          },
          { once: true },
        );
        recorder.addEventListener(
          "error",
          () => {
            stopTracks();
            reject(new Error("MediaRecorder failed"));
          },
          { once: true },
        );
        recorder.stop();
      });
      return stopped;
    },
    cancel() {
      if (recorder.state !== "inactive") recorder.stop();
      stopTracks();
    },
  };
}

function startBrowserPlayback(wav: Uint8Array): AudioPlayback {
  const blob = new Blob([new Uint8Array(wav)], { type: "audio/wav" });
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  let settle: (() => void) | null = null;
  let rejectPlayback: ((error: Error) => void) | null = null;
  const finished = new Promise<void>((resolve, reject) => {
    settle = resolve;
    rejectPlayback = reject;
  });
  const release = () => URL.revokeObjectURL(url);
  audio.addEventListener(
    "ended",
    () => {
      release();
      settle?.();
    },
    { once: true },
  );
  audio.addEventListener(
    "error",
    () => {
      release();
      rejectPlayback?.(new Error("Audio playback failed"));
    },
    { once: true },
  );
  void audio.play().catch((error: unknown) => {
    release();
    rejectPlayback?.(error instanceof Error ? error : new Error("Audio playback failed"));
  });
  return {
    finished,
    stop() {
      audio.pause();
      release();
      settle?.();
    },
  };
}

function recordingMediaType(value: string): "audio/webm" | "audio/wav" | "audio/mpeg" | "audio/mp4" | "audio/ogg" {
  const mediaType = value.split(";", 1)[0].trim().toLowerCase();
  if (["audio/webm", "audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg"].includes(mediaType)) {
    return mediaType as "audio/webm" | "audio/wav" | "audio/mpeg" | "audio/mp4" | "audio/ogg";
  }
  throw new Error("Recorded audio media type is unsupported");
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("Could not read recording"));
    reader.onload = () => {
      const value = reader.result;
      if (typeof value !== "string" || !value.includes(",")) {
        reject(new Error("Could not encode recording"));
        return;
      }
      resolve(value.slice(value.indexOf(",") + 1));
    };
    reader.readAsDataURL(blob);
  });
}

function decodeBase64(value: string): Uint8Array {
  try {
    const decoded = atob(value);
    return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
  } catch (error) {
    throw new Error("Voice response audio is not base64", { cause: error });
  }
}

function ascii(bytes: Uint8Array, offset: number, length: number): string {
  return String.fromCharCode(...bytes.slice(offset, offset + length));
}

function isPermissionDenied(error: unknown): boolean {
  return error instanceof DOMException && error.name === "NotAllowedError";
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
