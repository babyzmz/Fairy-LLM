import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { MediaGenerationJob } from "../core/client";
import { projectMediaJobs, WorkspaceOutputsPanel } from "./WorkspaceOutputsPanel";

describe("WorkspaceOutputsPanel", () => {
  it("shows image generation as a progress visualization instead of a fake result", () => {
    render(
      <WorkspaceOutputsPanel
        scopeKey="task:1"
        jobs={[job({ kind: "image", status: "generating", progress: 42 })]}
        loading={false}
        onOpenStream={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getAllByText("42%")).toHaveLength(2);
    expect(screen.getByLabelText("42% complete")).toBeTruthy();
    expect(document.querySelectorAll(".diffusion-preview i")).toHaveLength(80);
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("allows an active video job to be cancelled", () => {
    const onCancel = vi.fn(async () => undefined);
    const video = job({ kind: "video", status: "in_progress", progress: 63 });
    render(
      <WorkspaceOutputsPanel
        scopeKey="task:1"
        jobs={[video]}
        loading={false}
        onOpenStream={vi.fn()}
        onCancel={onCancel}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledWith(video);
    expect(screen.getByText("Rendering frames")).toBeTruthy();
  });

  it("keeps a failed image output terminal and stops the diffusion animation", () => {
    const { container } = render(
      <WorkspaceOutputsPanel
        scopeKey="task:1"
        jobs={[job({ kind: "image", status: "failed", progress: 4, error_code: "PROVIDER_PROTOCOL_ERROR" })]}
        loading={false}
        onOpenStream={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(container.querySelector(".diffusion-preview.active")).toBeNull();
    expect(screen.getByText("Failed")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("PROVIDER_PROTOCOL_ERROR");
    expect(screen.queryByLabelText("4% complete")).toBeNull();
  });

  it("collapses legacy duplicate media jobs from the same turn without deleting audit data", () => {
    const turnId = "019f566f-f8b4-7000-8000-000000000090";
    const first = job({
      id: "019f566f-f8b4-7000-8000-000000000091",
      turn_id: turnId,
      status: "failed",
      updated_at: "2026-07-16T12:01:00Z",
    });
    const latest = job({
      id: "019f566f-f8b4-7000-8000-000000000092",
      turn_id: turnId,
      status: "failed",
      updated_at: "2026-07-16T12:02:00Z",
    });

    expect(projectMediaJobs([first, latest])).toEqual([latest]);
  });

  it("opens only the verified completed Workspace file", async () => {
    const onOpenStream = vi.fn(async () => ({
      session_id: "019f566f-f8b4-7000-8000-000000000055",
      workspace_id: "019f566f-f8b4-7000-8000-000000000011",
      version_id: "019f566f-f8b4-7000-8000-000000000013",
      path: "generated/fairy.png",
      media_type: "image/png",
      byte_length: 100,
      content_hash: "a".repeat(64),
      url: "http://127.0.0.1:43110/files/token",
      expires_at: "2026-07-16T12:02:00Z",
    }));
    render(
      <WorkspaceOutputsPanel
        scopeKey="task:1"
        jobs={[job({ kind: "image", status: "completed", progress: 100 })]}
        loading={false}
        onOpenStream={onOpenStream}
        onCancel={vi.fn()}
      />,
    );

    expect((await screen.findByRole("img", { name: "fairy.png" })).getAttribute("src")).toBe(
      "http://127.0.0.1:43110/files/token",
    );
    expect(onOpenStream).toHaveBeenCalledWith("generated/fairy.png");
  });
});

function job(overrides: Partial<MediaGenerationJob>): MediaGenerationJob {
  return {
    id: "019f566f-f8b4-7000-8000-000000000050",
    project_id: null,
    workspace_id: "019f566f-f8b4-7000-8000-000000000011",
    conversation_id: "019f566f-f8b4-7000-8000-000000000012",
    task_id: "019f566f-f8b4-7000-8000-000000000010",
    version_id: "019f566f-f8b4-7000-8000-000000000013",
    turn_id: null,
    kind: "image",
    model_id: "google/gemini-3.1-flash-lite-image",
    endpoint_kind: "images",
    output_path: "generated/fairy.png",
    status: "completed",
    progress: 100,
    artifact_ids: [],
    usage_cost: null,
    error_code: null,
    revision: 1,
    created_at: "2026-07-16T12:00:00Z",
    updated_at: "2026-07-16T12:01:00Z",
    ...overrides,
  };
}
