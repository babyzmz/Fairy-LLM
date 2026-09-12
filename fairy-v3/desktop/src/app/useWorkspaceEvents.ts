import { useEffect, useRef, useState } from "react";

import type { EventEnvelope } from "../core/client";
import { runResilientEventDelivery } from "../core/eventStream";
import type { WorkspaceClient } from "./workspaceTypes";
import { readEventCheckpoint, writeEventCheckpoint } from "./workspacePreferences";
import { appendEvent, coreErrorCode, errorMessage } from "./workspaceModelUtils";

export function useWorkspaceEvents(
  client: WorkspaceClient,
  enabled: boolean,
  queueEventInvalidation: (event: EventEnvelope) => void,
) {
  const [allEvents, setAllEvents] = useState<EventEnvelope[]>([]);
  const eventCheckpoint = useRef(readEventCheckpoint());
  const [eventStreamError, setEventStreamError] = useState<string | null>(null);
  const [eventStreamErrorCode, setEventStreamErrorCode] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setEventStreamError(null);
    setEventStreamErrorCode(null);
    void runResilientEventDelivery(
      {
        sourceId: client.events.sourceId(),
        state: client.events.state,
        list: client.events.list,
        subscribe: client.events.subscribe,
      },
      {
        checkpoint: eventCheckpoint.current,
        signal: controller.signal,
        onCheckpoint(checkpoint) {
          eventCheckpoint.current = checkpoint;
          writeEventCheckpoint(checkpoint);
        },
        onEvent(event) {
          if (event.visibility !== "internal") {
            setAllEvents((current) => appendEvent(current, event));
          }
          queueEventInvalidation(event);
        },
        onError(error) {
          if (controller.signal.aborted) return;
          setEventStreamError(errorMessage(error));
          setEventStreamErrorCode(coreErrorCode(error));
        },
        onRecovered() {
          if (controller.signal.aborted) return;
          setEventStreamError(null);
          setEventStreamErrorCode(null);
        },
      },
    );
    return () => controller.abort();
  }, [client, enabled, queueEventInvalidation]);

  return { allEvents, eventStreamError, eventStreamErrorCode };
}
