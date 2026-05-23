import { useCallback, useMemo, useRef, useState } from "react";

export interface AttendingState {
  isAttending: boolean;
  attendingSince: number | null;
  startAttending: () => void;
  stopAttending: () => void;
}

export function useAttendingState(): AttendingState {
  const [attendingSince, setAttendingSince] = useState<number | null>(null);
  const startTimerRef = useRef<number | null>(null);

  const startAttending = useCallback(() => {
    if (startTimerRef.current !== null) {
      window.clearTimeout(startTimerRef.current);
      startTimerRef.current = null;
    }
    setAttendingSince(Date.now());
  }, []);

  const stopAttending = useCallback(() => {
    if (startTimerRef.current !== null) {
      window.clearTimeout(startTimerRef.current);
    }
    startTimerRef.current = window.setTimeout(() => {
      setAttendingSince(null);
      startTimerRef.current = null;
    }, 250);
  }, []);

  return useMemo(
    () => ({
      isAttending: attendingSince !== null,
      attendingSince,
      startAttending,
      stopAttending,
    }),
    [attendingSince, startAttending, stopAttending],
  );
}
