import { ExternalLink, FileText } from "lucide-react";

import type { TurnTrace } from "../core/client";
import { openWorkspaceSource } from "../app/workspaceNavigation";

type EvidenceSource = TurnTrace["evidence_sources"][number];

export function EvidenceSources({
  taskId,
  sources,
  onOpenLink,
}: {
  taskId: string;
  sources: EvidenceSource[];
  onOpenLink(taskId: string, url: string): Promise<void>;
}) {
  if (sources.length === 0) return null;
  return (
    <details className="evidence-sources">
      <summary>
        Sources <span>{sources.length}</span>
      </summary>
      <ol>
        {sources.map((source) => (
          <li key={source.id}>
            {source.safe_url != null ? (
              <button
                type="button"
                onClick={() => void onOpenLink(taskId, source.safe_url as string)}
                title={source.safe_url}
              >
                <ExternalLink size={12} aria-hidden="true" />
                <span>{source.public_label}</span>
              </button>
            ) : source.relative_path != null && source.workspace_id != null && source.version_id != null ? (
              <button
                type="button"
                onClick={() => openWorkspaceSource({
                  path: source.relative_path as string,
                  workspaceId: source.workspace_id as string,
                  versionId: source.version_id as string,
                  lineStart: source.line_start ?? null,
                  lineEnd: source.line_end ?? null,
                })}
                title={source.relative_path}
              >
                <FileText size={12} aria-hidden="true" />
                <span>{source.public_label}</span>
                <code>{lineLabel(source)}</code>
              </button>
            ) : (
              <span className="evidence-source-static">
                <FileText size={12} aria-hidden="true" />
                <span>{source.public_label}</span>
              </span>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}

function lineLabel(source: EvidenceSource): string {
  if (source.relative_path == null) return "";
  if (source.line_start == null) return source.relative_path;
  const suffix = source.line_end == null || source.line_end === source.line_start
    ? `:${source.line_start}`
    : `:${source.line_start}-${source.line_end}`;
  return `${source.relative_path}${suffix}`;
}
