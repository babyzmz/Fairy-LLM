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

  it("routes slash commands without sending them as model text", async () => {
    const user = userEvent.setup();
    const props = workspaceProps();
    render(<ChatWorkspace {...props} />);

    await user.type(screen.getByLabelText("Message Fairy"), "  /new");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(props.onNewConversation).toHaveBeenCalledOnce());
    expect(props.onSend).not.toHaveBeenCalled();
  });

  it("deduplicates streamed chunks and exposes stop then retry states", async () => {
    const user = userEvent.setup();
    const props = workspaceProps({
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

  it("disables sending while offline or when the selected provider is unavailable", () => {
    const props = workspaceProps({ offline: true, providerAvailable: false });
    render(<ChatWorkspace {...props} />);

    const composer = screen.getByLabelText("Message Fairy");
    fireEvent.change(composer, { target: { value: "Cannot send" } });
    expect(composer).toBeDisabled();
    expect(screen.getByText("Provider unavailable")).toBeVisible();
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
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
  profile_id: "openrouter-free",
  scope_digest: "a".repeat(64),
  memory_snapshot_id: "0198f4de-0114-7000-8000-000000000004",
  memory_snapshot_hash: "b".repeat(64),
  idempotency_key: "turn-1",
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
    id: "openrouter-free",
    display_name: "OpenRouter Free",
    kind: "openai_compatible",
    base_url: "https://openrouter.ai/api/v1",
    model_id: "openrouter/free",
    capabilities: ["text", "tools"],
    fallback_profile_id: null,
    timeout_seconds: 60,
    enabled: true,
    credential_required: true,
    credential_configured: true,
  },
];

const HEALTH: ProviderHealth[] = [
  {
    profile_id: "openrouter-free",
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
    streamedText: "",
    turn: TURN,
    approvals: [],
    providers: PROVIDERS,
    providerHealth: providerAvailable
      ? HEALTH
      : [{ ...HEALTH[0], status: "unavailable", error_code: "PROVIDER_UNAVAILABLE" }],
    selectedProfileId: "openrouter-free",
    isBusy: false,
    isActing: false,
    offline: false,
    developerMode: false,
    error: null,
    onProfileChange: vi.fn(),
    onDeveloperModeChange: vi.fn(),
    onNewConversation: vi.fn(async () => undefined),
    onSwitchProject: vi.fn(),
    onPermissionChange: vi.fn(),
    onSend: vi.fn(async () => undefined),
    onCancel: vi.fn(async () => undefined),
    onRetry: vi.fn(async () => undefined),
    onDecision: vi.fn(async () => undefined),
    ...props,
  };
}
