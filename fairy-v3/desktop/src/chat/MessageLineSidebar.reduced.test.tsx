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
          key: "message:reduced",
          role: "assistant",
          label: "Fairy",
          excerpt: "Reduced motion response",
          streaming: false,
        },
      ]}
      activeKey="message:reduced"
      onNavigate={onNavigate}
    />,
  );

  fireEvent.click(
    screen.getByRole("button", { name: "Fairy: Reduced motion response" }),
  );
  expect(onNavigate).toHaveBeenCalledWith("message:reduced", false);
});
