import { z } from "zod";

import type {
  CoreMethodMap,
  CoreMethodName,
  EventEnvelope,
  EventSubscriptionOptions,
} from "./contracts";
import type { CoreTransport } from "./client";

type AccessTokenProvider = () => Promise<string | null> | string | null;

export interface CloudCoreTransportOptions {
  baseUrl: string;
  accessToken: AccessTokenProvider;
  deviceId: string;
  fetch?: typeof fetch;
}

interface RequestDescriptor {
  method: "DELETE" | "GET" | "POST";
  path: string;
  body?: unknown;
}

type RuntimeParams = Record<string, unknown>;
type RouteBuilder = (params: RuntimeParams) => RequestDescriptor;

const routes = {
  health: () => get("/v1/health"),
  "projects.create": (params) => post("/v1/projects", params),
  "projects.import": (params) => post("/v1/projects/import", params),
  "projects.get": (params) =>
    get(`/v1/projects/${pathParameter(params, "project_id")}`),
  "conversations.create": (params) => post("/v1/conversations", params),
  "tasks.create": (params) => post("/v1/tasks", params),
  "tasks.get": (params) => get(`/v1/tasks/${pathParameter(params, "task_id")}`),
  "tasks.review": (params) =>
    post(`/v1/tasks/${pathParameter(params, "task_id")}/review`),
  "changesets.propose": (params) => post("/v1/changesets", params),
  "approvals.decide": (params) =>
    post(
      `/v1/approvals/${pathParameter(params, "approval_id")}/decision`,
      params,
    ),
  "versions.get": (params) =>
    get(`/v1/versions/${pathParameter(params, "version_id")}`),
  "versions.accept": (params) =>
    post(
      `/v1/tasks/${pathParameter(params, "task_id")}/accept-version`,
      params,
    ),
  "versions.discard": (params) => ({
    method: "DELETE",
    path: `/v1/tasks/${pathParameter(params, "task_id")}/version`,
  }),
  "capabilities.get": (params) => post("/v1/capabilities", params),
  "events.subscribe": (params) =>
    get(`/v1/events?cursor=${integerParameter(params, "cursor")}&follow=false`),
} satisfies Record<CoreMethodName, RouteBuilder>;

const eventEnvelopeSchema = z
  .object({
    id: z.uuid(),
    cursor: z.number().int().positive(),
    run_id: z.uuid().nullable(),
    project_id: z.uuid().nullable(),
    conversation_id: z.uuid(),
    task_id: z.uuid(),
    version_id: z.uuid().nullable(),
    task_sequence: z.number().int().positive(),
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
  private readonly baseUrl: string;
  private readonly accessToken: AccessTokenProvider;
  private readonly deviceId: string;
  private readonly fetcher: typeof fetch;

  constructor(options: CloudCoreTransportOptions) {
    this.baseUrl = `${options.baseUrl.replace(/\/+$/, "")}/`;
    this.accessToken = options.accessToken;
    this.deviceId = options.deviceId;
    this.fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  async call<M extends CoreMethodName>(
    method: M,
    params: CoreMethodMap[M]["params"],
  ): Promise<CoreMethodMap[M]["result"]> {
    const descriptor = routes[method](params as RuntimeParams);
    const response = await this.request(descriptor, {
      accept: method === "events.subscribe" ? "text/event-stream" : "application/json",
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

    return (await response.json()) as CoreMethodMap[M]["result"];
  }

  async *subscribeEvents(
    cursor: number,
    options: EventSubscriptionOptions = {},
  ): AsyncIterable<EventEnvelope> {
    let current = cursor;
    while (!options.signal?.aborted) {
      const response = await this.request(
        get(`/v1/events?cursor=${current}&follow=true`),
        {
          accept: "text/event-stream",
          lastEventId: current > 0 ? String(current) : undefined,
          signal: options.signal,
        },
      );
      await assertSuccessful(response);
      for await (const event of parseEventStream(response)) {
        if (event.cursor <= current) continue;
        current = event.cursor;
        yield event;
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

    return this.fetcher(new URL(descriptor.path.replace(/^\//, ""), this.baseUrl), {
      method: descriptor.method,
      headers,
      body: descriptor.body === undefined ? undefined : JSON.stringify(descriptor.body),
      signal: options.signal,
    });
  }
}

function get(path: string): RequestDescriptor {
  return { method: "GET", path };
}

function post(path: string, body?: unknown): RequestDescriptor {
  return { method: "POST", path, body };
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
    stringValue(detail.message) ??
    stringValue(root.message) ??
    response.statusText ??
    "Cloud Core request failed";
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
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
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
