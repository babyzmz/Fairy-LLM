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
