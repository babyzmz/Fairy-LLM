import { expect, test } from "@playwright/test";

import { installWorkspaceFixture } from "./support/coreFixture";
import { openInternalSettings } from "./support/settings";

test.beforeEach(async ({ page }) => {
  await installWorkspaceFixture(page);
});

for (const viewport of [
  { width: 880, height: 680 },
  { width: 640, height: 700 },
]) {
  test(`local readiness stays contained and fail-closed at ${viewport.width}x${viewport.height}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await openInternalSettings(page, {
      path: "/?localReadiness=runtime-missing",
      category: /Voice/,
    });

    const card = page.locator(".realtime-readiness-card");
    await expect(card).toBeVisible();
    await expect(
      page.getByRole("status", { name: "Local readiness status" }),
    ).toHaveText("Runtime required");
    await expect(page.getByRole("option", { name: "Local MiniCPM-o 4.5 Beta" }))
      .toHaveAttribute("disabled", "");
    const bounds = await card.evaluate((element) => {
      const rectangle = element.getBoundingClientRect();
      return {
        left: rectangle.left,
        right: rectangle.right,
        viewport: innerWidth,
        documentWidth: document.documentElement.scrollWidth,
      };
    });
    expect(bounds.left).toBeGreaterThanOrEqual(0);
    expect(bounds.right).toBeLessThanOrEqual(bounds.viewport);
    expect(bounds.documentWidth).toBeLessThanOrEqual(bounds.viewport);

    await page.screenshot({
      path: testInfo.outputPath(`local-readiness-${viewport.width}.png`),
    });
  });
}

for (const scenario of [
  { query: "unsupported", label: "Unavailable" },
  { query: "installable", label: "Install model" },
  { query: "runtime-missing", label: "Runtime required" },
  { query: "temporary", label: "Temporarily unavailable" },
  { query: "ready", label: "Ready" },
]) {
  test(`local readiness renders the ${scenario.query} projection`, async ({ page }) => {
    await page.setViewportSize({ width: 880, height: 680 });
    await openInternalSettings(page, {
      path: `/?localReadiness=${scenario.query}`,
      category: /Voice/,
    });
    await expect(
      page.getByRole("status", { name: "Local readiness status" }),
    ).toHaveText(scenario.label);

    const backend = page.getByRole("combobox", { name: "Backend" });
    await backend.selectOption("cloud_live");
    await expect(backend).toHaveValue("cloud_live");
    const localOption = page.getByRole("option", {
      name: "Local MiniCPM-o 4.5 Beta",
    });
    if (scenario.query === "ready") {
      await expect(localOption).not.toHaveAttribute("disabled");
    } else {
      await expect(localOption).toHaveAttribute("disabled", "");
    }
  });
}

test("active model progress is numeric, cancellable, and reduced-motion safe", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 640, height: 700 });
  await openInternalSettings(page, {
    path: "/?localReadiness=downloading",
    category: /Voice/,
  });

  const progress = page.getByRole("progressbar", {
    name: "Local model operation progress",
  });
  await expect(progress).toHaveAttribute("aria-valuenow", "42");
  const transition = await progress.locator("span").evaluate(
    (element) => getComputedStyle(element).transitionDuration,
  );
  expect(transition).toBe("0s");
  await page.getByRole("button", { name: "Cancel" }).click();
  const calls = await fixtureCalls(page);
  expect(calls.filter((call) => call.method === "omni_model_install_cancel"))
    .toHaveLength(1);
});

test("opening Voice readiness starts no model, Voice, Realtime, or Omni worker", async ({
  page,
}) => {
  await openInternalSettings(page, {
    path: "/?localReadiness=installable",
    category: /Voice/,
  });
  await expect(
    page.getByRole("status", { name: "Local readiness status" }),
  ).toHaveText("Install model");

  const calls = await fixtureCalls(page);
  expect(calls.some((call) => call.method === "realtime.local.readiness.get")).toBe(true);
  expect(calls.some((call) => [
    "omni_model_install_start",
    "omni_model_verify",
    "realtime_worker_start",
    "voice_session_start",
  ].includes(call.method))).toBe(false);
});

async function fixtureCalls(page: import("@playwright/test").Page) {
  return page.evaluate(() => (
    window as typeof window & {
      __FAIRY_FIXTURE_CALLS__: Array<{
        method: string;
        params: Record<string, unknown>;
      }>;
    }
  ).__FAIRY_FIXTURE_CALLS__);
}
