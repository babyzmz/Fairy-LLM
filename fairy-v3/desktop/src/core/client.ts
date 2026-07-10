export interface CoreTransport {
  call<T>(method: string, params: unknown): Promise<T>;
}

export interface ProjectCreateInput {
  name: string;
  residency: "local_only" | "synced";
}

export interface ConversationCreateInput {
  project_id: string | null;
  workspace_type: "project_chat" | "chat_scratch";
}

export interface TaskCreateInput {
  conversation_id: string;
  user_request: string;
  operation_mode: "continue_current_chat_draft" | "create_new_version" | "answer";
  execution_target: "local" | "cloud";
  idempotency_key: string;
}

export class CoreClient {
  readonly projects: {
    create: (input: ProjectCreateInput) => Promise<unknown>;
  };

  readonly conversations: {
    create: (input: ConversationCreateInput) => Promise<unknown>;
  };

  readonly tasks: {
    create: (input: TaskCreateInput) => Promise<unknown>;
  };

  constructor(private readonly transport: CoreTransport) {
    this.projects = {
      create: (input) => this.transport.call("projects.create", input),
    };
    this.conversations = {
      create: (input) => this.transport.call("conversations.create", input),
    };
    this.tasks = {
      create: (input) => this.transport.call("tasks.create", input),
    };
  }

  health(): Promise<{ status: string }> {
    return this.transport.call("health", {});
  }
}
