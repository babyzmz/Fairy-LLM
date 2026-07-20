import type { PresenceRenderSnapshot } from "./presenceRenderer";

export interface LiquidOpticsUniformState {
  render_origin: readonly [number, number];
  monitor_origin: readonly [number, number];
  monitor_size: readonly [number, number];
  device_scale: number;
  refraction_px: number;
  dispersion_px: number;
  caustic_strength: number;
  lens_strength: number;
  rim_strength: number;
  shadow_strength: number;
}

export const LIQUID_OPTICS_LIMITS = Object.freeze({
  minimum_dispersion_physical_px: 0.12,
  active_dispersion_physical_px: 0.35,
  maximum_dispersion_physical_px: 0.45,
  idle_refraction_logical_px: 5.5,
  active_refraction_logical_px: 8.5,
  maximum_refraction_logical_px: 10,
  idle_caustic_strength: 0.22,
  active_caustic_strength: 0.4,
  idle_lens_strength: 0.76,
  active_lens_strength: 1,
  idle_rim_strength: 0.76,
  active_rim_strength: 0.94,
  idle_shadow_strength: 0.1,
  active_shadow_strength: 0.16,
});

export function liquidOpticsForSnapshot(
  snapshot: PresenceRenderSnapshot,
  canvasWidth: number,
  canvasHeight: number,
  devicePixelRatio: number,
): LiquidOpticsUniformState {
  const deviceScale = clampFinite(devicePixelRatio, 0.5, 4, 1);
  const fallbackWidth = physicalDimension(canvasWidth, deviceScale);
  const fallbackHeight = physicalDimension(canvasHeight, deviceScale);
  const placement = snapshot.interaction?.placement;
  const monitorWidth = positiveFinite(placement?.monitor_work_area.width, fallbackWidth);
  const monitorHeight = positiveFinite(placement?.monitor_work_area.height, fallbackHeight);
  const activity = opticalActivity(snapshot);
  const dispersionPhysical = mix(
    LIQUID_OPTICS_LIMITS.minimum_dispersion_physical_px,
    LIQUID_OPTICS_LIMITS.active_dispersion_physical_px,
    activity,
  );
  const refractionLogical = mix(
    LIQUID_OPTICS_LIMITS.idle_refraction_logical_px,
    LIQUID_OPTICS_LIMITS.active_refraction_logical_px,
    activity,
  );
  const causticStrength = mix(
    LIQUID_OPTICS_LIMITS.idle_caustic_strength,
    LIQUID_OPTICS_LIMITS.active_caustic_strength,
    activity,
  );
  const lensStrength = mix(
    LIQUID_OPTICS_LIMITS.idle_lens_strength,
    LIQUID_OPTICS_LIMITS.active_lens_strength,
    activity,
  );
  const rimStrength = mix(
    LIQUID_OPTICS_LIMITS.idle_rim_strength,
    LIQUID_OPTICS_LIMITS.active_rim_strength,
    activity,
  );
  const shadowStrength = mix(
    LIQUID_OPTICS_LIMITS.idle_shadow_strength,
    LIQUID_OPTICS_LIMITS.active_shadow_strength,
    activity,
  );
  const reducedTransparency = snapshot.reduced_transparency === true;
  const increasedContrast = snapshot.increased_contrast === true;

  return Object.freeze({
    render_origin: Object.freeze([
      finiteOr(placement?.render_frame.x, 0),
      finiteOr(placement?.render_frame.y, 0),
    ]) as readonly [number, number],
    monitor_origin: Object.freeze([
      finiteOr(placement?.monitor_work_area.x, 0),
      finiteOr(placement?.monitor_work_area.y, 0),
    ]) as readonly [number, number],
    monitor_size: Object.freeze([monitorWidth, monitorHeight]) as readonly [number, number],
    device_scale: deviceScale,
    refraction_px: Math.min(
      refractionLogical,
      LIQUID_OPTICS_LIMITS.maximum_refraction_logical_px,
    ) * deviceScale * (reducedTransparency ? 0.35 : 1),
    dispersion_px: reducedTransparency || increasedContrast
      ? 0
      : Math.min(
          dispersionPhysical,
          LIQUID_OPTICS_LIMITS.maximum_dispersion_physical_px,
        ),
    caustic_strength: causticStrength * (reducedTransparency ? 0.45 : 1),
    lens_strength: lensStrength * (reducedTransparency ? 0.42 : 1),
    rim_strength: increasedContrast ? Math.max(0.96, rimStrength) : rimStrength,
    shadow_strength: increasedContrast
      ? Math.max(0.18, shadowStrength)
      : shadowStrength,
  });
}

function opticalActivity(snapshot: PresenceRenderSnapshot): number {
  if (snapshot.sleeping) return 0;
  let activity = 0;
  const interaction = snapshot.interaction;
  if (interaction?.cursor.band === "aware") activity = 0.38;
  if (interaction?.cursor.band === "active") activity = 1;
  if (
    interaction !== null &&
    interaction !== undefined &&
    !["idle", "aware", "suspended"].includes(interaction.phase)
  ) {
    activity = Math.max(activity, 0.72);
  }
  if (snapshot.work_state !== "idle") activity = Math.max(activity, 0.78);
  if (snapshot.speaking) activity = 1;
  return activity;
}

function physicalDimension(logicalSize: number, scale: number): number {
  return Math.max(1, Math.round(positiveFinite(logicalSize, 1) * scale));
}

function positiveFinite(value: number | undefined, fallback: number): number {
  return value !== undefined && Number.isFinite(value) && value > 0 ? value : fallback;
}

function finiteOr(value: number | undefined, fallback: number): number {
  return value !== undefined && Number.isFinite(value) ? value : fallback;
}

function clampFinite(
  value: number,
  minimum: number,
  maximum: number,
  fallback: number,
): number {
  const finite = Number.isFinite(value) ? value : fallback;
  return Math.min(maximum, Math.max(minimum, finite));
}

function mix(start: number, end: number, progress: number): number {
  return start + (end - start) * Math.min(1, Math.max(0, progress));
}
