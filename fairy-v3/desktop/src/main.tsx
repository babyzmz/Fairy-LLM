import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { isTauri } from "@tauri-apps/api/core";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("Fairy desktop root element is missing");
}
const rootElement = root;

async function mountSurface() {
  const surface = new URLSearchParams(window.location.search).get("surface");
  document.documentElement.dataset.surface = surface ?? "workspace";

  if (surface === "presence") {
    const { PresenceApp } = await import("./presence/PresenceApp");
    createRoot(rootElement).render(
      <StrictMode>
        <PresenceApp />
      </StrictMode>,
    );
    return;
  }

  if (surface === "guide") {
    const { GuideApp } = await import("./guide/GuideApp");
    createRoot(rootElement).render(
      <StrictMode>
        <GuideApp />
      </StrictMode>,
    );
    return;
  }

  if (surface === "settings") {
    const [{ invoke }, { SettingsApp }, { SettingsClient }] = await Promise.all([
      import("@tauri-apps/api/core"),
      import("./settings/SettingsApp"),
      import("./settings/client"),
    ]);
    createRoot(rootElement).render(
      <StrictMode>
        <SettingsApp client={new SettingsClient(invoke)} />
      </StrictMode>,
    );
    return;
  }

  if (!isTauri()) {
    const { DesktopHostRequired } = await import("./host/DesktopHostRequired");
    createRoot(rootElement).render(
      <StrictMode>
        <DesktopHostRequired />
      </StrictMode>,
    );
    return;
  }

  const [{ invoke }, { App }, { CoreClient }, { TauriCoreTransport }] = await Promise.all([
    import("@tauri-apps/api/core"),
    import("./app/App"),
    import("./core/client"),
    import("./core/tauriTransport"),
  ]);
  const client = new CoreClient(new TauriCoreTransport(invoke));
  createRoot(rootElement).render(
    <StrictMode>
      <App client={client} />
    </StrictMode>,
  );
}

void mountSurface();
