import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { isTauri } from "@tauri-apps/api/core";
import { AppMotion } from "./motion/AppMotion";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("Fairy desktop root element is missing");
}
const rootElement = root;

function renderSurface(surface: ReactNode) {
  createRoot(rootElement).render(<StrictMode><AppMotion>{surface}</AppMotion></StrictMode>);
}

async function mountSurface() {
  const surface = new URLSearchParams(window.location.search).get("surface");
  document.documentElement.dataset.surface = surface ?? "workspace";

  if (surface === "presence") {
    const { PresenceApp } = await import("./presence/PresenceApp");
    renderSurface(<PresenceApp />);
    return;
  }

  if (surface === "pet-render") {
    const { PresenceRenderApp } = await import("./presence/render/PresenceRenderApp");
    renderSurface(<PresenceRenderApp />);
    return;
  }

  if (surface === "pet-input") {
    const { PresenceInputApp } = await import("./presence/input/PresenceInputApp");
    renderSurface(<PresenceInputApp />);
    return;
  }

  if (surface === "presence-render") {
    const { PresenceRenderProbe } = await import("./presence/render/PresenceRenderProbe");
    renderSurface(<PresenceRenderProbe />);
    return;
  }

  if (surface === "settings") {
    const [{ invoke }, { SettingsApp }, { SettingsClient }] = await Promise.all([
      import("@tauri-apps/api/core"),
      import("./settings/SettingsApp"),
      import("./settings/client"),
    ]);
    renderSurface(<SettingsApp client={new SettingsClient(invoke)} />);
    return;
  }

  if (!isTauri()) {
    const { DesktopHostRequired } = await import("./host/DesktopHostRequired");
    renderSurface(<DesktopHostRequired />);
    return;
  }

  const [{ invoke }, { App }, { CoreClient }, { TauriCoreTransport }] = await Promise.all([
    import("@tauri-apps/api/core"),
    import("./app/App"),
    import("./core/client"),
    import("./core/tauriTransport"),
  ]);
  const client = new CoreClient(new TauriCoreTransport(invoke));
  renderSurface(<App client={client} />);
}

void mountSurface();
