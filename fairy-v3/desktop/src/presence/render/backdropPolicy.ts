import type { PresenceRenderSnapshot } from "./presenceRenderer";

export function shouldCaptureBackdrop(snapshot: PresenceRenderSnapshot): boolean {
  return snapshot.optics_mode === "enhanced"
    && snapshot.interaction !== null
    && snapshot.interaction.phase !== "repositioning";
}
