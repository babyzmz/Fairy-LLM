import type { Page } from "@playwright/test";
import { createHash } from "node:crypto";

export const PREVIEW_URL = "http://127.0.0.1:43125/";
const VOICE_WAV = pcmWav();
const VOICE_WAV_BASE64 = Buffer.from(VOICE_WAV).toString("base64");
const VOICE_WAV_HASH = createHash("sha256").update(VOICE_WAV).digest("hex");
const CAPTURE_PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";
const CAPTURE_PNG_HASH = createHash("sha256").update(Buffer.from(CAPTURE_PNG_BASE64, "base64")).digest("hex");
const PDF_FIXTURE_BASE64 =
  "JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQovVGl0bGUgKEZhaXJ5IFBERiBGaXh0dXJlKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Cj4+Ci9NZWRpYUJveCBbIDAuMCAwLjAgMzAwIDIwMCBdCi9QYXJlbnQgMiAwIFIKPj4KZW5kb2JqCnhyZWYKMCA1CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAxNSAwMDAwIG4gCjAwMDAwMDAwODEgMDAwMDAgbiAKMDAwMDAwMDE0MCAwMDAwMCBuIAowMDAwMDAwMTg5IDAwMDAwIG4gCnRyYWlsZXIKPDwKL1NpemUgNQovUm9vdCAzIDAgUgovSW5mbyAxIDAgUgo+PgpzdGFydHhyZWYKMjg4CiUlRU9GCg==";
const PDF_FIXTURE = Buffer.from(PDF_FIXTURE_BASE64, "base64");
const PDF_FIXTURE_HASH = createHash("sha256").update(PDF_FIXTURE).digest("hex");
const MODEL_BUFFER = Buffer.alloc(36);
[-1, -1, 0, 1, -1, 0, 0, 1, 0].forEach((value, index) => MODEL_BUFFER.writeFloatLE(value, index * 4));
const MODEL_FIXTURE_TEXT = JSON.stringify({
  asset: { version: "2.0", generator: "Fairy fixture" },
  buffers: [{ uri: `data:application/octet-stream;base64,${MODEL_BUFFER.toString("base64")}`, byteLength: 36 }],
  bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 36 }],
  accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: "VEC3", min: [-1, -1, 0], max: [1, 1, 0] }],
  materials: [{ name: "Fairy mint", pbrMetallicRoughness: { baseColorFactor: [0.2, 0.8, 0.72, 1] } }],
  meshes: [{ name: "Triangle", primitives: [{ attributes: { POSITION: 0 }, material: 0 }] }],
  nodes: [{ name: "Fairy Triangle", mesh: 0 }],
  scenes: [{ nodes: [0] }],
  scene: 0,
});
const MODEL_FIXTURE = Buffer.from(MODEL_FIXTURE_TEXT);
const MODEL_FIXTURE_HASH = createHash("sha256").update(MODEL_FIXTURE).digest("hex");

export async function installWorkspaceFixture(page: Page) {
  await installCoreFixture(page);
  await page.route(`${PREVIEW_URL}**`, async (route) => {
    if (route.request().url().endsWith("/fixture.pdf")) {
      await route.fulfill({
        contentType: "application/pdf",
        body: PDF_FIXTURE,
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Expose-Headers": "Accept-Ranges, Content-Length, Content-Range",
          "Accept-Ranges": "bytes",
        },
      });
      return;
    }
    if (route.request().url().endsWith("/fixture.png")) {
      await route.fulfill({
        contentType: "image/png",
        body: Buffer.from(CAPTURE_PNG_BASE64, "base64"),
        headers: { "Access-Control-Allow-Origin": "*" },
      });
      return;
    }
    if (route.request().url().endsWith("/fixture.gltf")) {
      await route.fulfill({
        contentType: "model/gltf+json",
        body: MODEL_FIXTURE,
        headers: { "Access-Control-Allow-Origin": "*" },
      });
      return;
    }
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
      pdfFixtureHash,
      pdfFixtureByteLength,
      modelFixtureText,
      modelFixtureHash,
      modelFixtureByteLength,
    }) => {
      const fixtureWindow = window as unknown as {
        isTauri: boolean;
        __FAIRY_BOOT_STARTED_AT__: number;
        __FAIRY_FIXTURE_CALLS__: Array<{
          method: string;
          params: Record<string, unknown>;
        }>;
        __FAIRY_PUSH_EVENT__: (message: string) => number;
      };
      fixtureWindow.isTauri = true;
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
        projectTurn: "0198f4de-0114-7000-8000-000000000014",
        message: "0198f4de-0114-7000-8000-000000000013",
        commandRun: "0198f4de-0114-7000-8000-000000000015",
        toolInvocation: "0198f4de-0114-7000-8000-000000000016",
        approval: "0198f4de-0114-7000-8000-000000000017",
        resumedMessage: "0198f4de-0114-7000-8000-000000000018",
        checkpoint: "0198f4de-0114-7000-8000-000000000019",
        scratchVersion: "0198f4de-0114-7000-8000-000000000021",
        fileSet: "0198f4de-0114-7000-8000-000000000022",
        renderJob: "0198f4de-0114-7000-8000-000000000023",
        presentation: "0198f4de-0114-7000-8000-000000000024",
        annotation: "0198f4de-0114-7000-8000-000000000025",
        selection: "0198f4de-0114-7000-8000-000000000026",
        pdfFileSet: "0198f4de-0114-7000-8000-000000000027",
        pdfRenderJob: "0198f4de-0114-7000-8000-000000000028",
        pdfPresentation: "0198f4de-0114-7000-8000-000000000029",
      };
      const timestamp = "2026-07-11T00:00:00Z";
      const fixtureParams = new URLSearchParams(window.location.search);
      const initialTaskStatus = fixtureParams.get("taskStatus") === "previewing" ? "previewing" : "ready";
      const executionRecovery = fixtureParams.get("executionRecovery") === "1";
      let project = {
        id: id.project,
        workspace_id: id.project,
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
        workspace_id: id.project,
        workspace_type: "project_chat",
        base_version_id: id.version,
        active_draft_version_id: id.version,
        active_task_id: id.task,
        active_preview_id: id.preview,
        title: "Project conversation",
        pinned_at: null,
        deleted_at: null,
        revision: 0,
        created_at: timestamp,
        updated_at: timestamp,
      };
      let task = {
        id: id.task,
        project_id: id.project,
        workspace_id: id.project,
        conversation_id: id.conversation,
        user_request: "Tighten the project overview",
        operation_mode: "continue_current_chat_draft",
        base_version_id: id.version,
        execution_target: "local",
        target_version_id: id.version,
        memory_snapshot_id: null,
        memory_snapshot_hash: null,
        status: initialTaskStatus,
        display_title: "Tighten the project overview",
        pinned_at: null,
        metadata_revision: 0,
        created_at: timestamp,
        updated_at: timestamp,
      };
      const scratchConversation = {
        id: id.scratchConversation,
        project_id: null,
        workspace_id: id.scratchConversation,
        workspace_type: "chat_scratch",
        base_version_id: null,
        active_draft_version_id: id.scratchVersion,
        active_task_id: null,
        active_preview_id: null,
        title: "Scratch chat",
        pinned_at: null,
        deleted_at: null,
        revision: 0,
        created_at: timestamp,
        updated_at: timestamp,
      };
      const scratchTask = {
        id: id.scratchTask,
        project_id: null,
        workspace_id: id.scratchConversation,
        conversation_id: id.scratchConversation,
        user_request: "Fixture chat request",
        operation_mode: "answer",
        base_version_id: null,
        execution_target: "local",
        target_version_id: id.scratchVersion,
        memory_snapshot_id: null,
        memory_snapshot_hash: null,
        status: "ready",
        display_title: "Fixture chat request",
        pinned_at: null,
        metadata_revision: 0,
        created_at: timestamp,
        updated_at: timestamp,
      };
      const completedTurn = {
        id: id.turn,
        task_id: id.scratchTask,
        conversation_id: id.scratchConversation,
        profile_id: "openrouter-deepseek-v4-pro",
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
      const projectMessage = {
        id: "0198f4de-0114-7000-8000-000000000160",
        conversation_id: id.conversation,
        task_id: id.task,
        turn_id: id.projectTurn,
        sequence: 1,
        role: "user",
        visibility: "user",
        content: "Tighten the project boundary",
        created_at: timestamp,
      };
      const projectTraceId = "0198f4de-0114-7000-8000-000000000161";
      const projectTraceStep = (
        stepId: string,
        sequence: number,
        kind: string,
        publicSummary: string,
        overrides: Record<string, unknown> = {},
      ) => ({
        id: stepId,
        trace_id: projectTraceId,
        turn_id: id.projectTurn,
        sequence,
        parent_step_id: null,
        caused_by_step_id: null,
        kind,
        status: "succeeded",
        public_summary: publicSummary,
        public_detail: null,
        model_id: null,
        model_role: null,
        provider_attempt_id: null,
        command_run_id: null,
        artifact_refs: [],
        visibility: "user",
        revision: 1,
        created_at: timestamp,
        updated_at: timestamp,
        started_at: timestamp,
        completed_at: timestamp,
        duration_ms: 40,
        ...overrides,
      });
      const projectTraceSteps = [
        projectTraceStep(
          "0198f4de-0114-7000-8000-000000000162",
          1,
          "plan",
          "Project scope prepared",
        ),
        projectTraceStep(
          "0198f4de-0114-7000-8000-000000000163",
          2,
          "verification",
          "Preview is ready",
          {
            caused_by_step_id: "0198f4de-0114-7000-8000-000000000162",
            duration_ms: 120,
          },
        ),
        ...(executionRecovery
          ? [
              projectTraceStep(
                "0198f4de-0114-7000-8000-000000000164",
                3,
                "tool",
                "Sandbox command running",
                {
                  parent_step_id: "0198f4de-0114-7000-8000-000000000162",
                  caused_by_step_id: "0198f4de-0114-7000-8000-000000000162",
                  command_run_id: id.commandRun,
                  duration_ms: 300,
                },
              ),
              projectTraceStep(
                "0198f4de-0114-7000-8000-000000000165",
                4,
                "observation",
                "Runtime recovered after worker interruption",
                {
                  caused_by_step_id: "0198f4de-0114-7000-8000-000000000164",
                  duration_ms: 75,
                },
              ),
            ]
          : []),
      ];
      const projectTrace = {
        id: projectTraceId,
        turn_id: id.projectTurn,
        conversation_id: id.conversation,
        task_id: id.task,
        legacy: false,
        last_sequence: projectTraceSteps.length,
        revision: projectTraceSteps.length,
        created_at: timestamp,
        updated_at: timestamp,
        started_at: timestamp,
        completed_at: timestamp,
        steps: projectTraceSteps,
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
      const toolMessage = {
        ...scratchMessage,
        id: "0198f4de-0114-7000-8000-000000000020",
        sequence: 2,
        role: "tool",
        visibility: "developer",
        content: "[TOOL_RESULT] fixture provider payload [/TOOL_RESULT]",
      };
      const resumedMessage = {
        ...scratchMessage,
        id: id.resumedMessage,
        sequence: 3,
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
        workspace_id: id.project,
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
        workspace_id: id.project,
        conversation_id: id.conversation,
        task_id: id.task,
        version_id: id.version,
        project_root: "C:/Fairy/versions/atlas",
        execution_target: "local",
        kind: "static_site",
        executor: "rust_local_worker",
        executor_handle: "preview-fixture",
        port: 43125,
        graph: {
          public_service_id: "web",
          services: [
            {
              service_id: "web",
              adapter: "static",
              cwd: ".",
              readiness_path: "/",
              depends_on: [],
            },
          ],
        },
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
        workspace_id: id.project,
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
        schema_version: 2,
        created_at: timestamp,
      };
      const events = [event];
      const scenarios = new URLSearchParams(window.location.search);
      if (scenarios.has("executionRecovery")) {
        events.push(
          {
            ...event,
            id: "0198f4de-0114-7000-8000-000000000020",
            cursor: 2,
            task_sequence: 2,
            event_type: "command.running",
            message: "Sandbox command running",
          },
          {
            ...event,
            id: "0198f4de-0114-7000-8000-000000000021",
            cursor: 3,
            task_sequence: 3,
            event_type: "runtime.recovered",
            message: "Runtime recovered after worker interruption",
          },
        );
      }
      let approvalScenario = false;
      let approvalVisible = false;
      let approvalDecision: "pending" | "approved" | "rejected" = "pending";
      let messages = [scratchMessage, toolMessage];
      let latestUserRequest = "";
      fixtureWindow.__FAIRY_PUSH_EVENT__ = (message) => {
        const startedAt = performance.now();
        const cursor = (events.at(-1)?.cursor ?? 0) + 1;
        events.push({
          ...event,
          id: `0198f4de-0114-7000-8000-${String(100_000_000_000 + cursor)}`,
          cursor,
          run_id: id.commandRun,
          task_sequence: cursor,
          event_type: "turn.trace.step.completed",
          message,
          payload: {
            trace_step_id: `0198f4de-0114-7000-8001-${String(100_000_000_000 + cursor)}`,
            trace_id: projectTraceId,
            turn_id: id.projectTurn,
            sequence: 100 + cursor,
            parent_step_id: null,
            caused_by_step_id: "0198f4de-0114-7000-8000-000000000163",
            kind: "observation",
            status: "succeeded",
            public_summary: message,
            public_detail: null,
            model_role: null,
            artifact_refs: [],
            duration_ms: 1,
          },
          created_at: new Date().toISOString(),
        });
        return startedAt;
      };

      let permissions = {
        profile: "standard",
        capability_overrides: {} as Record<string, boolean>,
        revision: 0,
        updated_at: "2026-07-11T00:00:00Z",
      };
      let openRouterStatus = {
        configured: true,
        account_id: "openrouter-default" as string | null,
      };
      let modelSelection = {
        mode: "auto" as "auto" | "manual",
        model_id: null as string | null,
        allow_free_fallback: false,
        zero_data_retention: false,
        revision: 0,
        updated_at: "2026-07-11T00:00:00Z",
      };
      const modelCatalogFixture = () => ({
        account: {
          account_id: "openrouter-default",
          provider_kind: "openrouter",
          display_name: "OpenRouter",
          credential_status: "configured",
        },
        items: [
          modelEntry("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro", "primary", "chat", true),
          modelEntry("z-ai/glm-5.2", "GLM 5.2", "strongest", "chat", true),
          modelEntry("moonshotai/kimi-k2.7-code", "Kimi K2.7 Code", "code", "chat", true),
          modelEntry("google/gemini-3.1-flash-lite-image", "Gemini 3.1 Flash Lite Image", "image", "images", true),
          modelEntry("google/lyria-3-pro-preview", "Lyria 3 Pro Preview", "music", "audio", true),
          modelEntry("bytedance/seedance-2.0", "Seedance 2.0", "video", "videos", true),
          modelEntry("nvidia/nemotron-3-ultra-550b-a55b:free", "Nemotron 3 Ultra", "free_general", "chat", false),
          modelEntry("qwen/qwen3-coder:free", "Qwen3 Coder", "free_code", "chat", false),
        ],
        fetched_at: "2026-07-11T00:00:00Z",
        expires_at: "2026-07-11T06:00:00Z",
        stale: false,
        revision: 1,
        last_error_code: null,
      });
      const modelEntry = (
        modelId: string,
        displayName: string,
        category: string,
        endpointKind: "chat" | "images" | "audio" | "videos",
        paid: boolean,
      ) => ({
        model_id: modelId,
        display_name: displayName,
        category,
        endpoint_kind: endpointKind,
        description: displayName,
        paid,
        availability: "available",
        unavailable_reason: null,
        input_modalities: ["text"],
        output_modalities: [endpointKind === "chat" ? "text" : endpointKind],
        context_length: endpointKind === "chat" ? 131072 : null,
        max_output_tokens: endpointKind === "chat" ? 16384 : null,
        supports_tools: endpointKind === "chat",
        supports_structured_output: endpointKind === "chat" && paid,
        supports_streaming: endpointKind === "chat",
        supported_resolutions: [],
        supported_aspect_ratios: [],
        prices: [],
      });
      let desktopPreferences = {
        schema_version: 2,
        revision: 0,
        language: "system",
        launch_at_startup: false,
        minimize_to_tray: true,
        theme: "system",
        reduced_motion: false,
        compact_density: false,
        selected_profile_id: null as string | null,
        voice_auto_play_chat: false,
        voice_auto_play_pet: true,
        voice_volume_percent: 80,
        voice_rate_percent: 100,
        permission_cloud_profile: "standard",
        memory_enabled: true,
        memory_retention_days: 90,
        analytics_enabled: false,
        pet_enabled: true,
        pet_always_on_top: true,
        pet_muted: false,
        pet_size_percent: 100,
        pet_opacity_percent: 92,
        pet_motion_enabled: true,
        pet_particles_enabled: true,
        pet_hover_enabled: true,
        pet_hover_dwell_ms: 250,
        pet_do_not_disturb: false,
        pet_remember_position: true,
        pet_renderer_mode: "auto",
        pet_anchor: null,
        developer_mode: false,
      };
      let mcpServer = {
        server_id: "docs",
        display_name: "Document server",
        transport: "streamable_http",
        command: null,
        arguments: [],
        endpoint: "https://mcp.example.test/mcp",
        credential_configured: true,
        environment_names: [],
        enabled: false,
        status: "review_required",
        revision: 2,
        accepted_schema_digest: null,
        pending_schema_digest: "pending-docs-schema",
        accepted_tools: [],
        pending_tools: [
          {
            name: "search",
            title: "Search",
            description: "Search governed documents.",
            input_schema: {
              type: "object",
              properties: { query: { type: "string", maxLength: 200 } },
              required: ["query"],
              additionalProperties: false,
            },
            output_schema: null,
            imported_name: "mcp.docs.search",
            schema_digest: "docs-search-schema",
          },
        ],
        policies: [],
        last_error_code: null,
        created_at: timestamp,
        updated_at: timestamp,
      };
      let permissionConflictPending = scenarios.has("permissionConflict");
      let annotationDocument: Record<string, unknown> | null = null;
      const filePresentation = {
        job: {
          id: id.renderJob,
          workspace_id: id.project,
          version_id: id.version,
          file_set_id: id.fileSet,
          source_path: "src/main.ts",
          source_hash: "fixture-main",
          requested_mode: "native",
          renderer_pack_id: null,
          renderer_pack_version: null,
          cache_key: "fixture-presentation-cache",
          status: "ready",
          progress: 1,
          public_summary: "Native text presentation ready",
          error_code: null,
          created_at: timestamp,
          updated_at: timestamp,
        },
        presentation: {
          id: id.presentation,
          workspace_id: id.project,
          version_id: id.version,
          file_set_id: id.fileSet,
          source_path: "src/main.ts",
          source_hash: "fixture-main",
          renderer: "native.text",
          fidelity: "native",
          status: "ready",
          capabilities: ["select", "annotate"],
          assets: [],
          created_at: timestamp,
        },
      };
      const pdfPresentation = {
        job: {
          ...filePresentation.job,
          id: id.pdfRenderJob,
          file_set_id: id.pdfFileSet,
          source_path: "docs/sample.pdf",
          source_hash: pdfFixtureHash,
          cache_key: "fixture-pdf-presentation-cache",
          public_summary: "Native PDF presentation ready",
        },
        presentation: {
          ...filePresentation.presentation,
          id: id.pdfPresentation,
          file_set_id: id.pdfFileSet,
          source_path: "docs/sample.pdf",
          source_hash: pdfFixtureHash,
          renderer: "browser-native",
          capabilities: ["pages", "search", "zoom", "select", "annotate"],
        },
      };
      const modelPresentation = {
        job: {
          ...filePresentation.job,
          id: "0198f4de-0114-7000-8000-000000000042",
          file_set_id: "0198f4de-0114-7000-8000-000000000043",
          source_path: "models/triangle.gltf",
          source_hash: modelFixtureHash,
          cache_key: "fixture-model-presentation-cache",
          public_summary: "Native 3D presentation ready",
        },
        presentation: {
          ...filePresentation.presentation,
          id: "0198f4de-0114-7000-8000-000000000044",
          file_set_id: "0198f4de-0114-7000-8000-000000000043",
          source_path: "models/triangle.gltf",
          source_hash: modelFixtureHash,
          renderer: "browser-native",
          capabilities: ["orbit", "pan", "zoom", "inspect"],
        },
      };
      const results: Record<string, unknown> = {
        health: {
          status: "ok",
          service: "fairy-core",
          protocol: "core-service-v1",
        },
        "projects.list": { items: [project], next_cursor: null },
        "conversations.list": {
          items: [conversation, scratchConversation],
          next_cursor: null,
        },
        "tasks.list": { items: [task], next_cursor: null },
        "versions.list": { items: [version], next_cursor: null },
        "approvals.list": { items: [], next_cursor: null },
        "asset_sets.list": {
          items: [
            {
              id: "0198f4de-0114-7000-8000-000000000031",
              workspace_id: id.project,
              version_id: id.version,
              kind: "image",
              title: "Generated hero",
              variants: [
                {
                  path: "media/generated.png",
                  content_hash: capturePngHash,
                  byte_length: 68,
                  media_type: "image/png",
                  role: "primary",
                  label: "Primary",
                },
              ],
              provenance: { provider: "fixture", model: "image-test" },
              generation_parameters: { width: 1, height: 1 },
              created_at: timestamp,
            },
          ],
        },
        "workspaces.get": {
          id: id.project,
          active_version_id: id.version,
          active_preview_id: id.preview,
          revision: 1,
          max_files: 200,
          max_bytes: 20 * 1024 * 1024,
          created_at: timestamp,
          updated_at: timestamp,
        },
        "workspaces.files.list": {
          workspace_id: id.project,
          version_id: id.version,
          generation: 1,
          source_hash: "fixture-workspace",
          items: [
            {
              path: "src/main.ts",
              byte_length: 22,
              content_hash: "fixture-main",
              kind: "source",
              language: "typescript",
            },
            {
              path: "docs/sample.pdf",
              byte_length: pdfFixtureByteLength,
              content_hash: pdfFixtureHash,
              kind: "binary",
              language: null,
            },
            {
              path: "media/generated.png",
              byte_length: 68,
              content_hash: capturePngHash,
              kind: "binary",
              language: null,
            },
            {
              path: "models/triangle.gltf",
              byte_length: modelFixtureByteLength,
              content_hash: modelFixtureHash,
              kind: "manifest",
              language: "json",
            },
          ],
        },
        "workspaces.files.read": {
          file: {
            path: "src/main.ts",
            byte_length: 22,
            content_hash: "fixture-main",
            kind: "source",
            language: "typescript",
          },
          media_type: "text/plain",
          text: "console.log('Fairy');",
          content_base64: null,
        },
        "files.present": filePresentation,
        "annotations.list": { document: null },
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
          operations: {
            "model.generate": true,
            "workspace.create_scratch": true,
            "web.search": true,
            "run.sandboxed": false,
          },
          sandbox_healthy: true,
          command_metadata: [
            {
              name: "web.search",
              side_effect: "read",
              risk_level: "low",
              approval_policy: "never",
              profiles: ["observe", "standard", "autonomous"],
              requires_sandbox: false,
              idempotent: true,
              model_visible: true,
              description: "Search public web or news sources.",
              input_schema: { type: "object" },
              definition_digest: "web-search-v3",
              required_extensions: [],
              required_operations: [],
              source: "builtin",
            },
            {
              name: "run.sandboxed",
              side_effect: "execute",
              risk_level: "high",
              approval_policy: "never",
              profiles: ["autonomous"],
              requires_sandbox: true,
              idempotent: false,
              model_visible: true,
              description: "Run structured argv in FairySandbox.",
              input_schema: { type: "object" },
              definition_digest: "run-sandboxed-v3",
              required_extensions: [],
              required_operations: [],
              source: "builtin",
            },
          ],
          slash_commands: [
            {
              name: "new",
              description: "Start a durable conversation.",
              argument_hint: null,
              required_operation: "workspace.create_scratch",
              available: true,
            },
            {
              name: "project",
              description: "Switch to the Project workspace.",
              argument_hint: null,
              required_operation: null,
              available: true,
            },
            {
              name: "permission",
              description: "Change the execution profile.",
              argument_hint: "<observe|standard|autonomous>",
              required_operation: null,
              available: true,
            },
            {
              name: "stop",
              description: "Stop the active response.",
              argument_hint: null,
              required_operation: "model.generate",
              available: true,
            },
            {
              name: "clear",
              description: "Start a durable conversation.",
              argument_hint: null,
              required_operation: "workspace.create_scratch",
              available: true,
            },
            {
              name: "help",
              description: "Show available commands.",
              argument_hint: null,
              required_operation: null,
              available: true,
            },
          ],
          schema_version: 3,
        },
        "skills.list": {
          items: [
            {
              name: "Fairy Docs",
              version: "1.0.0",
              description: "Work with governed project documents.",
              tool_name: "skill.fairy-docs",
              required_capabilities: ["document.search"],
              compatible_mcp_servers: ["docs"],
              provenance: {
                source: "fairy://skills/docs",
                publisher: "Fairy Labs",
                license: "Apache-2.0",
              },
              content_sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              available: false,
            },
          ],
        },
        "providers.list": {
          items: [
            {
              id: "openrouter-deepseek-v4-pro",
              display_name: "DeepSeek V4 Pro",
              kind: "openai_compatible",
              base_url: "https://openrouter.ai/api/v1",
              model_id: "deepseek/deepseek-v4-pro",
              capabilities: ["text", "tools", "structured_output", "stt"],
              credential_required: true,
              credential_configured: true,
              enabled: true,
              timeout_seconds: 60,
              fallback_profile_id: null,
            },
            {
              id: "openrouter-glm-5-2",
              display_name: "GLM 5.2",
              kind: "openai_compatible",
              base_url: "https://openrouter.ai/api/v1",
              model_id: "z-ai/glm-5.2",
              capabilities: ["text", "tools", "structured_output"],
              credential_required: true,
              credential_configured: true,
              enabled: true,
              timeout_seconds: 180,
              fallback_profile_id: "openrouter-deepseek-v4-pro",
            },
            {
              id: "openrouter-kimi-k2-7-code",
              display_name: "Kimi K2.7 Code",
              kind: "openai_compatible",
              base_url: "https://openrouter.ai/api/v1",
              model_id: "moonshotai/kimi-k2.7-code",
              capabilities: ["text", "tools", "vision", "structured_output"],
              credential_required: true,
              credential_configured: true,
              enabled: true,
              timeout_seconds: 180,
              fallback_profile_id: null,
            },
            {
              id: "openrouter-nemotron-free",
              display_name: "Nemotron 3 Ultra",
              kind: "openai_compatible",
              base_url: "https://openrouter.ai/api/v1",
              model_id: "nvidia/nemotron-3-ultra-550b-a55b:free",
              capabilities: ["text", "tools"],
              credential_required: true,
              credential_configured: true,
              enabled: true,
              timeout_seconds: 180,
              fallback_profile_id: null,
            },
            {
              id: "openrouter-qwen3-coder-free",
              display_name: "Qwen3 Coder",
              kind: "openai_compatible",
              base_url: "https://openrouter.ai/api/v1",
              model_id: "qwen/qwen3-coder:free",
              capabilities: ["text", "tools"],
              credential_required: true,
              credential_configured: true,
              enabled: true,
              timeout_seconds: 180,
              fallback_profile_id: null,
            },
          ],
        },
        "providers.health": {
          items: [
            {
              profile_id: "openrouter-deepseek-v4-pro",
              status: "available",
              error_code: null,
              diagnostics: [],
            },
            ...[
              "openrouter-glm-5-2",
              "openrouter-kimi-k2-7-code",
              "openrouter-nemotron-free",
              "openrouter-qwen3-coder-free",
            ].map((profileId) => ({
              profile_id: profileId,
              status: "available",
              error_code: null,
              diagnostics: [],
            })),
          ],
        },
        "models.catalog.list": modelCatalogFixture(),
        "models.catalog.refresh": modelCatalogFixture(),
        "messages.list": {
          items: [scratchMessage, toolMessage],
          next_cursor: null,
        },
        "tasks.create": { task: scratchTask },
        "documents.import": {},
        "documents.list": {
          items: [
            {
              document: {
                id: "0198f4de-0114-7000-8000-000000000101",
                filename: "fixture-notes.md",
                media_type: "text/markdown",
                byte_length: 128,
              },
              revision: {},
            },
          ],
        },
        "documents.search": { items: [] },
        "documents.delete": {},
        "assistant.turns.create": { ...completedTurn, status: "created" },
        "assistant.turns.get": completedTurn,
        "assistant.turns.start": completedTurn,
        "assistant.turns.cancel": { ...completedTurn, status: "cancelled" },
        "assistant.turns.retry": { ...completedTurn, status: "created" },
        "assistant.turns.trace.list": {
          id: "0198f4de-0114-7000-8000-000000000150",
          turn_id: id.turn,
          conversation_id: id.scratchConversation,
          task_id: id.scratchTask,
          legacy: false,
          last_sequence: 4,
          revision: 4,
          created_at: timestamp,
          updated_at: timestamp,
          started_at: timestamp,
          completed_at: timestamp,
          steps: [
            {
              id: "0198f4de-0114-7000-8000-000000000151",
              trace_id: "0198f4de-0114-7000-8000-000000000150",
              turn_id: id.turn,
              sequence: 1,
              parent_step_id: null,
              caused_by_step_id: null,
              kind: "route",
              status: "succeeded",
              public_summary: "Request routed for implementation",
              public_detail: "Kimi implements the request and Fairy verifies the result.",
              model_id: null,
              model_role: null,
              provider_attempt_id: null,
              command_run_id: null,
              artifact_refs: [],
              visibility: "user",
              revision: 1,
              created_at: timestamp,
              updated_at: timestamp,
              started_at: timestamp,
              completed_at: timestamp,
              duration_ms: 20,
            },
            {
              id: "0198f4de-0114-7000-8000-000000000152",
              trace_id: "0198f4de-0114-7000-8000-000000000150",
              turn_id: id.turn,
              sequence: 2,
              parent_step_id: "0198f4de-0114-7000-8000-000000000151",
              caused_by_step_id: "0198f4de-0114-7000-8000-000000000151",
              kind: "model",
              status: "succeeded",
              public_summary: "Implementation generated",
              public_detail: null,
              model_id: "moonshotai/kimi-k2.7-code",
              model_role: "primary",
              provider_attempt_id: "0198f4de-0114-7000-8000-000000000153",
              command_run_id: null,
              artifact_refs: [],
              visibility: "user",
              revision: 1,
              created_at: timestamp,
              updated_at: timestamp,
              started_at: timestamp,
              completed_at: timestamp,
              duration_ms: 420,
            },
            {
              id: "0198f4de-0114-7000-8000-000000000154",
              trace_id: "0198f4de-0114-7000-8000-000000000150",
              turn_id: id.turn,
              sequence: 3,
              parent_step_id: "0198f4de-0114-7000-8000-000000000152",
              caused_by_step_id: "0198f4de-0114-7000-8000-000000000152",
              kind: "tool",
              status: "succeeded",
              public_summary: "Workspace files verified",
              public_detail: null,
              model_id: null,
              model_role: null,
              provider_attempt_id: null,
              command_run_id: id.commandRun,
              artifact_refs: [],
              visibility: "user",
              revision: 1,
              created_at: timestamp,
              updated_at: timestamp,
              started_at: timestamp,
              completed_at: timestamp,
              duration_ms: 80,
            },
            {
              id: "0198f4de-0114-7000-8000-000000000155",
              trace_id: "0198f4de-0114-7000-8000-000000000150",
              turn_id: id.turn,
              sequence: 4,
              parent_step_id: null,
              caused_by_step_id: "0198f4de-0114-7000-8000-000000000154",
              kind: "response",
              status: "succeeded",
              public_summary: "Response ready",
              public_detail: null,
              model_id: null,
              model_role: null,
              provider_attempt_id: null,
              command_run_id: null,
              artifact_refs: [],
              visibility: "user",
              revision: 1,
              created_at: timestamp,
              updated_at: timestamp,
              started_at: timestamp,
              completed_at: timestamp,
              duration_ms: 30,
            },
          ],
        },
        "conversations.create": scratchConversation,
        "voice.transcribe": {
          conversation_id: id.scratchConversation,
          profile_id: "openrouter-deepseek-v4-pro",
          text: "Fixture voice transcript",
          language: "en",
          segments: [],
        },
      };

      const tauriWindow = window as unknown as {
        __TAURI_INTERNALS__: {
          invoke(command: string, args: Record<string, unknown>): Promise<unknown>;
          transformCallback(callback: (payload: unknown) => void): number;
          unregisterCallback(callbackId: number): void;
        };
      };
      let callbackId = 0;
      const callbacks = new Map<number, (payload: unknown) => void>();
      tauriWindow.__TAURI_INTERNALS__ = {
        transformCallback(callback) {
          callbackId += 1;
          callbacks.set(callbackId, callback);
          return callbackId;
        },
        unregisterCallback(id) {
          callbacks.delete(id);
        },
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
              source_label: captureRequest.kind === "window" ? "Game window" : "Primary display",
              media_type: "image/png",
              png_base64: capturePngBase64,
              width: captureRequest.kind === "window" ? 1 : 1920,
              height: captureRequest.kind === "window" ? 1 : 1080,
              byte_length: 68,
              content_hash: capturePngHash,
              captured_at_ms: 1_784_000_000_000,
            };
          }
          if (command === "provider_openrouter_status") {
            return openRouterStatus;
          }
          if (command === "provider_openrouter_configure") {
            openRouterStatus = { configured: true, account_id: "openrouter-default" };
            return openRouterStatus;
          }
          if (command === "provider_openrouter_delete") {
            openRouterStatus = { configured: false, account_id: null };
            return openRouterStatus;
          }
          if (command === "desktop_preferences_get") {
            return desktopPreferences;
          }
          if (command === "desktop_preferences_update") {
            const input = args.input as {
              expected_revision: number;
              preferences: typeof desktopPreferences;
            };
            if (input.expected_revision !== desktopPreferences.revision) {
              throw new Error("PREFERENCES_REVISION_CONFLICT");
            }
            desktopPreferences = {
              ...input.preferences,
              revision: desktopPreferences.revision + 1,
            };
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: "desktop.preferences.update",
              params: input as unknown as Record<string, unknown>,
            });
            return desktopPreferences;
          }
          if (command === "voice_session_start") {
            const input = args.input as Record<string, unknown>;
            const events = args.events as {
              onmessage(payload: Record<string, unknown>): void;
            };
            const audio = args.audio as {
              onmessage(payload: ArrayBuffer): void;
            };
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: "voice.sessions.start",
              params: input,
            });
            const sessionId = "0198f4de-0114-7000-8000-000000000099";
            events.onmessage({
              type: "started",
              session_id: sessionId,
              sample_rate: 24_000,
              channels: 1,
              scope_digest: "a".repeat(64),
            });
            audio.onmessage(new Int16Array([0, 0]).buffer);
            events.onmessage({
              type: "completed",
              session_id: sessionId,
              pcm_bytes: 4,
            });
            return { id: sessionId };
          }
          if (command === "voice_session_cancel") return null;
          if (command === "open_settings_window") {
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: "desktop.settings.open",
              params: {},
            });
            return null;
          }
          if (command === "select_project_folder") {
            return "C:\\Projects\\fixture";
          }
          if (command !== "core_rpc" && command !== "settings_rpc") {
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
          if (request.method === "permissions.update" && permissionConflictPending) {
            permissionConflictPending = false;
            permissions = {
              profile: "observe",
              capability_overrides: { "web.search": false },
              revision: permissions.revision + 1,
              updated_at: "2026-07-11T00:00:01Z",
            };
            return {
              jsonrpc: "2.0",
              id: request.id,
              error: {
                code: -32000,
                message: "stale permission revision",
                data: { error_code: "VERSION_CONFLICT" },
              },
            };
          }
          if (request.method === "tasks.create") {
            await new Promise((resolve) => window.setTimeout(resolve, 180));
          }
          if (request.method === "models.selection.get") {
            return { jsonrpc: "2.0", id: request.id, result: modelSelection };
          }
          if (request.method === "models.selection.update") {
            modelSelection = {
              mode: request.params.mode as "auto" | "manual",
              model_id: (request.params.model_id as string | null | undefined) ?? null,
              allow_free_fallback: Boolean(request.params.allow_free_fallback),
              zero_data_retention: Boolean(request.params.zero_data_retention),
              revision: Number(request.params.expected_revision) + 1,
              updated_at: "2026-07-11T00:00:01Z",
            };
            return { jsonrpc: "2.0", id: request.id, result: modelSelection };
          }
          const result =
            request.method === "projects.list"
              ? { items: [{ ...project }], next_cursor: null }
              : request.method === "tasks.list"
                ? {
                    items: [{ ...task }, { ...scratchTask }],
                    next_cursor: null,
                  }
                : request.method === "workspaces.get"
                  ? {
                      ...(results["workspaces.get"] as Record<string, unknown>),
                      id: request.params.workspace_id,
                      active_version_id:
                        request.params.workspace_id === id.scratchConversation ? id.scratchVersion : id.version,
                      active_preview_id: request.params.workspace_id === id.scratchConversation ? null : id.preview,
                    }
                  : request.method === "workspaces.files.list"
                    ? {
                        ...(results["workspaces.files.list"] as Record<string, unknown>),
                        workspace_id: request.params.workspace_id,
                        version_id: request.params.version_id,
                      }
                    : request.method === "file_sets.resolve"
                      ? {
                          id: "0198f4de-0114-7000-8000-000000000043",
                          workspace_id: request.params.workspace_id,
                          version_id: request.params.version_id,
                          kind: request.params.path === "models/triangle.gltf" ? "gltf" : "single",
                          primary_path: request.params.path,
                          parser_version: "1.0.0",
                          manifest_hash: "a".repeat(64),
                          members: [
                            {
                              path: request.params.path,
                              content_hash:
                                request.params.path === "models/triangle.gltf" ? modelFixtureHash : "a".repeat(64),
                              byte_length:
                                request.params.path === "models/triangle.gltf" ? modelFixtureByteLength : 1,
                              role: "primary",
                            },
                          ],
                          missing_dependencies: [],
                          blocked_dependencies: [],
                        }
                      : request.method === "workspaces.files.read" && request.params.path === "models/triangle.gltf"
                        ? {
                            file: {
                              path: "models/triangle.gltf",
                              byte_length: modelFixtureByteLength,
                              content_hash: modelFixtureHash,
                              kind: "manifest",
                              language: "json",
                            },
                            media_type: "model/gltf+json",
                            text: modelFixtureText,
                            content_base64: null,
                            stream_required: false,
                          }
                    : request.method === "workspaces.files.read" && request.params.path === "docs/sample.pdf"
                      ? {
                          file: {
                            path: "docs/sample.pdf",
                            byte_length: pdfFixtureByteLength,
                            content_hash: pdfFixtureHash,
                            kind: "binary",
                            language: null,
                          },
                          media_type: "application/pdf",
                          text: null,
                          content_base64: null,
                          stream_required: true,
                        }
                      : request.method === "workspaces.files.read" && request.params.path === "media/generated.png"
                        ? {
                            file: {
                              path: "media/generated.png",
                              byte_length: 68,
                              content_hash: capturePngHash,
                              kind: "binary",
                              language: null,
                            },
                            media_type: "image/png",
                            text: null,
                            content_base64: null,
                            stream_required: true,
                          }
                        : request.method === "files.open_stream"
                          ? {
                              session_id: "0198f4de-0114-7000-8000-000000000030",
                              workspace_id: request.params.workspace_id,
                              version_id: request.params.version_id,
                              path: request.params.path,
                              content_hash:
                                request.params.path === "media/generated.png"
                                  ? capturePngHash
                                  : request.params.path === "models/triangle.gltf"
                                    ? modelFixtureHash
                                    : pdfFixtureHash,
                              byte_length:
                                request.params.path === "media/generated.png"
                                  ? 68
                                  : request.params.path === "models/triangle.gltf"
                                    ? modelFixtureByteLength
                                    : pdfFixtureByteLength,
                              media_type:
                                request.params.path === "media/generated.png"
                                  ? "image/png"
                                  : request.params.path === "models/triangle.gltf"
                                    ? "model/gltf+json"
                                    : "application/pdf",
                              url: `${previewUrl}${
                                request.params.path === "media/generated.png"
                                  ? "fixture.png"
                                  : request.params.path === "models/triangle.gltf"
                                    ? "fixture.gltf"
                                    : "fixture.pdf"
                              }`,
                              expires_at: "2026-07-11T00:02:00Z",
                            }
                          : request.method === "files.present"
                            ? request.params.path === "docs/sample.pdf"
                              ? pdfPresentation
                              : request.params.path === "models/triangle.gltf"
                                ? modelPresentation
                                : filePresentation
                            : request.method === "annotations.list"
                              ? { document: annotationDocument }
                              : request.method === "annotations.update"
                                ? (() => {
                                    annotationDocument = {
                                      id: id.annotation,
                                      workspace_id: request.params.workspace_id,
                                      version_id: request.params.version_id,
                                      file_set_id: request.params.file_set_id,
                                      source_hash: request.params.source_hash,
                                      annotations: request.params.annotations,
                                      revision: Number(request.params.expected_revision) + 1,
                                      created_at: timestamp,
                                      updated_at: timestamp,
                                    };
                                    return annotationDocument;
                                  })()
                                : request.method === "selections.create"
                                  ? {
                                      id: id.selection,
                                      workspace_id: request.params.workspace_id,
                                      version_id: request.params.version_id,
                                      file_set_id: request.params.file_set_id,
                                      source_path: request.params.source_path,
                                      source_hash: request.params.source_hash,
                                      viewer_kind: request.params.viewer_kind,
                                      locator_kind: request.params.locator_kind,
                                      locator: request.params.locator,
                                      created_at: timestamp,
                                    }
                                  : request.method === "previews.resolve"
                                    ? request.params.task_id === id.scratchTask
                                      ? null
                                      : {
                                          task: { ...task },
                                          runtime: { ...runtime },
                                          preview: { ...preview },
                                        }
                                    : request.method === "voice.synthesize"
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
                                            latestUserRequest = userRequest;
                                            approvalScenario = userRequest === "Request a governed notification";
                                            approvalVisible = false;
                                            approvalDecision = "pending";
                                            messages = [scratchMessage, toolMessage];
                                            return {
                                              task: {
                                                ...scratchTask,
                                                user_request: userRequest,
                                              },
                                            };
                                          })()
                                        : request.method === "assistant.turns.create"
                                          ? (() => {
                                              messages = [
                                                ...messages,
                                                {
                                                  ...scratchMessage,
                                                  id: "0198f4de-0114-7000-8000-000000000030",
                                                  task_id: id.scratchTask,
                                                  turn_id: id.turn,
                                                  sequence: 3,
                                                  role: "user",
                                                  content: latestUserRequest,
                                                },
                                              ];
                                              return {
                                                ...completedTurn,
                                                status: "created",
                                                completed_at: null,
                                              };
                                            })()
                                          : request.method === "assistant.turns.start"
                                            ? (() => {
                                                if (!approvalScenario) {
                                                  messages = [
                                                    ...messages,
                                                    {
                                                      ...resumedMessage,
                                                      id: "0198f4de-0114-7000-8000-000000000031",
                                                      sequence: 4,
                                                      content: "Fixture streamed response completed",
                                                    },
                                                  ];
                                                  return completedTurn;
                                                }
                                                if (approvalDecision === "pending") {
                                                  approvalVisible = true;
                                                  return waitingTurn;
                                                }
                                                if (!messages.some((message) => message.id === resumedMessage.id)) {
                                                  messages = [...messages, resumedMessage];
                                                }
                                                return completedTurn;
                                              })()
                                            : request.method === "assistant.turns.trace.list"
                                              ? request.params.turn_id === id.projectTurn
                                                ? projectTrace
                                                : results[request.method]
                                            : request.method === "messages.list"
                                              ? {
                                                  items:
                                                    request.params.conversation_id === id.conversation
                                                      ? [projectMessage]
                                                      : messages,
                                                  next_cursor: null,
                                                }
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
                                                      approvalDecision = request.params.approved
                                                        ? "approved"
                                                        : "rejected";
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
                                                        task = {
                                                          ...task,
                                                          status: "ready",
                                                        };
                                                        const cursor = (events.at(-1)?.cursor ?? 0) + 1;
                                                        events.push({
                                                          ...event,
                                                          id: `0198f4de-0114-7000-8000-${String(100_000_000_000 + cursor)}`,
                                                          cursor,
                                                          task_sequence: cursor,
                                                          event_type: "task.reviewed",
                                                          message: "Review complete",
                                                          payload: {
                                                            status: "ready",
                                                            checkpoint_id: id.checkpoint,
                                                          },
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
                                                    : request.method === "versions.accept"
                                                      ? (() => {
                                                          task = {
                                                            ...task,
                                                            status: "accepted",
                                                          };
                                                          project = {
                                                            ...project,
                                                            active_version_id: id.version,
                                                            revision: project.revision + 1,
                                                          };
                                                          return project;
                                                        })()
                                                      : request.method === "permissions.get"
                                                        ? permissions
                                                        : request.method === "permissions.update"
                                                          ? (() => {
                                                              if (
                                                                request.params.expected_revision !==
                                                                permissions.revision
                                                              ) {
                                                                throw new Error("VERSION_CONFLICT");
                                                              }
                                                              permissions = {
                                                                profile: String(request.params.profile),
                                                                capability_overrides:
                                                                  (request.params.capability_overrides as Record<
                                                                    string,
                                                                    boolean
                                                                  >) ?? {},
                                                                revision: permissions.revision + 1,
                                                                updated_at: "2026-07-11T00:00:01Z",
                                                              };
                                                              return permissions;
                                                            })()
                                                          : request.method === "capabilities.get"
                                                            ? (() => {
                                                                const base = results["capabilities.get"] as {
                                                                  slash_commands: Array<{
                                                                    required_operation: string | null;
                                                                    available: boolean;
                                                                  }>;
                                                                  [key: string]: unknown;
                                                                };
                                                                const operations: Record<string, boolean> = {
                                                                  "model.generate": true,
                                                                  "workspace.create_scratch":
                                                                    permissions.profile !== "observe",
                                                                  "web.search":
                                                                    permissions.capability_overrides["web.search"] !==
                                                                    false,
                                                                  "run.sandboxed":
                                                                    permissions.profile === "autonomous" &&
                                                                    !scenarios.has("sandboxUnavailable") &&
                                                                    permissions.capability_overrides[
                                                                      "run.sandboxed"
                                                                    ] !== false,
                                                                };
                                                                return {
                                                                  ...base,
                                                                  profile: permissions.profile,
                                                                  operations,
                                                                  sandbox_healthy: !scenarios.has("sandboxUnavailable"),
                                                                  slash_commands: base.slash_commands.map(
                                                                    (command) => ({
                                                                      ...command,
                                                                      available:
                                                                        command.required_operation === null ||
                                                                        operations[command.required_operation] === true,
                                                                    }),
                                                                  ),
                                                                };
                                                              })()
                                                            : request.method === "mcp.servers.list"
                                                              ? {
                                                                  items: mcpServer === null ? [] : [mcpServer],
                                                                }
                                                              : request.method === "mcp.servers.accept"
                                                                ? (() => {
                                                                    if (
                                                                      mcpServer === null ||
                                                                      request.params.server_id !==
                                                                        mcpServer.server_id ||
                                                                      request.params.expected_revision !==
                                                                        mcpServer.revision ||
                                                                      request.params.schema_digest !==
                                                                        mcpServer.pending_schema_digest
                                                                    ) {
                                                                      throw new Error(
                                                                        "MCP acceptance revision mismatch",
                                                                      );
                                                                    }
                                                                    mcpServer = {
                                                                      ...mcpServer,
                                                                      enabled: Boolean(request.params.enabled),
                                                                      status: request.params.enabled
                                                                        ? "ready"
                                                                        : "disabled",
                                                                      revision: mcpServer.revision + 1,
                                                                      accepted_schema_digest:
                                                                        mcpServer.pending_schema_digest,
                                                                      accepted_tools: mcpServer.pending_tools,
                                                                      policies: request.params.tools,
                                                                      updated_at: "2026-07-11T00:00:01Z",
                                                                    };
                                                                    return mcpServer;
                                                                  })()
                                                                : request.method === "events.subscribe"
                                                                  ? (() => {
                                                                      const cursor = Number(request.params.cursor ?? 0);
                                                                      const items = events.filter(
                                                                        (item) => item.cursor > cursor,
                                                                      );
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
      pdfFixtureHash: PDF_FIXTURE_HASH,
      pdfFixtureByteLength: PDF_FIXTURE.length,
      modelFixtureText: MODEL_FIXTURE_TEXT,
      modelFixtureHash: MODEL_FIXTURE_HASH,
      modelFixtureByteLength: MODEL_FIXTURE.length,
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
