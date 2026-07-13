import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { PresenceRendererHost } from "./PresenceRendererHost";
import {
  type PresenceRendererHealth,
  type PresenceRenderSnapshot,
} from "./presenceRenderer";
import type { PresenceRendererMode } from "./rendererSupport";

interface PresenceRendererCanvasProps {
  requestedMode?: PresenceRendererMode;
  snapshot: PresenceRenderSnapshot;
  onHealth?: (health: PresenceRendererHealth) => void;
}

const INITIAL_HEALTH: PresenceRendererHealth = {
  mode: "compatibility",
  status: "initializing",
  error_code: null,
};

export function PresenceRendererCanvas({
  requestedMode = "auto",
  snapshot,
  onHealth,
}: PresenceRendererCanvasProps) {
  const webglRef = useRef<HTMLCanvasElement>(null);
  const compatibilityRef = useRef<HTMLCanvasElement>(null);
  const hostRef = useRef<PresenceRendererHost | null>(null);
  const snapshotRef = useRef(snapshot);
  snapshotRef.current = snapshot;
  const onHealthRef = useRef(onHealth);
  onHealthRef.current = onHealth;
  const [health, setHealth] = useState(INITIAL_HEALTH);

  useLayoutEffect(() => {
    const webglCanvas = webglRef.current;
    const compatibilityCanvas = compatibilityRef.current;
    if (webglCanvas === null || compatibilityCanvas === null) return;
    let mounted = true;
    const host = new PresenceRendererHost({
      webglCanvas,
      compatibilityCanvas,
      requestedMode,
      initialSnapshot: snapshotRef.current,
      onHealth: (nextHealth) => {
        if (mounted) {
          setHealth(nextHealth);
          onHealthRef.current?.(nextHealth);
        }
      },
    });
    hostRef.current = host;

    const resize = () => {
      const bounds = webglCanvas.parentElement?.getBoundingClientRect();
      host.resize(
        Math.max(1, bounds?.width ?? window.innerWidth),
        Math.max(1, bounds?.height ?? window.innerHeight),
        window.devicePixelRatio,
      );
    };
    resize();
    const observer = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(resize);
    if (webglCanvas.parentElement !== null) observer?.observe(webglCanvas.parentElement);
    const onVisibility = () => {
      if (document.hidden) host.suspend();
      else void host.start();
    };
    document.addEventListener("visibilitychange", onVisibility);
    void host.start();

    return () => {
      mounted = false;
      observer?.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      host.dispose();
      hostRef.current = null;
    };
  }, [requestedMode]);

  useEffect(() => {
    hostRef.current?.setSnapshot(snapshot);
  }, [snapshot]);

  return (
    <div
      className="presence-renderer-stack"
      data-error-code={health.error_code ?? ""}
      data-renderer={health.mode}
      data-renderer-health={health.status}
      data-testid="presence-renderer"
    >
      <canvas
        aria-label="Fairy Liquid Glass"
        className="presence-webgl-canvas"
        data-active="false"
        ref={webglRef}
        role="img"
      />
      <canvas
        aria-hidden="true"
        className="presence-compatibility-canvas"
        data-active="true"
        ref={compatibilityRef}
      />
    </div>
  );
}
