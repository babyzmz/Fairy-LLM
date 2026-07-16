import fs from "node:fs";

import { chromium } from "playwright";
import { PNG } from "pngjs";

const options = parseArguments(process.argv.slice(2));
const port = Number(options.port);
const action = options.action ?? "run";
const targetFps = Number(options["target-fps"] ?? 60);
const durationSeconds = Number(options["duration-seconds"] ?? 5);
const outputPath = options.output;

if (!Number.isInteger(port) || port < 1 || port > 65_535) {
  throw new Error("--port must be a valid TCP port");
}
if (!["run", "status", "prepare-capture", "capture", "stop"].includes(action)) {
  throw new Error("--action must be run, status, prepare-capture, capture, or stop");
}
if (action === "capture" && !outputPath) throw new Error("capture requires --output");
if (![60, 144].includes(targetFps)) {
  throw new Error("--target-fps must be 60 or 144");
}
if (!Number.isFinite(durationSeconds) || durationSeconds < 0 || durationSeconds > 300) {
  throw new Error("--duration-seconds must be between 0 and 300");
}

const browser = await connectWithRetry(`http://127.0.0.1:${port}`, 30_000);
try {
  const page = await findPresencePage(browser, 30_000);
  const result = await page.evaluate(async ({ nextAction, framesPerSecond, waitMs }) => {
    const invoke = window.__TAURI_INTERNALS__?.invoke;
    if (typeof invoke !== "function") throw new Error("Tauri invoke is unavailable");
    if (nextAction === "run") {
      const started = await invoke("pet_native_gpu_start", {
        request: {
          target_frame_rate: framesPerSecond,
          capsule_visible: true,
          visual_state: "aware",
          opacity: 0.92,
        },
      });
      document.documentElement.dataset.nativeGpu = "active";
      for (const canvas of document.querySelectorAll("canvas")) {
        canvas.style.visibility = "hidden";
      }
      await new Promise((resolve) => setTimeout(resolve, waitMs));
      const status = await invoke("pet_native_gpu_status");
      return { started, status };
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
  });
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
  await browser.close();
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
    for (const context of browser.contexts()) {
      for (const page of context.pages()) {
        const surface = await page.evaluate(
          () => document.documentElement.dataset.surface ?? null,
        ).catch(() => null);
        discovered.push({ url: page.url(), surface });
        if (surface === "pet-render" || page.url().includes("surface=pet-render")) {
          return page;
        }
      }
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
