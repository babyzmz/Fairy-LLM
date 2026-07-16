import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { InputLiquidGlassCanvas } from "./InputLiquidGlassCanvas";

afterEach(cleanup);

describe("InputLiquidGlassCanvas", () => {
  it("disables the second capture, WebGL context, and CSS material together", () => {
    render(
      <div data-testid="field">
        <InputLiquidGlassCanvas experimentMode="single-renderer" />
      </div>,
    );

    expect(screen.getByTestId("field")).toHaveAttribute(
      "data-experiment-mode",
      "single-renderer",
    );
    expect(screen.getByTestId("presence-input-glass")).toHaveAttribute(
      "data-backdrop-status",
      "disabled",
    );
  });
});
