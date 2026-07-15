import {
  AlertCircle,
  BadgeCheck,
  Bot,
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  CircleDot,
  FileOutput,
  ListChecks,
  LoaderCircle,
  MessageSquareText,
  Route,
  Search,
  ShieldCheck,
  Sparkles,
  Volume2,
  Wrench,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { m } from "motion/react";

import type { AssistantTurn, EventEnvelope, TurnTrace } from "../core/client";
import { useVoicePlaybackState } from "../voice/VoiceController";
import {
  modelRoleLabel,
  projectWorkChain,
  withVoiceStep,
  workChainTone,
  type WorkChainProjection,
  type WorkChainStep,
} from "./workChainProjection";
import type { TurnTraceQueryState } from "./useTurnTraces";

export { publicActivities } from "./workChainProjection";
export type { PublicActivity } from "./workChainProjection";

interface ActivityRailProps {
  turn?: AssistantTurn | null;
  turnId?: string | null;
  trace?: TurnTrace | null;
  traceState?: TurnTraceQueryState | null;
  events?: EventEnvelope[];
  developerMode?: boolean;
}

export function ActivityRail({
  turn = null,
  turnId = null,
  trace = null,
  traceState = null,
  events = [],
  developerMode = false,
}: ActivityRailProps) {
  const [expanded, setExpanded] = useState(false);
  const [clock, setClock] = useState(() => Date.now());
  const resolvedTurnId = trace?.turn_id ?? turn?.id ?? turnId;
  const voiceState = useVoicePlaybackState(resolvedTurnId ?? "no-active-turn");
  const baseProjection = useMemo(
    () => projectWorkChain({ trace, traceState, turn, turnId, events, developerMode, now: clock }),
    [clock, developerMode, events, trace, traceState, turn, turnId],
  );
  const projection = useMemo(
    () => resolvedTurnId === null
      ? baseProjection
      : withVoiceStep(baseProjection, resolvedTurnId, voiceState),
    [baseProjection, resolvedTurnId, voiceState],
  );
  const tone = workChainTone(projection.current);

  useEffect(() => {
    if (projection.terminal) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [projection.terminal]);

  return (
    <section
      className={`activity-rail work-chain activity-${tone}${projection.terminal ? " activity-terminal" : ""}`}
      aria-label="Fairy work chain"
      data-turn-id={resolvedTurnId ?? undefined}
      data-trace-state={traceState?.status ?? undefined}
    >
      <button
        type="button"
        className="activity-summary"
        aria-label="Fairy activity"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <ActivityIcon step={projection.current} active={!projection.terminal} />
        <span className="activity-summary-copy">
          <span className="activity-current">{projection.current.summary}</span>
          <span className="activity-recent">{summaryLine(projection)}</span>
        </span>
        <span className="activity-duration">{formatDuration(projection.durationMs)}</span>
        <ChevronDown className={expanded ? "activity-chevron expanded" : "activity-chevron"} size={14} />
      </button>
      {expanded ? (
        <m.ol
          className="activity-history work-chain-steps"
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          transition={{ duration: 0.16 }}
        >
          {projection.steps.map((step) => (
            <WorkChainStepRow
              key={step.id}
              step={step}
              steps={projection.steps}
              active={step.id === projection.current.id && !projection.terminal}
              developerMode={developerMode}
            />
          ))}
        </m.ol>
      ) : null}
    </section>
  );
}

function WorkChainStepRow({
  step,
  steps,
  active,
  developerMode,
}: {
  step: WorkChainStep;
  steps: WorkChainStep[];
  active: boolean;
  developerMode: boolean;
}) {
  const depth = stepDepth(step, steps);
  const roleLabel = modelRoleLabel(step);
  const tone = workChainTone(step);
  return (
    <li
      className={`work-chain-step work-chain-step-${tone}`}
      data-kind={step.kind}
      data-status={step.status}
      style={{ paddingLeft: `${depth * 18}px` }}
      aria-current={active ? "step" : undefined}
    >
      <span className="work-chain-node" aria-hidden="true">
        <ActivityIcon step={step} active={active} />
      </span>
      <div className="work-chain-step-copy">
        <div className="work-chain-step-heading">
          {roleLabel === null ? null : <span className="work-chain-role">{roleLabel}</span>}
          <strong>{step.summary}</strong>
        </div>
        {step.detail === null ? null : <p>{step.detail}</p>}
        {step.artifactRefs.length > 0 ? (
          <span className="work-chain-artifacts">
            {step.artifactRefs.length} artifact{step.artifactRefs.length === 1 ? "" : "s"}
          </span>
        ) : null}
        {developerMode ? <StepDiagnostics step={step} /> : null}
      </div>
      <time dateTime={step.startedAt ?? step.createdAt}>
        {step.durationMs === null ? formatTime(step.startedAt ?? step.createdAt) : formatDuration(step.durationMs)}
      </time>
    </li>
  );
}

function StepDiagnostics({ step }: { step: WorkChainStep }) {
  const diagnostics = [
    `seq ${step.sequence}`,
    step.modelId === null ? null : `model ${step.modelId}`,
    step.commandName === null ? null : `command ${step.commandName}`,
    step.providerAttemptId === null ? null : `attempt ${shortId(step.providerAttemptId)}`,
    step.commandRunId === null ? null : `run ${shortId(step.commandRunId)}`,
    `status ${step.status}`,
  ].filter((value): value is string => value !== null);
  return <div className="work-chain-diagnostics">{diagnostics.join(" | ")}</div>;
}

function ActivityIcon({
  step,
  active,
}: {
  step: WorkChainStep;
  active: boolean;
}) {
  if (active) return <LoaderCircle className="activity-spinner" size={14} />;
  if (step.status === "failed") return <AlertCircle size={14} />;
  if (step.status === "succeeded") return <CheckCircle2 size={14} />;
  const icons = {
    route: Route,
    plan: ListChecks,
    reasoning: BrainCircuit,
    model: Bot,
    tool: Wrench,
    approval: ShieldCheck,
    observation: Search,
    verification: BadgeCheck,
    artifact: FileOutput,
    response: MessageSquareText,
    voice: Volume2,
  } as const;
  const Icon = icons[step.kind] ?? Sparkles;
  return step.status === "cancelled" || step.status === "skipped"
    ? <CircleDot size={14} />
    : <Icon size={14} />;
}

function summaryLine(projection: WorkChainProjection): string {
  if (!projection.terminal) {
    return `${projection.completedSteps}/${projection.totalSteps} steps`;
  }
  const parts = [`${projection.totalSteps} ${projection.totalSteps === 1 ? "step" : "steps"}`];
  if (projection.toolCount > 0) parts.push(`${projection.toolCount} ${projection.toolCount === 1 ? "tool" : "tools"}`);
  if (projection.modelCount > 0) parts.push(`${projection.modelCount} ${projection.modelCount === 1 ? "model" : "models"}`);
  return parts.join(" · ");
}

function stepDepth(step: WorkChainStep, steps: WorkChainStep[]): number {
  const byId = new Map(steps.map((item) => [item.id, item]));
  let parentId = step.parentStepId;
  let depth = 0;
  const visited = new Set<string>();
  while (parentId !== null && depth < 2 && !visited.has(parentId)) {
    visited.add(parentId);
    const parent = byId.get(parentId);
    if (parent === undefined) break;
    depth += 1;
    parentId = parent.parentStepId;
  }
  return depth;
}

function shortId(value: string): string {
  return value.length <= 12 ? value : `${value.slice(0, 8)}…`;
}

function formatDuration(value: number | null): string {
  if (value === null) return "";
  if (value < 1_000) return `${Math.round(value)}ms`;
  const seconds = Math.round(value / 1_000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}m ${remainder}s`;
}

function formatTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return "";
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(parsed);
}
