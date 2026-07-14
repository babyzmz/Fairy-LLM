import { Gauge } from "lucide-react";
import { useRef, useState } from "react";

import "./media-viewer.css";

interface CaptionTrack {
  label: string;
  src: string;
  language: string;
}

export default function MediaViewer({
  src,
  mediaType,
  title,
  captions,
}: {
  src: string;
  mediaType: string;
  title: string;
  captions: CaptionTrack[];
}) {
  const mediaRef = useRef<HTMLMediaElement>(null);
  const [rate, setRate] = useState(1);
  const [error, setError] = useState(false);
  const common = {
    controls: true,
    preload: "metadata" as const,
    src,
    onError: () => setError(true),
  };
  const tracks = captions.map((caption, index) => (
    <track
      key={caption.src}
      kind="subtitles"
      src={caption.src}
      srcLang={caption.language}
      label={caption.label}
      default={index === 0}
    />
  ));
  return (
    <div className="media-viewer">
      <div className="media-toolbar">
        <Gauge size={14} />
        <label>
          Playback speed
          <select
            value={rate}
            onChange={(event) => {
              const value = Number(event.target.value);
              setRate(value);
              if (mediaRef.current !== null) mediaRef.current.playbackRate = value;
            }}
          >
            {[0.5, 0.75, 1, 1.25, 1.5, 2].map((value) => (
              <option key={value} value={value}>
                {value}x
              </option>
            ))}
          </select>
        </label>
        <i />
        <span>
          {captions.length === 0
            ? "No captions"
            : `${captions.length} caption track${captions.length === 1 ? "" : "s"}`}
        </span>
      </div>
      <div className={`media-stage ${mediaType.startsWith("audio/") ? "audio" : "video"}`}>
        {error ? <div role="alert">This device cannot decode the media format</div> : null}
        {mediaType.startsWith("audio/") ? (
          <audio
            ref={(node) => {
              mediaRef.current = node;
            }}
            {...common}
            aria-label={title}
          >
            {tracks}
          </audio>
        ) : (
          <video
            ref={(node) => {
              mediaRef.current = node;
            }}
            {...common}
            aria-label={title}
          >
            {tracks}
          </video>
        )}
      </div>
    </div>
  );
}

export type { CaptionTrack };
