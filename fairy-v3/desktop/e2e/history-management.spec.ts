import { expect, test } from "@playwright/test";

import { installWorkspaceFixture } from "./support/coreFixture";
import { openInternalSettings } from "./support/settings";

test.beforeEach(async ({ page }) => {
  await installWorkspaceFixture(page);
});

test("history navigation stays two levels deep and shares the project action menu", async ({
  page,
}) => {
  await page.goto("/");

  const history = page.getByLabel("History navigation");
  await expect(history.getByRole("button", { name: "Scratch chat", exact: true })).toBeVisible();
  await expect(history.getByTitle("Atlas Console")).toBeVisible();
  await history.getByRole("button", { name: "Expand Atlas Console" }).click();
  await expect(
    history.getByRole("button", { name: "Project conversation", exact: true }),
  ).toBeVisible();
  await expect(history).not.toContainText("Tighten the project overview");

  const projectButton = history.getByTitle("Atlas Console");
  await projectButton.focus();
  await page.keyboard.press("Shift+F10");
  const keyboardMenu = page.getByRole("menu", { name: "Actions for Atlas Console" });
  await expect(keyboardMenu).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(keyboardMenu).toBeHidden();
  await expect(projectButton).toBeFocused();

  await projectButton.click({ button: "right" });
  const contextMenu = page.getByRole("menu", { name: "Actions for Atlas Console" });
  await expect(contextMenu).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(contextMenu).toBeHidden();
  await expect(projectButton).toBeFocused();

  await history.getByRole("button", { name: "Actions for Atlas Console" }).click();
  const menu = page.getByRole("menu", { name: "Actions for Atlas Console" });
  await expect(menu.getByRole("menuitem", { name: "New chat" })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Rename" })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Pin" })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Archive" })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Delete" })).toBeVisible();

  await menu.getByRole("menuitem", { name: "Archive" }).click();
  const dialog = page.getByRole("dialog", { name: "Archive project" });
  await expect(dialog).toContainText("1 chat");
  await dialog.getByRole("button", { name: "Archive" }).click();

  await expect(history.getByTitle("Atlas Console")).toHaveCount(0);
  const archiveCall = await page.evaluate(() => {
    const fixtureWindow = window as typeof window & {
      __FAIRY_FIXTURE_CALLS__: Array<{ method: string; params: Record<string, unknown> }>;
    };
    return fixtureWindow.__FAIRY_FIXTURE_CALLS__.findLast(
      (call) => call.method === "projects.archive",
    );
  });
  expect(archiveCall?.params).toMatchObject({
    expected_revision: 0,
  });
});

test("settings restores archived projects and ordinary chats from Recently deleted", async ({
  page,
}) => {
  await openInternalSettings(page, { path: "/?historySeed=1" });

  await expect(page.getByRole("heading", { name: "General" })).toBeVisible();
  await expect(page.getByText("Atlas Console", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Restore" }).click();
  await expect(page.getByText("Atlas Console", { exact: true })).toHaveCount(0);

  await page.getByRole("tab", { name: /Recently deleted/ }).click();
  await expect(page.getByText("Scratch chat", { exact: true })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "Permanently delete after 30 days" })).not.toBeChecked();
  await page.getByRole("button", { name: "Restore" }).click();
  await expect(page.getByText("Recently deleted is empty")).toBeVisible();

  const methods = await page.evaluate(() => {
    const fixtureWindow = window as typeof window & {
      __FAIRY_FIXTURE_CALLS__: Array<{ method: string }>;
    };
    return fixtureWindow.__FAIRY_FIXTURE_CALLS__.map((call) => call.method);
  });
  expect(methods).toContain("projects.archived.restore");
  expect(methods).toContain("trash.items.restore");
});
