import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CoreClient } from "../core/client";
import type { InvokeFunction } from "../core/tauriTransport";
import { RealtimeCompanionWindowApp } from "./RealtimeCompanionWindowApp";

vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async () => () => undefined),
}));
vi.mock("../voice/nativeVoice", () => ({
  startRealtimeVoice: vi.fn(),
}));

afterEach(cleanup);

describe("RealtimeCompanionWindowApp", () => {
  it("owns the Companion UI and hides its secondary window on close", async () => {
    const invokeMock = vi.fn(async (
      command: string,
      _args?: Record<string, unknown>,
    ) => {
      if (command === "desktop_preferences_get") {
        return {
          realtime_provider: "auto",
          realtime_voice_mode: "native",
          realtime_game_audio_default: false,
          realtime_memory_enabled: true,
          realtime_max_session_minutes: 30,
        };
      }
      if (command === "list_capture_surfaces") return [];
      if (command === "provider_realtime_status") {
        return { provider: "zhipu", configured: true };
      }
      if (command === "hide_companion_window") return undefined;
      throw new Error(`unexpected invoke: ${command}`);
    });
    const invoke: InvokeFunction = (command, args) =>
      invokeMock(command, args) as Promise<never>;
    const client = {
      sessions: {
        list: vi.fn(async () => ({ items: [] })),
      },
      memories: { save: vi.fn() },
      worker: {
        status: vi.fn(async () => ({
          running: false,
          session_id: null,
          audio_input_ms: 0,
          audio_output_ms: 0,
          video_frame_count: 0,
          interruption_count: 0,
          tool_call_count: 0,
        })),
      },
    } as unknown as CoreClient["realtime"];

    render(<RealtimeCompanionWindowApp client={client} invoke={invoke} />);

    expect(
      await screen.findByRole("dialog", { name: "Game companion" }),
    ).not.toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(invokeMock).toHaveBeenCalledWith("hide_companion_window", undefined),
    );
    expect(invokeMock).not.toHaveBeenCalledWith("open_main_window", undefined);
  });
});
