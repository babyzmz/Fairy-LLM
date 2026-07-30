import { listen } from "@tauri-apps/api/event";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Cpu,
  Download,
  Gauge,
  HardDrive,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Trash2,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { ActionDialog } from "../ui/ActionDialog";
import type {
  LocalReadinessReport,
  OmniModelInstallPhase,
  OmniModelProgressEvent,
  RealtimeActivityProfile,
  SettingsClient,
} from "./client";

export const OMNI_MODEL_PROGRESS_EVENT = "fairy-omni-model-progress";

interface RealtimeReadinessCardProps {
  client: SettingsClient;
  profile: RealtimeActivityProfile;
  disabled?: boolean;
  onReadinessChange?(ready: boolean): void;
}

const activePhases = new Set<OmniModelInstallPhase>([
  "checking_space",
  "downloading",
  "cancelling",
  "verifying",
  "layout_check",
  "runtime_self_test",
]);

export function RealtimeReadinessCard({
  client,
  profile,
  disabled = false,
  onReadinessChange,
}: RealtimeReadinessCardProps) {
  const queryClient = useQueryClient();
  const queryKey = useMemo(
    () => ["settings", "category", "voice", "local-readiness", profile] as const,
    [profile],
  );
  const lastEventSequence = useRef(0);
  const [action, setAction] = useState<"refresh" | "install" | "cancel" | "verify" | "remove" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const readiness = useQuery({
    queryKey,
    queryFn: () => client.realtimeLocal.readiness(profile),
    staleTime: 15_000,
    refetchInterval: (query) =>
      query.state.data && activePhases.has(query.state.data.model.phase) ? 750 : false,
  });

  useEffect(() => {
    onReadinessChange?.(readiness.data?.capability.local_beta_eligible === true);
  }, [onReadinessChange, readiness.data?.capability.local_beta_eligible]);

  useEffect(() => {
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void listen<OmniModelProgressEvent>(OMNI_MODEL_PROGRESS_EVENT, ({ payload }) => {
      if (
        payload.schema_version !== 1 ||
        payload.event_sequence <= lastEventSequence.current
      ) {
        return;
      }
      lastEventSequence.current = payload.event_sequence;
      queryClient.setQueryData<LocalReadinessReport>(queryKey, (current) => {
        if (current === undefined) return current;
        return {
          ...current,
          model: {
            ...current.model,
            phase: payload.phase,
            current_file: payload.current_file,
            received_bytes: payload.received_bytes,
            total_bytes: payload.total_bytes,
            error_code: payload.error_code,
          },
        };
      });
      if (!activePhases.has(payload.phase)) {
        void readiness.refetch();
      }
    }).then((stop) => {
      if (disposed) stop();
      else unlisten = stop;
    }).catch(() => {
      // Browser tests and non-Tauri previews use active-operation polling only.
    });
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, [queryClient, queryKey, readiness.refetch]);

  const runAction = useCallback(async (
    next: Exclude<typeof action, null>,
    operation: () => Promise<unknown>,
  ) => {
    setAction(next);
    setActionError(null);
    try {
      await operation();
      await readiness.refetch();
    } catch (error) {
      setActionError(publicActionError(error));
    } finally {
      setAction(null);
    }
  }, [readiness.refetch]);

  const report = readiness.data;
  const view = readinessView(report, readiness.isError);
  const model = report?.model;
  const progress = model === undefined || model.total_bytes <= 0
    ? 0
    : Math.min(100, Math.max(0, model.received_bytes / model.total_bytes * 100));
  const canInstall = report !== undefined
    && report.capability.static_eligible
    && report.hardware.disk_available_bytes !== null
    && report.hardware.disk_available_bytes >= report.model_install_required_bytes
    && ["not_installed", "partial", "corrupt"].includes(report.model.phase);
  const canVerify = report?.model_shallow_present === true
    && !activePhases.has(report.model.phase);
  const canRemove = report?.model_shallow_present === true
    && !activePhases.has(report.model.phase);

  return (
    <section
      className="realtime-readiness-card"
      aria-labelledby="realtime-local-title"
      data-status={view.status}
    >
      <header>
        <span className="realtime-readiness-icon" aria-hidden="true">
          {view.status === "ready"
            ? <CheckCircle2 size={17} />
            : view.status === "unavailable"
              ? <XCircle size={17} />
              : <Cpu size={17} />}
        </span>
        <span>
          <strong id="realtime-local-title">Local MiniCPM-o 4.5 Beta</strong>
          <small>Runs locally only after every hardware, model, runtime, and session gate passes.</small>
        </span>
        <span
          className="realtime-readiness-badge"
          data-tone={view.tone}
          role="status"
          aria-label="Local readiness status"
        >
          {readiness.isPending ? "Checking…" : view.label}
        </span>
        <button
          className="realtime-readiness-refresh"
          type="button"
          aria-label="Refresh local readiness"
          disabled={disabled || action !== null}
          onClick={() => void runAction("refresh", async () => {
            const next = await client.realtimeLocal.readiness(profile, true);
            queryClient.setQueryData(queryKey, next);
          })}
        >
          <RefreshCw size={14} />
        </button>
      </header>

      {report === undefined ? (
        <div className="realtime-readiness-placeholder" role={readiness.isError ? "alert" : "status"}>
          {readiness.isError
            ? "Local readiness is unavailable. Cloud Live settings remain available."
            : "Checking device-local readiness without starting a model or worker…"}
        </div>
      ) : (
        <>
          <dl className="realtime-readiness-grid">
            <ReadinessRow
              icon={<Cpu size={14} />}
              label="Hardware"
              value={hardwareLabel(report)}
              tone={report.capability.static_eligible ? "success" : "error"}
            />
            <ReadinessRow
              icon={<ShieldAlert size={14} />}
              label="CUDA"
              value={cudaLabel(report)}
              tone={report.hardware.cuda.available && report.hardware.cuda.adapter_luid_matches ? "success" : "error"}
            />
            <ReadinessRow
              icon={<HardDrive size={14} />}
              label="Model"
              value={modelLabel(report)}
              tone={modelTone(report.model.phase)}
            />
            <ReadinessRow
              icon={<RotateCcw size={14} />}
              label="Runtime"
              value={runtimeLabel(report)}
              tone={report.runtime === "passed" ? "success" : report.runtime === "missing" ? "neutral" : "error"}
            />
            <ReadinessRow
              icon={<Gauge size={14} />}
              label="Current budget"
              value={budgetLabel(report)}
              tone={report.capability.reason === "insufficient_free_vram" ? "warning" : report.capability.local_beta_eligible ? "success" : "neutral"}
            />
          </dl>

          {model !== undefined && activePhases.has(model.phase) ? (
            <div className="realtime-readiness-progress">
              <span>
                <strong>{phaseLabel(model.phase)}</strong>
                <small>{model.current_file ?? "Preparing verified model layout"}</small>
              </span>
              <span>{progress.toFixed(0)}%</span>
              <div
                role="progressbar"
                aria-label="Local model operation progress"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(progress)}
              >
                <span style={{ transform: `scaleX(${progress / 100})` }} />
              </div>
            </div>
          ) : null}

          {report.capability.warnings.includes("system_memory_below_24_gib") ? (
            <p className="realtime-readiness-warning">
              System memory is below 24 GiB. Local Beta may compete with games and creative tools.
            </p>
          ) : null}

          <footer>
            <span>{view.detail}</span>
            <div>
              {model !== undefined && activePhases.has(model.phase) ? (
                <button
                  className="secondary-command"
                  type="button"
                  disabled={disabled || action !== null}
                  onClick={() => void runAction("cancel", () => client.realtimeLocal.cancel())}
                >
                  Cancel
                </button>
              ) : canInstall ? (
                <button
                  className="primary-command"
                  type="button"
                  disabled={disabled || action !== null}
                  onClick={() => void runAction("install", () => client.realtimeLocal.install(profile))}
                >
                  <Download size={14} />
                  {report.model.phase === "partial" ? "Resume" : report.model.phase === "corrupt" ? "Repair" : "Install model"}
                </button>
              ) : null}
              {canVerify ? (
                <button
                  className="secondary-command"
                  type="button"
                  disabled={disabled || action !== null}
                  onClick={() => void runAction("verify", () => client.realtimeLocal.verify())}
                >
                  <RefreshCw size={14} /> Verify
                </button>
              ) : null}
              {canRemove ? (
                <button
                  className="realtime-readiness-remove"
                  type="button"
                  aria-label="Remove local MiniCPM-o model"
                  disabled={disabled || action !== null}
                  onClick={() => setConfirmRemove(true)}
                >
                  <Trash2 size={14} />
                </button>
              ) : null}
            </div>
          </footer>
        </>
      )}

      {actionError !== null ? <p className="realtime-readiness-error" role="alert">{actionError}</p> : null}
      <ActionDialog
        open={confirmRemove}
        busy={action === "remove"}
        destructive
        title="Remove local MiniCPM-o model"
        description="This removes the verified device-local model and resumable staging data. Cloud Live settings and saved chats are unchanged."
        confirmLabel="Remove model"
        onCancel={() => setConfirmRemove(false)}
        onConfirm={async () => {
          await runAction("remove", () => client.realtimeLocal.remove());
          setConfirmRemove(false);
        }}
      />
    </section>
  );
}

function ReadinessRow({
  icon,
  label,
  value,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  tone: "neutral" | "success" | "warning" | "error";
}) {
  return (
    <div>
      <dt>{icon}<span>{label}</span></dt>
      <dd data-tone={tone}>{value}</dd>
    </div>
  );
}

function readinessView(report: LocalReadinessReport | undefined, failed: boolean) {
  if (report === undefined) {
    return {
      status: failed ? "unavailable" : "unknown",
      tone: failed ? "error" : "neutral",
      label: failed ? "Unavailable" : "Checking",
      detail: "No readiness claim is made until every required fact is available.",
    } as const;
  }
  if (!report.capability.static_eligible) {
    return {
      status: "unavailable",
      tone: "error",
      label: "Unavailable",
      detail: hardwareFailureDetail(report),
    } as const;
  }
  if (activePhases.has(report.model.phase)) {
    return {
      status: "working",
      tone: "neutral",
      label: phaseLabel(report.model.phase),
      detail: "The operation is resumable and can be cancelled without promoting partial files.",
    } as const;
  }
  if (report.model.phase === "corrupt") {
    return {
      status: "repair",
      tone: "error",
      label: "Repair required",
      detail: "The staged model failed size, digest, or layout verification.",
    } as const;
  }
  if (!report.model_shallow_present || ["not_installed", "partial"].includes(report.model.phase)) {
    return {
      status: "install",
      tone: "neutral",
      label: report.model.phase === "partial" ? "Resume install" : "Install model",
      detail: `${formatBytes(report.model.total_bytes)} reviewed model payload; download starts only when you choose Install.`,
    } as const;
  }
  if (report.runtime === "missing") {
    return {
      status: "runtime-missing",
      tone: "neutral",
      label: "Runtime required",
      detail: "The verified model is present, but the bundled Omni runtime is unavailable. Repair or reinstall Fairy before using Local Realtime.",
    } as const;
  }
  if (report.runtime === "failed" || report.capability.reason === "self_test_failed") {
    return {
      status: "self-test-failed",
      tone: "error",
      label: "Self-test failed",
      detail: "Local start remains blocked until an explicit verification succeeds.",
    } as const;
  }
  if (report.capability.reason === "insufficient_free_vram") {
    return {
      status: "temporary",
      tone: "warning",
      label: "Temporarily unavailable",
      detail: "Close GPU-heavy applications, then refresh and retry.",
    } as const;
  }
  if (report.capability.local_beta_eligible) {
    return {
      status: "ready",
      tone: "success",
      label: "Ready",
      detail: "Every local gate passed for the selected activity profile.",
    } as const;
  }
  return {
    status: "unavailable",
    tone: "error",
    label: "Unavailable",
    detail: "Local start remains fail-closed. Cloud Live settings are still available.",
  } as const;
}

function hardwareLabel(report: LocalReadinessReport) {
  const adapter = report.hardware.adapter;
  if (adapter === null) return report.hardware.error_code ?? "No supported hardware adapter";
  return `${adapter.name} · ${formatBytes(adapter.dedicated_vram_bytes)} VRAM`;
}

function cudaLabel(report: LocalReadinessReport) {
  const cuda = report.hardware.cuda;
  if (!cuda.available) return cuda.error_code ?? "CUDA driver unavailable";
  if (!cuda.adapter_luid_matches) return "DXGI / CUDA adapter mismatch";
  return cuda.driver_api_version === null
    ? "Driver ready · adapter matched"
    : `Driver ${cuda.driver_api_version} · adapter matched`;
}

function modelLabel(report: LocalReadinessReport) {
  const model = report.model;
  if (model.phase === "not_installed") return `${formatBytes(model.total_bytes)} not installed`;
  if (model.phase === "partial") return `${formatBytes(model.received_bytes)} resumable partial`;
  if (model.phase === "corrupt") return model.error_code ?? "Verification failed";
  if (activePhases.has(model.phase)) return phaseLabel(model.phase);
  return `v${model.model_version} · verified layout`;
}

function runtimeLabel(report: LocalReadinessReport) {
  if (report.runtime === "passed") return "Self-test passed";
  if (report.runtime === "not_tested") return "Installed · not tested";
  if (report.runtime === "failed") return report.runtime_error_code ?? "Self-test failed";
  return "Bundled runtime missing";
}

function budgetLabel(report: LocalReadinessReport) {
  const available = report.capability.available_budget_bytes;
  const required = report.capability.required_budget_bytes;
  if (available === null || required === null) return "Unknown · refresh required";
  return `${formatBytes(available)} available · ${formatBytes(required)} required`;
}

function hardwareFailureDetail(report: LocalReadinessReport) {
  const reason = report.capability.reason;
  if (reason === "vram_below12gb") return "Local Beta requires an NVIDIA GPU with at least 12 GB dedicated VRAM.";
  if (reason === "unsupported_vendor") return "Local Beta currently supports a qualifying NVIDIA adapter only.";
  if (reason === "avx2_unavailable") return "This processor does not expose the required AVX2 instruction set.";
  if (reason === "cuda_unavailable" || reason === "driver_incompatible") return "A compatible CUDA driver is required.";
  if (reason === "adapter_mismatch") return "The selected DXGI adapter does not match the CUDA device.";
  if (reason === "unsupported_os" || reason === "unsupported_architecture") return "Local Beta requires Windows 10/11 on x64.";
  return "The local hardware gate did not pass. Cloud Live settings remain available.";
}

function modelTone(phase: OmniModelInstallPhase): "neutral" | "success" | "warning" | "error" {
  if (["ready", "runtime_missing", "self_test_failed"].includes(phase)) return "success";
  if (phase === "corrupt") return "error";
  if (phase === "partial") return "warning";
  return "neutral";
}

function phaseLabel(phase: OmniModelInstallPhase) {
  switch (phase) {
    case "checking_space": return "Checking disk";
    case "downloading": return "Downloading";
    case "cancelling": return "Cancelling";
    case "verifying": return "Verifying";
    case "layout_check": return "Checking layout";
    case "runtime_self_test": return "Self-testing";
    default: return "Working";
  }
}

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value < 0) return "Unknown";
  if (value < 1_024) return `${value} B`;
  if (value < 1_048_576) return `${(value / 1_024).toFixed(1)} KiB`;
  if (value < 1_073_741_824) return `${(value / 1_048_576).toFixed(1)} MiB`;
  return `${(value / 1_073_741_824).toFixed(1)} GiB`;
}

function publicActionError(error: unknown) {
  const code = error instanceof Error ? error.message : String(error);
  const labels: Record<string, string> = {
    LOCAL_HARDWARE_UNSUPPORTED: "This device does not meet the Local Beta hardware gate.",
    OMNI_MODEL_DISK_UNKNOWN: "Available model disk space could not be determined.",
    OMNI_MODEL_DISK_INSUFFICIENT: "There is not enough free disk space for the verified install.",
    OMNI_MODEL_OPERATION_BUSY: "Another local model operation is already active.",
    OMNI_MODEL_IN_USE: "Stop the active Realtime session before removing the local model.",
  };
  return labels[code] ?? "The local model operation did not complete. Refresh readiness and try again.";
}
