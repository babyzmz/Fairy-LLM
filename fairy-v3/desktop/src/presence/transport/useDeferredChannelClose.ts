import { useEffect, useRef } from "react";

interface CloseableChannel {
  close(): void;
}

export function useDeferredChannelClose(
  channel: CloseableChannel,
  owned: boolean,
) {
  const pending = useRef(new Map<CloseableChannel, number>());

  useEffect(() => {
    if (!owned) return;
    const strictModeProbe = pending.current.get(channel);
    if (strictModeProbe !== undefined) {
      window.clearTimeout(strictModeProbe);
      pending.current.delete(channel);
    }
    return () => {
      const timer = window.setTimeout(() => {
        if (pending.current.get(channel) !== timer) return;
        pending.current.delete(channel);
        channel.close();
      }, 0);
      pending.current.set(channel, timer);
    };
  }, [channel, owned]);
}
