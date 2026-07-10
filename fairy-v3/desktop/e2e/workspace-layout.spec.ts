import { expect, test } from "@playwright/test";

test("minimum desktop window keeps the workspace and composer inside the viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 880, height: 680 });
  await page.goto("/");

  const layout = await page.evaluate(() => ({
    viewportHeight: window.innerHeight,
    documentHeight: document.documentElement.scrollHeight,
    composerBottom: document.querySelector(".composer")?.getBoundingClientRect().bottom,
  }));

  expect(layout.documentHeight).toBeLessThanOrEqual(layout.viewportHeight);
  expect(layout.composerBottom).toBeLessThanOrEqual(layout.viewportHeight);
  await expect(page.getByLabel("Workspace telemetry")).toContainText("Standard");
  await expect(page.getByLabel("Workspace telemetry")).toContainText(/Core (ready|offline)/);
});
