import { randomUUID } from "node:crypto";
import { lookup } from "node:dns/promises";
import { mkdir } from "node:fs/promises";
import { isIP } from "node:net";
import path from "node:path";
import { createInterface } from "node:readline";

import { chromium } from "playwright";

const sessions = new Map();
const actionResults = new Map();
const dnsCache = new Map();

function isBlockedIp(address) {
  const normalized = address.replace(/^::ffff:/, "");
  if (normalized === "::1") return false;
  if (isIP(normalized) === 4) {
    const parts = normalized.split(".").map(Number);
    if (parts[0] === 127) return false;
    return parts[0] === 0 || parts[0] === 10
      || (parts[0] === 169 && parts[1] === 254)
      || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31)
      || (parts[0] === 192 && parts[1] === 168)
      || parts[0] >= 224;
  }
  const lower = normalized.toLowerCase();
  return lower === "::" || lower.startsWith("fe8") || lower.startsWith("fe9")
    || lower.startsWith("fea") || lower.startsWith("feb")
    || lower.startsWith("fc") || lower.startsWith("fd") || lower.startsWith("ff");
}

async function validateNetworkUrl(rawUrl) {
  if (rawUrl === "about:blank" || rawUrl.startsWith("data:") || rawUrl.startsWith("blob:")) return;
  const url = new URL(rawUrl);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
    throw Object.assign(new Error("Browser destination is blocked"), { code: "PATH_OUT_OF_SCOPE" });
  }
  const cached = dnsCache.get(url.hostname);
  const addresses = cached?.expiresAt > Date.now()
    ? cached.addresses
    : (await lookup(url.hostname, { all: true, verbatim: true })).map((entry) => entry.address);
  dnsCache.set(url.hostname, { addresses, expiresAt: Date.now() + 30_000 });
  if (addresses.length === 0 || addresses.some(isBlockedIp)) {
    throw Object.assign(new Error("Browser destination is blocked"), { code: "PATH_OUT_OF_SCOPE" });
  }
}

async function installNetworkBoundary(context) {
  await context.route("**/*", async (route) => {
    try {
      await validateNetworkUrl(route.request().url());
      await route.continue();
    } catch {
      await route.abort("blockedbyclient");
    }
  });
}

function reply(id, result) {
  process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id, result })}\n`);
}

function fail(id, error, errorCode = "WORKER_INTERRUPTED") {
  const message = error instanceof Error ? error.message : String(error);
  process.stdout.write(`${JSON.stringify({
    jsonrpc: "2.0",
    id,
    error: { code: -32000, message, data: { error_code: errorCode } },
  })}\n`);
}

function requiredSession(sessionId) {
  const session = sessions.get(sessionId);
  if (!session) throw Object.assign(new Error("Browser session was not found"), { code: "SCOPE_MISMATCH" });
  return session;
}

function requiredTab(session, tabId) {
  const tab = session.tabs.get(tabId);
  if (!tab) throw Object.assign(new Error("Browser tab was not found"), { code: "SCOPE_MISMATCH" });
  return tab;
}

async function tabModel(session, tab) {
  return {
    id: tab.id,
    session_id: session.id,
    title: (await tab.page.title().catch(() => "")) || "New tab",
    url: tab.page.url(),
    active: session.activeTabId === tab.id,
    loading: tab.loading,
    revision: tab.revision,
  };
}

async function sessionResult(session, status = "active") {
  return {
    status,
    active_tab_id: session.activeTabId,
    tabs: await Promise.all([...session.tabs.values()].map((tab) => tabModel(session, tab))),
  };
}

function attachPage(session, page, id = randomUUID()) {
  const tab = { id, page, revision: 0, loading: false };
  session.tabs.set(id, tab);
  page.on("request", (request) => {
    if (request.isNavigationRequest() && request.frame() === page.mainFrame()) tab.loading = true;
  });
  page.on("domcontentloaded", () => {
    tab.loading = false;
    tab.revision += 1;
  });
  page.on("close", () => session.tabs.delete(id));
  return tab;
}

async function startSession(params) {
  if (sessions.has(params.session_id)) return sessionResult(sessions.get(params.session_id));
  const persistent = params.profile_kind === "persistent";
  const profileDir = persistent
    ? path.join(params.profile_root, "fairy-default")
    : path.join(params.profile_root, "ephemeral", params.session_id);
  await mkdir(profileDir, { recursive: true });
  const context = await chromium.launchPersistentContext(profileDir, {
    channel: "msedge",
    headless: true,
    viewport: { width: 1365, height: 768 },
    locale: "zh-CN",
    acceptDownloads: false,
    ignoreHTTPSErrors: false,
  });
  await installNetworkBoundary(context);
  const session = { id: params.session_id, context, tabs: new Map(), activeTabId: null };
  sessions.set(session.id, session);
  let page = context.pages()[0];
  if (!page) page = await context.newPage();
  const tab = attachPage(session, page);
  session.activeTabId = tab.id;
  if (params.initial_url && params.initial_url !== "about:blank") {
    await validateNetworkUrl(params.initial_url);
    await page.goto(params.initial_url, { waitUntil: "domcontentloaded", timeout: 30_000 });
  }
  return sessionResult(session);
}

async function stopSession(params) {
  const session = requiredSession(params.session_id);
  await session.context.close();
  sessions.delete(session.id);
  return { status: "stopped", active_tab_id: null, tabs: [] };
}

async function openTab(params) {
  const session = requiredSession(params.session_id);
  const page = await session.context.newPage();
  const tab = attachPage(session, page);
  session.activeTabId = tab.id;
  if (params.url !== "about:blank") {
    await validateNetworkUrl(params.url);
    await page.goto(params.url, { waitUntil: "domcontentloaded", timeout: 30_000 });
  }
  return sessionResult(session);
}

async function selectTab(params) {
  const session = requiredSession(params.session_id);
  requiredTab(session, params.tab_id);
  session.activeTabId = params.tab_id;
  return sessionResult(session);
}

async function closeTab(params) {
  const session = requiredSession(params.session_id);
  const tab = requiredTab(session, params.tab_id);
  await tab.page.close();
  if (session.activeTabId === tab.id) session.activeTabId = session.tabs.keys().next().value ?? null;
  if (session.tabs.size === 0) {
    const page = await session.context.newPage();
    const replacement = attachPage(session, page);
    session.activeTabId = replacement.id;
  }
  return sessionResult(session);
}

async function executeAction(params) {
  const cached = actionResults.get(params.idempotency_key);
  if (cached) return { ...cached, replayed: true };
  const session = requiredSession(params.session_id);
  const tab = requiredTab(session, params.tab_id);
  if (params.expected_page_revision != null && params.expected_page_revision !== tab.revision) {
    throw Object.assign(new Error("Page changed before the action could be applied"), { code: "VERSION_CONFLICT" });
  }
  const page = tab.page;
  const locator = params.selector ? page.locator(params.selector).first() : null;
  switch (params.kind) {
    case "navigate":
      await validateNetworkUrl(params.value);
      await page.goto(params.value, { waitUntil: "domcontentloaded", timeout: 30_000 });
      break;
    case "click":
      if (locator) await locator.click({ timeout: 10_000 });
      else if (Number.isFinite(params.x) && Number.isFinite(params.y)) await page.mouse.click(params.x, params.y);
      else throw Object.assign(new Error("Browser click requires a selector or coordinates"), { code: "SCOPE_MISMATCH" });
      break;
    case "fill":
      if (!locator) throw Object.assign(new Error("Browser fill requires a selector"), { code: "SCOPE_MISMATCH" });
      await locator.fill(params.value ?? "", { timeout: 10_000 });
      break;
    case "press":
      if (!locator) throw Object.assign(new Error("Browser press requires a selector"), { code: "SCOPE_MISMATCH" });
      await locator.press(params.value ?? "Enter", { timeout: 10_000 });
      break;
    case "select":
      if (!locator) throw Object.assign(new Error("Browser select requires a selector"), { code: "SCOPE_MISMATCH" });
      await locator.selectOption(params.value ?? "", { timeout: 10_000 });
      break;
    case "scroll": await page.mouse.wheel(params.delta_x ?? 0, params.delta_y ?? 0); break;
    case "wait": await page.waitForTimeout(Math.min(10_000, Math.max(0, Number(params.value ?? 250)))); break;
    case "reload": await page.reload({ waitUntil: "domcontentloaded", timeout: 30_000 }); break;
    case "go_back": await page.goBack({ waitUntil: "domcontentloaded", timeout: 30_000 }); break;
    case "go_forward": await page.goForward({ waitUntil: "domcontentloaded", timeout: 30_000 }); break;
    default: throw Object.assign(new Error("Browser action is unsupported"), { code: "CAPABILITY_NOT_AVAILABLE" });
  }
  tab.revision += 1;
  const result = {
    session: await sessionResult(session),
    tab: await tabModel(session, tab),
    public_summary: `Browser ${params.kind.replaceAll("_", " ")} completed`,
    replayed: false,
  };
  actionResults.set(params.idempotency_key, result);
  if (actionResults.size > 1000) actionResults.delete(actionResults.keys().next().value);
  return result;
}

async function snapshot(params) {
  const session = requiredSession(params.session_id);
  const tab = requiredTab(session, params.tab_id);
  const page = tab.page;
  const aria = await page.locator("body").ariaSnapshot({ timeout: 10_000 }).catch(() => "");
  const screenshot = params.include_screenshot
    ? await page.screenshot({ type: "jpeg", quality: 78, animations: "disabled" })
    : null;
  return {
    session_id: session.id,
    tab_id: tab.id,
    page_revision: tab.revision,
    url: page.url(),
    title: (await page.title().catch(() => "")) || "New tab",
    aria_snapshot: aria.slice(0, 200_000),
    viewport_width: page.viewportSize()?.width ?? 1365,
    viewport_height: page.viewportSize()?.height ?? 768,
    screenshot_data_url: screenshot ? `data:image/jpeg;base64,${screenshot.toString("base64")}` : null,
    captured_at: new Date().toISOString(),
  };
}

async function dispatch(method, params) {
  switch (method) {
    case "browser.health": return { available: true, browser_name: "Microsoft Edge", browser_version: null, error_code: null, diagnostic: chromium.executablePath() };
    case "browser.sessions.start": return startSession(params);
    case "browser.sessions.stop": return stopSession(params);
    case "browser.tabs.open": return openTab(params);
    case "browser.tabs.select": return selectTab(params);
    case "browser.tabs.close": return closeTab(params);
    case "browser.actions.execute": return executeAction(params);
    case "browser.snapshots.get": return snapshot(params);
    default: throw Object.assign(new Error(`Unknown browser worker method: ${method}`), { code: "CAPABILITY_NOT_AVAILABLE" });
  }
}

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  if (!line.trim()) continue;
  let request;
  try {
    request = JSON.parse(line);
    reply(request.id, await dispatch(request.method, request.params ?? {}));
  } catch (error) {
    fail(request?.id ?? null, error, error?.code ?? "WORKER_INTERRUPTED");
  }
}

await Promise.all([...sessions.values()].map((session) => session.context.close().catch(() => undefined)));
