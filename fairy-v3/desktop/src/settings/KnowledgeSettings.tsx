import { BookOpenText, Search, Trash2, X } from "lucide-react";
import { useState } from "react";

import type { DocumentContext, DocumentSearchHit, MemorySearchHit } from "../core/client";
import "./knowledgeSettings.css";

interface KnowledgeSettingsProps {
  available: boolean;
  disabled: boolean;
  onListDocuments(): Promise<DocumentContext[]>;
  onSearchDocuments(query: string): Promise<DocumentSearchHit[]>;
  onDeleteDocument(documentId: string): Promise<void>;
  onSearchMemory(query: string): Promise<MemorySearchHit[]>;
  onForgetMemory(kind: "observation" | "claim", id: string): Promise<void>;
}

export function KnowledgeSettings(props: KnowledgeSettingsProps) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"documents" | "memory">("documents");
  const [query, setQuery] = useState("");
  const [documents, setDocuments] = useState<DocumentContext[]>([]);
  const [documentHits, setDocumentHits] = useState<DocumentSearchHit[]>([]);
  const [memoryHits, setMemoryHits] = useState<MemorySearchHit[]>([]);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try { await operation(); } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Knowledge request failed");
    } finally { setBusy(false); }
  };
  const loadDocuments = () => act(async () => setDocuments(await props.onListDocuments()));
  const search = () => act(async () => {
    const value = query.trim();
    if (!value) return;
    if (tab === "documents") setDocumentHits(await props.onSearchDocuments(value));
    else setMemoryHits(await props.onSearchMemory(value));
  });

  return (
    <div className="execution-control knowledge-control">
      <button className={`icon-button ${open ? "active" : ""}`} type="button"
        aria-label="Knowledge" title="Knowledge" aria-expanded={open}
        disabled={!props.available} onClick={() => { setOpen((value) => !value); if (!open) void loadDocuments(); }}>
        <BookOpenText size={16} />
      </button>
      {open ? <aside className="execution-settings knowledge-settings" aria-label="Knowledge panel">
        <header><div><span className="eyebrow">Task scope</span><h2>Knowledge</h2></div>
          <button className="icon-button" type="button" aria-label="Close knowledge" title="Close knowledge" onClick={() => setOpen(false)}><X size={16} /></button>
        </header>
        <div className="extension-tabs" role="tablist" aria-label="Knowledge type">
          <button type="button" role="tab" aria-selected={tab === "documents"} className={tab === "documents" ? "active" : ""} onClick={() => setTab("documents")}>Documents</button>
          <button type="button" role="tab" aria-selected={tab === "memory"} className={tab === "memory" ? "active" : ""} onClick={() => setTab("memory")}>Memory</button>
        </div>
        <form className="knowledge-search" onSubmit={(event) => { event.preventDefault(); void search(); }}>
          <input aria-label="Search knowledge" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tab === "documents" ? "Search documents" : "Search memory"} />
          <button className="icon-button" type="submit" aria-label="Search" title="Search" disabled={props.disabled || busy || !query.trim()}><Search size={15} /></button>
        </form>
        {error ? <p className="mcp-error" role="alert">{error}</p> : null}
        {tab === "documents" ? <div className="knowledge-list">
          {(documentHits.length ? documentHits.map((hit) => hit.document) : documents.map((item) => item.document)).map((document) =>
            <section key={document.id} className="knowledge-row"><div><strong>{document.filename}</strong><span>{document.media_type} · {formatBytes(document.byte_length)}</span></div>
              {confirm === document.id ? <div className="delete-confirm"><button className="danger-command" type="button" onClick={() => void act(async () => { await props.onDeleteDocument(document.id); setConfirm(null); await loadDocuments(); })}>Delete</button><button className="compact-command" type="button" onClick={() => setConfirm(null)}>Cancel</button></div>
              : <button className="icon-button row-icon" type="button" aria-label={`Delete ${document.filename}`} title={`Delete ${document.filename}`} disabled={props.disabled || busy} onClick={() => setConfirm(document.id)}><Trash2 size={14} /></button>}
            </section>)}
        </div> : <div className="knowledge-list">
          {memoryHits.map((hit) => <section key={hit.document.id} className="knowledge-row memory-row"><div><strong>{hit.document.namespace ?? "Task memory"}</strong><p>{hit.document.normalized_text}</p></div>
            {confirm === hit.document.id ? <div className="delete-confirm"><button className="danger-command" type="button" onClick={() => void act(async () => { await props.onForgetMemory(hit.document.source_kind as "observation" | "claim", hit.document.source_id); setConfirm(null); setMemoryHits([]); })}>Forget</button><button className="compact-command" type="button" onClick={() => setConfirm(null)}>Cancel</button></div>
            : (["observation", "claim"] as const).includes(hit.document.source_kind as "observation" | "claim") ? <button className="icon-button row-icon" type="button" aria-label="Forget memory" title="Forget memory" disabled={props.disabled || busy} onClick={() => setConfirm(hit.document.id)}><Trash2 size={14} /></button> : null}
          </section>)}
        </div>}
      </aside> : null}
    </div>
  );
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  return `${(value / 1024).toFixed(1)} KiB`;
}
