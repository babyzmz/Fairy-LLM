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
import { useEffect, useMemo, useRef, useState } from "react";
import { m } from "motion/react";
import {
  Menu,
  MenuItem,
  Popover,
  SubmenuTrigger,
} from "react-aria-components";

import type { Conversation, Project } from "../core/client";
import fairyBrandIcon from "../fairyEye/brand.svg";
import { ActionDialog } from "../ui/ActionDialog";
import type { WorkspaceModel } from "./workspaceModel";
import "./history-sidebar.css";

interface HistorySidebarProps {
  model: WorkspaceModel;
  onCreateProject(): void;
}

type MenuTarget =
  | { kind: "project"; item: Project; x: number; y: number; returnFocus: HTMLElement }
  | { kind: "conversation"; item: Conversation; x: number; y: number; returnFocus: HTMLElement };

type HistoryAction =
  | { kind: "rename-project"; project: Project }
  | { kind: "archive-project"; project: Project }
  | { kind: "delete-project"; project: Project }
  | { kind: "rename-conversation"; conversation: Conversation }
  | { kind: "delete-conversation"; conversation: Conversation }
  | { kind: "move-conversation"; conversation: Conversation; project: Project };

export function HistorySidebar({ model, onCreateProject }: HistorySidebarProps) {
  const [query, setQuery] = useState("");
  const [chatsOpen, setChatsOpen] = usePersistedOpen("fairy.history.chats-open", true);
  const [projectsOpen, setProjectsOpen] = usePersistedOpen("fairy.history.projects-open", true);
  const [expandedProjects, setExpandedProjects] = usePersistedProjectExpansion();
  const [menu, setMenu] = useState<MenuTarget | null>(null);
  const [action, setAction] = useState<HistoryAction | null>(null);
  const [taskCheck, setTaskCheck] = useState<{ action: HistoryAction; count: number | null; failed: boolean } | null>(null);
  const requiresTaskCheck = action !== null && ["delete-conversation", "delete-project", "archive-project"].includes(action.kind);
  const currentTaskCheck = taskCheck?.action === action ? taskCheck : null;
  const loadTaskCount = model.loadHistoryActiveTaskCount;
  useEffect(() => {
    if (!requiresTaskCheck || action === null) return;
    let active = true;
    const scope = action.kind === "delete-conversation"
      ? { conversationId: action.conversation.id }
      : "project" in action ? { projectId: action.project.id } : null;
    if (scope === null) return;
    void loadTaskCount(scope).then(
      (count) => { if (active) setTaskCheck({ action, count, failed: false }); },
      () => { if (active) setTaskCheck({ action, count: null, failed: true }); },
    );
    return () => { active = false; };
  }, [action, requiresTaskCheck, loadTaskCount]);
  const [sidebarWidth, setSidebarWidth] = usePersistedSidebarWidth();
  const normalizedQuery = query.trim().toLocaleLowerCase();

  const chats = useMemo(
    () => model.chatConversations.filter((conversation) => matches(conversation.title, normalizedQuery)),
    [model.chatConversations, normalizedQuery],
  );
  const projects = useMemo(
    () => model.projects.filter((project) => projectMatches(model, project, normalizedQuery)),
    [model, normalizedQuery],
  );
  const noResults = normalizedQuery.length > 0 && chats.length === 0 && projects.length === 0;

  useEffect(() => {
    document.documentElement.style.setProperty("--fairy-history-sidebar-width", `${sidebarWidth}px`);
  }, [sidebarWidth]);

  const closeMenu = () => {
    const returnFocus = menu?.returnFocus;
    setMenu(null);
    queueMicrotask(() => returnFocus?.focus());
  };

  return (
    <aside className="history-sidebar" aria-label="History navigation">
      <div className="history-brand">
        <img className="history-mark" src={fairyBrandIcon} alt="" width={20} height={20} draggable={false} />
        <strong>Fairy</strong>
      </div>
      <label className="history-search">
        <Search size={14} aria-hidden="true" />
        <span className="sr-only">Search chats and projects</span>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search" />
      </label>

      <div className="history-groups">
        {model.connectionState === "offline" ? <p className="history-status">Fairy Core is offline</p> : null}
        {model.historyLoading ? <p className="history-status">Loading history...</p> : null}
        {noResults ? <p className="history-status">No matching chats or projects</p> : null}

        <HistoryGroup
          label="Chats"
          open={chatsOpen}
          count={model.chatConversations.length}
          onOpen={setChatsOpen}
          actionLabel="New chat"
          actionDisabled={model.state === "offline" || model.isActing}
          onAction={() => void model.createChatConversation().catch(() => undefined)}
        >
          {chats.length === 0 && normalizedQuery.length === 0 ? <p className="history-empty">No chats</p> : null}
          {chats.map((conversation) => (
            <HistoryRow
              key={conversation.id}
              active={model.mode === "chat" && model.selectedChatConversation?.id === conversation.id}
              icon={<MessageSquareText size={14} />}
              label={conversation.title}
              pinned={conversation.pinned_at !== null}
              onIntent={() => void model.prefetchConversation(conversation).catch(() => undefined)}
              onSelect={() => openConversation(model, conversation)}
              onMenu={(x, y, returnFocus) =>
                setMenu({ kind: "conversation", item: conversation, x, y, returnFocus })
              }
            />
          ))}
        </HistoryGroup>

        <HistoryGroup
          label="Projects"
          open={projectsOpen}
          count={model.projects.length}
          onOpen={setProjectsOpen}
          actionLabel="New project"
          actionDisabled={model.state === "offline" || model.isActing}
          onAction={onCreateProject}
        >
          {projects.length === 0 && normalizedQuery.length === 0 ? <p className="history-empty">No projects</p> : null}
          {projects.map((project) => {
            const forcedOpen = normalizedQuery.length > 0;
            const expanded = forcedOpen || expandedProjects.has(project.id);
            return (
              <ProjectTree
                key={project.id}
                project={project}
                model={model}
                query={normalizedQuery}
                expanded={expanded}
                onExpanded={(value) => setExpandedProjects(project.id, value)}
                onMenu={setMenu}
              />
            );
          })}
        </HistoryGroup>
      </div>

      <footer className="history-footer">
        <button type="button" aria-label="Open settings" title="Settings" onClick={() => void model.openSettings().catch(() => undefined)}>
          <Settings size={16} />
          <span>Settings</span>
        </button>
      </footer>

      <div
        className="history-resizer"
        role="separator"
        aria-label="Resize history sidebar"
        aria-orientation="vertical"
        aria-valuemin={210}
        aria-valuemax={340}
        aria-valuenow={sidebarWidth}
        tabIndex={0}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          const startX = event.clientX;
          const startWidth = sidebarWidth;
          const move = (moveEvent: PointerEvent) => setSidebarWidth(startWidth + moveEvent.clientX - startX);
          const stop = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", stop);
          };
          window.addEventListener("pointermove", move);
          window.addEventListener("pointerup", stop, { once: true });
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") setSidebarWidth(sidebarWidth - 10);
          if (event.key === "ArrowRight") setSidebarWidth(sidebarWidth + 10);
        }}
      />

      {menu !== null ? (
        <HistoryMenu
          target={menu}
          model={model}
          onAction={setAction}
          onClose={closeMenu}
        />
      ) : null}
      {action !== null ? (
        <ActionDialog
          open
          busy={model.isActing}
          confirmBlocked={requiresTaskCheck && currentTaskCheck?.count == null}
          destructive={action.kind.startsWith("delete-")}
          title={historyActionTitle(action)}
          description={historyActionDescription(model, action, currentTaskCheck?.count ?? 0)
            + (requiresTaskCheck && currentTaskCheck?.count == null
              ? currentTaskCheck?.failed ? " Could not check active tasks. Cancel and reopen to retry."
                : " Checking active tasks..." : "")}
          confirmLabel={historyActionConfirmLabel(action)}
          inputLabel={action.kind.startsWith("rename-") ? "Title" : undefined}
          initialValue={
            action.kind === "rename-project"
              ? action.project.name
              : action.kind === "rename-conversation"
                ? action.conversation.title
                : ""
          }
          onCancel={() => setAction(null)}
          onConfirm={async (value) => {
            try {
              await executeHistoryAction(model, action, value);
              setAction(null);
            } catch {
              // WorkspaceModel owns the durable error and stale-state refresh.
            }
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
  actionDisabled,
  children,
  onOpen,
  onAction,
}: {
  label: string;
  open: boolean;
  count: number;
  actionLabel: string;
  actionDisabled: boolean;
  children: React.ReactNode;
  onOpen(open: boolean): void;
  onAction(): void;
}) {
  return (
    <section className="history-group">
      <div className="history-group-heading">
        <button type="button" className="history-group-toggle" aria-label={label} aria-expanded={open} onClick={() => onOpen(!open)}>
          <ChevronDown size={14} className={open ? "expanded" : ""} />
          <span>{label}</span>
          <small>{count}</small>
        </button>
        <button className="history-icon-button" type="button" aria-label={actionLabel} title={actionLabel} disabled={actionDisabled} onClick={onAction}>
          <Plus size={14} />
        </button>
      </div>
      {open ? (
        <m.div className="history-group-content" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }}>
          {children}
        </m.div>
      ) : null}
    </section>
  );
}

function ProjectTree({
  project,
  model,
  query,
  expanded,
  onExpanded,
  onMenu,
}: {
  project: Project;
  model: WorkspaceModel;
  query: string;
  expanded: boolean;
  onExpanded(expanded: boolean): void;
  onMenu(target: MenuTarget): void;
}) {
  const conversations = conversationsFor(model, project).filter(
    (conversation) => !query || matches(project.name, query) || matches(conversation.title, query),
  );
  const threadCount = conversationsFor(model, project).length;
  const openMenu = (target: HTMLElement, x: number, y: number) =>
    onMenu({ kind: "project", item: project, x, y, returnFocus: target });

  return (
    <div className="project-tree">
      <div
        className={`project-row ${model.mode === "project" && model.selectedProject?.id === project.id && model.selectedConversation === null ? "active" : ""}`}
        tabIndex={-1}
        onContextMenu={(event) => {
          event.preventDefault();
          openMenu(menuReturnFocus(event.target, event.currentTarget), event.clientX, event.clientY);
        }}
        onKeyDown={(event) => {
          if (event.shiftKey && event.key === "F10") {
            event.preventDefault();
            const returnFocus = menuReturnFocus(event.target, event.currentTarget);
            const bounds = returnFocus.getBoundingClientRect();
            openMenu(returnFocus, bounds.left + 18, bounds.bottom);
          }
        }}
      >
        <button className="project-expand" type="button" aria-label={`${expanded ? "Collapse" : "Expand"} ${project.name}`} aria-expanded={expanded} onClick={() => onExpanded(!expanded)}>
          <ChevronDown size={13} className={expanded ? "expanded" : ""} />
        </button>
        <button className="project-row-main" type="button" title={project.name} onClick={() => openProject(model, project)}>
          <FolderKanban size={14} />
          <span>{project.name}</span>
          {project.pinned_at !== null ? <Pin size={11} /> : null}
          <small>{threadCount}</small>
        </button>
        <button className="project-row-action" type="button" aria-label={`New chat in ${project.name}`} title="New chat" disabled={model.isActing} onClick={() => void model.createProjectConversation(project).catch(() => undefined)}>
          <Plus size={13} />
        </button>
        <button
          className="history-row-menu"
          type="button"
          aria-label={`Actions for ${project.name}`}
          title="Actions"
          onClick={(event) => {
            const bounds = event.currentTarget.getBoundingClientRect();
            openMenu(event.currentTarget, bounds.right, bounds.bottom);
          }}
        >
          <Ellipsis size={14} />
        </button>
      </div>
      {expanded ? (
        <m.div className="project-conversations" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }}>
          {conversations.length === 0 ? <p className="history-empty project-empty">No chats</p> : null}
          {conversations.map((conversation) => (
            <HistoryRow
              key={conversation.id}
              active={model.mode === "project" && model.selectedConversation?.id === conversation.id}
              icon={<MessageSquareText size={13} />}
              label={conversation.title}
              pinned={conversation.pinned_at !== null}
              nested
              onIntent={() => void model.prefetchConversation(conversation).catch(() => undefined)}
              onSelect={() => openConversation(model, conversation)}
              onMenu={(x, y, returnFocus) =>
                onMenu({ kind: "conversation", item: conversation, x, y, returnFocus })
              }
            />
          ))}
        </m.div>
      ) : null}
    </div>
  );
}

function HistoryRow({
  active,
  icon,
  label,
  pinned,
  nested = false,
  onIntent,
  onSelect,
  onMenu,
}: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  pinned: boolean;
  nested?: boolean;
  onIntent(): void;
  onSelect(): void;
  onMenu(x: number, y: number, returnFocus: HTMLElement): void;
}) {
  const openKeyboardMenu = (target: HTMLElement) => {
    const bounds = target.getBoundingClientRect();
    onMenu(bounds.left + 18, bounds.bottom, target);
  };
  return (
    <div
      className={`history-row ${active ? "active" : ""} ${nested ? "nested" : ""}`}
      tabIndex={-1}
      onContextMenu={(event) => {
        event.preventDefault();
        onMenu(
          event.clientX,
          event.clientY,
          menuReturnFocus(event.target, event.currentTarget),
        );
      }}
      onKeyDown={(event) => {
        if (event.shiftKey && event.key === "F10") {
          event.preventDefault();
          openKeyboardMenu(menuReturnFocus(event.target, event.currentTarget));
        }
      }}
    >
      <button
        type="button"
        className="history-row-main"
        onMouseEnter={onIntent}
        onFocus={onIntent}
        onClick={onSelect}
        title={label}
      >
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
          onMenu(bounds.right, bounds.bottom, event.currentTarget);
        }}
      >
        <Ellipsis size={14} />
      </button>
    </div>
  );
}

function menuReturnFocus(target: EventTarget | null, row: HTMLElement): HTMLElement {
  return target instanceof HTMLElement ? (target.closest<HTMLElement>("button") ?? row) : row;
}

function HistoryMenu({
  target,
  model,
  onAction,
  onClose,
}: {
  target: MenuTarget;
  model: WorkspaceModel;
  onAction(action: HistoryAction): void;
  onClose(): void;
}) {
  const anchorRef = useRef<HTMLButtonElement>(null);
  const style = { left: target.x, top: target.y };
  const act = (operation: Promise<unknown>) => {
    onClose();
    void operation.catch(() => undefined);
  };

  return (
    <>
      <button ref={anchorRef} className="history-menu-anchor" style={style} tabIndex={-1} aria-hidden="true" />
      <Popover
        className="history-menu-popover"
        triggerRef={anchorRef}
        isOpen
        onOpenChange={(open) => { if (!open) onClose(); }}
        placement="bottom start"
        offset={2}
        containerPadding={8}
        shouldFlip
      >
        {target.kind === "project" ? (
          <Menu aria-label={`Actions for ${target.item.name}`} className="history-menu" onAction={(key) => {
            const project = target.item;
            if (key === "open") { openProject(model, project); onClose(); }
            if (key === "new-chat") act(model.createProjectConversation(project));
            if (key === "rename") { onAction({ kind: "rename-project", project }); onClose(); }
            if (key === "pin") act(model.setProjectPinned(project, project.pinned_at === null));
            if (key === "archive") { onAction({ kind: "archive-project", project }); onClose(); }
            if (key === "delete") { onAction({ kind: "delete-project", project }); onClose(); }
          }}>
            <HistoryMenuItem id="open" icon={<FolderKanban size={14} />}>Open</HistoryMenuItem>
            <HistoryMenuItem id="new-chat" icon={<Plus size={14} />}>New chat</HistoryMenuItem>
            <HistoryMenuItem id="rename" icon={<Pencil size={14} />}>Rename</HistoryMenuItem>
            <HistoryMenuItem id="pin" icon={target.item.pinned_at === null ? <Pin size={14} /> : <PinOff size={14} />}>
              {target.item.pinned_at === null ? "Pin" : "Unpin"}
            </HistoryMenuItem>
            <HistoryMenuItem id="archive" icon={<Archive size={14} />}>Archive</HistoryMenuItem>
            <HistoryMenuItem id="delete" icon={<Trash2 size={14} />} danger>Delete</HistoryMenuItem>
          </Menu>
        ) : (
          <ConversationMenu target={target} model={model} onAction={onAction} onClose={onClose} act={act} />
        )}
      </Popover>
    </>
  );
}

function ConversationMenu({
  target,
  model,
  onAction,
  onClose,
  act,
}: {
  target: Extract<MenuTarget, { kind: "conversation" }>;
  model: WorkspaceModel;
  onAction(action: HistoryAction): void;
  onClose(): void;
  act(operation: Promise<unknown>): void;
}) {
  const conversation = target.item;
  const scratch = conversation.workspace_type === "chat_scratch";
  return (
    <Menu aria-label={`Actions for ${conversation.title}`} className="history-menu" onAction={(key) => {
      if (key === "open") { openConversation(model, conversation); onClose(); }
      if (key === "rename") { onAction({ kind: "rename-conversation", conversation }); onClose(); }
      if (key === "pin") act(model.setConversationPinned(conversation, conversation.pinned_at === null));
      if (key === "delete") { onAction({ kind: "delete-conversation", conversation }); onClose(); }
    }}>
      <HistoryMenuItem id="open" icon={<MessageSquareText size={14} />}>Open</HistoryMenuItem>
      <HistoryMenuItem id="rename" icon={<Pencil size={14} />}>Rename</HistoryMenuItem>
      <HistoryMenuItem id="pin" icon={conversation.pinned_at === null ? <Pin size={14} /> : <PinOff size={14} />}>
        {conversation.pinned_at === null ? "Pin" : "Unpin"}
      </HistoryMenuItem>
      {scratch ? (
        <SubmenuTrigger>
          <HistoryMenuItem id="move" icon={<FolderInput size={14} />}>Move to project</HistoryMenuItem>
          <Popover className="history-menu-popover" placement="end top" offset={4} containerPadding={8} shouldFlip>
            <Menu aria-label="Move to project" className="history-menu history-submenu" onAction={(key) => {
              const project = model.projects.find((item) => item.id === String(key));
              if (project !== undefined) {
                onAction({ kind: "move-conversation", conversation, project });
                onClose();
              }
            }}>
              {model.projects.length === 0 ? (
                <MenuItem id="no-projects" isDisabled className="history-menu-item">No projects</MenuItem>
              ) : model.projects.map((project) => (
                <HistoryMenuItem key={project.id} id={project.id} icon={<FolderKanban size={13} />}>
                  {project.name}
                </HistoryMenuItem>
              ))}
            </Menu>
          </Popover>
        </SubmenuTrigger>
      ) : null}
      <HistoryMenuItem id="delete" icon={<Trash2 size={14} />} danger>Delete</HistoryMenuItem>
    </Menu>
  );
}

function HistoryMenuItem({
  id,
  icon,
  danger = false,
  children,
}: {
  id: string;
  icon: React.ReactNode;
  danger?: boolean;
  children: React.ReactNode;
}) {
  return (
    <MenuItem id={id} textValue={String(children)} className={`history-menu-item ${danger ? "danger" : ""}`}>
      {icon}<span>{children}</span>
    </MenuItem>
  );
}

async function executeHistoryAction(model: WorkspaceModel, action: HistoryAction, value: string | null) {
  switch (action.kind) {
    case "rename-project":
      if (value !== null) await model.renameProject(action.project, value);
      return;
    case "archive-project":
      await model.archiveProject(action.project);
      return;
    case "delete-project":
      await model.deleteProject(action.project, true);
      return;
    case "rename-conversation":
      if (value !== null) await model.renameConversation(action.conversation, value);
      return;
    case "delete-conversation":
      await model.deleteConversation(action.conversation);
      return;
    case "move-conversation":
      await model.moveConversationToProject(action.conversation, action.project);
  }
}

function historyActionTitle(action: HistoryAction) {
  switch (action.kind) {
    case "rename-project": return "Rename project";
    case "archive-project": return "Archive project";
    case "delete-project": return "Delete project";
    case "rename-conversation": return "Rename chat";
    case "delete-conversation": return "Delete chat";
    case "move-conversation": return "Move chat to project";
  }
}

function historyActionDescription(model: WorkspaceModel, action: HistoryAction, active: number) {
  if (action.kind === "rename-project") return `Choose a new name for “${action.project.name}”.`;
  if (action.kind === "rename-conversation") return `Choose a new title for “${action.conversation.title}”.`;
  if (action.kind === "move-conversation") {
    return `Copy “${action.conversation.title}” and its Workspace into ${action.project.name}, then move the source chat to Recently deleted.`;
  }
  if (action.kind === "delete-conversation") {
    return `Move “${action.conversation.title}” to Recently deleted. Its synchronization tombstone and audit provenance remain durable.${active > 0 ? ` ${active} active execution must finish or be cancelled first.` : " You can restore it from Settings."}`;
  }
  const conversations = conversationsFor(model, action.project);
  if (action.kind === "archive-project") {
    return `Archive “${action.project.name}” with ${conversations.length} chat${conversations.length === 1 ? "" : "s"}.${active > 0 ? ` ${active} active execution must finish before archiving.` : " Restore it from Settings → General → Project management."}`;
  }
  return `Move “${action.project.name}” and its ${conversations.length} chat${conversations.length === 1 ? "" : "s"} to Recently deleted.${active > 0 ? ` ${active} active execution will be cancelled first.` : " You can restore the project from Settings."}`;
}

function historyActionConfirmLabel(action: HistoryAction) {
  switch (action.kind) {
    case "rename-project":
    case "rename-conversation": return "Rename";
    case "archive-project": return "Archive";
    case "delete-project":
    case "delete-conversation": return "Delete";
    case "move-conversation": return "Move";
  }
}

function openProject(model: WorkspaceModel, project: Project) {
  model.setMode("project");
  model.selectProject(project.id);
}

function openConversation(model: WorkspaceModel, conversation: Conversation) {
  if (conversation.project_id === null) {
    model.setMode("chat");
    model.selectChatConversation(conversation.id);
    return;
  }
  model.setMode("project");
  model.selectProject(conversation.project_id);
  model.selectConversation(conversation.id);
}

function conversationsFor(model: WorkspaceModel, project: Project) {
  return model.projectConversations.filter((conversation) => conversation.project_id === project.id);
}

function projectMatches(model: WorkspaceModel, project: Project, query: string) {
  if (!query || matches(project.name, query)) return true;
  return conversationsFor(model, project).some((conversation) => matches(conversation.title, query));
}

function matches(value: string, query: string) {
  return !query || value.toLocaleLowerCase().includes(query);
}

function usePersistedOpen(key: string, fallback: boolean) {
  const [open, setOpen] = useState(() => window.localStorage.getItem(key) !== "false" && fallback);
  const update = (value: boolean) => {
    setOpen(value);
    window.localStorage.setItem(key, String(value));
  };
  return [open, update] as const;
}

function usePersistedProjectExpansion() {
  const storageKey = "fairy.history.expanded-projects";
  const [ids, setIds] = useState<Set<string>>(() => {
    try {
      const value = JSON.parse(window.localStorage.getItem(storageKey) ?? "[]");
      return new Set(Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []);
    } catch {
      return new Set();
    }
  });
  const update = (projectId: string, expanded: boolean) => {
    setIds((current) => {
      const next = new Set(current);
      if (expanded) next.add(projectId);
      else next.delete(projectId);
      window.localStorage.setItem(storageKey, JSON.stringify([...next]));
      return next;
    });
  };
  return [ids, update] as const;
}

function usePersistedSidebarWidth() {
  const storageKey = "fairy.history.sidebar-width";
  const clamp = (value: number) => Math.max(210, Math.min(340, Math.round(value)));
  const [width, setWidth] = useState(() => {
    const stored = Number(window.localStorage.getItem(storageKey));
    return Number.isFinite(stored) && stored > 0 ? clamp(stored) : 250;
  });
  const update = (value: number) => {
    const next = clamp(value);
    setWidth(next);
    window.localStorage.setItem(storageKey, String(next));
  };
  return [width, update] as const;
}
