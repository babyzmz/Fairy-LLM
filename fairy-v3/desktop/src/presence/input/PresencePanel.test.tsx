import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PresenceView } from "../domain/projection";
import {
  compactInputHeightForText,
  compactInputWidthForText,
  PresencePanel,
  type PresencePanelActions,
} from "./PresencePanel";

afterEach(cleanup);

const actions = new Proxy({}, {
  get: () => vi.fn(),
}) as PresencePanelActions;

const view: PresenceView = {
  activity: "ambient",
  density: "normal",
  work_state: "idle",
  status_text: "Standing by",
  last_cursor: 0,
  last_event_id: null,
  updated_at_ms: 0,
  recent_activity_ms: [],
  notice: null,
  reply: null,
  ambient_dialogue: null,
  speaking: false,
};

describe("PresencePanel optical boundary", () => {
  it("keeps text and controls as a transparent DOM overlay without a renderer", () => {
    const { container } = render(
      <PresencePanel
        actions={actions}
        alwaysOnTop
        autoPlay={false}
        inputOpen
        menuOpen={false}
        muted={false}
        reply={null}
        submission={null}
        view={view}
        visible
      />,
    );

    expect(screen.getByTestId("presence-input-field")).toHaveAttribute(
      "data-optical-layer",
      "transparent-overlay",
    );
    expect(screen.queryByRole("button", { name: "Move Fairy" })).toBeNull();
    expect(screen.getByLabelText("Quick message to Fairy")).toBeVisible();
    expect(container.querySelector("canvas")).toBeNull();
  });

  it("grows the compact surface with the message and keeps it bounded", () => {
    const onCompactSizeChange = vi.fn();
    render(
      <PresencePanel
        actions={actions}
        alwaysOnTop
        autoPlay={false}
        inputOpen
        menuOpen={false}
        muted={false}
        onCompactSizeChange={onCompactSizeChange}
        reply={null}
        submission={null}
        view={view}
        visible
      />,
    );
    fireEvent.change(screen.getByLabelText("Quick message to Fairy"), {
      target: { value: "这是一个足够长的消息，用来验证桌宠输入框会随内容增长" },
    });
    expect(onCompactSizeChange).toHaveBeenLastCalledWith(360, 84);
    expect(compactInputWidthForText(0)).toBe(220);
    expect(compactInputWidthForText(202)).toBe(308);
    expect(compactInputWidthForText(2_000)).toBe(360);
    expect(compactInputHeightForText("", 0)).toBe(64);
    expect(compactInputHeightForText("one\ntwo\nthree\nfour", 0)).toBe(104);
  });

  it("renders only failed submissions as fallback cards", () => {
    const { rerender } = render(
      <PresencePanel
        actions={actions}
        alwaysOnTop
        autoPlay={false}
        inputOpen={false}
        menuOpen={false}
        muted={false}
        reply={null}
        submission={{
          id: "submission-1",
          phase: "sending",
          title: "Sending to Fairy",
          detail: "Starting a private scratch chat",
          canCancel: true,
          canRetry: false,
        }}
        view={view}
        visible
      />,
    );
    expect(screen.queryByRole("status")).toBeNull();

    rerender(
      <PresencePanel
        actions={actions}
        alwaysOnTop
        autoPlay={false}
        inputOpen={false}
        menuOpen={false}
        muted={false}
        reply={null}
        submission={{
          id: "submission-1",
          phase: "failed",
          title: "Fairy could not send this",
          detail: "Open Fairy to retry.",
          canCancel: false,
          canRetry: true,
        }}
        view={view}
        visible
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Fairy could not send this");
  });

  it("keeps ambient dialogue below real reply and submission projections", () => {
    const ambientDialogue = {
      presentation_id: "ambient-1",
      dialogue_id: "idle.short.001",
      text: "A reviewed idle line.",
      source: "authored_original" as const,
      trigger: "idle_short" as const,
      locale: "en" as const,
      tts_allowed: false,
      expires_at: "2026-07-23T01:00:00Z",
      persona_digest: "digest",
    };
    const { rerender } = render(
      <PresencePanel
        actions={actions}
        alwaysOnTop
        ambientDialogue={ambientDialogue}
        autoPlay={false}
        inputOpen={false}
        menuOpen={false}
        muted={false}
        reply={null}
        submission={{
          id: "submission-1",
          phase: "sending",
          title: "Sending to Fairy",
          detail: "Starting a private scratch chat",
          canCancel: true,
          canRetry: false,
        }}
        view={view}
        visible
      />,
    );
    expect(screen.queryByText("A reviewed idle line.")).toBeNull();

    rerender(
      <PresencePanel
        actions={actions}
        alwaysOnTop
        ambientDialogue={ambientDialogue}
        autoPlay={false}
        inputOpen={false}
        menuOpen={false}
        muted={false}
        reply={null}
        submission={{
          id: "submission-1",
          phase: "failed",
          title: "Fairy could not send this",
          detail: "Open Fairy to retry.",
          canCancel: false,
          canRetry: true,
        }}
        view={view}
        visible
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Fairy could not send this");
    expect(screen.queryByText("A reviewed idle line.")).toBeNull();

    rerender(
      <PresencePanel
        actions={actions}
        alwaysOnTop
        ambientDialogue={ambientDialogue}
        autoPlay={false}
        inputOpen={false}
        menuOpen={false}
        muted={false}
        reply={null}
        submission={null}
        view={view}
        visible
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("A reviewed idle line.");
  });
});
