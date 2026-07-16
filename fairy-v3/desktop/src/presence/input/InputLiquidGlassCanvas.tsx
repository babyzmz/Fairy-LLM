import { isTauri } from "@tauri-apps/api/core";
import { useLayoutEffect, useRef } from "react";

import {
  resolvePresenceExperimentMode,
  type PresenceExperimentMode,
} from "../diagnostics/experimentMode";
import {
  PRESENCE_RUNTIME_METRICS_RESET_EVENT,
  PresenceRuntimeMetrics,
  writeRuntimeMetricsDataset,
} from "../diagnostics/PresenceRuntimeMetrics";
import { NativeBackdropStream } from "../render/nativeBackdrop";
import { InputLiquidGlassRenderer } from "./inputLiquidGlassRenderer";

export function InputLiquidGlassCanvas({
  paused = false,
  experimentMode = resolvePresenceExperimentMode(),
}: {
  paused?: boolean;
  experimentMode?: PresenceExperimentMode;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    const field = canvas?.parentElement;
    if (canvas == null || field == null) return;
    field.dataset.experimentMode = experimentMode;
    if (paused) {
      canvas.dataset.backdropStatus = "paused";
      delete canvas.dataset.backdropError;
      return () => {
        delete field.dataset.experimentMode;
      };
    }
    if (experimentMode === "single-renderer") {
      canvas.dataset.backdropStatus = "disabled";
      canvas.dataset.experimentMode = experimentMode;
      return () => {
        delete field.dataset.experimentMode;
      };
    }
    if (!isTauri()) {
      return () => {
        delete field.dataset.experimentMode;
      };
    }

    let renderer: InputLiquidGlassRenderer;
    try {
      renderer = new InputLiquidGlassRenderer(canvas, {
        refractionEnabled: experimentMode !== "no-refraction",
      });
    } catch (error) {
      canvas.dataset.backdropStatus = "unavailable";
      canvas.dataset.backdropError = safeError(error);
      return () => {
        delete field.dataset.experimentMode;
      };
    }

    const stream = new NativeBackdropStream();
    const metrics = new PresenceRuntimeMetrics();
    const resetMetrics = () => {
      metrics.reset();
      writeRuntimeMetricsDataset(canvas, metrics.snapshot());
    };
    let scheduledFrame: number | null = null;
    const update = () => {
      scheduledFrame = null;
      const bounds = field.getBoundingClientRect();
      if (bounds.width < 1 || bounds.height < 1) return;
      const dpr = window.devicePixelRatio || 1;
      const radius = Number.parseFloat(getComputedStyle(field).borderTopLeftRadius);
      renderer.resize(bounds.width, bounds.height, dpr);
      renderer.clear();
      canvas.dataset.backdropStatus = "starting";
      stream.start({
        framesPerSecond: 30,
        experimentMode,
        geometry: {
          visible: true,
          left: bounds.left,
          top: bounds.top,
          width: bounds.width,
          height: bounds.height,
          border_radius: Number.isFinite(radius) ? radius : bounds.height / 2,
          device_pixel_ratio: dpr,
        },
        onFrame: (frame) => {
          metrics.recordBackdrop({
            sequence: frame.sequence,
            captured_at_ms: frame.capturedAtMs,
            capture_total_ms: frame.captureTotalMs,
            frame_pack_ms: frame.framePackMs,
            ipc_roundtrip_ms: frame.ipcRoundtripMs,
            js_parse_ms: frame.jsParseMs,
          });
          const uploadStartedAt = performance.now();
          renderer.render(frame);
          metrics.recordTextureUploadCpu(performance.now() - uploadStartedAt);
          writeRuntimeMetricsDataset(canvas, metrics.snapshot());
          canvas.dataset.backdropStatus = "ready";
          canvas.dataset.backdropSequence = String(frame.sequence);
          canvas.dataset.backdropAgeMs = String(
            Math.max(0, Date.now() - frame.capturedAtMs),
          );
          delete canvas.dataset.backdropError;
        },
        onError: (error) => {
          renderer.clear();
          canvas.dataset.backdropStatus = "unavailable";
          canvas.dataset.backdropError = safeError(error);
        },
      });
    };
    const scheduleUpdate = () => {
      if (scheduledFrame !== null) return;
      scheduledFrame = window.requestAnimationFrame(update);
    };

    scheduleUpdate();
    const observer = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(scheduleUpdate);
    observer?.observe(field);
    window.addEventListener("resize", scheduleUpdate);
    if (import.meta.env.DEV) {
      window.addEventListener(
        PRESENCE_RUNTIME_METRICS_RESET_EVENT,
        resetMetrics,
      );
    }
    return () => {
      if (scheduledFrame !== null) window.cancelAnimationFrame(scheduledFrame);
      observer?.disconnect();
      window.removeEventListener("resize", scheduleUpdate);
      if (import.meta.env.DEV) {
        window.removeEventListener(
          PRESENCE_RUNTIME_METRICS_RESET_EVENT,
          resetMetrics,
        );
      }
      stream.stop();
      renderer.dispose();
      delete field.dataset.experimentMode;
    };
  }, [experimentMode, paused]);

  return (
    <canvas
      aria-hidden="true"
      className="presence-input-glass"
      data-backdrop-status="idle"
      data-paused={String(paused)}
      data-experiment-mode={experimentMode}
      data-testid="presence-input-glass"
      ref={canvasRef}
    />
  );
}

function safeError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.slice(0, 96);
}
