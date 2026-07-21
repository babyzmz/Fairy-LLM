import {
  ArrowLeft,
  ArrowRight,
  Camera,
  ExternalLink,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldAlert,
  Square,
  X,
  Plus,
} from "lucide-react";
import { type FormEvent, type MouseEvent, useEffect, useState } from "react";

import type { WorkspaceModel } from "./workspaceTypes";

export function BrowserPanel({ model, runtimeUrl }: { model: WorkspaceModel; runtimeUrl: string | null }) {
  const activeTab = model.browserSession?.tabs.find(
    (tab) => tab.id === model.browserSession?.active_tab_id,
  ) ?? null;
  const [address, setAddress] = useState(activeTab?.url ?? runtimeUrl ?? "https://example.com");
  const [addressError, setAddressError] = useState<string | null>(null);

  useEffect(() => {
    model.setBrowserSurfaceActive(true);
    return () => model.setBrowserSurfaceActive(false);
  }, [model.setBrowserSurfaceActive]);

  useEffect(() => {
    if (activeTab?.url && activeTab.url !== "about:blank") {
      setAddress(activeTab.url);
      setAddressError(null);
    }
  }, [activeTab?.url]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const target = normalizeAddress(address);
    if (target === null) {
      setAddressError("Enter an HTTP or HTTPS address.");
      return;
    }
    setAddressError(null);
    void model.navigateBrowser(target);
  };
  const start = () => {
    if (
      model.browserSession?.status === "interrupted"
      || model.browserSession?.status === "suspended"
    ) {
      setAddressError(null);
      void model.startBrowser();
      return;
    }
    const target = normalizeAddress(address);
    if (target === null) {
      setAddressError("Enter an HTTP or HTTPS address.");
      return;
    }
    setAddressError(null);
    void model.startBrowser(target);
  };
  const execute = (kind: "go_back" | "go_forward" | "reload") => {
    void model.executeBrowserAction({ kind });
  };
  const clickFrame = (event: MouseEvent<HTMLImageElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    if (bounds.width < 1 || bounds.height < 1) return;
    const viewportWidth = model.browserSnapshot?.viewport_width ?? 1365;
    const viewportHeight = model.browserSnapshot?.viewport_height ?? 768;
    const imageAspect = viewportWidth / viewportHeight;
    const boundsAspect = bounds.width / bounds.height;
    const renderedWidth = boundsAspect > imageAspect
      ? bounds.height * imageAspect
      : bounds.width;
    const renderedHeight = boundsAspect > imageAspect
      ? bounds.height
      : bounds.width / imageAspect;
    const renderedLeft = bounds.left + (bounds.width - renderedWidth) / 2;
    const renderedTop = bounds.top + (bounds.height - renderedHeight) / 2;
    if (
      event.clientX < renderedLeft
      || event.clientX > renderedLeft + renderedWidth
      || event.clientY < renderedTop
      || event.clientY > renderedTop + renderedHeight
    ) return;
    void model.executeBrowserAction({
      kind: "click",
      x: ((event.clientX - renderedLeft) / renderedWidth) * viewportWidth,
      y: ((event.clientY - renderedTop) / renderedHeight) * viewportHeight,
      expected_page_revision: model.browserSnapshot?.page_revision ?? activeTab?.revision ?? 0,
    });
  };

  return (
    <section className="browser-panel" aria-label="Controlled browser">
      <div className="browser-toolbar-stack">
        <header className="browser-toolbar">
          <div className="browser-nav-actions">
            <button className="icon-button" type="button" aria-label="Back" title="Back" disabled={activeTab === null || model.isActing} onClick={() => execute("go_back")}><ArrowLeft size={15} /></button>
            <button className="icon-button" type="button" aria-label="Forward" title="Forward" disabled={activeTab === null || model.isActing} onClick={() => execute("go_forward")}><ArrowRight size={15} /></button>
            <button className="icon-button" type="button" aria-label="Reload page" title="Reload page" disabled={activeTab === null || model.isActing} onClick={() => execute("reload")}><RefreshCw size={15} /></button>
            <button className="icon-button" type="button" aria-label="Refresh browser view" title="Refresh browser view" disabled={activeTab === null || model.isActing} onClick={() => void model.refreshBrowser()}><Camera size={15} /></button>
          </div>
          <form onSubmit={submit}>
            <ShieldAlert size={14} aria-hidden="true" />
            <input
              aria-label="Browser address"
              aria-invalid={addressError !== null}
              aria-describedby={addressError === null ? undefined : "browser-address-error"}
              value={address}
              onChange={(event) => {
                setAddress(event.target.value);
                setAddressError(null);
              }}
              spellCheck={false}
            />
          </form>
          {model.browserSession?.status === "active" ? (
            <button className="icon-button" type="button" aria-label="Stop browser" title="Stop browser" disabled={model.isActing} onClick={() => void model.stopBrowser()}><Square size={15} /></button>
          ) : (
            <button
              className="icon-button"
              type="button"
              aria-label={model.browserSession?.status === "interrupted" || model.browserSession?.status === "suspended" ? "Resume browser" : "Start browser"}
              title={model.browserSession?.status === "interrupted" || model.browserSession?.status === "suspended" ? "Resume browser" : "Start browser"}
              disabled={model.browserHealth?.available !== true || model.isActing}
              onClick={start}
            ><Play size={15} /></button>
          )}
        </header>
        {addressError !== null ? <p className="browser-address-error" id="browser-address-error" role="alert">{addressError}</p> : null}
      </div>
      {model.browserSession !== null ? (
        <div className="browser-tabs" role="tablist" aria-label="Browser tabs">
          {model.browserSession.tabs.map((tab) => (
            <div className="browser-tab" data-active={tab.id === model.browserSession?.active_tab_id} key={tab.id}>
              <button type="button" role="tab" aria-selected={tab.id === model.browserSession?.active_tab_id} onClick={() => void model.selectBrowserTab(tab.id)}>
                <span>{tab.title || "New tab"}</span>
              </button>
              <button type="button" aria-label={`Close ${tab.title || "tab"}`} title="Close tab" onClick={() => void model.closeBrowserTab(tab.id)}><X size={12} /></button>
            </div>
          ))}
          <button className="browser-new-tab" type="button" aria-label="New browser tab" title="New tab" onClick={() => void model.openBrowserTab()}><Plus size={14} /></button>
        </div>
      ) : null}
      {runtimeUrl !== null && model.browserSession === null ? (
        <button className="browser-runtime-entry" type="button" onClick={() => void model.startBrowser(runtimeUrl)}>
          <ExternalLink size={15} /> Open the current Runtime in controlled Browser
        </button>
      ) : null}
      <div className="browser-surface">
        {model.browserSession?.status === "interrupted" || model.browserSession?.status === "suspended" ? (
          <div className="browser-empty browser-empty-danger">
            <ShieldAlert />
            <strong>Browser session interrupted</strong>
            <span>{model.browserSession.public_error ?? "Resume explicitly to restore this scoped session."}</span>
          </div>
        ) : model.browserSnapshot?.screenshot_data_url ? (
          <img
            src={model.browserSnapshot.screenshot_data_url}
            alt="Current controlled browser page"
            draggable={false}
            onClick={clickFrame}
          />
        ) : model.browserLoading ? (
          <div className="browser-empty"><LoaderCircle className="spin" /><strong>Connecting to Browser Worker</strong></div>
        ) : model.browserHealth?.available !== true || model.browserError !== null ? (
          <div className="browser-empty browser-empty-danger"><ShieldAlert /><strong>Browser unavailable</strong><span>{model.browserHealth?.diagnostic ?? model.browserError ?? "Start Fairy Core with the Browser Worker"}</span></div>
        ) : (
          <div className="browser-empty"><ExternalLink /><strong>No browser page</strong><span>Enter an address or open the current Runtime.</span></div>
        )}
      </div>
      <footer className="browser-status">
        <span>{activeTab?.title ?? "Fairy Browser"}</span>
        <span>{model.browserSession?.status ?? "stopped"}</span>
      </footer>
    </section>
  );
}

function normalizeAddress(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (trimmed === "about:blank") return trimmed;
  try {
    const url = new URL(trimmed.includes("://") ? trimmed : `https://${trimmed}`);
    return ["http:", "https:"].includes(url.protocol) ? url.toString() : null;
  } catch {
    return null;
  }
}
