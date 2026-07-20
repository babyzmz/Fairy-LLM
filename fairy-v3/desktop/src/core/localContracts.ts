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
