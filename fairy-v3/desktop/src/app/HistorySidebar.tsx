import {
  Archive,
  ChevronDown,
  Ellipsis,
  FolderInput,
  FolderKanban,
  MessageSquareText,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Search,
  Settings,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { Conversation, Project, Task } from "../core/client";
import type { WorkspaceModel } from "./workspaceModel";
import "./history-sidebar.css";

interface HistorySidebarProps {
  model: WorkspaceModel;
  onCreateProject(): void;
}

type MenuTarget =
  | { kind: "conversation"; item: Conversation; x: number; y: number }
  | { kind: "task"; item: Task; x: number; y: number };

const terminalTaskStatuses = new Set([
  "ready",
  "accepted",
  "rejected",
  "failed",
]);

export function HistorySidebar({ model, onCreateProject }: HistorySidebarProps) {
  const [query, setQuery] = useState("");
  const [chatsOpen, setChatsOpen] = usePersistedOpen("fairy.history.chats-open", true);
  const [projectsOpen, setProjectsOpen] = usePersistedOpen(
    "fairy.history.projects-open",
    true,
  );
  const [menu, setMenu] = useState<MenuTarget | null>(null);
  const [moveOpen, setMoveOpen] = useState(false);
  const normalizedQuery = query.trim().toLocaleLowerCase();

  useEffect(() => {
    const close = () => {
      setMenu(null);
      setMoveOpen(false);
    };
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, []);

  const chats = useMemo(
    () =>
      sortPinned(model.chatConversations).filter((conversation) =>
        matches(conversation.title, normalizedQuery),
      ),
    [model.chatConversations, normalizedQuery],
  );
  const projects = useMemo(
    () =>
      model.projects.filter((project) => {
        if (matches(project.name, normalizedQuery)) return true;
        const conversations = conversationsFor(model, project);
        return conversations.some(
          (conversation) =>
            matches(conversation.title, normalizedQuery) ||
            tasksFor(model, conversation).some((task) =>
              matches(task.display_title, normalizedQuery),
            ),
        );
      }),
    [model, normalizedQuery],
  );

  return (
    <aside className="history-sidebar" aria-label="History navigation">
      <div className="history-brand">
        <span className="history-mark" aria-hidden="true" />
        <strong>Fairy</strong>
      </div>
      <label className="history-search">
        <Search size={14} aria-hidden="true" />
        <span className="sr-only">Search chats and projects</span>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search"
        />
      </label>

      <div className="history-groups">
        <HistoryGroup
          label="Chats"
          open={chatsOpen}
          count={model.chatConversations.length}
          onOpen={setChatsOpen}
          actionLabel="New chat"
          onAction={() => void model.createChatConversation()}
        >
          {chats.length === 0 ? (
            <p className="history-empty">No chats</p>
          ) : (
            chats.map((conversation) => (
              <HistoryRow
                key={conversation.id}
                active={model.selectedChatConversation?.id === conversation.id}
                icon={<MessageSquareText size={14} />}
                label={conversation.title}
                pinned={conversation.pinned_at !== null}
                onSelect={() => {
                  model.setMode("chat");
                  model.selectChatConversation(conversation.id);
                }}
                onMenu={(x, y) =>
                  setMenu({ kind: "conversation", item: conversation, x, y })
                }
              />
            ))
          )}
        </HistoryGroup>

        <HistoryGroup
          label="Projects"
          open={projectsOpen}
          count={model.projects.length}
          onOpen={setProjectsOpen}
          actionLabel="New project"
          onAction={onCreateProject}
        >
          {projects.length === 0 ? (
            <p className="history-empty">No projects</p>
          ) : (
            projects.map((project) => (
              <ProjectTree
                key={project.id}
                project={project}
                model={model}
                query={normalizedQuery}
                onMenu={setMenu}
              />
            ))
          )}
        </HistoryGroup>
      </div>

      <footer className="history-footer">
        <button type="button" aria-label="Open settings" title="Settings" onClick={() => void model.openSettings()}>
          <Settings size={16} />
          <span>Settings</span>
        </button>
      </footer>

      {menu !== null ? (
        <HistoryMenu
          target={menu}
          model={model}
          moveOpen={moveOpen}
          onMoveOpen={() => setMoveOpen((current) => !current)}
          onClose={() => {
            setMenu(null);
            setMoveOpen(false);
          }}
        />
      ) : null}
    </aside>
  );
}

function HistoryGroup({
  label,
  open,
  count,
  actionLabel,
  children,
  onOpen,
  onAction,
}: {
  label: string;
  open: boolean;
  count: number;
  actionLabel: string;
  children: React.ReactNode;
  onOpen(open: boolean): void;
  onAction(): void;
}) {
  return (
    <section className="history-group">
      <div className="history-group-heading">
        <button
          type="button"
          className="history-group-toggle"
          aria-label={label}
          aria-expanded={open}
          onClick={() => onOpen(!open)}
        >
          <ChevronDown size={14} className={open ? "expanded" : ""} />
          <span>{label}</span>
          <small>{count}</small>
        </button>
        <button className="history-icon-button" type="button" aria-label={actionLabel} title={actionLabel} onClick={onAction}>
          <Plus size={14} />
        </button>
      </div>
      {open ? <div className="history-group-content">{children}</div> : null}
    </section>
  );
}

function ProjectTree({
  project,
  model,
  query,
  onMenu,
}: {
  project: Project;
  model: WorkspaceModel;
  query: string;
  onMenu(target: MenuTarget): void;
}) {
  const conversations = sortPinned(conversationsFor(model, project)).filter((conversation) => {
    if (!query || matches(project.name, query) || matches(conversation.title, query)) return true;
    return tasksFor(model, conversation).some((task) => matches(task.display_title, query));
  });
  return (
    <details className="project-tree" open={query.length > 0 || model.selectedProject?.id === project.id}>
      <summary
        className={model.selectedProject?.id === project.id ? "active" : ""}
        onClick={() => {
          model.setMode("project");
          model.selectProject(project.id);
        }}
      >
        <ChevronDown size={13} />
        <FolderKanban size={14} />
        <span>{project.name}</span>
      </summary>
      <div className="project-conversations">
        {conversations.map((conversation) => (
          <ConversationTree
            key={conversation.id}
            conversation={conversation}
            model={model}
            query={query}
            onMenu={onMenu}
          />
        ))}
      </div>
    </details>
  );
}

function ConversationTree({
  conversation,
  model,
  query,
  onMenu,
}: {
  conversation: Conversation;
  model: WorkspaceModel;
  query: string;
  onMenu(target: MenuTarget): void;
}) {
  const tasks = sortPinned(tasksFor(model, conversation)).filter(
    (task) => !query || matches(conversation.title, query) || matches(task.display_title, query),
  );
  return (
    <details className="conversation-tree" open={query.length > 0 || model.selectedConversation?.id === conversation.id}>
      <summary
        className={model.selectedConversation?.id === conversation.id ? "active" : ""}
        onClick={() => {
          model.setMode("project");
          if (conversation.project_id !== null) model.selectProject(conversation.project_id);
          model.selectConversation(conversation.id);
        }}
        onContextMenu={(event) => {
          event.preventDefault();
          onMenu({
            kind: "conversation",
            item: conversation,
            x: event.clientX,
            y: event.clientY,
          });
        }}
      >
        <ChevronDown size={12} />
        <MessageSquareText size={13} />
        <span>{conversation.title}</span>
        {conversation.pinned_at !== null ? <Pin size={11} /> : null}
      </summary>
      <div className="task-tree">
        {tasks.map((task) => (
          <HistoryRow
            key={task.id}
            active={model.selectedTask?.id === task.id}
            icon={task.status === "archived" ? <Archive size={13} /> : <span className="task-dot" />}
            label={task.display_title}
            pinned={task.pinned_at !== null}
            muted={task.status === "archived"}
            onSelect={() => {
              model.setMode("project");
              if (conversation.project_id !== null) model.selectProject(conversation.project_id);
              model.selectConversation(conversation.id);
              model.selectTask(task.id);
            }}
            onMenu={(x, y) => onMenu({ kind: "task", item: task, x, y })}
          />
        ))}
      </div>
    </details>
  );

}

function HistoryRow({
  active,
  icon,
  label,
  pinned,
  muted = false,
  onSelect,
  onMenu,
}: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  pinned: boolean;
  muted?: boolean;
  onSelect(): void;
  onMenu(x: number, y: number): void;
}) {
  return (
    <div
      className={`history-row ${active ? "active" : ""} ${muted ? "muted" : ""}`}
      onContextMenu={(event) => {
        event.preventDefault();
        onMenu(event.clientX, event.clientY);
      }}
    >
      <button type="button" className="history-row-main" onClick={onSelect} title={label}>
        {icon}
        <span>{label}</span>
        {pinned ? <Pin size={11} /> : null}
      </button>
      <button
        type="button"
        className="history-row-menu"
        aria-label={`Actions for ${label}`}
        title="Actions"
        onClick={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect();
          onMenu(bounds.right, bounds.bottom);
        }}
      >
        <Ellipsis size={14} />
      </button>
    </div>
  );
}

function HistoryMenu({
  target,
  model,
  moveOpen,
  onMoveOpen,
  onClose,
}: {
  target: MenuTarget;
  model: WorkspaceModel;
  moveOpen: boolean;
  onMoveOpen(): void;
  onClose(): void;
}) {
  const style = {
    left: Math.min(target.x, window.innerWidth - 220),
    top: Math.min(target.y, window.innerHeight - 280),
  };
  if (target.kind === "task") {
    const task = target.item;
    return (
      <div className="history-menu" style={style} role="menu" onPointerDown={(event) => event.stopPropagation()}>
        <MenuButton icon={<Pencil size={14} />} label="Rename" onClick={() => renameTask(model, task, onClose)} />
        <MenuButton
          icon={task.pinned_at === null ? <Pin size={14} /> : <PinOff size={14} />}
          label={task.pinned_at === null ? "Pin" : "Unpin"}
          onClick={() => void model.setTaskPinned(task, task.pinned_at === null).finally(onClose)}
        />
        <MenuButton icon={<FolderKanban size={14} />} label="Open project" onClick={() => openTask(model, task, onClose)} />
        <MenuButton
          icon={<Archive size={14} />}
          label="Archive"
          disabled={!terminalTaskStatuses.has(task.status)}
          onClick={() => void model.archiveTask(task).finally(onClose)}
        />
      </div>
    );
  }
  const conversation = target.item;
  const scratch = conversation.workspace_type === "chat_scratch";
  return (
    <div className="history-menu" style={style} role="menu" onPointerDown={(event) => event.stopPropagation()}>
      <MenuButton icon={<Pencil size={14} />} label="Rename" onClick={() => renameConversation(model, conversation, onClose)} />
      <MenuButton
        icon={conversation.pinned_at === null ? <Pin size={14} /> : <PinOff size={14} />}
        label={conversation.pinned_at === null ? "Pin" : "Unpin"}
        onClick={() => void model.setConversationPinned(conversation, conversation.pinned_at === null).finally(onClose)}
      />
      {scratch ? (
        <>
          <MenuButton icon={<FolderInput size={14} />} label="Move to project" onClick={onMoveOpen} />
          {moveOpen ? (
            <div className="history-menu-projects">
              {model.projects.map((project) => (
                <button key={project.id} type="button" role="menuitem" onClick={() => moveConversation(model, conversation, project, onClose)}>
                  <FolderKanban size={13} /> <span>{project.name}</span>
                </button>
              ))}
            </div>
          ) : null}
          <MenuButton className="danger" icon={<Trash2 size={14} />} label="Delete" onClick={() => deleteConversation(model, conversation, onClose)} />
        </>
      ) : null}
    </div>
  );
}

function MenuButton({ icon, label, className = "", disabled = false, onClick }: { icon: React.ReactNode; label: string; className?: string; disabled?: boolean; onClick(): void }) {
  return <button className={className} type="button" role="menuitem" disabled={disabled} onClick={onClick}>{icon}<span>{label}</span></button>;
}

function renameConversation(model: WorkspaceModel, conversation: Conversation, close: () => void) {
  const title = window.prompt("Rename chat", conversation.title)?.trim();
  if (title) void model.renameConversation(conversation, title).finally(close);
  else close();
}

function renameTask(model: WorkspaceModel, task: Task, close: () => void) {
  const title = window.prompt("Rename task", task.display_title)?.trim();
  if (title) void model.renameTask(task, title).finally(close);
  else close();
}

function deleteConversation(model: WorkspaceModel, conversation: Conversation, close: () => void) {
  if (window.confirm(`Delete “${conversation.title}”?`)) {
    void model.deleteConversation(conversation).finally(close);
  } else close();
}

function moveConversation(model: WorkspaceModel, conversation: Conversation, project: Project, close: () => void) {
  if (window.confirm(`Move “${conversation.title}” to ${project.name}?`)) {
    void model.moveConversationToProject(conversation, project).finally(close);
  } else close();
}

function openTask(model: WorkspaceModel, task: Task, close: () => void) {
  const conversation = model.projectConversations.find((item) => item.id === task.conversation_id);
  if (conversation?.project_id !== null && conversation !== undefined) {
    model.setMode("project");
    model.selectProject(conversation.project_id);
    model.selectConversation(conversation.id);
    model.selectTask(task.id);
  }
  close();
}

function conversationsFor(model: WorkspaceModel, project: Project) {
  return model.projectConversations.filter((conversation) => conversation.project_id === project.id);
}

function tasksFor(model: WorkspaceModel, conversation: Conversation) {
  return model.allTasks.filter((task) => task.conversation_id === conversation.id);
}

function sortPinned<T extends { pinned_at: string | null; updated_at: string }>(items: T[]): T[] {
  return [...items].sort((left, right) => {
    if ((left.pinned_at !== null) !== (right.pinned_at !== null)) return left.pinned_at === null ? 1 : -1;
    return right.updated_at.localeCompare(left.updated_at);
  });
}

function matches(value: string, query: string) {
  return !query || value.toLocaleLowerCase().includes(query);
}

function usePersistedOpen(key: string, fallback: boolean) {
  const [open, setOpen] = useState(() => {
    const stored = window.localStorage.getItem(key);
    return stored === null ? fallback : stored === "true";
  });
  const update = (value: boolean) => {
    setOpen(value);
    window.localStorage.setItem(key, String(value));
  };
  return [open, update] as const;
}
