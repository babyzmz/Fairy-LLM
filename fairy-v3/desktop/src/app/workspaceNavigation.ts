export const OPEN_WORKSPACE_SOURCE_EVENT = "fairy:open-workspace-source";

export interface OpenWorkspaceSourceDetail {
  path: string;
  workspaceId: string;
  versionId: string;
  lineStart: number | null;
  lineEnd: number | null;
}

export function openWorkspaceSource(detail: OpenWorkspaceSourceDetail): void {
  window.dispatchEvent(
    new CustomEvent<OpenWorkspaceSourceDetail>(OPEN_WORKSPACE_SOURCE_EVENT, {
      detail,
    }),
  );
}
