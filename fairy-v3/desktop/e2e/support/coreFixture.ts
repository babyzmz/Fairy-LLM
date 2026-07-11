import type { Page } from "@playwright/test";
import { createHash } from "node:crypto";

export const PREVIEW_URL = "http://127.0.0.1:43125/";
const VOICE_WAV = pcmWav();
const VOICE_WAV_BASE64 = Buffer.from(VOICE_WAV).toString("base64");
const VOICE_WAV_HASH = createHash("sha256").update(VOICE_WAV).digest("hex");
const CAPTURE_PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";
const CAPTURE_PNG_HASH = createHash("sha256")
  .update(Buffer.from(CAPTURE_PNG_BASE64, "base64"))
  .digest("hex");

export async function installWorkspaceFixture(page: Page) {
  await installCoreFixture(page);
  await page.route(`${PREVIEW_URL}**`, async (route) => {
    await route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html><body style="margin:0;font-family:Segoe UI;background:#f4f6f3;color:#18201d"><main style="padding:28px"><h1>Atlas preview</h1><p>Durable candidate version</p></main></body></html>`,
    });
  });
}

async function installCoreFixture(page: Page) {
  await page.addInitScript(
    ({
      previewUrl,
      voiceWavBase64,
      voiceWavHash,
      capturePngBase64,
      capturePngHash,
    }) => {
      const fixtureWindow = window as unknown as {
        __FAIRY_BOOT_STARTED_AT__: number;
        __FAIRY_FIXTURE_CALLS__: Array<{
          method: string;
          params: Record<string, unknown>;
        }>;
        __FAIRY_PUSH_EVENT__: (message: string) => number;
      };
      fixtureWindow.__FAIRY_BOOT_STARTED_AT__ = performance.now();
      fixtureWindow.__FAIRY_FIXTURE_CALLS__ = [];
      Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: {
          getUserMedia: async () => ({
            getTracks: () => [{ stop() {} }],
          }),
        },
      });
      class FixtureMediaRecorder extends EventTarget {
        readonly mimeType = "audio/webm;codecs=opus";
        state = "inactive";

        constructor(_stream: unknown) {
          super();
        }

        start() {
          this.state = "recording";
        }

        stop() {
          if (this.state === "inactive") return;
          this.state = "inactive";
          const data = new Event("dataavailable");
          Object.defineProperty(data, "data", {
            value: new Blob(["fixture-recording"], { type: "audio/webm" }),
          });
          this.dispatchEvent(data);
          queueMicrotask(() => this.dispatchEvent(new Event("stop")));
        }
      }
      class FixtureAudio extends EventTarget {
        constructor(_url: string) {
          super();
        }

        play() {
          window.setTimeout(() => this.dispatchEvent(new Event("ended")), 20);
          return Promise.resolve();
        }

        pause() {}
      }
      Object.defineProperty(window, "MediaRecorder", {
        configurable: true,
        value: FixtureMediaRecorder,
      });
      Object.defineProperty(window, "Audio", {
        configurable: true,
        value: FixtureAudio,
      });
      const id = {
        project: "0198f4de-0114-7000-8000-000000000001",
        conversation: "0198f4de-0114-7000-8000-000000000002",
        task: "0198f4de-0114-7000-8000-000000000003",
        version: "0198f4de-0114-7000-8000-000000000004",
        event: "0198f4de-0114-7000-8000-000000000005",
        runtime: "0198f4de-0114-7000-8000-000000000006",
        preview: "0198f4de-0114-7000-8000-000000000007",
        scratchConversation: "0198f4de-0114-7000-8000-000000000010",
        scratchTask: "0198f4de-0114-7000-8000-000000000011",
        turn: "0198f4de-0114-7000-8000-000000000012",
        message: "0198f4de-0114-7000-8000-000000000013",
        commandRun: "0198f4de-0114-7000-8000-000000000015",
        toolInvocation: "0198f4de-0114-7000-8000-000000000016",
        approval: "0198f4de-0114-7000-8000-000000000017",
        resumedMessage: "0198f4de-0114-7000-8000-000000000018",
        checkpoint: "0198f4de-0114-7000-8000-000000000019",
      };
      const timestamp = "2026-07-11T00:00:00Z";
      const initialTaskStatus =
        new URLSearchParams(window.location.search).get("taskStatus") === "previewing"
          ? "previewing"
          : "ready";
      const project = {
        id: id.project,
        name: "Atlas Console",
        residency: "local_only",
        active_version_id: id.version,
        active_preview_id: id.preview,
        revision: 1,
        created_at: timestamp,
        updated_at: timestamp,
      };
      const conversation = {
        id: id.conversation,
        project_id: id.project,
        workspace_type: "project_chat",
        base_version_id: id.version,
        active_draft_version_id: id.version,
        active_task_id: id.task,
        active_preview_id: id.preview,
        created_at: timestamp,
        updated_at: timestamp,
      };
      const task = {
        id: id.task,
        project_id: id.project,
        conversation_id: id.conversation,
        user_request: "Tighten the project overview",
        operation_mode: "continue_current_chat_draft",
        base_version_id: id.version,
        execution_target: "local",
        target_version_id: id.version,
        memory_snapshot_id: null,
        memory_snapshot_hash: null,
        status: initialTaskStatus,
        created_at: timestamp,
        updated_at: timestamp,
      };
      const scratchConversation = {
        id: id.scratchConversation,
        project_id: null,
        workspace_type: "chat_scratch",
        base_version_id: null,
        active_draft_version_id: null,
        active_task_id: null,
        active_preview_id: null,
        created_at: timestamp,
        updated_at: timestamp,
      };
      const scratchTask = {
        id: id.scratchTask,
        project_id: null,
        conversation_id: id.scratchConversation,
        user_request: "Fixture chat request",
        operation_mode: "answer",
        base_version_id: null,
        execution_target: "local",
        target_version_id: null,
        memory_snapshot_id: null,
        memory_snapshot_hash: null,
        status: "ready",
        created_at: timestamp,
        updated_at: timestamp,
      };
      const completedTurn = {
        id: id.turn,
        task_id: id.scratchTask,
        conversation_id: id.scratchConversation,
        profile_id: "openrouter-free",
        status: "completed",
        idempotency_key: "e2e-turn",
        scope_digest: "e2e-scope",
        memory_snapshot_id: "0198f4de-0114-7000-8000-000000000014",
        memory_snapshot_hash: "e2e-memory",
        cancellation_revision: 0,
        usage: {},
        created_at: timestamp,
        updated_at: timestamp,
        started_at: timestamp,
        completed_at: timestamp,
        error_code: null,
      };
      const scratchMessage = {
        id: id.message,
        conversation_id: id.scratchConversation,
        task_id: id.scratchTask,
        turn_id: id.turn,
        sequence: 1,
        role: "assistant",
        visibility: "user",
        content: "Scratch chat is durable",
        created_at: timestamp,
      };
      const resumedMessage = {
        ...scratchMessage,
        id: id.resumedMessage,
        sequence: 2,
        content: "Notification completed after approval",
      };
      const waitingTurn = {
        ...completedTurn,
        status: "waiting_for_tool",
        completed_at: null,
      };
      const pendingApproval = {
        id: id.approval,
        task_id: id.scratchTask,
        command_run_id: id.commandRun,
        changeset_id: null,
        tool_invocation_id: id.toolInvocation,
        requested_by: "assistant:system.notify",
        reason: "Allow Fairy to send a notification",
        decision: "pending",
        decided_by: null,
        decided_at: null,
        created_at: timestamp,
      };
      const version = {
        id: id.version,
        project_id: id.project,
        source_conversation_id: id.conversation,
        source_task_id: id.task,
        parent_version_id: null,
        project_root: "C:/Fairy/versions/atlas",
        visibility: "chat_draft",
        created_at: timestamp,
      };
      const runtime = {
        id: id.runtime,
        project_id: id.project,
        conversation_id: id.conversation,
        task_id: id.task,
        version_id: id.version,
        project_root: "C:/Fairy/versions/atlas",
        execution_target: "local",
        kind: "static_site",
        executor: "rust_local_worker",
        executor_handle: "preview-fixture",
        port: 43125,
        status: "running",
        health: "healthy",
        error_code: null,
        idempotency_key: "e2e-runtime",
        revision: 1,
        created_at: timestamp,
        updated_at: timestamp,
      };
      const preview = {
        id: id.preview,
        project_id: id.project,
        conversation_id: id.conversation,
        task_id: id.task,
        version_id: id.version,
        runtime_id: id.runtime,
        project_root: "C:/Fairy/versions/atlas",
        execution_target: "local",
        url: previewUrl,
        visibility: "chat_draft",
        status: "ready",
        health: "healthy",
        error_code: null,
        idempotency_key: "e2e-preview",
        revision: 1,
        created_at: timestamp,
        updated_at: timestamp,
      };
      const event = {
        id: id.event,
        cursor: 1,
        run_id: null,
        project_id: id.project,
        conversation_id: id.conversation,
        task_id: id.task,
        version_id: id.version,
        task_sequence: 1,
        event_type: "preview.ready",
        visibility: "user",
        message: "Preview is ready",
        payload: {},
        schema_version: 1,
        created_at: timestamp,
      };
      const events = [event];
      let approvalScenario = false;
      let approvalVisible = false;
      let approvalDecision: "pending" | "approved" | "rejected" = "pending";
      let messages = [scratchMessage];
      fixtureWindow.__FAIRY_PUSH_EVENT__ = (message) => {
        const startedAt = performance.now();
        const cursor = (events.at(-1)?.cursor ?? 0) + 1;
        events.push({
          ...event,
          id: `0198f4de-0114-7000-8000-${String(100_000_000_000 + cursor)}`,
          cursor,
          task_sequence: cursor,
          event_type: "command.output",
          message,
          payload: { release_sequence: cursor },
        });
        return startedAt;
      };

      let permissions = {
        profile: "standard",
        capability_overrides: {} as Record<string, boolean>,
        revision: 0,
        updated_at: "2026-07-11T00:00:00Z",
      };
      const results: Record<string, unknown> = {
        health: { status: "ok", service: "fairy-core", protocol: "core-service-v1" },
        "projects.list": { items: [project], next_cursor: null },
        "conversations.list": {
          items: [conversation, scratchConversation],
          next_cursor: null,
        },
        "tasks.list": { items: [task], next_cursor: null },
        "versions.list": { items: [version], next_cursor: null },
        "approvals.list": { items: [], next_cursor: null },
        "previews.resolve": { task, runtime, preview },
        "runtimes.health": {
          executor: {
            available: true,
            executor: "rust_local_worker",
            version: "0.1.0",
            error_code: null,
            diagnostics: [],
          },
          runtime,
          preview,
        },
        "capabilities.get": {
          profile: "standard",
          operations: {},
          sandbox_healthy: true,
          command_metadata: [],
          schema_version: 1,
        },
        "providers.list": {
          items: [
            {
              id: "openrouter-free",
              display_name: "OpenRouter Free",
              kind: "openai_compatible",
              base_url: "https://openrouter.ai/api/v1",
              model_id: "openrouter/free",
              capabilities: ["text", "tools", "vision", "stt", "tts"],
              credential_required: true,
              credential_configured: true,
              enabled: true,
              timeout_seconds: 60,
              fallback_profile_id: null,
            },
          ],
        },
        "providers.health": {
          items: [
            {
              profile_id: "openrouter-free",
              status: "available",
              error_code: null,
              diagnostics: [],
            },
          ],
        },
        "messages.list": { items: [scratchMessage], next_cursor: null },
        "tasks.create": { task: scratchTask },
        "documents.import": {},
        "assistant.turns.create": { ...completedTurn, status: "created" },
        "assistant.turns.run": completedTurn,
        "assistant.turns.cancel": { ...completedTurn, status: "cancelled" },
        "assistant.turns.retry": { ...completedTurn, status: "created" },
        "conversations.create": scratchConversation,
        "voice.transcribe": {
          conversation_id: id.scratchConversation,
          profile_id: "openrouter-free",
          text: "Fixture voice transcript",
          language: "en",
          segments: [],
        },
      };

      const tauriWindow = window as unknown as {
        __TAURI_INTERNALS__: {
          invoke(command: string, args: Record<string, unknown>): Promise<unknown>;
        };
      };
      tauriWindow.__TAURI_INTERNALS__ = {
        async invoke(command, args) {
          if (command === "list_capture_surfaces") {
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: "capture.list",
              params: {},
            });
            return [
              {
                kind: "display",
                source_id: "1",
                label: "Primary display",
                width: 1920,
                height: 1080,
                is_primary: true,
              },
              {
                kind: "window",
                source_id: "2",
                label: "Game window",
                width: 1,
                height: 1,
                is_primary: false,
              },
            ];
          }
          if (command === "capture_surface") {
            const captureRequest = args.request as Record<string, unknown>;
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: "capture.surface",
              params: captureRequest,
            });
            return {
              kind: captureRequest.kind,
              source_id: captureRequest.source_id,
              source_label:
                captureRequest.kind === "window" ? "Game window" : "Primary display",
              media_type: "image/png",
              png_base64: capturePngBase64,
              width: captureRequest.kind === "window" ? 1 : 1920,
              height: captureRequest.kind === "window" ? 1 : 1080,
              byte_length: 68,
              content_hash: capturePngHash,
              captured_at_ms: 1_784_000_000_000,
            };
          }
          if (command !== "core_rpc") {
            throw new Error(`Unexpected Tauri command: ${command}`);
          }
          const request = args.request as {
            id: number;
            method: string;
            params: Record<string, unknown>;
          };
          fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
            method: request.method,
            params: request.params,
          });
          const result =
            request.method === "voice.synthesize"
              ? {
                  task_id: request.params.task_id,
                  turn_id: request.params.turn_id,
                  message_id: request.params.message_id,
                  profile_id: request.params.profile_id,
                  start_offset: request.params.start_offset,
                  end_offset: request.params.end_offset,
                  media_type: "audio/wav",
                  audio_base64: voiceWavBase64,
                  sample_rate: 24_000,
                  channels: 1,
                  frames: 2,
                  content_hash: voiceWavHash,
                }
              : request.method === "tasks.create"
                ? (() => {
                    const userRequest = String(request.params.user_request ?? "");
                    approvalScenario = userRequest === "Request a governed notification";
                    approvalVisible = false;
                    approvalDecision = "pending";
                    messages = [scratchMessage];
                    return { task: { ...scratchTask, user_request: userRequest } };
                  })()
              : request.method === "assistant.turns.create"
                ? { ...completedTurn, status: "created", completed_at: null }
              : request.method === "assistant.turns.run"
                ? (() => {
                    if (!approvalScenario) return completedTurn;
                    if (approvalDecision === "pending") {
                      approvalVisible = true;
                      return waitingTurn;
                    }
                    if (!messages.some((message) => message.id === resumedMessage.id)) {
                      messages = [...messages, resumedMessage];
                    }
                    return completedTurn;
                  })()
              : request.method === "messages.list"
                ? { items: messages, next_cursor: null }
              : request.method === "approvals.list"
                ? {
                    items:
                      approvalVisible && request.params.task_id === id.scratchTask
                        ? [
                            {
                              ...pendingApproval,
                              decision: approvalDecision,
                              decided_by:
                                approvalDecision === "pending" ? null : "user",
                              decided_at:
                                approvalDecision === "pending" ? null : timestamp,
                            },
                          ]
                        : [],
                    next_cursor: null,
                  }
              : request.method === "approvals.decide"
                ? (() => {
                    if (request.params.approval_id !== id.approval) {
                      throw new Error("Approval is unavailable");
                    }
                    if ("decided_by" in request.params) {
                      throw new Error("Renderer cannot choose decided_by");
                    }
                    approvalDecision = request.params.approved ? "approved" : "rejected";
                    const cursor = (events.at(-1)?.cursor ?? 0) + 1;
                    events.push({
                      ...event,
                      id: `0198f4de-0114-7000-8000-${String(100_000_000_000 + cursor)}`,
                      cursor,
                      run_id: id.commandRun,
                      project_id: null,
                      conversation_id: id.scratchConversation,
                      task_id: id.scratchTask,
                      version_id: null,
                      task_sequence: cursor,
                      event_type: "approval.decided",
                      message: `Approval ${approvalDecision}`,
                      payload: {
                        approval_id: id.approval,
                        decision: approvalDecision,
                      },
                    });
                    return {
                      approval: {
                        ...pendingApproval,
                        decision: approvalDecision,
                        decided_by: "user",
                        decided_at: timestamp,
                      },
                      changeset: null,
                    };
                  })()
              : request.method === "tasks.review"
                ? (() => {
                    if (request.params.task_id !== id.task) {
                      throw new Error("Task is unavailable");
                    }
                    task.status = "ready";
                    const cursor = (events.at(-1)?.cursor ?? 0) + 1;
                    events.push({
                      ...event,
                      id: `0198f4de-0114-7000-8000-${String(100_000_000_000 + cursor)}`,
                      cursor,
                      task_sequence: cursor,
                      event_type: "task.reviewed",
                      message: "Review complete",
                      payload: { status: "ready", checkpoint_id: id.checkpoint },
                    });
                    return {
                      id: id.checkpoint,
                      task_id: id.task,
                      version_id: id.version,
                      changed_files: ["README.md"],
                      command_run_ids: [id.commandRun],
                      preview_artifact_id: null,
                      created_at: timestamp,
                    };
                  })()
              : request.method === "permissions.get"
                ? permissions
              : request.method === "permissions.update"
                ? (() => {
                    if (request.params.expected_revision !== permissions.revision) {
                      throw new Error("VERSION_CONFLICT");
                    }
                    permissions = {
                      profile: String(request.params.profile),
                      capability_overrides:
                        (request.params.capability_overrides as Record<string, boolean>) ?? {},
                      revision: permissions.revision + 1,
                      updated_at: "2026-07-11T00:00:01Z",
                    };
                    return permissions;
                  })()
              : request.method === "capabilities.get"
                ? {
                    ...(results["capabilities.get"] as Record<string, unknown>),
                    profile: permissions.profile,
                    operations: Object.fromEntries(
                      Object.entries(permissions.capability_overrides).map(([name, enabled]) => [
                        name,
                        enabled,
                      ]),
                    ),
                  }
              : request.method === "events.subscribe"
              ? (() => {
                  const cursor = Number(request.params.cursor ?? 0);
                  const items = events.filter((item) => item.cursor > cursor);
                  return {
                    items,
                    next_cursor: items.at(-1)?.cursor ?? cursor,
                  };
                })()
              : results[request.method];
          if (result === undefined) {
            throw new Error(`Unexpected Core method: ${request.method}`);
          }
          return { jsonrpc: "2.0", id: request.id, result };
        },
      };
    },
    {
      previewUrl: PREVIEW_URL,
      voiceWavBase64: VOICE_WAV_BASE64,
      voiceWavHash: VOICE_WAV_HASH,
      capturePngBase64: CAPTURE_PNG_BASE64,
      capturePngHash: CAPTURE_PNG_HASH,
    },
  );
}

function pcmWav(): Uint8Array {
  const samples = new Uint8Array([0, 0, 1, 0]);
  const bodyLength = 4 + 8 + 16 + 8 + samples.length;
  const bytes = new Uint8Array(8 + bodyLength);
  const view = new DataView(bytes.buffer);
  writeAscii(bytes, 0, "RIFF");
  view.setUint32(4, bodyLength, true);
  writeAscii(bytes, 8, "WAVE");
  writeAscii(bytes, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 24_000, true);
  view.setUint32(28, 48_000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(bytes, 36, "data");
  view.setUint32(40, samples.length, true);
  bytes.set(samples, 44);
  return bytes;
}

function writeAscii(bytes: Uint8Array, offset: number, value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    bytes[offset + index] = value.charCodeAt(index);
  }
}
