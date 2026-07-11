import { useEffect, useMemo, useRef } from "react";

import type { EventEnvelope } from "../core/contracts";

import { createPresenceChannel, type PresenceChannel } from "./channel";
import { PresenceProjection } from "./projection";

interface PresenceBridgeProps {
  events: readonly EventEnvelope[];
  channelFactory?: () => PresenceChannel;
}

export function PresenceBridge({
  events,
  channelFactory = createPresenceChannel,
}: PresenceBridgeProps) {
  const projection = useMemo(
    () =>
      [...events]
        .sort((left, right) => left.cursor - right.cursor)
        .reduce(PresenceProjection.reduce, PresenceProjection.initial()),
    [events],
  );
  const projectionRef = useRef(projection);
  const channelRef = useRef<PresenceChannel | null>(null);
  projectionRef.current = projection;

  useEffect(() => {
    const channel = channelFactory();
    channelRef.current = channel;
    const stopRequests = channel.onRequest((request) => {
      if (request === "projection") {
        channel.publishProjection(projectionRef.current);
      } else {
        void toggleWorkspaceWindow();
      }
    });
    channel.publishProjection(projectionRef.current);
    return () => {
      stopRequests();
      channel.close();
      channelRef.current = null;
    };
  }, [channelFactory]);

  useEffect(() => {
    channelRef.current?.publishProjection(projection);
  }, [projection]);

  return null;
}

async function toggleWorkspaceWindow(): Promise<void> {
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    const current = getCurrentWindow();
    if (await current.isVisible()) {
      await current.hide();
    } else {
      await current.show();
      await current.setFocus();
    }
  } catch {
    // Browser previews do not expose native window controls.
  }
}
