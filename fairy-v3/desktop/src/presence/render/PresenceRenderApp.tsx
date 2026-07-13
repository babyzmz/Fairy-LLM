import { useEffect, useState } from "react";

import {
  derivePresenceView,
  PresenceProjection,
  type PresenceProjectionState,
} from "../domain/projection";
import type { PresenceInteractionSnapshot } from "../domain/interaction";
import {
  createPresenceInteractionSource,
  type PresenceInteractionSource,
} from "../transport/interactionEvents";
import {
  createPresenceChannel,
  type PresenceChannel,
} from "../transport/presenceChannel";
import { PresenceRendererCanvas } from "./PresenceRendererCanvas";
import "../presence.css";
import "./presence-render.css";

interface PresenceRenderAppProps {
  channel?: PresenceChannel;
  interactionSource?: PresenceInteractionSource;
  now?: () => number;
}

export function PresenceRenderApp({
  channel: suppliedChannel,
  interactionSource: suppliedInteractionSource,
  now = Date.now,
}: PresenceRenderAppProps) {
  const [channel] = useState(() => suppliedChannel ?? createPresenceChannel());
  const [interactionSource] = useState(
    () => suppliedInteractionSource ?? createPresenceInteractionSource(),
  );
  const [projection, setProjection] = useState<PresenceProjectionState>(() =>
    PresenceProjection.initial(),
  );
  const [clock, setClock] = useState(() => now());
  const [interaction, setInteraction] = useState<PresenceInteractionSnapshot | null>(null);

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

  useEffect(() => {
    let disposed = false;
    let stop: (() => void) | undefined;
    void interactionSource.subscribe((snapshot) => {
      if (!disposed) {
        setInteraction((current) =>
          current !== null && current.sequence >= snapshot.sequence ? current : snapshot,
        );
      }
    }).then((unlisten) => {
      if (disposed) unlisten();
      else stop = unlisten;
    });
    return () => {
      disposed = true;
      stop?.();
    };
  }, [interactionSource]);

  const view = derivePresenceView(projection, {
    now_ms: clock,
    quiet_mode: false,
    dismissed_notice_ids: [],
  });
  const reducedMotion =
    interaction?.reduced_motion === true ||
    (typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  return (
    <main
      aria-hidden="true"
      className="presence-render-window"
      data-cursor-band={interaction?.cursor.band ?? "outside"}
      data-expansion-direction={interaction?.placement.expansion_direction ?? "right"}
      data-interaction-phase={interaction?.phase ?? "idle"}
      data-reduced-motion={String(reducedMotion)}
      data-testid="presence-render-surface"
    >
      <PresenceRendererCanvas
        snapshot={{
          interaction,
          reduced_motion: reducedMotion,
          sleeping: view.density === "quiet",
          speaking: projection.speaking,
          work_state: view.work_state,
        }}
      />
    </main>
  );
}
