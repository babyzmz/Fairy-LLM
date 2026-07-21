import {
  ArrowLeft,
  ArrowRight,
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

  useEffect(() => {
    if (activeTab?.url && activeTab.url !== "about:blank") setAddress(activeTab.url);
  }, [activeTab?.url]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const target = normalizeAddress(address);
    if (target !== null) void model.navigateBrowser(target);
  };
  const execute = (kind: "go_back" | "go_forward" | "reload") => {
    void model.executeBrowserAction({ kind });
  };
  const clickFrame = (event: MouseEvent<HTMLImageElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    if (bounds.width < 1 || bounds.height < 1) return;
    void model.executeBrowserAction({
      kind: "click",
      x: ((event.clientX - bounds.left) / bounds.width) * (model.browserSnapshot?.viewport_width ?? 1365),
      y: ((event.clientY - bounds.top) / bounds.height) * (model.browserSnapshot?.viewport_height ?? 768),
      expected_page_revision: model.browserSnapshot?.page_revision ?? activeTab?.revision ?? 0,
    });
  };

  return (
    <section className="browser-panel" aria-label="Controlled browser">
      <header className="browser-toolbar">
        <div className="browser-nav-actions">
          <button className="icon-button" type="button" aria-label="Back" title="Back" disabled={activeTab === null} onClick={() => execute("go_back")}><ArrowLeft size={15} /></button>
          <button className="icon-button" type="button" aria-label="Forward" title="Forward" disabled={activeTab === null} onClick={() => execute("go_forward")}><ArrowRight size={15} /></button>
          <button className="icon-button" type="button" aria-label="Reload" title="Reload" disabled={activeTab === null} onClick={() => execute("reload")}><RefreshCw size={15} /></button>
        </div>
        <form onSubmit={submit}>
          <ShieldAlert size={14} aria-hidden="true" />
          <input aria-label="Browser address" value={address} onChange={(event) => setAddress(event.target.value)} spellCheck={false} />
        </form>
        {model.browserSession?.status === "active" ? (
          <button className="icon-button" type="button" aria-label="Stop browser" title="Stop browser" onClick={() => void model.stopBrowser()}><Square size={15} /></button>
        ) : (
          <button className="icon-button" type="button" aria-label="Start browser" title="Start browser" disabled={model.browserHealth?.available !== true} onClick={() => void model.startBrowser(normalizeAddress(address) ?? undefined)}><Play size={15} /></button>
        )}
      </header>
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
        {model.browserSnapshot?.screenshot_data_url ? (
          <img
            src={model.browserSnapshot.screenshot_data_url}
            alt="Current controlled browser page"
            draggable={false}
            onClick={clickFrame}
          />
        ) : model.browserLoading ? (
          <div className="browser-empty"><LoaderCircle className="spin" /><strong>Connecting to Browser Worker</strong></div>
        ) : model.browserHealth?.available !== true ? (
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
