import { AudioLines, Ban, Film, Image as ImageIcon, LoaderCircle, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

import type { FileReadSession, MediaGenerationJob } from "../core/client";
import "./workspace-outputs.css";

interface WorkspaceOutputsPanelProps {
  scopeKey: string;
  jobs: MediaGenerationJob[];
  loading: boolean;
  onOpenStream(path: string): Promise<FileReadSession>;
  onCancel(job: MediaGenerationJob): Promise<void>;
}

const terminalStatuses = new Set(["completed", "failed", "cancelled", "interrupted"]);

export function WorkspaceOutputsPanel({
  scopeKey,
  jobs,
  loading,
  onOpenStream,
  onCancel,
}: WorkspaceOutputsPanelProps) {
  if (loading && jobs.length === 0) {
    return <OutputEmpty icon={<LoaderCircle className="spin" />} title="Loading outputs" />;
  }
  if (jobs.length === 0) {
    return <OutputEmpty icon={<ImageIcon />} title="Generated media will appear here" />;
  }
  return (
    <section className="workspace-outputs" aria-label="Generated outputs">
      <header>
        <div>
          <strong>Outputs</strong>
          <span>Durable generation jobs and verified Workspace files</span>
        </div>
        {loading ? <RefreshCw className="spin" size={15} aria-label="Refreshing outputs" /> : null}
      </header>
      <div className="workspace-output-grid">
        {[...jobs].reverse().map((job) => (
          <OutputCard
            key={`${scopeKey}:${job.id}:${job.revision}`}
            job={job}
            onOpenStream={onOpenStream}
            onCancel={onCancel}
          />
        ))}
      </div>
    </section>
  );
}

function OutputCard({
  job,
  onOpenStream,
  onCancel,
}: {
  job: MediaGenerationJob;
  onOpenStream(path: string): Promise<FileReadSession>;
  onCancel(job: MediaGenerationJob): Promise<void>;
}) {
  const [session, setSession] = useState<FileReadSession | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const active = !terminalStatuses.has(job.status);

  useEffect(() => {
    let disposed = false;
    setSession(null);
    setStreamError(null);
    if (job.status !== "completed") return () => undefined;
    void onOpenStream(job.output_path)
      .then((value) => {
        if (!disposed) setSession(value);
      })
      .catch((error: unknown) => {
        if (!disposed) setStreamError(error instanceof Error ? error.message : "Output is unavailable");
      });
    return () => {
      disposed = true;
    };
  }, [job.output_path, job.status, onOpenStream]);

  return (
    <article className={`workspace-output-card ${job.kind} ${job.status}`}>
      <div className="workspace-output-stage">
        {job.status === "completed" && session !== null ? (
          <CompletedMedia job={job} session={session} />
        ) : job.kind === "image" ? (
          <DiffusionPreview progress={job.progress} active={active} />
        ) : job.kind === "video" ? (
          <VideoProgress progress={job.progress} active={active} />
        ) : (
          <AudioProgress progress={job.progress} active={active} />
        )}
      </div>
      <div className="workspace-output-meta">
        <div>
          {job.kind === "image" ? <ImageIcon size={15} /> : job.kind === "video" ? <Film size={15} /> : <AudioLines size={15} />}
          <strong>{fileName(job.output_path)}</strong>
        </div>
        <span className={`output-status ${job.status}`}>{statusLabel(job)}</span>
      </div>
      {active ? (
        <div className="workspace-output-progress" aria-label={`${job.progress}% complete`}>
          <span style={{ width: `${job.progress}%` }} />
        </div>
      ) : null}
      {streamError !== null ? <p role="alert">{streamError}</p> : null}
      {job.error_code !== null ? <p role="alert">Generation failed: {job.error_code}</p> : null}
      {job.kind === "video" && active ? (
        <button type="button" className="output-cancel" onClick={() => void onCancel(job)}>
          <Ban size={14} /> Cancel
        </button>
      ) : null}
    </article>
  );
}

function CompletedMedia({ job, session }: { job: MediaGenerationJob; session: FileReadSession }) {
  if (job.kind === "image") return <img src={session.url} alt={fileName(job.output_path)} />;
  if (job.kind === "video") return <video src={session.url} controls preload="metadata" aria-label={fileName(job.output_path)} />;
  return <audio src={session.url} controls preload="metadata" aria-label={fileName(job.output_path)} />;
}

function DiffusionPreview({ progress, active }: { progress: number; active: boolean }) {
  const dots = useMemo(
    () =>
      Array.from({ length: 80 }, (_, index) => ({
        x: (index * 47 + 13) % 100,
        y: (index * 71 + 29) % 100,
        size: 1 + ((index * 17) % 5),
        delay: (index % 11) * -0.12,
      })),
    [],
  );
  return (
    <div className={`diffusion-preview ${active ? "active" : ""}`} style={{ "--resolve": progress / 100 } as CSSProperties}>
      {dots.map((dot, index) => (
        <i
          key={index}
          style={{ left: `${dot.x}%`, top: `${dot.y}%`, width: dot.size, height: dot.size, animationDelay: `${dot.delay}s` }}
        />
      ))}
      <span>{progress}%</span>
    </div>
  );
}

function VideoProgress({ progress, active }: { progress: number; active: boolean }) {
  return (
    <div className={`video-generation ${active ? "active" : ""}`}>
      <Film size={30} />
      <div><i style={{ width: `${Math.max(progress, 4)}%` }} /></div>
      <span>{progress < 10 ? "Queued" : progress < 100 ? "Rendering frames" : "Finalizing"}</span>
    </div>
  );
}

function AudioProgress({ progress, active }: { progress: number; active: boolean }) {
  return (
    <div className={`audio-generation ${active ? "active" : ""}`}>
      {Array.from({ length: 20 }, (_, index) => <i key={index} style={{ animationDelay: `${index * -45}ms` }} />)}
      <span>{progress}%</span>
    </div>
  );
}

function OutputEmpty({ icon, title }: { icon: ReactNode; title: string }) {
  return <div className="workspace-output-empty">{icon}<span>{title}</span></div>;
}

function statusLabel(job: MediaGenerationJob): string {
  if (job.status === "completed") return "Ready";
  if (job.status === "failed") return "Failed";
  if (job.status === "cancelled") return "Cancelled";
  if (job.status === "interrupted") return "Interrupted";
  if (job.status === "pending") return "Queued";
  return `${job.progress}%`;
}

function fileName(path: string): string {
  return path.split("/").at(-1) ?? path;
}
