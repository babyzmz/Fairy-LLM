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

test("Companion projects Cloud upload scope without starting a worker", async ({
  page,
}) => {
  await page.setViewportSize({ width: 640, height: 700 });
  await page.goto("/?surface=companion&companionBackend=cloud");

  await expect(page.getByRole("dialog", { name: "Realtime Companion Beta" })).toBeVisible();
  await expect(page.getByText("Cloud Live · GLM Realtime Flash")).toBeVisible();
  await expect(page.getByText(/Cloud Live sends only this session/)).toBeVisible();
  await page.getByRole("checkbox", {
    name: "Upload microphone for this Cloud session",
  }).check();
  await page.getByRole("checkbox", {
    name: "Upload only the selected game window",
  }).check();
  await expect(page.getByRole("button", { name: "Start Realtime" })).toBeEnabled();

  const calls = await fixtureCalls(page);
  expect(calls.some((call) => call.method === "realtime.backend.preview")).toBe(true);
  expect(calls.some((call) => call.method === "realtime_worker_start")).toBe(false);
  expect(await page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(640);
});

test("Companion projects Local processing scope without Cloud credential lookup", async ({
  page,
}) => {
  await page.setViewportSize({ width: 640, height: 700 });
  await page.goto("/?surface=companion&companionBackend=local");

  await expect(page.getByRole("dialog", { name: "Realtime Companion Beta" })).toBeVisible();
  await expect(page.getByText("Local · MiniCPM-o 4.5")).toBeVisible();
  await expect(page.getByText(/Local MiniCPM processes enabled microphone/)).toBeVisible();
  await expect(page.getByRole("checkbox", {
    name: "Use microphone for this Local session",
  })).toBeVisible();

  const calls = await fixtureCalls(page);
  expect(calls.some((call) => call.method === "realtime.backend.preview")).toBe(true);
  expect(calls.some((call) => call.method === "provider_realtime_status")).toBe(false);
  expect(calls.some((call) => call.method === "realtime_worker_start")).toBe(false);
});

for (const viewport of [
  { width: 880, height: 680 },
  { width: 640, height: 700 },
]) {
  test(`active Companion restores native activity policy at ${viewport.width}x${viewport.height}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.goto("/?surface=companion&companionActive=standby");

    await expect(
      page.getByRole("dialog", { name: "Realtime Companion Beta" }),
    ).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Profile" }))
      .toHaveValue("auto");
    await expect(page.getByRole("combobox", { name: "Intensity" }))
      .toHaveValue("standard");
    const policy = page.getByLabel("Realtime activity policy");
    await expect(policy.locator("div").filter({ hasText: /^EffectiveGame$/ }))
      .toBeVisible();
    await expect(policy.locator("div").filter({ hasText: /^BackendCloud$/ }))
      .toBeVisible();
    await expect(page.getByText(
      "Proactive Game comments wait at least 45 seconds; direct replies stay immediate.",
    )).toBeVisible();
    await expect(page.getByRole("button", { name: "Wake" })).toBeVisible();

    const bounds = await policy.evaluate((element) => {
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
      path: testInfo.outputPath(`realtime-policy-${viewport.width}.png`),
    });
  });
}

for (const viewport of [
  { width: 880, height: 680 },
  { width: 640, height: 700 },
]) {
  test(`Companion restores bounded Assistance at ${viewport.width}x${viewport.height}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.goto(
      "/?surface=companion&companionActive=active&companionAssistance=approval",
    );

    const assistance = page.getByLabel("Core assistance");
    await expect(assistance).toContainText("Find the current raid route");
    await expect(assistance).toContainText("Approval needed");
    await expect(page.getByRole("button", { name: /approve/i })).toHaveCount(0);
    await expect(page.getByText(/Full answer must not render/)).toHaveCount(0);
    await expect(page.getByText(/https?:\/\//)).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(viewport.width);
    await page.screenshot({
      path: testInfo.outputPath(`realtime-assistance-${viewport.width}.png`),
    });

    await page.getByRole("button", { name: "Open main chat" }).click();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(assistance).toContainText("Cancelled");

    const calls = await fixtureCalls(page);
    expect(calls.some((call) => call.method === "open_realtime_main_chat")).toBe(true);
    expect(calls.some((call) => call.method === "realtime.assistance.cancel")).toBe(true);
  });
}

test("Companion applies policy and standby recovery through native commands", async ({
  page,
}) => {
  await page.setViewportSize({ width: 640, height: 700 });
  await page.goto("/?surface=companion&companionActive=standby");

  await page.getByRole("combobox", { name: "Profile" })
    .selectOption("focus");
  await expect(page.getByText(
    "Proactive Focus comments wait at least 8 minutes; direct replies stay immediate.",
  )).toBeVisible();
  await page.getByRole("combobox", { name: "Intensity" })
    .selectOption("active");
  await expect(page.getByText(
    "Proactive Focus comments wait at least 3 minutes; direct replies stay immediate.",
  )).toBeVisible();
  await page.getByRole("button", { name: "Wake" }).click();
  await expect(page.getByText("Listening", { exact: true })).toBeVisible();

  const calls = await fixtureCalls(page);
  expect(calls.filter((call) => call.method === "realtime_worker_set_policy"))
    .toHaveLength(2);
  expect(calls.filter((call) => call.method === "realtime_worker_wake"))
    .toHaveLength(1);
});

test("Companion requires explicit extension after the duration limit", async ({
  page,
}) => {
  await page.setViewportSize({ width: 640, height: 700 });
  await page.goto("/?surface=companion&companionActive=duration");

  await expect(page.getByText(
    "Presence duration reached. Extend explicitly before waking Fairy.",
  )).toBeVisible();
  await expect(page.getByRole("button", { name: "Extend 30 min" }))
    .toBeVisible();
  await expect(page.getByRole("button", { name: "Wake" })).toHaveCount(0);
  await page.getByRole("button", { name: "Extend 30 min" }).click();
  await expect(page.getByText("Listening", { exact: true })).toBeVisible();

  const calls = await fixtureCalls(page);
  expect(calls.filter((call) => call.method === "realtime_worker_extend"))
    .toHaveLength(1);
});

test("active Companion disables decorative motion under reduced motion", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 640, height: 700 });
  await page.goto("/?surface=companion&companionActive=active");

  await expect(page.getByText("Listening", { exact: true })).toBeVisible();
  const animation = await page.locator(".realtime-pulse").evaluate(
    (element) => getComputedStyle(element).animationDuration,
  );
  expect(animation).toBe("0s");
  expect(await page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(640);
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
