import { expect, type Page } from "@playwright/test";

export async function openInternalSettings(
  page: Page,
  options: {
    path?: string;
    category?: RegExp | string;
  } = {},
) {
  await page.goto(options.path ?? "/");
  await openSettingsFromWorkspace(page, options);
}

export async function openSettingsFromWorkspace(
  page: Page,
  options: {
    category?: RegExp | string;
  } = {},
) {
  await page.getByRole("button", { name: "Open settings" }).click();
  const settings = page.getByRole("main", { name: "Fairy settings" });
  await expect(
    page.getByRole("button", { name: "Back to workspace" }),
  ).toBeVisible({ timeout: 15_000 });
  await expect(settings).toHaveAttribute(
    "data-settings-state",
    "ready",
    { timeout: 15_000 },
  );
  if (options.category !== undefined) {
    const category = page.getByRole("button", { name: options.category });
    await category.click();
    await expect(category).toHaveClass(/active/u);
    await expect(settings).toHaveAttribute(
      "data-settings-state",
      "ready",
      { timeout: 15_000 },
    );
  }
}
