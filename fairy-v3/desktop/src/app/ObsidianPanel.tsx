import {
  ArrowLeft,
  BookOpenText,
  CircleDot,
  FolderTree,
  Link2,
  Network,
  RefreshCw,
  RotateCcw,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type {
  ObsidianVaultItem,
  ObsidianVaultItemContent,
  ObsidianVaultSelection,
  WorkspaceFile,
} from "../core/client";
import type { WorkspaceModel } from "./workspaceModel";
import "./obsidian-panel.css";

type ObsidianView = "overview" | "notes" | "links" | "graph" | "sync";

interface GraphNode {
  id: string;
  label: string;
  kind: "project" | "conversation" | "folder" | "file";
}

interface GraphEdge {
  source: string;
  target: string;
}

interface NoteEntry {
  id: string;
  title: string;
  relativePath: string;
  byteLength: number;
  vaultItem?: ObsidianVaultItem;
}

const views: Array<{ id: ObsidianView; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "notes", label: "Notes" },
  { id: "links", label: "Links" },
  { id: "graph", label: "Graph" },
  { id: "sync", label: "Sync" },
];

export function ObsidianPanel({ model, onOpenFiles }: { model: WorkspaceModel; onOpenFiles(): void }) {
  const [view, setView] = useState<ObsidianView>("overview");
  const [openNote, setOpenNote] = useState<ObsidianVaultItemContent | null>(null);
  const [noteLoading, setNoteLoading] = useState(false);
  const [noteError, setNoteError] = useState<string | null>(null);
  const project = model.selectedProject;
  const conversations = useMemo(
    () => model.projectConversations.filter((item) => item.project_id === project?.id),
    [model.projectConversations, project?.id],
  );
  const graph = useMemo(
    () => {
      const base = model.knowledgeGraph === null
        ? buildProjectGraph(project?.id ?? null, project?.name ?? "Project", conversations, model.workspaceFiles)
        : {
          nodes: model.knowledgeGraph.nodes.map((node) => ({
            id: node.id,
            label: node.title,
            kind: graphKind(node.kind),
          })),
          edges: model.knowledgeGraph.edges.map((edge) => ({ source: edge.source_id, target: edge.target_id })),
        };
      return mergeVaultGraph(base, project?.id ?? null, model.obsidianItems);
    },
    [conversations, model.knowledgeGraph, model.obsidianItems, model.workspaceFiles, project?.id, project?.name],
  );
  const noteFiles = useMemo(
    () => [
      ...model.knowledgeItems.filter((item) => item.kind === "note").map((item) => ({
        id: item.id,
        title: item.title,
        relativePath: item.relative_path ?? item.title,
        byteLength: item.byte_length ?? 0,
      })),
      ...model.obsidianItems.map((item) => ({
        id: `obsidian:${item.source_id}:${item.relative_path}`,
        title: item.title,
        relativePath: item.relative_path,
        byteLength: item.byte_length,
        vaultItem: item,
      })),
    ],
    [model.knowledgeItems, model.obsidianItems],
  );
  const showNote = async (entry: NoteEntry) => {
    if (entry.vaultItem === undefined) {
      onOpenFiles();
      return;
    }
    setNoteLoading(true);
    setNoteError(null);
    try {
      setOpenNote(await model.readObsidianItem(entry.vaultItem));
    } catch (error) {
      setNoteError(error instanceof Error ? error.message : "Unable to read this note");
    } finally {
      setNoteLoading(false);
    }
  };

  if (project === null) {
    return (
      <section className="obsidian-empty" aria-label="Obsidian project knowledge">
        <Network size={24} />
        <h2>Select a project</h2>
        <p>Project knowledge, managed notes, links, and graph are scoped to one project.</p>
      </section>
    );
  }

  return (
    <section className="obsidian-panel" aria-label="Obsidian project knowledge">
      <header className="obsidian-header">
        <div>
          <span>PROJECT KNOWLEDGE</span>
          <h2>{project.name}</h2>
        </div>
        <div className="obsidian-health" data-state="indexed">
          <CircleDot size={12} /> Fairy index ready
        </div>
      </header>
      <nav className="obsidian-subnav" aria-label="Obsidian views">
        {views.map((item) => (
          <button
            key={item.id}
            type="button"
            aria-current={view === item.id ? "page" : undefined}
            onClick={() => setView(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div className="obsidian-body">
        {view === "overview" ? (
          <Overview
            fileCount={model.knowledgeOverview?.file_count ?? model.workspaceFiles.length}
            noteCount={model.knowledgeOverview?.note_count ?? noteFiles.length}
            conversationCount={model.knowledgeOverview?.conversation_count ?? conversations.length}
            linkCount={model.knowledgeOverview?.relation_count ?? graph.edges.length}
            onOpenFiles={onOpenFiles}
          />
        ) : view === "notes" ? (
          openNote !== null ? (
            <NoteReader note={openNote} onBack={() => setOpenNote(null)} />
          ) : (
            <Notes files={noteFiles} loading={noteLoading} error={noteError} onOpenNote={showNote} />
          )
        ) : view === "links" ? (
          <Links graph={graph} />
        ) : view === "graph" ? (
          <ProjectGraph nodes={graph.nodes} edges={graph.edges} />
        ) : (
          <SyncStatus model={model} />
        )}
      </div>
    </section>
  );
}

function Overview({
  fileCount,
  noteCount,
  conversationCount,
  linkCount,
  onOpenFiles,
}: {
  fileCount: number;
  noteCount: number;
  conversationCount: number;
  linkCount: number;
  onOpenFiles(): void;
}) {
  return (
    <div className="obsidian-overview">
      <dl className="obsidian-metrics">
        <div><dt>Files</dt><dd>{fileCount}</dd></div>
        <div><dt>Managed notes</dt><dd>{noteCount}</dd></div>
        <div><dt>Project chats</dt><dd>{conversationCount}</dd></div>
        <div><dt>Relations</dt><dd>{linkCount}</dd></div>
      </dl>
      <section>
        <FolderTree size={18} />
        <div><h3>Workspace remains authoritative</h3><p>Source files stay in Fairy. Obsidian receives editable notes and stable links instead of duplicate source trees.</p></div>
        <button type="button" onClick={onOpenFiles}>Open files</button>
      </section>
      <section>
        <BookOpenText size={18} />
        <div><h3>Vault connection</h3><p>No official Obsidian Vault is connected yet. The project graph below is built from Fairy's current immutable file index.</p></div>
      </section>
    </div>
  );
}

function Notes({
  files,
  loading,
  error,
  onOpenNote,
}: {
  files: NoteEntry[];
  loading: boolean;
  error: string | null;
  onOpenNote(file: NoteEntry): Promise<void>;
}) {
  if (files.length === 0) {
    return <EmptyState icon={<BookOpenText size={22} />} title="No managed notes" detail="Markdown notes created for this project will appear here." />;
  }
  return (
    <div className="obsidian-list">
      {error === null ? null : <p className="obsidian-note-error">{error}</p>}
      {files.map((file) => (
        <button key={file.id} type="button" disabled={loading} onClick={() => void onOpenNote(file)}>
          <BookOpenText size={15} />
          <span><strong>{file.title}</strong><small>{file.relativePath}</small></span>
          <small>{formatBytes(file.byteLength)}</small>
        </button>
      ))}
    </div>
  );
}

function NoteReader({ note, onBack }: { note: ObsidianVaultItemContent; onBack(): void }) {
  return (
    <article className="obsidian-note-reader">
      <header>
        <button type="button" onClick={onBack} aria-label="Back to notes" title="Back to notes">
          <ArrowLeft size={16} />
        </button>
        <div><h3>{note.title}</h3><small>{note.relative_path}</small></div>
      </header>
      {note.kind === "markdown" ? (
        <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ a: ({ children }) => <span>{children}</span> }}>
          {note.content}
        </ReactMarkdown>
      ) : (
        <pre>{note.content}</pre>
      )}
    </article>
  );
}

function Links({ graph }: { graph: { nodes: GraphNode[]; edges: GraphEdge[] } }) {
  if (graph.edges.length === 0) {
    return <EmptyState icon={<Link2 size={22} />} title="No relations yet" detail="Folder, thread, and note relations appear after the project has indexed content." />;
  }
  const labels = new Map(graph.nodes.map((node) => [node.id, node.label]));
  return (
    <div className="obsidian-link-list">
      {graph.edges.slice(0, 200).map((edge) => (
        <div key={`${edge.source}:${edge.target}`}>
          <span>{labels.get(edge.source)}</span><Link2 size={13} /><span>{labels.get(edge.target)}</span>
        </div>
      ))}
    </div>
  );
}

function ProjectGraph({ nodes, edges }: { nodes: GraphNode[]; edges: GraphEdge[] }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const positionsRef = useRef(new Map<string, { x: number; y: number }>());
  const [zoom, setZoom] = useState(1);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = nodes.find((node) => node.id === selectedId) ?? null;
  const related = selected === null ? [] : edges
    .filter((edge) => edge.source === selected.id || edge.target === selected.id)
    .map((edge) => nodes.find((node) => node.id === (edge.source === selected.id ? edge.target : edge.source)))
    .filter((node): node is GraphNode => node !== undefined)
    .slice(0, 12);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    if (typeof ResizeObserver === "undefined") return;
    const draw = () => { positionsRef.current = drawGraph(canvas, nodes, edges, zoom, selectedId); };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [edges, nodes, selectedId, zoom]);
  const selectAt = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
    let match: string | null = null;
    let distance = 18;
    for (const [id, position] of positionsRef.current) {
      const candidate = Math.hypot(position.x - x, position.y - y);
      if (candidate < distance) { match = id; distance = candidate; }
    }
    setSelectedId(match);
  };
  return (
    <div className="obsidian-graph">
      <canvas
        ref={canvasRef}
        aria-label={`Project graph with ${nodes.length} nodes and ${edges.length} links`}
        onClick={selectAt}
      />
      <div className="obsidian-graph-tools" aria-label="Graph controls">
        <button type="button" aria-label="Zoom out" title="Zoom out" onClick={() => setZoom((value) => Math.max(0.7, value - 0.15))}><ZoomOut size={14} /></button>
        <button type="button" aria-label="Reset graph" title="Reset graph" onClick={() => { setZoom(1); setSelectedId(null); }}><RotateCcw size={14} /></button>
        <button type="button" aria-label="Zoom in" title="Zoom in" onClick={() => setZoom((value) => Math.min(1.6, value + 0.15))}><ZoomIn size={14} /></button>
      </div>
      <div className="obsidian-graph-legend"><span>Project</span><span>Chats</span><span>Folders</span><span>Files</span></div>
      {selected === null ? null : (
        <aside className="obsidian-graph-detail">
          <small>{selected.kind}</small><strong>{selected.label}</strong>
          <span>{related.length} visible relation{related.length === 1 ? "" : "s"}</span>
          {related.map((node) => <button key={node.id} type="button" onClick={() => setSelectedId(node.id)}>{node.label}</button>)}
        </aside>
      )}
    </div>
  );
}

function SyncStatus({ model }: { model: WorkspaceModel }) {
  const source = model.obsidianSources.at(0) ?? null;
  const [selection, setSelection] = useState<ObsidianVaultSelection | null>(null);
  const [readScope, setReadScope] = useState<"selected_directories" | "whole_vault">(
    "selected_directories",
  );
  const [allowedDirectories, setAllowedDirectories] = useState<string[]>([]);
  const [wholeVaultConfirmed, setWholeVaultConfirmed] = useState(false);
  const [managedDirectory, setManagedDirectory] = useState("Fairy");
  const chooseVault = async () => {
    const next = await model.selectObsidianVault();
    if (next === null) return;
    setSelection(next);
    setReadScope("selected_directories");
    setAllowedDirectories([]);
    setWholeVaultConfirmed(false);
  };
  const connect = async () => {
    if (selection === null) return;
    await model.connectObsidianVault(selection, {
      readScope,
      allowedDirectories: readScope === "selected_directories" ? allowedDirectories : [],
      wholeVaultConfirmed: readScope === "whole_vault" && wholeVaultConfirmed,
      managedDirectory: managedDirectory.trim(),
    });
    setSelection(null);
  };
  const canConnect = selection !== null && managedDirectory.trim().length > 0 && (
    readScope === "selected_directories"
      ? allowedDirectories.length > 0
      : wholeVaultConfirmed
  );
  return (
    <div className="obsidian-sync">
      <section><RefreshCw size={18} /><div><h3>Fairy project index</h3><p>Generation {model.workspaceGeneration || 0} is available for files, links, and graph projection.</p></div><strong>Ready</strong></section>
      <section>
        <Network size={18} />
        <div>
          <h3>Official Obsidian Vault</h3>
          <p>{source?.vault_display_path ?? model.obsidianHealth?.public_summary ?? "Checking the local Obsidian installation and official CLI."}</p>
        </div>
        {source === null ? (
          <button type="button" disabled={model.isActing} onClick={() => void chooseVault()}>
            {selection === null ? "Choose Vault" : "Change Vault"}
          </button>
        ) : (
          <button type="button" disabled={model.isActing} onClick={() => void model.syncObsidianSource(source)}>
            <RefreshCw size={13} /> Sync
          </button>
        )}
      </section>
      {source === null && selection !== null ? (
        <form
          className="obsidian-scope-form"
          onSubmit={(event) => {
            event.preventDefault();
            void connect();
          }}
        >
          <header>
            <strong>{selection.display_name}</strong>
            <span>The Vault path remains on this device.</span>
          </header>
          <fieldset>
            <legend>Read scope</legend>
            <label>
              <input
                type="radio"
                name="obsidian-read-scope"
                checked={readScope === "selected_directories"}
                onChange={() => {
                  setReadScope("selected_directories");
                  setWholeVaultConfirmed(false);
                }}
              />
              Selected folders
            </label>
            <label>
              <input
                type="radio"
                name="obsidian-read-scope"
                checked={readScope === "whole_vault"}
                onChange={() => {
                  setReadScope("whole_vault");
                  setAllowedDirectories([]);
                }}
              />
              Entire Vault
            </label>
          </fieldset>
          {readScope === "selected_directories" ? (
            <fieldset className="obsidian-directory-list">
              <legend>Folders Fairy may read</legend>
              {selection.available_directories.length === 0 ? (
                <p>This Vault has no eligible top-level folders.</p>
              ) : selection.available_directories.map((directory) => (
                <label key={directory}>
                  <input
                    type="checkbox"
                    checked={allowedDirectories.includes(directory)}
                    onChange={(event) => setAllowedDirectories((current) => (
                      event.target.checked
                        ? [...current, directory]
                        : current.filter((item) => item !== directory)
                    ))}
                  />
                  {directory}
                </label>
              ))}
            </fieldset>
          ) : (
            <label className="obsidian-whole-vault-confirmation">
              <input
                type="checkbox"
                checked={wholeVaultConfirmed}
                onChange={(event) => setWholeVaultConfirmed(event.target.checked)}
              />
              I authorize Fairy to read all eligible folders in this Vault.
            </label>
          )}
          <label className="obsidian-managed-directory">
            Fairy managed folder
            <input
              type="text"
              value={managedDirectory}
              maxLength={120}
              onChange={(event) => setManagedDirectory(event.target.value)}
            />
          </label>
          <footer>
            <button type="button" disabled={model.isActing} onClick={() => setSelection(null)}>
              Cancel
            </button>
            <button type="submit" disabled={model.isActing || !canConnect}>
              Confirm connection
            </button>
          </footer>
        </form>
      ) : null}
      {source !== null ? <p className="obsidian-source-meta">{source.item_count} items · {source.mode.replaceAll("_", " ")} · revision {source.revision}</p> : null}
      <p className="obsidian-sync-note">Ordinary Vault folders remain read-only. Fairy writes only inside the managed <code>Fairy/</code> directory after explicit authorization.</p>
    </div>
  );
}

function EmptyState({ icon, title, detail }: { icon: React.ReactNode; title: string; detail: string }) {
  return <div className="obsidian-empty">{icon}<h2>{title}</h2><p>{detail}</p></div>;
}

function buildProjectGraph(
  projectId: string | null,
  projectName: string,
  conversations: WorkspaceModel["projectConversations"],
  files: WorkspaceFile[],
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  if (projectId === null) return { nodes: [], edges: [] };
  const rootId = `project:${projectId}`;
  const nodes: GraphNode[] = [{ id: rootId, label: projectName, kind: "project" }];
  const edges: GraphEdge[] = [];
  for (const conversation of conversations) {
    const id = `conversation:${conversation.id}`;
    nodes.push({ id, label: conversation.title, kind: "conversation" });
    edges.push({ source: rootId, target: id });
  }
  const folders = new Set<string>();
  for (const file of files.slice(0, 300)) {
    const segments = file.path.split("/");
    const folder = segments.length > 1 ? segments.slice(0, -1).join("/") : "root";
    if (!folders.has(folder)) {
      folders.add(folder);
      nodes.push({ id: `folder:${folder}`, label: folder, kind: "folder" });
      edges.push({ source: rootId, target: `folder:${folder}` });
    }
    nodes.push({ id: `file:${file.path}`, label: basename(file.path), kind: "file" });
    edges.push({ source: `folder:${folder}`, target: `file:${file.path}` });
  }
  return { nodes, edges };
}

function drawGraph(
  canvas: HTMLCanvasElement,
  nodes: GraphNode[],
  edges: GraphEdge[],
  zoom: number,
  selectedId: string | null,
) {
  const bounds = canvas.getBoundingClientRect();
  const width = Math.max(320, Math.floor(bounds.width));
  const height = Math.max(280, Math.floor(bounds.height));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  const context = canvas.getContext("2d");
  if (context === null) return new Map<string, { x: number; y: number }>();
  context.scale(dpr, dpr);
  context.clearRect(0, 0, width, height);
  const center = { x: width / 2, y: height / 2 };
  const radius = Math.max(70, Math.min(width, height) * 0.32 * zoom);
  const positions = new Map<string, { x: number; y: number }>();
  nodes.forEach((node, index) => {
    if (index === 0) positions.set(node.id, center);
    else {
      const angle = ((index - 1) / Math.max(1, nodes.length - 1)) * Math.PI * 2 - Math.PI / 2;
      const band = node.kind === "file" ? radius : radius * 0.62;
      positions.set(node.id, { x: center.x + Math.cos(angle) * band, y: center.y + Math.sin(angle) * band });
    }
  });
  context.lineWidth = 1;
  for (const edge of edges) {
    const source = positions.get(edge.source);
    const target = positions.get(edge.target);
    if (!source || !target) continue;
    const highlighted = selectedId !== null && (edge.source === selectedId || edge.target === selectedId);
    context.strokeStyle = highlighted ? "rgba(102, 221, 210, 0.8)" : "rgba(132, 151, 143, 0.24)";
    context.lineWidth = highlighted ? 1.6 : 1;
    context.beginPath(); context.moveTo(source.x, source.y); context.lineTo(target.x, target.y); context.stroke();
  }
  const colors = { project: "#66ddd2", conversation: "#d7ad63", folder: "#8fa9ff", file: "#a6b0ab" };
  for (const node of nodes) {
    const position = positions.get(node.id);
    if (!position) continue;
    context.fillStyle = colors[node.kind];
    const selected = node.id === selectedId;
    context.beginPath(); context.arc(position.x, position.y, selected ? 8 : node.kind === "project" ? 7 : 4, 0, Math.PI * 2); context.fill();
    if (selected || node.kind !== "file" || nodes.length <= 45) {
      context.fillStyle = selected ? "#f4fffb" : "#aeb8b3";
      context.font = `${selected ? 600 : 400} 10px system-ui`;
      context.fillText(shortLabel(node.label), position.x + 8, position.y + 3, 120);
    }
  }
  return positions;
}

function basename(path: string) { return path.split("/").at(-1) ?? path; }
function formatBytes(value: number) { return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(1)} KB`; }
function shortLabel(value: string) { return value.length > 22 ? `${value.slice(0, 21)}...` : value; }
function graphKind(kind: string): GraphNode["kind"] {
  if (kind === "project" || kind === "conversation" || kind === "folder") return kind;
  return "file";
}

function mergeVaultGraph(
  graph: { nodes: GraphNode[]; edges: GraphEdge[] },
  projectId: string | null,
  items: WorkspaceModel["obsidianItems"],
) {
  if (projectId === null || items.length === 0) return graph;
  const nodes = [...graph.nodes];
  const edges = [...graph.edges];
  const rootId = `project:${projectId}`;
  const idsByTitle = new Map<string, string>();
  for (const item of items) {
    const id = `obsidian:${item.source_id}:${item.relative_path}`;
    idsByTitle.set(item.title.toLocaleLowerCase(), id);
    nodes.push({ id, label: item.title, kind: "file" });
    edges.push({ source: rootId, target: id });
  }
  for (const item of items) {
    const source = `obsidian:${item.source_id}:${item.relative_path}`;
    for (const link of item.links) {
      const target = idsByTitle.get(link.toLocaleLowerCase());
      if (target !== undefined) edges.push({ source, target });
    }
  }
  return { nodes, edges };
}
