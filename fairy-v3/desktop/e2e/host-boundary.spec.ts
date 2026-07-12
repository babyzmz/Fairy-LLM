import { expect, test } from "@playwright/test";

test("plain browser rendering reports the missing desktop host", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Open Fairy as a Windows app" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("Core not attached");
  await expect(page.getByRole("button")).toHaveCount(0);
});
