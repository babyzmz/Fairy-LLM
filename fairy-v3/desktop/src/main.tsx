import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { isTauri } from "@tauri-apps/api/core";
import { AppMotion } from "./motion/AppMotion";
import type { MainViewRequest } from "./app/mainViewBridge";

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

  if (surface === "companion") {
    const [
      { invoke },
      { RealtimeCompanionWindowApp },
      { CoreClient },
      { TauriCoreTransport },
    ] = await Promise.all([
      import("@tauri-apps/api/core"),
      import("./realtime/RealtimeCompanionWindowApp"),
      import("./core/client"),
      import("./core/tauriTransport"),
    ]);
    const client = new CoreClient(
      new TauriCoreTransport(invoke, { rpcCommand: "companion_rpc" }),
    );
    renderSurface(
      <RealtimeCompanionWindowApp client={client.realtime} invoke={invoke} />,
    );
    return;
  }

  if (!isTauri()) {
    const { DesktopHostRequired } = await import("./host/DesktopHostRequired");
    renderSurface(<DesktopHostRequired />);
    return;
  }

  const [
    { invoke },
    { listen },
    { App },
    { TauriMainViewHost },
    { CoreClient },
    { TauriCoreTransport },
    { SettingsClient },
  ] = await Promise.all([
    import("@tauri-apps/api/core"),
    import("@tauri-apps/api/event"),
    import("./app/App"),
    import("./app/mainViewBridge"),
    import("./core/client"),
    import("./core/tauriTransport"),
    import("./settings/client"),
  ]);
  const client = new CoreClient(new TauriCoreTransport(invoke));
  const mainViewHost = new TauriMainViewHost(
    invoke,
    (listener) =>
      listen<MainViewRequest>("main-view-requested", (event) => {
        listener(event.payload);
      }),
  );
  renderSurface(
    <App
      client={client}
      mainViewHost={mainViewHost}
      settingsClient={new SettingsClient(invoke)}
    />,
  );
}

void mountSurface();
