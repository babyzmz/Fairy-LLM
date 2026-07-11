import { expect, test } from "@playwright/test";

import { installWorkspaceFixture } from "./support/coreFixture";

test.beforeEach(async ({ page }) => {
  await installWorkspaceFixture(page);
});

test("sandbox health disables only capabilities that require it", async ({ page }) => {
  await page.goto("/?sandboxUnavailable=1");
  await page.getByRole("button", { name: "Execution controls" }).click();

  await expect(page.getByText("Sandbox unavailable", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "run.sandboxed" })).toBeDisabled();
  await expect(page.getByRole("checkbox", { name: "web.search" })).toBeEnabled();
});

test("permission conflicts reload the latest Core revision", async ({ page }) => {
  await page.goto("/?permissionConflict=1");
  await page.getByRole("button", { name: "Execution controls" }).click();
  await page.getByText("Autonomous", { exact: true }).click();

  await expect(
    page.getByText(
      "Permissions changed on another device. Latest settings loaded; review and retry.",
    ),
  ).toBeVisible();
  await expect(page.getByLabel("Workspace telemetry")).toContainText("observe");

  const updates = await fixtureCalls(page, "permissions.update");
  expect(updates).toHaveLength(1);
  expect(updates[0]?.params).toMatchObject({
    profile: "autonomous",
    expected_revision: 0,
  });
});

test("durable progress and recovery events remain user visible", async ({ page }) => {
  await page.goto("/?executionRecovery=1");

  await expect(page.getByText("Sandbox command running")).toBeVisible();
  await expect(page.getByText("Runtime recovered after worker interruption")).toBeVisible();
});

interface FixtureCall {
  method: string;
  params: Record<string, unknown>;
}

async function fixtureCalls(
  page: import("@playwright/test").Page,
  method: string,
): Promise<FixtureCall[]> {
  return page.evaluate((targetMethod) => {
    const fixture = window as unknown as { __FAIRY_FIXTURE_CALLS__: FixtureCall[] };
    return fixture.__FAIRY_FIXTURE_CALLS__.filter(
      (call) => call.method === targetMethod,
    );
  }, method);
}
