import type { Conversation, Project } from "../core/client";
import type { WorkspaceClient, WorkspaceMode } from "./workspaceTypes";

export const projectOverviewSelection = "__project_overview__";

export type WorkspaceRunAction = <T>(operation: () => Promise<T>) => Promise<T>;

interface WorkspaceHistoryActionOptions {
  client: WorkspaceClient;
  runAction: WorkspaceRunAction;
  invalidateHistory(): Promise<void>;
  projects: Project[];
  selectedProjectId: string | null;
  projectConversations: Conversation[];
  chatConversations: Conversation[];
  conversationSelection: string | null;
  chatConversationSelection: string | null;
  setProjectSelection: (value: string | null) => void;
  setConversationSelection: (value: string | null) => void;
  setChatConversationSelection: (value: string | null) => void;
  setTaskSelection: (value: string | null) => void;
  setChatTaskId: (value: string | null) => void;
  setPetTaskId: (value: string | null) => void;
  setPetConversationId: (value: string | null) => void;
  setMode: (value: WorkspaceMode) => void;
  resetChatAssistant: () => void;
  onChatSelected?: (conversationId: string) => void;
}

export function createWorkspaceHistoryActions(options: WorkspaceHistoryActionOptions) {
  const {
    client,
    runAction,
    invalidateHistory,
    projects,
    selectedProjectId,
    projectConversations,
    chatConversations,
    conversationSelection,
    chatConversationSelection,
    setProjectSelection,
    setConversationSelection,
    setChatConversationSelection,
    setTaskSelection,
    setChatTaskId,
    setPetTaskId,
    setPetConversationId,
    setMode,
    resetChatAssistant,
  } = options;
  const mutateHistory = async <T>(operation: () => Promise<T>): Promise<T> => {
    const result = await runAction(operation);
    await invalidateHistory();
    return result;
  };

  const selectChatConversation = (conversationId: string) => {
    options.onChatSelected?.(conversationId);
    if (!chatConversations.some((conversation) => conversation.id === conversationId)) {
      void invalidateHistory();
    }
    setMode("chat");
    const effectiveSelection =
      chatConversations.find((conversation) => conversation.id === chatConversationSelection)?.id ??
      chatConversations.at(0)?.id ??
      null;
    if (effectiveSelection === conversationId) {
      if (chatConversationSelection !== conversationId) {
        setChatConversationSelection(conversationId);
      }
      return;
    }
    setChatConversationSelection(conversationId);
    setChatTaskId(null);
    setPetTaskId(null);
    setPetConversationId(null);
    resetChatAssistant();
  };

  const createScratchConversation = async (forPet: boolean) => {
    const conversation = await mutateHistory(() =>
      client.conversations.create({ project_id: null, workspace_type: "chat_scratch" }),
    );
    setPetConversationId(forPet ? conversation.id : null);
    options.onChatSelected?.(conversation.id);
    setChatConversationSelection(conversation.id);
    setChatTaskId(null);
    setPetTaskId(null);
    setMode("chat");
    resetChatAssistant();
  };

  const createProjectFrom = async (
    operation: () => ReturnType<WorkspaceClient["projects"]["create"]>,
  ) => {
    const { result, initialConversation } = await mutateHistory(async () => {
      const result = await operation();
      const page = await collectCursorPages((cursor) =>
        client.conversations.list({ limit: 100, cursor }),
      );
      return {
        result,
        initialConversation:
          page.items.find((conversation) => conversation.project_id === result.project.id) ?? null,
      };
    });
    setProjectSelection(result.project.id);
    setConversationSelection(initialConversation?.id ?? projectOverviewSelection);
    setTaskSelection(null);
    setMode("project");
  };

  return {
    selectProject(projectId: string) {
      setProjectSelection(projectId);
      setConversationSelection(projectOverviewSelection);
      setTaskSelection(null);
    },
    selectConversation(conversationId: string) {
      setConversationSelection(conversationId);
      setTaskSelection(null);
    },
    selectChatConversation,
    createProject: (name: string) =>
      createProjectFrom(() => client.projects.create({ name, residency: "local_only" })),
    importProject: (name: string, sourcePath: string) =>
      createProjectFrom(() =>
        client.projects.import({ name, residency: "local_only", source_path: sourcePath }),
      ),
    createChatConversation: () => createScratchConversation(false),
    createPetChatConversation: () => createScratchConversation(true),
    async createProjectConversation(project: Project) {
      const conversation = await mutateHistory(() =>
        client.conversations.create({ project_id: project.id, workspace_type: "project_chat" }),
      );
      setProjectSelection(project.id);
      setConversationSelection(conversation.id);
      setTaskSelection(null);
      setMode("project");
    },
    async renameProject(project: Project, name: string) {
      const trimmed = name.trim();
      if (trimmed.length === 0 || trimmed === project.name) return;
      await mutateHistory(async () => {
        const latest = await client.projects.get(project.id);
        return client.projects.updateMetadata({
          project_id: project.id,
          name: trimmed,
          expected_revision: latest.metadata_revision,
        });
      });
    },
    async setProjectPinned(project: Project, pinned: boolean) {
      await mutateHistory(async () => {
        const latest = await client.projects.get(project.id);
        return client.projects.updateMetadata({
          project_id: project.id,
          pinned,
          expected_revision: latest.metadata_revision,
        });
      });
    },
    async archiveProject(project: Project) {
      await mutateHistory(async () => {
        const latest = await client.projects.get(project.id);
        return client.projects.archive({
          project_id: project.id,
          expected_revision: latest.metadata_revision,
        });
      });
      selectProjectFallback(project.id);
    },
    async deleteProject(project: Project, cancelActive = true) {
      await mutateHistory(async () => {
        const latest = await client.projects.get(project.id);
        return client.projects.delete({
          project_id: project.id,
          expected_revision: latest.metadata_revision,
          cancel_active: cancelActive,
          user_confirmed: true,
        });
      });
      selectProjectFallback(project.id);
    },
    async renameConversation(conversation: Conversation, title: string) {
      const trimmed = title.trim();
      if (trimmed.length === 0 || trimmed === conversation.title) return;
      await mutateHistory(async () => {
        const latest = await client.conversations.get(conversation.id);
        return client.conversations.update({
          conversation_id: conversation.id,
          title: trimmed,
          expected_revision: latest.revision,
        });
      });
    },
    async setConversationPinned(conversation: Conversation, pinned: boolean) {
      await mutateHistory(async () => {
        const latest = await client.conversations.get(conversation.id);
        return client.conversations.update({
          conversation_id: conversation.id,
          pinned,
          expected_revision: latest.revision,
        });
      });
    },
    async deleteConversation(conversation: Conversation) {
      await mutateHistory(async () => {
        const latest = await client.conversations.get(conversation.id);
        return client.conversations.delete({
          conversation_id: conversation.id,
          expected_revision: latest.revision,
          user_confirmed: true,
        });
      });
      if (chatConversationSelection === conversation.id) {
        const fallback = adjacentItem(chatConversations, conversation.id);
        setChatConversationSelection(fallback?.id ?? null);
        if (fallback) options.onChatSelected?.(fallback.id);
        setChatTaskId(null);
        resetChatAssistant();
      } else if (conversationSelection === conversation.id) {
        const siblings = projectConversations.filter(
          (item) => item.project_id === conversation.project_id,
        );
        const fallback = adjacentItem(siblings, conversation.id);
        setConversationSelection(fallback?.id ?? projectOverviewSelection);
        setTaskSelection(null);
      }
    },
    async moveConversationToProject(conversation: Conversation, project: Project) {
      const result = await mutateHistory(async () => {
        const latest = await client.conversations.get(conversation.id);
        return client.conversations.moveToProject({
          conversation_id: conversation.id,
          target_project_id: project.id,
          expected_revision: latest.revision,
          user_confirmed: true,
          idempotency_key: `desktop:conversation-move:${conversation.id}:${project.id}:${latest.revision}`,
        });
      });
      setProjectSelection(project.id);
      setConversationSelection(result.destination_conversation.id);
      setTaskSelection(null);
      setMode("project");
    },
  };

  function selectProjectFallback(removedId: string) {
    if (selectedProjectId !== removedId) return;
    const fallback = adjacentItem(projects, removedId);
    setProjectSelection(fallback?.id ?? null);
    setConversationSelection(fallback === null ? null : projectOverviewSelection);
    setTaskSelection(null);
  }
}

export async function collectCursorPages<T>(
  load: (cursor: string | null) => Promise<{ items: T[]; next_cursor?: string | null }>,
): Promise<{ items: T[]; next_cursor: null }> {
  const items: T[] = [];
  const seenCursors = new Set<string>();
  let cursor: string | null = null;
  do {
    const page = await load(cursor);
    items.push(...page.items);
    cursor = page.next_cursor ?? null;
    if (cursor !== null) {
      if (seenCursors.has(cursor)) throw new Error("Workspace pagination did not advance");
      seenCursors.add(cursor);
    }
  } while (cursor !== null);
  return { items, next_cursor: null };
}

export function sortHistoryItems<T extends { pinned_at: string | null; updated_at: string }>(items: T[]): T[] {
  return [...items].sort((left, right) => {
    if ((left.pinned_at !== null) !== (right.pinned_at !== null)) {
      return left.pinned_at === null ? 1 : -1;
    }
    return right.updated_at.localeCompare(left.updated_at);
  });
}

function adjacentItem<T extends { id: string }>(items: T[], removedId: string): T | null {
  const index = items.findIndex((item) => item.id === removedId);
  if (index < 0) return items.at(0) ?? null;
  return items[index + 1] ?? items[index - 1] ?? null;
}
