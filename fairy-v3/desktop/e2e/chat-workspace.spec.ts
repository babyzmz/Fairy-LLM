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
    await page.getByRole("tab", { name: "Chat" }).click();

    await expect(page.getByRole("heading", { name: "Chat" })).toBeVisible();
    await expect(page.getByText("Scratch chat is durable")).toBeVisible();
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

test("reduced motion disables repeated chat activity animation", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 640, height: 700 });
  await page.goto("/");
  await page.getByRole("tab", { name: "Chat" }).click();

  const animation = await page.locator(".brand-button svg").evaluate((element) => {
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
  await page.getByRole("tab", { name: "Chat" }).click();
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
