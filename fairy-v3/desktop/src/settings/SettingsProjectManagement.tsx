import {
  FolderKanban,
  HardDrive,
  MessageSquareText,
  RotateCcw,
  Search,
  Trash2,
} from "lucide-react";
import { useState } from "react";

import type { ProjectArchivedItem, TrashItem } from "../core/client";
import { ActionDialog } from "../ui/ActionDialog";
import { markTrashMaintenanceSucceeded } from "./trashMaintenance";
import { SettingToggle } from "./settingsControls";
import { formatBytes, type SettingsCategoryProps } from "./settingsShared";

type ProjectManagementAction =
  | { kind: "archive-delete"; item: ProjectArchivedItem }
  | { kind: "trash-purge"; item: TrashItem }
  | { kind: "trash-purge-all" };

export function ProjectManagementPanel(props: SettingsCategoryProps) {
  const { data, busy, client, act, reload, updatePreferences } = props;
  const [tab, setTab] = useState<"archived" | "trash">("archived");
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState<ProjectManagementAction | null>(null);
  const normalized = query.trim().toLocaleLowerCase();
  const archived = data.archivedProjects.filter((item) =>
    item.project.name.toLocaleLowerCase().includes(normalized),
  );
  const trash = data.trashItems.filter((item) =>
    `${item.title} ${item.source_project_title ?? ""}`.toLocaleLowerCase().includes(normalized),
  );
  const estimatedBytes = data.trashItems.reduce((total, item) => total + item.estimated_bytes, 0);
  const runAutoPurge = () => act(async () => {
    await client.projectManagement.trash.purgeAll({
      user_confirmed: true,
      deleted_before: new Date(Date.now() - 30 * 24 * 60 * 60 * 1_000).toISOString(),
      maintenance: true,
    });
    markTrashMaintenanceSucceeded();
    await reload();
  });

  return (
    <section className="project-management" aria-labelledby="project-management-heading">
      <header>
        <div>
          <HardDrive size={17} />
          <span><strong id="project-management-heading">Project management</strong><small>Archived work and Recently deleted</small></span>
        </div>
        <span>{data.archivedProjects.length + data.trashItems.length} items</span>
      </header>
      <div className="settings-tabs" role="tablist" aria-label="Project management views">
        <button type="button" role="tab" aria-selected={tab === "archived"} className={tab === "archived" ? "active" : ""} onClick={() => setTab("archived")}>Archived <span>{data.archivedProjects.length}</span></button>
        <button type="button" role="tab" aria-selected={tab === "trash"} className={tab === "trash" ? "active" : ""} onClick={() => setTab("trash")}>Recently deleted <span>{data.trashItems.length}</span></button>
      </div>
      <label className="project-management-search">
        <Search size={14} />
        <span className="sr-only">Search project management</span>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tab === "archived" ? "Search archived projects" : "Search recently deleted"} />
      </label>

      {tab === "archived" ? (
        <div className="project-management-list">
          {archived.length === 0 ? <p className="settings-empty">No archived projects</p> : archived.map((item) => (
            <div className="project-management-row" key={item.project.id}>
              <FolderKanban size={16} />
              <span><strong>{item.project.name}</strong><small>{item.thread_count} chat{item.thread_count === 1 ? "" : "s"} · Archived {formatHistoryDate(item.archived_at)}</small></span>
              <button className="secondary-command" type="button" disabled={busy} onClick={() => void act(async () => {
                await client.projectManagement.archived.restore({
                  project_id: item.project.id,
                  expected_revision: item.project.metadata_revision,
                });
                await reload();
              })}><RotateCcw size={14} />Restore</button>
              <button className="danger-icon" type="button" aria-label={`Delete archived project ${item.project.name}`} title="Move to Recently deleted" disabled={busy} onClick={() => setPending({ kind: "archive-delete", item })}><Trash2 size={14} /></button>
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="project-management-summary">
            <span>{data.trashItems.length} items · {formatBytes(estimatedBytes)} estimated</span>
            <button className="secondary-command" type="button" disabled={busy || data.trashItems.length === 0} onClick={() => setPending({ kind: "trash-purge-all" })}>Clear all</button>
          </div>
          <div className="project-management-list">
            {trash.length === 0 ? <p className="settings-empty">Recently deleted is empty</p> : trash.map((item) => (
              <div className="project-management-row" key={`${item.item_type}:${item.item_id}`}>
                {item.item_type === "project" ? <FolderKanban size={16} /> : <MessageSquareText size={16} />}
                <span><strong>{item.title}</strong><small>{trashTypeLabel(item)}{item.source_project_title ? ` · ${item.source_project_title}` : ""} · Deleted {formatHistoryDate(item.deleted_at)}</small></span>
                <button className="secondary-command" type="button" disabled={busy || !item.can_restore} title={item.can_restore ? "Restore" : "Restore the parent project first"} onClick={() => void act(async () => {
                  await client.projectManagement.trash.restore({
                    item_type: item.item_type,
                    item_id: item.item_id,
                    expected_revision: item.metadata_revision,
                    user_confirmed: true,
                  });
                  await reload();
                })}><RotateCcw size={14} />Restore</button>
                <button className="danger-icon" type="button" aria-label={`Permanently delete ${item.title}`} title="Permanently delete" disabled={busy} onClick={() => setPending({ kind: "trash-purge", item })}><Trash2 size={14} /></button>
              </div>
            ))}
          </div>
          <SettingToggle
            label="Permanently delete after 30 days"
            detail="Runs at most once per day on this device; disabled by default"
            checked={data.preferences.trash_auto_purge_30_days}
            disabled={busy}
            onChange={(value) => void updatePreferences({ trash_auto_purge_30_days: value })}
          />
          {data.autoPurgeError ? <div className="settings-callout project-management-retry"><span>Automatic cleanup did not finish; remaining items were kept.</span><button className="secondary-command" type="button" disabled={busy} onClick={() => void runAutoPurge()}>Retry cleanup</button></div> : null}
        </>
      )}

      <ActionDialog
        open={pending !== null}
        busy={busy}
        destructive
        title={pending === null ? "Delete" : managementActionTitle(pending)}
        description={pending === null ? "" : managementActionDescription(pending, estimatedBytes)}
        confirmLabel={pending?.kind === "archive-delete" ? "Move to Recently deleted" : "Permanently delete"}
        onCancel={() => setPending(null)}
        onConfirm={async () => {
          if (pending === null) return;
          await act(async () => {
            if (pending.kind === "archive-delete") {
              await client.projectManagement.archived.delete({
                project_id: pending.item.project.id,
                expected_revision: pending.item.project.metadata_revision,
                cancel_active: false,
                user_confirmed: true,
              });
            } else if (pending.kind === "trash-purge") {
              await client.projectManagement.trash.purge({
                item_type: pending.item.item_type,
                item_id: pending.item.item_id,
                expected_revision: pending.item.metadata_revision,
                user_confirmed: true,
              });
            } else {
              await client.projectManagement.trash.purgeAll({
                user_confirmed: true,
                deleted_before: null,
                maintenance: false,
              });
            }
            await reload();
          });
          setPending(null);
        }}
      />
    </section>
  );
}

function managementActionTitle(action: ProjectManagementAction) {
  if (action.kind === "archive-delete") return "Move archived project to Recently deleted";
  if (action.kind === "trash-purge-all") return "Permanently delete all items";
  return "Permanently delete item";
}

function managementActionDescription(action: ProjectManagementAction, estimatedBytes: number) {
  if (action.kind === "archive-delete") {
    return `Move “${action.item.project.name}” and its ${action.item.thread_count} chat${action.item.thread_count === 1 ? "" : "s"} to Recently deleted. It can still be restored.`;
  }
  if (action.kind === "trash-purge-all") {
    return `Permanently delete all user content in Recently deleted and release approximately ${formatBytes(estimatedBytes)}. Minimal synchronization tombstones and audit identifiers remain.`;
  }
  return `Permanently delete “${action.item.title}”. Transcript, attachments, derived indexes and exclusive Workspace content cannot be restored.`;
}

function trashTypeLabel(item: TrashItem) {
  if (item.item_type === "project") return `${item.thread_count} project chat${item.thread_count === 1 ? "" : "s"}`;
  return item.item_type === "project_conversation" ? "Project chat" : "Chat";
}

function formatHistoryDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" }).format(new Date(value));
}
