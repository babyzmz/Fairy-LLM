import { MonitorUp, ShieldAlert } from "lucide-react";

import "./desktopHostRequired.css";

export function DesktopHostRequired() {
  return (
    <main className="host-required" aria-labelledby="host-required-title">
      <section className="host-required-panel">
        <div className="host-required-mark" aria-hidden="true">
          <ShieldAlert size={22} />
        </div>
        <p className="host-required-eyebrow">DESKTOP HOST REQUIRED</p>
        <h1 id="host-required-title">Open Fairy as a Windows app</h1>
        <p className="host-required-detail">
          This browser page cannot access Fairy Core, managed projects, credentials, or local
          execution.
        </p>
        <div className="host-required-status" role="status">
          <MonitorUp size={16} />
          <span>Web renderer ready</span>
          <strong>Core not attached</strong>
        </div>
      </section>
    </main>
  );
}
