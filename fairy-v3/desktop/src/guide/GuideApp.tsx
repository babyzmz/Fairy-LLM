import { useEffect, useState } from "react";

import { createPresenceChannel, type PresenceChannel } from "../presence/channel";
import {
  PresenceProjection,
  type PresenceProjectionState,
} from "../presence/projection";
import "../presence/presence.css";

interface GuideAppProps {
  channel?: PresenceChannel;
}

export function GuideApp({ channel: suppliedChannel }: GuideAppProps) {
  const [channel] = useState(() => suppliedChannel ?? createPresenceChannel());
  const [projection, setProjection] = useState<PresenceProjectionState>(() =>
    PresenceProjection.initial(),
  );

  useEffect(() => {
    const stop = channel.onProjection(setProjection);
    channel.requestProjection();
    return () => {
      stop();
    };
  }, [channel]);

  useEffect(() => {
    if (suppliedChannel !== undefined) return;
    const close = () => channel.close();
    window.addEventListener("beforeunload", close, { once: true });
    return () => window.removeEventListener("beforeunload", close);
  }, [channel, suppliedChannel]);

  return (
    <main className="guide-window" data-activity={projection.activity}>
      <img
        alt=""
        aria-hidden="true"
        draggable={false}
        height="42"
        src="/presence/fairy-blue-ring.webp"
        width="42"
      />
      <div aria-live="polite">
        <strong>{projection.status_text}</strong>
        <span>Fairy is present</span>
      </div>
    </main>
  );
}
