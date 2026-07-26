import { AudioLines, Bot, CircleUserRound } from "lucide-react";

import type { RealtimeTranscriptEntry } from "../core/client";

interface RealtimeTranscriptProps {
  entries: RealtimeTranscriptEntry[];
}

function speakerLabel(speaker: RealtimeTranscriptEntry["speaker"]): string {
  return speaker === "assistant" ? "Fairy" : "You";
}

export function RealtimeTranscript({ entries }: RealtimeTranscriptProps) {
  if (entries.length === 0) return null;
  return (
    <section className="realtime-transcript" aria-label="Voice chat transcript">
      <header className="realtime-transcript-head">
        <AudioLines size={14} />
        <span>Voice chat</span>
      </header>
      {entries.map((entry) => (
        <div
          key={entry.id}
          className={`realtime-transcript-entry realtime-transcript-${entry.speaker}`}
        >
          <span className="realtime-transcript-avatar" aria-hidden="true">
            {entry.speaker === "assistant" ? <Bot size={14} /> : <CircleUserRound size={14} />}
          </span>
          <div className="realtime-transcript-body">
            <strong>{speakerLabel(entry.speaker)}</strong>
            <p>{entry.text}</p>
          </div>
        </div>
      ))}
    </section>
  );
}
