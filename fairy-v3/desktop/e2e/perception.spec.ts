import { expect, test } from "@playwright/test";

import { installWorkspaceFixture } from "./support/coreFixture";

test.beforeEach(async ({ page }) => {
  await installWorkspaceFixture(page);
  await page.goto("/");
  await page
    .getByLabel("History navigation")
    .getByRole("button", { name: "Scratch chat", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: "Chat" })).toBeVisible();
});

test("captures only after a user gesture and sends the confirmed image to Core", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 640, height: 700 });
  expect(await fixtureCalls(page, "capture.")).toEqual([]);

  await page.getByRole("button", { name: "Capture screen" }).click();
  await page.getByLabel("Capture source").selectOption("window:2");
  await page.getByRole("button", { name: "Capture selected source" }).click();
  await expect(page.getByAltText("Game window capture preview")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("perception-preview-640x700.png"),
  });
  const dialogBox = await page.getByRole("dialog", { name: "Screen capture" }).boundingBox();
  const composerBox = await page.locator(".composer-controls").boundingBox();
  expect(dialogBox).not.toBeNull();
  expect(composerBox).not.toBeNull();
  if (dialogBox !== null && composerBox !== null) {
    expect(dialogBox.x).toBeGreaterThanOrEqual(48);
    expect(dialogBox.x + dialogBox.width).toBeLessThanOrEqual(640);
    expect(dialogBox.y + dialogBox.height).toBeLessThanOrEqual(composerBox.y);
  }

  expect(await fixtureCalls(page, "capture.surface")).toEqual([
    {
      method: "capture.surface",
      params: { kind: "window", source_id: "2" },
    },
  ]);
  await page.getByRole("button", { name: "Attach capture" }).click();
  await page.getByLabel("Message Fairy").fill("Explain this game state");
  await page.getByRole("button", { name: "Send message" }).click();

  await expect
    .poll(async () => (await fixtureCalls(page, "assistant.turns.create")).length)
    .toBe(1);
  const [turnCall] = await fixtureCalls(page, "assistant.turns.create");
  expect(turnCall.params.image_attachments).toEqual([
    expect.objectContaining({
      media_type: "image/png",
      content_hash: "431ced6916a2a21a156e38701afe55bbd7f88969fbbfc56d7fe099d47f265460",
      width: 1,
      height: 1,
      source_label: "Game window",
      persistence: "ephemeral",
    }),
  ]);
  expect(JSON.stringify(turnCall.params.image_attachments)).not.toContain("source_id");
});

async function fixtureCalls(page, prefix: string) {
  return page.evaluate((value) => {
    const fixture = window as unknown as {
      __FAIRY_FIXTURE_CALLS__: Array<{
        method: string;
        params: Record<string, unknown>;
      }>;
    };
    return fixture.__FAIRY_FIXTURE_CALLS__.filter((call) =>
      call.method.startsWith(value),
    );
  }, prefix);
}
