export type AmbientSurface = "pet" | "main" | "hidden";
export type DialogueSource = "protected" | "authored_original" | "generated_original";
export type DialogueTrigger =
  | "startup"
  | "idle_short"
  | "idle_long"
  | "user_returned"
  | "network_restored"
  | "battery_low"
  | "charging_started"
  | "self_commentary";

export interface AmbientContextSnapshot {
  observed_at: string;
  locale?: string;
  surface?: AmbientSurface;
  user_idle_seconds?: number;
  startup_eligible?: boolean;
  user_returned?: boolean;
  network_restored?: boolean;
  battery_percent?: number | null;
  charging?: boolean | null;
  charging_started?: boolean;
  locked?: boolean;
  do_not_disturb?: boolean;
  typing?: boolean;
  input_open?: boolean;
  microphone_active?: boolean;
  fullscreen?: boolean;
  realtime_active?: boolean;
  active_turn?: boolean;
  approval_waiting?: boolean;
  severe_error?: boolean;
  tts_active?: boolean;
}

export interface AmbientDialoguePreferences {
  enabled?: boolean;
  voice_enabled?: boolean;
  generated_enabled?: boolean;
}

export interface AmbientDialogueState {
  local_date?: string | null;
  startup_date?: string | null;
  daily_text_count?: number;
  daily_voice_count?: number;
  daily_generated_count?: number;
  last_global_at?: string | null;
  category_last_at?: Record<string, string>;
  line_last_at?: Record<string, string>;
  returned_last_at?: string | null;
  generated_digests?: string[];
}

export interface AmbientDialogueEvaluateInput {
  context: AmbientContextSnapshot;
  preferences?: AmbientDialoguePreferences;
  state?: AmbientDialogueState;
}

export interface GeneratedDialogueRequest {
  trigger: DialogueTrigger;
  locale: string;
  persona_digest: string;
  safe_facts: string[];
  recent_categories: string[];
  recent_digests: string[];
  max_output_tokens: number;
}

export interface GeneratedDialogueCandidate {
  text: string;
  intent: string;
  required_facts: string[];
  safe_for_tts: boolean;
  cooldown_group: string;
}

export interface AmbientDialogueProjection {
  presentation_id: string;
  dialogue_id: string;
  text: string;
  source: DialogueSource;
  trigger: DialogueTrigger;
  locale: string;
  tts_allowed: boolean;
  expires_at: string;
  persona_digest: string;
}

export interface AmbientDialogueDecision {
  projection: AmbientDialogueProjection | null;
  generation_request: GeneratedDialogueRequest | null;
  next_state: Required<AmbientDialogueState>;
  reason: string;
}

export type ObsidianSourceMode = "read_only" | "bidirectional";
export type ObsidianReadScope = "selected_directories" | "whole_vault";

export interface ObsidianConnectorHealth {
  desktop_installed: boolean;
  cli_available: boolean;
  minimum_installer_version: string;
  status: string;
  public_summary: string;
}

export interface ObsidianSourceCreateInput {
  project_id: string;
  display_name: string;
  local_path_token: string;
  read_scope: ObsidianReadScope;
  allowed_directories: string[];
  whole_vault_confirmed: boolean;
  managed_directory: string;
  mode: ObsidianSourceMode;
  idempotency_key: string;
}

export interface ObsidianSource {
  id: string;
  project_id: string;
  display_name: string;
  vault_display_path: string;
  read_scope: ObsidianReadScope;
  allowed_directories: string[];
  managed_directory: string;
  mode: ObsidianSourceMode;
  status: string;
  revision: number;
  item_count: number;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ObsidianSourcePage {
  items: ObsidianSource[];
}

export interface ObsidianSourceSyncInput {
  source_id: string;
  expected_revision: number;
}

export interface ObsidianVaultItem {
  source_id: string;
  relative_path: string;
  title: string;
  kind: string;
  content_hash: string;
  byte_length: number;
  links: string[];
  modified_at: string;
}

export interface ObsidianVaultItemPage {
  items: ObsidianVaultItem[];
  source_revision: number;
}

export interface ObsidianVaultItemReadInput {
  source_id: string;
  relative_path: string;
  expected_source_revision: number;
  expected_content_hash: string;
}

export interface ObsidianVaultItemContent {
  source_id: string;
  relative_path: string;
  title: string;
  kind: string;
  content_hash: string;
  content: string;
}

export interface ObsidianSyncResult {
  source: ObsidianSource;
  scanned_count: number;
  changed_count: number;
  deleted_count: number;
  failed_count: number;
}

export interface KnowledgeSyncStartInput {
  source_id: string;
  expected_revision: number;
  idempotency_key: string;
}

export interface KnowledgeSyncRunInput {
  run_id: string;
}

export interface KnowledgeSyncRun {
  id: string;
  source_id: string;
  project_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
  expected_source_revision: number;
  source_cursor: number;
  scanned_count: number;
  changed_count: number;
  deleted_count: number;
  failed_count: number;
  error_code: string | null;
  attempts: number;
  cancellation_revision: number;
  started_at: string;
  completed_at: string | null;
}

export type RealtimeCaptionSpeaker = "user" | "assistant";

export interface RealtimeTranscriptAppendInput {
  session_id: string;
  speaker: RealtimeCaptionSpeaker;
  text: string;
}

export interface RealtimeTranscriptListInput {
  conversation_id: string;
  limit?: number;
}

export interface RealtimeTranscriptEntry {
  id: string;
  session_id: string;
  conversation_id: string;
  sequence: number;
  speaker: RealtimeCaptionSpeaker;
  text: string;
  created_at: string;
}

export interface RealtimeTranscriptPage {
  items: RealtimeTranscriptEntry[];
}

export type CompanionDigestActivity = "auto" | "game" | "focus";

export interface CompanionDigestCreateInput {
  session_id: string;
  request_id: string;
  activity?: CompanionDigestActivity;
  subject_title?: string | null;
}

export interface CompanionDigestGetInput {
  digest_id: string;
}

export interface CompanionDigestListInput {
  session_id?: string | null;
  limit?: number;
}

export interface CompanionSessionDigest {
  id: string;
  session_id: string;
  conversation_id: string;
  request_id: string;
  activity: CompanionDigestActivity;
  subject_title: string | null;
  started_at: string;
  ended_at: string;
  duration_seconds: number;
  activities: string[];
  progress_summary: string;
  unresolved_issue: string | null;
  next_goal: string | null;
  notable_outcome: string | null;
  source_first_sequence: number;
  source_last_sequence: number;
  source_digest: string;
  policy_version: string;
  proposal_ids: string[];
  created_at: string;
  revision: number;
}

export interface CompanionSessionDigestPage {
  items: CompanionSessionDigest[];
}

export type BrowserProfileKind = "persistent" | "ephemeral";
export type BrowserSessionStatus =
  | "starting"
  | "active"
  | "suspended"
  | "stopped"
  | "interrupted"
  | "failed";
export type BrowserActionKind =
  | "navigate"
  | "click"
  | "fill"
  | "press"
  | "select"
  | "scroll"
  | "wait"
  | "reload"
  | "go_back"
  | "go_forward"
  | "viewport";

export interface BrowserWorkerHealth {
  available: boolean;
  browser_name: string;
  browser_version: string | null;
  error_code: string | null;
  diagnostic: string | null;
}

export interface BrowserProfile {
  id: string;
  kind: BrowserProfileKind;
  configured: boolean;
  local_only: boolean;
  retention_days: number;
}

export interface BrowserTab {
  id: string;
  session_id: string;
  title: string;
  url: string;
  active: boolean;
  loading: boolean;
  revision: number;
}

export interface BrowserSession {
  id: string;
  project_id: string | null;
  conversation_id: string | null;
  task_id: string | null;
  execution_target: "local" | "cloud";
  profile_kind: BrowserProfileKind;
  status: BrowserSessionStatus;
  active_tab_id: string | null;
  tabs: BrowserTab[];
  revision: number;
  created_at: string;
  updated_at: string;
  error_code: string | null;
  public_error: string | null;
}

export interface BrowserSessionStartInput {
  project_id?: string | null;
  conversation_id?: string | null;
  task_id?: string | null;
  execution_target?: "local" | "cloud";
  profile_kind?: BrowserProfileKind;
  initial_url?: string | null;
  idempotency_key: string;
}

export interface BrowserSessionPage {
  items: BrowserSession[];
}

export interface BrowserActionInput {
  session_id: string;
  tab_id: string;
  kind: BrowserActionKind;
  selector?: string | null;
  value?: string | null;
  x?: number | null;
  y?: number | null;
  delta_x?: number | null;
  delta_y?: number | null;
  width?: number | null;
  height?: number | null;
  expected_page_revision?: number | null;
  idempotency_key: string;
}

export interface BrowserActionResult {
  session: BrowserSession;
  tab: BrowserTab;
  public_summary: string;
  replayed: boolean;
}

export interface BrowserSnapshot {
  session_id: string;
  tab_id: string;
  page_revision: number;
  url: string;
  title: string;
  aria_snapshot: string;
  viewport_width: number;
  viewport_height: number;
  screenshot_data_url: string | null;
  captured_at: string;
}
