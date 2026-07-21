import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:http";
import { rm } from "node:fs/promises";
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

const server = createServer((_request, response) => {
  response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  response.end("<!doctype html><title>Fairy Browser Smoke</title><main><h1>Browser ready</h1><button>Test</button></main>");
});

let sessionId;
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
  assert.match(snapshot.screenshot_data_url, /^data:image\/jpeg;base64,/);
  assert.equal(snapshot.viewport_width, 1365);

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
  worker.stdin.end();
  await Promise.race([once(worker, "exit"), new Promise((resolve) => setTimeout(resolve, 5_000))]);
  if (worker.exitCode === null) worker.kill();
  server.close();
  await rm(profileRoot, { recursive: true, force: true });
}
