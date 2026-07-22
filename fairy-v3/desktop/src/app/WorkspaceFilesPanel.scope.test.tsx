import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  FilePresentationResult,
  FileReadSession,
  FileSet,
  WorkspaceFile,
  WorkspaceFileContent,
} from "../core/client";
import { WorkspaceFilesPanel } from "./WorkspaceFilesPanel";

afterEach(cleanup);

describe("WorkspaceFilesPanel conversation scope", () => {
  it("clears a selected artifact when the workspace version changes even if the path is identical", async () => {
    const file = workspaceFile("generated/result.txt");
    const view = render(panel({
      versionId: "00000000-0000-4000-8000-0000000000a1",
      file,
      content: "Conversation A artifact",
    }));

    fireEvent.click(screen.getByRole("button", { name: "result.txt" }));
    expect(await screen.findByText("Conversation A artifact")).toBeVisible();

    view.rerender(panel({
      versionId: "00000000-0000-4000-8000-0000000000b1",
      file,
      content: "Conversation B artifact",
    }));

    expect(screen.queryByText("Conversation A artifact")).not.toBeInTheDocument();
    expect(screen.getByText("Select a file")).toBeVisible();
  });

  it("ignores an artifact read that completes after its scope is replaced", async () => {
    const file = workspaceFile("generated/result.txt");
    let resolveRead: (value: WorkspaceFileContent) => void = () => {
      throw new Error("read resolver was not initialized");
    };
    const delayedRead = new Promise<WorkspaceFileContent>((resolve) => {
      resolveRead = resolve;
    });
    const view = render(panel({
      versionId: "00000000-0000-4000-8000-0000000000a1",
      file,
      content: "Conversation A late artifact",
      onRead: async () => delayedRead,
    }));

    fireEvent.click(screen.getByRole("button", { name: "result.txt" }));
    view.rerender(panel({
      versionId: "00000000-0000-4000-8000-0000000000b1",
      file,
      content: "Conversation B artifact",
    }));
    await vi.waitFor(() => expect(screen.getByText("Select a file")).toBeVisible());
    resolveRead({
      file,
      media_type: "text/plain",
      stream_required: false,
      text: "Conversation A late artifact",
    });
    await Promise.resolve();

    expect(screen.queryByText("Conversation A late artifact")).not.toBeInTheDocument();
    expect(screen.getByText("Select a file")).toBeVisible();
  });

  it("rejects an oversized 3D dependency set before opening any streams", async () => {
    const file = workspaceFile("models/scene.glb");
    const onOpenStream = vi.fn(async (): Promise<FileReadSession> => {
      throw new Error("stream fan-out must not start");
    });
    render(panel({
      versionId: "00000000-0000-4000-8000-0000000000c1",
      file,
      content: "",
      mediaType: "model/gltf-binary",
      streamRequired: true,
      onOpenStream,
      fileSet: {
        id: "00000000-0000-4000-8000-0000000000f1",
        workspace_id: "00000000-0000-4000-8000-0000000000f2",
        version_id: "00000000-0000-4000-8000-0000000000c1",
        kind: "gltf",
        primary_path: file.path,
        parser_version: "test-v1",
        manifest_hash: "b".repeat(64),
        members: [
          { path: file.path, role: "primary", byte_length: 80 * 1024 * 1024, content_hash: "c".repeat(64) },
          { path: "models/buffer.bin", role: "buffer", byte_length: 50 * 1024 * 1024, content_hash: "d".repeat(64) },
        ],
        missing_dependencies: [],
        blocked_dependencies: [],
      },
    }));

    fireEvent.click(screen.getByRole("button", { name: "scene.glb" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("128 MiB");
    expect(onOpenStream).not.toHaveBeenCalled();
  });
});

function panel({
  versionId,
  file,
  content,
  onRead,
  mediaType = "text/plain",
  streamRequired = false,
  onOpenStream,
  fileSet,
}: {
  versionId: string;
  file: WorkspaceFile;
  content: string;
  onRead?: () => Promise<WorkspaceFileContent>;
  mediaType?: string;
  streamRequired?: boolean;
  onOpenStream?: () => Promise<FileReadSession>;
  fileSet?: FileSet;
}) {
  const fileContent: WorkspaceFileContent = {
    file,
    media_type: mediaType,
    stream_required: streamRequired,
    text: content,
  };
  return (
    <WorkspaceFilesPanel
      scopeKey={versionId}
      files={[file]}
      assetSets={[]}
      versions={[]}
      currentVersionId={versionId}
      loading={false}
      onRead={vi.fn(onRead ?? (async () => fileContent))}
      onOpenStream={vi.fn(onOpenStream ?? (async () => { throw new Error("not streamed"); }))}
      onPresent={vi.fn(async () => ({
        job: { status: "ready" },
        presentation: null,
      }) as FilePresentationResult)}
      onCompare={vi.fn(async () => { throw new Error("not compared"); })}
      onResolveFileSet={vi.fn(async () => fileSet ?? ({
        id: "00000000-0000-4000-8000-0000000000f1",
        workspace_id: "00000000-0000-4000-8000-0000000000f2",
        version_id: versionId,
        kind: "single_file",
        primary_path: file.path,
        parser_version: "test-v1",
        manifest_hash: "b".repeat(64),
        members: [],
        missing_dependencies: [],
        blocked_dependencies: [],
      }) satisfies FileSet)}
      onListAnnotations={vi.fn(async () => ({ document: null }))}
      onUpdateAnnotations={vi.fn(async () => { throw new Error("not annotated"); })}
      onCreateTextSelection={vi.fn(async () => { throw new Error("not selected"); })}
      onCreateSceneSelection={vi.fn(async () => { throw new Error("not selected"); })}
      onReveal={vi.fn(async () => undefined)}
      onRefresh={vi.fn(async () => undefined)}
      onUpload={vi.fn(async () => undefined)}
      onRename={vi.fn(async () => undefined)}
      onDelete={vi.fn(async () => undefined)}
      onExport={vi.fn(async () => { throw new Error("not exported"); })}
    />
  );
}

function workspaceFile(path: string): WorkspaceFile {
  return {
    path,
    kind: "source",
    language: "text",
    byte_length: 23,
    content_hash: "a".repeat(64),
    imports: [],
  };
}
