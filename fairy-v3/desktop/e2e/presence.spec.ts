import { expect, test, type Page } from "@playwright/test";
import type { PresenceProjectionState } from "../src/presence/projection";

const NOW = Date.now();
const READY_PROJECTION: PresenceProjectionState = {
  activity: "ready",
  work_state: "ready",
  status_text: "Ready for review",
  last_cursor: 8,
  last_event_id: "event-8",
  updated_at_ms: NOW,
  recent_activity_ms: [NOW],
  notice: null,
  reply: null,
  speaking: false,
};

for (const deviceScaleFactor of [1, 1.25, 2]) {
  test(`Fairy core renders inside the collapsed window at ${deviceScaleFactor * 100}% scale`, async ({
    browser,
  }, testInfo) => {
    const context = await browser.newContext({
      deviceScaleFactor,
      viewport: { width: 176, height: 176 },
    });
    const page = await context.newPage();
    await page.goto("/?surface=presence");

    const canvas = page.getByRole("img", { name: "Fairy" });
    await expect(canvas).toBeVisible();
    await expect(canvas).toHaveAttribute("data-rendered", "true");
    expect(await visibleCanvasPixels(page)).toBeGreaterThan(250);
    expect(await overflow(page)).toEqual({ horizontal: 0, vertical: 0 });
    await page.screenshot({
      path: testInfo.outputPath(`fairy-core-${deviceScaleFactor * 100}.png`),
      omitBackground: true,
    });
    await context.close();
  });
}

test("Fairy quick chat, cards, and context menu stay inside the expanded surface", async ({
  browser,
}, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 420, height: 360 } });
  const page = await context.newPage();
  await page.goto("/?surface=presence");
  await installRequestCapture(page);

  await page.getByRole("button", { name: "Fairy companion" }).click();
  const input = page.getByRole("textbox", { name: "Quick message to Fairy" });
  await expect(input).toBeVisible();
  await input.fill("Summarize today");
  await input.press("Enter");
  await expect.poll(() => capturedRequests(page)).toContainEqual(
    expect.objectContaining({
      kind: "chat.send",
      submission_id: expect.any(String),
      text: "Summarize today",
    }),
  );

  await publishProjection(page, {
    ...READY_PROJECTION,
    work_state: "streaming",
    status_text: "Writing the reply",
    reply: {
      id: "reply-8",
      text: "I am preparing a concise summary now.",
      kind: "scratch",
      streaming: true,
    },
  });
  await expect(page.getByRole("status")).toContainText("I am preparing");
  await page.getByRole("button", { name: "Close reply" }).click();

  await publishProjection(page, {
    ...READY_PROJECTION,
    activity: "needs_attention",
    work_state: "awaiting_confirmation",
    status_text: "Waiting for your decision",
    notice: {
      id: "notice-9",
      tone: "critical",
      text: "An approval needs your decision",
    },
  });
  await expect(page.getByRole("alert")).toContainText("An approval needs your decision");
  await expect(page.getByRole("button", { name: "Review in Fairy" })).toBeVisible();

  await page.getByTestId("presence-surface").click({ button: "right" });
  await expect(page.getByRole("menu", { name: "Fairy menu" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "New chat" })).toBeVisible();
  await expect(page.getByRole("menuitemcheckbox", { name: "Auto-play replies" })).toHaveAttribute("aria-checked", "true");
  expect(await overflow(page)).toEqual({ horizontal: 0, vertical: 0 });
  await page.screenshot({ path: testInfo.outputPath("fairy-expanded-menu.png"), omitBackground: true });
  await context.close();
});

test("Fairy renders a stable reduced-motion frame", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 176, height: 176 }, reducedMotion: "reduce" });
  const page = await context.newPage();
  await page.goto("/?surface=presence");
  await expect(page.getByTestId("presence-surface")).toHaveAttribute("data-reduced-motion", "true");
  await expect(page.getByRole("img", { name: "Fairy" })).toHaveAttribute("data-rendered", "true");
  expect(await visibleCanvasPixels(page)).toBeGreaterThan(250);
  await context.close();
});

async function publishProjection(page: Page, projection: PresenceProjectionState) {
  await page.evaluate((value) => {
    const channel = new BroadcastChannel("fairy.presence.v2");
    channel.postMessage({ kind: "presence.projection", projection: value });
    window.setTimeout(() => channel.close(), 100);
  }, projection);
}

async function installRequestCapture(page: Page) {
  await page.evaluate(() => {
    const scope = window as typeof window & {
      __fairyRequests?: unknown[];
      __fairyRequestChannel?: BroadcastChannel;
    };
    scope.__fairyRequests = [];
    scope.__fairyRequestChannel = new BroadcastChannel("fairy.presence.v2");
    scope.__fairyRequestChannel.onmessage = (event) => {
      if (event.data?.kind === "presence.request") {
        scope.__fairyRequests?.push(event.data.request);
      }
    };
  });
}

async function capturedRequests(page: Page): Promise<unknown[]> {
  return page.evaluate(() => {
    const scope = window as typeof window & { __fairyRequests?: unknown[] };
    return scope.__fairyRequests ?? [];
  });
}

async function visibleCanvasPixels(page: Page): Promise<number> {
  return page.getByRole("img", { name: "Fairy" }).evaluate((element) => {
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
