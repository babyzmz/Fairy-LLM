import type { PresenceRenderSnapshot } from "./presenceRenderer";

export interface LiquidAnchor {
  x: number;
  y: number;
}

export interface LiquidCapsuleGeometry {
  center_x: number;
  center_y: number;
  half_width: number;
}

const CORE_ANCHOR_X = 96;
const CORE_ANCHOR_Y = 88;
const CAPSULE_CENTER_Y = 220;
const CAPSULE_EDGE_INSET = 24;
const CAPSULE_HORIZONTAL_PADDING = 8;

export function liquidAnchorForSnapshot(
  snapshot: PresenceRenderSnapshot,
  _logicalWidth: number,
  logicalHeight: number,
  devicePixelRatio: number,
): LiquidAnchor {
  const dpr = Math.min(2, Math.max(0.5, devicePixelRatio));
  const interaction = snapshot.interaction;
  const sizeShiftLogical = Math.max(0, 72 * snapshot.size_scale + 4 - CORE_ANCHOR_X);
  if (interaction === null) {
    return {
      x: (CORE_ANCHOR_X + sizeShiftLogical) * dpr,
      y: logicalHeight * dpr - CORE_ANCHOR_Y * dpr,
    };
  }
  const localX = interaction.placement.anchor.x - interaction.placement.render_frame.x;
  const localY = interaction.placement.anchor.y - interaction.placement.render_frame.y;
  const expansionSign = interaction.placement.expansion_direction === "left" ? -1 : 1;
  return {
    x: localX + sizeShiftLogical * dpr * expansionSign,
    y: logicalHeight * dpr - localY,
  };
}

export function liquidCapsuleGeometryForSnapshot(
  snapshot: PresenceRenderSnapshot,
  logicalWidth: number,
  logicalHeight: number,
  devicePixelRatio: number,
): LiquidCapsuleGeometry {
  const dpr = Math.min(2, Math.max(0.5, devicePixelRatio));
  const width = Math.min(360, Math.max(220, snapshot.input_capsule_width));
  const left = snapshot.interaction?.placement.expansion_direction === "left"
    ? logicalWidth - CAPSULE_EDGE_INSET - width
    : CAPSULE_EDGE_INSET;
  return {
    center_x: (left + width / 2) * dpr,
    center_y: (logicalHeight - CAPSULE_CENTER_Y) * dpr,
    half_width: Math.max(26, width / 2 - CAPSULE_HORIZONTAL_PADDING) * dpr,
  };
}
