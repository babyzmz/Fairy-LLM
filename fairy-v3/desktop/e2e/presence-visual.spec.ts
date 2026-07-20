import { expect, test, type Page } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { PNG } from "pngjs";

import type { PresenceInteractionSnapshot } from "../src/presence/domain/interaction";

const WIDTH = 640;
const HEIGHT = 260;

test("stable liquid surfaces stay aligned and return without a one-frame jump", async ({
  browser,
}, testInfo) => {
  const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT } });
  const page = await context.newPage();
  await page.goto("/?surface=pet-render");
  await waitForInteractionSource(page);
  await publishInputPresentation(page, true);
  await publishInteraction(page, interactionSnapshot("interactive", 1, 520));
  await page.waitForTimeout(800);

  const interactive = PNG.sync.read(await page.screenshot({ omitBackground: true }));
  await writeFile(testInfo.outputPath("liquid-interactive.png"), PNG.sync.write(interactive));
  const coreCoverage = axisCoverage(interactive, 24, 168, 88, 72);
  const capsuleCoverage = axisCoverage(interactive, 31, 297, 220, 26);
  expect(coreCoverage).toBeGreaterThan(0.96);
  expect(capsuleCoverage).toBeGreaterThan(0.96);
  expect(axisCoverage(interactive, 172, 220, 176, 5)).toBeLessThan(0.1);
  expect(visiblePixelRatio(interactive)).toBeLessThan(0.34);
  expect(alphaAt(interactive, 0, 0)).toBe(0);
  expect(alphaAt(interactive, WIDTH - 1, HEIGHT - 1)).toBe(0);
  expect(maximumAlpha(interactive)).toBeLessThan(230);

  await startReturnMotionRecording(page);
  await publishInteraction(page, interactionSnapshot("returning", 2, 1_000));
  await waitForReturnMotionComplete(page);
  const returnSamples = await readReturnMotionSamples(page);
  expect(returnSamples[0]?.capsule).toBeGreaterThan(0.75);
  expect(returnSamples.some((sample) => sample.capsule > 0.15 && sample.capsule < 0.85))
    .toBe(true);
  expect(Math.max(...returnSamples.map((sample) => sample.bridge))).toBeGreaterThan(0.25);
  for (let index = 1; index < returnSamples.length; index += 1) {
    expect(returnSamples[index].capsule - returnSamples[index - 1].capsule)
      .toBeLessThanOrEqual(0.01);
  }

  const returned = PNG.sync.read(await page.screenshot({ omitBackground: true }));
  await writeFile(testInfo.outputPath("liquid-return-complete.png"), PNG.sync.write(returned));
  expect(axisCoverage(returned, 31, 297, 220, 26)).toBeLessThan(0.42);
  expect(alphaAt(returned, 0, 0)).toBe(0);
  expect(alphaAt(returned, WIDTH - 1, HEIGHT - 1)).toBe(0);
  await context.close();
});

test("liquid glass remains legible across desktop background classes", async ({
  browser,
}, testInfo) => {
  const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT } });
  const page = await context.newPage();
  await page.goto("/?surface=pet-render");
  await waitForInteractionSource(page);
  await publishInputPresentation(page, true);
  await publishInteraction(page, interactionSnapshot("interactive", 1, 520));
  await page.waitForTimeout(800);
  const foreground = PNG.sync.read(await page.screenshot({ omitBackground: true }));

  for (const backgroundName of [
    "light",
    "dark",
    "complex",
    "high-contrast",
    "hdr-tone-map",
    "video-frame-a",
    "video-frame-b",
  ] as const) {
    const background = createBackground(backgroundName, WIDTH, HEIGHT);
    const composited = composite(foreground, background);
    await writeFile(
      testInfo.outputPath(`liquid-on-${backgroundName}.png`),
      PNG.sync.write(composited),
    );
    expect(changedPixelCount(composited, background, 2)).toBeGreaterThan(1_000);
    expect(alphaAt(foreground, 0, 0)).toBe(0);
  }
  await context.close();
});

test("liquid optics expose bounded thickness, paired dispersion, and screen lock", async ({
  browser,
}, testInfo) => {
  const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT } });
  const page = await context.newPage();
  await page.goto("/?surface=pet-render");
  await waitForInteractionSource(page);
  await publishInputPresentation(page, true);
  await publishInteraction(page, interactionSnapshot("interactive", 1, 520));
  await page.waitForTimeout(800);

  const origin = PNG.sync.read(await page.screenshot({ omitBackground: true }));
  await writeFile(testInfo.outputPath("liquid-optics-origin.png"), PNG.sync.write(origin));
  const centerAlpha = alphaAt(origin, 164, 220);
  const rimAlpha = Math.max(
    maximumAlphaAtX(origin, 164, 188, 202),
    maximumAlphaAtX(origin, 164, 238, 252),
  );
  expect(centerAlpha).toBeGreaterThanOrEqual(8);
  expect(centerAlpha).toBeLessThanOrEqual(29);
  expect(rimAlpha).toBeGreaterThanOrEqual(64);
  expect(rimAlpha).toBeLessThanOrEqual(120);
  expect(rimAlpha - centerAlpha).toBeGreaterThanOrEqual(48);

  const dispersion = spectralExtremes(origin, {
    left: 8,
    top: 8,
    right: 304,
    bottom: 258,
  });
  expect(dispersion.warm).toBeGreaterThanOrEqual(32);
  expect(dispersion.cool).toBeGreaterThanOrEqual(32);

  await publishInteraction(page, interactionSnapshot("interactive", 2, 1_000, 320));
  await page.waitForTimeout(120);
  const moved = PNG.sync.read(await page.screenshot({ omitBackground: true }));
  await writeFile(testInfo.outputPath("liquid-optics-moved.png"), PNG.sync.write(moved));
  const movedCoverage = axisCoverage(moved, 31, 297, 220, 26);
  expect(movedCoverage).toBe(1);
  expect(changedPixelCountInBounds(origin, moved, 5, {
    left: 8,
    top: 8,
    right: 304,
    bottom: 258,
  })).toBeGreaterThan(2_000);
  await context.close();
});

test("compatibility glass stays anchored with restrained spectral rims", async ({
  browser,
}, testInfo) => {
  const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT } });
  const page = await context.newPage();
  await page.goto("/?surface=pet-render");
  await waitForInteractionSource(page);
  await publishRenderSettings(page, "compatibility");
  await expect(page.getByTestId("presence-renderer")).toHaveAttribute(
    "data-renderer",
    "compatibility",
  );
  await publishInteraction(page, interactionSnapshot("interactive", 1, 520));
  await page.waitForTimeout(300);

  const frame = PNG.sync.read(await page.screenshot({ omitBackground: true }));
  await writeFile(testInfo.outputPath("compatibility-glass.png"), PNG.sync.write(frame));
  const centroid = alphaCentroid(frame, 8);
  expect(centroid.x).toBeGreaterThan(72);
  expect(centroid.x).toBeLessThan(126);
  expect(centroid.y).toBeGreaterThan(70);
  expect(centroid.y).toBeLessThan(110);
  expect(alphaAt(frame, 0, 0)).toBe(0);
  expect(alphaAt(frame, WIDTH - 1, HEIGHT - 1)).toBe(0);
  const dispersion = spectralExtremes(frame, {
    left: 8,
    top: 8,
    right: 186,
    bottom: 180,
  });
  expect(dispersion.warm).toBeGreaterThanOrEqual(12);
  expect(dispersion.cool).toBeGreaterThanOrEqual(12);
  await context.close();
});

async function publishInteraction(page: Page, snapshot: PresenceInteractionSnapshot) {
  await page.evaluate((value) => {
    const channel = new BroadcastChannel("fairy.presence.interaction.v1");
    const publish = () => channel.postMessage({
      kind: "presence.interaction",
      snapshot: value,
    });
    publish();
    window.setTimeout(publish, 30);
    window.setTimeout(publish, 80);
    window.setTimeout(() => channel.close(), 120);
  }, snapshot);
}

interface ReturnMotionSample {
  bridge: number;
  capsule: number;
}

async function startReturnMotionRecording(page: Page) {
  await page.evaluate(() => {
    type MotionWindow = typeof window & {
      __fairyReturnMotionSamples?: Array<{ bridge: number; capsule: number }>;
      __fairyReturnMotionFrame?: number;
    };
    const motionWindow = window as MotionWindow;
    motionWindow.__fairyReturnMotionSamples = [];
    if (motionWindow.__fairyReturnMotionFrame !== undefined) {
      cancelAnimationFrame(motionWindow.__fairyReturnMotionFrame);
    }
    const record = () => {
      const surface = document.querySelector<HTMLElement>(
        '[data-testid="presence-render-surface"]',
      );
      const canvas = document.querySelector<HTMLCanvasElement>(".presence-webgl-canvas");
      if (surface?.dataset.interactionPhase === "returning" && canvas !== null) {
        const bridge = Number(canvas.dataset.shapeBridge);
        const capsule = Number(canvas.dataset.shapeCapsule);
        if (Number.isFinite(bridge) && Number.isFinite(capsule)) {
          motionWindow.__fairyReturnMotionSamples?.push({ bridge, capsule });
        }
      }
      const complete = motionWindow.__fairyReturnMotionSamples?.some(
        (sample) => sample.bridge === 0 && sample.capsule === 0,
      );
      if (!complete) {
        motionWindow.__fairyReturnMotionFrame = requestAnimationFrame(record);
      }
    };
    motionWindow.__fairyReturnMotionFrame = requestAnimationFrame(record);
  });
}

async function readReturnMotionSamples(page: Page): Promise<ReturnMotionSample[]> {
  return page.evaluate(() => {
    const motionWindow = window as typeof window & {
      __fairyReturnMotionSamples?: ReturnMotionSample[];
    };
    return motionWindow.__fairyReturnMotionSamples ?? [];
  });
}

async function waitForReturnMotionComplete(page: Page) {
  try {
    await page.waitForFunction(() => {
      const motionWindow = window as typeof window & {
        __fairyReturnMotionSamples?: ReturnMotionSample[];
      };
      const samples = motionWindow.__fairyReturnMotionSamples ?? [];
      const last = samples.at(-1);
      return samples.length >= 5 && last?.capsule === 0 && last.bridge === 0;
    }, undefined, { timeout: 2_000 });
  } catch (error) {
    const samples = await readReturnMotionSamples(page);
    throw new Error(
      `Return motion did not settle: ${JSON.stringify(samples.slice(-12))}`,
      { cause: error },
    );
  }
}

async function waitForInteractionSource(page: Page) {
  await expect(page.getByTestId("presence-renderer")).toHaveAttribute(
    "data-renderer",
    "liquid",
  );
  await expect(page.getByTestId("presence-renderer")).toHaveAttribute(
    "data-renderer-health",
    "running",
  );
  await page.locator('[data-testid="presence-render-surface"][data-interaction-ready="true"]')
    .waitFor({ state: "attached" });
}

async function publishRenderSettings(
  page: Page,
  mode: "liquid" | "compatibility",
) {
  await page.evaluate((rendererMode) => {
    const channel = new BroadcastChannel("fairy.presence.render-settings.v1");
    channel.postMessage({
      kind: "render-settings.snapshot",
      settings: {
        schema_version: 3,
        mode: rendererMode,
        optics_mode: "standard",
        size_scale: 1,
        opacity: 0.92,
        motion_enabled: true,
        particles_enabled: true,
        target_frame_rate: 60,
      },
    });
    window.setTimeout(() => channel.close(), 100);
  }, mode);
}

async function publishInputPresentation(page: Page, capsuleVisible: boolean) {
  await page.evaluate((visible) => {
    const channel = new BroadcastChannel("fairy.presence.input-presentation.v3");
    const message = {
      kind: "input-presentation.snapshot",
      presentation: {
        schema_version: 3,
        sequence: 1,
        layout: visible ? "compact" : "core",
        capsule_visible: visible,
        capsule_width: 280,
        motion: {
          schema_version: 1,
          revision: 1,
          state: visible ? "input" : "idle",
          surface: visible ? "input" : "core",
          activity: "none",
          state_started_at_ms: 0,
          state_duration_ms: null,
          phase_progress: 1,
          content_visible: visible,
          surface_interactive: true,
          capsule_visible: visible,
          reduced_motion: false,
          do_not_disturb: false,
        },
      },
    };
    const publish = () => channel.postMessage(message);
    publish();
    window.setTimeout(publish, 30);
    window.setTimeout(publish, 80);
    window.setTimeout(() => channel.close(), 120);
  }, capsuleVisible);
}

function interactionSnapshot(
  phase: PresenceInteractionSnapshot["phase"],
  sequence: number,
  sampledAt: number,
  renderX = 0,
): PresenceInteractionSnapshot {
  const active = phase !== "returning";
  return {
    schema_version: 1,
    sequence,
    sampled_at_ms: sampledAt,
    phase,
    phase_started_at_ms: sampledAt,
    reduced_motion: false,
    cursor: {
      point: { x: active ? 136 : 500, y: 88 },
      direction: { x: active ? 1 : 0, y: 0 },
      distance_px: active ? 40 : 404,
      speed_px_s: 0,
      dwell_ms: active ? 520 : 0,
      band: active ? "active" : "outside",
    },
    placement: {
      anchor: { x: renderX + 96, y: 88 },
      render_frame: { x: renderX, y: 0, width: WIDTH, height: HEIGHT },
      input_compact_frame: { x: renderX + 24, y: 0, width: 280, height: 260 },
      input_expanded_frame: { x: renderX + 24, y: -100, width: 616, height: 360 },
      monitor_work_area: { x: 0, y: 0, width: 1920, height: 1040 },
      scale_factor: 1,
      expansion_direction: "right",
    },
  };
}

type BackgroundName =
  | "light"
  | "dark"
  | "complex"
  | "high-contrast"
  | "hdr-tone-map"
  | "video-frame-a"
  | "video-frame-b";

function createBackground(name: BackgroundName, width: number, height: number): PNG {
  const image = new PNG({ width, height });
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const index = (y * width + x) * 4;
      const color = backgroundColor(name, x, y);
      image.data[index] = color[0];
      image.data[index + 1] = color[1];
      image.data[index + 2] = color[2];
      image.data[index + 3] = 255;
    }
  }
  return image;
}

function backgroundColor(
  name: BackgroundName,
  x: number,
  y: number,
): readonly [number, number, number] {
  if (name === "light") return [238, 241, 243];
  if (name === "dark") return [16, 19, 21];
  if (name === "high-contrast") return (Math.floor(x / 24) + Math.floor(y / 24)) % 2
    ? [250, 250, 250]
    : [4, 4, 4];
  if (name === "hdr-tone-map") {
    const bright = (x - 490) ** 2 + (y - 42) ** 2 < 4_800;
    return bright ? [255, 252, 242] : [3, 8, 14];
  }
  if (name === "complex") {
    const broad = 0.5 + 0.5 * Math.sin(x / 43 + y / 67);
    const detail = 0.5 + 0.5 * Math.sin(x / 11 - y / 17);
    const panel = x > 310 && x < 570 && y > 36 && y < 214 ? 36 : 0;
    return [
      Math.round(28 + broad * 78 + detail * 22 + panel),
      Math.round(48 + broad * 62 + (1 - detail) * 34 + panel),
      Math.round(66 + (1 - broad) * 70 + detail * 28 + panel),
    ];
  }
  const offset = name === "video-frame-a" ? 0 : 37;
  const wave = Math.round(42 * (0.5 + 0.5 * Math.sin((x + offset) / 21 + y / 31)));
  return [28 + wave, 52 + ((wave + offset) % 70), 74 + ((wave * 2) % 90)];
}

function composite(foreground: PNG, background: PNG): PNG {
  const output = new PNG({ width: foreground.width, height: foreground.height });
  for (let index = 0; index < foreground.data.length; index += 4) {
    const alpha = foreground.data[index + 3] / 255;
    for (let channel = 0; channel < 3; channel += 1) {
      output.data[index + channel] = Math.round(
        foreground.data[index + channel] * alpha +
          background.data[index + channel] * (1 - alpha),
      );
    }
    output.data[index + 3] = 255;
  }
  return output;
}

function axisCoverage(
  image: PNG,
  startX: number,
  endX: number,
  centerY: number,
  halfHeight: number,
): number {
  let covered = 0;
  for (let x = startX; x <= endX; x += 1) {
    let maximum = 0;
    for (let y = centerY - halfHeight; y <= centerY + halfHeight; y += 1) {
      maximum = Math.max(maximum, alphaAt(image, x, y));
    }
    if (maximum > 4) covered += 1;
  }
  return covered / (endX - startX + 1);
}

function visiblePixelRatio(image: PNG): number {
  let visible = 0;
  for (let index = 3; index < image.data.length; index += 4) {
    if (image.data[index] > 4) visible += 1;
  }
  return visible / (image.width * image.height);
}

function maximumAlpha(image: PNG): number {
  let maximum = 0;
  for (let index = 3; index < image.data.length; index += 4) {
    maximum = Math.max(maximum, image.data[index]);
  }
  return maximum;
}

function maximumAlphaAtX(
  image: PNG,
  x: number,
  startY: number,
  endY: number,
): number {
  let maximum = 0;
  for (let y = startY; y <= endY; y += 1) {
    maximum = Math.max(maximum, alphaAt(image, x, y));
  }
  return maximum;
}

function changedPixelCount(left: PNG, right: PNG, threshold: number): number {
  let changed = 0;
  for (let index = 0; index < left.data.length; index += 4) {
    const difference = Math.max(
      Math.abs(left.data[index] - right.data[index]),
      Math.abs(left.data[index + 1] - right.data[index + 1]),
      Math.abs(left.data[index + 2] - right.data[index + 2]),
    );
    if (difference >= threshold) changed += 1;
  }
  return changed;
}

interface PixelBounds {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

function changedPixelCountInBounds(
  left: PNG,
  right: PNG,
  threshold: number,
  bounds: PixelBounds,
): number {
  let changed = 0;
  for (let y = bounds.top; y <= bounds.bottom; y += 1) {
    for (let x = bounds.left; x <= bounds.right; x += 1) {
      const index = (y * left.width + x) * 4;
      const difference = Math.max(
        Math.abs(left.data[index] - right.data[index]),
        Math.abs(left.data[index + 1] - right.data[index + 1]),
        Math.abs(left.data[index + 2] - right.data[index + 2]),
      );
      if (difference >= threshold) changed += 1;
    }
  }
  return changed;
}

function spectralExtremes(image: PNG, bounds: PixelBounds) {
  let warm = Number.NEGATIVE_INFINITY;
  let cool = Number.NEGATIVE_INFINITY;
  for (let y = bounds.top; y <= bounds.bottom; y += 1) {
    for (let x = bounds.left; x <= bounds.right; x += 1) {
      const index = (y * image.width + x) * 4;
      const alpha = image.data[index + 3];
      if (alpha < 20 || alpha > 100) continue;
      const red = image.data[index];
      const blue = image.data[index + 2];
      warm = Math.max(warm, red - blue);
      cool = Math.max(cool, blue - red);
    }
  }
  return { warm, cool };
}

function alphaCentroid(image: PNG, threshold: number) {
  let weight = 0;
  let weightedX = 0;
  let weightedY = 0;
  for (let y = 0; y < image.height; y += 1) {
    for (let x = 0; x < image.width; x += 1) {
      const alpha = alphaAt(image, x, y);
      if (alpha < threshold) continue;
      weight += alpha;
      weightedX += x * alpha;
      weightedY += y * alpha;
    }
  }
  return {
    x: weightedX / Math.max(1, weight),
    y: weightedY / Math.max(1, weight),
  };
}

function alphaAt(image: PNG, x: number, y: number): number {
  return image.data[(y * image.width + x) * 4 + 3] ?? 0;
}
