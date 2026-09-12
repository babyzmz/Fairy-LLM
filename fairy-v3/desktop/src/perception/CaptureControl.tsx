import { invoke } from "@tauri-apps/api/core";
import {
  Camera,
  Check,
  Image as ImageIcon,
  LoaderCircle,
  MonitorUp,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { z } from "zod";

const captureSurfaceSchema = z
  .object({
    kind: z.enum(["display", "window"]),
    source_id: z.string().regex(/^\d{1,10}$/),
    label: z.string().min(1).max(255),
    width: z.number().int().min(1).max(16_384),
    height: z.number().int().min(1).max(16_384),
    is_primary: z.boolean(),
  })
  .strict();

const captureResultSchema = z
  .object({
    kind: z.enum(["display", "window"]),
    source_id: z.string().regex(/^\d{1,10}$/),
    source_label: z.string().min(1).max(255),
    media_type: z.literal("image/png"),
    png_base64: z.string().min(1).max(28_000_000),
    width: z.number().int().min(1).max(16_384),
    height: z.number().int().min(1).max(16_384),
    byte_length: z.number().int().min(1).max(20 * 1024 * 1024),
    content_hash: z.string().regex(/^[0-9a-f]{64}$/),
    captured_at_ms: z.number().int().min(0),
  })
  .strict();

export type CaptureSurface = z.infer<typeof captureSurfaceSchema>;
export type CaptureResult = z.infer<typeof captureResultSchema>;

export interface PendingImageAttachment extends CaptureResult {
  persistence: "ephemeral" | "conversation";
}

export interface CaptureClient {
  listSurfaces(): Promise<CaptureSurface[]>;
  capture(request: {
    kind: CaptureSurface["kind"];
    source_id: string;
  }): Promise<CaptureResult>;
}

const tauriCaptureClient: CaptureClient = {
  async listSurfaces() {
    return z
      .array(captureSurfaceSchema)
      .max(256)
      .parse(await invoke("list_capture_surfaces"));
  },
  async capture(request) {
    return captureResultSchema.parse(await invoke("capture_surface", { request }));
  },
};

interface CaptureControlProps {
  disabled: boolean;
  visionAvailable: boolean;
  value: PendingImageAttachment | null;
  onChange(value: PendingImageAttachment | null): void;
  client?: CaptureClient;
}

export function CaptureControl({
  disabled,
  visionAvailable,
  value,
  onChange,
  client = tauriCaptureClient,
}: CaptureControlProps) {
  const [open, setOpen] = useState(false);
  const [surfaces, setSurfaces] = useState<CaptureSurface[]>([]);
  const [selection, setSelection] = useState("");
  const [preview, setPreview] = useState<CaptureResult | null>(null);
  const [keep, setKeep] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const unavailable = disabled || !visionAvailable;
  const generation = useRef(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => {
    generation.current += 1;
    setOpen(false);
    setBusy(false);
    setPreview(null);
    setSurfaces([]);
    setSelection("");
    setKeep(false);
    setError(null);
  }, []);

  useLayoutEffect(() => {
    if (unavailable) close();
    return () => { generation.current += 1; };
  }, [unavailable, client, close]);

  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      close();
      triggerRef.current?.focus();
    };
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !containerRef.current?.contains(event.target)) close();
    };
    window.addEventListener("keydown", escape, true);
    window.addEventListener("pointerdown", outside, true);
    return () => {
      window.removeEventListener("keydown", escape, true);
      window.removeEventListener("pointerdown", outside, true);
    };
  }, [open, close]);

  const openPicker = async () => {
    if (unavailable || busy) return;
    const request = ++generation.current;
    setOpen(true);
    setPreview(null);
    setKeep(false);
    setError(null);
    setBusy(true);
    try {
      const items = await client.listSurfaces();
      if (generation.current !== request) return;
      setSurfaces(items);
      setSelection(items.length > 0 ? surfaceKey(items[0]) : "");
      if (items.length === 0) setError("No capturable display or window is available");
    } catch (caught) {
      if (generation.current !== request) return;
      setSurfaces([]);
      setSelection("");
      setError(errorMessage(caught, "Screen capture is unavailable"));
    } finally {
      if (generation.current === request) setBusy(false);
    }
  };

  const capture = async () => {
    const surface = surfaces.find((item) => surfaceKey(item) === selection);
    if (surface === undefined || busy || unavailable) return;
    const request = ++generation.current;
    setBusy(true);
    setError(null);
    try {
      const result = await client.capture({
        kind: surface.kind,
        source_id: surface.source_id,
      });
      if (generation.current !== request) return;
      if (
        result.kind !== surface.kind ||
        result.source_id !== surface.source_id ||
        result.source_label !== surface.label
      ) {
        throw new Error("Captured surface identity changed");
      }
      setPreview(result);
    } catch (caught) {
      if (generation.current !== request) return;
      setPreview(null);
      setError(errorMessage(caught, "Could not capture the selected source"));
    } finally {
      if (generation.current === request) setBusy(false);
    }
  };

  return (
    <div className="capture-control" ref={containerRef}>
      <button
        ref={triggerRef}
        className={`icon-button capture-button ${value ? "active" : ""}`}
        type="button"
        aria-label="Capture screen"
        title={
          visionAvailable
            ? value
              ? "Replace screen capture"
              : "Capture screen"
            : "Selected provider has no vision capability"
        }
        disabled={unavailable || busy}
        onClick={() => void openPicker()}
      >
        {busy && !open ? (
          <LoaderCircle className="spin" size={16} />
        ) : value ? (
          <ImageIcon size={17} />
        ) : (
          <MonitorUp size={17} />
        )}
      </button>

      {value ? (
        <span className="capture-attached" title={value.source_label}>
          <img
            src={`data:image/png;base64,${value.png_base64}`}
            alt=""
            aria-hidden="true"
          />
          <span>{value.source_label}</span>
          <button
            type="button"
            aria-label="Remove screen capture"
            title="Remove screen capture"
            onClick={() => onChange(null)}
          >
            <X size={13} />
          </button>
        </span>
      ) : null}

      {open ? (
        <div className="capture-popover" role="dialog" aria-label="Screen capture">
          <header>
            <strong>Screen capture</strong>
            <button type="button" aria-label="Close screen capture" title="Close" onClick={close}>
              <X size={15} />
            </button>
          </header>
          {preview === null ? (
            <div className="capture-source-row">
              <label>
                <span>Source</span>
                <select
                  aria-label="Capture source"
                  value={selection}
                  disabled={busy || surfaces.length === 0}
                  onChange={(event) => setSelection(event.target.value)}
                >
                  {surfaces.map((surface) => (
                    <option key={surfaceKey(surface)} value={surfaceKey(surface)}>
                      {surface.label} ({surface.width} x {surface.height})
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="primary-command"
                type="button"
                aria-label="Capture selected source"
                disabled={busy || selection.length === 0}
                onClick={() => void capture()}
              >
                {busy ? <LoaderCircle className="spin" size={14} /> : <Camera size={14} />}
                Capture
              </button>
            </div>
          ) : (
            <div className="capture-preview">
              <img
                src={`data:image/png;base64,${preview.png_base64}`}
                alt={`${preview.source_label} capture preview`}
              />
              <div className="capture-preview-meta">
                <span>{preview.source_label}</span>
                <span>{preview.width} x {preview.height}</span>
              </div>
              <label className="capture-persistence">
                <input
                  type="checkbox"
                  checked={keep}
                  onChange={(event) => setKeep(event.target.checked)}
                />
                <span>Keep with conversation</span>
              </label>
              <div className="capture-decisions">
                <button
                  className="secondary-command"
                  type="button"
                  aria-label="Discard capture"
                  onClick={close}
                >
                  <Trash2 size={14} /> Discard
                </button>
                <button
                  className="primary-command"
                  type="button"
                  aria-label="Attach capture"
                  disabled={unavailable || busy}
                  onClick={() => {
                    onChange({
                      ...preview,
                      persistence: keep ? "conversation" : "ephemeral",
                    });
                    close();
                  }}
                >
                  <Check size={14} /> Attach
                </button>
              </div>
            </div>
          )}
          {error ? <div className="composer-error" role="alert">{error}</div> : null}
        </div>
      ) : null}
    </div>
  );
}

function surfaceKey(surface: CaptureSurface): string {
  return `${surface.kind}:${surface.source_id}`;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
