import { Eye, Files, Network } from "lucide-react";
import {
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

import type { WorkspaceModel } from "./workspaceModel";
import { PreviewWorkspace } from "./PreviewWorkspace";
import { WorkspaceFilesPanel } from "./WorkspaceFilesPanel";
import { ObsidianPanel } from "./ObsidianPanel";
import "./workspace-inspector.css";

type InspectorTab = "preview" | "files" | "obsidian";

const INSPECTOR_TABS: readonly InspectorTab[] = ["preview", "files", "obsidian"];
const MIN_INSPECTOR_WIDTH = 360;
const MIN_CHAT_COLUMN_WIDTH = 420;
const INSPECTOR_RESIZE_STEP = 24;

export function WorkspaceInspector({ model }: { model: WorkspaceModel }) {
  const hasFiles = model.workspaceFiles.length > 0;
  const previewReady = model.preview?.preview.status === "ready";
  const previewStarting = model.previewActivationLoading ||
    ["ready", "starting", "waiting_for_slot"].includes(model.previewActivation?.outcome ?? "");
  const activePreviewId = model.workspaceActivePreviewId;
  const hasMedia = model.mediaJobs.length > 0;
  const hasActiveMedia = model.mediaJobs.some(
    (job) => !["completed", "failed", "cancelled", "interrupted"].includes(job.status),
  );
  // Preview now also hosts generated media, so any media makes it available.
  const hasPreview = previewReady || previewStarting || activePreviewId !== null || hasMedia;
  const [tab, setTab] = useState<InspectorTab>(
    hasPreview ? "preview" : "files",
  );
  const [inspectorMaximum, setInspectorMaximum] = useState(() =>
    maximumInspectorWidth(),
  );
  const [inspectorWidth, setInspectorWidth] = useState(() =>
    clampInspectorWidth(Math.round(window.innerWidth * 0.42)),
  );
  const idPrefix = useId().replaceAll(":", "");
  const tabRefs = useRef<Record<InspectorTab, HTMLButtonElement | null>>({
    preview: null,
    files: null,
    obsidian: null,
  });
  const manuallySelectedTab = useRef(false);
  const scopeKey = [
    model.workspaceTask?.conversation_id ?? "no-conversation",
    model.workspaceTask?.id ?? "no-task",
    model.workspaceTask?.workspace_id ?? "no-workspace",
    model.workspaceTask?.target_version_id ?? "no-version",
  ].join(":");
  const recommendedTab = hasPreview ? "preview" : hasFiles ? "files" : "preview";

  useEffect(() => {
    manuallySelectedTab.current = false;
    setTab(recommendedTab);
  }, [scopeKey]);

  useEffect(() => {
    // Active generation now lives inside Preview, so pull attention there.
    if (hasActiveMedia) {
      manuallySelectedTab.current = false;
      setTab("preview");
    } else if (!manuallySelectedTab.current) {
      setTab(recommendedTab);
    }
  }, [hasActiveMedia, recommendedTab]);

  useEffect(() => {
    const parent = document.querySelector<HTMLElement>(".unified-workspace-chat, .workspace-main");
    if (parent === null) return;
    const maximum = maximumInspectorWidth(
      window.innerWidth,
      parent.clientWidth || window.innerWidth,
    );
    setInspectorMaximum(maximum);
    setInspectorWidth((current) => clampInspectorWidth(current, maximum));
    const raw = localStorage.getItem("fairy.workspace.inspector-width");
    // With no saved width, leave the CSS percentage default in place instead of
    // forcing the minimum pixel width.
    if (raw === null || raw.trim() === "") return;
    const saved = Number(raw);
    if (!Number.isFinite(saved)) return;
    const width = clampInspectorWidth(saved, maximum);
    setInspectorWidth(width);
    parent.style.setProperty("--inspector-width", `${width}px`);
  }, []);

  useEffect(() => {
    const onResize = () => {
      const parent = document.querySelector<HTMLElement>(
        ".unified-workspace-chat, .workspace-main",
      );
      const maximum = maximumInspectorWidth(
        window.innerWidth,
        parent?.clientWidth || window.innerWidth,
      );
      setInspectorMaximum(maximum);
      setInspectorWidth((current) => {
        const width = clampInspectorWidth(current, maximum);
        if (localStorage.getItem("fairy.workspace.inspector-width") !== null) {
          parent?.style.setProperty("--inspector-width", `${width}px`);
          localStorage.setItem("fairy.workspace.inspector-width", String(width));
        }
        return width;
      });
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const updateInspectorWidth = useCallback((
    value: number,
    parent?: HTMLElement | null,
  ) => {
    const width = clampInspectorWidth(value, inspectorMaximum);
    const container = parent ?? document.querySelector<HTMLElement>(
      ".unified-workspace-chat, .workspace-main",
    );
    container?.style.setProperty("--inspector-width", `${width}px`);
    localStorage.setItem("fairy.workspace.inspector-width", String(width));
    setInspectorWidth(width);
  }, [inspectorMaximum]);

  const hasKnowledgeScope = model.mode === "project"
    ? model.selectedProject !== null
    : model.selectedChatConversation !== null;
  if (model.workspaceTask === null && !hasFiles && !hasMedia && !hasKnowledgeScope) return null;

  return (
    <aside className="workspace-inspector" aria-label="Workspace inspector">
      <div
        className="workspace-inspector-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize workspace inspector"
        aria-valuemin={MIN_INSPECTOR_WIDTH}
        aria-valuemax={inspectorMaximum}
        aria-valuenow={inspectorWidth}
        tabIndex={0}
        onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          const parent = event.currentTarget.closest<HTMLElement>(
            ".unified-workspace-chat, .workspace-main",
          );
          if (parent === null) return;
          event.preventDefault();
          const next = event.key === "Home"
            ? MIN_INSPECTOR_WIDTH
            : event.key === "End"
              ? inspectorMaximum
              : inspectorWidth + (
                event.key === "ArrowLeft"
                  ? INSPECTOR_RESIZE_STEP
                  : -INSPECTOR_RESIZE_STEP
              );
          updateInspectorWidth(next, parent);
        }}
        onPointerDown={(event) => startResize(event, updateInspectorWidth)}
      />
      <div className="workspace-inspector-tabs" role="tablist" aria-label="Workspace view">
        <button
          ref={(node) => { tabRefs.current.preview = node; }}
          id={tabId("preview")}
          type="button"
          role="tab"
          aria-controls={panelId("preview")}
          aria-selected={tab === "preview"}
          tabIndex={tab === "preview" ? 0 : -1}
          onClick={() => selectTab("preview")}
          onKeyDown={(event) => moveTabWithKeyboard(event, "preview")}
        >
          <Eye size={14} /> Preview
        </button>
        <button
          ref={(node) => { tabRefs.current.files = node; }}
          id={tabId("files")}
          type="button"
          role="tab"
          aria-controls={panelId("files")}
          aria-selected={tab === "files"}
          tabIndex={tab === "files" ? 0 : -1}
          onClick={() => selectTab("files")}
          onKeyDown={(event) => moveTabWithKeyboard(event, "files")}
        >
          <Files size={14} /> Files
          {hasFiles ? <span>{model.workspaceFiles.length}</span> : null}
        </button>
        <button
          ref={(node) => { tabRefs.current.obsidian = node; }}
          id={tabId("obsidian")}
          type="button"
          role="tab"
          aria-controls={panelId("obsidian")}
          aria-selected={tab === "obsidian"}
          tabIndex={tab === "obsidian" ? 0 : -1}
          onClick={() => selectTab("obsidian")}
          onKeyDown={(event) => moveTabWithKeyboard(event, "obsidian")}
        >
          <Network size={14} /> Obsidian
        </button>
      </div>
      {INSPECTOR_TABS.map((panel) => (
        <div
          key={panel}
          id={panelId(panel)}
          className="workspace-inspector-content"
          role="tabpanel"
          aria-labelledby={tabId(panel)}
          hidden={tab !== panel}
          tabIndex={0}
        >
          {tab === panel ? panel === "preview" ? (
            <PreviewWorkspace model={model} />
          ) : panel === "files" ? (
            <WorkspaceFilesPanel
              scopeKey={scopeKey}
              files={model.workspaceFiles}
              assetSets={model.assetSets}
              versions={model.versions}
              currentVersionId={model.workspaceTask?.target_version_id ?? model.selectedVersion?.id ?? null}
              loading={model.workspaceFilesLoading}
              onRead={model.readWorkspaceFile}
              onOpenStream={model.openWorkspaceFileStream}
              onPresent={model.presentWorkspaceFile}
              onCompare={model.compareWorkspaceFile}
              onResolveFileSet={model.resolveWorkspaceFileSet}
              onListAnnotations={model.listFileAnnotations}
              onUpdateAnnotations={model.updateFileAnnotations}
              onCreateTextSelection={model.createTextSelection}
              onCreateSceneSelection={model.createSceneSelection}
              onReveal={model.revealWorkspaceFile}
              onRefresh={model.refreshWorkspaceFiles}
              onUpload={model.uploadWorkspaceFiles}
              onRename={model.renameWorkspaceFile}
              onDelete={model.deleteWorkspaceFile}
              onExport={model.exportWorkspace}
            />
          ) : (
            <ObsidianPanel model={model} onOpenFiles={() => selectTab("files")} />
          ) : null}
        </div>
      ))}
    </aside>
  );

  function selectTab(next: InspectorTab) {
    manuallySelectedTab.current = true;
    setTab(next);
  }

  function moveTabWithKeyboard(
    event: ReactKeyboardEvent<HTMLButtonElement>,
    current: InspectorTab,
  ) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const currentIndex = INSPECTOR_TABS.indexOf(current);
    const next = event.key === "Home"
      ? INSPECTOR_TABS[0]
      : event.key === "End"
        ? INSPECTOR_TABS.at(-1)!
        : INSPECTOR_TABS[
          (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + INSPECTOR_TABS.length)
          % INSPECTOR_TABS.length
        ];
    selectTab(next);
    tabRefs.current[next]?.focus();
  }

  function tabId(value: InspectorTab): string {
    return `workspace-inspector-${idPrefix}-${value}-tab`;
  }

  function panelId(value: InspectorTab): string {
    return `workspace-inspector-${idPrefix}-${value}-panel`;
  }
}

function startResize(
  event: ReactPointerEvent<HTMLDivElement>,
  updateWidth: (value: number, parent?: HTMLElement | null) => void,
) {
  event.currentTarget.setPointerCapture(event.pointerId);
  const parent = event.currentTarget.closest<HTMLElement>(".unified-workspace-chat, .workspace-main");
  if (parent === null) return;
  const move = (pointer: PointerEvent) => {
    updateWidth(window.innerWidth - pointer.clientX, parent);
  };
  const finish = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", finish);
    window.removeEventListener("pointercancel", finish);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", finish, { once: true });
  window.addEventListener("pointercancel", finish, { once: true });
}

function maximumInspectorWidth(
  viewportWidth = window.innerWidth,
  containerWidth = viewportWidth,
): number {
  return Math.max(
    MIN_INSPECTOR_WIDTH,
    Math.min(
      Math.round(viewportWidth * 0.75),
      Math.round(containerWidth - MIN_CHAT_COLUMN_WIDTH),
    ),
  );
}

function clampInspectorWidth(
  value: number,
  maximum = maximumInspectorWidth(),
): number {
  return Math.max(
    MIN_INSPECTOR_WIDTH,
    Math.min(maximum, Math.round(value)),
  );
}
