import type { PresenceRenderSnapshot } from "./presenceRenderer";

export interface LiquidAnchor {
  x: number;
  y: number;
}

export function liquidAnchorForSnapshot(
  snapshot: PresenceRenderSnapshot,
  _logicalWidth: number,
  logicalHeight: number,
  devicePixelRatio: number,
): LiquidAnchor {
  const dpr = Math.min(2, Math.max(0.5, devicePixelRatio));
  const interaction = snapshot.interaction;
  const sizeShiftLogical = Math.max(0, 72 * snapshot.size_scale + 4 - 96);
  if (interaction === null) {
    return {
      x: (96 + sizeShiftLogical) * dpr,
      y: logicalHeight * dpr - 130 * dpr,
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
