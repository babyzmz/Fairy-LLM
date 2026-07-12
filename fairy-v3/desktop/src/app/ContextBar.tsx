import { Cloud, Play, ShieldCheck, WifiOff } from "lucide-react";

import type { WorkspaceModel } from "./workspaceModel";

export function ContextBar({ model }: { model: WorkspaceModel }) {
  const path =
    model.mode === "chat"
      ? (model.selectedChatConversation?.title ?? "New chat")
      : [
          model.selectedProject?.name,
          model.selectedConversation?.title,
          model.selectedTask?.display_title,
        ]
          .filter(Boolean)
          .join(" / ") || "Projects";
  return (
    <header className="context-bar" role="banner">
      <div className="context-identity">
        <span className="context-path" title={path}>
          {path}
        </span>
        {model.mode === "project" && model.selectedVersion !== null ? (
          <span className="context-version">
            {versionLabel(model.selectedVersion.visibility)}
          </span>
        ) : null}
      </div>
      <div className="telemetry-strip" aria-label="Workspace status">
        <span className="telemetry-item">
          <Play size={14} /> {model.selectedTask?.execution_target ?? "local"}
        </span>
        <span className="telemetry-item">
          <Cloud size={14} />{" "}
          {model.selectedProject?.residency === "synced"
            ? "SYNCED"
            : "LOCAL ONLY"}
        </span>
        <span className="telemetry-item">
          <ShieldCheck size={14} /> {model.permissionProfile ?? "unavailable"}
        </span>
        <span
          className={`telemetry-item ${model.state === "offline" ? "offline" : "online"}`}
        >
          {model.state === "offline" ? (
            <WifiOff size={14} />
          ) : (
            <Cloud size={14} />
          )}
          {model.statusLabel}
        </span>
      </div>
    </header>
  );
}

function versionLabel(visibility: string): string {
  if (visibility === "active") return "Active";
  if (visibility.includes("draft")) return "Draft";
  return "Candidate";
}
