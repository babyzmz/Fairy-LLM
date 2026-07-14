import { Maximize2, RotateCcw, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import "./media-viewer.css";

export default function ImageViewer({ src, title }: { src: string; title: string }) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setScale(1);
    setRotation(0);
    setDimensions(null);
    setError(false);
  }, [src]);

  return (
    <div className="image-viewer">
      <div className="media-toolbar">
        <button
          type="button"
          aria-label="Zoom out image"
          disabled={scale <= 0.1}
          onClick={() => setScale((value) => clamp(value - 0.25))}
        >
          <ZoomOut size={15} />
        </button>
        <span>{Math.round(scale * 100)}%</span>
        <button
          type="button"
          aria-label="Zoom in image"
          disabled={scale >= 8}
          onClick={() => setScale((value) => clamp(value + 0.25))}
        >
          <ZoomIn size={15} />
        </button>
        <button
          type="button"
          aria-label="Fit image"
          onClick={() => fitImage(viewportRef.current, dimensions, setScale)}
        >
          <Maximize2 size={15} />
        </button>
        <button type="button" aria-label="Rotate image" onClick={() => setRotation((value) => (value + 90) % 360)}>
          <RotateCcw size={15} />
        </button>
        <i />
        <span>{dimensions === null ? "Loading" : `${dimensions.width} x ${dimensions.height}`}</span>
      </div>
      <div
        ref={viewportRef}
        className="image-viewport"
        onWheel={(event) => {
          if (!event.ctrlKey) return;
          event.preventDefault();
          setScale((value) => clamp(value * (event.deltaY > 0 ? 0.9 : 1.1)));
        }}
      >
        {error ? <div role="alert">Image could not be decoded</div> : null}
        <img
          src={src}
          alt={title}
          draggable={false}
          style={{ transform: `scale(${scale}) rotate(${rotation}deg)` }}
          onLoad={(event) => {
            const image = event.currentTarget;
            const next = { width: image.naturalWidth, height: image.naturalHeight };
            setDimensions(next);
            fitImage(viewportRef.current, next, setScale);
          }}
          onError={() => setError(true)}
        />
      </div>
    </div>
  );
}

function clamp(value: number): number {
  return Math.min(8, Math.max(0.1, value));
}

function fitImage(
  viewport: HTMLDivElement | null,
  dimensions: { width: number; height: number } | null,
  setScale: (value: number) => void,
): void {
  if (viewport === null || dimensions === null || dimensions.width === 0 || dimensions.height === 0) return;
  setScale(
    clamp(
      Math.min((viewport.clientWidth - 40) / dimensions.width, (viewport.clientHeight - 40) / dimensions.height, 1),
    ),
  );
}
