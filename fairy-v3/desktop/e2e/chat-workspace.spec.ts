import { expect, test } from "@playwright/test";

import { installWorkspaceFixture } from "./support/coreFixture";

test.beforeEach(async ({ page }) => {
  await installWorkspaceFixture(page);
});

for (const viewport of [
  { width: 880, height: 680 },
  { width: 640, height: 700 },
]) {
  test(`scratch chat remains usable at ${viewport.width}x${viewport.height}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.goto("/");
    await openScratchChat(page);

    await expect(page.getByRole("heading", { name: "Chat" })).toBeVisible();
    await expect(page.getByText("Scratch chat is durable")).toBeVisible();
    await expect(page.locator("body")).not.toContainText("fixture provider payload");
    const composer = page.getByLabel("Message Fairy");
    await composer.fill("Check Sydney weather");
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(composer).toHaveValue("");

    const bounds = await page.evaluate(() => ({
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      documentWidth: document.documentElement.scrollWidth,
      documentHeight: document.documentElement.scrollHeight,
      composerBottom:
        document.querySelector(".chat-composer")?.getBoundingClientRect().bottom ?? 0,
    }));
    expect(bounds.documentWidth).toBeLessThanOrEqual(bounds.viewportWidth);
    expect(bounds.documentHeight).toBeLessThanOrEqual(bounds.viewportHeight);
    expect(bounds.composerBottom).toBeLessThanOrEqual(bounds.viewportHeight);
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await page.screenshot({
      path: testInfo.outputPath(`chat-${viewport.width}x${viewport.height}.png`),
      fullPage: true,
    });
  });
}

test("tool protocol is visible only in developer mode", async ({ page }) => {
  await page.goto("/");
  await openScratchChat(page);

  await expect(page.locator("body")).not.toContainText("fixture provider payload");
  await page.getByRole("button", { name: "Provider settings" }).click();
  await page.getByRole("checkbox", { name: "Developer mode" }).check();
  await expect(page.getByText(/fixture provider payload/)).toBeVisible();
});

test("a user message appears before Core task creation returns", async ({ page }) => {
  await page.goto("/");
  await openScratchChat(page);
  const composer = page.getByLabel("Message Fairy");
  await composer.fill("Visible before Core confirms");
  await page.getByRole("button", { name: "Send message" }).click();

  await expect(
    page.getByLabel("Conversation messages").getByText("Visible before Core confirms"),
  ).toBeVisible();
  await expect(page.getByText("Sending", { exact: true })).toBeVisible();
  await expect(page.getByText("Fixture streamed response completed")).toBeVisible();
  await expect(page.getByRole("region", { name: "Fairy activity" })).toContainText(
    "Response ready",
  );
});

test("reduced motion disables repeated chat activity animation", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 640, height: 700 });
  await page.goto("/");
  await openScratchChat(page);

  const animation = await page.locator(".history-mark").evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      duration: style.animationDuration,
      iterations: style.animationIterationCount,
    };
  });
  expect(animation.iterations === "1" || animation.duration === "0s").toBe(true);
});

test("provider settings stay inside the narrow workspace and expose no secret", async ({
  page,
}) => {
  await page.setViewportSize({ width: 640, height: 700 });
  await page.goto("/");
  await openScratchChat(page);
  await page.getByRole("button", { name: "Provider settings" }).click();

  const panel = page.getByLabel("Provider settings panel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("Credential configured");
  await expect(page.locator("body")).not.toContainText("sk-or-v1-");
  const bounds = await panel.evaluate((element) => {
    const rectangle = element.getBoundingClientRect();
    return {
      left: rectangle.left,
      right: rectangle.right,
      top: rectangle.top,
      bottom: rectangle.bottom,
      width: window.innerWidth,
      height: window.innerHeight,
    };
  });
  expect(bounds.left).toBeGreaterThanOrEqual(0);
  expect(bounds.top).toBeGreaterThanOrEqual(0);
  expect(bounds.right).toBeLessThanOrEqual(bounds.width);
  expect(bounds.bottom).toBeLessThanOrEqual(bounds.height);
});

test("approved assistant tools resume the durable turn exactly once", async ({ page }) => {
  await page.setViewportSize({ width: 880, height: 680 });
  await page.goto("/");
  await openScratchChat(page);

  const composer = page.getByLabel("Message Fairy");
  await composer.fill("Request a governed notification");
  await page.getByRole("button", { name: "Send message" }).click();

  const approval = page.getByRole("group", { name: "Pending approval" });
  await expect(approval).toContainText("Allow Fairy to send a notification");
  await expect(page.getByText("Approval required", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();

  await expect(page.getByText("Notification completed after approval")).toBeVisible();
  await expect(approval).not.toBeVisible();

  const calls = await page.evaluate(() =>
    (
      window as unknown as {
        __FAIRY_FIXTURE_CALLS__: Array<{
          method: string;
          params: Record<string, unknown>;
        }>;
      }
    ).__FAIRY_FIXTURE_CALLS__,
  );
  const decisions = calls.filter((call) => call.method === "approvals.decide");
  expect(decisions).toHaveLength(1);
  expect(decisions[0]?.params).toEqual({
    approval_id: "0198f4de-0114-7000-8000-000000000017",
    approved: true,
  });
  expect(calls.filter((call) => call.method === "assistant.turns.start")).toHaveLength(2);
});

async function openScratchChat(page: import("@playwright/test").Page) {
  await page
    .getByLabel("History navigation")
    .getByRole("button", { name: "Scratch chat", exact: true })
    .click();
}
