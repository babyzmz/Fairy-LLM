import { expect, type Page } from "@playwright/test";

export async function openInternalSettings(
  page: Page,
  options: {
    path?: string;
    category?: RegExp | string;
  } = {},
) {
  await page.goto(options.path ?? "/");
  await page.getByRole("button", { name: "Open settings" }).click();
  await expect(
    page.getByRole("button", { name: "Back to workspace" }),
  ).toBeVisible();
  if (options.category !== undefined) {
    await page.getByRole("button", { name: options.category }).click();
  }
}
