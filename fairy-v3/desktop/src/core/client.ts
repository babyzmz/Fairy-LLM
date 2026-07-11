import type {
  ApprovalDecisionInput,
  ApprovalListInput,
  AssistantTurnCancelInput,
  AssistantTurnCreateInput,
  AssistantTurnRetryInput,
  CapabilityRequest,
  ChangesetProposal,
  ConversationCreateInput,
  ConversationListInput,
  CoreMethodMap,
  CoreMethodName,
  DocumentDeleteInput,
  DocumentImportInput,
  DocumentListInput,
  DocumentSearchInput,
  EventEnvelope,
  EventSubscriptionOptions,
  MemoryClaimPromoteInput,
  MemoryClaimResolveInput,
  MemoryClaimSupersedeInput,
  MemoryForgetInput,
  MemoryNamespace,
  MemoryObserveInput,
  MemorySearchInput,
  MessageListInput,
  ProjectCreateInput,
  ProjectImportInput,
  ProjectListInput,
  PreviewResolveInput,
  PreviewStartInput,
  PreviewStopInput,
  ProviderHealthInput,
  TaskCreateInput,
  TaskListInput,
  VersionAcceptInput,
  VersionListInput,
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
    list: (input: ProjectListInput = {}) => this.transport.call("projects.list", input),
  };

  readonly conversations = {
    create: (input: ConversationCreateInput) =>
      this.transport.call("conversations.create", input),
    get: (conversationId: string) =>
      this.transport.call("conversations.get", { conversation_id: conversationId }),
    list: (input: ConversationListInput = {}) =>
      this.transport.call("conversations.list", input),
  };

  readonly tasks = {
    create: (input: TaskCreateInput) => this.transport.call("tasks.create", input),
    get: (taskId: string) => this.transport.call("tasks.get", { task_id: taskId }),
    list: (input: TaskListInput = {}) => this.transport.call("tasks.list", input),
    review: (taskId: string) => this.transport.call("tasks.review", { task_id: taskId }),
  };

  readonly changesets = {
    propose: (input: ChangesetProposal) => this.transport.call("changesets.propose", input),
  };

  readonly approvals = {
    decide: (input: ApprovalDecisionInput) => this.transport.call("approvals.decide", input),
    list: (input: ApprovalListInput = {}) =>
      this.transport.call("approvals.list", input),
  };

  readonly versions = {
    get: (versionId: string) =>
      this.transport.call("versions.get", { version_id: versionId }),
    list: (input: VersionListInput = {}) =>
      this.transport.call("versions.list", input),
    accept: (input: VersionAcceptInput) => this.transport.call("versions.accept", input),
    discard: (taskId: string) =>
      this.transport.call("versions.discard", { task_id: taskId }),
  };

  readonly capabilities = {
    get: (input: CapabilityRequest) => this.transport.call("capabilities.get", input),
  };

  readonly providers = {
    list: () => this.transport.call("providers.list", {}),
    health: (profileId?: string) =>
      this.transport.call(
        "providers.health",
        profileId === undefined
          ? {}
          : ({ profile_id: profileId } satisfies ProviderHealthInput),
      ),
  };

  readonly runtimes = {
    get: (runtimeId: string) =>
      this.transport.call("runtimes.get", { runtime_id: runtimeId }),
    health: (taskId: string) =>
      this.transport.call("runtimes.health", { task_id: taskId }),
  };

  readonly previews = {
    start: (input: PreviewStartInput) => this.transport.call("previews.start", input),
    get: (previewId: string) =>
      this.transport.call("previews.get", { preview_id: previewId }),
    resolve: (input: PreviewResolveInput) =>
      this.transport.call("previews.resolve", input),
    stop: (input: PreviewStopInput) => this.transport.call("previews.stop", input),
  };

  readonly artifacts = {
    list: (taskId: string) =>
      this.transport.call("artifacts.list", { task_id: taskId }),
    read: (artifactId: string) =>
      this.transport.call("artifacts.read", { artifact_id: artifactId }),
  };

  readonly documents = {
    import: (input: DocumentImportInput) =>
      this.transport.call("documents.import", input),
    list: (input: DocumentListInput) =>
      this.transport.call("documents.list", input),
    get: (taskId: string, documentId: string) =>
      this.transport.call("documents.get", {
        task_id: taskId,
        document_id: documentId,
      }),
    search: (input: DocumentSearchInput) =>
      this.transport.call("documents.search", input),
    delete: (input: DocumentDeleteInput) =>
      this.transport.call("documents.delete", input),
  };

  readonly assistant = {
    turns: {
      create: (input: AssistantTurnCreateInput) =>
        this.transport.call("assistant.turns.create", input),
      get: (turnId: string) =>
        this.transport.call("assistant.turns.get", { turn_id: turnId }),
      cancel: (input: AssistantTurnCancelInput) =>
        this.transport.call("assistant.turns.cancel", input),
      run: (turnId: string) =>
        this.transport.call("assistant.turns.run", { turn_id: turnId }),
      retry: (input: AssistantTurnRetryInput) =>
        this.transport.call("assistant.turns.retry", input),
    },
  };

  readonly messages = {
    list: (input: MessageListInput) => this.transport.call("messages.list", input),
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
