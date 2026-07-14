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
  invalidateWorkspace(): Promise<void>;
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
    await context.invalidateWorkspace();
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
