import { invoke as tauriInvoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useRef } from "react";

import type { CoreClient } from "../core/client";
import type { InvokeFunction } from "../core/tauriTransport";

import { RealtimeCompanion } from "./RealtimeCompanion";
import {
  createRealtimePresencePublisher,
  type RealtimePresenceProjection,
} from "./realtimePresence";

export function RealtimeCompanionWindowApp({
  client,
  invoke = tauriInvoke,
}: {
  client: CoreClient["realtime"];
  invoke?: InvokeFunction;
}) {
  const publisher = useRef<ReturnType<typeof createRealtimePresencePublisher> | null>(
    null,
  );
  const lastPresence = useRef<RealtimePresenceProjection | null>(null);
  useEffect(() => {
    const current = createRealtimePresencePublisher();
    publisher.current = current;
    current.publish(lastPresence.current);
    return () => {
      if (publisher.current === current) publisher.current = null;
      current.close();
    };
  }, []);
  const publishPresence = useCallback((projection: RealtimePresenceProjection | null) => {
    lastPresence.current = projection;
    publisher.current?.publish(projection);
  }, []);
  const hideWindow = useCallback(
    () => void invoke<void>("hide_companion_window"),
    [invoke],
  );

  return (
    <RealtimeCompanion
      client={client}
      hostInvoke={invoke}
      onClose={hideWindow}
      onPresenceChange={publishPresence}
      windowMode
    />
  );
}
