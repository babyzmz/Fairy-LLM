import { synthesizeVoiceAudio } from "../../../lib/api/system";
import type { VoicePlaybackDiagnostic, VoicePlaybackItem, VoicePlaybackState } from "./voiceTypes";

function base64ToBlobUrl(encoded: string, mimeType: string): string {
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  const blob = new Blob([bytes], { type: mimeType || "audio/wav" });
  return URL.createObjectURL(blob);
}

export class VoicePlaybackController {
  private state: VoicePlaybackState = {
    current: null,
    queue: [],
  };

  private currentAudio: HTMLAudioElement | null = null;
  private currentObjectUrl: string | null = null;
  private playbackNonce = 0;

  constructor(
    private readonly onStateChange: (state: VoicePlaybackState) => void,
    private readonly onDiagnostic: (diagnostic: VoicePlaybackDiagnostic) => void,
  ) {}

  snapshot(): VoicePlaybackState {
    return {
      current: this.state.current,
      queue: [...this.state.queue],
    };
  }

  updateQueue(queue: VoicePlaybackItem[]): void {
    this.state = {
      ...this.state,
      queue: [...queue],
    };
    this.onStateChange(this.snapshot());
  }

  play(item: VoicePlaybackItem, onDone: () => void): void {
    const playbackId = ++this.playbackNonce;
    this.emitDiagnostic("item_selected", item, { detail: item.kind === "tts" ? "queued_tts" : "queued_asset" });
    this.state = {
      ...this.state,
      current: item,
    };
    this.onStateChange(this.snapshot());

    const finalize = (): void => {
      if (playbackId !== this.playbackNonce) {
        return;
      }
      this.releaseCurrentAudio();
      this.state = {
        ...this.state,
        current: null,
      };
      this.onStateChange(this.snapshot());
      onDone();
    };

    if (item.kind === "asset" && item.assetSrc) {
      this.emitDiagnostic("asset_loading", item, { detail: item.assetSrc });
      const audio = new Audio(item.assetSrc);
      audio.preload = "auto";
      this.currentAudio = audio;
      audio.onended = () => {
        this.emitDiagnostic("playback_ended", item, { detail: "asset_ended" });
        finalize();
      };
      audio.onerror = () => {
        this.emitDiagnostic("playback_error", item, { error: "asset_audio_error" });
        finalize();
      };
      audio.onplay = () => this.emitDiagnostic("playback_started", item, { detail: "asset_started" });
      void audio.play().catch((error) => {
        this.emitDiagnostic("playback_error", item, { error: stringifyError(error) });
        finalize();
      });
      return;
    }

    if (item.kind === "tts" && item.text?.trim()) {
      void this.playSynthesizedItem(item, playbackId, finalize);
      return;
    }

    finalize();
  }

  stopCurrent(): void {
    if (this.state.current) {
      this.emitDiagnostic("playback_interrupted", this.state.current, { detail: "stop_current" });
    }
    this.playbackNonce += 1;
    this.releaseCurrentAudio();
    this.state = {
      ...this.state,
      current: null,
    };
    this.onStateChange(this.snapshot());
  }

  shutdown(): void {
    this.stopCurrent();
    this.state = { current: null, queue: [] };
    this.onStateChange(this.snapshot());
  }

  private async playSynthesizedItem(item: VoicePlaybackItem, playbackId: number, finalize: () => void): Promise<void> {
    try {
      this.emitDiagnostic("requesting_tts", item, { detail: item.systemVoice ? "system_voice" : "reply_voice" });
      const payload = await synthesizeVoiceAudio(item.text || "", Boolean(item.systemVoice));
      if (playbackId !== this.playbackNonce) {
        this.emitDiagnostic("stale_tts_response", item, { detail: "playback_nonce_changed" });
        return;
      }
      this.emitDiagnostic("received_tts_audio", item, {
        detail: `${payload.mime_type} / ${payload.audio_base64.length}b64`,
      });
      const objectUrl = base64ToBlobUrl(payload.audio_base64, payload.mime_type);
      const audio = new Audio(objectUrl);
      audio.preload = "auto";
      audio.volume = 1;
      this.currentObjectUrl = objectUrl;
      this.currentAudio = audio;
      audio.onloadeddata = () => this.emitDiagnostic("audio_loaded", item, { detail: payload.mime_type });
      audio.oncanplaythrough = () => this.emitDiagnostic("audio_can_play", item, { detail: payload.mime_type });
      audio.onplay = () => this.emitDiagnostic("playback_started", item, { detail: payload.mime_type });
      audio.onended = () => {
        this.emitDiagnostic("playback_ended", item, { detail: payload.mime_type });
        finalize();
      };
      audio.onerror = () => {
        this.emitDiagnostic("playback_error", item, { error: "audio_element_error" });
        finalize();
      };
      audio.load();
      await audio.play();
    } catch (error) {
      this.emitDiagnostic("tts_play_failed", item, { error: stringifyError(error) });
      finalize();
    }
  }

  private releaseCurrentAudio(): void {
    if (this.currentAudio) {
      this.currentAudio.pause();
      this.currentAudio.src = "";
      this.currentAudio.onended = null;
      this.currentAudio.onerror = null;
      this.currentAudio = null;
    }
    if (this.currentObjectUrl) {
      URL.revokeObjectURL(this.currentObjectUrl);
      this.currentObjectUrl = null;
    }
  }

  private emitDiagnostic(
    stage: string,
    item: VoicePlaybackItem,
    options: { detail?: string; error?: string } = {},
  ): void {
    const diagnostic: VoicePlaybackDiagnostic = {
      stage,
      detail: options.detail || "",
      error: options.error || "",
      itemKey: item.key,
      preview: item.preview || item.text || "",
      timestampMs: Date.now(),
    };
    if (diagnostic.error) {
      console.error("[FairyVoice]", diagnostic);
    } else {
      console.info("[FairyVoice]", diagnostic);
    }
    this.onDiagnostic(diagnostic);
  }
}

function stringifyError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error || "unknown_error");
}
