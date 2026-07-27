import { useCallback, useEffect, useRef, useState } from "react";

import type { PreviewActivation, Task, Workspace } from "../core/client";
import { coreErrorCode } from "./workspaceModelUtils";
import type { WorkspaceClient } from "./workspaceTypes";

const READY_HEARTBEAT_MS = 120_000;
const STARTING_RETRY_MS = 2_000;
const WAITING_RETRY_MS = 15_000;
const ERROR_RETRY_MS = 8_000;
const MAX_ERROR_ATTEMPTS = 3;

const TERMINAL_ERROR_CODES = new Set([
  "APPROVAL_REQUIRED",
  "CAPABILITY_NOT_AVAILABLE",
  "IDEMPOTENCY_CONFLICT",
  "INVALID_PARAMS",
  "INVALID_REQUEST",
  "METHOD_NOT_FOUND",
  "NOT_FOUND",
  "PATH_IDENTITY_CHANGED",
  "PATH_OUT_OF_SCOPE",
  "PERMISSION_DENIED",
  "SCOPE_MISMATCH",
  "VALIDATION_ERROR",
  "VERSION_CONFLICT",
]);

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
  retry(): void;
}

interface PreviewActivationFailure {
  code: string | null;
  message: string;
  terminal: boolean;
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
  const [failure, setFailure] = useState<PreviewActivationFailure | null>(null);
  const generationRef = useRef(0);
  const activationRef = useRef<PreviewActivation | null>(null);
  const inFlightRef = useRef(false);
  const errorAttemptsRef = useRef(0);
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
      setFailure(null);
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
        errorAttemptsRef.current = 0;
        setActivation(result);
        onActivatedRef.current?.(result);
      } catch (reason) {
        if (generation !== generationRef.current) return;
        errorAttemptsRef.current += 1;
        setFailure(previewActivationFailure(reason));
      } finally {
        if (generation === generationRef.current) {
          inFlightRef.current = false;
          setLoading(false);
        }
      }
    },
    [activatePreview, identity, taskId, versionId, workspaceId, workspaceRevision],
  );

  const retry = useCallback(() => {
    errorAttemptsRef.current = 0;
    setFailure(null);
    void activate(true);
  }, [activate]);

  useEffect(() => {
    generationRef.current += 1;
    setStateIdentity(identity);
    activationRef.current = null;
    inFlightRef.current = false;
    errorAttemptsRef.current = 0;
    setActivation(null);
    setFailure(null);
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
      const delay = retryDelay(
        activationRef.current,
        failure,
        errorAttemptsRef.current,
      );
      if (delay === null) return;
      timeout = window.setTimeout(() => void activate(false), delay);
    };
    schedule();
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
  }, [activate, activation, failure, identity]);

  return stateIdentity === identity
    ? { activation, loading, error: failure?.message ?? null, retry }
    : { activation: null, loading: identity !== null, error: null, retry };
}

function activationIdentity(
  enabled: boolean,
  task: Task | null,
  workspace: Workspace | null,
): string | null {
  if (!enabled || task === null || workspace === null || task.target_version_id === null) return null;
  return [task.id, task.workspace_id, task.target_version_id, workspace.revision].join(":");
}

function retryDelay(
  activation: PreviewActivation | null,
  failure: PreviewActivationFailure | null,
  errorAttempts: number,
): number | null {
  if (failure !== null) {
    if (failure.terminal || errorAttempts >= MAX_ERROR_ATTEMPTS) return null;
    return ERROR_RETRY_MS * 2 ** Math.max(0, errorAttempts - 1);
  }
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

function previewActivationFailure(reason: unknown): PreviewActivationFailure {
  const code = coreErrorCode(reason);
  return {
    code,
    message: reason instanceof Error && reason.message.trim() !== ""
      ? reason.message
      : "Preview could not be started automatically.",
    terminal: code !== null && TERMINAL_ERROR_CODES.has(code),
  };
}
