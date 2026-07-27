import { BookOpenText, Brain, Database, Eye, Network } from "lucide-react";
import { useState } from "react";

import type {
  KnowledgeSource,
  MemoryProposal,
  MemorySettings,
  ObsidianConnectorHealth,
  Project,
  Task,
} from "../core/client";
import { ActionDialog } from "../ui/ActionDialog";
import type { DesktopPreferences, SettingsClient } from "./client";
import { RealtimeMemoryReview } from "./RealtimeMemoryReview";
import {
  loadRealtimeMemoryReview,
  type RealtimeMemoryReviewData,
} from "./realtimeMemoryReviewData";
import { Category, HealthRow, SettingRange, SettingToggle } from "./settingsControls";

export interface KnowledgePrivacyData {
  knowledgeSources: Array<{ source: KnowledgeSource; projectName: string }>;
  memoryProposals: MemoryProposal[];
  obsidianHealth: ObsidianConnectorHealth;
  knowledgeDiagnostics: string[];
  realtimeMemoryReview: RealtimeMemoryReviewData;
}

type KnowledgePrivacyTab = "sources" | "memory" | "proposals" | "companion" | "diagnostics";
type MemoryProposalDecision = {
  proposal: MemoryProposal;
  action: "accept" | "reject";
};

interface KnowledgePrivacyPanelProps {
  data: KnowledgePrivacyData & {
    memorySettings: MemorySettings;
    preferences: DesktopPreferences;
  };
  busy: boolean;
  client: SettingsClient;
  act(operation: () => Promise<void>): Promise<void>;
  reload(): Promise<void>;
  updatePreferences(patch: Partial<DesktopPreferences>): Promise<void>;
  updateMemorySettings(
    patch: Partial<Pick<MemorySettings, "enabled" | "retention_days" | "export_to_obsidian" | "sync_normalized_content">>,
  ): Promise<void>;
}

export function KnowledgePrivacyPanel(props: KnowledgePrivacyPanelProps) {
  const {
    data,
    busy,
    client,
    act,
    reload,
    updatePreferences,
    updateMemorySettings,
  } = props;
  const [tab, setTab] = useState<KnowledgePrivacyTab>("sources");
  const [pending, setPending] = useState<MemoryProposalDecision | null>(null);
  const pendingProposals = data.memoryProposals.filter((proposal) => proposal.status === "pending");
  const readySources = data.knowledgeSources.filter(({ source }) => source.status === "ready").length;
  const connectorTone = data.obsidianHealth.status === "ready"
    ? "success"
    : data.obsidianHealth.status === "unavailable"
      ? "error"
      : "neutral";

  return <Category title="Knowledge & privacy" subtitle="Sources, memory and local data">
    <section className="knowledge-privacy" aria-labelledby="knowledge-privacy-heading">
      <header>
        <div>
          <Database size={17} />
          <span><strong id="knowledge-privacy-heading">Knowledge control</strong><small>Device-local sources and confirmed durable memory</small></span>
        </div>
        <span>{readySources}/{data.knowledgeSources.length} sources ready</span>
      </header>
      <div className="settings-tabs" role="tablist" aria-label="Knowledge and privacy views">
        <button type="button" role="tab" aria-selected={tab === "sources"} className={tab === "sources" ? "active" : ""} onClick={() => setTab("sources")}>Sources <span>{data.knowledgeSources.length}</span></button>
        <button type="button" role="tab" aria-selected={tab === "memory"} className={tab === "memory" ? "active" : ""} onClick={() => setTab("memory")}>Memory</button>
        <button type="button" role="tab" aria-selected={tab === "proposals"} className={tab === "proposals" ? "active" : ""} onClick={() => setTab("proposals")}>Proposals <span>{pendingProposals.length}</span></button>
        <button type="button" role="tab" aria-selected={tab === "companion"} className={tab === "companion" ? "active" : ""} onClick={() => setTab("companion")}>Companion <span>{data.realtimeMemoryReview.proposals.filter((item) => item.status === "pending").length}</span></button>
        <button type="button" role="tab" aria-selected={tab === "diagnostics"} className={tab === "diagnostics" ? "active" : ""} onClick={() => setTab("diagnostics")}>Diagnostics</button>
      </div>

      {tab === "sources" ? <div className="knowledge-source-list">
        <HealthRow
          icon={<BookOpenText size={17} />}
          label="Obsidian connector"
          status={data.obsidianHealth.public_summary}
          tone={connectorTone}
        />
        {data.knowledgeSources.length === 0 ? <p className="settings-empty">No project knowledge sources are connected.</p> : data.knowledgeSources.map(({ source, projectName }) => (
          <div className="knowledge-source-row" key={source.id}>
            <BookOpenText size={16} />
            <span>
              <strong>{source.display_name}</strong>
              <small>{projectName} · {source.display_path}</small>
            </span>
            <span className={`knowledge-source-state ${source.status}`}>{titleCase(source.status)}</span>
            <small>Revision {source.revision} · Sync {source.sync_cursor}</small>
          </div>
        ))}
        <p className="settings-callout">Vault paths stay on this device. Fairy reads only explicitly authorized directories and writes only to the managed Fairy directory.</p>
      </div> : null}

      {tab === "memory" ? <div className="knowledge-memory-settings">
        <SettingToggle label="Use durable memory" checked={data.memorySettings.enabled} disabled={busy} onChange={(value) => void updateMemorySettings({ enabled: value })} />
        <SettingRange label="Memory retention" value={data.memorySettings.retention_days} min={1} max={3650} suffix=" days" disabled={busy} onCommit={(value) => void updateMemorySettings({ retention_days: value })} />
        <SettingToggle label="Export confirmed memory to Obsidian" detail="Only confirmed Hermes claims can be projected into the Fairy-managed Vault directory" checked={data.memorySettings.export_to_obsidian} disabled={busy} onChange={(value) => void updateMemorySettings({ export_to_obsidian: value })} />
        <SettingToggle label="Allow normalized knowledge sync" detail="Off by default; device paths and raw Vault files are never uploaded" checked={data.memorySettings.sync_normalized_content} disabled={busy} onChange={(value) => void updateMemorySettings({ sync_normalized_content: value })} />
        <SettingToggle label="Anonymous diagnostics" checked={data.preferences.analytics_enabled} disabled={busy} onChange={(value) => void updatePreferences({ analytics_enabled: value })} />
        <HealthRow icon={<Eye size={17} />} label="Credential storage" status="Protected by Windows DPAPI; secrets are never returned" tone="success" />
      </div> : null}

      {tab === "proposals" ? <div className="knowledge-proposal-list">
        {pendingProposals.length === 0 ? <p className="settings-empty">No memory proposals need review.</p> : pendingProposals.map((proposal) => (
          <div className="knowledge-proposal-row" key={proposal.id}>
            <Brain size={16} />
            <span>
              <strong>{proposal.content}</strong>
              <small>{titleCase(proposal.proposed_namespace.replaceAll("_", " "))} · Suggested {formatHistoryDate(proposal.created_at)}</small>
            </span>
            <div>
              <button className="secondary-command" type="button" disabled={busy} onClick={() => setPending({ proposal, action: "reject" })}>Reject</button>
              <button className="primary-command" type="button" disabled={busy} onClick={() => setPending({ proposal, action: "accept" })}>Accept</button>
            </div>
          </div>
        ))}
      </div> : null}

      {tab === "companion" ? (
        <RealtimeMemoryReview
          data={data.realtimeMemoryReview}
          busy={busy}
          client={client}
          act={act}
          reload={reload}
        />
      ) : null}

      {tab === "diagnostics" ? <div className="knowledge-diagnostics">
        <HealthRow icon={<Network size={17} />} label="Obsidian Desktop" status={data.obsidianHealth.desktop_installed ? "Detected" : "Not detected"} tone={data.obsidianHealth.desktop_installed ? "success" : "neutral"} />
        <HealthRow icon={<BookOpenText size={17} />} label="Obsidian CLI" status={data.obsidianHealth.cli_available ? "Available for managed writes" : "Read-only indexing remains available"} tone={data.obsidianHealth.cli_available ? "success" : "neutral"} />
        <HealthRow icon={<Database size={17} />} label="Knowledge index" status={`${data.knowledgeSources.length} sources · ${readySources} ready`} tone={data.knowledgeSources.length === 0 || readySources === data.knowledgeSources.length ? "success" : "neutral"} />
        {data.knowledgeDiagnostics.length === 0 ? <p className="settings-empty">No knowledge synchronization errors.</p> : data.knowledgeDiagnostics.map((diagnostic) => <p className="settings-callout knowledge-diagnostic-error" key={diagnostic}>{diagnostic}</p>)}
      </div> : null}
    </section>

    <ActionDialog
      open={pending !== null}
      busy={busy}
      destructive={pending?.action === "reject"}
      title={pending?.action === "accept" ? "Accept memory proposal" : "Reject memory proposal"}
      description={pending === null ? "" : pending.action === "accept"
        ? "Add this bounded, sourced observation to Hermes durable memory. Existing claims are not silently overwritten."
        : "Reject this suggestion. It will remain auditable but will not become durable memory."}
      confirmLabel={pending?.action === "accept" ? "Accept memory" : "Reject proposal"}
      onCancel={() => setPending(null)}
      onConfirm={async () => {
        if (pending === null) return;
        const decision = pending;
        await act(async () => {
          const action = decision.action === "accept"
            ? client.memory.proposals.accept
            : client.memory.proposals.reject;
          await action({
            task_id: decision.proposal.task_id,
            observation_id: decision.proposal.id,
            user_confirmed: true,
            idempotency_key: `settings:memory-proposal:${decision.action}:${decision.proposal.id}:${decision.proposal.source_cursor}`,
          });
          await reload();
        });
        setPending(null);
      }}
    />
  </Category>;
}

export async function loadKnowledgePrivacy(client: SettingsClient): Promise<KnowledgePrivacyData> {
  const diagnostics: string[] = [];
  const [projectsResult, tasksResult, healthResult, realtimeMemoryResult] = await Promise.allSettled([
    listAllSettingsProjects(client),
    listAllSettingsTasks(client),
    client.obsidian.health(),
    loadRealtimeMemoryReview(client),
  ]);
  const projects = projectsResult.status === "fulfilled" ? projectsResult.value : [];
  const tasks = tasksResult.status === "fulfilled" ? tasksResult.value : [];
  if (projectsResult.status === "rejected") diagnostics.push("Project knowledge sources could not be loaded.");
  if (tasksResult.status === "rejected") diagnostics.push("Memory proposals could not be loaded.");
  if (healthResult.status === "rejected") diagnostics.push("Obsidian connector health could not be read.");

  const sourceResults = await settleInBatches(projects, (project) => client.knowledge.sources(project.id));
  const knowledgeSources: KnowledgePrivacyData["knowledgeSources"] = [];
  let sourceFailures = 0;
  sourceResults.forEach((result, index) => {
    if (result.status === "fulfilled") {
      knowledgeSources.push(...result.value.items.map((source) => ({
        source,
        projectName: projects[index].name,
      })));
    } else {
      sourceFailures += 1;
    }
  });
  if (sourceFailures > 0) {
    diagnostics.push(`${sourceFailures} project source ${sourceFailures === 1 ? "query" : "queries"} did not complete.`);
  }

  const proposalResults = await settleInBatches(tasks, (task) => client.memory.proposals.list(task.id));
  const proposalsById = new Map<string, MemoryProposal>();
  let proposalFailures = 0;
  proposalResults.forEach((result) => {
    if (result.status === "fulfilled") {
      result.value.items.forEach((proposal) => proposalsById.set(proposal.id, proposal));
    } else {
      proposalFailures += 1;
    }
  });
  if (proposalFailures > 0) {
    diagnostics.push(`Memory proposals were unavailable for ${proposalFailures} ${proposalFailures === 1 ? "task" : "tasks"}.`);
  }

  return {
    knowledgeSources: knowledgeSources.sort((left, right) =>
      left.projectName.localeCompare(right.projectName) || left.source.display_name.localeCompare(right.source.display_name)
    ),
    memoryProposals: [...proposalsById.values()].sort((left, right) => right.created_at.localeCompare(left.created_at)),
    obsidianHealth: healthResult.status === "fulfilled" ? healthResult.value : unavailableObsidianHealth(),
    knowledgeDiagnostics: diagnostics,
    realtimeMemoryReview: realtimeMemoryResult.status === "fulfilled"
      ? realtimeMemoryResult.value
      : {
          digests: [],
          proposals: [],
          diagnostic: "Realtime memory review could not be loaded.",
        },
  };
}

async function listAllSettingsProjects(client: SettingsClient): Promise<Project[]> {
  const items: Project[] = [];
  const seenCursors = new Set<string>();
  let cursor: string | null = null;
  do {
    const page = await client.context.projects(cursor);
    items.push(...page.items);
    cursor = page.next_cursor ?? null;
    if (cursor !== null) {
      if (seenCursors.has(cursor)) throw new Error("Project pagination did not advance");
      seenCursors.add(cursor);
    }
  } while (cursor !== null);
  return items;
}

async function listAllSettingsTasks(client: SettingsClient): Promise<Task[]> {
  const items: Task[] = [];
  const seenCursors = new Set<string>();
  let cursor: string | null = null;
  do {
    const page = await client.context.tasks(cursor);
    items.push(...page.items);
    cursor = page.next_cursor ?? null;
    if (cursor !== null) {
      if (seenCursors.has(cursor)) throw new Error("Task pagination did not advance");
      seenCursors.add(cursor);
    }
  } while (cursor !== null);
  return items;
}

async function settleInBatches<T, Result>(
  items: readonly T[],
  operation: (item: T) => Promise<Result>,
  batchSize = 8,
): Promise<PromiseSettledResult<Result>[]> {
  const results: PromiseSettledResult<Result>[] = [];
  for (let start = 0; start < items.length; start += batchSize) {
    results.push(...await Promise.allSettled(items.slice(start, start + batchSize).map(operation)));
  }
  return results;
}

function unavailableObsidianHealth(): ObsidianConnectorHealth {
  return {
    desktop_installed: false,
    cli_available: false,
    minimum_installer_version: "1.12.7",
    status: "unavailable",
    public_summary: "Obsidian connector is unavailable",
  };
}

function formatHistoryDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" }).format(new Date(value));
}

function titleCase(value: string) {
  return `${value.charAt(0).toUpperCase()}${value.slice(1)}`;
}
