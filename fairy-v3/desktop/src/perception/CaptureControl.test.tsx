import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CaptureControl,
  type CaptureClient,
  type PendingImageAttachment,
} from "./CaptureControl";

const client: CaptureClient = {
  listSurfaces: vi.fn(async () => [
    {
      kind: "display" as const,
      source_id: "display-1",
      label: "Primary display",
      width: 1920,
      height: 1080,
      is_primary: true,
    },
    {
      kind: "window" as const,
      source_id: "window-2",
      label: "Game window",
      width: 1280,
      height: 720,
      is_primary: false,
    },
  ]),
  capture: vi.fn(async () => ({
    kind: "window" as const,
    source_id: "window-2",
    source_label: "Game window",
    media_type: "image/png" as const,
    png_base64: "iVBORw0KGgo=",
    width: 1280,
    height: 720,
    byte_length: 8,
    content_hash: "a".repeat(64),
    captured_at_ms: 1_784_000_000_000,
  })),
};

afterEach(cleanup);

describe("CaptureControl", () => {
  it("previews a selected source and requires an explicit attach decision", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn<(value: PendingImageAttachment | null) => void>();
    render(
      <CaptureControl
        client={client}
        disabled={false}
        visionAvailable
        value={null}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Capture screen" }));
    await user.selectOptions(screen.getByLabelText("Capture source"), "window:window-2");
    await user.click(
      screen.getByRole("button", { name: "Capture selected source" }),
    );

    expect(await screen.findByAltText("Game window capture preview")).toBeVisible();
    expect(onChange).not.toHaveBeenCalled();
    await user.click(screen.getByRole("checkbox", { name: "Keep with conversation" }));
    await user.click(screen.getByRole("button", { name: "Attach capture" }));

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        source_label: "Game window",
        persistence: "conversation",
      }),
    );
  });

  it("discards preview data and disables capture without a vision provider", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(
      <CaptureControl
        client={client}
        disabled={false}
        visionAvailable
        value={null}
        onChange={onChange}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Capture screen" }));
    await user.selectOptions(screen.getByLabelText("Capture source"), "window:window-2");
    await user.click(
      await screen.findByRole("button", { name: "Capture selected source" }),
    );
    await user.click(await screen.findByRole("button", { name: "Discard capture" }));
    expect(onChange).not.toHaveBeenCalled();

    rerender(
      <CaptureControl
        client={client}
        disabled={false}
        visionAvailable={false}
        value={null}
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("button", { name: "Capture screen" })).toBeDisabled();
  });
});
