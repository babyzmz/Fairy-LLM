import { expect, test, type Page } from "@playwright/test";
import { PNG } from "pngjs";

import type { PresenceProjectionState } from "../src/presence/domain/projection";

const DISPLAY_SCALES = [1, 1.25, 1.5, 2] as const;

for (const scale of DISPLAY_SCALES) {
  test(`render surface stays transparent at ${scale * 100}% scale`, async ({
    browser,
  }, testInfo) => {
    const context = await browser.newContext({
      deviceScaleFactor: scale,
      viewport: { width: 640, height: 260 },
    });
    const page = await context.newPage();
    await page.goto("/?surface=pet-render");

    await expect(page.getByTestId("presence-render-surface")).toBeVisible();
    const canvas = page.locator("canvas.presence-webgl-canvas");
    await expect(canvas).toHaveAttribute("data-rendered", "true");
    await expect(page.getByRole("button")).toHaveCount(0);
    expect(await canvas.evaluate((element) => ({
      height: (element as HTMLCanvasElement).height,
      width: (element as HTMLCanvasElement).width,
    }))).toEqual({
      height: Math.round(260 * scale),
      width: Math.round(640 * scale),
    });
    const screenshot = await page.screenshot({
      path: testInfo.outputPath(`pet-render-liquid-${scale * 100}.png`),
      omitBackground: true,
    });
    const pixels = PNG.sync.read(screenshot);
    expect(visiblePngPixels(pixels)).toBeGreaterThan(250);
    expect(alphaAt(pixels, 0, 0)).toBe(0);
    expect(alphaAt(pixels, Math.round(96 * scale), Math.round(130 * scale))).toBeGreaterThan(0);
    expect(await overflow(page)).toEqual({ horizontal: 0, vertical: 0 });
    await context.close();
  });
}

test("input surface owns cards and controls without duplicating the renderer", async ({
  browser,
}, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 420, height: 360 } });
  const page = await context.newPage();
  await page.goto("/?surface=pet-input");
  await publishProjection(page, {
    activity: "working",
    work_state: "streaming",
    status_text: "Writing the reply",
    last_cursor: 12,
    last_event_id: "event-12",
    updated_at_ms: Date.now(),
    recent_activity_ms: [Date.now()],
    notice: null,
    reply: {
      id: "reply-12",
      text: "The reply card is isolated from the render-only surface.",
      kind: "scratch",
      streaming: true,
    },
    speaking: false,
  });

  await expect(page.getByRole("status")).toContainText("isolated from the render-only surface");
  await expect(page.getByRole("img", { name: "Fairy" })).toHaveCount(0);
  await expect(page.getByLabel("Fairy companion")).toHaveCount(0);
  expect(await overflow(page)).toEqual({ horizontal: 0, vertical: 0 });
  await page.screenshot({
    path: testInfo.outputPath("pet-input-reply.png"),
    omitBackground: true,
  });
  await context.close();
});

test("WebGL context loss falls back to Canvas and restores the liquid renderer", async ({
  browser,
}) => {
  const context = await browser.newContext({ viewport: { width: 640, height: 260 } });
  const page = await context.newPage();
  await page.goto("/?surface=pet-render");
  const renderer = page.getByTestId("presence-renderer");
  const canvas = page.locator("canvas.presence-webgl-canvas");
  await expect(renderer).toHaveAttribute("data-renderer", "liquid");
  await expect(renderer).toHaveAttribute("data-renderer-health", "running");

  const extensionAvailable = await canvas.evaluate((element) => {
    const context = (element as HTMLCanvasElement).getContext("webgl2");
    const extension = context?.getExtension("WEBGL_lose_context") ?? null;
    if (extension === null) return false;
    const scope = window as typeof window & {
      __fairyContextExtension?: WEBGL_lose_context;
    };
    scope.__fairyContextExtension = extension;
    extension.loseContext();
    return true;
  });
  expect(extensionAvailable).toBe(true);
  await expect(renderer).toHaveAttribute("data-renderer", "compatibility");
  await expect(renderer).toHaveAttribute("data-renderer-health", "fallback");
  await expect(renderer).toHaveAttribute("data-error-code", "WEBGL_CONTEXT_LOST");
  await expect(page.locator("canvas.presence-compatibility-canvas")).toHaveAttribute(
    "data-rendered",
    "true",
  );

  await page.evaluate(() => {
    const scope = window as typeof window & {
      __fairyContextExtension?: WEBGL_lose_context;
    };
    scope.__fairyContextExtension?.restoreContext();
  });
  await expect(renderer).toHaveAttribute("data-renderer", "liquid");
  await expect(renderer).toHaveAttribute("data-renderer-health", "running");
  await expect(canvas).toHaveAttribute("data-rendered", "true");
  await context.close();
});

async function publishProjection(page: Page, projection: PresenceProjectionState) {
  await page.evaluate((value) => {
    const channel = new BroadcastChannel("fairy.presence.v2");
    channel.postMessage({ kind: "presence.projection", projection: value });
    window.setTimeout(() => channel.close(), 100);
  }, projection);
}

function visiblePngPixels(image: PNG): number {
  let visible = 0;
  for (let index = 3; index < image.data.length; index += 4) {
    if (image.data[index] > 4) visible += 1;
  }
  return visible;
}

function alphaAt(image: PNG, x: number, y: number): number {
  return image.data[(y * image.width + x) * 4 + 3] ?? 0;
}

async function overflow(page: Page) {
  return page.evaluate(() => ({
    horizontal: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
    vertical: Math.max(0, document.documentElement.scrollHeight - window.innerHeight),
  }));
}
