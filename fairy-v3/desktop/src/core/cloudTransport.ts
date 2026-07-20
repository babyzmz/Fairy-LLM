import { z } from "zod";

import { LOCAL_ONLY_CORE_METHODS } from "./contracts";
import type { CoreMethodMap, CoreMethodName, EventEnvelope, EventSubscriptionOptions } from "./contracts";
import type { CoreCallOptions, CoreTransport } from "./client";
import { parseMemoryResult } from "./memoryValidation";
import { parseRuntimeResult } from "./runtimeValidation";

type AccessTokenProvider = () => Promise<string | null> | string | null;

export interface CloudCoreTransportOptions {
  baseUrl: string;
  accessToken: AccessTokenProvider;
  deviceId: string;
  fetch?: typeof fetch;
}

interface RequestDescriptor {
  method: "DELETE" | "GET" | "POST" | "PUT";
  path: string;
  body?: unknown;
  idempotencyKey?: string;
}

type RuntimeParams = Record<string, unknown>;
type RouteBuilder = (params: RuntimeParams) => RequestDescriptor;
const localOnlyMethods = new Set<CoreMethodName>(LOCAL_ONLY_CORE_METHODS);

const routes = {
  health: () => get("/v1/health"),
  "projects.create": (params) => post("/v1/projects", params),
  "projects.import": (params) => post("/v1/projects/import", params),
  "projects.get": (params) => get(`/v1/projects/${pathParameter(params, "project_id")}`),
  "projects.list": (params) => getWithQuery("/v1/projects", params, ["limit", "cursor"]),
  "projects.update_metadata": (params) =>
    put(`/v1/projects/${pathParameter(params, "project_id")}/metadata`, params),
  "projects.archive": (params) =>
    post(`/v1/projects/${pathParameter(params, "project_id")}/archive`, params),
  "projects.delete": (params) => remove(`/v1/projects/${pathParameter(params, "project_id")}`, params),
  "projects.archived.list": (params) =>
    getWithQuery("/v1/history/archived-projects", params, ["limit", "cursor"]),
  "projects.archived.restore": (params) =>
    post(`/v1/history/archived-projects/${pathParameter(params, "project_id")}/restore`, params),
  "projects.archived.delete": (params) =>
    remove(`/v1/history/archived-projects/${pathParameter(params, "project_id")}`, params),
  "knowledge.projects.overview": (params) =>
    get(`/v1/projects/${pathParameter(params, "project_id")}/knowledge`),
  "knowledge.items.list": (params) =>
    getWithQuery(`/v1/projects/${pathParameter(params, "project_id")}/knowledge/items`, params, ["query", "limit"]),
  "knowledge.graph.get": (params) =>
    get(`/v1/projects/${pathParameter(params, "project_id")}/knowledge/graph`),
  "trash.items.list": (params) => getWithQuery("/v1/history/trash", params, ["limit", "cursor"]),
  "trash.items.restore": (params) =>
    post(
      `/v1/history/trash/${pathParameter(params, "item_type")}/${pathParameter(params, "item_id")}/restore`,
      params,
    ),
  "trash.items.purge": (params) =>
    remove(
      `/v1/history/trash/${pathParameter(params, "item_type")}/${pathParameter(params, "item_id")}`,
      params,
    ),
  "trash.items.purge_all": (params) => remove("/v1/history/trash", params),
  "conversations.create": (params) => post("/v1/conversations", params),
  "conversations.delete": (params) => remove(`/v1/conversations/${pathParameter(params, "conversation_id")}`, params),
  "conversations.get": (params) => get(`/v1/conversations/${pathParameter(params, "conversation_id")}`),
  "conversations.list": (params) => getWithQuery("/v1/conversations", params, ["limit", "cursor", "project_id"]),
  "conversations.move_to_project": (params) =>
    post(`/v1/conversations/${pathParameter(params, "conversation_id")}/move-to-project`, params),
  "conversations.update": (params) => put(`/v1/conversations/${pathParameter(params, "conversation_id")}`, params),
  "tasks.archive": (params) => post(`/v1/tasks/${pathParameter(params, "task_id")}/archive`, params),
  "tasks.create": (params) => post("/v1/tasks", params),
  "tasks.get": (params) => get(`/v1/tasks/${pathParameter(params, "task_id")}`),
  "tasks.list": (params) => getWithQuery("/v1/tasks", params, ["limit", "cursor", "project_id", "conversation_id"]),
  "tasks.review": (params) => post(`/v1/tasks/${pathParameter(params, "task_id")}/review`),
  "tasks.update_metadata": (params) => put(`/v1/tasks/${pathParameter(params, "task_id")}/metadata`, params),
  "execution_plans.create": (params) => post("/v1/execution-plans", params),
  "execution_plans.get": (params) => get(`/v1/tasks/${pathParameter(params, "task_id")}/execution-plan`),
  "changesets.propose": (params) => post("/v1/changesets", params),
  "approvals.decide": (params) => post(`/v1/approvals/${pathParameter(params, "approval_id")}/decision`, params),
  "approvals.list": (params) =>
    getWithQuery("/v1/approvals", params, ["limit", "cursor", "project_id", "conversation_id", "task_id"]),
  "versions.get": (params) => get(`/v1/versions/${pathParameter(params, "version_id")}`),
  "versions.list": (params) =>
    getWithQuery("/v1/versions", params, [
      "limit",
      "cursor",
      "workspace_id",
      "project_id",
      "conversation_id",
      "task_id",
    ]),
  "versions.accept": (params) => post(`/v1/tasks/${pathParameter(params, "task_id")}/accept-version`, params),
  "versions.discard": (params) => ({
    method: "DELETE",
    path: `/v1/tasks/${pathParameter(params, "task_id")}/version`,
  }),
  "workspaces.get": (params) => get(`/v1/workspaces/${pathParameter(params, "workspace_id")}`),
  "workspaces.files.list": (params) =>
    getWithQuery(`/v1/workspaces/${pathParameter(params, "workspace_id")}/files`, params, ["version_id"]),
  "workspaces.files.read": (params) =>
    getWithQuery(`/v1/workspaces/${pathParameter(params, "workspace_id")}/file-content`, params, [
      "path",
      "version_id",
    ]),
  "files.open_stream": (params) => post("/v1/files/open-stream", params),
  "files.probe": (params) =>
    getWithQuery(`/v1/workspaces/${pathParameter(params, "workspace_id")}/probe`, params, ["path", "version_id"]),
  "files.present": (params) => post("/v1/files/present", params),
  "files.compare": (params) => post("/v1/files/compare", params),
  "files.cancel": (params) => post("/v1/files/cancel", params),
  "asset_sets.create": (params) => postWithIdempotency("/v1/asset-sets", params),
  "asset_sets.list": (params) => post("/v1/asset-sets/list", params),
  "file_sets.resolve": (params) => post("/v1/file-sets/resolve", params),
  "file_sets.get": (params) => post("/v1/file-sets/get", params),
  "renderer_packs.list": () => get("/v1/renderer-packs"),
  "renderer_packs.health": () => get("/v1/renderer-packs/health"),
  "renderer_packs.install": (params) => post("/v1/renderer-packs/install", params),
  "renderer_packs.update": (params) => post("/v1/renderer-packs/update", params),
  "renderer_packs.remove": (params) => post("/v1/renderer-packs/remove", params),
  "annotations.list": (params) => post("/v1/annotations/list", params),
  "annotations.update": (params) => post("/v1/annotations/update", params),
  "selections.create": (params) => post("/v1/selections", params),
  "edit_recipes.create": (params) => post("/v1/edit-recipes", params),
  "edit_recipes.update": (params) => post("/v1/edit-recipes/update", params),
  "edit_recipes.apply": (params) => postWithIdempotency("/v1/edit-recipes/apply", params),
  "edit_recipes.discard": (params) => post("/v1/edit-recipes/discard", params),
  "workspaces.files.mutate": (params) => postWithIdempotency("/v1/workspaces/files/mutate", params),
  "workspaces.export": (params) => post("/v1/workspaces/export", params),
  "capabilities.get": () => get("/v1/capabilities"),
  "permissions.get": () => get("/v1/permissions"),
  "permissions.update": (params) => putWithIdempotency("/v1/permissions", params),
  "providers.list": () => get("/v1/providers"),
  "providers.health": (params) => getWithQuery("/v1/providers/health", params, ["profile_id"]),
  "models.catalog.list": () => get("/v1/models/catalog"),
  "models.catalog.refresh": () => post("/v1/models/catalog/refresh", {}),
  "models.selection.get": () => get("/v1/models/selection"),
  "models.selection.update": (params) => putWithIdempotency("/v1/models/selection", params),
  "media.images.generate": (params) => postWithIdempotency("/v1/media/images", params),
  "media.jobs.list": (params) => getWithQuery("/v1/media/jobs", params, ["task_id"]),
  "media.audio.generate": (params) => postWithIdempotency("/v1/media/audio", params),
  "media.videos.start": (params) => postWithIdempotency("/v1/media/videos", params),
  "media.videos.get": (params) => get(`/v1/media/videos/${pathParameter(params, "job_id")}`),
  "media.videos.cancel": (params) =>
    deleteWithIdempotency(`/v1/media/videos/${pathParameter(params, "job_id")}`, params),
  "extensions.catalog.list": () => get("/v1/extensions/catalog"),
  "skills.install": (params) =>
    postWithIdempotency(`/v1/skills/${pathParameter(params, "catalog_id")}`, params),
  "skills.import.inspect": (params) => post("/v1/skills/import/inspect", params),
  "skills.import.install": (params) => postWithIdempotency("/v1/skills/import/install", params),
  "skills.create": (params) => postWithIdempotency("/v1/skills/create", params),
  "skills.list": () => get("/v1/skills"),
  "skills.remove": (params) =>
    deleteWithIdempotency(`/v1/skills/${pathParameter(params, "name")}`, params),
  "skills.set_enabled": (params) =>
    postWithIdempotency(`/v1/skills/${pathParameter(params, "name")}/enabled`, params),
  "skills.update": (params) =>
    putWithIdempotency(`/v1/skills/${pathParameter(params, "name")}`, params),
  "mcp.servers.list": () => get("/v1/mcp/servers"),
  "mcp.presets.install": (params) =>
    postWithIdempotency(`/v1/mcp/presets/${pathParameter(params, "catalog_id")}`, params),
  "mcp.servers.configure": (params) =>
    putWithIdempotency(`/v1/mcp/servers/${pathParameter(params, "server_id")}`, params),
  "mcp.servers.discover": (params) =>
    postWithIdempotency(`/v1/mcp/servers/${pathParameter(params, "server_id")}/discover`, params),
  "mcp.servers.accept": (params) =>
    postWithIdempotency(`/v1/mcp/servers/${pathParameter(params, "server_id")}/accept`, params),
  "mcp.servers.set_enabled": (params) =>
    postWithIdempotency(`/v1/mcp/servers/${pathParameter(params, "server_id")}/enabled`, params),
  "mcp.servers.delete": (params) =>
    deleteWithIdempotency(`/v1/mcp/servers/${pathParameter(params, "server_id")}`, params),
  "runtimes.get": (params) => get(`/v1/runtimes/${pathParameter(params, "runtime_id")}`),
  "runtimes.health": (params) => getWithQuery("/v1/runtimes/health", params, ["task_id"]),
  "system.actions.execute": (params) => postWithIdempotency("/v1/system/actions", params),
  "previews.start": (params) => postWithIdempotency("/v1/previews/start", params),
  "previews.get": (params) => get(`/v1/previews/${pathParameter(params, "preview_id")}`),
  "previews.resolve": (params) =>
    getWithQuery("/v1/previews/resolve", params, ["task_id", "workspace_id", "version_id", "preview_id"]),
  "previews.stop": (params) => postWithIdempotency(`/v1/previews/${pathParameter(params, "preview_id")}/stop`, params),
  "artifacts.list": (params) => getWithQuery("/v1/artifacts", params, ["task_id"]),
  "artifacts.read": (params) => get(`/v1/artifacts/${pathParameter(params, "artifact_id")}`),
  "documents.import": (params) => postWithIdempotency("/v1/documents/import", params),
  "documents.list": (params) => getWithQuery("/v1/documents", params, ["task_id", "limit"]),
  "documents.get": (params) =>
    get(`/v1/documents/${pathParameter(params, "document_id")}` + `?task_id=${stringParameter(params, "task_id")}`),
  "documents.search": (params) => post("/v1/documents/search", params),
  "documents.delete": (params) =>
    postWithIdempotency(`/v1/documents/${pathParameter(params, "document_id")}/delete`, params),
  "assistant.turns.create": (params) => postWithIdempotency("/v1/assistant/turns", params),
  "assistant.turns.get": (params) => get(`/v1/assistant/turns/${pathParameter(params, "turn_id")}`),
  "assistant.turns.cancel": (params) => post(`/v1/assistant/turns/${pathParameter(params, "turn_id")}/cancel`, params),
  "assistant.turns.run": (params) => post(`/v1/assistant/turns/${pathParameter(params, "turn_id")}/run`, params),
  "assistant.turns.start": (params) => post(`/v1/assistant/turns/${pathParameter(params, "turn_id")}/start`, params),
  "assistant.turns.retry": (params) =>
    postWithIdempotency(`/v1/assistant/turns/${pathParameter(params, "turn_id")}/retry`, params),
  "assistant.turns.trace.list": (params) =>
    get(`/v1/assistant/turns/${pathParameter(params, "turn_id")}/trace`),
  "messages.list": (params) => getWithQuery("/v1/messages", params, ["conversation_id", "limit", "cursor"]),
  "voice.synthesize": (params) => post("/v1/voice/speech", params),
  "voice.sessions.start": (params) => post("/v1/voice/sessions", params),
  "voice.sessions.get": (params) => get(`/v1/voice/sessions/${pathParameter(params, "session_id")}`),
  "voice.sessions.cancel": (params) => remove(`/v1/voice/sessions/${pathParameter(params, "session_id")}`),
  "voice.transcribe": (params) => post("/v1/voice/transcriptions", params),
  "realtime.sessions.start": (params) => post("/v1/realtime/sessions", params),
  "realtime.sessions.get": (params) =>
    get(`/v1/realtime/sessions/${pathParameter(params, "session_id")}`),
  "realtime.sessions.list": (params) =>
    getWithQuery("/v1/realtime/sessions", params, ["limit"]),
  "realtime.sessions.report": (params) =>
    post(`/v1/realtime/sessions/${pathParameter(params, "session_id")}/report`, params),
  "realtime.sessions.stop": (params) =>
    post(`/v1/realtime/sessions/${pathParameter(params, "session_id")}/stop`, params),
  "realtime.memories.save": (params) => post("/v1/realtime/memories", params),
  "realtime.memories.list": (params) =>
    getWithQuery("/v1/realtime/memories", params, ["limit"]),
  "realtime.memories.delete": (params) =>
    remove(`/v1/realtime/memories/${pathParameter(params, "memory_id")}`),
  "memory.observations.create": (params) => post("/v1/memory/observations", params),
  "memory.observations.list": (params) =>
    get(
      `/v1/memory/observations?task_id=${stringParameter(params, "task_id")}` +
        `&namespace=${stringParameter(params, "namespace")}`,
    ),
  "memory.claims.promote": (params) => post("/v1/memory/claims/promote", params),
  "memory.claims.get": (params) =>
    get(`/v1/memory/claims/${pathParameter(params, "claim_id")}` + `?task_id=${stringParameter(params, "task_id")}`),
  "memory.claims.list": (params) =>
    get(
      `/v1/memory/claims?task_id=${stringParameter(params, "task_id")}` +
        `&namespace=${stringParameter(params, "namespace")}`,
    ),
  "memory.claims.supersede": (params) =>
    post(`/v1/memory/claims/${pathParameter(params, "claim_id")}/supersede`, params),
  "memory.claims.resolve_conflict": (params) =>
    post(`/v1/memory/claims/${pathParameter(params, "claim_id")}/resolve-conflict`, params),
  "memory.forget": (params) => post("/v1/memory/forget", params),
  "memory.search": (params) =>
    get(
      `/v1/memory/search?task_id=${stringParameter(params, "task_id")}` +
        `&query=${stringParameter(params, "query")}` +
        `&limit=${memorySearchLimit(params)}`,
    ),
  "memory.snapshots.get": (params) =>
    get(
      `/v1/memory/snapshots/${pathParameter(params, "snapshot_id")}` + `?task_id=${stringParameter(params, "task_id")}`,
    ),
  "memory.projection.health": (params) =>
    get(`/v1/memory/projection/health?task_id=${stringParameter(params, "task_id")}`),
  "memory.proposals.list": (params) =>
    getWithQuery("/v1/memory/proposals", params, ["task_id", "limit"]),
  "memory.proposals.accept": (params) =>
    postWithIdempotency(
      `/v1/memory/proposals/${pathParameter(params, "observation_id")}/accept`,
      params,
    ),
  "memory.proposals.reject": (params) =>
    postWithIdempotency(
      `/v1/memory/proposals/${pathParameter(params, "observation_id")}/reject`,
      params,
    ),
  "memory.settings.get": () => get("/v1/memory/settings"),
  "memory.settings.update": (params) =>
    putWithIdempotency("/v1/memory/settings", params),
  "events.state": () => get("/v1/events/state"),
  "events.list": (params) =>
    get(
      `/v1/events/history?cursor=${integerParameter(params, "cursor")}` +
        `&limit=${integerParameter(params, "limit")}`,
    ),
  "events.subscribe": (params) => get(`/v1/events?cursor=${integerParameter(params, "cursor")}&follow=false`),
} satisfies Partial<Record<CoreMethodName, RouteBuilder>>;

const eventEnvelopeSchema = z
  .object({
    id: z.uuid(),
    cursor: z.number().int().positive(),
    run_id: z.uuid().nullable(),
    project_id: z.uuid().nullable(),
    conversation_id: z.uuid().nullable(),
    task_id: z.uuid().nullable(),
    version_id: z.uuid().nullable(),
    task_sequence: z.number().int().positive().nullable(),
    event_type: z.string().min(1),
    visibility: z.enum(["user", "developer", "internal"]),
    message: z.string(),
    payload: z.record(z.string(), z.unknown()),
    schema_version: z.number().int().positive(),
    created_at: z.string().min(1),
  })
  .strict();

export class CloudCoreError extends Error {
  readonly status: number;
  readonly errorCode: string;
  readonly details: Record<string, unknown>;

  constructor(
    message: string,
    options: {
      status: number;
      errorCode: string;
      details?: Record<string, unknown>;
    },
  ) {
    super(message);
    this.name = "CloudCoreError";
    this.status = options.status;
    this.errorCode = options.errorCode;
    this.details = options.details ?? {};
  }
}

export class CloudCoreTransport implements CoreTransport {
  readonly eventSourceId: string;

  private readonly baseUrl: string;
  private readonly accessToken: AccessTokenProvider;
  private readonly deviceId: string;
  private readonly fetcher: typeof fetch;

  constructor(options: CloudCoreTransportOptions) {
    const normalizedBaseUrl = normalizeBaseUrl(options.baseUrl);
    this.baseUrl = `${normalizedBaseUrl}/`;
    this.eventSourceId = `cloud:${normalizedBaseUrl}`;
    this.accessToken = options.accessToken;
    this.deviceId = options.deviceId;
    this.fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  async call<M extends CoreMethodName>(
    method: M,
    params: CoreMethodMap[M]["params"],
    options: CoreCallOptions = {},
  ): Promise<CoreMethodMap[M]["result"]> {
    if (localOnlyMethods.has(method)) {
      throw new CloudCoreError("This capability is available only on the local Fairy device", {
        status: 400,
        errorCode: "CAPABILITY_NOT_AVAILABLE",
      });
    }
    const route = (routes as Partial<Record<CoreMethodName, RouteBuilder>>)[method];
    if (route === undefined) {
      throw new CloudCoreError("Cloud transport route is unavailable", {
        status: 400,
        errorCode: "CAPABILITY_NOT_AVAILABLE",
      });
    }
    const descriptor = route(params as RuntimeParams);
    const response = await this.request(descriptor, {
      accept: method === "events.subscribe" ? "text/event-stream" : "application/json",
      signal: options.signal,
    });
    await assertSuccessful(response);

    if (method === "events.subscribe") {
      const items: EventEnvelope[] = [];
      let nextCursor = integerParameter(params as RuntimeParams, "cursor");
      for await (const event of parseEventStream(response)) {
        if (event.cursor <= nextCursor) continue;
        nextCursor = event.cursor;
        items.push(event);
      }
      return { items, next_cursor: nextCursor } as CoreMethodMap[M]["result"];
    }

    return validateCoreResult(method, await response.json()) as CoreMethodMap[M]["result"];
  }

  async *subscribeEvents(cursor: number, options: EventSubscriptionOptions = {}): AsyncIterable<EventEnvelope> {
    let current = cursor;
    while (!options.signal?.aborted) {
      try {
        const response = await this.request(get(`/v1/events?cursor=${current}&follow=true`), {
          accept: "text/event-stream",
          lastEventId: current > 0 ? String(current) : undefined,
          signal: options.signal,
        });
        await assertSuccessful(response);
        for await (const event of parseEventStream(response)) {
          if (event.cursor <= current) continue;
          current = event.cursor;
          yield event;
        }
      } catch (error) {
        if (options.signal?.aborted) return;
        if (!isRetryableEventStreamError(error)) throw error;
      }
      if (!options.signal?.aborted) {
        await waitForReconnect(options.pollIntervalMs ?? 500, options.signal);
      }
    }
  }

  private async request(
    descriptor: RequestDescriptor,
    options: {
      accept: string;
      lastEventId?: string;
      signal?: AbortSignal;
    },
  ): Promise<Response> {
    const token = await this.accessToken();
    const headers = new Headers({
      Accept: options.accept,
      "X-Fairy-Device-ID": this.deviceId,
    });
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (options.lastEventId) headers.set("Last-Event-ID", options.lastEventId);
    if (descriptor.body !== undefined) headers.set("Content-Type", "application/json");
    if (descriptor.idempotencyKey) {
      headers.set("Idempotency-Key", descriptor.idempotencyKey);
    }

    return this.fetcher(new URL(descriptor.path.replace(/^\//, ""), this.baseUrl), {
      method: descriptor.method,
      headers,
      body: descriptor.body === undefined ? undefined : JSON.stringify(descriptor.body),
      signal: options.signal,
    });
  }
}

function normalizeBaseUrl(value: string): string {
  const parsed = new URL(value);
  parsed.hash = "";
  parsed.search = "";
  parsed.pathname = parsed.pathname.replace(/\/+$/, "");
  return parsed.toString().replace(/\/+$/, "");
}

function isRetryableEventStreamError(error: unknown): boolean {
  if (error instanceof TypeError) return true;
  return (
    error instanceof CloudCoreError &&
    (error.status === 408 || error.status === 425 || error.status === 429 || error.status >= 500)
  );
}

function get(path: string): RequestDescriptor {
  return { method: "GET", path };
}

function post(path: string, body?: unknown): RequestDescriptor {
  return { method: "POST", path, body };
}

function put(path: string, body?: unknown): RequestDescriptor {
  return { method: "PUT", path, body };
}

function remove(path: string, body?: unknown): RequestDescriptor {
  return { method: "DELETE", path, body };
}

function putWithIdempotency(path: string, params: RuntimeParams): RequestDescriptor {
  return {
    method: "PUT",
    path,
    body: params,
    idempotencyKey: rawStringParameter(params, "idempotency_key"),
  };
}

function postWithIdempotency(path: string, params: RuntimeParams): RequestDescriptor {
  return {
    method: "POST",
    path,
    body: params,
    idempotencyKey: rawStringParameter(params, "idempotency_key"),
  };
}

function deleteWithIdempotency(path: string, params: RuntimeParams): RequestDescriptor {
  return {
    method: "DELETE",
    path,
    body: params,
    idempotencyKey: rawStringParameter(params, "idempotency_key"),
  };
}

function getWithQuery(path: string, params: RuntimeParams, names: readonly string[]): RequestDescriptor {
  const query = new URLSearchParams();
  for (const name of names) {
    const value = params[name];
    if (value === undefined || value === null) continue;
    if (typeof value !== "string" && typeof value !== "number") {
      throw new TypeError(`Core parameter ${name} must be a string or number`);
    }
    query.set(name, String(value));
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return get(`${path}${suffix}`);
}

function pathParameter(params: RuntimeParams, name: string): string {
  const value = params[name];
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError(`Core parameter ${name} must be a non-empty string`);
  }
  return encodeURIComponent(value);
}

function integerParameter(params: RuntimeParams, name: string): number {
  const value = params[name];
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new TypeError(`Core parameter ${name} must be a non-negative integer`);
  }
  return value;
}

function stringParameter(params: RuntimeParams, name: string): string {
  return encodeURIComponent(rawStringParameter(params, name));
}

function rawStringParameter(params: RuntimeParams, name: string): string {
  const value = params[name];
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError(`Core parameter ${name} must be a non-empty string`);
  }
  return value;
}

function memorySearchLimit(params: RuntimeParams): number {
  const value = params.limit ?? 20;
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1 || value > 100) {
    throw new TypeError("Core parameter limit must be an integer between 1 and 100");
  }
  return value;
}

function validateCoreResult(method: CoreMethodName, payload: unknown): unknown {
  try {
    return parseRuntimeResult(method, parseMemoryResult(method, payload));
  } catch (error) {
    if (!(error instanceof z.ZodError)) throw error;
    throw new CloudCoreError("Cloud Core response does not match its contract", {
      status: 200,
      errorCode: "INVALID_RESPONSE",
      details: { issues: error.issues },
    });
  }
}

async function assertSuccessful(response: Response): Promise<void> {
  if (response.ok) return;
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = undefined;
  }
  const root = recordValue(payload);
  const detail = recordValue(root.detail);
  const errorCode = stringValue(detail.code) ?? stringValue(root.code) ?? `HTTP_${response.status}`;
  const message =
    stringValue(detail.message) ?? stringValue(root.message) ?? response.statusText ?? "Cloud Core request failed";
  throw new CloudCoreError(message, {
    status: response.status,
    errorCode,
    details: Object.keys(detail).length > 0 ? detail : root,
  });
}

async function* parseEventStream(response: Response): AsyncIterable<EventEnvelope> {
  if (!response.body) {
    throw new CloudCoreError("Cloud event stream has no body", {
      status: response.status,
      errorCode: "INVALID_RESPONSE",
    });
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    buffer = buffer.replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = parseEventBlock(block);
      if (event) yield event;
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  const trailing = parseEventBlock(buffer.trim());
  if (trailing) yield trailing;
}

function parseEventBlock(block: string): EventEnvelope | null {
  const data = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return null;
  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch (error) {
    throw new CloudCoreError("Cloud event data is not valid JSON", {
      status: 200,
      errorCode: "INVALID_RESPONSE",
      details: { cause: String(error) },
    });
  }
  const parsed = eventEnvelopeSchema.safeParse(payload);
  if (!parsed.success) {
    throw new CloudCoreError("Cloud event does not match EventEnvelope", {
      status: 200,
      errorCode: "INVALID_RESPONSE",
      details: { issues: parsed.error.issues },
    });
  }
  return parsed.data;
}

function recordValue(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function waitForReconnect(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const timeout = globalThis.setTimeout(finish, milliseconds);
    signal?.addEventListener("abort", finish, { once: true });

    function finish() {
      globalThis.clearTimeout(timeout);
      signal?.removeEventListener("abort", finish);
      resolve();
    }
  });
}
