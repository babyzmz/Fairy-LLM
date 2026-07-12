import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DesktopHostRequired } from "./DesktopHostRequired";

afterEach(cleanup);

describe("DesktopHostRequired", () => {
  it("explains the trusted host boundary without rendering disabled workspace controls", () => {
    render(<DesktopHostRequired />);

    expect(screen.getByRole("heading", { name: "Open Fairy as a Windows app" })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Core not attached");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
