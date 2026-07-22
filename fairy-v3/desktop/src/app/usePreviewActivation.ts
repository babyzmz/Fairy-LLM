import { useCallback, useEffect, useRef, useState } from "react";

import type { PreviewActivation, Task, Workspace } from "../core/client";
import type { WorkspaceClient } from "./workspaceTypes";

const READY_HEARTBEAT_MS = 120_000;
const STARTING_RETRY_MS = 2_000;
const WAITING_RETRY_MS = 15_000;

const pendingActivations = new Map<string, Promise<PreviewActivation>>();

interface PreviewActivationInput {
  client: WorkspaceClient["previews"];
  enabled: boolean;
  task: Task | null;
  workspace: Workspace | null;
  onActivated?(activation: PreviewActivation): void;
}

interface PreviewActivationState {
  activation: PreviewActivation | null;
  loading: boolean;
  error: string | null;
}

export function usePreviewActivation({
  client,
  enabled,
  task,
  workspace,
  onActivated,
}: PreviewActivationInput): PreviewActivationState {
  const activatePreview = client.activate;
  const identity = activationIdentity(enabled, task, workspace);
  const taskId = task?.id ?? null;
  const workspaceId = task?.workspace_id ?? null;
  const versionId = task?.target_version_id ?? null;
  const workspaceRevision = workspace?.revision ?? null;
  const [stateIdentity, setStateIdentity] = useState<string | null>(null);
  const [activation, setActivation] = useState<PreviewActivation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generationRef = useRef(0);
  const activationRef = useRef<PreviewActivation | null>(null);
  const inFlightRef = useRef(false);
  const onActivatedRef = useRef(onActivated);
  onActivatedRef.current = onActivated;

  const activate = useCallback(
    async (showLoading: boolean) => {
      if (
        identity === null ||
        taskId === null ||
        workspaceId === null ||
        versionId === null ||
        workspaceRevision === null ||
        inFlightRef.current
      ) return;
      const generation = generationRef.current;
      inFlightRef.current = true;
      if (showLoading) setLoading(true);
      setError(null);
      try {
        const request = {
          task_id: taskId,
          workspace_id: workspaceId,
          version_id: versionId,
          expected_workspace_revision: workspaceRevision,
          idempotency_key: `desktop:preview-activate:${taskId}:${versionId}`,
        };
        const result = await sharedActivation(identity, () => activatePreview(request));
        if (generation !== generationRef.current) return;
        activationRef.current = result;
        setActivation(result);
        onActivatedRef.current?.(result);
      } catch (reason) {
        if (generation !== generationRef.current) return;
        setError(previewActivationError(reason));
      } finally {
        if (generation === generationRef.current) {
          inFlightRef.current = false;
          setLoading(false);
        }
      }
    },
    [activatePreview, identity, taskId, versionId, workspaceId, workspaceRevision],
  );

  useEffect(() => {
    generationRef.current += 1;
    setStateIdentity(identity);
    activationRef.current = null;
    inFlightRef.current = false;
    setActivation(null);
    setError(null);
    setLoading(identity !== null);
    if (identity === null) return undefined;
    void activate(true);
    return () => {
      generationRef.current += 1;
    };
  }, [activate, identity]);

  useEffect(() => {
    if (identity === null) return;
    let timeout = 0;
    let cancelled = false;

    const schedule = () => {
      if (cancelled) return;
      const delay = retryDelay(activationRef.current);
      if (delay === null) return;
      timeout = window.setTimeout(async () => {
        await activate(false);
        schedule();
      }, delay);
    };
    schedule();
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
  }, [activate, activation, identity]);

  return stateIdentity === identity
    ? { activation, loading, error }
    : { activation: null, loading: identity !== null, error: null };
}

function activationIdentity(
  enabled: boolean,
  task: Task | null,
  workspace: Workspace | null,
): string | null {
  if (!enabled || task === null || workspace === null || task.target_version_id === null) return null;
  return [task.id, task.workspace_id, task.target_version_id, workspace.revision].join(":");
}

function retryDelay(activation: PreviewActivation | null): number | null {
  if (activation === null) return null;
  if (activation.outcome === "starting") return STARTING_RETRY_MS;
  if (activation.outcome === "waiting_for_slot") return WAITING_RETRY_MS;
  if (activation.outcome === "ready") return READY_HEARTBEAT_MS;
  return null;
}

function sharedActivation(
  identity: string,
  factory: () => Promise<PreviewActivation>,
): Promise<PreviewActivation> {
  const pending = pendingActivations.get(identity);
  if (pending !== undefined) return pending;
  const activation = factory().finally(() => pendingActivations.delete(identity));
  pendingActivations.set(identity, activation);
  return activation;
}

function previewActivationError(reason: unknown): string {
  if (reason instanceof Error && reason.message.trim() !== "") return reason.message;
  return "Preview could not be started automatically.";
}
