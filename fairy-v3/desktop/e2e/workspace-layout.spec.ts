import { expect, test, type Page } from "@playwright/test";

import { installWorkspaceFixture, PREVIEW_URL } from "./support/coreFixture";

test.beforeEach(async ({ page }) => {
  await installWorkspaceFixture(page);
});

test("minimum desktop window renders the durable workspace without overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 880, height: 680 });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Task Timeline" })).toBeVisible();
  await expect(page.getByText("Preview is ready")).toBeVisible();
  await expect(page.getByTitle("Task preview")).toHaveAttribute("src", PREVIEW_URL);
  await expect(page.getByTitle("Task preview")).toHaveAttribute(
    "sandbox",
    "allow-forms allow-scripts",
  );
  await expect(page.getByText("Developer diagnostic")).toHaveCount(0);
  await expect(page.getByLabel("Workspace telemetry")).toContainText("standard");
  await expect(page.getByLabel("Workspace telemetry")).toContainText("Core ready");

  const layout = await measureLayout(page);
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.documentHeight).toBeLessThanOrEqual(layout.viewportHeight);
  expect(layout.composerBottom).toBeLessThanOrEqual(layout.viewportHeight);
  expect(layout.contextBottom).toBeLessThanOrEqual(layout.composerTop);
});

test("narrow workspace remains usable and reduced motion disables repeated HUD motion", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 640, height: 700 });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Task Timeline" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Preview" })).toBeVisible();

  const acceptButton = page.getByRole("button", { name: "Use this version" });
  await acceptButton.scrollIntoViewIfNeeded();
  await expect(acceptButton).toBeVisible();

  const layout = await measureLayout(page);
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.composerBottom).toBeLessThanOrEqual(layout.viewportHeight);

  const decisionBottom = await acceptButton.evaluate(
    (element) => element.getBoundingClientRect().bottom,
  );
  expect(decisionBottom).toBeLessThanOrEqual(layout.composerTop);

  const motion = await page.locator(".scan-line").evaluate((element) => {
    const style = getComputedStyle(element);
    const duration = style.animationDuration;
    return {
      durationMs: duration.endsWith("ms")
        ? Number.parseFloat(duration)
        : Number.parseFloat(duration) * 1_000,
      iterations: style.animationIterationCount,
    };
  });
  expect(motion.durationMs).toBeLessThanOrEqual(0.001);
  expect(motion.iterations).toBe("1");
});

async function measureLayout(page: Page) {
  return page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    viewportHeight: window.innerHeight,
    documentWidth: document.documentElement.scrollWidth,
    documentHeight: document.documentElement.scrollHeight,
    contextBottom:
      document.querySelector(".context-bar")?.getBoundingClientRect().bottom ?? 0,
    composerTop:
      document.querySelector(".chat-composer")?.getBoundingClientRect().top ?? 0,
    composerBottom:
      document.querySelector(".chat-composer")?.getBoundingClientRect().bottom ?? 0,
  }));
}
