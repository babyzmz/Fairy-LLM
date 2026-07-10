import type {
  ApprovalDecisionInput,
  CapabilityRequest,
  ChangesetProposal,
  ConversationCreateInput,
  CoreMethodMap,
  CoreMethodName,
  EventEnvelope,
  EventSubscriptionOptions,
  MemoryClaimPromoteInput,
  MemoryClaimResolveInput,
  MemoryClaimSupersedeInput,
  MemoryForgetInput,
  MemoryNamespace,
  MemoryObserveInput,
  MemorySearchInput,
  ProjectCreateInput,
  ProjectImportInput,
  TaskCreateInput,
  VersionAcceptInput,
} from "./contracts";

export type * from "./contracts";

export interface CoreTransport {
  call<M extends CoreMethodName>(
    method: M,
    params: CoreMethodMap[M]["params"],
  ): Promise<CoreMethodMap[M]["result"]>;

  subscribeEvents?(
    cursor: number,
    options?: EventSubscriptionOptions,
  ): AsyncIterable<EventEnvelope>;
}

export class CoreClient {
  readonly projects = {
    create: (input: ProjectCreateInput) => this.transport.call("projects.create", input),
    import: (input: ProjectImportInput) => this.transport.call("projects.import", input),
    get: (projectId: string) =>
      this.transport.call("projects.get", { project_id: projectId }),
  };

  readonly conversations = {
    create: (input: ConversationCreateInput) =>
      this.transport.call("conversations.create", input),
  };

  readonly tasks = {
    create: (input: TaskCreateInput) => this.transport.call("tasks.create", input),
    get: (taskId: string) => this.transport.call("tasks.get", { task_id: taskId }),
    review: (taskId: string) => this.transport.call("tasks.review", { task_id: taskId }),
  };

  readonly changesets = {
    propose: (input: ChangesetProposal) => this.transport.call("changesets.propose", input),
  };

  readonly approvals = {
    decide: (input: ApprovalDecisionInput) => this.transport.call("approvals.decide", input),
  };

  readonly versions = {
    get: (versionId: string) =>
      this.transport.call("versions.get", { version_id: versionId }),
    accept: (input: VersionAcceptInput) => this.transport.call("versions.accept", input),
    discard: (taskId: string) =>
      this.transport.call("versions.discard", { task_id: taskId }),
  };

  readonly capabilities = {
    get: (input: CapabilityRequest) => this.transport.call("capabilities.get", input),
  };

  readonly memory = {
    observations: {
      create: (input: MemoryObserveInput) =>
        this.transport.call("memory.observations.create", input),
      list: (taskId: string, namespace: MemoryNamespace) =>
        this.transport.call("memory.observations.list", {
          task_id: taskId,
          namespace,
        }),
    },
    claims: {
      promote: (input: MemoryClaimPromoteInput) =>
        this.transport.call("memory.claims.promote", input),
      get: (taskId: string, claimId: string) =>
        this.transport.call("memory.claims.get", {
          task_id: taskId,
          claim_id: claimId,
        }),
      list: (taskId: string, namespace: MemoryNamespace) =>
        this.transport.call("memory.claims.list", {
          task_id: taskId,
          namespace,
        }),
      supersede: (input: MemoryClaimSupersedeInput) =>
        this.transport.call("memory.claims.supersede", input),
      resolveConflict: (input: MemoryClaimResolveInput) =>
        this.transport.call("memory.claims.resolve_conflict", input),
    },
    search: (input: MemorySearchInput) => this.transport.call("memory.search", input),
    snapshots: {
      get: (taskId: string, snapshotId: string) =>
        this.transport.call("memory.snapshots.get", {
          task_id: taskId,
          snapshot_id: snapshotId,
        }),
    },
    projection: {
      health: (taskId: string) =>
        this.transport.call("memory.projection.health", { task_id: taskId }),
    },
    forget: (input: MemoryForgetInput) => this.transport.call("memory.forget", input),
  };

  readonly events = {
    subscribe: (cursor = 0, options: EventSubscriptionOptions = {}) =>
      this.subscribeToEvents(cursor, options),
  };

  constructor(private readonly transport: CoreTransport) {}

  health() {
    return this.transport.call("health", {});
  }

  private subscribeToEvents(
    cursor: number,
    options: EventSubscriptionOptions,
  ): AsyncIterable<EventEnvelope> {
    return (
      this.transport.subscribeEvents?.(cursor, options) ??
      this.pollEvents(cursor, options)
    );
  }

  private async *pollEvents(
    cursor: number,
    options: EventSubscriptionOptions,
  ): AsyncIterable<EventEnvelope> {
    let current = cursor;
    while (!options.signal?.aborted) {
      const batch = await this.transport.call("events.subscribe", { cursor: current });
      for (const event of batch.items) {
        if (event.cursor <= current) continue;
        current = event.cursor;
        yield event;
      }
      if (batch.items.length === 0) {
        await waitForPoll(options.pollIntervalMs ?? 250, options.signal);
      }
    }
  }
}

function waitForPoll(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const timeout = window.setTimeout(finish, milliseconds);
    signal?.addEventListener("abort", finish, { once: true });

    function finish() {
      window.clearTimeout(timeout);
      signal?.removeEventListener("abort", finish);
      resolve();
    }
  });
}
