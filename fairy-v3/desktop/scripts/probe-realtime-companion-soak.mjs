import { rename, writeFile } from "node:fs/promises";

import { chromium } from "playwright";

const options = parseArguments(process.argv.slice(2));
const port = integerOption(options, "port", 1, 65_535);
const durationSeconds = integerOption(options, "duration-seconds", 14_400, 604_800);
const sampleSeconds = integerOption(options, "sample-seconds", 5, 60);
const startupTimeoutSeconds = integerOption(
  options,
  "startup-timeout-seconds",
  60,
  7_200,
);
const stopTimeoutSeconds = integerOption(options, "stop-timeout-seconds", 60, 3_600);
const outputPath = requiredOption(options, "output");

const REPORT_KEYS = Object.freeze([
  "schema_version",
  "status",
  "started_at",
  "completed_at",
  "observed_duration_seconds",
  "sample_interval_seconds",
  "sample_count",
  "backend",
  "segment_count",
  "context_rotation_count",
  "privacy_pause_count",
  "resource_transition_counts",
  "sidecar_restart_observed",
  "sidecar_quarantine_observed",
  "maximum_usage",
  "terminal_session_status",
  "digest_count",
  "proposal_counts",
]);

const browser = await connectWithRetry(`http://127.0.0.1:${port}`, 60_000);
try {
  const page = await findMainPage(browser, 60_000);
  process.stderr.write(
    "Waiting for a verified Local MiniCPM runtime and a manually started Realtime Session...\n",
  );
  await waitForLocalReadiness(page, startupTimeoutSeconds * 1_000);
  const initial = await waitForLocalSession(page, startupTimeoutSeconds * 1_000);
  const sessionId = initial.session_id;
  if (typeof sessionId !== "string" || sessionId.length === 0) {
    throw new Error("The running Realtime Session has no bounded identity");
  }

  const startedAt = Date.now();
  const segments = new Set([initial.segment_id]);
  let previousSegment = initial.segment_id;
  let previousEpoch = Number(initial.context_epoch ?? 1);
  let previousPresence = initial.presence_projection?.state ?? null;
  let contextRotationCount = 0;
  let privacyPauseCount = previousPresence === "privacy_paused" ? 1 : 0;
  let sampleCount = 0;
  let sidecarRestartObserved = Boolean(initial.sidecar?.restart_used);
  let sidecarQuarantineObserved = Boolean(initial.sidecar?.quarantined);
  const resourceTransitionCounts = {
    normal: 0,
    pressure: 0,
    high: 0,
    critical: 0,
    device_removed: 0,
  };
  let previousResource = null;
  const maximumUsage = {
    audio_input_ms: 0,
    audio_output_ms: 0,
    video_frame_count: 0,
    interruption_count: 0,
    tool_call_count: 0,
  };

  while ((Date.now() - startedAt) / 1_000 < durationSeconds) {
    const status = await invoke(page, "realtime_worker_status");
    assertSameLocalSession(status, sessionId);
    sampleCount += 1;
    if (typeof status.segment_id === "string") segments.add(status.segment_id);
    const epoch = Number(status.context_epoch ?? 1);
    if (status.segment_id === previousSegment && epoch !== previousEpoch) {
      contextRotationCount += Math.max(1, epoch - previousEpoch);
    }
    previousSegment = status.segment_id;
    previousEpoch = epoch;
    const presence = status.presence_projection?.state ?? null;
    if (presence === "privacy_paused" && previousPresence !== "privacy_paused") {
      privacyPauseCount += 1;
    }
    previousPresence = presence;
    const resource = status.resource?.policy?.level ?? "normal";
    if (!(resource in resourceTransitionCounts)) {
      throw new Error(`Unexpected Realtime resource level: ${String(resource)}`);
    }
    if (resource !== previousResource) {
      resourceTransitionCounts[resource] += 1;
      previousResource = resource;
    }
    sidecarRestartObserved ||= Boolean(status.sidecar?.restart_used);
    sidecarQuarantineObserved ||= Boolean(status.sidecar?.quarantined);
    for (const key of Object.keys(maximumUsage)) {
      maximumUsage[key] = Math.max(maximumUsage[key], Number(status[key] ?? 0));
    }
    await delay(sampleSeconds * 1_000);
  }

  const observedDurationSeconds = Math.floor((Date.now() - startedAt) / 1_000);
  if (observedDurationSeconds < durationSeconds) {
    throw new Error("Realtime soak ended before the required wall-clock duration");
  }
  process.stderr.write(
    "Four-hour gate duration reached. Stop the Session from the Companion window to finalize its digest.\n",
  );
  await waitForWorkerStop(page, sessionId, stopTimeoutSeconds * 1_000);
  const terminal = await waitForTerminalSession(page, sessionId, stopTimeoutSeconds * 1_000);
  if (terminal.status !== "completed") {
    throw new Error(`Realtime soak Session ended as ${String(terminal.status)}`);
  }
  const digestPage = await waitForDigest(page, sessionId, stopTimeoutSeconds * 1_000);
  const digest = digestPage.items[0];
  const proposalPage = await coreCall(page, "realtime.memory-proposals.list", {
    session_id: sessionId,
    digest_id: digest.id,
    pending_only: false,
    limit: 100,
  });
  const proposalCounts = { promoted: 0, pending: 0, rejected: 0 };
  for (const proposal of proposalPage.items ?? []) {
    if (!(proposal.status in proposalCounts)) {
      throw new Error(`Unexpected Realtime proposal status: ${String(proposal.status)}`);
    }
    proposalCounts[proposal.status] += 1;
  }

  const report = {
    schema_version: 1,
    status: "passed",
    started_at: new Date(startedAt).toISOString(),
    completed_at: new Date().toISOString(),
    observed_duration_seconds: observedDurationSeconds,
    sample_interval_seconds: sampleSeconds,
    sample_count: sampleCount,
    backend: "local_mini_cpm_o45",
    segment_count: segments.size,
    context_rotation_count: contextRotationCount,
    privacy_pause_count: privacyPauseCount,
    resource_transition_counts: resourceTransitionCounts,
    sidecar_restart_observed: sidecarRestartObserved,
    sidecar_quarantine_observed: sidecarQuarantineObserved,
    maximum_usage: maximumUsage,
    terminal_session_status: terminal.status,
    digest_count: digestPage.items.length,
    proposal_counts: proposalCounts,
  };
  assertSanitizedReport(report);
  await writeAtomically(outputPath, `${JSON.stringify(report, null, 2)}\n`);
} finally {
  await browser.close();
}

async function waitForLocalReadiness(page, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastReason = "LOCAL_READINESS_UNAVAILABLE";
  while (Date.now() < deadline) {
    const preferences = await invoke(page, "desktop_preferences_get");
    if (preferences.realtime_beta_enabled !== true) {
      lastReason = "REALTIME_BETA_DISABLED";
    } else if (preferences.realtime_memory_enabled !== true) {
      lastReason = "REALTIME_MEMORY_DISABLED";
    } else {
      const model = await invoke(page, "omni_model_status");
      const readiness = await invoke(page, "realtime_local_readiness_get", {
        input: { profile: "focus", refresh_hardware: true },
      });
      lastReason = readiness.capability?.reason ?? readiness.runtime_error_code ?? model.phase;
      if (
        model.phase === "ready"
        && readiness.runtime === "passed"
        && readiness.capability?.local_beta_eligible === true
      ) {
        return;
      }
    }
    await delay(2_000);
  }
  throw new Error(`Local Realtime prerequisites did not become ready: ${lastReason}`);
}

async function waitForLocalSession(page, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const status = await invoke(page, "realtime_worker_status");
    if (status.running) {
      if (status.backend !== "local_mini_cpm_o45") {
        throw new Error("The soak gate refuses Cloud or CPU fallback");
      }
      return status;
    }
    await delay(1_000);
  }
  throw new Error("No Local Realtime Session started before the timeout");
}

async function waitForWorkerStop(page, sessionId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const status = await invoke(page, "realtime_worker_status");
    if (!status.running) return;
    if (status.session_id !== sessionId) {
      throw new Error("Realtime Session identity changed while awaiting stop");
    }
    await delay(1_000);
  }
  throw new Error("Realtime Session was not stopped before the finalization timeout");
}

async function waitForTerminalSession(page, sessionId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const session = await coreCall(page, "realtime.sessions.get", {
      session_id: sessionId,
    });
    if (["completed", "failed", "cancelled", "interrupted"].includes(session.status)) {
      return session;
    }
    await delay(1_000);
  }
  throw new Error("Core Session did not reach a terminal state");
}

async function waitForDigest(page, sessionId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const digests = await coreCall(page, "realtime.digests.list", {
      session_id: sessionId,
      limit: 5,
    });
    if (Array.isArray(digests.items) && digests.items.length > 0) return digests;
    await delay(1_000);
  }
  throw new Error("Completed Realtime Session did not produce a digest");
}

function assertSameLocalSession(status, sessionId) {
  if (!status.running) throw new Error("Realtime Worker stopped before the soak completed");
  if (status.session_id !== sessionId) {
    throw new Error("Realtime Session identity changed during the soak");
  }
  if (status.backend !== "local_mini_cpm_o45") {
    throw new Error("Realtime Backend changed away from the local GPU runtime");
  }
}

async function coreCall(page, method, params) {
  const response = await invoke(page, "core_rpc", {
    request: {
      jsonrpc: "2.0",
      id: 1,
      method,
      params,
    },
  });
  if (response?.error) {
    const code = response.error?.data?.error_code ?? response.error?.code ?? "CORE_ERROR";
    throw new Error(`Core call failed: ${String(code)}`);
  }
  if (!("result" in response)) throw new Error("Core call returned no result");
  return response.result;
}

async function invoke(page, command, args = undefined) {
  return page.evaluate(
    async ({ nextCommand, nextArgs }) => {
      const tauriInvoke = window.__TAURI_INTERNALS__?.invoke;
      if (typeof tauriInvoke !== "function") throw new Error("Tauri invoke is unavailable");
      return tauriInvoke(nextCommand, nextArgs);
    },
    { nextCommand: command, nextArgs: args },
  );
}

async function findMainPage(browser, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (const context of browser.contexts()) {
      for (const page of context.pages()) {
        const usable = await page.evaluate(() => ({
          invoke: typeof window.__TAURI_INTERNALS__?.invoke === "function",
          surface: document.documentElement.dataset.surface ?? "main",
        })).catch(() => ({ invoke: false, surface: "" }));
        if (usable.invoke && usable.surface === "workspace") return page;
      }
    }
    await delay(250);
  }
  throw new Error("Timed out waiting for the Fairy main WebView");
}

async function connectWithRetry(endpoint, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      return await chromium.connectOverCDP(endpoint);
    } catch (error) {
      lastError = error;
      await delay(250);
    }
  }
  throw lastError ?? new Error("Timed out connecting to WebView2");
}

function assertSanitizedReport(report) {
  const keys = Object.keys(report);
  if (keys.length !== REPORT_KEYS.length || keys.some((key) => !REPORT_KEYS.includes(key))) {
    throw new Error("Realtime soak report contains an unapproved field");
  }
  const serialized = JSON.stringify(report);
  for (const forbidden of [
    "session_id",
    "segment_id",
    "conversation_id",
    "credential",
    "reasoning",
    "application_path",
  ]) {
    if (serialized.includes(forbidden)) {
      throw new Error(`Realtime soak report contains forbidden field: ${forbidden}`);
    }
  }
}

async function writeAtomically(path, contents) {
  const temporary = `${path}.tmp-${process.pid}`;
  await writeFile(temporary, contents, { encoding: "utf8", flag: "wx" });
  await rename(temporary, path);
}

function parseArguments(values) {
  const parsed = {};
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index];
    const value = values[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new Error("Arguments must use --name value pairs");
    }
    parsed[key.slice(2)] = value;
  }
  return parsed;
}

function requiredOption(values, key) {
  const value = values[key];
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`--${key} is required`);
  }
  return value;
}

function integerOption(values, key, minimum, maximum) {
  const value = Number(requiredOption(values, key));
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`--${key} must be an integer between ${minimum} and ${maximum}`);
  }
  return value;
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
