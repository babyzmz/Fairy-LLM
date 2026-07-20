import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PresenceView } from "../domain/projection";
import {
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
    const onCompactWidthChange = vi.fn();
    render(
      <PresencePanel
        actions={actions}
        alwaysOnTop
        autoPlay={false}
        inputOpen
        menuOpen={false}
        muted={false}
        onCompactWidthChange={onCompactWidthChange}
        reply={null}
        submission={null}
        view={view}
        visible
      />,
    );
    fireEvent.change(screen.getByLabelText("Quick message to Fairy"), {
      target: { value: "这是一个足够长的消息，用来验证桌宠输入框会随内容增长" },
    });
    expect(onCompactWidthChange).toHaveBeenLastCalledWith(420);
    expect(compactInputWidthForText(0)).toBe(280);
    expect(compactInputWidthForText(202)).toBe(308);
    expect(compactInputWidthForText(2_000)).toBe(420);
  });
});
