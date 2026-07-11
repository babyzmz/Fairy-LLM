import type { Page } from "@playwright/test";

export const PREVIEW_URL = "http://127.0.0.1:43125/";

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
    ({ previewUrl }) => {
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
      };
      const timestamp = "2026-07-11T00:00:00Z";
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
        status: "ready",
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
              capabilities: ["text", "tools"],
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
      };

      const tauriWindow = window as unknown as {
        __TAURI_INTERNALS__: {
          invoke(command: string, args: Record<string, unknown>): Promise<unknown>;
        };
      };
      tauriWindow.__TAURI_INTERNALS__ = {
        async invoke(_command, args) {
          const request = args.request as {
            id: number;
            method: string;
            params: Record<string, unknown>;
          };
          const result =
            request.method === "events.subscribe"
              ? {
                  items: request.params.cursor === 0 ? [event] : [],
                  next_cursor: 1,
                }
              : results[request.method];
          if (result === undefined) {
            throw new Error(`Unexpected Core method: ${request.method}`);
          }
          return { jsonrpc: "2.0", id: request.id, result };
        },
      };
    },
    { previewUrl: PREVIEW_URL },
  );
}
