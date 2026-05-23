import type { FairyAvatarMode, FairyAvatarSignal } from "../../components/fairy/FairyAvatar";

export type CompanionScene =
  | "idle"
  | "game_warming"
  | "game_active"
  | "combat"
  | "boss"
  | "victory_afterglow"
  | "defeat_regroup"
  | "afk"
  | "homecoming";

export const SCENE_TO_MODE: Record<CompanionScene, FairyAvatarMode> = {
  idle: "relaxed",
  game_warming: "focused",
  game_active: "focused",
  combat: "thinking",
  boss: "alert",
  victory_afterglow: "focused",
  defeat_regroup: "uncertain",
  afk: "sleeping",
  homecoming: "warming_up",
};

export const SCENE_TO_SIGNAL: Partial<Record<CompanionScene, FairyAvatarSignal>> = {
  game_warming: { state: "focused", certainty: 0.7, urgency: 0.35 },
  combat: { state: "thinking", certainty: 0.55, urgency: 0.7 },
  boss: { state: "alert", certainty: 0.78, urgency: 0.95 },
  victory_afterglow: { state: "focused", certainty: 0.95, urgency: 0.25 },
  defeat_regroup: { state: "uncertain", certainty: 0.4, urgency: 0.45 },
  afk: { state: "standby", certainty: 0.9, urgency: 0.04 },
  homecoming: { state: "relaxed", certainty: 0.6, urgency: 0.25 },
};

export function sceneToAvatarMode(scene: CompanionScene | undefined): FairyAvatarMode | undefined {
  if (!scene) return undefined;
  return SCENE_TO_MODE[scene];
}

export function sceneToSignal(scene: CompanionScene | undefined): FairyAvatarSignal | undefined {
  if (!scene) return undefined;
  return SCENE_TO_SIGNAL[scene];
}
