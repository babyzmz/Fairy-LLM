import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BrowserPanel } from "./BrowserPanel";
import type { WorkspaceModel } from "./workspaceTypes";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("BrowserPanel", () => {
  it("navigates using the controlled browser instead of the runtime iframe", () => {
    const navigateBrowser = vi.fn(async () => undefined);
    render(<BrowserPanel model={browserModel({ navigateBrowser })} runtimeUrl={null} />);

    fireEvent.change(screen.getByLabelText("Browser address"), { target: { value: "example.org" } });
    fireEvent.submit(screen.getByLabelText("Browser address").closest("form")!);

    expect(navigateBrowser).toHaveBeenCalledWith("https://example.org/");
  });

  it("captures snapshots only while the Browser surface is mounted", () => {
    const setBrowserSurfaceActive = vi.fn();
    const view = render(
      <BrowserPanel model={browserModel({ setBrowserSurfaceActive })} runtimeUrl={null} />,
    );

    expect(setBrowserSurfaceActive).toHaveBeenCalledWith(true);
    view.unmount();
    expect(setBrowserSurfaceActive).toHaveBeenLastCalledWith(false);
  });

  it("maps screenshot clicks through the snapshot viewport and revision", () => {
    const executeBrowserAction = vi.fn(async () => undefined);
    vi.spyOn(HTMLImageElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 10,
      top: 20,
      width: 500,
      height: 250,
      right: 510,
      bottom: 270,
      x: 10,
      y: 20,
      toJSON: () => ({}),
    });
    render(<BrowserPanel model={browserModel({ executeBrowserAction })} runtimeUrl={null} />);

    fireEvent.click(screen.getByAltText("Current controlled browser page"), {
      clientX: 260,
      clientY: 145,
    });

    expect(executeBrowserAction).toHaveBeenCalledWith({
      kind: "click",
      x: 720,
      y: 450,
      expected_page_revision: 4,
    });
  });

  it("ignores screenshot clicks in object-fit letterboxing", () => {
    const executeBrowserAction = vi.fn(async () => undefined);
    vi.spyOn(HTMLImageElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 10,
      top: 20,
      width: 500,
      height: 250,
      right: 510,
      bottom: 270,
      x: 10,
      y: 20,
      toJSON: () => ({}),
    });
    render(<BrowserPanel model={browserModel({ executeBrowserAction })} runtimeUrl={null} />);

    fireEvent.click(screen.getByAltText("Current controlled browser page"), {
      clientX: 20,
      clientY: 145,
    });

    expect(executeBrowserAction).not.toHaveBeenCalled();
  });

  it("starts the controlled browser from the active Runtime", () => {
    const startBrowser = vi.fn(async () => undefined);
    render(<BrowserPanel model={browserModel({ startBrowser, browserSession: null })} runtimeUrl="http://127.0.0.1:4173/" />);

    fireEvent.click(screen.getByRole("button", { name: /Open the current Runtime/ }));

    expect(startBrowser).toHaveBeenCalledWith("http://127.0.0.1:4173/");
  });

  it("uses the Core tab lifecycle for new, selected, and closed tabs", () => {
    const openBrowserTab = vi.fn(async () => undefined);
    const selectBrowserTab = vi.fn(async () => undefined);
    const closeBrowserTab = vi.fn(async () => undefined);
    const model = browserModel({ openBrowserTab, selectBrowserTab, closeBrowserTab });
    model.browserSession!.tabs.push({
      ...model.browserSession!.tabs[0],
      id: "019f666f-f8b4-7000-8000-000000000003",
      title: "Second",
      active: false,
    });
    render(<BrowserPanel model={model} runtimeUrl={null} />);

    fireEvent.click(screen.getByRole("tab", { name: "Second" }));
    fireEvent.click(screen.getByRole("button", { name: "Close Second" }));
    fireEvent.click(screen.getByRole("button", { name: "New browser tab" }));

    expect(selectBrowserTab).toHaveBeenCalledWith("019f666f-f8b4-7000-8000-000000000003");
    expect(closeBrowserTab).toHaveBeenCalledWith("019f666f-f8b4-7000-8000-000000000003");
    expect(openBrowserTab).toHaveBeenCalledWith();
  });

  it("shows an inline validation error instead of ignoring an invalid address", () => {
    const navigateBrowser = vi.fn(async () => undefined);
    render(<BrowserPanel model={browserModel({ navigateBrowser })} runtimeUrl={null} />);

    fireEvent.change(screen.getByLabelText("Browser address"), { target: { value: "file:///secret" } });
    fireEvent.submit(screen.getByLabelText("Browser address").closest("form")!);

    expect(navigateBrowser).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("HTTP or HTTPS");
  });

  it("does not render a stale screenshot for an interrupted session", () => {
    const model = browserModel();
    const startBrowser = vi.fn(async () => undefined);
    model.browserSession = {
      ...model.browserSession!,
      status: "interrupted",
      public_error: "Browser Worker stopped",
    };
    model.browserSnapshot = null;
    model.startBrowser = startBrowser;
    render(<BrowserPanel model={model} runtimeUrl={null} />);

    expect(screen.queryByAltText("Current controlled browser page")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Resume browser" }));
    expect(startBrowser).toHaveBeenCalledWith();
    expect(screen.getByText("Browser session interrupted")).toBeInTheDocument();
  });
});

function browserModel(overrides: Partial<WorkspaceModel> = {}): WorkspaceModel {
  return {
    browserHealth: { available: true, browser_name: "Microsoft Edge", browser_version: null, error_code: null, diagnostic: null },
    browserSession: {
      id: "019f666f-f8b4-7000-8000-000000000001",
      project_id: null,
      conversation_id: null,
      task_id: null,
      execution_target: "local",
      profile_kind: "ephemeral",
      status: "active",
      active_tab_id: "019f666f-f8b4-7000-8000-000000000002",
      tabs: [{
        id: "019f666f-f8b4-7000-8000-000000000002",
        session_id: "019f666f-f8b4-7000-8000-000000000001",
        title: "Example",
        url: "https://example.org/",
        active: true,
        loading: false,
        revision: 4,
      }],
      revision: 1,
      created_at: "2026-07-21T00:00:00Z",
      updated_at: "2026-07-21T00:00:00Z",
      error_code: null,
      public_error: null,
    },
    browserSnapshot: {
      session_id: "019f666f-f8b4-7000-8000-000000000001",
      tab_id: "019f666f-f8b4-7000-8000-000000000002",
      page_revision: 4,
      url: "https://example.org/",
      title: "Example",
      aria_snapshot: "- document",
      viewport_width: 1440,
      viewport_height: 900,
      screenshot_data_url: "data:image/jpeg;base64,AA==",
      captured_at: "2026-07-21T00:00:00Z",
    },
    browserLoading: false,
    browserError: null,
    startBrowser: vi.fn(async () => undefined),
    stopBrowser: vi.fn(async () => undefined),
    navigateBrowser: vi.fn(async () => undefined),
    openBrowserTab: vi.fn(async () => undefined),
    selectBrowserTab: vi.fn(async () => undefined),
    closeBrowserTab: vi.fn(async () => undefined),
    executeBrowserAction: vi.fn(async () => undefined),
    refreshBrowser: vi.fn(async () => undefined),
    setBrowserSurfaceActive: vi.fn(),
    ...overrides,
  } as WorkspaceModel;
}
