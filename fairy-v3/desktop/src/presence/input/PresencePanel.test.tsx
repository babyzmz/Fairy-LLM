import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PresenceView } from "../domain/projection";
import { PresencePanel, type PresencePanelActions } from "./PresencePanel";

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
    expect(screen.getByLabelText("Quick message to Fairy")).toBeVisible();
    expect(container.querySelector("canvas")).toBeNull();
  });
});
