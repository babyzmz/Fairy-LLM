import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AssistantTurn,
  EventEnvelope,
  Message,
  ProviderHealth,
  ProviderProfile,
  VoiceAudio,
} from "../core/client";
import { DESKTOP_PREFERENCES_EVENT, type DesktopPreferences } from "../settings/client";
import { ActivityRail } from "../chat/ActivityRail";
import {
  type AudioPlayback,
  type RecordingSession,
  type VoiceClient,
  type VoiceEnvironment,
  VoiceController,
  VoiceRecordControl,
  VoiceSpeakControl,
  useVoicePresence,
} from "./VoiceController";

afterEach(cleanup);

describe("VoiceController", () => {
  it("records through MediaRecorder and sends bounded audio to Core transcription", async () => {
    const onTranscript = vi.fn();
    const session: RecordingSession = {
      mediaType: "audio/webm;codecs=opus",
      stop: vi.fn(async () => new Blob(["recorded"], { type: "audio/webm" })),
      cancel: vi.fn(),
    };
    const startRecording = vi.fn(async () => session);
    const transcribe = vi.fn(async () => ({
      conversation_id: "conversation-1",
      profile_id: "voice",
      text: "Hello Fairy",
      language: "en",
      segments: [],
    }));
    renderVoice(
      <VoiceRecordControl onTranscript={onTranscript} />,
      voiceClient({ transcribe }),
      environment({ startRecording }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Start recording" }));
    expect(await screen.findByRole("button", { name: "Stop recording" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Stop recording" }));

    await waitFor(() => expect(onTranscript).toHaveBeenCalledWith("Hello Fairy"));
    expect(transcribe).toHaveBeenCalledWith({
      conversation_id: "conversation-1",
      profile_id: "voice",
      media_type: "audio/webm",
      audio_base64: "cmVjb3JkZWQ=",
      language: null,
    });
  });

  it("shows explicit denied and unavailable microphone states", async () => {
    const denied = new DOMException("denied", "NotAllowedError");
    renderVoice(
      <VoiceRecordControl onTranscript={vi.fn()} />,
      voiceClient(),
      environment({ startRecording: vi.fn(async () => Promise.reject(denied)) }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Start recording" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Microphone permission denied",
    );

    const { unmount } = renderVoice(
      <VoiceRecordControl onTranscript={vi.fn()} />,
      voiceClient(),
      environment(),
      provider(["text"]),
    );
    expect(screen.getByText("Speech transcription unavailable")).toBeVisible();
    expect(screen.getAllByRole("button", { name: "Start recording" }).at(-1)).toBeDisabled();
    unmount();
  });

  it("synthesizes ledger-bound sentence ranges in order and validates WAV playback", async () => {
    const wav = pcmWav();
    const synthesize = vi.fn(async (input) => voiceAudio(wav, input));
    const playbacks: AudioPlayback[] = [];
    const startPlayback = vi.fn(() => {
      const playback = { finished: Promise.resolve(), stop: vi.fn() };
      playbacks.push(playback);
      return playback;
    });
    renderVoice(
      <VoiceSpeakControl message={assistantMessage("First. Second.")} />,
      voiceClient({ synthesize }),
      environment({ startPlayback }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Speak message" }));

    await waitFor(() => expect(synthesize).toHaveBeenCalledTimes(2));
    expect(synthesize.mock.calls.map(([input]) => input)).toEqual([
      expect.objectContaining({ start_offset: 0, end_offset: 6 }),
      expect.objectContaining({ start_offset: 7, end_offset: 14 }),
    ]);
    expect(startPlayback).toHaveBeenCalledTimes(2);
    expect(playbacks).toHaveLength(2);
  });

  it("stops current playback before a new recording starts", async () => {
    const wav = pcmWav();
    let finishPlayback: (() => void) | undefined;
    const playback: AudioPlayback = {
      finished: new Promise<void>((resolve) => {
        finishPlayback = resolve;
      }),
      stop: vi.fn(() => finishPlayback?.()),
    };
    const session: RecordingSession = {
      mediaType: "audio/webm",
      stop: vi.fn(async () => new Blob(["recorded"])),
      cancel: vi.fn(),
    };
    const startPlayback = vi.fn(() => playback);
    renderVoice(
      <>
        <VoiceSpeakControl message={assistantMessage("One sentence.")} />
        <VoiceRecordControl onTranscript={vi.fn()} />
      </>,
      voiceClient({
        synthesize: vi.fn(async (input) => voiceAudio(wav, input)),
      }),
      environment({
        startPlayback,
        startRecording: vi.fn(async () => session),
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Speak message" }));
    await waitFor(() => expect(startPlayback).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "Start recording" }));

    await waitFor(() => expect(playback.stop).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("button", { name: "Stop recording" })).toBeVisible();
  });

  it("aborts an in-flight cloud synthesis request when speaking stops", async () => {
    let capturedSignal: AbortSignal | undefined;
    const synthesize = vi.fn(
      async (_input, signal?: AbortSignal) =>
        new Promise<VoiceAudio>((_resolve, reject) => {
          capturedSignal = signal;
          signal?.addEventListener(
            "abort",
            () => reject(new DOMException("aborted", "AbortError")),
            { once: true },
          );
        }),
    );
    renderVoice(
      <VoiceSpeakControl message={assistantMessage("Waiting.")} />,
      voiceClient({ synthesize }),
      environment(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Speak message" }));
    await screen.findByRole("button", { name: "Stop speaking" });
    fireEvent.click(screen.getByRole("button", { name: "Stop speaking" }));

    await waitFor(() => expect(capturedSignal?.aborted).toBe(true));
  });

  it("keeps playback failures on the matching turn activity rail", async () => {
    const turn = {
      id: "turn-1",
      task_id: "task-1",
      conversation_id: "conversation-1",
      status: "completed",
      created_at: "2026-07-11T00:00:00Z",
      updated_at: "2026-07-11T00:00:01Z",
    } as AssistantTurn;
    renderVoice(
      <>
        <VoiceSpeakControl message={assistantMessage("One sentence.")} />
        <VoiceRecordControl onTranscript={vi.fn()} />
        <ActivityRail turn={turn} events={[]} />
      </>,
      voiceClient({ synthesize: vi.fn(async () => Promise.reject(new Error("worker offline"))) }),
      environment(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Speak message" }));

    expect(await screen.findByText("Voice playback failed")).toBeVisible();
    expect(screen.queryByText("Speech playback failed")).not.toBeInTheDocument();
  });

  it("auto-plays only stable durable delta ranges through native voice", async () => {
    const startNativePlayback = vi.fn(async () => ({
      finished: Promise.resolve(),
      stop: vi.fn(),
    }));
    const turn = {
      id: "turn-live",
      task_id: "task-1",
      status: "running",
    } as AssistantTurn;
    const events = [
      {
        id: "event-1",
        cursor: 1,
        event_type: "assistant.message.delta",
        payload: {
          turn_id: turn.id,
          model_round: 0,
          chunk_index: 1,
          text: "A stable sentence.",
        },
      } as unknown as EventEnvelope,
    ];
    render(
      <VoiceController
        client={voiceClient()}
        conversationId="conversation-1"
        profile={provider()}
        health={health()}
        environment={environment({ startNativePlayback })}
        turn={turn}
        events={events}
      >
        <span>voice surface</span>
      </VoiceController>,
    );

    fireEvent(
      window,
      new CustomEvent<DesktopPreferences>(DESKTOP_PREFERENCES_EVENT, {
        detail: { voice_replies_enabled: true } as DesktopPreferences,
      }),
    );

    await waitFor(() => expect(startNativePlayback).toHaveBeenCalledTimes(1));
    expect(startNativePlayback).toHaveBeenCalledWith({
      task_id: "task-1",
      turn_id: "turn-live",
      message_id: null,
      start_offset: 0,
      end_offset: 18,
      idempotency_key: "desktop-voice:auto:turn-live:0:18",
    });
  });

  it("uses the pet auto-play preference and stops immediately when the pet is muted", async () => {
    const stop = vi.fn();
    const startNativePlayback = vi.fn(async () => ({
      finished: new Promise<void>(() => undefined),
      stop,
    }));
    const turn = {
      id: "turn-pet",
      task_id: "task-pet",
      status: "running",
    } as AssistantTurn;
    const events = [{
      id: "event-pet",
      cursor: 1,
      event_type: "assistant.message.delta",
      payload: {
        turn_id: turn.id,
        model_round: 0,
        chunk_index: 0,
        text: "Pet reply sentence.",
      },
    } as unknown as EventEnvelope];
    render(
      <VoiceController
        client={voiceClient()}
        conversationId="conversation-1"
        profile={provider()}
        health={health()}
        environment={environment({ startNativePlayback })}
        turn={turn}
        events={events}
        petTaskId="task-pet"
      >
        <span>pet voice surface</span>
      </VoiceController>,
    );

    fireEvent(
      window,
      new CustomEvent<DesktopPreferences>(DESKTOP_PREFERENCES_EVENT, {
        detail: {
          voice_replies_enabled: true,
          pet_muted: false,
        } as DesktopPreferences,
      }),
    );
    await waitFor(() => expect(startNativePlayback).toHaveBeenCalledOnce());
    fireEvent(
      window,
      new CustomEvent<DesktopPreferences>(DESKTOP_PREFERENCES_EVENT, {
        detail: {
          voice_replies_enabled: true,
          pet_muted: true,
        } as DesktopPreferences,
      }),
    );
    await waitFor(() => expect(stop).toHaveBeenCalledOnce());
  });

  it("cancels active automatic playback when voice replies are disabled", async () => {
    const stop = vi.fn();
    const startNativePlayback = vi.fn(async () => ({
      finished: new Promise<void>(() => undefined),
      stop,
    }));
    const turn = {
      id: "turn-disable",
      task_id: "task-1",
      status: "running",
    } as AssistantTurn;
    const events = [{
      id: "event-disable",
      cursor: 1,
      event_type: "assistant.message.delta",
      payload: {
        turn_id: turn.id,
        model_round: 0,
        chunk_index: 0,
        text: "This reply is speaking.",
      },
    } as unknown as EventEnvelope];
    render(
      <VoiceController
        client={voiceClient()}
        conversationId="conversation-1"
        profile={provider()}
        health={health()}
        environment={environment({ startNativePlayback })}
        turn={turn}
        events={events}
      >
        <span>voice surface</span>
      </VoiceController>,
    );

    fireEvent(
      window,
      new CustomEvent<DesktopPreferences>(DESKTOP_PREFERENCES_EVENT, {
        detail: { voice_replies_enabled: true, pet_muted: false } as DesktopPreferences,
      }),
    );
    await waitFor(() => expect(startNativePlayback).toHaveBeenCalledOnce());
    fireEvent(
      window,
      new CustomEvent<DesktopPreferences>(DESKTOP_PREFERENCES_EVENT, {
        detail: { voice_replies_enabled: false, pet_muted: false } as DesktopPreferences,
      }),
    );

    await waitFor(() => expect(stop).toHaveBeenCalledOnce());
  });

  it("bounds automatic playback accumulated from one large delta", async () => {
    const startNativePlayback = vi.fn(async () => ({
      readyForNext: Promise.resolve(),
      finished: Promise.resolve(),
      stop: vi.fn(),
    }));
    const turn = {
      id: "turn-bounded",
      task_id: "task-1",
      status: "running",
    } as AssistantTurn;
    const events = [{
      id: "event-bounded",
      cursor: 1,
      event_type: "assistant.message.delta",
      payload: {
        turn_id: turn.id,
        model_round: 0,
        chunk_index: 0,
        text: "One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten.",
      },
    } as unknown as EventEnvelope];
    render(
      <VoiceController
        client={voiceClient()}
        conversationId="conversation-1"
        profile={provider()}
        health={health()}
        environment={environment({ startNativePlayback })}
        turn={turn}
        events={events}
      >
        <span>voice surface</span>
      </VoiceController>,
    );

    fireEvent(
      window,
      new CustomEvent<DesktopPreferences>(DESKTOP_PREFERENCES_EVENT, {
        detail: { voice_replies_enabled: true, pet_muted: false } as DesktopPreferences,
      }),
    );

    await waitFor(() => expect(startNativePlayback).toHaveBeenCalledTimes(8));
  });

  it("plays an ambient projection exactly once without creating Turn voice state", async () => {
    const stop = vi.fn();
    const startAmbientPlayback = vi.fn(async () => ({
      finished: new Promise<void>(() => undefined),
      stop,
    }));
    renderVoice(
      <AmbientVoiceProbe />,
      voiceClient(),
      environment({ startAmbientPlayback }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Speak ambient" }));
    await waitFor(() => expect(startAmbientPlayback).toHaveBeenCalledWith("Exact reviewed text."));
    fireEvent.click(screen.getByRole("button", { name: "Speak ambient" }));
    expect(startAmbientPlayback).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("ambient-speaking")).toHaveTextContent("yes");

    fireEvent.click(screen.getByRole("button", { name: "Stop ambient" }));
    await waitFor(() => expect(stop).toHaveBeenCalledOnce());
  });
});

function AmbientVoiceProbe() {
  const voice = useVoicePresence();
  return (
    <>
      <button onClick={() => void voice.speakAmbient("Exact reviewed text.", "ambient-1")}>
        Speak ambient
      </button>
      <button onClick={voice.stopAmbient}>Stop ambient</button>
      <span data-testid="ambient-speaking">{voice.speakingAmbient ? "yes" : "no"}</span>
    </>
  );
}

function renderVoice(
  children: React.ReactNode,
  client: VoiceClient,
  voiceEnvironment: VoiceEnvironment,
  selectedProvider = provider(),
) {
  return render(
    <VoiceController
      client={client}
      conversationId="conversation-1"
      profile={selectedProvider}
      health={health()}
      environment={voiceEnvironment}
    >
      {children}
    </VoiceController>,
  );
}

function voiceClient(
  overrides: Partial<VoiceClient["voice"]> = {},
): VoiceClient {
  return {
    voice: {
      transcribe:
        overrides.transcribe ??
        vi.fn(async () => ({
          conversation_id: "conversation-1",
          profile_id: "voice",
          text: "Transcript",
          language: null,
          segments: [],
        })),
      synthesize:
        overrides.synthesize ??
        vi.fn(async () => {
          throw new Error("not used");
        }),
    },
  };
}

function environment(overrides: Partial<VoiceEnvironment> = {}): VoiceEnvironment {
  const result: VoiceEnvironment = {
    supported: true,
    startRecording:
      overrides.startRecording ??
      vi.fn(async () => {
        throw new Error("not used");
      }),
    startPlayback:
      overrides.startPlayback ??
      vi.fn(() => ({ finished: Promise.resolve(), stop: vi.fn() })),
  };
  if (overrides.startNativePlayback !== undefined) {
    result.startNativePlayback = overrides.startNativePlayback;
  }
  if (overrides.startAmbientPlayback !== undefined) {
    result.startAmbientPlayback = overrides.startAmbientPlayback;
  }
  return result;
}

function provider(
  capabilities: ProviderProfile["capabilities"] = ["text", "stt", "tts"],
): ProviderProfile {
  return {
    id: "voice",
    display_name: "Voice",
    kind: "openai_compatible",
    base_url: "https://voice.example.test/v1",
    model_id: "audio-model",
    capabilities,
    credential_required: true,
    credential_configured: true,
    enabled: true,
    timeout_seconds: 30,
    fallback_profile_id: null,
  };
}

function health(): ProviderHealth {
  return {
    profile_id: "voice",
    status: "available",
    error_code: null,
    diagnostics: [],
  };
}

function assistantMessage(content: string): Message {
  return {
    id: "message-1",
    conversation_id: "conversation-1",
    task_id: "task-1",
    turn_id: "turn-1",
    sequence: 2,
    role: "assistant",
    visibility: "user",
    content,
    created_at: "2026-07-11T00:00:00Z",
  };
}

function pcmWav(): Uint8Array {
  const samples = new Uint8Array([0, 0, 1, 0]);
  const bodyLength = 4 + 8 + 16 + 8 + samples.length;
  const bytes = new Uint8Array(8 + bodyLength);
  const view = new DataView(bytes.buffer);
  writeAscii(bytes, 0, "RIFF");
  view.setUint32(4, bodyLength, true);
  writeAscii(bytes, 8, "WAVE");
  writeAscii(bytes, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 24_000, true);
  view.setUint32(28, 48_000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(bytes, 36, "data");
  view.setUint32(40, samples.length, true);
  bytes.set(samples, 44);
  return bytes;
}

function voiceAudio(
  wav: Uint8Array,
  input: Parameters<VoiceClient["voice"]["synthesize"]>[0],
): VoiceAudio {
  return {
    task_id: input.task_id,
    turn_id: input.turn_id,
    message_id: input.message_id,
    profile_id: input.profile_id,
    start_offset: input.start_offset,
    end_offset: input.end_offset,
    media_type: "audio/wav",
    audio_base64: Buffer.from(wav).toString("base64"),
    sample_rate: 24_000,
    channels: 1,
    frames: 2,
    content_hash: createHash("sha256").update(wav).digest("hex"),
  };
}

function writeAscii(bytes: Uint8Array, offset: number, value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    bytes[offset + index] = value.charCodeAt(index);
  }
}
