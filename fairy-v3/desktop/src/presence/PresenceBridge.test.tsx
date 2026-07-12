import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PresenceChannel, PresenceRequest } from "./channel";
import { PresenceBridge } from "./PresenceBridge";

afterEach(cleanup);

describe("PresenceBridge", () => {
  it("exposes only typed scratch, voice, and projection routes", async () => {
    const requests: { listener?: (request: PresenceRequest) => void } = {};
    const channel: PresenceChannel = {
      publishProjection: vi.fn(),
      requestProjection: vi.fn(),
      requestWorkspaceOpen: vi.fn(),
      requestNewChat: vi.fn(),
      requestChatSend: vi.fn(),
      requestVoiceStop: vi.fn(),
      onProjection: vi.fn(() => () => undefined),
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
    const onStopVoice = vi.fn();
    render(
      <PresenceBridge
        channelFactory={() => channel}
        events={[]}
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
    requests.listener?.({ kind: "chat.send", text: "Hello" });
    requests.listener?.({ kind: "voice.stop" });

    await waitFor(() => expect(onNewChat).toHaveBeenCalledOnce());
    expect(onSend).not.toHaveBeenCalled();
    pendingNewChat.resolve?.();
    await waitFor(() => expect(onSend).toHaveBeenCalledWith("Hello"));
    expect(onStopVoice).toHaveBeenCalledOnce();
    expect(channel.publishProjection).toHaveBeenLastCalledWith(
      expect.objectContaining({
        speaking: true,
        reply: expect.objectContaining({ id: "scratch-1", kind: "scratch" }),
      }),
    );
  });
});
