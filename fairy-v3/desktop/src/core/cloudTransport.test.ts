import { describe, expect, it, vi } from "vitest";

import { CoreClient } from "./client";
import { CloudCoreTransport } from "./cloudTransport";

const EVENT = {
  id: "0198f4de-0114-7000-8000-000000000001",
  cursor: 5,
  run_id: null,
  project_id: null,
  conversation_id: "0198f4de-0114-7000-8000-000000000002",
  task_id: "0198f4de-0114-7000-8000-000000000003",
  version_id: null,
  task_sequence: 1,
  event_type: "task.created",
  visibility: "user",
  message: "Task created",
  payload: {},
  schema_version: 1,
  created_at: "2026-07-10T00:00:00Z",
};

const CLAIM_CONTEXT = {
  claim: {
    id: "0198f4de-0114-7000-8000-000000000004",
    namespace: "conversation_draft",
    project_id: null,
    conversation_id: "0198f4de-0114-7000-8000-000000000002",
    task_id: "0198f4de-0114-7000-8000-000000000003",
    version_id: null,
    device_id: null,
    subject: "project",
    predicate: "framework",
    current_revision: 1,
    conflict_set_id: null,
    status: "active",
    created_at: "2026-07-10T00:00:00Z",
    updated_at: "2026-07-10T00:00:00Z",
  },
  current_revision: {
    claim_id: "0198f4de-0114-7000-8000-000000000004",
    revision: 1,
    value: "React 19",
    normalized_text: "react 19",
    source_observation_ids: ["0198f4de-0114-7000-8000-000000000005"],
    source_event_ids: ["0198f4de-0114-7000-8000-000000000001"],
    authority: "explicit_user",
    confidence: 1,
    valid_from: null,
    valid_to: null,
    recorded_at: "2026-07-10T00:00:00Z",
    actor: "user:core-client",
    supersedes_revision: null,
    resolved_claim_ids: [],
  },
};

const SNAPSHOT = {
  id: "0198f4de-0114-7000-8000-000000000006",
  project_id: null,
  conversation_id: "0198f4de-0114-7000-8000-000000000002",
  task_id: "0198f4de-0114-7000-8000-000000000003",
  base_version_id: null,
  target_version_id: null,
  snapshot_version: 1,
  policy_version: "hermes-lexical-v1",
  source_watermark_cursor: 5,
  projection_generation: 1,
  projection_watermark_cursor: 4,
  projection_state: "stale",
  status: "degraded",
  degraded_reason: "MEMORY_PROJECTION_STALE",
  content_hash: "a".repeat(64),
  token_count: 0,
  items: [],
  created_at: "2026-07-10T00:00:00Z",
};

const PROJECTION_HEALTH = {
  generation: 1,
  state: "stale",
  source_watermark_cursor: 5,
  projected_watermark_cursor: 4,
  lag: 1,
  last_error_code: "MEMORY_PROJECTION_STALE",
  updated_at: "2026-07-10T00:00:00Z",
};

const RUNTIME = {
  id: "0198f4de-0114-7000-8000-000000000010",
  project_id: "0198f4de-0114-7000-8000-000000000011",
  workspace_id: "0198f4de-0114-7000-8000-000000000011",
  conversation_id: "0198f4de-0114-7000-8000-000000000002",
  task_id: "0198f4de-0114-7000-8000-000000000003",
  version_id: "0198f4de-0114-7000-8000-000000000012",
  project_root: "C:/managed/version",
  execution_target: "local",
  kind: "static_site",
  graph: {
    public_service_id: "app",
    services: [
      {
        service_id: "app",
        adapter: "static",
        cwd: ".",
        readiness_path: "/",
        depends_on: [],
      },
    ],
  },
  executor: "rust_local_worker",
  executor_handle: "static:0198f4de-0114-7000-8000-000000000013",
  port: 43125,
  status: "running",
  health: "healthy",
  error_code: null,
  idempotency_key: "preview:runtime",
  revision: 2,
  created_at: "2026-07-10T00:00:00Z",
  updated_at: "2026-07-10T00:00:00Z",
};

const PREVIEW = {
  id: "0198f4de-0114-7000-8000-000000000013",
  project_id: RUNTIME.project_id,
  workspace_id: RUNTIME.workspace_id,
  conversation_id: RUNTIME.conversation_id,
  task_id: RUNTIME.task_id,
  version_id: RUNTIME.version_id,
  runtime_id: RUNTIME.id,
  project_root: RUNTIME.project_root,
  execution_target: "local",
  url: "http://127.0.0.1:43125/0198f4de-0114-7000-8000-000000000013/",
  visibility: "chat_draft",
  status: "ready",
  health: "healthy",
  error_code: null,
  idempotency_key: "preview:start",
  revision: 2,
  last_accessed_at: "2026-07-10T00:00:00Z",
  created_at: "2026-07-10T00:00:00Z",
  updated_at: "2026-07-10T00:00:00Z",
};

const PREVIEW_CONTEXT = {
  task: {
    id: RUNTIME.task_id,
    conversation_id: RUNTIME.conversation_id,
    status: "previewing",
  },
  runtime: RUNTIME,
  preview: PREVIEW,
};

describe("CloudCoreTransport", () => {
  it("rejects device-local Obsidian methods before issuing a cloud request", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: fetcher,
    });

    await expect(
      transport.call("obsidian.sources.create", {
        project_id: "0198f4de-0114-7000-8000-000000000001",
        display_name: "Local Vault",
        local_path_token: "0198f4de-0114-7000-8000-000000000002",
        read_scope: "selected_directories",
        allowed_directories: ["Notes"],
        whole_vault_confirmed: false,
        managed_directory: "Fairy",
        mode: "read_only",
        idempotency_key: "obsidian:local-only",
      }),
    ).rejects.toMatchObject({
      name: "CloudCoreError",
      status: 400,
      errorCode: "CAPABILITY_NOT_AVAILABLE",
    });
    await expect(
      transport.call("knowledge.sync.start", {
        source_id: "0198f4de-0114-7000-8000-000000000001",
        expected_revision: 1,
        idempotency_key: "knowledge-sync:local-only",
      }),
    ).rejects.toMatchObject({
      name: "CloudCoreError",
      status: 400,
      errorCode: "CAPABILITY_NOT_AVAILABLE",
    });
    await expect(
      transport.call("ambient.dialogue.evaluate", {
        context: {
          observed_at: "2026-07-23T09:00:00Z",
          surface: "pet",
          startup_eligible: true,
        },
      }),
    ).rejects.toMatchObject({
      name: "CloudCoreError",
      status: 400,
      errorCode: "CAPABILITY_NOT_AVAILABLE",
    });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("maps typed Core calls to authenticated REST requests", async () => {
    const requests: Request[] = [];
    const fetcher: typeof fetch = async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);
      if (request.url.includes("/v1/memory/observations?")) {
        return Response.json({ items: [] });
      }
      if (request.url.includes("/v1/memory/search?")) {
        return Response.json({ items: [] });
      }
      if (request.url.includes("/v1/memory/snapshots/")) {
        return Response.json(SNAPSHOT);
      }
      if (request.url.includes("/v1/memory/projection/health?")) {
        return Response.json(PROJECTION_HEALTH);
      }
      if (request.url.endsWith("/stop")) {
        return Response.json({
          ...PREVIEW,
          url: null,
          status: "stopped",
          health: "stopped",
          revision: 4,
        });
      }
      if (request.url.endsWith("/v1/previews/activate")) {
        return Response.json({
          outcome: "ready",
          context: PREVIEW_CONTEXT,
          adapter: "static",
          capacity: 3,
          active_count: 1,
          evicted_preview_id: null,
          public_reason: null,
        });
      }
      if (request.url.includes("/v1/previews/")) {
        return Response.json(PREVIEW_CONTEXT);
      }
      if (request.url.includes("/v1/memory/claims/")) {
        return Response.json(CLAIM_CONTEXT);
      }
      return Response.json({ id: "result" });
    };
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test/",
      accessToken: () => "access-token",
      deviceId: "device-1",
      fetch: fetcher,
    });

    await transport.call("projects.get", { project_id: "project/a" });
    await transport.call("projects.list", { limit: 25, cursor: "next page" });
    await transport.call("approvals.decide", {
      approval_id: "approval-1",
      approved: true,
    });
    await transport.call("capabilities.get", {});
    await transport.call("permissions.get", {});
    await transport.call("permissions.update", {
      profile: "standard",
      capability_overrides: { "web.search": false },
      expected_revision: 0,
      idempotency_key: "permissions:cloud:standard",
    });
    await transport.call("providers.list", {});
    await transport.call("providers.health", { profile_id: "openrouter-free" });
    await transport.call("memory.observations.list", {
      task_id: "task/1",
      namespace: "conversation_draft",
    });
    await transport.call("memory.claims.get", {
      task_id: "task/1",
      claim_id: "claim/1",
    });
    await transport.call("memory.claims.supersede", {
      task_id: "task-1",
      claim_id: "claim/1",
      expected_revision: 1,
      source_observation_ids: ["observation-1"],
      value: "React 19",
      normalized_text: "react 19",
      user_confirmed: true,
      idempotency_key: "memory-supersede-1",
    });
    await transport.call("memory.search", {
      task_id: "task/1",
      query: "compact navigation",
      limit: 10,
    });
    await transport.call("memory.snapshots.get", {
      task_id: "task/1",
      snapshot_id: "snapshot/1",
    });
    await transport.call("memory.projection.health", { task_id: "task/1" });
    await transport.call("previews.activate", {
      task_id: RUNTIME.task_id,
      workspace_id: RUNTIME.workspace_id,
      version_id: RUNTIME.version_id,
      expected_workspace_revision: 0,
      idempotency_key: "preview:activate",
    });
    await transport.call("previews.start", {
      task_id: RUNTIME.task_id,
      workspace_id: RUNTIME.workspace_id,
      version_id: RUNTIME.version_id,
      expected_workspace_revision: 0,
      idempotency_key: "preview:start",
    });
    await transport.call("previews.stop", {
      preview_id: PREVIEW.id,
      task_id: RUNTIME.task_id,
      workspace_id: RUNTIME.workspace_id,
      version_id: RUNTIME.version_id,
      expected_workspace_revision: 0,
      idempotency_key: "preview:stop",
    });
    await transport.call("assistant.turns.run", { turn_id: "turn/1" });
    await transport.call("assistant.turns.start", { turn_id: "turn/1" });
    await transport.call("assistant.turns.pause", { turn_id: "turn/1" });
    await transport.call("assistant.turns.resume", { turn_id: "turn/1" });
    await transport.call("assistant.turns.interpretation.get", {
      turn_id: "turn/1",
      revision: 2,
    });
    await transport.call("assistant.turns.respond", {
      turn_id: "turn/1",
      content: "Use src/app.ts",
      expected_interpretation_revision: 2,
      idempotency_key: "turn:respond",
    });
    await transport.call("assistant.turns.steer", {
      turn_id: "turn/1",
      instruction: "Use the updated requirement",
      expected_revision: 1,
      idempotency_key: "turn:steer",
    });
    await transport.call("assistant.turns.workflow.get", { turn_id: "turn/1" });
    await transport.call("assistant.turns.retry", {
      turn_id: "turn/1",
      idempotency_key: "turn:retry",
    });
    await transport.call("assistant.turns.trace.list", { turn_id: "turn/1" });
    await transport.call("documents.import", {
      task_id: "task/1",
      filename: "evidence.txt",
      media_type: "text/plain",
      content_base64: "RmFpcnk=",
      visibility: "conversation",
      idempotency_key: "documents:import",
      user_confirmed: true,
    });
    await transport.call("documents.list", { task_id: "task/1", limit: 25 });
    await transport.call("documents.get", {
      task_id: "task/1",
      document_id: "document/1",
    });
    await transport.call("documents.search", {
      task_id: "task/1",
      query: "evidence",
      limit: 10,
    });
    await transport.call("documents.delete", {
      task_id: "task/1",
      document_id: "document/1",
      idempotency_key: "documents:delete",
      user_confirmed: true,
    });
    await transport.call("voice.transcribe", {
      conversation_id: "conversation-1",
      profile_id: "voice",
      media_type: "audio/webm",
      audio_base64: "cmVjb3JkaW5n",
      language: null,
    });
    await transport.call("voice.synthesize", {
      task_id: "task-1",
      turn_id: "turn-1",
      message_id: "message-1",
      profile_id: "voice",
      voice: "alloy",
      start_offset: 0,
      end_offset: 6,
    });
    await transport.call("system.actions.execute", {
      task_id: "task-1",
      action: { type: "reveal_path", relative_path: "README.md" },
      idempotency_key: "system:reveal",
      user_confirmed: true,
    });
    await transport.call("files.compare", {
      workspace_id: "workspace-1",
      left_version_id: "version-1",
      right_version_id: "version-2",
    });

    expect(requests.map(({ method, url }) => [method, url])).toEqual([
      ["GET", "https://cloud.fairy.test/v1/projects/project%2Fa"],
      [
        "GET",
        "https://cloud.fairy.test/v1/projects?limit=25&cursor=next+page",
      ],
      ["POST", "https://cloud.fairy.test/v1/approvals/approval-1/decision"],
      ["GET", "https://cloud.fairy.test/v1/capabilities"],
      ["GET", "https://cloud.fairy.test/v1/permissions"],
      ["PUT", "https://cloud.fairy.test/v1/permissions"],
      ["GET", "https://cloud.fairy.test/v1/providers"],
      [
        "GET",
        "https://cloud.fairy.test/v1/providers/health?profile_id=openrouter-free",
      ],
      [
        "GET",
        "https://cloud.fairy.test/v1/memory/observations?task_id=task%2F1&namespace=conversation_draft",
      ],
      [
        "GET",
        "https://cloud.fairy.test/v1/memory/claims/claim%2F1?task_id=task%2F1",
      ],
      [
        "POST",
        "https://cloud.fairy.test/v1/memory/claims/claim%2F1/supersede",
      ],
      [
        "GET",
        "https://cloud.fairy.test/v1/memory/search?task_id=task%2F1&query=compact%20navigation&limit=10",
      ],
      [
        "GET",
        "https://cloud.fairy.test/v1/memory/snapshots/snapshot%2F1?task_id=task%2F1",
      ],
      [
        "GET",
        "https://cloud.fairy.test/v1/memory/projection/health?task_id=task%2F1",
      ],
      ["POST", "https://cloud.fairy.test/v1/previews/activate"],
      ["POST", "https://cloud.fairy.test/v1/previews/start"],
      [
        "POST",
        `https://cloud.fairy.test/v1/previews/${PREVIEW.id}/stop`,
      ],
      ["POST", "https://cloud.fairy.test/v1/assistant/turns/turn%2F1/run"],
      ["POST", "https://cloud.fairy.test/v1/assistant/turns/turn%2F1/start"],
      ["POST", "https://cloud.fairy.test/v1/assistant/turns/turn%2F1/pause"],
      ["POST", "https://cloud.fairy.test/v1/assistant/turns/turn%2F1/resume"],
      ["GET", "https://cloud.fairy.test/v1/assistant/turns/turn%2F1/interpretation?revision=2"],
      ["POST", "https://cloud.fairy.test/v1/assistant/turns/turn%2F1/respond"],
      ["POST", "https://cloud.fairy.test/v1/assistant/turns/turn%2F1/steer"],
      ["GET", "https://cloud.fairy.test/v1/assistant/turns/turn%2F1/workflow"],
      ["POST", "https://cloud.fairy.test/v1/assistant/turns/turn%2F1/retry"],
      ["GET", "https://cloud.fairy.test/v1/assistant/turns/turn%2F1/trace"],
      ["POST", "https://cloud.fairy.test/v1/documents/import"],
      [
        "GET",
        "https://cloud.fairy.test/v1/documents?task_id=task%2F1&limit=25",
      ],
      [
        "GET",
        "https://cloud.fairy.test/v1/documents/document%2F1?task_id=task%2F1",
      ],
      ["POST", "https://cloud.fairy.test/v1/documents/search"],
      [
        "POST",
        "https://cloud.fairy.test/v1/documents/document%2F1/delete",
      ],
      ["POST", "https://cloud.fairy.test/v1/voice/transcriptions"],
      ["POST", "https://cloud.fairy.test/v1/voice/speech"],
      ["POST", "https://cloud.fairy.test/v1/system/actions"],
      ["POST", "https://cloud.fairy.test/v1/files/compare"],
    ]);
    expect(requests[0]?.headers.get("Authorization")).toBe("Bearer access-token");
    expect(requests[0]?.headers.get("X-Fairy-Device-ID")).toBe("device-1");
    expect(requests[14]?.headers.get("Idempotency-Key")).toBe("preview:activate");
    expect(requests[15]?.headers.get("Idempotency-Key")).toBe("preview:start");
    expect(requests[16]?.headers.get("Idempotency-Key")).toBe("preview:stop");
    expect(requests[22]?.headers.get("Idempotency-Key")).toBe("turn:respond");
    expect(requests[23]?.headers.get("Idempotency-Key")).toBe("turn:steer");
    expect(requests[25]?.headers.get("Idempotency-Key")).toBe("turn:retry");
    expect(requests[27]?.headers.get("Idempotency-Key")).toBe("documents:import");
    expect(requests[31]?.headers.get("Idempotency-Key")).toBe("documents:delete");
    expect(requests[34]?.headers.get("Idempotency-Key")).toBe("system:reveal");
    expect(requests[5]?.headers.get("Idempotency-Key")).toBe(
      "permissions:cloud:standard",
    );
    await expect(requests[31]?.json()).resolves.not.toHaveProperty("text");
    expect(requests[3]?.headers.has("Content-Type")).toBe(false);
    await expect(requests[5]?.json()).resolves.toEqual({
      profile: "standard",
      capability_overrides: { "web.search": false },
      expected_revision: 0,
      idempotency_key: "permissions:cloud:standard",
    });
    await expect(requests[34]?.json()).resolves.toEqual({
      task_id: "task-1",
      action: { type: "reveal_path", relative_path: "README.md" },
      idempotency_key: "system:reveal",
      user_confirmed: true,
    });
  });

  it("sends generated Asset Set mutations with an idempotency header", async () => {
    let request: Request | undefined;
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: async (input, init) => {
        request = new Request(input, init);
        return Response.json({});
      },
    });

    await transport.call("asset_sets.create", {
      workspace_id: "0198f4de-0114-7000-8000-000000000010",
      version_id: "0198f4de-0114-7000-8000-000000000011",
      idempotency_key: "asset-set:image:1",
      kind: "image",
      title: "Image variants",
      variants: [],
      provenance: {},
      generation_parameters: {},
    });

    expect(request?.url).toBe("https://cloud.fairy.test/v1/asset-sets");
    expect(request?.headers.get("Idempotency-Key")).toBe("asset-set:image:1");
  });

  it("maps media jobs to endpoint-specific REST routes", async () => {
    const requests: Request[] = [];
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: async (input, init) => {
        requests.push(new Request(input, init));
        return Response.json({});
      },
    });

    await transport.call("media.images.generate", {
      task_id: "task-1",
      prompt: "Fairy image",
      size: "1024x1024",
      aspect_ratio: "1:1",
      idempotency_key: "media:image:1",
      user_confirmed: true,
    });
    await transport.call("media.jobs.list", { task_id: "task/1" });
    await transport.call("media.audio.generate", {
      task_id: "task-1",
      prompt: "Fairy theme",
      output_format: "wav",
      idempotency_key: "media:music:1",
      user_confirmed: true,
    });
    await transport.call("media.videos.start", {
      task_id: "task-1",
      prompt: "Fairy motion",
      duration_seconds: 5,
      resolution: "720p",
      aspect_ratio: "16:9",
      generate_audio: true,
      idempotency_key: "media:video:1",
      user_confirmed: true,
    });
    await transport.call("media.videos.get", { job_id: "job/1" });
    await transport.call("media.videos.cancel", {
      job_id: "job/1",
      expected_revision: 2,
      idempotency_key: "media:video:cancel:1",
      user_confirmed: true,
    });

    expect(requests.map(({ method, url }) => [method, url])).toEqual([
      ["POST", "https://cloud.fairy.test/v1/media/images"],
      ["GET", "https://cloud.fairy.test/v1/media/jobs?task_id=task%2F1"],
      ["POST", "https://cloud.fairy.test/v1/media/audio"],
      ["POST", "https://cloud.fairy.test/v1/media/videos"],
      ["GET", "https://cloud.fairy.test/v1/media/videos/job%2F1"],
      ["DELETE", "https://cloud.fairy.test/v1/media/videos/job%2F1"],
    ]);
    expect(requests.map((request) => request.headers.get("Idempotency-Key"))).toEqual([
      "media:image:1",
      null,
      "media:music:1",
      "media:video:1",
      null,
      "media:video:cancel:1",
    ]);
  });

  it("binds edit recipe apply to the body and HTTP idempotency key", async () => {
    let request: Request | undefined;
    const id = "0198f4de-0114-7000-8000-000000000020";
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test/",
      accessToken: () => "access-token",
      deviceId: "device-1",
      fetch: async (input, init) => {
        request = new Request(input, init);
        return Response.json({
          id,
          workspace_id: id,
          version_id: id,
          file_set_id: id,
          source_hash: "a".repeat(64),
          kind: "text_patch",
          operations: [],
          status: "applied",
          revision: 3,
          apply_idempotency_key: "edit-recipe:apply:1",
          applied_version_id: id,
          output_hash: "b".repeat(64),
          created_at: "2026-07-14T00:00:00Z",
          updated_at: "2026-07-14T00:00:01Z",
        });
      },
    });

    await transport.call("edit_recipes.apply", {
      recipe_id: id,
      conversation_id: id,
      expected_recipe_revision: 1,
      expected_workspace_revision: 1,
      idempotency_key: "edit-recipe:apply:1",
      user_confirmed: true,
    });

    expect(request?.url).toBe("https://cloud.fairy.test/v1/edit-recipes/apply");
    expect(request?.headers.get("Idempotency-Key")).toBe("edit-recipe:apply:1");
    await expect(request?.json()).resolves.toMatchObject({
      recipe_id: id,
      idempotency_key: "edit-recipe:apply:1",
      user_confirmed: true,
    });
  });

  it("parses finite and live SSE with cursor deduplication", async () => {
    const requests: Request[] = [];
    const fetcher: typeof fetch = async (input, init) => {
      requests.push(new Request(input, init));
      return new Response(`id: 5\nevent: task.created\ndata: ${JSON.stringify(EVENT)}\n\n`, {
        headers: { "Content-Type": "text/event-stream" },
      });
    };
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: fetcher,
    });

    const batch = await transport.call("events.subscribe", { cursor: 4 });
    const controller = new AbortController();
    const iterator = transport
      .subscribeEvents(4, { signal: controller.signal })
      [Symbol.asyncIterator]();
    const live = await iterator.next();
    controller.abort();
    await iterator.return?.();

    expect(batch).toEqual({ items: [EVENT], next_cursor: 5 });
    expect(live.value).toEqual(EVENT);
    expect(requests[0]?.url).toBe(
      "https://cloud.fairy.test/v1/events?cursor=4&follow=false",
    );
    expect(requests[1]?.headers.get("Last-Event-ID")).toBe("4");
  });

  it("uses a stable source identity and the bounded event recovery routes", async () => {
    const requests: Request[] = [];
    const transport = new CloudCoreTransport({
      baseUrl: "HTTPS://CLOUD.FAIRY.TEST/root/",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: async (input, init) => {
        const request = new Request(input, init);
        requests.push(request);
        if (request.url.endsWith("/events/state")) {
          return Response.json({
            ledger_id: "0198f4de-0114-7000-8000-000000000099",
            oldest_cursor: 5,
            latest_cursor: 8,
          });
        }
        return Response.json({ items: [EVENT], next_cursor: 5 });
      },
    });

    expect(transport.eventSourceId).toBe("cloud:https://cloud.fairy.test/root");
    await transport.call("events.state", {});
    await transport.call("events.list", { cursor: 4, limit: 20 });

    expect(requests.map(({ url }) => url)).toEqual([
      "https://cloud.fairy.test/root/v1/events/state",
      "https://cloud.fairy.test/root/v1/events/history?cursor=4&limit=20",
    ]);
  });

  it("reconnects a live SSE stream after a transient network failure", async () => {
    const requests: Request[] = [];
    let attempt = 0;
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: async (input, init) => {
        requests.push(new Request(input, init));
        attempt += 1;
        if (attempt === 1) throw new TypeError("network disconnected");
        return new Response(`id: 5\nevent: task.created\ndata: ${JSON.stringify(EVENT)}\n\n`, {
          headers: { "Content-Type": "text/event-stream" },
        });
      },
    });
    const controller = new AbortController();
    const iterator = transport
      .subscribeEvents(4, { signal: controller.signal, pollIntervalMs: 1 })
      [Symbol.asyncIterator]();

    const live = await iterator.next();
    controller.abort();
    await iterator.return?.();

    expect(live.value).toEqual(EVENT);
    expect(requests).toHaveLength(2);
    expect(requests[1]?.headers.get("Last-Event-ID")).toBe("4");
  });

  it("forwards voice cancellation to the cloud fetch request", async () => {
    let requestSignal: AbortSignal | null | undefined;
    let markRequestStarted: (() => void) | undefined;
    const requestStarted = new Promise<void>((resolve) => {
      markRequestStarted = resolve;
    });
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: async (_input, init) => {
        requestSignal = init?.signal;
        markRequestStarted?.();
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () => reject(new DOMException("aborted", "AbortError")),
            { once: true },
          );
        });
      },
    });
    const controller = new AbortController();
    const pending = transport.call(
      "voice.synthesize",
      {
        task_id: "task-1",
        turn_id: "turn-1",
        message_id: "message-1",
        profile_id: "voice",
        voice: "alloy",
        start_offset: 0,
        end_offset: 6,
      },
      { signal: controller.signal },
    );

    await requestStarted;
    controller.abort();

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(requestSignal?.aborted).toBe(true);
  });

  it("preserves stable cloud error codes", async () => {
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: async () =>
        Response.json(
          { detail: { code: "VERSION_CONFLICT", message: "stale revision" } },
          { status: 409 },
        ),
    });
    const client = new CoreClient(transport);

    await expect(client.projects.get("project-1")).rejects.toMatchObject({
      name: "CloudCoreError",
      status: 409,
      errorCode: "VERSION_CONFLICT",
      message: "stale revision",
    });
  });

  it("rejects malformed memory envelopes before they reach the client", async () => {
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: async () => Response.json({ claim: { id: "not-a-uuid" } }),
    });

    await expect(
      transport.call("memory.claims.get", {
        task_id: "0198f4de-0114-7000-8000-000000000003",
        claim_id: "0198f4de-0114-7000-8000-000000000004",
      }),
    ).rejects.toMatchObject({
      name: "CloudCoreError",
      status: 200,
      errorCode: "INVALID_RESPONSE",
    });

    const invalidSnapshotTransport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: async () => Response.json({ ...SNAPSHOT, content_hash: "ABC" }),
    });
    await expect(
      invalidSnapshotTransport.call("memory.snapshots.get", {
        task_id: "0198f4de-0114-7000-8000-000000000003",
        snapshot_id: "0198f4de-0114-7000-8000-000000000006",
      }),
    ).rejects.toMatchObject({
      name: "CloudCoreError",
      status: 200,
      errorCode: "INVALID_RESPONSE",
    });
  });

  it("rejects unscoped Preview URLs before they reach the client", async () => {
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: async () =>
        Response.json({
          ...PREVIEW_CONTEXT,
          preview: { ...PREVIEW, url: `http://localhost:${RUNTIME.port}/preview/` },
        }),
    });

    await expect(
      transport.call("previews.get", { preview_id: PREVIEW.id }),
    ).rejects.toMatchObject({
      name: "CloudCoreError",
      status: 200,
      errorCode: "INVALID_RESPONSE",
    });
  });
});
