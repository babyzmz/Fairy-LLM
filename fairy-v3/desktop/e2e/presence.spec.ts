import { expect, test } from "@playwright/test";

const READY_PROJECTION = {
  activity: "ready",
  status_text: "Ready for review",
  last_cursor: 8,
  last_event_id: "event-8",
  updated_at_ms: Date.now(),
  recent_activity_ms: [Date.now()],
  notice: { id: "notice-8", tone: "info", text: "Preview is ready" },
  reply: null,
};

for (const deviceScaleFactor of [1, 1.25, 2]) {
  test(`Presence remains contained at ${deviceScaleFactor * 100}% scale`, async ({
    browser,
  }, testInfo) => {
    const context = await browser.newContext({
      deviceScaleFactor,
      viewport: { width: 196, height: 240 },
    });
    const page = await context.newPage();
    await page.goto("/?surface=presence");

    await expect(page.getByRole("img", { name: "Fairy" })).toBeVisible();
    await publishProjection(page, READY_PROJECTION);
    await expect(page.getByRole("alert")).toHaveText("Preview is ready");

    await page.getByTestId("presence-surface").hover();
    await expect(page.getByRole("toolbar", { name: "Presence controls" })).toBeVisible();
    await page.getByRole("button", { name: "Enable quiet mode" }).click();
    await expect(page.getByTestId("presence-surface")).toHaveAttribute(
      "data-quiet",
      "true",
    );
    await expect(page.getByRole("status")).toHaveCount(0);
    await expect(page.getByText("Ready for review", { exact: true })).toBeVisible();

    const statusBox = await page.locator(".presence-status").boundingBox();
    expect(statusBox).not.toBeNull();
    if (statusBox !== null) {
      expect(statusBox.y).toBeGreaterThanOrEqual(0);
      expect(statusBox.y + statusBox.height).toBeLessThanOrEqual(240);
    }

    const overflow = await page.evaluate(() => ({
      horizontal: document.documentElement.scrollWidth - window.innerWidth,
      vertical: document.documentElement.scrollHeight - window.innerHeight,
    }));
    expect(overflow.horizontal).toBeLessThanOrEqual(0);
    expect(overflow.vertical).toBeLessThanOrEqual(0);
    await page.screenshot({
      path: testInfo.outputPath(`presence-${deviceScaleFactor * 100}.png`),
    });
    await context.close();
  });
}

test("Presence dismisses projections locally and Guide stays display-only", async ({
  browser,
}) => {
  const presenceContext = await browser.newContext({
    viewport: { width: 196, height: 240 },
  });
  const presence = await presenceContext.newPage();
  await presence.goto("/?surface=presence");
  await expect(presence.getByRole("img", { name: "Fairy" })).toBeVisible();
  await publishProjection(presence, {
    ...READY_PROJECTION,
    notice: null,
    reply: { id: "reply-8", text: "The task update is ready" },
    updated_at_ms: Date.now(),
    recent_activity_ms: [Date.now()],
  });
  await presence.getByRole("button", { name: "Close reply" }).click();
  await publishProjection(presence, {
    ...READY_PROJECTION,
    last_cursor: 9,
    last_event_id: "event-9",
    notice: { id: "notice-9", tone: "info", text: "Preview is ready" },
    reply: null,
    updated_at_ms: Date.now(),
    recent_activity_ms: [Date.now()],
  });
  await presence.getByRole("button", { name: "Dismiss notice" }).click();
  await expect(presence.getByRole("status")).toHaveCount(0);
  await expect(presence.getByRole("alert")).toHaveCount(0);

  const guideContext = await browser.newContext({
    viewport: { width: 360, height: 66 },
  });
  const guide = await guideContext.newPage();
  await guide.goto("/?surface=guide");
  await expect(guide.getByText("Fairy is present")).toBeVisible();
  await publishProjection(guide, {
    ...READY_PROJECTION,
    notice: null,
    reply: null,
    updated_at_ms: Date.now(),
    recent_activity_ms: [Date.now()],
  });
  await expect(guide.getByText("Ready for review")).toBeVisible();
  await expect(guide.getByRole("button")).toHaveCount(0);
  await expect(guide.locator(".guide-window")).toHaveCSS("pointer-events", "none");

  await guideContext.close();
  await presenceContext.close();
});

async function publishProjection(
  page: import("@playwright/test").Page,
  projection: typeof READY_PROJECTION,
) {
  await page.evaluate((value) => {
    const channel = new BroadcastChannel("fairy.presence.v1");
    channel.postMessage({ kind: "presence.projection", projection: value });
    window.setTimeout(() => channel.close(), 100);
  }, projection);
}
