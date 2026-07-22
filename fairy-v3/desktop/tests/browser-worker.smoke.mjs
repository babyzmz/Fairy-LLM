import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:http";
import { readdir, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createInterface } from "node:readline";

const profileRoot = path.join(os.tmpdir(), `fairy-browser-smoke-${process.pid}`);
const worker = spawn(process.execPath, [path.resolve("browser-worker.mjs")], {
  cwd: process.cwd(),
  stdio: ["pipe", "pipe", "inherit"],
  windowsHide: true,
});
const lines = createInterface({ input: worker.stdout, crlfDelay: Infinity });
const pending = new Map();
let requestId = 0;

lines.on("line", (line) => {
  const message = JSON.parse(line);
  const waiter = pending.get(message.id);
  if (!waiter) return;
  pending.delete(message.id);
  if (message.error) waiter.reject(Object.assign(new Error(message.error.message), message.error.data));
  else waiter.resolve(message.result);
});

function call(method, params = {}) {
  const id = ++requestId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
  });
}

let protectedRequests = 0;
const server = createServer((request, response) => {
  if (request.url === "/protected") {
    protectedRequests += 1;
    response.writeHead(200, { "content-type": "text/plain" });
    response.end("local secret");
    return;
  }
  if (request.url === "/download") {
    response.writeHead(200, {
      "content-type": "application/octet-stream",
      "content-disposition": "attachment; filename=blocked.txt",
    });
    response.end("must not persist");
    return;
  }
  if (request.url === "/dynamic") {
    response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    response.end(`<!doctype html><title>Dynamic page</title><main>
      <button id="stable">Stable action</button>
      <script>setTimeout(() => document.body.append(" late update"), 250)</script>
    </main>`);
    return;
  }
  response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  response.end(`<!doctype html><title>Fairy Browser Smoke</title><main>
    <h1>Browser ready</h1>
    <a id="popup" href="/popup" target="_blank">Open popup</a>
    <a id="download" href="/download" download>Download</a>
  </main>`);
});

let sessionId;
let persistentSessionId;
let secondPersistentSessionId;
try {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  assert(address && typeof address !== "string");
  const url = `http://127.0.0.1:${address.port}/`;

  const health = await call("browser.health");
  assert.equal(health.available, true);

  sessionId = crypto.randomUUID();
  const session = await call("browser.sessions.start", {
    session_id: sessionId,
    profile_kind: "ephemeral",
    profile_root: profileRoot,
    initial_url: url,
  });
  assert.equal(session.status, "active");
  const tabId = session.active_tab_id;
  const snapshot = await call("browser.snapshots.get", {
    session_id: sessionId,
    tab_id: tabId,
    include_screenshot: true,
  });
  assert.match(snapshot.aria_snapshot, /Browser ready/);
  assert.match(snapshot.screenshot_data_url, /^data:image\/png;base64,/);
  assert.equal(snapshot.viewport_width, 1365);

  persistentSessionId = crypto.randomUUID();
  secondPersistentSessionId = crypto.randomUUID();
  const firstPersistent = await call("browser.sessions.start", {
    session_id: persistentSessionId,
    profile_kind: "persistent",
    profile_root: profileRoot,
    initial_url: url,
  });
  const secondPersistent = await call("browser.sessions.start", {
    session_id: secondPersistentSessionId,
    profile_kind: "persistent",
    profile_root: profileRoot,
    initial_url: url,
  });
  assert.equal(firstPersistent.status, "active");
  assert.equal(secondPersistent.status, "active");
  assert.equal(firstPersistent.tabs.length, 1);
  assert.equal(secondPersistent.tabs.length, 1);

  const popupResult = await call("browser.actions.execute", {
    session_id: sessionId,
    tab_id: tabId,
    kind: "click",
    selector: "#popup",
    expected_page_revision: snapshot.page_revision,
    idempotency_key: "open-popup",
  });
  assert.equal(popupResult.session.tabs.length, 2);

  await call("browser.actions.execute", {
    session_id: sessionId,
    tab_id: tabId,
    kind: "click",
    selector: "#download",
    expected_page_revision: popupResult.tab.revision,
    idempotency_key: "blocked-download",
  });
  const profileFiles = await readdir(profileRoot, { recursive: true });
  assert.equal(profileFiles.some((entry) => String(entry).endsWith("blocked.txt")), false);

  const dynamicSession = await call("browser.tabs.open", {
    session_id: sessionId,
    url: `${url}dynamic`,
  });
  const dynamicTabId = dynamicSession.active_tab_id;
  const dynamicSnapshot = await call("browser.snapshots.get", {
    session_id: sessionId,
    tab_id: dynamicTabId,
    include_screenshot: false,
  });
  await new Promise((resolve) => setTimeout(resolve, 350));
  await assert.rejects(
    call("browser.actions.execute", {
      session_id: sessionId,
      tab_id: dynamicTabId,
      kind: "click",
      selector: "#stable",
      expected_page_revision: dynamicSnapshot.page_revision,
      idempotency_key: "stale-dynamic-page",
    }),
    /Page changed/,
  );

  const localTarget = `${url}protected`;
  const crossScopeDocument = `<!doctype html><main id="result">waiting</main><script>
    fetch(${JSON.stringify(localTarget)})
      .then(() => document.querySelector("#result").textContent = "allowed")
      .catch(() => document.querySelector("#result").textContent = "blocked")
  </script>`;
  const crossScopeSession = await call("browser.tabs.open", {
    session_id: sessionId,
    url: `data:text/html,${encodeURIComponent(crossScopeDocument)}`,
  });
  await new Promise((resolve) => setTimeout(resolve, 250));
  const crossScopeSnapshot = await call("browser.snapshots.get", {
    session_id: sessionId,
    tab_id: crossScopeSession.active_tab_id,
    include_screenshot: false,
  });
  assert.match(crossScopeSnapshot.aria_snapshot, /blocked/);
  assert.equal(protectedRequests, 0);

  const firstReload = await call("browser.actions.execute", {
    session_id: persistentSessionId,
    tab_id: firstPersistent.active_tab_id,
    kind: "reload",
    expected_page_revision: firstPersistent.tabs[0].revision,
    idempotency_key: "same-key-different-session",
  });
  const secondReload = await call("browser.actions.execute", {
    session_id: secondPersistentSessionId,
    tab_id: secondPersistent.active_tab_id,
    kind: "reload",
    expected_page_revision: secondPersistent.tabs[0].revision,
    idempotency_key: "same-key-different-session",
  });
  assert.equal(firstReload.replayed, false);
  assert.equal(secondReload.replayed, false);

  await assert.rejects(
    call("browser.actions.execute", {
      session_id: sessionId,
      tab_id: tabId,
      kind: "navigate",
      value: "http://10.0.0.1/",
      idempotency_key: "blocked-private-network",
    }),
    /blocked/,
  );
  process.stdout.write("Fairy Browser Worker smoke passed\n");
} finally {
  if (sessionId) await call("browser.sessions.stop", { session_id: sessionId }).catch(() => undefined);
  if (persistentSessionId) await call("browser.sessions.stop", { session_id: persistentSessionId }).catch(() => undefined);
  if (secondPersistentSessionId) await call("browser.sessions.stop", { session_id: secondPersistentSessionId }).catch(() => undefined);
  worker.stdin.end();
  await Promise.race([once(worker, "exit"), new Promise((resolve) => setTimeout(resolve, 5_000))]);
  if (worker.exitCode === null) worker.kill();
  server.close();
  await rm(profileRoot, { recursive: true, force: true });
}
