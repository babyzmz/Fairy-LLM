import { isTauri } from "@tauri-apps/api/core";
import { useLayoutEffect, useRef } from "react";

import { NativeBackdropStream } from "../render/nativeBackdrop";
import { InputLiquidGlassRenderer } from "./inputLiquidGlassRenderer";

export function InputLiquidGlassCanvas({ paused = false }: { paused?: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    const field = canvas?.parentElement;
    if (canvas == null || field == null) return;
    if (paused) {
      canvas.dataset.backdropStatus = "paused";
      delete canvas.dataset.backdropError;
      return;
    }
    if (!isTauri()) return;

    let renderer: InputLiquidGlassRenderer;
    try {
      renderer = new InputLiquidGlassRenderer(canvas);
    } catch (error) {
      canvas.dataset.backdropStatus = "unavailable";
      canvas.dataset.backdropError = safeError(error);
      return;
    }

    const stream = new NativeBackdropStream();
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
          renderer.render(frame);
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
    return () => {
      if (scheduledFrame !== null) window.cancelAnimationFrame(scheduledFrame);
      observer?.disconnect();
      window.removeEventListener("resize", scheduleUpdate);
      stream.stop();
      renderer.dispose();
    };
  }, [paused]);

  return (
    <canvas
      aria-hidden="true"
      className="presence-input-glass"
      data-backdrop-status="idle"
      data-paused={String(paused)}
      data-testid="presence-input-glass"
      ref={canvasRef}
    />
  );
}

function safeError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.slice(0, 96);
}
