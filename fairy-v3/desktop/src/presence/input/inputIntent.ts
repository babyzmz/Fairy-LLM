export interface PresenceInputIntent {
  pinned: boolean;
  hover_blocked_until_exit: boolean;
}

export type PresenceInputIntentAction =
  | { type: "open" }
  | { type: "toggle"; transient_visible: boolean }
  | { type: "close" }
  | { type: "engage" }
  | { type: "drag_started" }
  | { type: "pointer_exited" };

export const INITIAL_PRESENCE_INPUT_INTENT: PresenceInputIntent = Object.freeze({
  pinned: false,
  hover_blocked_until_exit: false,
});

export function reducePresenceInputIntent(
  current: PresenceInputIntent,
  action: PresenceInputIntentAction,
): PresenceInputIntent {
  switch (action.type) {
    case "open":
    case "engage":
      return {
        pinned: true,
        hover_blocked_until_exit: false,
      };
    case "toggle":
      if (current.pinned || action.transient_visible) {
        return {
          pinned: false,
          hover_blocked_until_exit: true,
        };
      }
      return {
        pinned: true,
        hover_blocked_until_exit: false,
      };
    case "close":
      return {
        pinned: false,
        hover_blocked_until_exit: true,
      };
    case "drag_started":
      return {
        ...current,
        hover_blocked_until_exit: true,
      };
    case "pointer_exited":
      if (!current.hover_blocked_until_exit) return current;
      return {
        ...current,
        hover_blocked_until_exit: false,
      };
  }
}
