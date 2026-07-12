import { expect, test } from "@playwright/test";

import { installWorkspaceFixture } from "./support/coreFixture";

test.beforeEach(async ({ page }) => {
  await installWorkspaceFixture(page);
  await page.goto("/?surface=settings");
  await expect(page.getByRole("heading", { name: "General" })).toBeVisible();
});

test("reviews governed skills and accepts an MCP schema with conservative defaults", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1000, height: 760 });
  await page.getByRole("button", { name: /Skills \/ MCP/ }).click();
  await expect(page.getByText("Fairy Docs")).toBeVisible();

  await page.getByRole("tab", { name: /MCP/u }).click();
  await expect(page.getByText("Document server")).toBeVisible();
  await expect(page.getByLabel("Effect")).toHaveValue("execute");
  await expect(page.getByLabel("Risk")).toHaveValue("high");
  await expect(page.getByLabel("Approval")).toHaveValue("always");
  await page.screenshot({ path: testInfo.outputPath("extensions-desktop.png") });
  await page.getByRole("button", { name: "Accept reviewed schema" }).click();

  const panel = page.locator(".settings-category");
  await expect(panel.getByRole("checkbox", { name: "Enable Document server" })).toBeChecked();
  await expect(panel.getByText("Pending tool review")).toHaveCount(0);
  const calls = await page.evaluate(() =>
    (window as unknown as { __FAIRY_FIXTURE_CALLS__: Array<{ method: string }> })
      .__FAIRY_FIXTURE_CALLS__.map((call) => call.method),
  );
  expect(calls).toContain("mcp.servers.accept");

  await page.setViewportSize({ width: 720, height: 700 });
  const bounds = await panel.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      left: rect.left,
      right: rect.right,
      viewport: document.documentElement.clientWidth,
      overflows: element.scrollWidth > element.clientWidth,
    };
  });
  expect(bounds.left).toBeGreaterThanOrEqual(0);
  expect(bounds.right).toBeLessThanOrEqual(bounds.viewport);
  expect(bounds.overflows).toBe(false);
  await page.screenshot({ path: testInfo.outputPath("extensions-narrow.png") });
});
