import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useRef } from "react";

import type {
  AmbientContextSnapshot,
  AmbientDialogueProjection,
  AmbientDialogueState,
  CoreClient,
} from "../core/client";
import type { DesktopPreferences } from "../settings/client";

const INITIAL_DELAY_MS = 2_500;
const EVALUATION_INTERVAL_MS = 15_000;
const defaultMainActive = () => document.hasFocus();
const defaultNow = () => new Date();

interface AmbientDeviceFacts {
  user_idle_seconds: number;
  battery_percent: number | null;
  charging: boolean | null;
  foreground_fullscreen: boolean;
  locked: boolean;
  microphone_active: boolean;
  do_not_disturb: boolean;
}

interface AmbientDialogueLocalState extends Required<AmbientDialogueState> {
  schema_version: number;
  revision: number;
}

interface AmbientDialogueStateUpdate {
  expected_revision: number;
  state: AmbientDialogueLocalState;
}

interface AmbientDialogueHostProps {
  client?: Pick<CoreClient["ambient"], "evaluate">;
  preferences: DesktopPreferences | null;
  activeTurn: boolean;
  approvalWaiting: boolean;
  inputOpen: boolean;
  microphoneActive: boolean;
  realtimeActive: boolean;
  severeError: boolean;
  ttsActive: boolean;
  onProjection(projection: AmbientDialogueProjection | null): void;
  invokeCommand?: typeof invoke;
  isMainActive?: () => boolean;
  now?: () => Date;
}

export function AmbientDialogueHost({
  client,
  preferences,
  activeTurn,
  approvalWaiting,
  inputOpen,
  microphoneActive,
  realtimeActive,
  severeError,
  ttsActive,
  onProjection,
  invokeCommand = invoke,
  isMainActive = defaultMainActive,
  now = defaultNow,
}: AmbientDialogueHostProps) {
  const state = useRef<AmbientDialogueLocalState | null>(null);
  const evaluating = useRef(false);
  const previousIdleSeconds = useRef(0);
  const previousOnline = useRef(navigator.onLine);
  const previousCharging = useRef<boolean | null>(null);
  const expiryTimer = useRef<number | null>(null);

  const clearProjection = useCallback(() => {
    if (expiryTimer.current !== null) {
      window.clearTimeout(expiryTimer.current);
      expiryTimer.current = null;
    }
    onProjection(null);
  }, [onProjection]);

  const blocked =
    preferences === null ||
    !preferences.pet_enabled ||
    !preferences.ambient_dialogue_enabled ||
    preferences.pet_do_not_disturb ||
    activeTurn ||
    approvalWaiting ||
    inputOpen ||
    microphoneActive ||
    realtimeActive ||
    severeError ||
    ttsActive;

  useEffect(() => {
    if (blocked) clearProjection();
  }, [blocked, clearProjection]);

  useEffect(() => {
    if (client === undefined || preferences === null) return;
    let disposed = false;
    let initialTimer: number | null = null;
    let interval: number | null = null;

    const evaluate = async () => {
      const mainActive = isMainActive();
      if (disposed || evaluating.current || blocked || mainActive) {
        if (mainActive) clearProjection();
        return;
      }
      evaluating.current = true;
      try {
        const localState =
          state.current ??
          (await invokeCommand<AmbientDialogueLocalState>("ambient_dialogue_state_get"));
        if (disposed) return;
        state.current = localState;
        const facts = await invokeCommand<AmbientDeviceFacts>("ambient_device_facts_get");
        if (disposed) return;
        const observedAt = now();
        const context = buildAmbientContext({
          facts,
          observedAt,
          locale: resolveAmbientLocale(preferences.language),
          startupEligible: true,
          userReturned:
            previousIdleSeconds.current >= 15 * 60 && facts.user_idle_seconds < 60,
          networkRestored: !previousOnline.current && navigator.onLine,
          chargingStarted: previousCharging.current === false && facts.charging === true,
          inputOpen,
          microphoneActive,
          realtimeActive,
          activeTurn,
          approvalWaiting,
          severeError,
          ttsActive,
          doNotDisturb: preferences.pet_do_not_disturb,
        });
        previousIdleSeconds.current = facts.user_idle_seconds;
        previousOnline.current = navigator.onLine;
        previousCharging.current = facts.charging;
        const decision = await client.evaluate({
          context,
          preferences: {
            enabled: preferences.ambient_dialogue_enabled,
            voice_enabled:
              preferences.ambient_dialogue_voice_enabled && !preferences.pet_muted,
            generated_enabled: preferences.ambient_generated_dialogue_enabled,
          },
          state: toCoreState(localState),
        });
        if (disposed || blocked || isMainActive()) return;
        if (ambientStateChanged(toCoreState(localState), decision.next_state)) {
          const update: AmbientDialogueStateUpdate = {
            expected_revision: localState.revision,
            state: {
              schema_version: localState.schema_version,
              revision: localState.revision,
              ...decision.next_state,
            },
          };
          const saved = await invokeCommand<AmbientDialogueLocalState>(
            "ambient_dialogue_state_update",
            { input: update },
          );
          if (disposed) return;
          state.current = saved;
        }
        if (decision.projection === null) return;
        const expiresIn = Date.parse(decision.projection.expires_at) - now().getTime();
        if (expiresIn <= 0) return;
        clearProjection();
        onProjection(decision.projection);
        expiryTimer.current = window.setTimeout(clearProjection, expiresIn);
      } catch (error) {
        if (String(error).includes("AMBIENT_DIALOGUE_REVISION_CONFLICT")) {
          state.current = null;
        }
      } finally {
        evaluating.current = false;
      }
    };

    initialTimer = window.setTimeout(() => void evaluate(), INITIAL_DELAY_MS);
    interval = window.setInterval(() => void evaluate(), EVALUATION_INTERVAL_MS);
    const onFocus = () => clearProjection();
    window.addEventListener("focus", onFocus);
    return () => {
      disposed = true;
      if (initialTimer !== null) window.clearTimeout(initialTimer);
      if (interval !== null) window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      clearProjection();
    };
  }, [
    activeTurn,
    approvalWaiting,
    blocked,
    clearProjection,
    client,
    inputOpen,
    invokeCommand,
    isMainActive,
    microphoneActive,
    now,
    onProjection,
    preferences,
    realtimeActive,
    severeError,
    ttsActive,
  ]);

  return null;
}

export function buildAmbientContext(input: {
  facts: AmbientDeviceFacts;
  observedAt: Date;
  locale: string;
  startupEligible: boolean;
  userReturned: boolean;
  networkRestored: boolean;
  chargingStarted: boolean;
  inputOpen: boolean;
  microphoneActive: boolean;
  realtimeActive: boolean;
  activeTurn: boolean;
  approvalWaiting: boolean;
  severeError: boolean;
  ttsActive: boolean;
  doNotDisturb: boolean;
}): AmbientContextSnapshot {
  return {
    observed_at: input.observedAt.toISOString(),
    locale: input.locale,
    surface: "pet",
    user_idle_seconds: input.facts.user_idle_seconds,
    startup_eligible: input.startupEligible,
    user_returned: input.userReturned,
    network_restored: input.networkRestored,
    battery_percent: input.facts.battery_percent,
    charging: input.facts.charging,
    charging_started: input.chargingStarted,
    locked: input.facts.locked,
    do_not_disturb: input.doNotDisturb || input.facts.do_not_disturb,
    typing: input.facts.user_idle_seconds < 3,
    input_open: input.inputOpen,
    microphone_active: input.microphoneActive || input.facts.microphone_active,
    fullscreen: input.facts.foreground_fullscreen,
    realtime_active: input.realtimeActive,
    active_turn: input.activeTurn,
    approval_waiting: input.approvalWaiting,
    severe_error: input.severeError,
    tts_active: input.ttsActive,
  };
}

export function resolveAmbientLocale(language: DesktopPreferences["language"]): "zh-CN" | "en" {
  if (language === "zh-CN" || language === "en") return language;
  return navigator.language.toLowerCase().startsWith("zh") ? "zh-CN" : "en";
}

function toCoreState(state: AmbientDialogueLocalState): AmbientDialogueState {
  const { schema_version: _schemaVersion, revision: _revision, ...coreState } = state;
  return coreState;
}

export function ambientStateChanged(
  current: AmbientDialogueState,
  next: AmbientDialogueState,
): boolean {
  return stateFingerprint(current) !== stateFingerprint(next);
}

function stateFingerprint(state: AmbientDialogueState): string {
  return JSON.stringify({
    local_date: state.local_date ?? null,
    startup_date: state.startup_date ?? null,
    daily_text_count: state.daily_text_count ?? 0,
    daily_voice_count: state.daily_voice_count ?? 0,
    daily_generated_count: state.daily_generated_count ?? 0,
    last_global_at: state.last_global_at ?? null,
    category_last_at: Object.entries(state.category_last_at ?? {}).sort(([left], [right]) =>
      left.localeCompare(right),
    ),
    line_last_at: Object.entries(state.line_last_at ?? {}).sort(([left], [right]) =>
      left.localeCompare(right),
    ),
    returned_last_at: state.returned_last_at ?? null,
    generated_digests: state.generated_digests ?? [],
  });
}
