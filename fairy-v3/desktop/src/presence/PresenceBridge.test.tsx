import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
const runtime = vi.hoisted(() => ({ native: false }));
vi.mock("@tauri-apps/api/core", () => ({ isTauri: () => runtime.native }));

import type { PresenceChannel, PresenceRequest } from "./channel";
import { PresenceBridge } from "./PresenceBridge";

afterEach(() => { cleanup(); runtime.native = false; });

describe("PresenceBridge", () => {
  it("reports only the ambient dialogue that remains visible after priority projection", async () => {
    const channel: PresenceChannel = {
      publishProjection: vi.fn(),
      publishSubmission: vi.fn(),
      requestProjection: vi.fn(),
      requestWorkspaceOpen: vi.fn(),
      requestNewChat: vi.fn(),
      requestChatSend: vi.fn(),
      requestChatCancel: vi.fn(),
      requestVoiceStop: vi.fn(),
      onProjection: vi.fn(() => () => undefined),
      onSubmission: vi.fn(() => () => undefined),
      onRequest: vi.fn(() => () => undefined),
      close: vi.fn(),
    };
    const onAmbientVisibilityChange = vi.fn();
    const ambientDialogue = {
      presentation_id: "019f4b33-c7fb-7652-a127-75914303dbeb",
      dialogue_id: "idle.long.01",
      text: "The quiet interval remains under control.",
      source: "authored_original" as const,
      trigger: "idle_long" as const,
      locale: "en",
      tts_allowed: true,
      expires_at: "2026-07-23T09:00:30Z",
      persona_digest: "a".repeat(64),
    };
    const rendered = render(
      <PresenceBridge
        ambientDialogue={ambientDialogue}
        channelFactory={() => channel}
        events={[]}
        onAmbientVisibilityChange={onAmbientVisibilityChange}
      />,
    );

    await waitFor(() => expect(onAmbientVisibilityChange).toHaveBeenLastCalledWith(
      ambientDialogue,
    ));
    rendered.rerender(
      <PresenceBridge
        ambientDialogue={ambientDialogue}
        channelFactory={() => channel}
        events={[]}
        onAmbientVisibilityChange={onAmbientVisibilityChange}
        reply={{
          id: "reply-1",
          text: "The real reply has priority.",
          kind: "scratch",
          streaming: false,
        }}
      />,
    );

    await waitFor(() => expect(onAmbientVisibilityChange).toHaveBeenLastCalledWith(null));
    expect(channel.publishProjection).toHaveBeenLastCalledWith(
      expect.objectContaining({ ambient_dialogue: null }),
    );
  });

  it("exposes only typed scratch, voice, and projection routes", async () => {
    const requests: { listener?: (request: PresenceRequest) => void } = {};
    const channel: PresenceChannel = {
      publishProjection: vi.fn(),
      publishSubmission: vi.fn(),
      requestProjection: vi.fn(),
      requestWorkspaceOpen: vi.fn(),
      requestNewChat: vi.fn(),
      requestChatSend: vi.fn(),
      requestChatCancel: vi.fn(),
      requestVoiceStop: vi.fn(),
      onProjection: vi.fn(() => () => undefined),
      onSubmission: vi.fn(() => () => undefined),
      onRequest(listener) {
        requests.listener = listener;
        return () => {
          delete requests.listener;
        };
      },
      close: vi.fn(),
    };
    const pendingNewChat: { resolve?: () => void } = {};
    const onNewChat = vi.fn(
      () => new Promise<void>((resolve) => {
        pendingNewChat.resolve = resolve;
      }),
    );
    const onSend = vi.fn();
    const onCancel = vi.fn();
    const onStopVoice = vi.fn();
    render(
      <PresenceBridge
        channelFactory={() => channel}
        events={[]}
        onCancel={onCancel}
        onNewChat={onNewChat}
        onSend={onSend}
        onStopVoice={onStopVoice}
        reply={{ id: "scratch-1", text: "Bounded reply", kind: "scratch", streaming: true }}
        speaking
      />,
    );

    await waitFor(() => expect(channel.publishProjection).toHaveBeenCalled());
    requests.listener?.({ kind: "projection" });
    requests.listener?.({ kind: "chat.new" });
    requests.listener?.({ kind: "chat.send", submission_id: "submission-1", text: "Hello" });
    requests.listener?.({ kind: "chat.cancel", submission_id: "submission-1" });
    requests.listener?.({ kind: "voice.stop" });

    await waitFor(() => expect(onNewChat).toHaveBeenCalledOnce());
    expect(onSend).not.toHaveBeenCalled();
    pendingNewChat.resolve?.();
    await waitFor(() => expect(onSend).toHaveBeenCalledWith("Hello"));
    await waitFor(() => expect(onCancel).toHaveBeenCalledOnce());
    expect(onStopVoice).toHaveBeenCalledOnce();
    expect(channel.publishSubmission).toHaveBeenCalledWith({
      submission_id: "submission-1",
      status: "accepted",
      failure: null,
    });
    expect(channel.publishSubmission).toHaveBeenCalledWith({
      submission_id: "submission-1",
      status: "cancelled",
      failure: null,
    });
    expect(channel.publishProjection).toHaveBeenLastCalledWith(
      expect.objectContaining({
        speaking: true,
        reply: expect.objectContaining({ id: "scratch-1", kind: "scratch" }),
      }),
    );
    runtime.native = true;
    onNewChat.mockClear(); onSend.mockClear(); onCancel.mockClear();
    requests.listener?.({ kind: "chat.new" });
    requests.listener?.({ kind: "chat.send", submission_id: "late-native", text: "Do not duplicate" });
    requests.listener?.({ kind: "chat.cancel", submission_id: "late-native" });
    await Promise.resolve(); await Promise.resolve();
    expect(onNewChat).not.toHaveBeenCalled();
    expect(onSend).not.toHaveBeenCalled();
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("returns only a safe failure category to the companion", async () => {
    const requests: { listener?: (request: PresenceRequest) => void } = {};
    const channel: PresenceChannel = {
      publishProjection: vi.fn(),
      publishSubmission: vi.fn(),
      requestProjection: vi.fn(),
      requestWorkspaceOpen: vi.fn(),
      requestNewChat: vi.fn(),
      requestChatSend: vi.fn(),
      requestChatCancel: vi.fn(),
      requestVoiceStop: vi.fn(),
      onProjection: vi.fn(() => () => undefined),
      onSubmission: vi.fn(() => () => undefined),
      onRequest(listener) {
        requests.listener = listener;
        return () => undefined;
      },
      close: vi.fn(),
    };
    render(
      <PresenceBridge
        channelFactory={() => channel}
        events={[]}
        onSend={() => Promise.reject(new Error("Network connection refused: secret diagnostic"))}
      />,
    );

    await waitFor(() => expect(channel.publishProjection).toHaveBeenCalled());
    requests.listener?.({
      kind: "chat.send",
      submission_id: "submission-failed",
      text: "Hello",
    });

    await waitFor(() => expect(channel.publishSubmission).toHaveBeenCalledWith({
      submission_id: "submission-failed",
      status: "failed",
      failure: "offline",
    }));
    expect(JSON.stringify(vi.mocked(channel.publishSubmission).mock.calls)).not.toContain(
      "secret diagnostic",
    );
  });
});
