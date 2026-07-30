import { RotateCcw } from "lucide-react";

import type {
  RealtimeRetryableMediaChannel,
  RealtimeWorkerStatus,
} from "../core/client";
import {
  isRetryableMediaChannel,
  mediaChannelLabel,
  type CaptureSurface,
} from "./realtimeCompanionModel";

export function RealtimeMediaStatus({
  busy,
  captureScope,
  channels,
  onReplaceSource,
  onRetry,
  onSourceChange,
  sourceId,
  surfaces,
}: {
  busy: boolean;
  captureScope: RealtimeWorkerStatus["capture_scope"];
  channels: RealtimeWorkerStatus["media_channels"];
  onReplaceSource(): void;
  onRetry(channel: RealtimeRetryableMediaChannel): void;
  onSourceChange(sourceId: string): void;
  sourceId: string;
  surfaces: CaptureSurface[];
}) {
  return (
    <>
      {channels.length > 0 ? (
        <section className="realtime-media-channels" aria-label="Realtime media channels">
          {channels.map((channel) => (
            <div key={channel.channel} data-status={channel.status}>
              <span>{mediaChannelLabel(channel.channel)}</span>
              <strong>{channel.status.replace("_", " ")}</strong>
              {channel.status === "unavailable" && isRetryableMediaChannel(channel.channel) ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    if (isRetryableMediaChannel(channel.channel)) {
                      onRetry(channel.channel);
                    }
                  }}
                >
                  <RotateCcw size={12} /> Retry
                </button>
              ) : null}
            </div>
          ))}
        </section>
      ) : null}
      {captureScope !== null && !captureScope.source_available ? (
        <div className="realtime-source-recovery" role="status">
          <span>The observed window is unavailable. Microphone conversation can continue.</span>
          {captureScope.mode === "selected_window" ? (
            <div>
              <select
                aria-label="Replacement observed window"
                value={sourceId}
                onChange={(event) => onSourceChange(event.target.value)}
                disabled={busy}
              >
                {surfaces.map((surface) => (
                  <option key={surface.source_id} value={surface.source_id}>
                    {surface.label} · {surface.width}×{surface.height}
                  </option>
                ))}
              </select>
              <button
                type="button"
                disabled={busy || sourceId === ""}
                onClick={onReplaceSource}
              >
                Use window
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

export function RealtimeCaptions({
  captions,
  draftCaption,
}: {
  captions: string[];
  draftCaption: string;
}) {
  return (
    <div className="realtime-captions" aria-live="polite">
      {captions.length === 0 && draftCaption === "" ? (
        <span>Listening for the conversation and observed context…</span>
      ) : (
        <>
          {captions.map((text, index) => (
            <p key={`${index}-${text.slice(0, 16)}`}>{text}</p>
          ))}
          {draftCaption ? <p className="is-streaming">{draftCaption}</p> : null}
        </>
      )}
    </div>
  );
}
