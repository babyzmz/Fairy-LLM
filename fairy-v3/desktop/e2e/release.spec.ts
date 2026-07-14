import { expect, test } from "@playwright/test";

import { installWorkspaceFixture } from "./support/coreFixture";

test.beforeEach(async ({ page }) => {
  await installWorkspaceFixture(page);
});

test("@performance release shell and durable event delivery stay inside performance budgets", async ({
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
    await page.waitForFunction(
      (eventMessage) => document.body.innerText.includes(eventMessage),
      message,
      { polling: "raf" },
    );
    latencies.push(
      await page.evaluate((started) => performance.now() - started, startedAt),
    );
  }

  latencies.sort((left, right) => left - right);
  const p95 = latencies[Math.ceil(latencies.length * 0.95) - 1] ?? Number.POSITIVE_INFINITY;
  expect(p95).toBeLessThanOrEqual(100);
});

test("release workspace exposes project, chat, preview, settings, and developer flows", async ({
  page,
}) => {
  await page.goto("/");

  await expect(page.getByLabel("Workspace status")).toContainText("Core ready");
  await expect(page.getByLabel("Workspace status")).toContainText("LOCAL ONLY");
  await expect(page.getByTitle("Task preview")).toBeVisible();
  await page.getByRole("button", { name: "New project" }).click();
  await expect(page.getByRole("complementary", { name: "Project manager" })).toBeVisible();
  await page.getByRole("button", { name: "Choose project folder" }).click();
  await expect(page.getByLabel("Folder path")).toHaveValue("C:\\Projects\\fixture");
  await page.getByRole("button", { name: "Close project manager" }).click();
  await page.getByRole("button", { name: "Open settings" }).click();
  await page.goto("/?surface=settings");
  await page.getByRole("button", { name: /Advanced/ }).click();
  await page.getByRole("checkbox", { name: "Developer mode" }).check();
  await page.getByRole("button", { name: /Models/ }).click();
  await expect(page.getByRole("heading", { name: "Models" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("sk-or-v1-");
  await page.goto("/");
  await expect(page.getByLabel("Developer details")).toBeVisible();

  await page
    .getByLabel("History navigation")
    .getByRole("button", { name: "Scratch chat", exact: true })
    .click();
  await expect(page.getByText("Scratch chat is durable")).toBeVisible();
  await expect(page.getByRole("button", { name: "Open settings" })).toBeVisible();
});

test("permission changes persist through Core without client sandbox authority", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByLabel("History navigation")
    .getByRole("button", { name: "Scratch chat", exact: true })
    .click();
  await page.getByLabel("Message Fairy").fill("/permission autonomous");
  await page.getByRole("button", { name: "Send message" }).click();

  await expect(page.getByLabel("Workspace status")).toContainText("autonomous");
  const update = await page.evaluate(() => {
    const fixtureWindow = window as typeof window & {
      __FAIRY_FIXTURE_CALLS__: Array<{
        method: string;
        params: Record<string, unknown>;
      }>;
    };
    return fixtureWindow.__FAIRY_FIXTURE_CALLS__.findLast(
      (call) => call.method === "permissions.update",
    );
  });

  expect(update?.params).toMatchObject({
    profile: "autonomous",
    expected_revision: 0,
  });
  expect(update?.params).not.toHaveProperty("sandbox_healthy");
});
