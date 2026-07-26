import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("motion/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("motion/react")>();
  return {
    ...actual,
    useReducedMotion: () => true,
  };
});

import { MessageLineSidebar } from "./MessageLineSidebar";

afterEach(cleanup);

it("uses immediate anchor navigation when reduced motion is requested", () => {
  const onNavigate = vi.fn();
  render(
    <MessageLineSidebar
      items={[
        {
          key: "turn:reduced",
          anchorKey: "message:reduced",
          messageIds: ["request:reduced", "response:reduced"],
          title: "Reduced motion request",
          response: "Reduced motion response",
          streaming: false,
        },
      ]}
      activeKey="turn:reduced"
      onNavigate={onNavigate}
    />,
  );

  fireEvent.click(
    screen.getByRole("button", {
      name: "Reduced motion request: Reduced motion response",
    }),
  );
  expect(onNavigate).toHaveBeenCalledWith("turn:reduced", false);
});
