import {
  Download,
  File,
  FileCode2,
  FileImage,
  FolderOpen,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  Pencil,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type {
  FileReadSession,
  WorkspaceExport,
  WorkspaceFile,
  WorkspaceFileContent,
} from "../core/client";

interface WorkspaceFilesPanelProps {
  files: WorkspaceFile[];
  loading: boolean;
  onRead(path: string): Promise<WorkspaceFileContent>;
  onOpenStream(path: string): Promise<FileReadSession>;
  onReveal(path: string): Promise<void>;
  onRefresh(): Promise<void>;
  onUpload(files: Array<{ path: string; contentBase64: string }>): Promise<void>;
  onRename(file: WorkspaceFile, destinationPath: string): Promise<void>;
  onDelete(file: WorkspaceFile): Promise<void>;
  onExport(): Promise<WorkspaceExport>;
}

export function WorkspaceFilesPanel({
  files,
  loading,
  onRead,
  onOpenStream,
  onReveal,
  onRefresh,
  onUpload,
  onRename,
  onDelete,
  onExport,
}: WorkspaceFilesPanelProps) {
  const uploadRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [content, setContent] = useState<WorkspaceFileContent | null>(null);
  const [readSession, setReadSession] = useState<FileReadSession | null>(null);
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
      setReadSession(null);
    }
  }, [files, selectedPath]);

  const selectFile = async (path: string) => {
    setSelectedPath(path);
    setContent(null);
    setReadSession(null);
    setError(null);
    try {
      const nextContent = await onRead(path);
      setContent(nextContent);
      if (nextContent.stream_required) {
        setReadSession(await onOpenStream(path));
      }
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
        <button
          className="icon-button"
          type="button"
          aria-label="Upload files"
          title="Upload files"
          disabled={loading}
          onClick={() => uploadRef.current?.click()}
        >
          <Upload size={15} />
        </button>
        <button
          className="icon-button"
          type="button"
          aria-label="Export workspace"
          title="Export workspace"
          disabled={loading || files.length === 0}
          onClick={() => void exportWorkspace(onExport, setError)}
        >
          <Download size={15} />
        </button>
        <input
          ref={uploadRef}
          className="workspace-file-input"
          type="file"
          multiple
          tabIndex={-1}
          aria-hidden="true"
          onChange={(event) => {
            const selected = Array.from(event.target.files ?? []);
            event.target.value = "";
            void uploadFiles(selected, onUpload, setError);
          }}
        />
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
                    aria-label="Rename file"
                    title="Rename file"
                    onClick={() => void renameFile(content.file, onRename, setError)}
                  >
                    <Pencil size={15} />
                  </button>
                  <button
                    className="icon-button danger"
                    type="button"
                    aria-label="Delete file"
                    title="Delete file"
                    onClick={() => void deleteFile(content.file, onDelete, setError)}
                  >
                    <Trash2 size={15} />
                  </button>
                  <button
                    className="icon-button"
                    type="button"
                    aria-label="Download file"
                    title="Download file"
                    disabled={content.stream_required && readSession === null}
                    onClick={() => void downloadContent(content, readSession)}
                  >
                    <Download size={15} />
                  </button>
                </div>
              </header>
              <FileContent content={content} readSession={readSession} />
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

function FileContent({
  content,
  readSession,
}: {
  content: WorkspaceFileContent;
  readSession: FileReadSession | null;
}) {
  if (content.text != null) {
    return <pre className="workspace-file-text"><code>{content.text}</code></pre>;
  }
  if (readSession !== null && content.media_type.startsWith("image/")) {
    return (
      <div className="workspace-file-image">
        <img
          src={readSession.url}
          alt={content.file.path.split("/").at(-1) ?? content.file.path}
        />
      </div>
    );
  }
  return <div className="workspace-file-placeholder"><File size={22} /><span>Binary preview unavailable</span></div>;
}

async function downloadContent(
  content: WorkspaceFileContent,
  readSession: FileReadSession | null,
): Promise<void> {
  const blob = content.text != null
    ? new Blob([content.text], { type: content.media_type })
    : await fetchRequiredStream(readSession);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = content.file.path.split("/").at(-1) ?? "workspace-file";
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

async function fetchRequiredStream(readSession: FileReadSession | null): Promise<Blob> {
  if (readSession === null) throw new Error("File stream is unavailable");
  const response = await fetch(readSession.url, { cache: "no-store" });
  if (!response.ok) throw new Error("File stream could not be read");
  return response.blob();
}

function pathDepth(path: string): number {
  return Math.min(path.split("/").length - 1, 8);
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

async function uploadFiles(
  files: File[],
  onUpload: WorkspaceFilesPanelProps["onUpload"],
  setError: (message: string | null) => void,
): Promise<void> {
  if (files.length === 0) return;
  setError(null);
  try {
    await onUpload(
      await Promise.all(
        files.map(async (file) => ({
          path: file.name,
          contentBase64: bytesToBase64(new Uint8Array(await file.arrayBuffer())),
        })),
      ),
    );
  } catch (uploadError) {
    setError(uploadError instanceof Error ? uploadError.message : "Files could not be uploaded");
  }
}

async function renameFile(
  file: WorkspaceFile,
  onRename: WorkspaceFilesPanelProps["onRename"],
  setError: (message: string | null) => void,
): Promise<void> {
  const destination = window.prompt("New workspace path", file.path)?.trim();
  if (!destination || destination === file.path) return;
  setError(null);
  try {
    await onRename(file, destination);
  } catch (renameError) {
    setError(renameError instanceof Error ? renameError.message : "File could not be renamed");
  }
}

async function deleteFile(
  file: WorkspaceFile,
  onDelete: WorkspaceFilesPanelProps["onDelete"],
  setError: (message: string | null) => void,
): Promise<void> {
  if (!window.confirm(`Delete ${file.path} from the next Workspace Version?`)) return;
  setError(null);
  try {
    await onDelete(file);
  } catch (deleteError) {
    setError(deleteError instanceof Error ? deleteError.message : "File could not be deleted");
  }
}

async function exportWorkspace(
  onExport: WorkspaceFilesPanelProps["onExport"],
  setError: (message: string | null) => void,
): Promise<void> {
  setError(null);
  try {
    const archive = await onExport();
    downloadBytes(
      Uint8Array.from(atob(archive.content_base64), (value) => value.charCodeAt(0)),
      archive.media_type,
      archive.filename,
    );
  } catch (exportError) {
    setError(exportError instanceof Error ? exportError.message : "Workspace could not be exported");
  }
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

function downloadBytes(bytes: Uint8Array, mediaType: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([bytes.buffer as ArrayBuffer], { type: mediaType }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
