import {
  Bell,
  Bot,
  Boxes,
  ChevronDown,
  Cloud,
  Code2,
  History,
  MessageSquareText,
  Mic,
  PanelRightOpen,
  Play,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  Square,
} from "lucide-react";
import { useState } from "react";

import "./workspace.css";

export interface WorkspaceContext {
  project: string;
  conversation: string;
  version: string;
  executionTarget: string;
  permission: string;
  syncStatus: string;
}

interface WorkspaceShellProps {
  context: WorkspaceContext;
}

const sampleTimeline = [
  { time: "20:14:08", title: "Scope resolved", detail: "12 files indexed in current draft", state: "done" },
  { time: "20:14:11", title: "Changeset prepared", detail: "3 files · medium risk", state: "done" },
  { time: "20:14:14", title: "Review running", detail: "typecheck · build · browser", state: "active" },
] as const;

export function WorkspaceShell({ context }: WorkspaceShellProps) {
  const [developerOpen, setDeveloperOpen] = useState(false);
  const [message, setMessage] = useState("");

  return (
    <div className="workspace-shell">
      <nav className="primary-rail" aria-label="Primary navigation">
        <button className="brand-button" aria-label="Fairy home" title="Fairy home">
          <Sparkles size={21} />
        </button>
        <div className="rail-actions">
          <button className="rail-button active" aria-label="Project workspace" title="Project workspace">
            <Boxes size={19} />
          </button>
          <button className="rail-button" aria-label="Conversations" title="Conversations">
            <MessageSquareText size={19} />
          </button>
          <button className="rail-button" aria-label="Task history" title="Task history">
            <History size={19} />
          </button>
        </div>
        <div className="rail-actions rail-bottom">
          <button className="rail-button" aria-label="Notifications" title="Notifications">
            <Bell size={19} />
          </button>
          <button className="rail-button" aria-label="Settings" title="Settings">
            <Settings size={19} />
          </button>
        </div>
      </nav>

      <div className="workspace-body">
        <header className="context-bar" role="banner">
          <div className="context-identity">
            <span className="eyebrow">ACTIVE PROJECT</span>
            <button className="project-selector">
              <strong>{context.project}</strong>
              <ChevronDown size={15} />
            </button>
            <span className="context-separator">/</span>
            <span>{context.conversation}</span>
          </div>
          <div className="telemetry-strip" aria-label="Workspace telemetry">
            <span className="telemetry-item"><Code2 size={14} /> {context.version}</span>
            <span className="telemetry-item"><Play size={14} /> {context.executionTarget}</span>
            <span className="telemetry-item"><ShieldCheck size={14} /> {context.permission}</span>
            <span className="telemetry-item online"><Cloud size={14} /> {context.syncStatus}</span>
          </div>
        </header>

        <main className="workspace-main">
          <section className="timeline-pane" aria-labelledby="timeline-heading">
            <div className="pane-header">
              <div>
                <span className="eyebrow">TASK 03 · RUNNING</span>
                <h1 id="timeline-heading">Task Timeline</h1>
              </div>
              <div className="task-signal" aria-label="Task is running">
                <span /> LIVE
              </div>
            </div>

            <div className="conversation-thread">
              <div className="message user-message">
                <span className="message-author">YOU</span>
                <p>Add a clearer pricing section and keep the mobile layout compact.</p>
              </div>
              <div className="message fairy-message">
                <span className="message-author"><Bot size={14} /> FAIRY</span>
                <p>I isolated a new draft and prepared the change. Review is running now.</p>
              </div>
            </div>

            <ol className="event-timeline" aria-label="Execution events">
              {sampleTimeline.map((item) => (
                <li key={item.time} className={item.state === "active" ? "event-active" : ""}>
                  <span className="event-node" />
                  <time>{item.time}</time>
                  <div>
                    <strong>{item.title}</strong>
                    <p>{item.detail}</p>
                  </div>
                </li>
              ))}
            </ol>

            <div className="approval-block">
              <div>
                <span className="eyebrow">APPROVAL · MEDIUM RISK</span>
                <strong>Update 3 files and install no new dependencies</strong>
                <p>Homepage layout, pricing styles, and responsive tests.</p>
              </div>
              <div className="approval-actions">
                <button className="secondary-command">Cancel</button>
                <button className="primary-command">Allow changes</button>
              </div>
            </div>
          </section>

          <section className="preview-pane" aria-labelledby="preview-heading">
            <div className="preview-toolbar">
              <div>
                <span className="eyebrow">CURRENT CHAT DRAFT</span>
                <h2 id="preview-heading">Preview</h2>
              </div>
              <div className="preview-actions">
                <button className="icon-button" aria-label="Stop preview" title="Stop preview"><Square size={15} /></button>
                <button
                  className={`icon-button ${developerOpen ? "active" : ""}`}
                  aria-label="Toggle developer details"
                  title="Developer details"
                  onClick={() => setDeveloperOpen((open) => !open)}
                >
                  <PanelRightOpen size={16} />
                </button>
              </div>
            </div>
            <div className="hud-frame">
              <div className="hud-corner hud-top-left">PORT 1430</div>
              <div className="hud-corner hud-top-right">60 FPS · LOCAL</div>
              <div className="preview-surface">
                <span className="preview-kicker">NORTHSTAR / PRICING</span>
                <h3>Plans that stay clear<br />as your work grows.</h3>
                <p>Preview content is isolated in this conversation draft.</p>
                <div className="preview-price-row">
                  <div><span>STARTER</span><strong>$12</strong><small>/month</small></div>
                  <div className="featured"><span>STUDIO</span><strong>$29</strong><small>/month</small></div>
                  <div><span>SCALE</span><strong>$79</strong><small>/month</small></div>
                </div>
              </div>
              <div className="scan-line" aria-hidden="true" />
            </div>
            <div className="version-decision-bar">
              <span>Review complete after 2 remaining checks</span>
              <div>
                <button className="secondary-command">Discard</button>
                <button className="primary-command" disabled>Use this version</button>
              </div>
            </div>
            {developerOpen ? (
              <aside className="developer-drawer" aria-label="Developer details">
                <span className="eyebrow">DEVELOPER MODE</span>
                <strong>3 changed files</strong>
                <code>src/pages/Pricing.tsx</code>
                <code>src/styles/pricing.css</code>
                <code>tests/pricing.spec.ts</code>
              </aside>
            ) : null}
          </section>
        </main>

        <form className="composer" onSubmit={(event) => event.preventDefault()}>
          <button className="icon-button" type="button" aria-label="Voice input" title="Voice input"><Mic size={17} /></button>
          <label className="composer-field">
            <span className="sr-only">Message Fairy</span>
            <input
              aria-label="Message Fairy"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="Describe the next change…"
            />
          </label>
          <button className="send-button" type="submit" aria-label="Send message" title="Send message"><Send size={17} /></button>
        </form>
      </div>
    </div>
  );
}
