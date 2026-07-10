import { invoke } from "@tauri-apps/api/core";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { CoreClient } from "./core/client";
import { TauriCoreTransport } from "./core/tauriTransport";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("Fairy desktop root element is missing");
}

const client = new CoreClient(new TauriCoreTransport(invoke));

createRoot(root).render(
  <StrictMode>
    <App client={client} />
  </StrictMode>,
);
