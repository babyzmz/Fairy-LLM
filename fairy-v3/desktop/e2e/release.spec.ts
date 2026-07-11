import { expect, test } from "@playwright/test";

import { installWorkspaceFixture } from "./support/coreFixture";

test.beforeEach(async ({ page }) => {
  await installWorkspaceFixture(page);
});

test("release shell and durable event delivery stay inside performance budgets", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Task Timeline" })).toBeVisible();

  const shellInteractiveMs = await page.evaluate(() => {
    const releaseWindow = window as typeof window & { __FAIRY_BOOT_STARTED_AT__: number };
    return performance.now() - releaseWindow.__FAIRY_BOOT_STARTED_AT__;
  });
  expect(shellInteractiveMs).toBeLessThanOrEqual(1_500);

  const latencies: number[] = [];
  for (let index = 0; index < 20; index += 1) {
    const message = `Release event ${index + 1}`;
    const startedAt = await page.evaluate((eventMessage) => {
      const releaseWindow = window as typeof window & {
        __FAIRY_PUSH_EVENT__: (message: string) => number;
      };
      return releaseWindow.__FAIRY_PUSH_EVENT__(eventMessage);
    }, message);
    await expect(page.getByText(message)).toBeVisible();
    latencies.push(
      await page.evaluate((started) => performance.now() - started, startedAt),
    );
  }

  latencies.sort((left, right) => left - right);
  const p95 = latencies[Math.ceil(latencies.length * 0.95) - 1] ?? Number.POSITIVE_INFINITY;
  expect(p95).toBeLessThanOrEqual(100);
});

test("release workspace exposes project, chat, preview, provider, and developer flows", async ({
  page,
}) => {
  await page.goto("/");

  await expect(page.getByLabel("Workspace telemetry")).toContainText("Core ready");
  await expect(page.getByTitle("Task preview")).toBeVisible();
  await page.getByRole("button", { name: "Developer mode" }).click();
  await expect(page.getByLabel("Developer details")).toBeVisible();

  await page.getByRole("tab", { name: "Chat" }).click();
  await expect(page.getByText("Scratch chat is durable")).toBeVisible();
  await page.getByRole("button", { name: "Provider settings" }).click();
  await expect(page.getByLabel("Provider settings panel")).toContainText(
    "Credential configured",
  );
  await expect(page.locator("body")).not.toContainText("sk-or-v1-");
});
