import { useCallback, useEffect, useState } from "react";

export function readEventCursor(): number {
  try {
    const cursor = Number.parseInt(window.localStorage.getItem("fairy.events.cursor") ?? "0", 10);
    return Number.isSafeInteger(cursor) && cursor >= 0 ? cursor : 0;
  } catch {
    return 0;
  }
}

export function writeEventCursor(cursor: number): void {
  try {
    window.localStorage.setItem("fairy.events.cursor", String(cursor));
  } catch {
    // Event delivery remains correct in memory when persistence is unavailable.
  }
}

export function usePersistedSelection(
  key: string,
): [string | null, (value: string | null) => void] {
  const [value, setValue] = useState<string | null>(() => {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  });
  const update = useCallback(
    (next: string | null) => {
      setValue(next);
      try {
        if (next === null) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, next);
      } catch {
        // Selection persistence is optional; Core remains authoritative.
      }
    },
    [key],
  );
  useStorageSync(key, (next) => setValue(next));
  return [value, update];
}

export function usePersistedEnum<T extends string>(
  key: string,
  fallback: T,
  allowed: readonly T[],
): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = window.localStorage.getItem(key) as T | null;
      return stored !== null && allowed.includes(stored) ? stored : fallback;
    } catch {
      return fallback;
    }
  });
  const update = useCallback(
    (next: T) => {
      setValue(next);
      try {
        window.localStorage.setItem(key, next);
      } catch {
        // Preference persistence is optional.
      }
    },
    [key],
  );
  useStorageSync(key, (next) => {
    if (next !== null && allowed.includes(next as T)) setValue(next as T);
  });
  return [value, update];
}

export function usePersistedBoolean(
  key: string,
  fallback: boolean,
): [boolean, (value: boolean) => void] {
  const [value, setValue] = useState(() => {
    try {
      const stored = window.localStorage.getItem(key);
      return stored === null ? fallback : stored === "true";
    } catch {
      return fallback;
    }
  });
  const update = useCallback(
    (next: boolean) => {
      setValue(next);
      try {
        window.localStorage.setItem(key, String(next));
      } catch {
        // Preference persistence is optional.
      }
    },
    [key],
  );
  useStorageSync(key, (next) => {
    if (next === "true" || next === "false") setValue(next === "true");
  });
  return [value, update];
}

function useStorageSync(key: string, update: (value: string | null) => void): void {
  useEffect(() => {
    const listener = (event: StorageEvent) => {
      if (event.key === key) update(event.newValue);
    };
    window.addEventListener("storage", listener);
    return () => window.removeEventListener("storage", listener);
  }, [key, update]);
}
