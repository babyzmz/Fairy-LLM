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

test("developer mode shows bounded trace diagnostics without raw tool protocol", async ({ page }) => {
  await enableDeveloperMode(page);
  await openScratchChat(page);

  const composer = page.getByLabel("Message Fairy");
  await composer.fill("Inspect the durable work trace");
  await page.getByRole("button", { name: "Send message" }).click();
  const workChain = page.getByRole("region", { name: "Fairy work chain" });
  await workChain.getByRole("button", { name: "Fairy activity" }).click();
  await expect(workChain.getByText(/model moonshotai\/kimi-k2\.7-code/)).toBeVisible();
  await expect(page.locator("body")).not.toContainText("fixture provider payload");
});

test("ordinary chat exposes the shared workspace file inspector", async ({ page }) => {
  await page.setViewportSize({ width: 880, height: 680 });
  await page.goto("/");
  await openScratchChat(page);

  await expect(page.getByLabel("Workspace inspector")).toBeVisible();
  await page.getByRole("tab", { name: /Files/ }).click();
  await page.getByRole("button", { name: "main.ts" }).click();
  await expect(page.getByText("console.log('Fairy');")).toBeVisible();
  await expect(page.getByRole("button", { name: "Use this version" })).toHaveCount(0);
});

test("model selector stays inside the window and persists the global choice", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 880, height: 680 });
  await page.goto("/");
  await openScratchChat(page);

  await page.getByRole("button", { name: "Model: Auto" }).click();
  await expect(page.getByText("General & reasoning")).toBeVisible();
  await expect(page.getByText("Professional generation")).toBeVisible();
  const bounds = await page.locator(".model-selector-popover").evaluate((element) => {
    const rectangle = element.getBoundingClientRect();
    return {
      left: rectangle.left,
      top: rectangle.top,
      right: rectangle.right,
      bottom: rectangle.bottom,
      viewportWidth: innerWidth,
      viewportHeight: innerHeight,
    };
  });
  expect(bounds.left).toBeGreaterThanOrEqual(8);
  expect(bounds.top).toBeGreaterThanOrEqual(8);
  expect(bounds.right).toBeLessThanOrEqual(bounds.viewportWidth - 8);
  expect(bounds.bottom).toBeLessThanOrEqual(bounds.viewportHeight - 8);

  await page.getByRole("menuitemradio", { name: /Kimi K2.7 Code/ }).click();
  await expect(page.getByRole("button", { name: "Model: Kimi K2.7 Code" })).toBeVisible();
  await expect(page.locator(".model-selector-popover")).toBeHidden();
  const calls = await page.evaluate(() => window.__FAIRY_FIXTURE_CALLS__);
  expect(calls.some((call) => call.method === "models.selection.update" &&
    call.params.model_id === "moonshotai/kimi-k2.7-code")).toBe(true);
  await page.getByRole("button", { name: "Model: Kimi K2.7 Code" }).click();
  await expect(page.locator(".model-selector-popover")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("model-selector.png") });
});

test("a user message appears before Core task creation returns", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 640, height: 700 });
  await page.goto("/");
  await openScratchChat(page);
  const composer = page.getByLabel("Message Fairy");
  await composer.fill("Visible before Core confirms");
  await page.getByRole("button", { name: "Send message" }).click();

  await expect(composer).toHaveValue("");
  await expect(
    page.getByLabel("Conversation messages").getByText("Visible before Core confirms"),
  ).toBeVisible();
  await expect(page.getByText("Sending", { exact: true })).toBeVisible();
  await expect(page.getByText("Fixture streamed response completed")).toBeVisible();
  const workChain = page.getByRole("region", { name: "Fairy work chain" });
  await expect(workChain).toContainText(
    "Response ready",
  );
  await workChain.getByRole("button", { name: "Fairy activity" }).click();
  await expect(workChain.getByText("Kimi implementation")).toBeVisible();
  await expect(workChain).not.toContainText("moonshotai/kimi-k2.7-code");
  await page.waitForTimeout(250);
  const workChainBounds = await workChain.evaluate((element) => {
    const rectangle = element.getBoundingClientRect();
    return { left: rectangle.left, right: rectangle.right, width: innerWidth };
  });
  expect(workChainBounds.left).toBeGreaterThanOrEqual(0);
  expect(workChainBounds.right).toBeLessThanOrEqual(workChainBounds.width);
  await page.screenshot({ path: testInfo.outputPath("work-chain-expanded.png") });
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

test("model settings stay inside the narrow settings window and expose no secret", async ({
  page,
}) => {
  await page.setViewportSize({ width: 760, height: 700 });
  await page.goto("/?surface=settings");
  await page.getByRole("button", { name: /Models/ }).click();

  const panel = page.locator(".settings-category");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("Credential protected by Windows");
  await expect(page.locator("body")).not.toContainText("sk-or-v1-");
  const bounds = await page.locator(".settings-content").evaluate((element) => {
    const rectangle = element.getBoundingClientRect();
    return {
      left: rectangle.left,
      right: rectangle.right,
      top: rectangle.top,
      bottom: rectangle.bottom,
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      overflowY: getComputedStyle(element).overflowY,
      width: window.innerWidth,
      height: window.innerHeight,
      documentWidth: document.documentElement.scrollWidth,
      documentHeight: document.documentElement.scrollHeight,
    };
  });
  expect(bounds.left).toBeGreaterThanOrEqual(0);
  expect(bounds.top).toBeGreaterThanOrEqual(0);
  expect(bounds.right).toBeLessThanOrEqual(bounds.width);
  expect(bounds.bottom).toBeLessThanOrEqual(bounds.height);
  expect(bounds.clientHeight).toBeLessThanOrEqual(bounds.height);
  expect(bounds.scrollHeight).toBeGreaterThan(bounds.clientHeight);
  expect(bounds.overflowY).toBe("auto");
  expect(bounds.documentWidth).toBeLessThanOrEqual(bounds.width);
  expect(bounds.documentHeight).toBeLessThanOrEqual(bounds.height);
  await page.getByRole("button", { name: "Save and connect" }).scrollIntoViewIfNeeded();
  await expect(page.getByRole("button", { name: "Save and connect" })).toBeVisible();
});

test("Liquid Glass pet settings persist without overflowing the settings window", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 980, height: 720 });
  await page.goto("/?surface=settings");
  await page.getByRole("button", { name: /^Pet/ }).click();

  const renderer = page.getByRole("combobox", { name: "Renderer" });
  await renderer.selectOption("compatibility");
  await expect(renderer).toHaveValue("compatibility");
  await page.getByRole("checkbox", { name: "Do not disturb" }).check();
  await expect(page.getByRole("checkbox", { name: "Do not disturb" })).toBeChecked();
  await expect(
    page.getByRole("checkbox", { name: /^Ambient dialogue Use the reviewed/ }),
  ).toBeChecked();
  const ambientVoice = page.getByRole("checkbox", { name: /^Speak ambient dialogue/ });
  await expect(ambientVoice).not.toBeChecked();
  await ambientVoice.check();
  await expect(ambientVoice).toBeChecked();
  await expect(page.getByRole("slider", { name: "Size" })).toHaveAttribute("min", "75");
  await expect(page.getByRole("slider", { name: "Opacity" })).toHaveAttribute("max", "100");
  expect(await page.evaluate(() => ({
    horizontal: Math.max(0, document.documentElement.scrollWidth - innerWidth),
    vertical: Math.max(0, document.documentElement.scrollHeight - innerHeight),
  }))).toEqual({ horizontal: 0, vertical: 0 });

  await page.getByRole("button", { name: /^Advanced/ }).click();
  const generatedDialogue = page.getByRole("checkbox", {
    name: /^Generated ambient dialogue/,
  });
  await expect(generatedDialogue).not.toBeChecked();
  await generatedDialogue.check();
  await expect(generatedDialogue).toBeChecked();
  await page.screenshot({ path: testInfo.outputPath("pet-settings.png") });
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
  expect(calls.filter((call) => call.method === "assistant.turns.start")).toHaveLength(1);
});

async function openScratchChat(page: import("@playwright/test").Page) {
  await page
    .getByLabel("History navigation")
    .getByRole("button", { name: "Scratch chat", exact: true })
    .click();
}

async function enableDeveloperMode(page: import("@playwright/test").Page) {
  await page.goto("/?surface=settings");
  await page.getByRole("button", { name: /Advanced/ }).click();
  await page.getByRole("checkbox", { name: "Developer mode" }).check();
  await page.goto("/");
}
