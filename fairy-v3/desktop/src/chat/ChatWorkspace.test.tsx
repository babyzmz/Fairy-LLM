import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  Approval,
  AssistantTurn,
  Message,
  ProviderHealth,
  ProviderProfile,
} from "../core/client";
import { ChatWorkspace, type ChatWorkspaceProps } from "./ChatWorkspace";

afterEach(cleanup);

describe("ChatWorkspace", () => {
  it("renders durable messages and sends natural-language text", async () => {
    const user = userEvent.setup();
    const props = workspaceProps();
    render(<ChatWorkspace {...props} />);

    expect(screen.getByText("Hello Fairy")).toBeVisible();
    expect(screen.getByText("Hello from the durable ledger")).toBeVisible();
    await user.type(screen.getByLabelText("Message Fairy"), "What is the weather today?");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() =>
      expect(props.onSend).toHaveBeenCalledWith("What is the weather today?", [], []),
    );
    expect(screen.getByLabelText("Message Fairy")).toHaveFocus();
  });

  it("does not send while a Chinese IME composition is being confirmed", async () => {
    const props = workspaceProps();
    render(<ChatWorkspace {...props} />);
    const composer = screen.getByLabelText("Message Fairy");
    fireEvent.change(composer, { target: { value: "中文输入" } });

    fireEvent.keyDown(composer, { key: "Enter", isComposing: true });
    expect(props.onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(composer, { key: "Enter", isComposing: false });
    await waitFor(() => expect(props.onSend).toHaveBeenCalledWith("中文输入", [], []));
  });

  it("renders safe GFM and routes links and copy through controlled actions", async () => {
    const user = userEvent.setup();
    const content = [
      "| Name | Value |",
      "| --- | --- |",
      "| Fairy | Ready |",
      "",
      "[OpenAI](https://openai.com)",
      "",
      "```ts",
      "const ready = true;",
      "```",
      "<script>unsafe</script>",
    ].join("\n");
    const props = workspaceProps({
      messages: [{ ...MESSAGES[1], content }],
    });
    render(<ChatWorkspace {...props} />);

    expect(screen.getByRole("table")).toBeVisible();
    expect(screen.queryByText("unsafe")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "OpenAI" }));
    expect(props.onOpenMessageLink).toHaveBeenCalledWith(
      MESSAGES[1].task_id,
      "https://openai.com",
    );
    await user.click(screen.getByRole("button", { name: "Copy code" }));
    expect(props.onCopyMessage).toHaveBeenCalledWith(
      MESSAGES[1].task_id,
      "const ready = true;",
    );
  });

  it("never projects durable tool protocol as a second chat message", () => {
    const toolMessage: Message = {
      ...MESSAGES[0],
      id: "0198f4de-0114-7000-8000-000000000099",
      role: "tool",
      sequence: 3,
      content: "[TOOL_RESULT] raw provider payload [/TOOL_RESULT]",
    };
    const props = workspaceProps({ messages: [...MESSAGES, toolMessage] });
    const { rerender } = render(<ChatWorkspace {...props} />);

    expect(screen.queryByText(toolMessage.content)).not.toBeInTheDocument();

    rerender(<ChatWorkspace {...props} developerMode />);
    expect(screen.queryByText(toolMessage.content)).not.toBeInTheDocument();
  });

  it("routes slash commands without sending them as model text", async () => {
    const user = userEvent.setup();
    const props = workspaceProps();
    render(<ChatWorkspace {...props} />);

    await user.type(screen.getByLabelText("Message Fairy"), "  /new");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(props.onNewConversation).toHaveBeenCalledOnce());
    expect(props.onSend).not.toHaveBeenCalled();
  });

  it("rejects Slash and button actions disabled by Core metadata", async () => {
    const user = userEvent.setup();
    const props = workspaceProps({
      slashCommands: workspaceProps().slashCommands.map((command) =>
        command.name === "new" ? { ...command, available: false } : command,
      ),
    });
    render(<ChatWorkspace {...props} />);

    expect(screen.getByRole("button", { name: "New conversation" })).toBeDisabled();
    await user.type(screen.getByLabelText("Message Fairy"), "/new");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByText("Command unavailable: /new")).toBeVisible();
    expect(props.onNewConversation).not.toHaveBeenCalled();
    expect(props.onSend).not.toHaveBeenCalled();
  });

  it("deduplicates streamed chunks and exposes stop then retry states", async () => {
    const user = userEvent.setup();
    const props = workspaceProps({
      messages: [MESSAGES[0]],
      streamedText: "Streaming once, not twice",
      turn: { ...TURN, status: "running" },
      isBusy: true,
    });
    const { rerender } = render(<ChatWorkspace {...props} />);

    expect(screen.getAllByText("Streaming once, not twice")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Stop response" }));
    expect(props.onCancel).toHaveBeenCalledOnce();

    rerender(
      <ChatWorkspace
        {...props}
        streamedText=""
        isBusy={false}
        turn={{ ...TURN, status: "failed", error_code: "PROVIDER_UNAVAILABLE" }}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Retry response" }));
    expect(props.onRetry).toHaveBeenCalledOnce();
  });

  it("hides a stream projection once its durable assistant message exists", () => {
    const props = workspaceProps({
      streamedText: MESSAGES[1].content,
      turn: { ...TURN, status: "running", completed_at: null },
      isBusy: true,
    });

    render(<ChatWorkspace {...props} />);

    expect(screen.getAllByText(MESSAGES[1].content)).toHaveLength(1);
    expect(screen.queryByText("responding")).not.toBeInTheDocument();
  });

  it("disables sending while offline or when the selected provider is unavailable", () => {
    const props = workspaceProps({ providerAvailable: false });
    const view = render(<ChatWorkspace {...props} />);

    const composer = screen.getByLabelText("Message Fairy");
    fireEvent.change(composer, { target: { value: "Cannot send" } });
    expect(composer).toBeEnabled();
    expect(screen.getAllByText("Provider unavailable")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();

    view.rerender(<ChatWorkspace {...workspaceProps({ offline: true })} />);
    expect(screen.getByLabelText("Message Fairy")).toBeDisabled();
  });

  it("renders a durable tool approval and submits only its decision", async () => {
    const user = userEvent.setup();
    const pending: Approval = {
      id: "0198f4de-0114-7000-8000-000000000041",
      task_id: TURN.task_id,
      command_run_id: "0198f4de-0114-7000-8000-000000000042",
      changeset_id: null,
      tool_invocation_id: "0198f4de-0114-7000-8000-000000000043",
      requested_by: "assistant",
      reason: "Run show a notification",
      decision: "pending",
      decided_by: null,
      created_at: "2026-07-11T00:00:00Z",
      decided_at: null,
    };
    const props = workspaceProps({
      turn: { ...TURN, status: "waiting_for_tool", completed_at: null },
      approvals: [pending],
    });
    render(<ChatWorkspace {...props} />);

    expect(screen.getByText("Approval required")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(props.onDecision).toHaveBeenCalledWith(pending.id, true);
  });
});

const TURN: AssistantTurn = {
  id: "0198f4de-0114-7000-8000-000000000011",
  conversation_id: "0198f4de-0114-7000-8000-000000000002",
  task_id: "0198f4de-0114-7000-8000-000000000003",
  profile_id: "openrouter-deepseek-v4-pro",
  scope_digest: "a".repeat(64),
  memory_snapshot_id: "0198f4de-0114-7000-8000-000000000004",
  memory_snapshot_hash: "b".repeat(64),
  idempotency_key: "turn-1",
  model_selection: null,
  routing_decision: null,
  budget_approval_run_id: null,
  status: "completed",
  cancellation_revision: 0,
  usage: {},
  error_code: null,
  created_at: "2026-07-11T00:00:00Z",
  updated_at: "2026-07-11T00:00:00Z",
  started_at: "2026-07-11T00:00:00Z",
  completed_at: "2026-07-11T00:00:01Z",
};

const MESSAGES: Message[] = [
  {
    id: "0198f4de-0114-7000-8000-000000000021",
    conversation_id: TURN.conversation_id,
    task_id: TURN.task_id,
    turn_id: TURN.id,
    sequence: 1,
    role: "user",
    visibility: "user",
    content: "Hello Fairy",
    created_at: "2026-07-11T00:00:00Z",
  },
  {
    id: "0198f4de-0114-7000-8000-000000000022",
    conversation_id: TURN.conversation_id,
    task_id: TURN.task_id,
    turn_id: TURN.id,
    sequence: 2,
    role: "assistant",
    visibility: "user",
    content: "Hello from the durable ledger",
    created_at: "2026-07-11T00:00:01Z",
  },
];

const PROVIDERS: ProviderProfile[] = [
  {
    id: "openrouter-deepseek-v4-pro",
    display_name: "DeepSeek V4 Pro",
    kind: "openai_compatible",
    base_url: "https://openrouter.ai/api/v1",
    model_id: "deepseek/deepseek-v4-pro",
    capabilities: ["text", "tools", "structured_output"],
    fallback_profile_id: null,
    timeout_seconds: 60,
    enabled: true,
    credential_required: true,
    credential_configured: true,
  },
];

const HEALTH: ProviderHealth[] = [
  {
    profile_id: "openrouter-deepseek-v4-pro",
    status: "available",
    error_code: null,
    diagnostics: [],
  },
];

function workspaceProps(
  overrides: Partial<ChatWorkspaceProps> & { providerAvailable?: boolean } = {},
): ChatWorkspaceProps {
  const { providerAvailable = true, ...props } = overrides;
  return {
    conversationAvailable: true,
    messages: MESSAGES,
    events: [],
    streamedText: "",
    pendingUserMessage: null,
    turn: TURN,
    turnTraces: {},
    approvals: [],
    providers: PROVIDERS,
    providerHealth: providerAvailable
      ? HEALTH
      : [{ ...HEALTH[0], status: "unavailable", error_code: "PROVIDER_UNAVAILABLE" }],
    selectedProfileId: "openrouter-deepseek-v4-pro",
    modelCatalog: null,
    modelSelection: null,
    modelSelectionLoading: false,
    modelSelectionBlockReason: providerAvailable ? null : "Provider unavailable",
    visionAvailable: false,
    isBusy: false,
    isActing: false,
    offline: false,
    developerMode: false,
    error: null,
    slashCommands: [
      {
        name: "new",
        description: "Start a durable conversation.",
        argument_hint: null,
        required_operation: "workspace.create_scratch",
        available: true,
      },
      {
        name: "permission",
        description: "Change the permission profile.",
        argument_hint: "<observe|standard|autonomous>",
        required_operation: null,
        available: true,
      },
    ],
    onNewConversation: vi.fn(async () => undefined),
    onSwitchProject: vi.fn(),
    onPermissionChange: vi.fn(async () => undefined),
    onSend: vi.fn(async () => undefined),
    onCancel: vi.fn(async () => undefined),
    onRetry: vi.fn(async () => undefined),
    onRetryPending: vi.fn(async () => undefined),
    onDeletePending: vi.fn(),
    onTakePendingForEdit: vi.fn(() => null),
    onCopyMessage: vi.fn(async () => undefined),
    onOpenMessageLink: vi.fn(async () => undefined),
    onDecision: vi.fn(async () => undefined),
    onSelectModel: vi.fn(async () => undefined),
    onOpenModelSettings: vi.fn(async () => undefined),
    ...props,
  };
}
