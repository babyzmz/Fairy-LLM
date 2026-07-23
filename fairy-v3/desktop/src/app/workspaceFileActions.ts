import type { Conversation, Task, Workspace, WorkspaceFile } from "../core/client";
import type { WorkspaceClient, WorkspaceMode } from "./workspaceTypes";

interface WorkspaceFileActionContext {
  client: WorkspaceClient;
  mode: WorkspaceMode;
  task: Task | null;
  conversation: Conversation | null;
  workspace: Workspace | undefined;
  runAction<T>(action: () => Promise<T>): Promise<T>;
  selectTask(taskId: string): void;
  invalidateExecution(): Promise<void>;
  refreshFiles(): Promise<void>;
}

export function createWorkspaceFileActions(context: WorkspaceFileActionContext) {
  const scope = () => {
    if (context.task === null || context.conversation === null || context.workspace === undefined) {
      throw new Error("Workspace scope is unavailable");
    }
    return {
      task: context.task,
      conversation: context.conversation,
      workspace: context.workspace,
    };
  };
  const mutate = async (
    files: Parameters<WorkspaceClient["workspaces"]["mutateFiles"]>[0]["files"],
    reason: string,
  ) => {
    const current = scope();
    const result = await context.runAction(() =>
      context.client.workspaces.mutateFiles({
        workspace_id: current.task.workspace_id,
        conversation_id: current.conversation.id,
        expected_workspace_revision: current.workspace.revision,
        files,
        reason,
        idempotency_key: `desktop:workspace-mutation:${crypto.randomUUID()}`,
        user_confirmed: true,
      }),
    );
    context.selectTask(result.task.id);
    await context.invalidateExecution();
  };
  return {
    async readWorkspaceFile(path: string) {
      const current = scope();
      if (current.task.target_version_id === null) throw new Error("Version is unavailable");
      return context.client.workspaces.readFile({
        workspace_id: current.task.workspace_id,
        version_id: current.task.target_version_id,
        path,
      });
    },
    async openWorkspaceFileStream(path: string) {
      const current = scope();
      if (current.task.target_version_id === null) throw new Error("Version is unavailable");
      return context.client.workspaces.openStream({
        workspace_id: current.task.workspace_id,
        version_id: current.task.target_version_id,
        path,
        expires_seconds: 120,
      });
    },
    async presentWorkspaceFile(path: string) {
      const current = scope();
      if (current.task.target_version_id === null) throw new Error("Version is unavailable");
      return context.client.files.present({
        workspace_id: current.task.workspace_id,
        version_id: current.task.target_version_id,
        path,
        requested_mode: "auto",
      });
    },
    async compareWorkspaceFile(leftVersionId: string, rightVersionId: string, path: string) {
      const current = scope();
      return context.client.files.compare({
        workspace_id: current.task.workspace_id,
        left_version_id: leftVersionId,
        right_version_id: rightVersionId,
        path,
      });
    },
    async resolveWorkspaceFileSet(path: string) {
      const current = scope();
      if (current.task.target_version_id === null) throw new Error("Version is unavailable");
      return context.client.fileSets.resolve({
        workspace_id: current.task.workspace_id,
        version_id: current.task.target_version_id,
        path,
      });
    },
    async listFileAnnotations(presentation: Awaited<ReturnType<WorkspaceClient["files"]["present"]>>) {
      const value = presentation.presentation;
      if (value === null) return { document: null };
      return context.client.annotations.list({
        workspace_id: value.workspace_id,
        version_id: value.version_id,
        file_set_id: value.file_set_id,
      });
    },
    async updateFileAnnotations(
      presentation: Awaited<ReturnType<WorkspaceClient["files"]["present"]>>,
      current: Awaited<ReturnType<WorkspaceClient["annotations"]["update"]>> | null,
      annotations: Array<Record<string, unknown>>,
    ) {
      const value = presentation.presentation;
      if (value === null) throw new Error("Presentation is not ready");
      return context.client.annotations.update({
        workspace_id: value.workspace_id,
        version_id: value.version_id,
        file_set_id: value.file_set_id,
        source_hash: value.source_hash,
        expected_revision: current?.revision ?? 0,
        annotations,
      });
    },
    async createTextSelection(
      presentation: Awaited<ReturnType<WorkspaceClient["files"]["present"]>>,
      start: number,
      end: number,
    ) {
      const value = presentation.presentation;
      if (value === null) throw new Error("Presentation is not ready");
      return context.client.selections.create({
        workspace_id: value.workspace_id,
        version_id: value.version_id,
        file_set_id: value.file_set_id,
        source_path: value.source_path,
        source_hash: value.source_hash,
        viewer_kind: "text",
        locator_kind: "text_range",
        locator: { start, end },
      });
    },
    async createSceneSelection(
      presentation: Awaited<ReturnType<WorkspaceClient["files"]["present"]>>,
      nodePath: string,
    ) {
      const value = presentation.presentation;
      if (value === null) throw new Error("Presentation is not ready");
      return context.client.selections.create({
        workspace_id: value.workspace_id,
        version_id: value.version_id,
        file_set_id: value.file_set_id,
        source_path: value.source_path,
        source_hash: value.source_hash,
        viewer_kind: "threejs",
        locator_kind: "scene_node",
        locator: { node_path: nodePath },
      });
    },
    async revealWorkspaceFile(path: string) {
      const current = scope();
      await context.runAction(() =>
        context.client.systemActions.execute({
          task_id: current.task.id,
          action: { type: "reveal_path", relative_path: path },
          idempotency_key: `desktop:workspace-reveal:${current.task.id}:${path}`,
          user_confirmed: true,
        }),
      );
    },
    refreshWorkspaceFiles: context.refreshFiles,
    async uploadWorkspaceFiles(files: Array<{ path: string; contentBase64: string }>) {
      await mutate(
        files.map((file) => ({
          operation: "create" as const,
          path: file.path,
          content_base64: file.contentBase64,
        })),
        `Upload ${files.length} workspace file(s)`,
      );
    },
    async renameWorkspaceFile(file: WorkspaceFile, destinationPath: string) {
      await mutate(
        [{
          operation: "rename",
          path: file.path,
          destination_path: destinationPath,
          expected_hash: file.content_hash,
        }],
        `Rename ${file.path} to ${destinationPath}`,
      );
    },
    async deleteWorkspaceFile(file: WorkspaceFile) {
      await mutate(
        [{ operation: "delete", path: file.path, expected_hash: file.content_hash }],
        `Delete ${file.path}`,
      );
    },
    async exportWorkspace() {
      const current = scope();
      if (current.task.target_version_id === null) throw new Error("Version is unavailable");
      return context.client.workspaces.export({
        workspace_id: current.task.workspace_id,
        version_id: current.task.target_version_id,
        filename: "fairy-workspace.zip",
      });
    },
  };
}
