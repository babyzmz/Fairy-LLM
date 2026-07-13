import type { PresenceInteractionSnapshot } from "../domain/interaction";

export interface PresenceInputGate {
  window_visible: boolean;
  content_visible: boolean;
  interactive: boolean;
}

const CLOSED_GATE: PresenceInputGate = Object.freeze({
  window_visible: false,
  content_visible: false,
  interactive: false,
});

export function presenceInputGate(
  snapshot: PresenceInteractionSnapshot | null,
  suppressed = false,
): PresenceInputGate {
  if (snapshot === null || suppressed) return CLOSED_GATE;
  const elapsed = Math.max(0, snapshot.sampled_at_ms - snapshot.phase_started_at_ms);
  if (snapshot.phase === "input_reveal") {
    return {
      window_visible: true,
      content_visible: snapshot.reduced_motion || elapsed >= 130,
      interactive: false,
    };
  }
  if (snapshot.phase === "interactive") {
    return {
      window_visible: true,
      content_visible: true,
      interactive: true,
    };
  }
  if (snapshot.phase === "returning") {
    return {
      window_visible: true,
      content_visible: snapshot.reduced_motion ? false : elapsed < 150,
      interactive: false,
    };
  }
  return CLOSED_GATE;
}
