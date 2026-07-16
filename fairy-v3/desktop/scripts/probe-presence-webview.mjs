import { chromium } from "playwright";
import { writeFile } from "node:fs/promises";

const argumentsMap = parseArguments(process.argv.slice(2));
const port = Number(argumentsMap.port);
const expectedMode = argumentsMap["expected-mode"] ?? "liquid";
const warmupSeconds = Number(argumentsMap["warmup-seconds"] ?? 0);
const durationSeconds = Number(argumentsMap["duration-seconds"] ?? 0);
const outputPath = argumentsMap.output ?? null;
const experimentMode = argumentsMap.experiment ?? "normal";
const targetFps = Number(argumentsMap["target-fps"] ?? 60);
const experimentModes = new Set([
  "normal",
  "static-backdrop",
  "capture-only",
  "ipc-upload-only",
  "single-renderer",
  "no-particles",
  "no-refraction",
]);

if (!Number.isInteger(port) || port < 1 || port > 65_535) {
  throw new Error("--port must be a valid TCP port");
}
if (!["liquid", "compatibility"].includes(expectedMode)) {
  throw new Error("--expected-mode must be liquid or compatibility");
}
if (!Number.isFinite(warmupSeconds) || warmupSeconds < 0) {
  throw new Error("--warmup-seconds must be non-negative");
}
if (!Number.isFinite(durationSeconds) || durationSeconds < 0) {
  throw new Error("--duration-seconds must be non-negative");
}
if (!experimentModes.has(experimentMode)) {
  throw new Error("--experiment must be a supported Presence experiment mode");
}
if (![60, 144].includes(targetFps)) {
  throw new Error("--target-fps must be 60 or 144");
}

const browser = await connectWithRetry(`http://127.0.0.1:${port}`, 30_000);
await configureExperiment(browser, experimentMode, targetFps);
const page = await findPresencePage(browser, 30_000);
const renderer = page.getByTestId("presence-renderer");
await renderer.waitFor({ state: "visible", timeout: 30_000 });
await page.waitForFunction(
  (mode) => document.querySelector('[data-testid="presence-renderer"]')?.getAttribute("data-renderer") === mode,
  expectedMode,
  { timeout: 30_000 },
);
const activeCanvas = expectedMode === "liquid"
  ? page.locator("canvas.presence-webgl-canvas")
  : page.locator("canvas.presence-compatibility-canvas");
await activeCanvas.waitFor({ state: "visible", timeout: 30_000 });
await page.waitForFunction(
  (selector) => document.querySelector(selector)?.getAttribute("data-rendered") === "true",
  expectedMode === "liquid"
    ? "canvas.presence-webgl-canvas"
    : "canvas.presence-compatibility-canvas",
  { timeout: 30_000 },
);
await page.waitForFunction(() => {
  const surface = document.querySelector('[data-testid="presence-render-surface"]');
  return surface instanceof HTMLElement &&
    surface.dataset.anchorX !== undefined &&
    surface.dataset.anchorX !== "" &&
    surface.dataset.placementScale !== undefined &&
    surface.dataset.placementScale !== "";
}, undefined, { timeout: 30_000 });

if (warmupSeconds > 0) {
  await page.waitForTimeout(warmupSeconds * 1_000);
}
await resetRuntimeMetrics(browser);
await page.waitForTimeout(Math.max(50, 1_000 / targetFps));
const initial = await readMetrics(page, expectedMode);
const pagesInitial = await readPageHeaps(browser);
if (durationSeconds > 0) {
  await page.waitForTimeout(durationSeconds * 1_000);
}
const final = await readMetrics(page, expectedMode);
const pagesFinal = await readPageHeaps(browser);
const gpu = await readGpuInfo(browser);
const result = {
  url: page.url(),
  expected_mode: expectedMode,
  experiment_mode: experimentMode,
  target_fps: targetFps,
  warmup_seconds: warmupSeconds,
  duration_seconds: durationSeconds,
  live_heap_growth_bytes:
    initial.used_js_heap_bytes === null || final.used_js_heap_bytes === null
      ? null
      : final.used_js_heap_bytes - initial.used_js_heap_bytes,
  gpu,
  initial,
  final,
  pages_initial: pagesInitial,
  pages_final: pagesFinal,
};
const serialized = `${JSON.stringify(result, null, 2)}\n`;
if (outputPath !== null) await writeFile(outputPath, serialized, "utf8");
process.stdout.write(serialized);
process.exit(0);

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
        const identity = await page.evaluate(() => ({
          surface: document.documentElement.dataset.surface ?? null,
          title: document.title,
        })).catch(() => ({ surface: null, title: "unavailable" }));
        discovered.push({ url: page.url(), ...identity });
        if (page.url().includes("surface=pet-render") || identity.surface === "pet-render") {
          return page;
        }
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`pet-render WebView was not exposed through CDP: ${JSON.stringify(discovered)}`);
}

async function configureExperiment(browser, mode, framesPerSecond) {
  const startedAt = Date.now();
  let presencePages = [];
  while (presencePages.length < 2 && Date.now() - startedAt < 30_000) {
    presencePages = [];
    for (const context of browser.contexts()) {
      for (const page of context.pages()) {
        const surface = await page.evaluate(
          () => document.documentElement.dataset.surface ?? null,
        ).catch(() => null);
        if (surface === "pet-render" || surface === "pet-input") {
          presencePages.push(page);
        }
      }
    }
    if (presencePages.length < 2) {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  if (presencePages.length < 2) throw new Error("Presence experiment surfaces are unavailable");
  for (const page of presencePages) {
    await page.evaluate(({ value, target }) => {
      localStorage.setItem("fairy.presence.experiment", value);
      localStorage.setItem("fairy.presence.target-fps-experiment", String(target));
    }, { value: mode, target: framesPerSecond });
  }
  await Promise.all(presencePages.map((page) => page.reload({ waitUntil: "domcontentloaded" })));
}

async function resetRuntimeMetrics(browser) {
  const resetEventName = "fairy:presence-runtime-metrics-reset";
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      const isPresenceSurface = await page.evaluate(
        () => ["pet-render", "pet-input"].includes(
          document.documentElement.dataset.surface ?? "",
        ),
      ).catch(() => false);
      if (!isPresenceSurface) continue;
      await page.evaluate((eventName) => {
        window.dispatchEvent(new Event(eventName));
      }, resetEventName);
    }
  }
}

async function readGpuInfo(browser) {
  let session;
  try {
    session = await browser.newBrowserCDPSession();
    const { gpu } = await session.send("SystemInfo.getInfo");
    return {
      devices: gpu.devices.map((device) => ({
        vendor_id: device.vendorId,
        device_id: device.deviceId,
        vendor: device.vendorString,
        device: device.deviceString,
        driver_version: device.driverVersion,
      })),
      feature_status: gpu.featureStatus,
      gl_renderer: gpu.auxAttributes?.glRenderer ?? null,
      display_type: gpu.auxAttributes?.displayType ?? null,
    };
  } catch (error) {
    return { error: String(error) };
  } finally {
    await session?.detach();
  }
}

async function readPageHeaps(browser) {
  const pages = [];
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      const metrics = await page.evaluate(() => ({
        surface: document.documentElement.dataset.surface ?? null,
        title: document.title,
        visibility: document.visibilityState,
        used_js_heap_bytes:
          typeof performance.memory?.usedJSHeapSize === "number"
            ? performance.memory.usedJSHeapSize
            : null,
      })).catch((error) => ({
        surface: null,
        title: "unavailable",
        visibility: "unavailable",
        used_js_heap_bytes: null,
        error: String(error),
      }));
      pages.push({ url: page.url(), ...metrics });
    }
  }
  return pages;
}

async function readMetrics(page, mode) {
  return page.evaluate((rendererMode) => {
    const numberOrNull = (value) => {
      if (value === undefined || value === "" || value === "unavailable") return null;
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    };
    const renderer = document.querySelector('[data-testid="presence-renderer"]');
    const surface = document.querySelector('[data-testid="presence-render-surface"]');
    const canvas = document.querySelector(
      rendererMode === "liquid"
        ? "canvas.presence-webgl-canvas"
        : "canvas.presence-compatibility-canvas",
    );
    if (!(renderer instanceof HTMLElement) || !(canvas instanceof HTMLCanvasElement)) {
      throw new Error("presence renderer DOM is incomplete");
    }
    return {
      renderer: renderer.dataset.renderer ?? null,
      health: renderer.dataset.rendererHealth ?? null,
      error_code: renderer.dataset.errorCode || null,
      interaction_phase: surface instanceof HTMLElement ? surface.dataset.interactionPhase ?? null : null,
      input_capsule_visible: surface instanceof HTMLElement
        ? surface.dataset.inputCapsuleVisible === "true"
        : false,
      cursor_band: surface instanceof HTMLElement ? surface.dataset.cursorBand ?? null : null,
      cursor_distance: surface instanceof HTMLElement ? numberOrNull(surface.dataset.cursorDistance) : null,
      cursor_point: surface instanceof HTMLElement ? {
        x: numberOrNull(surface.dataset.cursorX),
        y: numberOrNull(surface.dataset.cursorY),
      } : null,
      expansion_direction: surface instanceof HTMLElement ? surface.dataset.expansionDirection ?? null : null,
      placement: surface instanceof HTMLElement ? {
        anchor_x: numberOrNull(surface.dataset.anchorX),
        anchor_y: numberOrNull(surface.dataset.anchorY),
        scale_factor: numberOrNull(surface.dataset.placementScale),
        render_x: numberOrNull(surface.dataset.renderX),
        render_y: numberOrNull(surface.dataset.renderY),
        work_area_x: numberOrNull(surface.dataset.workAreaX),
        work_area_y: numberOrNull(surface.dataset.workAreaY),
        work_area_width: numberOrNull(surface.dataset.workAreaWidth),
        work_area_height: numberOrNull(surface.dataset.workAreaHeight),
      } : null,
      canvas_width: canvas.width,
      canvas_height: canvas.height,
      timing_samples: numberOrNull(canvas.dataset.timingSamples),
      cpu_frame_p95_ms: numberOrNull(canvas.dataset.cpuFrameP95Ms),
      gpu_frame_p95_ms: numberOrNull(canvas.dataset.gpuFrameP95Ms),
      heap_growth_bytes: numberOrNull(canvas.dataset.heapGrowthBytes),
      frame_samples: numberOrNull(canvas.dataset.frameSamples),
      fps_avg: numberOrNull(canvas.dataset.fpsAvg),
      fps_p1: numberOrNull(canvas.dataset.fpsP1),
      deadline_miss_count: numberOrNull(canvas.dataset.deadlineMissCount),
      backdrop_samples: numberOrNull(canvas.dataset.backdropSamples),
      backdrop_fps_avg: numberOrNull(canvas.dataset.backdropFpsAvg),
      capture_p95_ms: numberOrNull(canvas.dataset.captureP95Ms),
      pack_p95_ms: numberOrNull(canvas.dataset.packP95Ms),
      ipc_p95_ms: numberOrNull(canvas.dataset.ipcP95Ms),
      parse_p95_ms: numberOrNull(canvas.dataset.parseP95Ms),
      upload_cpu_p95_ms: numberOrNull(canvas.dataset.uploadCpuP95Ms),
      backdrop_age_p95_ms: numberOrNull(canvas.dataset.backdropAgeP95Ms),
      dropped_frame_count: numberOrNull(canvas.dataset.droppedFrameCount),
      experiment_mode: canvas.dataset.experimentMode ?? null,
      liquid_shape: {
        droplet: numberOrNull(canvas.dataset.shapeDroplet),
        bridge: numberOrNull(canvas.dataset.shapeBridge),
        capsule: numberOrNull(canvas.dataset.shapeCapsule),
      },
      target_frame_rate: surface instanceof HTMLElement
        ? numberOrNull(surface.dataset.targetFrameRate)
        : null,
      used_js_heap_bytes: numberOrNull(performance.memory?.usedJSHeapSize),
      visible_controls: document.querySelectorAll("button, input, textarea, select").length,
      horizontal_overflow: Math.max(0, document.documentElement.scrollWidth - innerWidth),
      vertical_overflow: Math.max(0, document.documentElement.scrollHeight - innerHeight),
    };
  }, mode);
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
