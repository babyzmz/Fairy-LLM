import {
  BookOpenText,
  CircleDot,
  FolderTree,
  Link2,
  Network,
  RefreshCw,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { WorkspaceFile } from "../core/client";
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

const views: Array<{ id: ObsidianView; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "notes", label: "Notes" },
  { id: "links", label: "Links" },
  { id: "graph", label: "Graph" },
  { id: "sync", label: "Sync" },
];

export function ObsidianPanel({ model, onOpenFiles }: { model: WorkspaceModel; onOpenFiles(): void }) {
  const [view, setView] = useState<ObsidianView>("overview");
  const project = model.selectedProject;
  const conversations = useMemo(
    () => model.projectConversations.filter((item) => item.project_id === project?.id),
    [model.projectConversations, project?.id],
  );
  const graph = useMemo(
    () => buildProjectGraph(project?.id ?? null, project?.name ?? "Project", conversations, model.workspaceFiles),
    [conversations, model.workspaceFiles, project?.id, project?.name],
  );
  const noteFiles = useMemo(
    () => model.workspaceFiles.filter((file) => /(^|\/)(notes?|docs?)\//i.test(file.path) || /\.mdx?$/i.test(file.path)),
    [model.workspaceFiles],
  );

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
            fileCount={model.workspaceFiles.length}
            noteCount={noteFiles.length}
            conversationCount={conversations.length}
            linkCount={graph.edges.length}
            onOpenFiles={onOpenFiles}
          />
        ) : view === "notes" ? (
          <Notes files={noteFiles} onOpenFiles={onOpenFiles} />
        ) : view === "links" ? (
          <Links graph={graph} />
        ) : view === "graph" ? (
          <ProjectGraph nodes={graph.nodes} edges={graph.edges} />
        ) : (
          <SyncStatus workspaceGeneration={model.workspaceGeneration} />
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

function Notes({ files, onOpenFiles }: { files: WorkspaceFile[]; onOpenFiles(): void }) {
  if (files.length === 0) {
    return <EmptyState icon={<BookOpenText size={22} />} title="No managed notes" detail="Markdown notes created for this project will appear here." />;
  }
  return (
    <div className="obsidian-list">
      {files.map((file) => (
        <button key={file.path} type="button" onClick={onOpenFiles}>
          <BookOpenText size={15} />
          <span><strong>{basename(file.path)}</strong><small>{file.path}</small></span>
          <small>{formatBytes(file.byte_length)}</small>
        </button>
      ))}
    </div>
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
  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    if (typeof ResizeObserver === "undefined") return;
    const draw = () => drawGraph(canvas, nodes, edges);
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [edges, nodes]);
  return (
    <div className="obsidian-graph">
      <canvas ref={canvasRef} aria-label={`Project graph with ${nodes.length} nodes and ${edges.length} links`} />
      <div className="obsidian-graph-legend"><span>Project</span><span>Chats</span><span>Folders</span><span>Files</span></div>
    </div>
  );
}

function SyncStatus({ workspaceGeneration }: { workspaceGeneration: number }) {
  return (
    <div className="obsidian-sync">
      <section><RefreshCw size={18} /><div><h3>Fairy project index</h3><p>Generation {workspaceGeneration || 0} is available for files, links, and graph projection.</p></div><strong>Ready</strong></section>
      <section><Network size={18} /><div><h3>Official Obsidian Vault</h3><p>Not connected. Vault authorization, managed folder selection, and test sync will be configured here.</p></div><strong>Not connected</strong></section>
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

function drawGraph(canvas: HTMLCanvasElement, nodes: GraphNode[], edges: GraphEdge[]) {
  const bounds = canvas.getBoundingClientRect();
  const width = Math.max(320, Math.floor(bounds.width));
  const height = Math.max(280, Math.floor(bounds.height));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  const context = canvas.getContext("2d");
  if (context === null) return;
  context.scale(dpr, dpr);
  context.clearRect(0, 0, width, height);
  const center = { x: width / 2, y: height / 2 };
  const radius = Math.max(70, Math.min(width, height) * 0.37);
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
  context.strokeStyle = "rgba(132, 151, 143, 0.28)";
  for (const edge of edges) {
    const source = positions.get(edge.source);
    const target = positions.get(edge.target);
    if (!source || !target) continue;
    context.beginPath(); context.moveTo(source.x, source.y); context.lineTo(target.x, target.y); context.stroke();
  }
  const colors = { project: "#66ddd2", conversation: "#d7ad63", folder: "#8fa9ff", file: "#a6b0ab" };
  for (const node of nodes) {
    const position = positions.get(node.id);
    if (!position) continue;
    context.fillStyle = colors[node.kind];
    context.beginPath(); context.arc(position.x, position.y, node.kind === "project" ? 7 : 4, 0, Math.PI * 2); context.fill();
  }
}

function basename(path: string) { return path.split("/").at(-1) ?? path; }
function formatBytes(value: number) { return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(1)} KB`; }
