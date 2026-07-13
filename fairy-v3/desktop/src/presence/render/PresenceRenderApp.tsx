import { useEffect, useState } from "react";

import {
  derivePresenceView,
  PresenceProjection,
  type PresenceProjectionState,
} from "../domain/projection";
import {
  createPresenceChannel,
  type PresenceChannel,
} from "../transport/presenceChannel";
import { CompatibilityFairyCanvas } from "./CompatibilityFairyCanvas";
import "../presence.css";
import "./presence-render.css";

interface PresenceRenderAppProps {
  channel?: PresenceChannel;
  now?: () => number;
}

export function PresenceRenderApp({
  channel: suppliedChannel,
  now = Date.now,
}: PresenceRenderAppProps) {
  const [channel] = useState(() => suppliedChannel ?? createPresenceChannel());
  const [projection, setProjection] = useState<PresenceProjectionState>(() =>
    PresenceProjection.initial(),
  );
  const [clock, setClock] = useState(() => now());

  useEffect(() => {
    const stop = channel.onProjection((next) => {
      setProjection(next);
      setClock(now());
    });
    channel.requestProjection();
    return stop;
  }, [channel, now]);

  useEffect(() => {
    if (suppliedChannel !== undefined) return;
    return () => channel.close();
  }, [channel, suppliedChannel]);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(now()), 30_000);
    return () => window.clearInterval(timer);
  }, [now]);

  const view = derivePresenceView(projection, {
    now_ms: clock,
    quiet_mode: false,
    dismissed_notice_ids: [],
  });
  const reducedMotion =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  return (
    <main
      aria-hidden="true"
      className="presence-render-window"
      data-reduced-motion={String(reducedMotion)}
      data-testid="presence-render-surface"
    >
      <CompatibilityFairyCanvas
        dragging={false}
        gaze={{ x: 0, y: 0 }}
        hovered={false}
        listening={false}
        reducedMotion={reducedMotion}
        sleeping={view.density === "quiet"}
        speaking={projection.speaking}
        workState={view.work_state}
      />
    </main>
  );
}
