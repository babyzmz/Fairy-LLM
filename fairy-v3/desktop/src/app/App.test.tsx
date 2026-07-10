import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("checks Core readiness and exposes it in the persistent context bar", async () => {
    const health = vi.fn().mockResolvedValue({ status: "ok" });

    render(<App client={{ health }} />);

    expect(screen.getByRole("banner")).toHaveTextContent("Core starting");
    expect(await screen.findByText("Core ready")).toBeVisible();
    expect(health).toHaveBeenCalledTimes(1);
  });
});
