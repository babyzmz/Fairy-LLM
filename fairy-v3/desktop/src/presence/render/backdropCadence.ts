import type { PresenceRenderSnapshot } from "./presenceRenderer";

export const BACKDROP_CAPTURE_RATES = Object.freeze({
  idle: 5,
  aware: 10,
  active: 15,
});

export function backdropFrameRate(snapshot: PresenceRenderSnapshot): number {
  let requested: number = BACKDROP_CAPTURE_RATES.idle;
  const interaction = snapshot.interaction;

  if (snapshot.speaking || snapshot.work_state !== "idle") {
    requested = BACKDROP_CAPTURE_RATES.active;
  } else if (interaction !== null) {
    if (
      interaction.cursor.band === "active" ||
      !["idle", "aware", "suspended"].includes(interaction.phase)
    ) {
      requested = BACKDROP_CAPTURE_RATES.active;
    } else if (
      interaction.phase === "aware" ||
      interaction.cursor.band === "aware"
    ) {
      requested = BACKDROP_CAPTURE_RATES.aware;
    }
  }

  return Math.min(snapshot.frame_rate_limit, requested);
}
