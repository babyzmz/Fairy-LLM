import { expect, test, type Page } from "@playwright/test";

import type { PresenceProjectionState } from "../src/presence/domain/projection";

test("render surface stays transparent and contains no interactive controls", async ({
  browser,
}, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 640, height: 260 } });
  const page = await context.newPage();
  await page.goto("/?surface=pet-render");

  await expect(page.getByTestId("presence-render-surface")).toBeVisible();
  await expect(page.locator("canvas.fairy-canvas")).toHaveAttribute(
    "data-rendered",
    "true",
  );
  await expect(page.getByRole("button")).toHaveCount(0);
  expect(await visibleCanvasPixels(page)).toBeGreaterThan(250);
  expect(await overflow(page)).toEqual({ horizontal: 0, vertical: 0 });
  await page.screenshot({
    path: testInfo.outputPath("pet-render-compatibility.png"),
    omitBackground: true,
  });
  await context.close();
});

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

async function publishProjection(page: Page, projection: PresenceProjectionState) {
  await page.evaluate((value) => {
    const channel = new BroadcastChannel("fairy.presence.v2");
    channel.postMessage({ kind: "presence.projection", projection: value });
    window.setTimeout(() => channel.close(), 100);
  }, projection);
}

async function visibleCanvasPixels(page: Page): Promise<number> {
  return page.locator("canvas.fairy-canvas").evaluate((element) => {
    const canvas = element as HTMLCanvasElement;
    const pixels = canvas.getContext("2d")?.getImageData(0, 0, canvas.width, canvas.height).data;
    if (pixels === undefined) return 0;
    let visible = 0;
    for (let index = 3; index < pixels.length; index += 4) {
      if (pixels[index] > 4) visible += 1;
    }
    return visible;
  });
}

async function overflow(page: Page) {
  return page.evaluate(() => ({
    horizontal: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
    vertical: Math.max(0, document.documentElement.scrollHeight - window.innerHeight),
  }));
}
