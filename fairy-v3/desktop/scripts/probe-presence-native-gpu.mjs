import fs from "node:fs";

import { chromium } from "playwright";
import { PNG } from "pngjs";

const options = parseArguments(process.argv.slice(2));
const port = Number(options.port);
const action = options.action ?? "run";
const targetFps = Number(options["target-fps"] ?? 60);
const durationSeconds = Number(options["duration-seconds"] ?? 5);
const cycles = Number(options.cycles ?? 3);
const outputPath = options.output;

if (!Number.isInteger(port) || port < 1 || port > 65_535) {
  throw new Error("--port must be a valid TCP port");
}
if (!["run", "status", "cadence", "rebind", "restart", "prepare-capture", "capture", "stop"].includes(action)) {
  throw new Error("--action must be run, status, cadence, rebind, restart, prepare-capture, capture, or stop");
}
if (action === "capture" && !outputPath) throw new Error("capture requires --output");
if (![60, 144, 300].includes(targetFps)) {
  throw new Error("--target-fps must be 60, 144, or 300");
}
if (!Number.isFinite(durationSeconds) || durationSeconds < 0 || durationSeconds > 300) {
  throw new Error("--duration-seconds must be between 0 and 300");
}
if (!Number.isInteger(cycles) || cycles < 1 || cycles > 10) {
  throw new Error("--cycles must be between 1 and 10");
}

const browser = await connectWithRetry(`http://127.0.0.1:${port}`, 30_000);
try {
  const page = await findPresencePage(browser, 30_000);
  const result = await page.evaluate(async ({ nextAction, framesPerSecond, waitMs, restartCycles }) => {
    const invoke = window.__TAURI_INTERNALS__?.invoke;
    if (typeof invoke !== "function") throw new Error("Tauri invoke is unavailable");
    const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
    const startRequest = () => ({
      target_frame_rate: framesPerSecond,
      capsule_visible: false,
      expansion_direction: "right",
      visual_state: "idle",
      opacity: 0.92,
      voice_level: 0,
      reduced_motion: false,
      reduced_transparency: false,
      increased_contrast: false,
      particles_enabled: true,
      frame_rate_limit: framesPerSecond,
      shape_droplet: 0,
      shape_bridge: 0,
      shape_capsule: 0,
      returning: false,
      core_x: 96,
      core_y: 88,
      capsule_x: 164,
      capsule_y: 220,
      capsule_half_width: 132,
    });
    if (nextAction === "run") {
      let started = null;
      let startError = null;
      try {
        let existing = await invoke("pet_native_gpu_status");
        const startupDeadline = performance.now() + 3_000;
        while (existing.lifecycle === "starting" && performance.now() < startupDeadline) {
          await sleep(25);
          existing = await invoke("pet_native_gpu_status");
        }
        if (existing.lifecycle === "running") {
          started = await invoke("pet_native_gpu_update", { request: startRequest() });
        } else {
          try {
            started = await invoke("pet_native_gpu_start", { request: startRequest() });
          } catch (error) {
            if (!String(error).includes("PRESENCE_NATIVE_GPU_ALREADY_RUNNING")) throw error;
            const takeoverDeadline = performance.now() + 3_000;
            do {
              await sleep(25);
              existing = await invoke("pet_native_gpu_status");
            } while (existing.lifecycle === "starting" && performance.now() < takeoverDeadline);
            if (existing.lifecycle !== "running") throw error;
            started = await invoke("pet_native_gpu_update", { request: startRequest() });
          }
        }
      } catch (error) {
        startError = String(error);
      }
      document.documentElement.dataset.nativeGpu = "active";
      for (const canvas of document.querySelectorAll("canvas")) {
        canvas.style.visibility = "hidden";
      }
      await new Promise((resolve) => setTimeout(resolve, waitMs));
      const status = await invoke("pet_native_gpu_status");
      return { started, start_error: startError, status };
    }
    if (nextAction === "cadence") {
      const samples = [];
      for (const frameRateLimit of [15, 30, 60, 144, 300]) {
        await invoke("pet_native_gpu_update", {
          request: {
            ...startRequest(),
            frame_rate_limit: frameRateLimit,
          },
        });
        await sleep(300);
        const applied = await invoke("pet_native_gpu_update", {
          request: {
            ...startRequest(),
            frame_rate_limit: frameRateLimit,
          },
        });

        // Native status is intentionally time-batched. First wait until the render loop has
        // applied this exact revision/rate, then cross one more publication boundary. Otherwise
        // frames rendered at the previous cadence can be counted in the new sample.
        const settleDeadline = performance.now() + 3_000;
        let before = await invoke("pet_native_gpu_status");
        const displayLimit = before.display_refresh_rate_hz >= 24
          ? before.display_refresh_rate_hz
          : framesPerSecond;
        const expectedEffectiveFrameRate = Math.min(
          frameRateLimit,
          framesPerSecond,
          displayLimit,
        );
        while (
          (
            before.presentation_revision < applied.presentation_revision
            || before.effective_frame_rate !== expectedEffectiveFrameRate
          )
          && performance.now() < settleDeadline
        ) {
          await sleep(25);
          before = await invoke("pet_native_gpu_status");
        }
        if (before.effective_frame_rate !== expectedEffectiveFrameRate) {
          throw new Error(`cadence rate did not apply at ${frameRateLimit} FPS`);
        }
        const appliedFrames = before.frames_presented;
        const baselineDeadline = performance.now() + 3_000;
        while (
          before.frames_presented <= appliedFrames
          && performance.now() < baselineDeadline
        ) {
          await sleep(25);
          before = await invoke("pet_native_gpu_status");
        }
        if (before.frames_presented <= appliedFrames) {
          throw new Error(`cadence baseline did not advance at ${frameRateLimit} FPS`);
        }

        let effectiveFrameRate = Math.max(1, before.effective_frame_rate);
        let minimumFrames = Math.max(30, Math.ceil(effectiveFrameRate * 1.5));
        let startedAt = performance.now();
        const sampleDeadline = startedAt + 12_000;
        let after = before;
        while (
          after.frames_presented - before.frames_presented < minimumFrames
          && performance.now() < sampleDeadline
        ) {
          await sleep(25);
          after = await invoke("pet_native_gpu_status");
          if (
            after.started_at_ms !== before.started_at_ms
            || after.frames_presented < before.frames_presented
          ) {
            // A monitor-source rebind creates a new session whose first presentation may still
            // contain the product-owned runtime limit. Reapply this diagnostic limit and wait for
            // its revision before starting a fresh session-local sample.
            const reapplied = await invoke("pet_native_gpu_update", {
              request: {
                ...startRequest(),
                frame_rate_limit: frameRateLimit,
              },
            });
            const reapplyDeadline = performance.now() + 3_000;
            do {
              await sleep(25);
              after = await invoke("pet_native_gpu_status");
            } while (
              (
                after.presentation_revision < reapplied.presentation_revision
                || after.effective_frame_rate !== expectedEffectiveFrameRate
              )
              && performance.now() < reapplyDeadline
            );
            if (after.effective_frame_rate !== expectedEffectiveFrameRate) {
              throw new Error(`cadence rate did not survive rebind at ${frameRateLimit} FPS`);
            }
            before = after;
            effectiveFrameRate = Math.max(1, expectedEffectiveFrameRate);
            minimumFrames = Math.max(30, Math.ceil(effectiveFrameRate * 1.5));
            startedAt = performance.now();
          }
        }
        const elapsedSeconds = (performance.now() - startedAt) / 1_000;
        const framesPresented = after.frames_presented - before.frames_presented;
        if (framesPresented < minimumFrames) {
          throw new Error(`cadence sample timed out at ${frameRateLimit} FPS`);
        }
        samples.push({
          frame_rate_limit: frameRateLimit,
          effective_frame_rate: after.effective_frame_rate,
          frames_presented: framesPresented,
          elapsed_seconds: elapsedSeconds,
          observed_fps: framesPresented / elapsedSeconds,
          presentation_revision: after.presentation_revision,
          lifecycle: after.lifecycle,
        });
      }
      return { samples, status: await invoke("pet_native_gpu_status") };
    }
    if (nextAction === "rebind") {
      const before = await invoke("pet_native_gpu_status");
      const rebound = await invoke("pet_native_gpu_rebind", { request: startRequest() });
      await sleep(100);
      const after = await invoke("pet_native_gpu_status");
      return { before, rebound, after };
    }
    if (nextAction === "restart") {
      const runs = [];
      for (let cycle = 0; cycle < restartCycles; cycle += 1) {
        let started;
        let attempts = 0;
        let lastError;
        while (attempts < 2 && started === undefined) {
          attempts += 1;
          try {
            started = await invoke("pet_native_gpu_start", { request: startRequest() });
          } catch (error) {
            lastError = String(error);
            await sleep(attempts * 300);
          }
        }
        if (started === undefined) throw new Error(lastError ?? "native restart failed");
        await sleep(350);
        const running = await invoke("pet_native_gpu_status");
        const stopped = await invoke("pet_native_gpu_stop");
        runs.push({ cycle, attempts, running, stopped });
        await sleep(350);
      }
      return { runs };
    }
    if (nextAction === "stop") {
      const status = await invoke("pet_native_gpu_stop");
      document.documentElement.dataset.nativeGpu = "inactive";
      for (const canvas of document.querySelectorAll("canvas")) {
        canvas.style.visibility = "";
      }
      return { status };
    }
    if (nextAction === "prepare-capture") {
      return { status: await invoke("pet_native_gpu_prepare_visual_test") };
    }
    if (nextAction === "capture") {
      const payload = await invoke("pet_backdrop_capture", {
        sequence: 1,
        experimentMode: "normal",
      });
      const bytes = payload instanceof Uint8Array ? payload : new Uint8Array(payload);
      const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      if (bytes.byteLength < 64 || String.fromCharCode(...bytes.subarray(0, 4)) !== "FBG2") {
        throw new Error("PRESENCE_NATIVE_GPU_CAPTURE_PACKET_INVALID");
      }
      const width = view.getUint32(4, true);
      const height = view.getUint32(8, true);
      const pixelOffset = view.getUint32(52, true);
      const rgba = bytes.subarray(pixelOffset);
      if (rgba.byteLength !== width * height * 4) {
        throw new Error("PRESENCE_NATIVE_GPU_CAPTURE_LENGTH_INVALID");
      }
      let binary = "";
      const chunkSize = 0x8000;
      for (let offset = 0; offset < rgba.byteLength; offset += chunkSize) {
        binary += String.fromCharCode(...rgba.subarray(offset, offset + chunkSize));
      }
      return { width, height, rgbaBase64: btoa(binary) };
    }
    return { status: await invoke("pet_native_gpu_status") };
  }, {
    nextAction: action,
    framesPerSecond: targetFps,
    waitMs: Math.round(durationSeconds * 1_000),
    restartCycles: cycles,
  });
  if (["run", "cadence", "rebind"].includes(action)) {
    // Native diagnostics deliberately update the live session behind the renderer host's cached
    // presentation key. Reload the renderer surface after collecting the result so the
    // product-owned Presence projection becomes authoritative again.
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForTimeout(500);
  }
  if (action === "capture") {
    const rgba = Buffer.from(result.rgbaBase64, "base64");
    const png = new PNG({ width: result.width, height: result.height });
    rgba.copy(png.data);
    fs.writeFileSync(outputPath, PNG.sync.write(png));
    process.stdout.write(`${JSON.stringify({ width: result.width, height: result.height, output: outputPath }, null, 2)}\n`);
  } else {
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  }
} finally {
  await new Promise((resolve) => {
    const timeout = setTimeout(resolve, 1_000);
    browser.close().catch(() => undefined).finally(() => {
      clearTimeout(timeout);
      resolve();
    });
  });
}

async function connectWithRetry(endpoint, timeoutMs) {
  const startedAt = Date.now();
  let lastError;
  while (Date.now() - startedAt < timeoutMs) {
    try {
      return await chromium.connectOverCDP(endpoint);
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  throw new Error(`WebView2 CDP did not become ready: ${String(lastError)}`);
}

async function findPresencePage(browser, timeoutMs) {
  const startedAt = Date.now();
  let discovered = [];
  while (Date.now() - startedAt < timeoutMs) {
    discovered = [];
    let renderPage = null;
    let inputReady = false;
    for (const context of browser.contexts()) {
      for (const page of context.pages()) {
        const surface = await page.evaluate(
          () => document.documentElement.dataset.surface ?? null,
        ).catch(() => null);
        discovered.push({ url: page.url(), surface });
        if (surface === "pet-render" || page.url().includes("surface=pet-render")) {
          renderPage = page;
        }
        if (surface === "pet-input" || page.url().includes("surface=pet-input")) {
          inputReady = true;
        }
      }
    }
    if (renderPage !== null && inputReady) {
      await new Promise((resolve) => setTimeout(resolve, 300));
      return renderPage;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`pet-render WebView is unavailable: ${JSON.stringify(discovered)}`);
}

function parseArguments(values) {
  const result = {};
  for (let index = 0; index < values.length; index += 2) {
    const name = values[index];
    const value = values[index + 1];
    if (!name?.startsWith("--") || value === undefined) {
      throw new Error(`Invalid argument near ${name ?? "end of input"}`);
    }
    result[name.slice(2)] = value;
  }
  return result;
}
