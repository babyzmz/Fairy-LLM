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
  MessageSquarePlus,
  Quote,
} from "lucide-react";
import { Fragment, lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";

import type {
  FileReadSession,
  WorkspaceExport,
  WorkspaceFile,
  WorkspaceFileContent,
  FilePresentationResult,
  FileSet,
  AnnotationDocument,
  AssetSet,
  SelectionReference,
} from "../core/client";

const PdfViewer = lazy(() => import("./viewers/PdfViewer"));
const DocumentViewer = lazy(() => import("./viewers/DocumentViewer"));
const DataViewer = lazy(() => import("./viewers/DataViewer"));
const ImageViewer = lazy(() => import("./viewers/ImageViewer"));
const MediaViewer = lazy(() => import("./viewers/MediaViewer"));
const SvgViewer = lazy(() => import("./viewers/SvgViewer"));
const ModelViewer = lazy(() => import("./viewers/ModelViewer"));

interface ModelSource {
  fileSet: FileSet;
  primaryUrl: string;
  resources: Record<string, string>;
}

type ViewerSelection =
  | { kind: "text_range"; start: number; end: number }
  | { kind: "scene_node"; nodePath: string; label: string };

interface WorkspaceFilesPanelProps {
  files: WorkspaceFile[];
  assetSets: AssetSet[];
  loading: boolean;
  onRead(path: string): Promise<WorkspaceFileContent>;
  onOpenStream(path: string): Promise<FileReadSession>;
  onPresent(path: string): Promise<FilePresentationResult>;
  onResolveFileSet(path: string): Promise<FileSet>;
  onListAnnotations(presentation: FilePresentationResult): Promise<{ document: AnnotationDocument | null }>;
  onUpdateAnnotations(
    presentation: FilePresentationResult,
    current: AnnotationDocument | null,
    annotations: Array<Record<string, unknown>>,
  ): Promise<AnnotationDocument>;
  onCreateTextSelection(presentation: FilePresentationResult, start: number, end: number): Promise<SelectionReference>;
  onCreateSceneSelection(presentation: FilePresentationResult, nodePath: string): Promise<SelectionReference>;
  onReveal(path: string): Promise<void>;
  onRefresh(): Promise<void>;
  onUpload(files: Array<{ path: string; contentBase64: string }>): Promise<void>;
  onRename(file: WorkspaceFile, destinationPath: string): Promise<void>;
  onDelete(file: WorkspaceFile): Promise<void>;
  onExport(): Promise<WorkspaceExport>;
}

export function WorkspaceFilesPanel({
  files,
  assetSets,
  loading,
  onRead,
  onOpenStream,
  onPresent,
  onResolveFileSet,
  onListAnnotations,
  onUpdateAnnotations,
  onCreateTextSelection,
  onCreateSceneSelection,
  onReveal,
  onRefresh,
  onUpload,
  onRename,
  onDelete,
  onExport,
}: WorkspaceFilesPanelProps) {
  const uploadRef = useRef<HTMLInputElement>(null);
  const selectionRequestRef = useRef(0);
  const [query, setQuery] = useState("");
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [content, setContent] = useState<WorkspaceFileContent | null>(null);
  const [readSession, setReadSession] = useState<FileReadSession | null>(null);
  const [modelSource, setModelSource] = useState<ModelSource | null>(null);
  const [captionSessions, setCaptionSessions] = useState<Array<{ label: string; language: string; src: string }>>([]);
  const [presentation, setPresentation] = useState<FilePresentationResult | null>(null);
  const [annotations, setAnnotations] = useState<AnnotationDocument | null>(null);
  const [selection, setSelection] = useState<ViewerSelection | null>(null);
  const [selectionSaved, setSelectionSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const visibleFiles = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return normalized.length === 0 ? files : files.filter((file) => file.path.toLocaleLowerCase().includes(normalized));
  }, [files, query]);

  useEffect(() => {
    if (selectedPath !== null && !files.some((file) => file.path === selectedPath)) {
      setSelectedPath(null);
      setContent(null);
      setReadSession(null);
      setModelSource(null);
      setCaptionSessions([]);
      setPresentation(null);
      setAnnotations(null);
      setSelection(null);
    }
  }, [files, selectedPath]);

  const selectFile = async (path: string) => {
    const request = ++selectionRequestRef.current;
    setSelectedPath(path);
    setContent(null);
    setReadSession(null);
    setModelSource(null);
    setCaptionSessions([]);
    setPresentation(null);
    setAnnotations(null);
    setSelection(null);
    setSelectionSaved(false);
    setError(null);
    try {
      const [nextContent, nextPresentation, nextFileSet] = await Promise.all([
        onRead(path),
        onPresent(path),
        onResolveFileSet(path),
      ]);
      if (request !== selectionRequestRef.current) return;
      setContent(nextContent);
      setPresentation(nextPresentation);
      if (nextPresentation.presentation !== null) {
        const nextAnnotations = await onListAnnotations(nextPresentation);
        if (request !== selectionRequestRef.current) return;
        setAnnotations(nextAnnotations.document);
      }
      if (nextContent.stream_required) {
        const captionPaths = mediaCaptionPaths(path, files);
        const nextSession = await onOpenStream(path);
        const nextCaptions = await Promise.all(
          captionPaths.map(async (caption) => {
            try {
              return { ...caption, session: await onOpenStream(caption.path) };
            } catch {
              return null;
            }
          }),
        );
        if (request !== selectionRequestRef.current) return;
        setReadSession(nextSession);
        setCaptionSessions(
          nextCaptions
            .filter((caption) => caption !== null)
            .map((caption) => ({
              label: caption.label,
              language: caption.language,
              src: caption.session.url,
            })),
        );
      }
      if (nextContent.media_type === "model/gltf-binary" || nextContent.media_type === "model/gltf+json") {
        if (nextFileSet.missing_dependencies.length > 0 || nextFileSet.blocked_dependencies.length > 0) {
          throw new Error("3D model dependencies are missing or outside the Workspace Version");
        }
        const sessions = await Promise.all(
          nextFileSet.members.map(async (member) => ({
            path: member.path,
            session: await onOpenStream(member.path),
          })),
        );
        if (request !== selectionRequestRef.current) return;
        const primary = sessions.find((item) => item.path === nextFileSet.primary_path);
        if (primary === undefined) throw new Error("3D model primary stream is unavailable");
        setModelSource({
          fileSet: nextFileSet,
          primaryUrl: primary.session.url,
          resources: Object.fromEntries(sessions.map((item) => [item.path, item.session.url])),
        });
      }
    } catch (readError) {
      if (request !== selectionRequestRef.current) return;
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
          {error !== null ? (
            <div className="workspace-file-error" role="alert">
              {error}
            </div>
          ) : null}
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
              <FileContent
                content={content}
                readSession={readSession}
                captionSessions={captionSessions}
                presentation={presentation}
                modelSource={modelSource}
                onTextSelection={(value) => {
                  setSelection(value === null ? null : { kind: "text_range", ...value });
                  setSelectionSaved(false);
                }}
                onSceneSelection={(nodePath, label) => {
                  setSelection({ kind: "scene_node", nodePath, label });
                  setSelectionSaved(false);
                }}
              />
            </>
          )}
        </section>

        <StudioProperties
          content={content}
          assetSet={
            content === null
              ? null
              : (assetSets.find((item) => item.variants.some((variant) => variant.path === content.file.path)) ?? null)
          }
          presentation={presentation}
          annotations={annotations}
          selection={selection}
          selectionSaved={selectionSaved}
          onAddAnnotation={async (body) => {
            if (presentation === null) return;
            const next = [
              ...(annotations?.annotations ?? []),
              {
                id: crypto.randomUUID(),
                body,
                created_at: new Date().toISOString(),
                ...(selection === null
                  ? {}
                  : {
                      locator:
                        selection.kind === "text_range"
                          ? { kind: selection.kind, start: selection.start, end: selection.end }
                          : { kind: selection.kind, node_path: selection.nodePath },
                    }),
              },
            ];
            try {
              setAnnotations(await onUpdateAnnotations(presentation, annotations, next));
            } catch (annotationError) {
              setError(annotationError instanceof Error ? annotationError.message : "Annotation could not be saved");
              throw annotationError;
            }
          }}
          onAttachSelection={async () => {
            if (presentation === null || selection === null) return;
            try {
              if (selection.kind === "text_range") {
                await onCreateTextSelection(presentation, selection.start, selection.end);
              } else {
                await onCreateSceneSelection(presentation, selection.nodePath);
              }
              setSelectionSaved(true);
            } catch (selectionError) {
              setError(selectionError instanceof Error ? selectionError.message : "Selection could not be saved");
            }
          }}
        />
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
  captionSessions,
  presentation,
  modelSource,
  onTextSelection,
  onSceneSelection,
}: {
  content: WorkspaceFileContent;
  readSession: FileReadSession | null;
  captionSessions: Array<{ label: string; language: string; src: string }>;
  presentation: FilePresentationResult | null;
  modelSource: ModelSource | null;
  onTextSelection(value: { start: number; end: number } | null): void;
  onSceneSelection(nodePath: string, label: string): void;
}) {
  if (presentation?.job.status === "waiting_for_pack") {
    return (
      <div className="workspace-file-placeholder">
        <File size={22} />
        <span>{presentation.job.public_summary ?? "Renderer Pack required"}</span>
      </div>
    );
  }
  if (presentation?.presentation?.renderer.startsWith("builtin.") && presentation.presentation.assets.length > 0) {
    return (
      <Suspense fallback={<div className="workspace-file-placeholder">Loading document</div>}>
        <DocumentViewer presentation={presentation} />
      </Suspense>
    );
  }
  if (modelSource !== null) {
    return (
      <Suspense fallback={<div className="workspace-file-placeholder">Loading 3D scene</div>}>
        <ModelViewer
          path={content.file.path}
          mediaType={content.media_type}
          primaryUrl={modelSource.primaryUrl}
          sourceText={content.text ?? null}
          resources={modelSource.resources}
          onSelectNode={onSceneSelection}
        />
      </Suspense>
    );
  }
  if (content.text != null) {
    if (content.media_type === "image/svg+xml" || /\.svg$/i.test(content.file.path)) {
      return (
        <Suspense fallback={<div className="workspace-file-placeholder">Sanitizing SVG</div>}>
          <SvgViewer source={content.text} title={content.file.path} />
        </Suspense>
      );
    }
    if (/\.(csv|tsv|json|jsonl|ndjson)$/i.test(content.file.path)) {
      return (
        <Suspense fallback={<div className="workspace-file-placeholder">Loading data</div>}>
          <DataViewer text={content.text} path={content.file.path} />
        </Suspense>
      );
    }
    return <TextViewer text={content.text} onSelection={onTextSelection} />;
  }
  if (readSession !== null && /^image\/(png|jpeg|gif|webp|avif)$/.test(content.media_type)) {
    return (
      <Suspense fallback={<div className="workspace-file-placeholder">Loading image</div>}>
        <ImageViewer src={readSession.url} title={content.file.path.split("/").at(-1) ?? content.file.path} />
      </Suspense>
    );
  }
  if (readSession !== null && (content.media_type.startsWith("audio/") || content.media_type.startsWith("video/"))) {
    return (
      <Suspense fallback={<div className="workspace-file-placeholder">Loading media</div>}>
        <MediaViewer
          src={readSession.url}
          mediaType={content.media_type}
          title={content.file.path}
          captions={captionSessions}
        />
      </Suspense>
    );
  }
  if (readSession !== null && content.media_type === "application/pdf") {
    return (
      <Suspense fallback={<div className="workspace-file-placeholder">Loading PDF viewer</div>}>
        <PdfViewer url={readSession.url} title={content.file.path} />
      </Suspense>
    );
  }
  return (
    <div className="workspace-file-placeholder">
      <File size={22} />
      <span>Binary preview unavailable</span>
    </div>
  );
}

function TextViewer({
  text,
  onSelection,
}: {
  text: string;
  onSelection(value: { start: number; end: number } | null): void;
}) {
  const codeRef = useRef<HTMLElement>(null);
  return (
    <pre
      className="workspace-file-text"
      onMouseUp={() => {
        const root = codeRef.current;
        const selected = window.getSelection();
        if (root === null || selected === null || selected.rangeCount === 0 || selected.isCollapsed) {
          onSelection(null);
          return;
        }
        const range = selected.getRangeAt(0);
        if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) {
          onSelection(null);
          return;
        }
        const prefix = document.createRange();
        prefix.selectNodeContents(root);
        prefix.setEnd(range.startContainer, range.startOffset);
        const start = prefix.toString().length;
        onSelection({ start, end: start + range.toString().length });
      }}
    >
      <code ref={codeRef}>{text}</code>
    </pre>
  );
}

function StudioProperties({
  content,
  assetSet,
  presentation,
  annotations,
  selection,
  selectionSaved,
  onAddAnnotation,
  onAttachSelection,
}: {
  content: WorkspaceFileContent | null;
  assetSet: AssetSet | null;
  presentation: FilePresentationResult | null;
  annotations: AnnotationDocument | null;
  selection: ViewerSelection | null;
  selectionSaved: boolean;
  onAddAnnotation(body: string): Promise<void>;
  onAttachSelection(): Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [savingNote, setSavingNote] = useState(false);
  if (content === null) return <aside className="studio-properties" aria-label="File properties" />;
  return (
    <aside className="studio-properties" aria-label="File properties">
      <section>
        <h3>Properties</h3>
        <dl>
          <dt>Type</dt>
          <dd>{content.media_type}</dd>
          <dt>Size</dt>
          <dd>{formatBytes(content.file.byte_length)}</dd>
          <dt>Fidelity</dt>
          <dd>{presentation?.presentation?.fidelity ?? "Pending"}</dd>
          <dt>Status</dt>
          <dd>{presentation?.job.status ?? "Probing"}</dd>
        </dl>
      </section>
      {assetSet !== null ? (
        <section>
          <h3>Generated asset</h3>
          <dl>
            <dt>Set</dt>
            <dd>{assetSet.title}</dd>
            <dt>Variants</dt>
            <dd>{assetSet.variants.length}</dd>
            {Object.entries(assetSet.provenance).map(([key, value]) => (
              <Fragment key={key}>
                <dt>{key}</dt>
                <dd>{displayMetadata(value)}</dd>
              </Fragment>
            ))}
          </dl>
        </section>
      ) : null}
      <section>
        <h3>Selection</h3>
        {selection?.kind === "scene_node" ? <p>{selection.label}</p> : null}
        <button type="button" disabled={selection === null || selectionSaved} onClick={() => void onAttachSelection()}>
          <Quote size={14} /> {selectionSaved ? "Selection saved" : "Save selection"}
        </button>
      </section>
      <section className="studio-annotations">
        <h3>Annotations</h3>
        <div className="studio-note-list">
          {(annotations?.annotations ?? []).map((item, index) => (
            <p key={String(item.id ?? index)}>{String(item.body ?? "Note")}</p>
          ))}
        </div>
        <textarea
          value={note}
          maxLength={2000}
          placeholder="Add a note"
          onChange={(event) => setNote(event.target.value)}
        />
        <button
          type="button"
          disabled={note.trim().length === 0 || savingNote}
          onClick={() => {
            const value = note.trim();
            setSavingNote(true);
            void onAddAnnotation(value)
              .then(() => setNote(""))
              .catch(() => undefined)
              .finally(() => setSavingNote(false));
          }}
        >
          <MessageSquarePlus size={14} /> Add note
        </button>
      </section>
    </aside>
  );
}

async function downloadContent(content: WorkspaceFileContent, readSession: FileReadSession | null): Promise<void> {
  const blob =
    content.text != null
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

function mediaCaptionPaths(
  path: string,
  files: WorkspaceFile[],
): Array<{ path: string; label: string; language: string }> {
  if (!/\.(mp4|webm|mov|m4v|mp3|m4a|wav|ogg|flac)$/i.test(path)) return [];
  const base = path.replace(/\.[^./]+$/, "");
  return files
    .filter((file) => file.path === `${base}.vtt` || (file.path.startsWith(`${base}.`) && file.path.endsWith(".vtt")))
    .slice(0, 16)
    .map((file) => {
      const suffix = file.path.slice(base.length + 1, -4);
      const language = /^[a-z]{2,3}(?:-[A-Z]{2})?$/.test(suffix) ? suffix : "und";
      return { path: file.path, label: language === "und" ? "Captions" : language, language };
    });
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function displayMetadata(value: unknown): string {
  if (value === null) return "None";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
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
