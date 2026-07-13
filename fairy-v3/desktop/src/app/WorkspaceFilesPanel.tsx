import {
  Download,
  File,
  FileCode2,
  FileImage,
  FolderOpen,
  RefreshCw,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { WorkspaceFile, WorkspaceFileContent } from "../core/client";

interface WorkspaceFilesPanelProps {
  files: WorkspaceFile[];
  loading: boolean;
  onRead(path: string): Promise<WorkspaceFileContent>;
  onReveal(path: string): Promise<void>;
  onRefresh(): Promise<void>;
}

export function WorkspaceFilesPanel({
  files,
  loading,
  onRead,
  onReveal,
  onRefresh,
}: WorkspaceFilesPanelProps) {
  const [query, setQuery] = useState("");
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [content, setContent] = useState<WorkspaceFileContent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const visibleFiles = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return normalized.length === 0
      ? files
      : files.filter((file) => file.path.toLocaleLowerCase().includes(normalized));
  }, [files, query]);

  useEffect(() => {
    if (selectedPath !== null && !files.some((file) => file.path === selectedPath)) {
      setSelectedPath(null);
      setContent(null);
    }
  }, [files, selectedPath]);

  const selectFile = async (path: string) => {
    setSelectedPath(path);
    setContent(null);
    setError(null);
    try {
      setContent(await onRead(path));
    } catch (readError) {
      setError(readError instanceof Error ? readError.message : "File could not be read");
    }
  };

  return (
    <div className="workspace-files">
      <div className="workspace-files-toolbar">
        <label>
          <Search size={14} />
          <input
            type="search"
            value={query}
            placeholder="Search files"
            aria-label="Search workspace files"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button
          className="icon-button"
          type="button"
          aria-label="Refresh files"
          title="Refresh files"
          disabled={loading}
          onClick={() => void onRefresh()}
        >
          <RefreshCw size={15} className={loading ? "spin" : undefined} />
        </button>
      </div>

      <div className="workspace-files-body">
        <nav className="workspace-file-tree" aria-label="Workspace files">
          {visibleFiles.length === 0 ? (
            <div className="workspace-files-empty">
              <File size={20} />
              <span>{loading ? "Loading files" : "No generated files"}</span>
            </div>
          ) : (
            visibleFiles.map((file) => (
              <button
                key={file.path}
                type="button"
                className={selectedPath === file.path ? "selected" : undefined}
                style={{ paddingLeft: `${10 + pathDepth(file.path) * 12}px` }}
                title={file.path}
                onClick={() => void selectFile(file.path)}
              >
                <FileKindIcon file={file} />
                <span>{file.path.split("/").at(-1)}</span>
              </button>
            ))
          )}
        </nav>

        <section className="workspace-file-viewer" aria-label="File viewer">
          {error !== null ? <div className="workspace-file-error" role="alert">{error}</div> : null}
          {content === null ? (
            <div className="workspace-file-placeholder">
              <FileCode2 size={22} />
              <span>Select a file</span>
            </div>
          ) : (
            <>
              <header>
                <div>
                  <strong>{content.file.path}</strong>
                  <span>{formatBytes(content.file.byte_length)}</span>
                </div>
                <div>
                  <button
                    className="icon-button"
                    type="button"
                    aria-label="Show in File Explorer"
                    title="Show in File Explorer"
                    onClick={() => void onReveal(content.file.path)}
                  >
                    <FolderOpen size={15} />
                  </button>
                  <button
                    className="icon-button"
                    type="button"
                    aria-label="Download file"
                    title="Download file"
                    onClick={() => downloadContent(content)}
                  >
                    <Download size={15} />
                  </button>
                </div>
              </header>
              <FileContent content={content} />
            </>
          )}
        </section>
      </div>
    </div>
  );
}

function FileKindIcon({ file }: { file: WorkspaceFile }) {
  return file.kind === "binary" && file.path.match(/\.(png|jpe?g|gif|webp)$/i) ? (
    <FileImage size={14} />
  ) : file.kind === "source" || file.kind === "manifest" || file.kind === "config" ? (
    <FileCode2 size={14} />
  ) : (
    <File size={14} />
  );
}

function FileContent({ content }: { content: WorkspaceFileContent }) {
  if (content.text != null) {
    return <pre className="workspace-file-text"><code>{content.text}</code></pre>;
  }
  if (content.content_base64 != null && content.media_type.startsWith("image/")) {
    return (
      <div className="workspace-file-image">
        <img
          src={`data:${content.media_type};base64,${content.content_base64}`}
          alt={content.file.path.split("/").at(-1) ?? content.file.path}
        />
      </div>
    );
  }
  return <div className="workspace-file-placeholder"><File size={22} /><span>Binary preview unavailable</span></div>;
}

function downloadContent(content: WorkspaceFileContent): void {
  const bytes =
    content.text != null
      ? new TextEncoder().encode(content.text)
      : Uint8Array.from(atob(content.content_base64 ?? ""), (value) => value.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes.buffer as ArrayBuffer], { type: content.media_type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = content.file.path.split("/").at(-1) ?? "workspace-file";
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function pathDepth(path: string): number {
  return Math.min(path.split("/").length - 1, 8);
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
